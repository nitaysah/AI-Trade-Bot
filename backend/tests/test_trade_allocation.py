"""Fixed vs compound trade allocation: backtest sizing, the live-bot compounding rules and the Amount-edit rule.

Fixed: every buy uses the same Amount, whatever happened to earlier trades.
Compound: the next buy is the starting Amount plus the realized P/L so far.
"""
import io
import contextlib
import unittest
from datetime import timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

import backtester
from backtester import Backtester
from user_config import active_evaluation_context, active_ticker_context

AMOUNT = 1000.0


def whipsaw_frame(bars=900):
    """Choppy daily series: several round trips, most of them losers, then a few winners."""
    t = np.arange(bars)
    closes = 100 + 6 * np.sin(2 * np.pi * t / 37) + 4 * np.sin(2 * np.pi * t / 23) + 0.01 * t
    opens = np.concatenate([[closes[0]], closes[:-1] * 1.002])
    frame = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) + 0.7, "low": np.minimum(opens, closes) - 0.7,
                          "close": closes, "volume": 1e6}, index=pd.bdate_range("2020-01-02", periods=bars))
    frame.attrs.update({"data_source": "alpaca", "feed": "sip", "data_quality": "OK", "adjustment": "split"})
    return frame


def run_preset(mode, capital=AMOUNT, amount=AMOUNT, **kwargs):
    active_evaluation_context.set("backtest")
    active_ticker_context.set("TEST")
    frame = whipsaw_frame()
    params = dict(initial_capital=capital, trade_amount=amount, allocation_mode=mode, risk_per_trade=0.5, max_pos_pct=1.0,
                  enable_cooldown=False, strategy_preset="golden_cross", strategy_preset_variant="sma_20_50")
    params.update(kwargs)
    with patch.object(backtester, "get_historical_data", return_value=frame), contextlib.redirect_stdout(io.StringIO()):
        bt = Backtester("TEST", "1Day", frame.index[260].to_pydatetime(), frame.index[-1].to_pydatetime() + timedelta(days=1), **params)
        bt._entry_guard_allows = lambda *a, **k: (True, None)  # sizing only; guards are tested elsewhere
        return bt, bt.run()


def entries(result):
    return sorted(result["trades"], key=lambda t: t["entry_time"])


class PresetBacktestAllocationTests(unittest.TestCase):
    def test_fixed_buys_the_same_amount_every_time_even_after_losses(self):
        _, result = run_preset("fixed")  # starting capital == Amount, exactly what the Backtest page sends
        trades = entries(result)
        self.assertGreaterEqual(len(trades), 8)
        self.assertGreater(sum(1 for t in trades if t["pl"] < 0), 3, "the series must contain losing trades")
        for trade in trades:
            self.assertAlmostEqual(trade["entry_price"] * trade["qty"], AMOUNT, delta=0.5)

    def test_fixed_is_unchanged_by_a_larger_starting_capital(self):
        _, result = run_preset("fixed", capital=10 * AMOUNT)
        for trade in entries(result):
            self.assertAlmostEqual(trade["entry_price"] * trade["qty"], AMOUNT, delta=0.5)

    def test_compound_buys_the_starting_amount_plus_realized_pl_so_far(self):
        _, result = run_preset("compound")
        trades = entries(result)
        realized = 0.0
        for trade in trades:
            self.assertAlmostEqual(trade["entry_price"] * trade["qty"], AMOUNT + realized, delta=0.5)
            realized += trade["pl"]

    def test_a_smaller_max_position_still_caps_a_fixed_buy(self):
        _, result = run_preset("fixed", max_pos_pct=0.5)
        for trade in entries(result):
            self.assertLessEqual(trade["entry_price"] * trade["qty"], 0.5 * AMOUNT + 0.5)


class BacktestWithoutRiskCapsTests(unittest.TestCase):
    """The Backtest page runs with risk management off: the Amount alone decides the size."""

    def test_preset_buys_the_full_amount_even_when_tiny_risk_and_position_caps_are_supplied(self):
        _, result = run_preset("fixed", enable_risk=False, risk_per_trade=0.0001, max_pos_pct=0.05)
        trades = entries(result)
        self.assertGreaterEqual(len(trades), 8)
        for trade in trades:
            self.assertAlmostEqual(trade["entry_price"] * trade["qty"], AMOUNT, delta=0.5)

    def test_the_same_caps_do_shrink_a_buy_when_risk_management_is_on(self):
        _, result = run_preset("fixed", enable_risk=True, risk_per_trade=0.0001, max_pos_pct=0.05)
        for trade in entries(result):
            self.assertLess(trade["entry_price"] * trade["qty"], 0.1 * AMOUNT)

    def test_normal_path_skips_the_position_cap_and_the_high_volatility_size_cut(self):
        active_evaluation_context.set("backtest")
        active_ticker_context.set("TEST")
        bt = Backtester("TEST", "1Day", pd.Timestamp("2020-01-02").to_pydatetime(), pd.Timestamp("2021-01-02").to_pydatetime(),
                        initial_capital=AMOUNT, trade_amount=AMOUNT, allocation_mode="fixed", risk_per_trade=1.0,
                        max_pos_pct=0.05, enable_risk=False)
        bar = pd.Series({"Close": 100.0, "ATR": 6.0, "Open": 100.0, "High": 101.0, "Low": 99.0, "Volume": 1e6})  # ATR 6% of price
        with contextlib.redirect_stdout(io.StringIO()):
            bt._enter_trade(bar, pd.Timestamp("2020-06-01"), "test entry")
        self.assertAlmostEqual(bt.position["qty"] * 100.0, AMOUNT, places=6)
        self.assertNotIn("Volatility Protection", bt.position["reason"])


class StrategyIgnoresManualReentryRulesTests(unittest.TestCase):
    def trades(self, **kwargs):
        bt, result = run_preset("fixed", **kwargs)
        return bt, [(t["entry_time"], t["exit_time"], round(t["pl"], 4)) for t in entries(result)]

    def test_a_cooldown_signal_reset_or_smart_reentry_setting_changes_nothing_for_a_strategy(self):
        _, off = self.trades(enable_cooldown=False, enable_signal_reset=False, enable_smart_reentry=False)
        bt, on = self.trades(enable_cooldown=True, enable_signal_reset=True, enable_smart_reentry=True)
        self.assertGreaterEqual(len(off), 8)
        self.assertEqual(on, off)
        self.assertEqual((bt.enable_cooldown, bt.enable_signal_reset, bt.enable_smart_reentry), (False, False, False))


class NormalBacktestAllocationTests(unittest.TestCase):
    def enter(self, mode, equity):
        active_evaluation_context.set("backtest")
        active_ticker_context.set("TEST")
        bt = Backtester("TEST", "1Day", pd.Timestamp("2020-01-02").to_pydatetime(), pd.Timestamp("2021-01-02").to_pydatetime(),
                        initial_capital=AMOUNT, trade_amount=AMOUNT, allocation_mode=mode, risk_per_trade=0.5, max_pos_pct=1.0)
        bt.equity = equity
        bt.current_trade_amount = equity if mode == "compound" else AMOUNT
        bar = pd.Series({"Close": 100.0, "ATR": 0.5, "Open": 100.0, "High": 101.0, "Low": 99.0, "Volume": 1e6})
        with contextlib.redirect_stdout(io.StringIO()):
            bt._enter_trade(bar, pd.Timestamp("2020-06-01"), "test entry")
        return bt.position["qty"] * 100.0

    def test_fixed_keeps_the_amount_after_losses_and_after_gains(self):
        self.assertAlmostEqual(self.enter("fixed", 935.0), AMOUNT, places=6)
        self.assertAlmostEqual(self.enter("fixed", 1200.0), AMOUNT, places=6)

    def test_compound_follows_the_realized_balance(self):
        self.assertAlmostEqual(self.enter("compound", 935.0), 935.0, places=6)
        self.assertAlmostEqual(self.enter("compound", 1200.0), 1200.0, places=6)


class AmountEditRuleTests(unittest.TestCase):
    def setUp(self):
        import main
        self.rule = main._restart_compounding_when_amount_changed

    def test_editing_the_amount_in_compound_mode_restarts_compounding_at_the_new_amount(self):
        current = {"allocation_mode": "compound", "amount": 2000.0, "compound_amount": 1234.5}
        self.rule(current, {"allocation_mode": "compound", "amount": 1000.0, "compound_amount": 1234.5}, {"amount": 2000.0})
        self.assertEqual(current["compound_amount"], 2000.0)

    def test_saving_the_same_amount_keeps_the_running_compounded_value(self):
        current = {"allocation_mode": "compound", "amount": 1000.0, "compound_amount": 1234.5}
        self.rule(current, {"allocation_mode": "compound", "amount": 1000.0, "compound_amount": 1234.5}, {"amount": 1000.0})
        self.assertEqual(current["compound_amount"], 1234.5)

    def test_fixed_mode_and_missing_or_invalid_amounts_are_left_alone(self):
        fixed = {"allocation_mode": "fixed", "amount": 2000.0}
        self.rule(fixed, {"amount": 1000.0}, {"amount": 2000.0})
        self.assertNotIn("compound_amount", fixed)
        compound = {"allocation_mode": "compound", "amount": 1000.0, "compound_amount": 1100.0}
        for incoming in ({}, {"amount": None}, {"amount": "abc"}, {"amount": -5}, {"amount": float("nan")}):
            self.rule(compound, {"amount": 1000.0}, incoming)
        self.assertEqual(compound["compound_amount"], 1100.0)

    def test_options_amounts_follow_the_same_rule(self):
        current = {"allocation_mode": "fixed", "options_allocation_mode": "compound", "options_trade_amount": 300.0, "options_compound_amount": 410.0}
        self.rule(current, {"options_trade_amount": 100.0}, {"options_trade_amount": 300.0})
        self.assertEqual(current["options_compound_amount"], 300.0)


if __name__ == "__main__":
    unittest.main()
