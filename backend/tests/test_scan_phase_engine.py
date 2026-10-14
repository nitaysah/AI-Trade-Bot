import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)
SIGNALS = {"EMA Cross": {"signal": "BULLISH", "enabled": True}, "SMA": {"signal": "NEUTRAL", "enabled": True}}


def fake_engine(last_trade=None, lock=None):
    return SimpleNamespace(
        get_now=lambda: NOW,
        last_trade_timestamps={"SPY": last_trade} if last_trade else {},
        config=SimpleNamespace(),
        _get_signal_reset_lock=lambda ticker, settings=None: lock,
        _signal_reset_count=lambda result, locked: float(result.get("bullish_count", 0)),
    )


def phase(eng, settings, result, has_position=False, timeframe="30Min"):
    return engine.UserEngine._scan_phase_for(eng, "SPY", settings, result, has_position, timeframe)


class EnginePhaseTests(unittest.TestCase):
    def test_flat_with_no_history_is_plain_entry_watch(self):
        out = phase(fake_engine(), {"min_buy_signals": 2, "enable_cooldown": True}, {"signals": SIGNALS, "action": "HOLD"})
        self.assertEqual(out["phase"], "ENTRY WATCH")
        self.assertEqual(out["progress"], {"have": 1, "need": 2})

    def test_cooldown_counts_four_candles_by_default_and_reports_the_time_left(self):
        eng = fake_engine(last_trade=NOW - timedelta(minutes=30))  # 4 x 30Min = 120 min cooldown
        out = phase(eng, {"enable_cooldown": True}, {"signals": SIGNALS, "action": "HOLD", "bullish_count": 1})
        self.assertEqual(out["phase"], "RE-ENTRY")
        self.assertEqual(out["label"], "COOLDOWN")
        self.assertIn("1h 30m left", out["headline"])

    def test_custom_cooldown_minutes_and_expiry(self):
        eng = fake_engine(last_trade=NOW - timedelta(minutes=30))
        out = phase(eng, {"enable_cooldown": True, "cooldown_minutes": 15}, {"signals": SIGNALS, "action": "HOLD"})
        self.assertEqual(out["phase"], "ENTRY WATCH")  # 15 minutes already passed

    def test_signal_reset_lock_blocks_until_signals_drop(self):
        eng = fake_engine(lock="BUY")
        out = phase(eng, {"enable_cooldown": False}, {"signals": SIGNALS, "action": "HOLD", "bullish_count": 2})
        self.assertEqual(out["label"], "RESET")
        self.assertIn("2 bullish signal(s) still on", out["headline"])
        cleared = phase(eng, {"enable_cooldown": False}, {"signals": SIGNALS, "action": "HOLD", "bullish_count": 0})
        self.assertEqual(cleared["phase"], "ENTRY WATCH")

    def test_smart_reentry_rsi_filter_blocks_above_the_limit(self):
        settings = {"enable_cooldown": False, "enable_signal_reset": False, "enable_smart_reentry": True,
                    "smart_reentry_type": "RSI", "smart_reentry_rsi_call_max": 70}
        out = phase(fake_engine(), settings, {"signals": SIGNALS, "action": "HOLD", "_internal_rsi": 74.2})
        self.assertEqual(out["label"], "SMART RE-ENTRY")
        self.assertIn("RSI 74.2 above 70", out["headline"])
        self.assertEqual(out["phase"], "ENTRY GATE")  # nothing has traded yet, so this is not a re-entry
        ok = phase(fake_engine(), settings, {"signals": SIGNALS, "action": "HOLD", "_internal_rsi": 55.0})
        self.assertEqual(ok["phase"], "ENTRY WATCH")

    def test_smart_reentry_vwap_filter(self):
        settings = {"enable_cooldown": False, "enable_signal_reset": False, "enable_smart_reentry": True,
                    "smart_reentry_type": "VWAP", "smart_reentry_vwap_call_max_dist_pct": 0.5}
        out = phase(fake_engine(), settings, {"signals": SIGNALS, "action": "HOLD", "vwap": 100.0, "price_raw": 101.0})
        self.assertEqual(out["label"], "SMART RE-ENTRY")
        self.assertIn("1.00% from VWAP", out["headline"])

    def test_post_stop_wait_is_detected_from_the_guard_text(self):
        settings = {"enable_cooldown": False, "enable_signal_reset": False, "post_stop_state": {"time": "x"}}
        out = phase(fake_engine(), settings, {"signals": SIGNALS, "action": "HOLD",
                                              "reason": "Post-stop stabilization active (2 completed candles remaining)"})
        self.assertEqual(out["label"], "POST-STOP")

    def test_open_position_is_exit_watch_whatever_the_gates_say(self):
        eng = fake_engine(last_trade=NOW - timedelta(minutes=1))
        out = phase(eng, {"enable_cooldown": True}, {"price_raw": 100.0, "exit_watch_details": {"stop_loss": 99.0}}, has_position=True)
        self.assertEqual(out["phase"], "EXIT WATCH")

    def test_strategy_preset_and_options_bots_are_not_described(self):
        self.assertIsNone(phase(fake_engine(), {"options_trading": True}, {"signals": SIGNALS}))
        preset = {"strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_variant": "sma_20_50"}
        self.assertIsNone(phase(fake_engine(), preset, {"signals": SIGNALS}))

    def test_liquidity_hold_back_is_carried_into_entry_watch(self):
        signals = {"A": {"signal": "BULLISH", "enabled": True}}
        out = phase(fake_engine(), {"min_buy_signals": 1, "enable_cooldown": False},
                    {"signals": signals, "action": "HOLD", "reason": "Liquidity filter: spread 0.59% exceeds 0.50%"})
        self.assertIn("held back: Liquidity filter", out["headline"])


if __name__ == "__main__":
    unittest.main()
