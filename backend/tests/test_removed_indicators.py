"""Premium indicators (BotBulls1/2/3/5 and Botbulls4 News AI) are gone. Saved bots, Screener profiles and backtest
requests that still list them keep working with the remaining indicators."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import indicators as I  # noqa: E402
from removed_indicators import is_removed, strip_removed  # noqa: E402

PREMIUM = ["BotBulls1", "BotBulls2", "BotBulls3", "Botbulls4 News AI", "BotBulls5"]


class StripTests(unittest.TestCase):
    def test_only_the_removed_names_are_dropped_in_order(self):
        kept, removed = strip_removed(["RSI", "BotBulls2", "SMA", "Botbulls4 News AI", "Chart Patterns", "BotBulls5"])
        self.assertEqual(kept, ["RSI", "SMA", "Chart Patterns"])
        self.assertEqual(removed, ["BotBulls2", "Botbulls4 News AI", "BotBulls5"])
        self.assertTrue(all(is_removed(name) for name in PREMIUM))
        self.assertFalse(is_removed("Trend Rider"))
        self.assertEqual(strip_removed(None), (None, []))


class SavedBotTests(unittest.TestCase):
    def decide(self, indicators):
        from user_config import UserConfig, set_user_config, _active_user_config
        from trader import get_confluence_decision
        config_ = UserConfig("removed-indicators")
        config_.TICKER_SETTINGS = {"AAPL": {"indicators": indicators, "min_buy_signals": 1, "min_sell_signals": 1, "buy_mode": "indicator"}}
        token = set_user_config(config_)
        try:
            signals = {"RSI": {"signal": "BULLISH", "weight": 1}, "BotBulls2": {"signal": "BEARISH", "weight": 1, "enabled": True}}
            return get_confluence_decision("AAPL", {"price": 100.0, "price_history": [], "signals": signals}, use_bot_settings=True)
        finally:
            _active_user_config.reset(token)

    def test_a_saved_bot_keeps_trading_on_its_remaining_indicators(self):
        result = self.decide(["RSI", "BotBulls2", "Botbulls4 News AI"])
        self.assertEqual(result["action"], "BUY")
        self.assertEqual(result["removed_indicators"], ["BotBulls2", "Botbulls4 News AI"])

    def test_a_bot_with_only_removed_indicators_holds_instead_of_trading(self):
        result = self.decide(["BotBulls1", "BotBulls3"])
        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(result["removed_indicators"], ["BotBulls1", "BotBulls3"])


class RemovalTests(unittest.TestCase):
    def test_catalog_and_config_no_longer_carry_the_premium_set(self):
        for name in ("ENABLE_BOTBULLS1", "ENABLE_BOTBULLS2", "ENABLE_BOTBULLS3", "ENABLE_BOTBULLS5", "BOTBULLS1_WT_CHANNEL",
                     "BOTBULLS2_ATR_MULT", "BOTBULLS3_ATR_MULT"):
            self.assertFalse(hasattr(config, name), name)
        self.assertTrue(hasattr(config, "BOTBULLS5_MIN_CONFIDENCE"))  # the Chart Patterns engine still reads these

    def test_full_analysis_returns_no_premium_signals_or_chart_fields(self):
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(5)
        close = 100 + np.cumsum(rng.normal(0, 0.4, 260))
        df = pd.DataFrame({"Open": close, "High": close + 0.3, "Low": close - 0.3, "Close": close, "Volume": 1000.0},
                          index=pd.date_range("2026-08-03 09:30", periods=260, freq="15min"))
        result = I.calculate_indicators_for_df(df, timeframe="15Min", ticker="TEST",
                                               settings={"enabled_indicators": ["RSI", "Chart Patterns"] + PREMIUM})
        self.assertFalse(any(name in result["signals"] for name in PREMIUM))
        row = result["price_history"][-1]
        self.assertFalse(any(key.startswith(("lx_", "ba1_", "wt", "bb5_")) for key in row))
        self.assertIn("Chart Patterns", result["signals"])


if __name__ == "__main__":
    unittest.main()
