"""Backtest result analytics: annualisation, risk ratios, drawdown profile,
buy & hold edge, exposure, monthly returns, period split and trade tags."""

import io
import contextlib
import math
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest_metrics as bm  # noqa: E402


def fmt(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%SZ")


def series(values, start="2026-01-02", freq="D"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq), dtype=float)


class AnnualisationTests(unittest.TestCase):
    def test_fixed_sizing_annualises_in_a_straight_line(self):
        out = bm.annualized_return(10.0, 60, "fixed")
        self.assertEqual(out["annualised_roi_method"], "simple")
        self.assertAlmostEqual(out["annualised_roi_pct"], round(10.0 * 365 / 60, 2))
        self.assertTrue(out["annualised_is_projection"])

    def test_compound_sizing_annualises_with_compounding(self):
        out = bm.annualized_return(10.0, 60, "compound")
        self.assertEqual(out["annualised_roi_method"], "compounded")
        self.assertAlmostEqual(out["annualised_roi_pct"], round((1.10 ** (365 / 60) - 1) * 100, 2))

    def test_a_year_or_more_is_not_a_projection(self):
        self.assertFalse(bm.annualized_return(5.0, 400, "fixed")["annualised_is_projection"])

    def test_total_loss_compounds_to_minus_100(self):
        self.assertEqual(bm.annualized_return(-100.0, 30, "compound")["annualised_roi_pct"], -100.0)

    def test_basis_days_uses_the_tested_bars(self):
        idx = pd.date_range("2026-01-01", periods=31, freq="D")
        self.assertEqual(bm.basis_days(idx), 30.0)
        self.assertEqual(bm.basis_days(idx[:1]), 1.0)


class RiskRatioTests(unittest.TestCase):
    def test_sharpe_and_sortino_use_daily_equity_returns(self):
        rets = np.array([0.01, -0.005, 0.02, -0.01, 0.015, 0.0, 0.005])
        values = 1000 * np.cumprod(1 + rets)
        out = bm.risk_ratios(series(values), 1000.0, is_crypto=False)
        expected_sharpe = rets.mean() / rets.std(ddof=1) * math.sqrt(252)
        downside = math.sqrt(np.mean(np.minimum(rets, 0) ** 2))
        self.assertAlmostEqual(out["sharpe_ratio"], round(expected_sharpe, 2))
        self.assertAlmostEqual(out["sortino_ratio"], round(rets.mean() / downside * math.sqrt(252), 2))
        self.assertEqual(out["risk_ratio_basis"]["daily_returns"], len(rets))

    def test_intraday_bars_collapse_to_one_return_per_day(self):
        idx = pd.date_range("2026-01-05 09:30", periods=6 * 78, freq="5min")
        idx = idx[(idx.hour >= 9) & (idx.hour < 16)]
        values = np.linspace(1000, 1100, len(idx))
        returns = bm.daily_returns(pd.Series(values, index=idx), 1000.0)
        self.assertEqual(len(returns), len(set(idx.normalize())))

    def test_too_few_days_returns_none_with_a_reason(self):
        out = bm.risk_ratios(series([1000, 1010, 1005]), 1000.0, is_crypto=False)
        self.assertIsNone(out["sharpe_ratio"])
        self.assertIn("Needs at least", out["risk_ratio_basis"]["note"])

    def test_no_losing_day_gives_no_sortino(self):
        out = bm.risk_ratios(series([1000, 1010, 1020, 1035, 1040, 1050, 1060]), 1000.0, is_crypto=False)
        self.assertIsNone(out["sortino_ratio"])
        self.assertIsNotNone(out["sharpe_ratio"])

    def test_crypto_annualises_over_365_days(self):
        out = bm.risk_ratios(series([1000, 1010, 1000, 1020, 1015, 1030, 1025]), 1000.0, is_crypto=True)
        self.assertEqual(out["risk_ratio_basis"]["periods_per_year"], 365)


class DrawdownTests(unittest.TestCase):
    def test_worst_drawdown_has_peak_trough_and_recovery(self):
        eq = series([100, 110, 99, 105, 111, 108])
        prof = bm.drawdown_profile(eq, fmt)
        worst = prof["worst_drawdown"]
        self.assertAlmostEqual(worst["depth_pct"], 10.0)
        self.assertEqual(worst["peak_time"], fmt(eq.index[1]))
        self.assertEqual(worst["trough_time"], fmt(eq.index[2]))
        self.assertEqual(worst["recovery_time"], fmt(eq.index[4]))
        self.assertEqual(worst["days_to_recover"], 2.0)
        self.assertEqual(prof["longest_underwater"]["days"], 3.0)
        self.assertFalse(prof["longest_underwater"]["ongoing"])

    def test_an_unrecovered_drawdown_is_reported_as_ongoing(self):
        eq = series([100, 120, 90, 100])
        prof = bm.drawdown_profile(eq, fmt)
        self.assertFalse(prof["worst_drawdown"]["recovered"])
        self.assertIsNone(prof["worst_drawdown"]["recovery_time"])
        self.assertTrue(prof["longest_underwater"]["ongoing"])

    def test_a_curve_that_never_falls_has_no_drawdown(self):
        prof = bm.drawdown_profile(series([100, 101, 102]), fmt)
        self.assertIsNone(prof["worst_drawdown"])
        self.assertIsNone(prof["longest_underwater"])

    def test_max_drawdown_pct(self):
        self.assertAlmostEqual(bm.max_drawdown_pct([100, 120, 90, 130]), 25.0)


class BuyHoldEdgeTests(unittest.TestCase):
    def test_buy_hold_curve_starts_at_initial_capital(self):
        curve = bm.buy_hold_curve(series([50, 55, 45]), 1000.0)
        self.assertEqual(list(curve.round(2)), [1000.0, 1100.0, 900.0])

    def test_higher_return_per_drawdown_beats_buy_hold(self):
        edge = bm.edge_vs_buy_hold(10.0, 5.0, 30.0, 20.0)
        self.assertEqual(edge["verdict"], "beats")
        self.assertEqual(edge["strategy_return_per_drawdown"], 2.0)
        self.assertEqual(edge["buy_hold_return_per_drawdown"], 1.5)

    def test_lower_return_per_drawdown_lags(self):
        self.assertEqual(bm.edge_vs_buy_hold(10.0, 10.0, 30.0, 10.0)["verdict"], "lags")

    def test_a_profit_with_no_drawdown_beats_any_drawdown(self):
        edge = bm.edge_vs_buy_hold(2.0, 0.0, 40.0, 5.0)
        self.assertEqual(edge["verdict"], "beats")
        self.assertIsNone(edge["strategy_return_per_drawdown"])


class ExposureMonthlySplitTests(unittest.TestCase):
    def test_exposure_counts_bars_with_a_position(self):
        self.assertEqual(bm.exposure_pct(range(10), [(2, 5)]), 30.0)
        self.assertEqual(bm.exposure_pct(range(10), [(2, 5), (4, 7)]), 50.0)
        self.assertEqual(bm.exposure_pct(range(10), []), 0.0)

    def test_monthly_returns_chain_from_initial_capital(self):
        idx = pd.DatetimeIndex(["2026-01-10", "2026-01-30", "2026-02-15", "2026-02-27"])
        rows = bm.monthly_returns(pd.Series([1000, 1100, 1050, 990], index=idx, dtype=float), 1000.0)
        self.assertEqual(rows, [{"year": 2026, "month": 1, "return_pct": 10.0},
                                {"year": 2026, "month": 2, "return_pct": -10.0}])

    def test_period_split_assigns_trades_by_exit_time(self):
        eq = series([1000, 1050, 1100, 1080, 1060])
        unix = [int(pd.Timestamp(t, tz="UTC").timestamp()) for t in eq.index]
        trades = [{"pl": 50.0, "exit_time": eq.index[1]}, {"pl": -20.0, "exit_time": eq.index[4]}]
        halves = bm.period_split(eq, unix, 1000.0, trades,
                                 lambda t: int(pd.Timestamp(t["exit_time"], tz="UTC").timestamp()), fmt)
        self.assertEqual([h["trades"] for h in halves], [1, 1])
        self.assertEqual(halves[0]["return_pct"], 10.0)
        self.assertAlmostEqual(halves[1]["return_pct"], round((1060 / 1100 - 1) * 100, 2))
        self.assertEqual(halves[1]["win_rate_pct"], 0.0)


class ClassificationTests(unittest.TestCase):
    def test_exit_reasons_are_bucketed(self):
        cases = {
            "Stop Loss Hit ($98.00)": "stop",
            "Percentage Stop Loss Hit (5.0% SL, trailing)": "stop",
            "Stop Loss Hit ($68.66, 3×ATR protective stop)": "stop",
            "Take Profit Hit ($110.00)": "target",
            "Pivot Take Profit Hit (R1=$5.00)": "target",
            "Indicator Exit: 3 bearish signals (RSI)": "signal",
            "Strategy exit: Golden Cross exit condition met": "signal",
            "AI Exit: momentum faded": "signal",
            "Adaptive Grid L2 sell": "grid",
            "End of Backtest": "end",
            "": "other",
        }
        for reason, expected in cases.items():
            self.assertEqual(bm.classify_exit(reason), expected, reason)
        self.assertEqual(bm.classify_exit("Prior High Breakout (Trigger=$10)", "long"), "target")
        self.assertEqual(bm.classify_exit("Prior Low Breakdown (Trigger=$9)", "long"), "stop")

    def test_hold_hours(self):
        self.assertEqual(bm.hold_hours("2026-01-02 14:30:00Z", "2026-01-03 14:30:00Z"), 24.0)
        self.assertIsNone(bm.hold_hours(None, "2026-01-03"))


class BacktesterIntegrationTests(unittest.TestCase):
    """The real Backtester attaches every new field on a manual-mode run."""

    def test_full_result_carries_the_new_analytics(self):
        import backtester as btmod
        from backtester import Backtester

        n = 140
        idx = pd.date_range("2026-01-02", periods=n, freq="D")
        close = 100 + np.cumsum(np.where(np.arange(n) % 20 < 10, 1.0, -0.9))
        df = pd.DataFrame({"Open": close - 0.2, "High": close + 1, "Low": close - 1, "Close": close,
                           "Volume": np.full(n, 1e6), "ATR": np.full(n, 1.5)}, index=idx)
        calls = {"n": 0}

        def fake_entry(history, current_price=None, **kwargs):
            calls["n"] += 1
            return {"action": "BUY" if calls["n"] % 15 == 0 else "HOLD", "reason": "test", "structure_direction": "BULLISH"}

        original = btmod.build_entry_signal
        btmod.build_entry_signal = fake_entry
        try:
            bt = Backtester("TEST", "1Day", idx[0].to_pydatetime(), idx[-1].to_pydatetime(),
                            initial_capital=1000, trade_amount=1000, allocation_mode="fixed",
                            buy_mode="atr", sell_mode="sltp", atr_stop_multiplier=1.2, atr_take_profit_multiplier=1.5,
                            enable_risk=False, enable_cooldown=False, enable_signal_reset=False,
                            max_consecutive_losses=0, enable_liquidity_filter=False, post_stop_stabilization_bars=0)
            with contextlib.redirect_stdout(io.StringIO()):
                res = bt.run(precomputed_df=df)
        finally:
            btmod.build_entry_signal = original

        s = res["summary"]
        self.assertGreater(s["total_trades"], 0)
        self.assertEqual(s["annualised_roi_method"], "simple")
        days = bm.basis_days(pd.DatetimeIndex([e["time"] for e in bt.equity_history]))
        self.assertAlmostEqual(s["annualised_roi_pct"], round(s["roi_pct"] * 365 / days, 2), places=1)
        if s["max_drawdown_pct"] > 0 and s["annualised_roi_pct"] is not None:
            self.assertAlmostEqual(s["calmar_ratio"], round(s["annualised_roi_pct"] / s["max_drawdown_pct"], 2))
        for key in ("sharpe_ratio", "sortino_ratio", "risk_ratio_basis", "buy_hold_max_drawdown_pct",
                    "edge_vs_buy_hold", "exposure_pct", "worst_drawdown", "longest_underwater",
                    "monthly_returns", "period_split", "run_settings", "metrics_version"):
            self.assertIn(key, s)
        self.assertGreater(s["exposure_pct"], 0)
        self.assertEqual(len(s["period_split"]), 2)
        self.assertEqual(sum(h["trades"] for h in s["period_split"]), s["total_trades"])
        self.assertEqual(s["run_settings"]["fill_model"], "next_bar_open")
        self.assertEqual(s["run_settings"]["buy_mode"], "atr")
        times = [p["time"] for p in res["equity_curve"]]
        self.assertEqual([p["time"] for p in res["buy_hold_curve"]], times)
        self.assertAlmostEqual(res["buy_hold_curve"][-1]["equity"], s["buy_hold_equity"], places=1)
        for trade in res["trades"]:
            self.assertIn(trade["exit_category"], bm.EXIT_CATEGORIES)
            self.assertIsNotNone(trade["hold_hours"])


if __name__ == "__main__":
    unittest.main()
