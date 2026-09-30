import unittest
import asyncio
from unittest.mock import patch

from screener_service import ScreenerUserState
from trader import get_confluence_decision
from user_config import UserConfig, _active_user_config, set_user_config


class ScreenerSignalIntegrityTests(unittest.TestCase):
    def setUp(self):
        # The full suite includes isolated async tests that close the default
        # loop.  ScreenerUserState creates asyncio locks during construction,
        # so provide a fresh loop when this synchronous test class runs after
        # those tests.
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

    def test_zero_alert_cooldown_allows_new_direction_after_neutral_candle(self):
        state = ScreenerUserState("signal-integrity", None)
        state.profiles["AAPL"] = {
            "source": "screener",
            "enabled": True,
            "indicators": ["RSI"],
            "min_buy_signals": 1,
            "min_sell_signals": 1,
            "alert_bullish_enabled": True,
            "alert_bearish_enabled": True,
            "alert_cooldown_minutes": 0,
        }

        def evaluation(direction, candle_key):
            return {
                "evaluation_status": "valid",
                "direction": direction,
                "price_raw": 100.0,
                "reason": direction,
                "signals": {"RSI": {"signal": direction}},
                "candle_meta": {"candle_key": candle_key, "candle_close_ts": int(candle_key)},
            }

        self.assertIsNotNone(state.commit_evaluation("AAPL", evaluation("BULLISH", "1"), now_ts=1000))
        self.assertIsNone(state.commit_evaluation("AAPL", evaluation("NEUTRAL", "2"), now_ts=1010))
        self.assertIsNotNone(state.commit_evaluation("AAPL", evaluation("BULLISH", "3"), now_ts=1020))

    def test_hybrid_swing_requires_indicator_and_swing_confirmation(self):
        config = UserConfig("signal-integrity")
        config.TICKER_SETTINGS = {
            "AAPL": {
                "indicators": ["RSI"],
                "min_buy_signals": 1,
                "min_sell_signals": 1,
                "buy_mode": "hybrid_swing",
                "swing_entry_mode": "bounce_retest",
            }
        }
        token = set_user_config(config)
        try:
            analysis = {
                "price": 100.0,
                "atr": 1.0,
                "price_history": [],
                "signals": {"RSI": {"signal": "BULLISH"}},
            }
            with patch("trader.build_entry_signal", return_value={
                "action": "HOLD",
                "structure_direction": "NONE",
                "reason": "No swing setup",
            }):
                waiting = get_confluence_decision("AAPL", analysis, use_bot_settings=True)
            self.assertEqual(waiting["action"], "HOLD")
            self.assertIn("waiting for bullish Swing", waiting["reason"])

            structure_only = dict(analysis, signals={"RSI": {"signal": "NEUTRAL"}})
            with patch("trader.build_entry_signal", return_value={
                "action": "BUY",
                "structure_direction": "BULLISH",
                "reason": "Bullish swing confirmed",
            }):
                structure_waiting = get_confluence_decision("AAPL", structure_only, use_bot_settings=True)
            self.assertEqual(structure_waiting["action"], "HOLD")
            self.assertIn("only waiting for the indicator signals", structure_waiting["reason"])

            with patch("trader.build_entry_signal", return_value={
                "action": "BUY",
                "structure_direction": "BULLISH",
                "reason": "Bullish swing confirmed",
            }):
                triggered = get_confluence_decision("AAPL", analysis, use_bot_settings=True)
            self.assertEqual(triggered["action"], "BUY")
        finally:
            _active_user_config.reset(token)

    def test_empty_indicator_pool_is_explicitly_blocked_in_scan_log(self):
        from main import _build_screener_scan_log

        scan_log = _build_screener_scan_log(
            "AAPL",
            {
                "direction": "NEUTRAL",
                "evaluation_status": "valid",
                "reason": "No directional signal",
                "signals": {"RSI": {"signal": "NEUTRAL", "enabled": True}},
                "candle_meta": {"candle_key": "15MIN:1"},
                "price_raw": 100.0,
            },
            {
                "buy_mode": "indicator",
                "indicators": [],
                "min_buy_signals": 0,
                "min_sell_signals": 0,
            },
            {},
        )
        self.assertEqual(scan_log["status"], "NO_INDICATORS")
        self.assertEqual(scan_log["indicator_progress_label"], "No indicator pool selected")
        self.assertIn("select at least one indicator", scan_log["final_summary"])

        exit_log = _build_screener_scan_log(
            "AAPL",
            {
                "direction": "NEUTRAL",
                "evaluation_status": "valid",
                "reason": "No directional signal",
                "signals": {},
                "candle_meta": {"candle_key": "15MIN:2"},
                "price_raw": 100.0,
            },
            {"buy_mode": "indicator", "sell_mode": "indicator", "indicators": []},
            {"virtual_position": True},
        )
        self.assertEqual(exit_log["status"], "NO_INDICATORS")
        self.assertIn("Watch unavailable", exit_log["final_summary"])

    def test_directional_watch_evaluates_both_sides_independently(self):
        from main import _build_screener_scan_log

        scan_log = _build_screener_scan_log(
            "AAPL",
            {
                "direction": "BEARISH",
                "technical_direction": "BEARISH",
                "evaluation_status": "valid",
                "reason": "Bearish setup",
                "signals": {
                    "RSI": {"signal": "BEARISH", "reason": "Below range"},
                    "EMA Cross": {"signal": "BULLISH", "reason": "Cross up"},
                },
                "candle_meta": {"candle_key": "30MIN:3"},
                "price_raw": 100.0,
            },
            {
                "buy_mode": "indicator",
                "sell_mode": "indicator",
                "indicators": ["RSI", "EMA Cross"],
                "min_buy_signals": 2,
                "min_sell_signals": 2,
                "strategy_mode": "technical",
            },
            {
                "virtual_position": True,
                "direction_watch_mode": "BOTH",
                "direction_watch_label": "Both directions",
                "direction_watch_directions": ["BULLISH", "BEARISH"],
                "direction_watch_bullish_enabled": True,
                "direction_watch_bearish_enabled": True,
            },
        )
        self.assertEqual(scan_log["direction_watch_mode"], "BOTH")
        self.assertEqual(scan_log["target_direction"], "BOTH")
        self.assertEqual(scan_log["phase"], "WATCH")
        self.assertEqual(scan_log["bullish_confirmed_weight"], 1)
        self.assertEqual(scan_log["bearish_confirmed_weight"], 1)
        self.assertIn("Both directions", scan_log["final_summary"])

    def test_empty_indicator_pool_cannot_trigger_indicator_decision(self):
        config = UserConfig("empty-indicator-decision")
        config.TICKER_SETTINGS = {
            "AAPL": {
                "indicators": [],
                "min_buy_signals": 0,
                "min_sell_signals": 0,
                "buy_mode": "indicator",
            }
        }
        token = set_user_config(config)
        try:
            result = get_confluence_decision(
                "AAPL",
                {"price": 100.0, "price_history": [], "signals": {"RSI": {"signal": "BULLISH"}}},
                use_bot_settings=True,
            )
            self.assertEqual(result["action"], "HOLD")
            self.assertIn("no indicators selected", result["reason"])
        finally:
            _active_user_config.reset(token)


if __name__ == "__main__":
    unittest.main()
