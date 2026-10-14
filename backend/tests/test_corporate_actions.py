import unittest
from datetime import date

import numpy as np
import pandas as pd

import corporate_actions as ca
import strategy_presets as sp


def daily_frame(closes, start="2026-01-05"):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start=start, periods=len(closes), freq="B")
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes,
         "volume": np.full(len(closes), 1000.0)},
        index=idx,
    )


def split_series(n=120, split_at=60, ratio=4.0, adjusted=False):
    """Smooth uptrend; raw data drops by 1/ratio at the split bar."""
    base = np.linspace(100.0, 160.0, n)
    values = base.copy()
    if not adjusted:
        values[split_at:] = values[split_at:] / ratio
    else:
        values[:split_at] = values[:split_at] / ratio
        values[split_at:] = values[split_at:] / ratio
        values = base / ratio
    return values


class SplitAdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.split_at = 60
        raw = daily_frame(split_series(split_at=self.split_at, ratio=4.0))
        self.ex_date = raw.index[self.split_at].date()
        self.event = ca.SplitEvent("TEST", self.ex_date, 4.0)
        self.raw = raw

    def test_forward_split_makes_a_continuous_series(self):
        adjusted = ca.apply_split_adjustment(self.raw, [self.event])
        ratios = (adjusted["close"] / adjusted["close"].shift(1)).dropna()
        self.assertTrue(((ratios > 0.97) & (ratios < 1.03)).all())
        # volume scales inversely for the pre-split bars only
        self.assertEqual(adjusted["volume"].iloc[0], 4000.0)
        self.assertEqual(adjusted["volume"].iloc[-1], 1000.0)

    def test_reverse_split_makes_a_continuous_series(self):
        base = np.linspace(10.0, 16.0, 120)
        raw = base.copy()
        raw[60:] = raw[60:] * 10  # 1-for-10 reverse split: price jumps 10x
        frame = daily_frame(raw)
        event = ca.SplitEvent("TEST", frame.index[60].date(), 0.1)
        adjusted = ca.apply_split_adjustment(frame, [event])
        ratios = (adjusted["close"] / adjusted["close"].shift(1)).dropna()
        self.assertTrue(((ratios > 0.9) & (ratios < 1.12)).all())
        self.assertAlmostEqual(adjusted["volume"].iloc[0], 100.0)

    def test_apply_is_idempotent(self):
        once = ca.apply_split_adjustment(self.raw, [self.event])
        twice = ca.apply_split_adjustment(once, [self.event])
        pd.testing.assert_frame_equal(once, twice)

    def test_raw_data_is_detected_and_adjusted_exactly_once(self):
        self.assertEqual(ca.classify_split(self.raw, self.event)["status"], ca.STATUS_RAW)
        fixed, quality, _ = ca.reconcile_split_adjustment(self.raw, [self.event])
        self.assertEqual(quality, "OK")
        self.assertEqual(ca.classify_split(fixed, self.event)["status"], ca.STATUS_ADJUSTED)

    def test_already_adjusted_data_is_never_double_adjusted(self):
        adjusted = daily_frame(split_series(adjusted=True))
        self.assertEqual(ca.classify_split(adjusted, self.event)["status"], ca.STATUS_ADJUSTED)
        out, quality, _ = ca.reconcile_split_adjustment(adjusted, [self.event])
        self.assertEqual(quality, "OK")
        pd.testing.assert_frame_equal(out, adjusted)

    def test_event_outside_the_window_and_tiny_ratio_are_not_touched(self):
        outside = ca.SplitEvent("TEST", date(2020, 1, 2), 2.0)
        self.assertEqual(ca.classify_split(self.raw, outside)["status"], ca.STATUS_OUTSIDE)
        tiny = ca.SplitEvent("TEST", self.ex_date, 1.04)
        self.assertEqual(ca.classify_split(self.raw, tiny)["status"], ca.STATUS_INCONCLUSIVE)
        _, quality, _ = ca.reconcile_split_adjustment(self.raw, [tiny])
        self.assertEqual(quality, "DEGRADED")

    def test_unrecorded_split_like_jump_is_flagged(self):
        self.assertEqual(len(ca.suspect_split_jumps(self.raw)), 1)
        calm = daily_frame(np.linspace(100, 130, 80))
        self.assertEqual(ca.suspect_split_jumps(calm), [])
        crash = daily_frame(np.concatenate([np.full(40, 100.0), np.full(40, 60.0)]))
        self.assertEqual(ca.suspect_split_jumps(crash), [])  # -40% is not a common split ratio

    def test_make_split_event_rejects_bad_input(self):
        self.assertIsNone(ca.make_split_event("X", "2026-01-01", 1.0))
        self.assertIsNone(ca.make_split_event("X", "2026-01-01", "abc"))
        self.assertIsNone(ca.make_split_event("X", "not-a-date", 2))
        self.assertEqual(ca.make_split_event("x", "2026-03-02", 2).symbol, "X")


class NoFalseSignalTests(unittest.TestCase):
    """The reason splits matter: raw data fakes a death cross."""

    def build(self):
        n = 400
        closes = np.linspace(100.0, 200.0, n)
        raw = closes.copy()
        raw[300:] = raw[300:] / 4.0
        frame = daily_frame(raw, start="2024-01-02")
        event = ca.SplitEvent("TEST", frame.index[300].date(), 4.0)
        return frame, event

    def test_raw_series_fakes_an_exit_but_adjusted_series_does_not(self):
        raw, event = self.build()
        raw_frame = sp.compute_strategy_frame(raw, "golden_cross", "sma_20_50")
        fake_exits = list(raw_frame.index[raw_frame["SP_exit_event"]])
        self.assertEqual([d.date() for d in fake_exits], [event.ex_date])  # false death cross on the split day
        fixed, _, _ = ca.reconcile_split_adjustment(raw, [event])
        fixed_frame = sp.compute_strategy_frame(fixed, "golden_cross", "sma_20_50")
        self.assertFalse(fixed_frame["SP_exit_cond"].any())
        self.assertFalse(fixed_frame["SP_exit_event"].any())


class RescaleTests(unittest.TestCase):
    def setUp(self):
        self.event = ca.SplitEvent("AAPL", date(2026, 6, 9), 4.0)
        self.settings = {
            "custom_buy_price": 200.0,
            "custom_stop_loss_price": 180.0,
            "custom_take_profit_price": 240.0,
            "entry_grid_anchor_price": 205.0,
            "entry_grid_anchor_atr": 4.0,
            "exit_grid_anchor_qty": 10,
            "exit_grid_pending_before_qty": 10,
            "strategy_preset_stop": {"stop_atr_mult": 3.0, "entry_atr": 4.0},
            "stop_loss_pct": 5.0,
            "entry_atr_multiplier": 1.5,
            "risk_per_trade": 2.0,
        }

    def test_prices_divide_quantities_multiply_percentages_untouched(self):
        out, changes = ca.rescale_price_levels(self.settings, self.event)
        self.assertEqual(out["custom_buy_price"], 50.0)
        self.assertEqual(out["custom_stop_loss_price"], 45.0)
        self.assertEqual(out["entry_grid_anchor_atr"], 1.0)
        self.assertEqual(out["exit_grid_anchor_qty"], 40.0)
        self.assertEqual(out["strategy_preset_stop"], {"stop_atr_mult": 3.0, "entry_atr": 1.0})
        for scale_free in ("stop_loss_pct", "entry_atr_multiplier", "risk_per_trade"):
            self.assertEqual(out[scale_free], self.settings[scale_free])
        self.assertIn("custom_buy_price", changes)
        self.assertNotIn("stop_loss_pct", changes)

    def test_reverse_split_inverts_the_scaling(self):
        reverse = ca.SplitEvent("XYZ", date(2026, 6, 9), 0.1)
        out, _ = ca.rescale_price_levels({"custom_buy_price": 2.0, "exit_grid_anchor_qty": 100}, reverse)
        self.assertAlmostEqual(out["custom_buy_price"], 20.0)
        self.assertAlmostEqual(out["exit_grid_anchor_qty"], 10.0)

    def test_zero_missing_and_non_numeric_values_are_skipped(self):
        out, changes = ca.rescale_price_levels({"custom_buy_price": 0, "custom_stop_loss_price": "x"}, self.event)
        self.assertEqual(changes, {})
        self.assertEqual(out["custom_buy_price"], 0)

    def test_input_is_not_mutated_and_quantity_helpers(self):
        snapshot = dict(self.settings)
        ca.rescale_price_levels(self.settings, self.event)
        self.assertEqual(self.settings, snapshot)
        self.assertEqual(ca.rescale_quantity(10, self.event), 40.0)
        self.assertTrue(ca.quantity_matches(40, 39.6))
        self.assertFalse(ca.quantity_matches(40, 30))
        self.assertTrue(ca.has_pending_order({"entry_grid_pending_order_id": "abc"}))
        self.assertFalse(ca.has_pending_order({}))


if __name__ == "__main__":
    unittest.main()
