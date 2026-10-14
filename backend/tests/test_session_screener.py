"""Session Rider on the Screener: clock-only phase, one alert per buy/sell time, no broker, keys survive saves."""
import asyncio
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import trader  # noqa: E402
from screener_service import ScreenerUserState  # noqa: E402
from screener_strategy import assess_strategy_preset_technical  # noqa: E402

ET = ZoneInfo("America/New_York")
SETTINGS = {"strategy_preset_enabled": True, "strategy_preset": "session_rider", "strategy_preset_variant": "scheduled",
            "session_buy_time": "close-5", "session_sell_time": "open+5", "timeframe": "1Day", "market_session": "Regular"}


class ScheduleAlertTests(unittest.TestCase):
    def setUp(self):
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        self.state = ScreenerUserState("session-alerts", None)
        self.state.notification_settings.update({"browser_enabled": True, "email_enabled": True})
        self.state.profiles["SPY"] = {"source": "screener", "enabled": True, **SETTINGS,
                                      "alert_bullish_enabled": False, "alert_bearish_enabled": False}
        self.eval = self.state.evaluation_state.setdefault("SPY", {})

    def scan(self, now):
        main._screener_schedule_scan(self.state, "SPY", self.state.profiles["SPY"], self.eval, 1800, now_et=now)

    def test_sell_alert_fires_before_any_session_gate_and_only_once(self):
        broker = MagicMock()
        with patch.object(main, "create_broker", broker, create=True):
            self.scan(datetime(2026, 10, 12, 9, 35, 3, tzinfo=ET))
            self.scan(datetime(2026, 10, 12, 9, 36, 0, tzinfo=ET))
        sells = [n for n in self.state.notifications if n.get("alert_kind") == "SCHEDULE"]
        self.assertEqual(len(sells), 1)
        self.assertIn("Sell time", sells[0]["title"])
        self.assertEqual(sells[0]["email_status"], "queued")
        self.assertEqual(len(self.state.email_outbox), 1)
        broker.assert_not_called()

    def test_direction_filters_do_not_block_schedule_alerts_but_the_switch_does(self):
        self.scan(datetime(2026, 10, 8, 15, 55, 2, tzinfo=ET))
        self.assertEqual(len(self.state.notifications), 1)  # both direction filters are off in setUp
        self.state.profiles["SPY"]["session_alerts_enabled"] = False
        self.scan(datetime(2026, 10, 9, 9, 35, 2, tzinfo=ET))
        self.assertEqual(len(self.state.notifications), 1)

    def test_schedule_scan_reports_when_it_ran_and_the_card_is_current(self):
        """The card's Refreshing state waits for a report time; a clock-only scan must provide one (it used to hang)."""
        self.scan(datetime(2026, 10, 13, 14, 5, tzinfo=ET))
        log = self.state.latest_scans["SPY"]["scan_log"]
        self.assertEqual(log["status"], "SCHEDULE")
        self.assertTrue(datetime.fromisoformat(log["updated_at"]).timestamp() > 0)
        projected = main._project_screener_scan(self.state, "SPY")
        self.assertEqual(projected["data_status"], "current")  # no candle to go stale, never "degraded"

    def test_schedule_scan_without_candles_is_not_marked_degraded_by_the_freshness_check(self):
        self.scan(datetime(2026, 10, 13, 14, 5, tzinfo=ET))
        scan = self.state.latest_scans["SPY"]
        self.assertFalse(scan.get("candle_meta", {}).get("candle_key"))
        self.assertNotIn(main._project_screener_scan(self.state, "SPY")["data_status"], {"degraded", "stale_reference"})

    def test_old_events_are_not_alerted_and_wake_up_is_the_next_event(self):
        self.scan(datetime(2026, 10, 8, 16, 30, tzinfo=ET))  # 35 minutes after the buy time
        self.assertEqual(self.state.notifications, [])
        nxt = datetime.fromtimestamp(self.eval["next_schedule_at"], ET)
        self.assertEqual(nxt, datetime(2026, 10, 9, 9, 35, tzinfo=ET))
        self.assertLessEqual(main._screener_due_at(self.eval), self.eval["next_schedule_at"])

    def test_phase_badge_data_and_assessment(self):
        self.scan(datetime(2026, 10, 10, 12, 0, tzinfo=ET))  # Saturday, inside the overnight hold
        scan = self.state.latest_scans["SPY"]
        sched = scan["strategy_preset"]["schedule"]
        self.assertEqual(sched["phase"], "HOLDING_OVERNIGHT")
        self.assertEqual(sched["next_event"], "SELL")
        self.assertIn("8:35 AM CT", sched["next_label"])
        tech = assess_strategy_preset_technical({"evaluation_status": "valid", "strategy_preset": scan["strategy_preset"]})
        self.assertEqual(tech["status"], "NEUTRAL")

    def test_due_helper_without_schedule(self):
        self.assertEqual(main._screener_due_at({"next_due_at": 100.0}), 100.0)
        self.assertEqual(main._screener_due_at({"next_due_at": 100.0, "next_schedule_at": 50.0}), 50.0)


class DashboardAndSaveTests(unittest.TestCase):
    def test_dashboard_decision_is_hold_only(self):
        d = trader._strategy_preset_decision("SPY", {}, SETTINGS, False, "1Day")
        self.assertEqual(d["action"], "HOLD")
        self.assertEqual(d["strategy_preset"]["kind"], "schedule")

    def test_session_keys_survive_the_screener_save_filter(self):
        state = ScreenerUserState("session-save", None)
        saved = main._screener_profile_from_request({**SETTINGS, "session_buy_time": "close-2", "session_skip_weekend": True},
                                                    state, "SPY")
        self.assertEqual(saved["session_buy_time"], "close-2")
        self.assertTrue(saved["session_skip_weekend"])
        self.assertEqual(saved["session_sell_time"], "open+5")


if __name__ == "__main__":
    unittest.main()
