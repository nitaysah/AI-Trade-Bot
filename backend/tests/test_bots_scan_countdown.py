"""The Bots card countdown must show the interval the engine really uses, including its busy cap."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz

import main
import nyse_calendar
from test_bots_card_fields import MANUAL, engine_stub, POSITION, CANDLE_TS


def setUpModule():
    global _cal
    _cal = patch.object(nyse_calendar, "_calendar", return_value=None)
    _cal.start()


def tearDownModule():
    _cal.stop()


def build(settings, position=None, pending=None, pending_fill=None):
    eng = engine_stub()
    eng.config.SCAN_INTERVAL_SECONDS = 60
    eng.config.OPTIONS_SCAN_INTERVAL_SECONDS = 10
    settings = dict(settings)
    if pending_fill:
        settings["pending_fill"] = pending_fill
    scan = {"time": "2026-10-07T10:00:00-04:00", "action": "HOLD", "reason": "no signal", "price_raw": 100.0,
            "candle_meta": {"candle_key": f"1DAY:{CANDLE_TS}", "candle_status": "closed"}}
    formatted = main._format_scan_for_ui(scan)
    return main._build_scan_log("SPY", formatted, settings, position or {}, pending or [], {"equity": 10000}, {},
                                eng, [position] if position else [])


class ScanCountdownTests(unittest.TestCase):
    def test_default_bot_counts_the_engine_default(self):
        log = build(MANUAL)
        self.assertEqual(log["refresh_rate_sec"], 60)
        self.assertFalse(log["scan_interval_capped"])

    def test_flat_custom_interval_is_not_overwritten_by_60s(self):
        log = build({**MANUAL, "scan_interval_seconds": 900})
        self.assertEqual(log["refresh_rate_sec"], 900)
        self.assertEqual(log["scan_interval_mode"], "custom")
        self.assertFalse(log["scan_interval_capped"])

    def test_auto_follows_the_timeframe(self):
        log = build({**MANUAL, "scan_interval_auto": True, "timeframe": "15Min"})
        self.assertEqual(log["refresh_rate_sec"], 900)

    def test_open_position_caps_the_countdown_like_the_engine_does(self):
        log = build({**MANUAL, "scan_interval_seconds": 900}, position=POSITION)
        self.assertEqual(log["refresh_rate_sec"], 60)
        self.assertTrue(log["scan_interval_capped"])

    def test_indicator_exit_position_keeps_the_bots_own_interval_and_says_so(self):
        log = build({**MANUAL, "sell_mode": "indicator", "scan_interval_seconds": 900}, position=POSITION)
        self.assertEqual(log["refresh_rate_sec"], 900)
        self.assertFalse(log["scan_interval_capped"])
        self.assertEqual(log["exit_scan"], {"kind": "indicator", "interval_sec": 900})

    def test_price_exit_position_reports_a_price_watch(self):
        log = build({**MANUAL, "sell_mode": "sltp", "scan_interval_seconds": 900}, position=POSITION)
        self.assertEqual(log["exit_scan"], {"kind": "price", "interval_sec": 60})

    def test_no_exit_badge_while_flat_and_indicator_exit_with_an_order_is_fast(self):
        self.assertIsNone(build({**MANUAL, "sell_mode": "indicator"})["exit_scan"])
        log = build({**MANUAL, "sell_mode": "indicator", "scan_interval_seconds": 900}, position=POSITION, pending=[{"symbol": "SPY", "side": "sell"}])
        self.assertEqual(log["refresh_rate_sec"], 60)

    def test_working_order_or_pending_fill_also_cap_it(self):
        self.assertEqual(build({**MANUAL, "scan_interval_seconds": 900}, pending=[{"symbol": "SPY", "side": "buy"}])["refresh_rate_sec"], 60)
        self.assertEqual(build({**MANUAL, "scan_interval_seconds": 900}, pending_fill={"order_id": "x"})["refresh_rate_sec"], 60)

    def test_a_faster_custom_interval_is_kept_while_busy(self):
        log = build({**MANUAL, "scan_interval_seconds": 30}, position=POSITION)
        self.assertEqual(log["refresh_rate_sec"], 30)
        self.assertFalse(log["scan_interval_capped"])

    def test_next_due_uses_the_same_interval(self):
        log = build({**MANUAL, "scan_interval_seconds": 900}, position=POSITION)
        self.assertTrue(log["next_due_at"].startswith("2026-10-07T10:01:00"))


RIDER = {"strategy_preset_enabled": True, "strategy_preset": "session_rider", "session_buy_time": "close-5",
         "session_sell_time": "open+5", "timeframe": "1Day", "market_session": "Regular"}


def with_strategy_time(when):
    """The real strategy view, with only its timing replaced."""
    real = main.strategy_scan_log.scan_view

    def view(*args, **kwargs):
        out = real(*args, **kwargs)
        out["timing"] = {"candle": None, "idle": False, "next_evaluation_at": when}
        return out
    return patch.object(main.strategy_scan_log, "scan_view", side_effect=view)


class StrategyOwnCountdownTests(unittest.TestCase):
    def test_helper_accepts_only_a_future_time(self):
        future = (datetime.now(pytz.UTC) + timedelta(hours=3)).isoformat()
        past = (datetime.now(pytz.UTC) - timedelta(minutes=1)).isoformat()
        self.assertEqual(main._strategy_next_scan_at({"next_evaluation_at": future}), datetime.fromisoformat(future).isoformat())
        self.assertIsNone(main._strategy_next_scan_at({"next_evaluation_at": past}))      # overdue: normal countdown
        self.assertIsNone(main._strategy_next_scan_at({"next_evaluation_at": None}))
        self.assertIsNone(main._strategy_next_scan_at({"next_evaluation_at": "not a time"}))
        self.assertIsNone(main._strategy_next_scan_at(None))
        naive = (datetime.now(pytz.UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        self.assertIsNotNone(main._strategy_next_scan_at({"next_evaluation_at": naive}))   # treated as UTC, never raises

    def test_scan_log_carries_the_strategy_time_and_a_manual_bot_does_not(self):
        future = (datetime.now(pytz.UTC) + timedelta(hours=5)).isoformat()
        with with_strategy_time(future):
            log = build(RIDER)
        self.assertEqual(log["next_scan_source"], "strategy")
        self.assertEqual(log["next_scan_at"], datetime.fromisoformat(future).isoformat())
        self.assertEqual(log["refresh_rate_sec"], 60)                                      # the normal timer is still published
        manual = build(MANUAL)
        self.assertEqual((manual["next_scan_source"], manual["next_scan_at"]), ("interval", None))

    def test_an_overdue_strategy_time_falls_back_to_the_interval_countdown(self):
        past = (datetime.now(pytz.UTC) - timedelta(minutes=2)).isoformat()
        with with_strategy_time(past):
            log = build(RIDER)
        self.assertEqual((log["next_scan_source"], log["next_scan_at"]), ("interval", None))


if __name__ == "__main__":
    unittest.main()
