import unittest
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import main
import pytz
from screener_service import ScreenerUserState, normalize_screener_sms_phone


class ScreenerStatusCardTests(unittest.IsolatedAsyncioTestCase):
    def test_sms_phone_requires_e164_format(self):
        self.assertEqual(normalize_screener_sms_phone("+1 (212) 555-1234"), "+12125551234")
        self.assertEqual(normalize_screener_sms_phone("212-555-1234"), "")

    async def test_sms_cannot_be_enabled_without_a_valid_phone_number(self):
        state = ScreenerUserState("sms-settings-test", None)
        state.save = AsyncMock()

        with self.assertRaises(main.HTTPException):
            await main.update_screener_settings(
                {"settings": {"sms_enabled": True, "sms_phone_number": ""}},
                state,
            )

        self.assertFalse(state.notification_settings["sms_enabled"])
        state.save.assert_not_awaited()

    async def test_unconfigured_sms_provider_skips_queued_sms_without_network_call(self):
        state = ScreenerUserState("sms-outbox-test", None)
        state.profiles = {"AAPL": {"enabled": True}}
        state.notification_settings.update({"sms_enabled": True, "sms_phone_number": "+12125551234"})
        state.notifications = [{"id": "alert-1", "symbol": "AAPL", "direction": "BULLISH"}]
        state.sms_outbox = [{"id": "alert-1", "notification_id": "alert-1", "to": "+12125551234", "status": "queued", "created_at": 1}]
        state.save = AsyncMock()

        with patch.object(main, "_sms_provider_configured", return_value=False), patch.object(main, "_send_screener_sms_sync") as send_sms:
            await main._process_screener_sms_outbox(state)

        send_sms.assert_not_called()
        self.assertEqual(state.sms_outbox[0]["status"], "skipped")
        self.assertEqual(state.notifications[0]["sms_status"], "setup_required")
        state.save.assert_awaited_once()

    def _state(self):
        state = ScreenerUserState("status-card-test", None)
        state.profiles = {"AAPL": {"enabled": True}}
        state.order = ["AAPL"]
        timezone = pytz.timezone("America/Chicago")
        today = datetime.now(timezone).date()
        today_noon = timezone.localize(datetime.combine(today, time(12, 0))).timestamp()
        older = timezone.localize(datetime.combine(today - timedelta(days=2), time(12, 0))).timestamp()
        state.notifications = [
            {"id": "bull-today", "direction": "BULLISH", "created_at": today_noon, "read": False},
            {"id": "bear-today", "direction": "BEARISH", "created_at": today_noon - 60, "read": True},
            {"id": "bull-old", "direction": "BULLISH", "created_at": older, "read": False},
        ]
        state.email_outbox = [
            {"id": "queued", "status": "queued"},
            {"id": "failed", "status": "failed"},
        ]
        state.sms_outbox = [
            {"id": "sms-queued", "status": "queued"},
            {"id": "sms-failed", "status": "failed"},
        ]
        state.notification_settings.update({
            "email_enabled": False,
            "sms_enabled": True,
            "sms_phone_number": "+12125551234",
            "bullish_enabled": True,
            "bearish_enabled": False,
        })
        return state

    def test_summary_includes_alert_breakdown_and_both_delivery_counts(self):
        summary = main._screener_summary(self._state(), ["AAPL"])

        self.assertEqual(summary["alerts_today"], 2)
        self.assertEqual(summary["alerts_today_by_direction"], {"bullish": 1, "bearish": 1})
        self.assertEqual(summary["unread_alerts"], 2)
        self.assertTrue(summary["browser_delivery"]["enabled"])
        self.assertFalse(summary["email_delivery"]["enabled"])
        self.assertEqual(summary["email_delivery"]["queued"], 1)
        self.assertEqual(summary["email_delivery"]["failed"], 1)
        self.assertTrue(summary["sms_delivery"]["enabled"])
        self.assertTrue(summary["sms_delivery"]["phone_configured"])
        self.assertEqual(summary["sms_delivery"]["queued"], 1)
        self.assertEqual(summary["sms_delivery"]["failed"], 1)

    async def test_time_and_read_filters_return_the_correct_alert_subset(self):
        response = await main.list_screener_notifications(
            limit=50,
            cursor=None,
            unread_only=False,
            period="today",
            state=self._state(),
        )

        self.assertEqual(response["period"], "today")
        self.assertEqual(response["count"], 2)
        self.assertEqual(
            {item["id"] for item in response["notifications"]},
            {"bull-today", "bear-today"},
        )
        self.assertEqual(response["unread_count"], 1)
        self.assertEqual(response["read_count"], 1)
        self.assertEqual(response["total_count"], 2)

        unread_response = await main.list_screener_notifications(
            limit=50,
            cursor=None,
            unread_only=False,
            period="1d",
            read_state="unread",
            state=self._state(),
        )

        self.assertEqual(unread_response["period"], "1d")
        self.assertEqual(unread_response["read_state"], "unread")
        self.assertEqual(
            {item["id"] for item in unread_response["notifications"]},
            {"bull-today"},
        )


if __name__ == "__main__":
    unittest.main()
