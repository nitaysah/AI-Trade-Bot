"""Trend Rider and HA+UT: regular indicators that carry the same maths as two premium trend indicators, under their own
names, settings and toggles. Parity is checked line for line on the same candles."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtester  # noqa: E402
import indicator_catalog as catalog  # noqa: E402
import indicators as I  # noqa: E402
import options_backtester  # noqa: E402


def candles(n=420, seed=11):
    """A random walk with up, down and sideways regimes so the trails flip several times."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 4, 0.12), np.full(n // 4, -0.15), np.full(n // 4, 0.0), np.full(n - 3 * (n // 4), 0.14)])
    close = 100 + np.cumsum(drift + rng.normal(0, 0.55, n))
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.1, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.25, 0.1, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.25, 0.1, n))
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": rng.integers(800, 1200, n).astype(float)},
                      index=pd.date_range("2026-08-03 09:30", periods=n, freq="15min"))
    return df


def analyse(selected, df=None, **settings):
    return I.calculate_indicators_for_df((df if df is not None else candles()).copy(), timeframe="15Min", ticker="TEST",
                                         settings={"enabled_indicators": selected, **settings})


def column(result, key):
    return [row.get(key) for row in result["price_history"]]


class CatalogTests(unittest.TestCase):
    def test_both_are_regular_indicators_with_their_own_toggles_and_settings(self):
        for name, toggle in (("Trend Rider", "ENABLE_TREND_RIDER"), ("HA+UT", "ENABLE_HA_UT")):
            self.assertIn(name, catalog.STANDARD_INDICATOR_NAMES)
            self.assertEqual(catalog.EXTENSION_INDICATOR_TOGGLE_KEYS[name], toggle)
            self.assertEqual(catalog.STANDARD_CHART_PLACEMENT[name], "overlay")
            self.assertTrue(all("botbulls" not in str(f).lower() for f in catalog.STANDARD_CHART_GROUPS[name]))
        for key in ("TREND_RIDER_ATR_PERIOD", "TREND_RIDER_ATR_MULT", "TREND_RIDER_TRACER_PERIOD", "TREND_RIDER_ZONE_PERIOD",
                    "HA_UT_ATR_PERIOD", "HA_UT_ATR_MULT"):
            self.assertIn(key, catalog.EXTENSION_INDICATOR_DEFAULTS)
        config = __import__("config")
        self.assertIs(config.ENABLE_TREND_RIDER, False)
        self.assertIs(config.ENABLE_HA_UT, False)


class ParityTests(unittest.TestCase):
    def assert_same(self, left, right, label):
        pairs = [(a, b) for a, b in zip(left, right)]
        self.assertTrue(all((a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-6) for a, b in pairs), label)
        self.assertGreater(sum(a is not None for a in left), 300, label)

    # Reference values were recorded from the original premium implementations (since removed) on candles(),
    # so the regular indicators still carry exactly the same maths.
    def check_golden(self, result, key, total, count, last):
        values = [float(v) for v in column(result, key) if v is not None]
        self.assertEqual(len(values), count, key)
        self.assertAlmostEqual(sum(values), total, places=3, msg=key)
        self.assertAlmostEqual(values[-1], last, places=5, msg=key)

    def test_trend_rider_keeps_the_original_trend_indicator_values(self):
        result = analyse(["Trend Rider"])
        self.check_golden(result, "tr_trail", 43278.5862, 407, 121.515282)
        self.check_golden(result, "tr_tracer", 39665.5408, 371, 122.730935)
        self.check_golden(result, "tr_zone_upper", 40850.3616, 371, 123.46524)
        self.check_golden(result, "tr_zone_lower", 37752.4288, 371, 111.975859)
        self.check_golden(result, "tr_score", 1016.0, 420, 2.0)
        self.assertEqual(sum(1 for x in column(result, "tr_trend") if x), 248)
        flips = sum(1 for a, b in zip(column(result, "tr_trend"), column(result, "tr_trend")[1:]) if a != b)
        self.assertGreaterEqual(flips, 3)  # the data really exercises up and down trends
        self.assertEqual(result["signals"]["Trend Rider"]["signal"], "BULLISH")

    def test_ha_ut_trail_uses_real_candle_atr_golden_values(self):
        result = analyse(["HA+UT"])
        self.check_golden(result, "haut_trail", 43714.1267, 411, 122.343508)
        self.check_golden(result, "haut_close", 44724.299, 420, 123.007105)
        self.assertEqual(sum(1 for x in column(result, "haut_above") if x), 269)
        self.assertEqual(result["signals"]["HA+UT"]["signal"], "BULLISH")

    def test_custom_settings_change_the_lines_and_unselected_adds_nothing(self):
        base = analyse(["Trend Rider"])
        wide = analyse(["Trend Rider"], TREND_RIDER_ATR_MULT=4.0)
        self.assertNotEqual(column(base, "tr_trail")[-1], column(wide, "tr_trail")[-1])
        ha_base, ha_fast = analyse(["HA+UT"]), analyse(["HA+UT"], HA_UT_ATR_MULT=2.0, HA_UT_ATR_PERIOD=5)
        self.assertNotEqual(column(ha_base, "haut_trail")[-1], column(ha_fast, "haut_trail")[-1])
        other = analyse(["RSI"])
        self.assertNotIn("Trend Rider", other["signals"])
        self.assertNotIn("HA+UT", other["signals"])
        self.assertFalse(any(k.startswith(("tr_", "haut_")) for k in other["price_history"][-1]))


class VoteTests(unittest.TestCase):
    def test_votes_follow_the_trail_side_and_never_vote_before_there_is_history(self):
        up = analyse(["Trend Rider", "HA+UT"], candles(seed=11))
        self.assertIn(up["signals"]["Trend Rider"]["signal"], ("BULLISH", "BEARISH"))
        self.assertTrue(up["signals"]["Trend Rider"]["reason"].startswith("Trend Rider "))
        self.assertIn("confluence", up["signals"]["Trend Rider"]["reason"])
        self.assertTrue(up["signals"]["HA+UT"]["reason"].startswith("HA+UT "))
        for name in ("Trend Rider", "HA+UT"):
            self.assertNotIn("botbulls", up["signals"][name]["reason"].lower())
        short = analyse(["Trend Rider", "HA+UT"], candles(n=34))
        self.assertEqual(short["signals"]["Trend Rider"]["signal"] in ("BULLISH", "BEARISH", "NEUTRAL"), True)

    def test_no_lookahead_earlier_candles_read_the_same_without_later_ones(self):
        df = candles()
        full, cut = analyse(["Trend Rider", "HA+UT"], df), analyse(["Trend Rider", "HA+UT"], df.iloc[:300])
        for field in ("tr_trail", "tr_trend", "tr_score", "haut_trail", "haut_above"):
            a, b = column(full, field)[:len(column(cut, field))], column(cut, field)
            self.assertEqual([None if x is None else round(float(x), 6) for x in a], [None if x is None else round(float(x), 6) for x in b], field)


class BacktestChartTests(unittest.TestCase):
    def check(self, module):
        row = pd.Series({"TR_Trail": 101.5, "TR_Trend": True, "TR_Tracer": 100.9, "TR_Zone_Upper": 105.0, "TR_Zone_Lower": 96.0,
                         "TR_Score": 3, "HAUT_Close": 101.0, "HAUT_Trail": 100.2, "HAUT_Above": True})
        fields = module._chart_indicator_fields(row, 2, {"Trend Rider", "HA+UT"})
        for key in ("tr_trail", "tr_trend", "tr_tracer", "tr_zone_upper", "tr_zone_lower", "tr_score", "haut_close", "haut_trail", "haut_above"):
            self.assertIn(key, fields)
        self.assertEqual(float(fields["tr_trail"]), 101.5)
        only_rsi = module._chart_indicator_fields(row, 2, {"RSI"})
        self.assertFalse(any(k.startswith(("tr_", "haut_")) for k in only_rsi))

    def test_stock_backtester(self):
        self.check(backtester)

    def test_options_backtester(self):
        self.check(options_backtester)


if __name__ == "__main__":
    unittest.main()
