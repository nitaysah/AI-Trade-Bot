import os
import sys
import unittest
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import allocation_strategy as A  # noqa: E402
import strategy_presets as sp  # noqa: E402


def closes_series(values, end="2026-09-30"):
    idx = pd.bdate_range(end=end, periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=idx)


class IndicatorTests(unittest.TestCase):
    def test_values_match_a_hand_calculation(self):
        c = list(np.linspace(100, 150, 300))
        v = A.indicator_values(c, 151.0)
        self.assertAlmostEqual(v["sma_slow"], (sum(c[-249:]) + 151.0) / 250)
        self.assertAlmostEqual(v["sma_fast"], (sum(c[-49:]) + 151.0) / 50)
        self.assertAlmostEqual(v["roc10"], (151.0 / c[-10] - 1) * 100)
        self.assertAlmostEqual(v["acc"], v["roc5"] - v["roc10"] / 2)

    def test_needs_250_completed_closes_and_a_price(self):
        self.assertIsNone(A.indicator_values([100.0] * 249, 100.0))
        self.assertIsNone(A.indicator_values([100.0] * 300, float("nan")))
        r = A.evaluate([100.0] * 10, 100.0, "long_only")
        self.assertEqual(r["status"], A.STATUS_WAITING)
        self.assertIsNone(r["weights"])

    def test_history_matches_single_evaluations_and_ignores_later_data(self):
        rng = np.random.default_rng(3)
        c = closes_series(100 * np.exp(np.cumsum(rng.normal(0, 0.012, 600))))
        prov = c * (1 + rng.normal(0, 0.003, len(c)))
        h = A.regime_history(c, prov, "long_inverse")
        for day in h.index[[260, 400, 599]]:
            i = c.index.get_loc(day)
            one = A.evaluate(c.iloc[:i].tolist(), float(prov.loc[day]), "long_inverse")
            self.assertEqual(one["regime"], h.loc[day, "regime"])
            self.assertAlmostEqual(one["weights"]["bear"], h.loc[day, "w_bear"])
        # Corrupting the decision day's own close and everything later leaves the decision unchanged.
        day = h.index[400]
        bad = c.copy()
        bad.loc[bad.index >= day] *= 5
        h2 = A.regime_history(bad, prov, "long_inverse")
        self.assertTrue(h.loc[:day, "regime"].equals(h2.loc[:day, "regime"]))


class RegimeTests(unittest.TestCase):
    def vals(self, price, slow, fast, roc10=0.0, acc=0.0):
        return {"price": price, "sma_slow": slow, "sma_fast": fast, "roc10": roc10, "acc": acc}

    def test_table(self):
        cases = [
            (self.vals(110, 100, 105), "long_only", "BULL_RIDER", 1.0, 0.0),
            (self.vals(110, 100, 105, -2.5, -2.0), "long_only", "DECEL_TRIM", 0.3, 0.0),
            (self.vals(110, 100, 112, -0.5), "long_only", "CONSOLIDATION", 0.5, 0.0),
            (self.vals(110, 100, 112, -1.5), "long_only", "DEFENSIVE", 0.2, 0.0),
            (self.vals(90, 100, 95, -2.0), "long_only", "BEAR_CRASH", 0.0, 0.0),
            (self.vals(90, 100, 95, -2.0), "long_inverse", "BEAR_CRASH", 0.0, 0.6),
            (self.vals(90, 100, 95, -1.0), "long_inverse", "BEAR_CHOP", 0.0, 0.2),
            (self.vals(90, 100, 85), "long_inverse", "CASH", 0.0, 0.0),
        ]
        for values, direction, regime, wb, wr in cases:
            got, w = A.classify(values, direction)
            self.assertEqual(got, regime, values)
            self.assertEqual((w["bull"], w["bear"]), (wb, wr))

    def test_ties_fall_on_the_cautious_side(self):
        self.assertEqual(A.classify(self.vals(100, 100, 95), "long_only")[0], "CASH")  # at the 250-day average
        self.assertEqual(A.classify(self.vals(110, 100, 110), "long_only")[0], "CONSOLIDATION")  # at the 50-day
        self.assertEqual(A.classify(self.vals(90, 100, 90), "long_inverse")[0], "CASH")


class RebalanceTests(unittest.TestCase):
    def test_band_sells_first_and_zero_target_closes_all(self):
        orders = A.rebalance_orders({"TQQQ": 0.3, "SQQQ": 0.6}, {"TQQQ": 100.0, "SQQQ": 0.0}, {"TQQQ": 50.0, "SQQQ": 20.0}, 5000.0)
        self.assertEqual([o["side"] for o in orders], ["SELL", "BUY"])
        self.assertAlmostEqual(orders[0]["notional"], 5000 - 1500)
        self.assertAlmostEqual(orders[1]["notional"], 3000)
        close = A.rebalance_orders({"TQQQ": 0.0}, {"TQQQ": 0.5}, {"TQQQ": 10.0}, 10000.0)
        self.assertTrue(close[0]["close_all"])  # a 0% target always closes, even a tiny leg

    def test_inside_the_band_nothing_trades(self):
        self.assertEqual(A.rebalance_orders({"TQQQ": 1.0}, {"TQQQ": 96.0}, {"TQQQ": 100.0}, 10000.0), [])
        self.assertEqual(len(A.rebalance_orders({"TQQQ": 1.0}, {"TQQQ": 94.0}, {"TQQQ": 100.0}, 10000.0)), 1)

    def test_missing_price_for_a_traded_leg_raises(self):
        with self.assertRaises(ValueError):
            A.rebalance_orders({"TQQQ": 1.0}, {}, {"TQQQ": 0.0}, 1000.0)

    def test_sleeve_fixed_vs_compound(self):
        self.assertEqual(A.sleeve_value("fixed", 1000, 500, 3000, 2000), 1000)
        self.assertEqual(A.sleeve_value("compound", 1000, 500, 3000, 2000), 2500)  # amount + realized + unrealized


class ExecTimeTests(unittest.TestCase):
    def test_default_is_15_minutes_before_the_close_and_tracks_early_closes(self):
        self.assertEqual(A.decision_time(date(2026, 10, 8), "close-15")["at"].strftime("%H:%M"), "15:45")
        early = A.decision_time(date(2026, 11, 27), "close-15")  # day after Thanksgiving, 1 PM close
        self.assertEqual(early["at"].strftime("%H:%M"), "12:45")
        moved = A.decision_time(date(2026, 11, 27), "15:30")
        self.assertEqual((moved["at"].strftime("%H:%M"), moved["adjusted"]), ("12:45", True))
        self.assertIsNone(A.decision_time(date(2026, 12, 25), "close-15"))
        self.assertIsNone(A.decision_time(date(2026, 10, 10), "close-15"))  # Saturday

    def test_custom_time_bounds(self):
        self.assertEqual(A.normalize_exec_time("14:07"), "14:07")
        self.assertEqual(A.normalize_exec_time(""), "close-15")
        for bad in ("09:30", "15:56", "16:00", "25:00", "abc", "close-20", "close-390", "close-0"):
            self.assertIsNone(A.normalize_exec_time(bad), bad)
        self.assertEqual(A.normalize_exec_time("T-30"), "close-30")

    def test_t_minus_options_in_15_minute_steps_with_t15_first(self):
        opts = A.exec_time_options()
        values = [o["value"] for o in opts]
        self.assertEqual(values[0], "close-15")
        self.assertIn("Recommended", opts[0]["label"])
        self.assertTrue(opts[0]["label"].startswith("T-15 · 2:45 PM CT"))
        self.assertEqual(values, [f"close-{m}" for m in range(15, 376, 15)])
        self.assertTrue(opts[-1]["label"].startswith("T-375 · 8:45 AM CT"))
        self.assertEqual(A.decision_time(date(2026, 10, 8), "close-60")["at"].strftime("%H:%M"), "15:00")
        early = A.decision_time(date(2026, 11, 27), "close-30")  # 1 PM close
        self.assertEqual(early["at"].strftime("%H:%M"), "12:30")
        clamp = A.decision_time(date(2026, 11, 27), "close-375")  # would be 6:45 AM: earliest is 9:35
        self.assertEqual((clamp["at"].strftime("%H:%M"), clamp["adjusted"]), ("09:35", True))


class SettingsTests(unittest.TestCase):
    ON = {"strategy_preset_enabled": True, "strategy_preset": "malik_qqq"}

    def test_legs_and_validation(self):
        self.assertEqual(A.resolve_legs("malik_qqq", {**self.ON, "strategy_preset_variant": "long_only"}), {"bull": "TQQQ", "bear": None})
        self.assertEqual(A.resolve_legs("malik_qqq", {**self.ON, "strategy_preset_variant": "long_inverse", "allocation_bull_etf": "QLD",
                                                      "allocation_bear_etf": "QID"}), {"bull": "QLD", "bear": "QID"})
        self.assertEqual(sp.validate_strategy_preset(self.ON, ticker="QQQ"), [])
        self.assertTrue(sp.validate_strategy_preset(self.ON, ticker="TQQQ"))
        self.assertTrue(sp.validate_strategy_preset({**self.ON, "allocation_bull_etf": "SOXL"}, ticker="QQQ"))

    def test_regime_table_matches_the_rules(self):
        table = {r["id"]: r for r in A.regime_table()}
        self.assertEqual(list(table), list(A.REGIMES))
        self.assertEqual((table["BULL_RIDER"]["bull_pct"], table["DECEL_TRIM"]["bull_pct"], table["CONSOLIDATION"]["bull_pct"],
                          table["DEFENSIVE"]["bull_pct"]), (100, 30, 50, 20))
        self.assertEqual((table["BEAR_CRASH"]["bear_pct"], table["BEAR_CHOP"]["bear_pct"], table["CASH"]["bear_pct"]), (60, 20, 0))
        samples = {"BULL_RIDER": (110, 100, 105, 0, 0), "DECEL_TRIM": (110, 100, 105, -2.5, -2), "CONSOLIDATION": (110, 100, 112, -0.5, 0),
                   "DEFENSIVE": (110, 100, 112, -1.5, 0), "BEAR_CRASH": (90, 100, 95, -2, 0), "BEAR_CHOP": (90, 100, 95, -1, 0), "CASH": (90, 100, 85, 0, 0)}
        for rid, (p, ss, sf, r10, acc) in samples.items():
            regime, w = A.classify({"price": p, "sma_slow": ss, "sma_fast": sf, "roc10": r10, "acc": acc}, "long_inverse")
            self.assertEqual(regime, rid)
            self.assertEqual((round(w["bull"] * 100), round(w["bear"] * 100)), (table[rid]["bull_pct"], table[rid]["bear_pct"]))
        opt = next(o for o in sp.strategy_preset_options() if o["id"] == "malik_qqq")
        self.assertEqual(opt["allocation"]["regimes"], A.regime_table())

    def test_usd_ticker_is_not_offered(self):
        self.assertNotIn("USD", A.all_leg_symbols())

    def test_catalog_entry(self):
        opt = next(o for o in sp.strategy_preset_options() if o["id"] == "malik_soxx")
        self.assertEqual(opt["kind"], "allocation")
        self.assertEqual(opt["allocation"]["signal"], "SOXX")
        self.assertIn("Did not pass", opt["allocation"]["research_note"])
        self.assertEqual([v["id"] for v in opt["variants"]], ["long_only", "long_inverse"])
        self.assertEqual(opt["allowed_timeframes"], ["1Day"])
        self.assertIsNone(sp.effective_scan_policy({**self.ON, "timeframe": "1Day"}))
        self.assertEqual(sp.evaluate_strategy_preset(None, "malik_qqq")["status"], sp.STATUS_INVALID)


class SimulateTests(unittest.TestCase):
    def build(self, w_bull, prices, w_bear=None):
        days = pd.bdate_range("2026-01-05", periods=len(w_bull))
        plan = pd.DataFrame({"fill_day": days, "decision_time": "15:45", "regime": "BULL_RIDER", "w_bull": w_bull,
                             "w_bear": w_bear if w_bear is not None else [0.0] * len(w_bull),
                             "fill_bull": prices, "fill_bear": [10.0] * len(w_bull)}, index=days)
        return plan, {"TQQQ": pd.Series(prices, index=days), "SQQQ": pd.Series([10.0] * len(days), index=days)}, days

    def test_equity_is_cash_plus_positions_and_trades_book_pl(self):
        plan, marks, days = self.build([1.0, 1.0, 0.0], [100.0, 110.0, 120.0])
        out = A.simulate(plan, marks, bull="TQQQ", bear=None, initial_capital=1000, allocation_mode="compound", calendar=days)
        self.assertAlmostEqual(out["daily"]["equity"].iloc[-1], 1200.0)
        self.assertEqual(len(out["trades"]), 1)
        self.assertAlmostEqual(out["trades"][0]["pl"], 200.0)

    def test_fixed_mode_trims_gains_back_to_the_amount(self):
        plan, marks, days = self.build([1.0, 1.0], [100.0, 120.0])
        out = A.simulate(plan, marks, bull="TQQQ", bear=None, initial_capital=1000, allocation_mode="fixed", base_amount=1000, calendar=days)
        sells = [f for f in out["fills"] if f["side"] == "SELL"]
        self.assertAlmostEqual(sells[0]["notional"], 200.0)  # 20% drift > 5% band, back to $1,000

    def test_max_position_caps_a_buy(self):
        plan, marks, days = self.build([1.0], [100.0])
        out = A.simulate(plan, marks, bull="TQQQ", bear=None, initial_capital=1000, max_position_fraction=0.25, calendar=days)
        self.assertAlmostEqual(out["fills"][0]["notional"], 250.0)
        self.assertEqual(out["fills"][0]["limit"], "max_position")


if __name__ == "__main__":
    unittest.main()
