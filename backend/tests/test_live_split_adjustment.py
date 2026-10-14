import unittest
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

import indicators
from corporate_actions import SplitEvent
from corporate_actions_service import SplitLookup

N, SPLIT_AT = 120, 60


def frame(raw=True, title=False):
    base = np.linspace(100.0, 160.0, N)
    closes = base.copy()
    if raw:
        closes[SPLIT_AT:] = closes[SPLIT_AT:] / 4.0
    else:
        closes = base / 4.0
    index = pd.date_range("2026-01-05", periods=N, freq="B")
    names = ("Open", "High", "Low", "Close", "Volume") if title else ("open", "high", "low", "close", "volume")
    return pd.DataFrame(dict(zip(names, (closes, closes * 1.01, closes * 0.99, closes, np.full(N, 1000.0)))), index=index)


def event():
    return SplitEvent("SPY", pd.date_range("2026-01-05", periods=N, freq="B")[SPLIT_AT].date(), 4.0)


class LiveSplitAdjustmentTests(unittest.TestCase):
    def reconcile(self, df, lookup):
        with patch("corporate_actions_service.get_split_events", return_value=lookup):
            return indicators._reconcile_live_splits(df, "SPY")

    def test_raw_live_bars_are_adjusted_for_both_column_styles(self):
        for title in (False, True):
            adjusted, quality = self.reconcile(frame(raw=True, title=title), SplitLookup("ok", "alpaca", [event()]))
            close = adjusted["Close" if title else "close"]
            ratios = (close / close.shift(1)).dropna()
            self.assertTrue(((ratios > 0.97) & (ratios < 1.03)).all())
            self.assertEqual(quality, "OK")

    def test_already_adjusted_bars_are_left_alone(self):
        source = frame(raw=False)
        adjusted, quality = self.reconcile(source, SplitLookup("ok", "alpaca", [event()]))
        pd.testing.assert_frame_equal(adjusted, source)
        self.assertEqual(quality, "OK")

    def test_unavailable_events_with_a_split_like_jump_is_degraded_and_calm_data_is_ok(self):
        unavailable = SplitLookup("unavailable", "none", [])
        self.assertEqual(self.reconcile(frame(raw=True), unavailable)[1], "DEGRADED")
        self.assertEqual(self.reconcile(frame(raw=False), unavailable)[1], "OK")

    def test_a_failing_lookup_never_breaks_live_analysis(self):
        source = frame(raw=True)
        with patch("corporate_actions_service.get_split_events", side_effect=RuntimeError("boom")):
            out, quality = indicators._reconcile_live_splits(source, "SPY")
        pd.testing.assert_frame_equal(out, source)
        self.assertEqual(quality, "OK")


if __name__ == "__main__":
    unittest.main()
