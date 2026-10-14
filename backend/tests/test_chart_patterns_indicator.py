"""Chart Patterns: a regular indicator (bullish/bearish flag + pattern name, like Candle Patterns) that reads the
causal chart-pattern engine's confirmed-breakout signal."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indicator_catalog as catalog  # noqa: E402
import indicators as I  # noqa: E402


def bull_flag(flip=False, breakout_bars=2):
    """Quiet base, a strong 12-bar pole, a shallow orderly pullback on light volume, then a decisive breakout."""
    rng = np.random.default_rng(7)
    price, rows = 100.0, []

    def bar(o, c, vol):
        hi = max(o, c) + abs(rng.normal(0.08, 0.02))
        lo = min(o, c) - abs(rng.normal(0.08, 0.02))
        rows.append((o, hi, lo, c, vol))

    for _ in range(80):
        c = price + rng.normal(0, 0.15)
        bar(price, c, 1000 + rng.integers(0, 100))
        price = c
    for _ in range(12):
        c = price + 0.9
        bar(price, c, 2600)
        price = c
    top = price
    for i in range(9):
        c = top - 0.35 - i * 0.06 + rng.normal(0, 0.03)
        bar(price, c, 650)
        price = c
    upper = max(r[1] for r in rows[-9:])
    for _ in range(breakout_bars):
        c = upper + 1.6
        bar(price, c, 4200)
        price = c
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"])
    df.index = pd.date_range("2026-09-14 09:30", periods=len(df), freq="5min")
    if flip:
        pivot = 200.0
        o, h, l, c = (df[k] for k in ("Open", "High", "Low", "Close"))
        df["Open"], df["High"], df["Low"], df["Close"] = pivot - o, pivot - l, pivot - h, pivot - c
    return df


def analyse(df, selected):
    return I.calculate_indicators_for_df(df.copy(), timeframe="5Min", ticker="TEST", settings={"enabled_indicators": selected})


def last_row(result):
    return (result.get("price_history") or [])[-1]


class CatalogTests(unittest.TestCase):
    def test_it_is_one_of_the_regular_indicators_and_is_not_named_after_a_premium_indicator(self):
        self.assertIn("Chart Patterns", catalog.STANDARD_INDICATOR_NAMES)
        self.assertEqual(catalog.EXTENSION_INDICATOR_TOGGLE_KEYS["Chart Patterns"], "ENABLE_CHART_PATTERNS")
        self.assertIn("Chart Patterns", catalog.STANDARD_CHART_GROUPS)
        self.assertEqual(catalog.STANDARD_CHART_PLACEMENT["Chart Patterns"], "overlay")
        self.assertFalse(any("botbulls" in str(v).lower() for v in catalog.STANDARD_CHART_GROUPS["Chart Patterns"]))
        self.assertIs(__import__("config").ENABLE_CHART_PATTERNS, False)  # off in global confluence until chosen, like the other extensions


class SignalTests(unittest.TestCase):
    def test_a_confirmed_bull_flag_is_one_bullish_vote_with_its_name(self):
        result = analyse(bull_flag(), ["Chart Patterns"])
        row = last_row(result)
        self.assertEqual((row["cp_name"], row["cp_bullish"], row["cp_bearish"]), ("Bull Flag", True, False))
        self.assertGreater(row["cp_quality"], 70)
        vote = result["signals"]["Chart Patterns"]
        self.assertEqual(vote["signal"], "BULLISH")
        self.assertEqual(vote["reason"], f"Pattern: Bull Flag breakout confirmed ({row['cp_quality']:.0f}% setup quality)")
        self.assertEqual(result["bullish_count"], 1)
        self.assertNotIn("botbulls", vote["reason"].lower())

    def test_the_mirrored_series_is_a_bearish_vote(self):
        result = analyse(bull_flag(flip=True), ["Chart Patterns"])
        row = last_row(result)
        self.assertEqual((row["cp_name"], row["cp_bullish"], row["cp_bearish"]), ("Bear Flag", False, True))
        self.assertEqual(result["signals"]["Chart Patterns"]["signal"], "BEARISH")
        self.assertEqual(result["bearish_count"], 1)

    def test_it_agrees_with_the_pattern_engine_it_reads(self):
        result = analyse(bull_flag(), ["Chart Patterns"])
        engine = result["df"].iloc[-1]
        self.assertEqual(result["signals"]["Chart Patterns"]["signal"], "BULLISH")
        self.assertEqual(int(engine["BB5_Signal"]), 1)
        self.assertEqual(round(last_row(result)["cp_quality"], 2), round(float(engine["BB5_Signal_Confidence"]), 2))

    def test_quiet_markets_and_unselected_indicator_stay_neutral_and_absent(self):
        rng = np.random.default_rng(3)
        close = 100 + np.cumsum(rng.normal(0, 0.1, 200))
        quiet = pd.DataFrame({"Open": close, "High": close + 0.1, "Low": close - 0.1, "Close": close, "Volume": 1000.0},
                             index=pd.date_range("2026-09-14 09:30", periods=200, freq="5min"))
        result = analyse(quiet, ["Chart Patterns"])
        self.assertEqual(result["signals"]["Chart Patterns"]["signal"], "NEUTRAL")
        row = last_row(result)
        self.assertEqual((row["cp_name"], row["cp_bullish"], row["cp_bearish"]), ("Neutral", False, False))
        other = analyse(bull_flag(), ["RSI"])
        self.assertNotIn("Chart Patterns", other["signals"])
        self.assertFalse(any(key.startswith("cp_") for key in last_row(other)))

    def test_a_forming_pattern_never_votes_before_the_breakout_candle_completes(self):
        full = bull_flag(breakout_bars=1)
        before = analyse(full.iloc[:-1], ["Chart Patterns"])      # the candle before the breakout
        self.assertEqual(before["signals"]["Chart Patterns"]["signal"], "NEUTRAL")
        after = analyse(full, ["Chart Patterns"])
        self.assertEqual(after["signals"]["Chart Patterns"]["signal"], "BULLISH")
        # no lookahead: every earlier candle reads the same whether or not the later candles exist
        earlier = {r["time"]: (r["cp_bullish"], r["cp_bearish"], r["cp_name"]) for r in before["price_history"]}
        for r in after["price_history"]:
            if r["time"] in earlier:
                self.assertEqual((r["cp_bullish"], r["cp_bearish"], r["cp_name"]), earlier[r["time"]], r["time"])

    def test_the_vote_is_not_forward_filled_into_later_candles(self):
        result = analyse(bull_flag(breakout_bars=2), ["Chart Patterns"])
        flags = [r["cp_bullish"] for r in result["price_history"]]
        self.assertFalse(all(flags[-20:]))  # only the confirming candles (and the engine's short hold), not a permanent state


class BacktestChartTests(unittest.TestCase):
    """The Backtester and options Backtester chart rows carry the same fields, only when the indicator is selected."""

    def check(self, module):
        row = pd.Series({"CP_Name": "Bull Flag", "CP_Bullish": True, "CP_Bearish": False, "CP_Quality": 88.0})
        selected = module._chart_indicator_fields(row, 2, {"Chart Patterns"})
        self.assertEqual((selected["cp_name"], selected["cp_bullish"], selected["cp_bearish"]), ("Bull Flag", True, False))
        self.assertEqual(selected["cp_quality"], 88.0)
        self.assertFalse(any(k.startswith("cp_") for k in module._chart_indicator_fields(row, 2, {"RSI"})))
        self.assertTrue(any(k.startswith("cp_") for k in module._chart_indicator_fields(row, 2, None)))

    def test_stock_backtester(self):
        import backtester
        self.check(backtester)

    def test_options_backtester(self):
        import options_backtester
        self.check(options_backtester)


if __name__ == "__main__":
    unittest.main()
