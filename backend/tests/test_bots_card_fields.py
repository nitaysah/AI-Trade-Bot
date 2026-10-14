"""Display-only fields added to the Bots scan log: old keys are unchanged, new ones are additive."""
import unittest
from unittest.mock import MagicMock, patch

import main
import nyse_calendar
from test_strategy_scan_log import POSITION, CANDLE_TS

MANUAL = {"buy_mode": "indicator", "sell_mode": "sltp", "min_buy_signals": 2, "min_sell_signals": 2,
          "indicators": ["RSI", "MACD"], "timeframe": "1Day", "market_session": "Regular",
          "enable_cooldown": False, "enable_signal_reset": False, "enable_smart_reentry": False}
NEW_KEYS = {"lifecycle", "entry_guard", "next_due_at", "setup_config", "scan_interval_capped", "next_scan_at", "next_scan_source", "exit_scan"}


def setUpModule():
    global _cal
    _cal = patch.object(nyse_calendar, "_calendar", return_value=None)
    _cal.start()


def tearDownModule():
    _cal.stop()


def engine_stub():
    eng = MagicMock()
    eng.last_trailing_stops = {}
    eng.get_now.return_value = main.get_now()
    eng.get_ticker_settings.return_value = {}
    return eng


def build(settings, position=None, pending=None, **scan_extra):
    scan = {"time": "2026-10-07T10:00:00-04:00", "action": "HOLD", "reason": "no signal", "price_raw": 100.0,
            "candle_meta": {"candle_key": f"1DAY:{CANDLE_TS}", "candle_status": "closed"}}
    scan.update(scan_extra)
    formatted = main._format_scan_for_ui(scan)
    return main._build_scan_log("SPY", formatted, dict(settings), position or {}, pending or [], {"equity": 10000}, {},
                                engine_stub(), [position] if position else [])


class BotsCardFieldsTests(unittest.TestCase):
    def test_flat_bot_is_flat_with_next_due_time(self):
        log = build(MANUAL)
        self.assertEqual(log["lifecycle"], "FLAT")
        self.assertIsNone(log["entry_guard"])
        self.assertTrue(log["next_due_at"])

    def test_open_position_is_in_position(self):
        log = build(MANUAL, position=POSITION)
        self.assertEqual(log["lifecycle"], "IN_POSITION")
        self.assertEqual(log["phase"], "EXIT")

    def test_open_order_is_order_pending(self):
        log = build(MANUAL, pending=[{"symbol": "SPY", "side": "buy"}])
        self.assertEqual(log["lifecycle"], "ORDER_PENDING")

    def test_guard_reason_reaches_the_log_when_flat_only(self):
        log = build(MANUAL, entry_guard_reason="Liquidity filter: spread too wide")
        self.assertEqual(log["entry_guard"], {"reason": "Liquidity filter: spread too wide"})
        self.assertIsNone(build(MANUAL, position=POSITION, entry_guard_reason="stale")["entry_guard"])

    def test_guard_text_does_not_change_status_or_blockers(self):
        plain = build(MANUAL)
        guarded = build(MANUAL, entry_guard_reason="Liquidity filter: spread too wide")
        for key in ("status", "blockers", "summary", "detail", "progress_label", "decision", "phase"):
            self.assertEqual(plain[key], guarded[key], key)

    def test_only_new_keys_were_added(self):
        for log in (build(MANUAL), build(MANUAL, position=POSITION)):
            old_style = set(log) - NEW_KEYS
            self.assertEqual(old_style, {
                "setup", "forced_scan", "summary", "detail", "status", "phase", "decision", "target_direction", "strategy_mode",
                "strategy_label", "condition_logic", "uses_indicators", "uses_structural", "show_indicator_leg", "progress_label",
                "live_levels", "required_count", "confirmed_count", "confirmed_weight", "waiting_count", "opposing_count",
                "remaining_count", "blockers", "exit_protection", "continuous_grid_forecast", "indicators", "updated_at",
                "is_option", "refresh_rate_sec", "scan_interval_mode", "scan_policy", "strategy_log"})

    def test_formatter_passes_the_guard_reason_and_defaults_to_none(self):
        self.assertIsNone(main._format_scan_for_ui({"action": "HOLD"})["entry_guard_reason"])
        self.assertEqual(main._format_scan_for_ui({"entry_guard_reason": "x"})["entry_guard_reason"], "x")

    def test_next_due_helper_handles_bad_input(self):
        self.assertIsNone(main._scan_next_due_at("", 60))
        self.assertIsNone(main._scan_next_due_at(None, 60))
        self.assertTrue(main._scan_next_due_at("2026-10-07T10:00:00-04:00", 60).startswith("2026-10-07T10:01:00"))

    def test_setup_config_lines_describe_the_configured_entry_and_exit(self):
        cfg = build(dict(MANUAL, buy_mode="hybrid_atr", entry_atr_multiplier=1.5, sell_mode="sltp", atr_stop_multiplier=2,
                         take_profit_multiplier=4, atr_trail_multiplier=3))["setup_config"]
        self.assertEqual(cfg["entry"], ["Indicators Config: RSI, MACD (2 of 2 bullish confirmations needed)",
                                        "ATR config: 1.5× volatility · default"])
        self.assertEqual(cfg["exit"], ["ATR config: SL 2× · TP 4× · trail 3× (on)"])

    def test_percentage_and_custom_exits_and_empty_indicator_pool(self):
        cfg = build(dict(MANUAL, indicators=[], sell_mode="percentage", regular_stop_loss_pct=5, regular_take_profit_pct=10))["setup_config"]
        self.assertTrue(cfg["entry"][0].startswith("Indicators Config: select at least one indicator"))
        self.assertIn("PCT config: SL 5% · TP 10%", cfg["exit"][0])

    def test_ai_autopilot_has_no_config_lines(self):
        self.assertEqual(build(dict(MANUAL, ai_autopilot=True))["setup_config"], {"entry": [], "exit": []})

    def test_schedule_summary_gives_the_bots_own_buy_and_sell_times(self):
        import strategy_scan_log as sl
        rider = {"strategy_preset_enabled": True, "strategy_preset": "session_rider", "session_buy_time": "open+1", "session_sell_time": "close-5"}
        self.assertEqual(sl.schedule_summary(rider), {"mode": "Intraday", "buy_label": "Open + 1 min · 8:31 AM CT",
                                                      "sell_label": "Close − 5 min · 2:55 PM CT", "skip_weekend": False})
        overnight = dict(rider, session_buy_time="close-5", session_sell_time="open+5")
        self.assertEqual(sl.schedule_summary(overnight)["mode"], "Overnight")
        self.assertIsNone(sl.schedule_summary(MANUAL))
        self.assertIsNone(sl.schedule_summary(dict(rider, options_trading=True)))

    def test_times_shown_to_the_user_are_central_only(self):
        import re
        import preset_runtime
        import session_schedule as S
        import strategy_scan_log as sl
        from datetime import datetime
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo("America/New_York")
        texts = [
            S.et_ct_label("open+1"),
            S.fmt_et_ct(datetime(2026, 10, 13, 15, 56, tzinfo=eastern)),
            sl._et_label("2026-10-13T15:56:00-04:00"),
            sl._hhmm_et_as_ct("14:30"),
            preset_runtime.window_reason(datetime(2026, 10, 13, 8, 0, tzinfo=eastern), "Regular"),
            preset_runtime.window_reason(datetime(2026, 10, 13, 2, 0, tzinfo=eastern), "Extended"),
        ]
        for text in texts:
            self.assertRegex(text, r"CT\b|\bCT")
            self.assertIsNone(re.search(r"\b(ET|EST|EDT)\b", text), text)
        self.assertEqual(sl._hhmm_et_as_ct("14:30"), "1:30 PM CT")
        self.assertEqual(S.fmt_et_ct(datetime(2026, 10, 13, 15, 56, tzinfo=eastern)), "Tue Oct 13, 2:56 PM CT")


if __name__ == "__main__":
    unittest.main()
