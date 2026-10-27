import time
import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

import main
from screener_service import ScreenerUserState


def _alert(alert_id, age_seconds=60, read=False, **extra):
    return {"id": alert_id, "symbol": "AAPL", "direction": "BULLISH", "created_at": time.time() - age_seconds,
            "read": read, "browser_status": "delivered", **extra}


class DismissScreenerAlertTests(unittest.IsolatedAsyncioTestCase):
    def _state(self, alerts):
        state = ScreenerUserState("dismiss-test", None)
        state.save = AsyncMock()
        state.notifications = list(alerts)
        return state

    async def _listed(self, state, **kw):
        response = await main.list_screener_notifications(state=state, **kw)
        return [item["id"] for item in response["notifications"]], response

    async def test_dismissing_by_id_hides_the_alert_but_keeps_the_record(self):
        state = self._state([_alert("a"), _alert("b"), _alert("c", read=True)])
        out = await main.dismiss_screener_notifications({"ids": ["a"]}, state)
        self.assertEqual(out["dismissed"], 1)
        self.assertEqual(out["unread_count"], 1)                      # b is the only unread alert left in view
        ids, response = await self._listed(state)
        self.assertEqual(ids, ["b", "c"])
        self.assertEqual(response["unread_count"], 1)
        self.assertEqual(response["total_count"], 2)
        kept = next(item for item in state.notifications if item["id"] == "a")
        self.assertTrue(kept["dismissed"])
        self.assertTrue(kept["read"])                                  # a dismissed alert can never count as unread
        state.save.assert_awaited_once()

    async def test_dismissing_twice_or_an_unknown_id_changes_nothing_and_does_not_save_again(self):
        state = self._state([_alert("a")])
        await main.dismiss_screener_notifications({"ids": ["a"]}, state)
        state.save.reset_mock()
        out = await main.dismiss_screener_notifications({"ids": ["a", "nope"]}, state)
        self.assertEqual(out["dismissed"], 0)
        state.save.assert_not_awaited()

    async def test_an_empty_or_ambiguous_request_clears_nothing(self):
        state = self._state([_alert("a"), _alert("b")])
        for body in (None, {}, {"ids": []}, {"all": "true"}, {"all": 1}, {"all": False}, "all", {"cutoff": time.time()}):
            with self.assertRaises(HTTPException) as ctx:
                await main.dismiss_screener_notifications(body, state)
            self.assertEqual(ctx.exception.status_code, 400, body)
        ids, _ = await self._listed(state)
        self.assertEqual(sorted(ids), ["a", "b"])
        state.save.assert_not_awaited()

    async def test_clear_all_clears_exactly_what_the_filters_show(self):
        state = self._state([_alert("new-unread"), _alert("new-read", read=True),
                             _alert("old-unread", age_seconds=3 * 86400), _alert("old-read", age_seconds=3 * 86400, read=True)])
        # a 1-day window with read status "read" matches only new-read
        out = await main.dismiss_screener_notifications({"all": True, "period": "1d", "read_state": "read"}, state)
        self.assertEqual(out["dismissed"], 1)
        ids, _ = await self._listed(state)
        self.assertEqual(sorted(ids), ["new-unread", "old-read", "old-unread"])
        # all periods, all statuses: everything left goes
        out = await main.dismiss_screener_notifications({"all": True}, state)
        self.assertEqual(out["dismissed"], 3)
        ids, response = await self._listed(state)
        self.assertEqual(ids, [])
        self.assertEqual((response["unread_count"], response["total_count"], response["matching_count"]), (0, 0, 0))

    async def test_clear_all_leaves_alerts_that_arrived_after_the_list_was_shown(self):
        state = self._state([_alert("seen-1", age_seconds=120), _alert("seen-2", age_seconds=90)])
        _, response = await self._listed(state)
        self.assertEqual(response["matching_count"], 2)
        state.notifications.insert(0, _alert("arrived-during-confirm", age_seconds=-5))   # created after as_of
        out = await main.dismiss_screener_notifications({"all": True, "as_of": response["as_of"]}, state)
        self.assertEqual(out["dismissed"], 2)
        self.assertEqual(out["unread_count"], 1)
        ids, _ = await self._listed(state)
        self.assertEqual(ids, ["arrived-during-confirm"])
        fresh = next(item for item in state.notifications if item["id"] == "arrived-during-confirm")
        self.assertFalse(fresh["read"])                                 # still unread, so the user sees it

    async def test_clear_all_rejects_a_non_numeric_as_of_and_clears_nothing(self):
        state = self._state([_alert("a")])
        for bad in ("now", True, [1]):
            with self.assertRaises(HTTPException):
                await main.dismiss_screener_notifications({"all": True, "as_of": bad}, state)
        state.save.assert_not_awaited()

    async def test_clear_all_reaches_alerts_beyond_the_first_page_of_fifty(self):
        state = self._state([_alert(f"n{i}") for i in range(120)])
        ids, response = await self._listed(state)
        self.assertEqual((len(ids), response["matching_count"]), (50, 120))
        out = await main.dismiss_screener_notifications({"all": True}, state)
        self.assertEqual(out["dismissed"], 120)

    async def test_summary_counters_ignore_dismissed_alerts_but_the_history_record_survives(self):
        state = self._state([_alert("a"), _alert("b")])
        await main.dismiss_screener_notifications({"ids": ["a"]}, state)
        visible = main._screener_visible_notifications(state)
        self.assertEqual([item["id"] for item in visible], ["b"])
        self.assertEqual(len(state.notifications), 2)                  # still stored for dedupe, outbox lookups and history

    async def test_a_dismissed_alert_does_not_come_back_when_the_same_candle_is_evaluated_again(self):
        ts = 1_790_000_000
        state = ScreenerUserState("dismiss-dedupe", None)
        state.save = AsyncMock()
        state.profiles["AAPL"] = {"enabled": True, "timeframe": "30Min", "market_session": "Regular", "alert_source": "technical",
                                  "strategy_mode": "technical", "indicators": [], "alert_cooldown_minutes": 0}
        state.order = ["AAPL"]
        state.mark_profile_baseline("AAPL", "profile_activated")
        evaluation = {"evaluation_status": "valid", "direction": "BULLISH", "action": "BUY", "price_raw": 100.0, "price": "$100.00",
                      "atr": 2.0, "reason": "x", "signals": {}, "atr_levels": {},
                      "candle_meta": {"candle_key": f"30MIN:{ts}", "candle_open_ts": ts, "candle_close_ts": ts + 1800, "candle_status": "closed"}}
        created = state.commit_evaluation("AAPL", dict(evaluation), now_ts=ts + 1800)
        self.assertIsNotNone(created)
        await main.dismiss_screener_notifications({"ids": [created["id"]]}, state)
        self.assertIsNone(state.commit_evaluation("AAPL", dict(evaluation), now_ts=ts + 1900))
        ids, _ = await self._listed(state)
        self.assertEqual(ids, [])


if __name__ == "__main__":
    unittest.main()


class SignalActivityHistoryTests(unittest.IsolatedAsyncioTestCase):
    """The Signal Activity chart keeps the full history by default and can be told to match the visible list."""

    def _state(self):
        state = ScreenerUserState("history-dismiss-test", None)
        state.save = AsyncMock()
        state.notifications = [
            _alert("bull-kept", direction="BULLISH"),
            _alert("bear-kept", direction="BEARISH"),
            _alert("bull-cleared", direction="BULLISH", dismissed=True),
            _alert("bear-cleared", direction="BEARISH", dismissed=True),
            _alert("bear-cleared-2", direction="BEARISH", dismissed=True),
        ]
        return state

    def test_history_includes_cleared_alerts_by_default(self):
        state = self._state()
        out = main.get_screener_history(period="1W", timeframe="1D", state=state)
        self.assertEqual((sum(out["bullish"]), sum(out["bearish"])), (2, 3))
        self.assertTrue(out["include_dismissed"])
        self.assertEqual(out["cleared_in_range"], 3)

    def test_history_can_match_the_visible_list(self):
        state = self._state()
        out = main.get_screener_history(period="1W", timeframe="1D", include_dismissed=False, state=state)
        self.assertEqual((sum(out["bullish"]), sum(out["bearish"])), (1, 1))
        self.assertFalse(out["include_dismissed"])
        self.assertEqual(out["cleared_in_range"], 3)           # still reported, so the page can say what the switch would add

    def test_cleared_count_respects_the_chart_range(self):
        state = self._state()
        state.notifications.append(_alert("old-cleared", age_seconds=40 * 86400, direction="BULLISH", dismissed=True))
        week = main.get_screener_history(period="1W", timeframe="1D", state=state)
        everything = main.get_screener_history(period="ALL", timeframe="1D", state=state)
        self.assertEqual(week["cleared_in_range"], 3)
        self.assertEqual(everything["cleared_in_range"], 4)

    def test_an_empty_history_still_returns_a_timeline_so_the_chart_redraws_empty(self):
        state = ScreenerUserState("history-empty", None)
        out = main.get_screener_history(period="1W", timeframe="1D", include_dismissed=False, state=state)
        self.assertTrue(len(out["timestamp"]) > 0)
        self.assertEqual((sum(out["bullish"]), sum(out["bearish"])), (0, 0))
