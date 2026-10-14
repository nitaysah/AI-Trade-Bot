import unittest
from datetime import timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

import backtester
import strategy_presets as sp
import trader
from indicators import _required_indicator_history_bars
from risk_manager import RiskManager
from user_config import UserConfig, _active_user_config, active_evaluation_context, set_user_config


def one_cross_closes():
    return np.concatenate([np.linspace(100, 60, 120), np.linspace(60, 140, 120), np.linspace(140, 70, 120)])


def title_frame(closes, open_gap=0.01):
    closes = np.asarray(closes, dtype=float)
    index = pd.bdate_range("2020-01-02", periods=len(closes))
    opens = np.concatenate([[closes[0]], closes[:-1] * (1.0 + open_gap)])
    return pd.DataFrame(
        {"Open": opens, "High": np.maximum(opens, closes) + 0.5, "Low": np.minimum(opens, closes) - 0.5,
         "Close": closes, "Volume": 1_000_000.0},
        index=index,
    )


PRESET_SETTINGS = {
    "strategy_preset_enabled": True,
    "strategy_preset": "golden_cross",
    "strategy_preset_variant": "sma_20_50",
    "strategy_preset_catalog_version": sp.CATALOG_VERSION,
    "buy_mode": sp.PRESET_MODE,
    "sell_mode": sp.PRESET_MODE,
    "timeframe": "1Day",
    # Manual settings that must have no effect while a preset is active:
    "indicators": ["RSI", "MACD"],
    "min_buy_signals": 5,
    "min_sell_signals": 5,
}


class UserConfigCase(unittest.TestCase):
    def use_settings(self, settings, **attributes):
        config = UserConfig("preset-live")
        config.TICKER_SETTINGS = {"SPY": dict(settings)}
        for name, value in attributes.items():
            setattr(config, name, value)
        token = set_user_config(config)
        self.addCleanup(_active_user_config.reset, token)
        return config


class LiveDecisionTests(UserConfigCase):
    def setUp(self):
        self.use_settings(PRESET_SETTINGS)
        self.df = title_frame(one_cross_closes())

    def decide(self, upto, has_position=False, df=None, **analysis_extra):
        frame = (df if df is not None else self.df).iloc[: upto + 1]
        analysis = {"df": frame, "candle_meta": {"candle_status": "closed"}, **analysis_extra}
        return trader.get_confluence_decision("SPY", analysis, use_bot_settings=True, has_position=has_position, timeframe="1Day")

    def strategy_columns(self):
        frame = sp.compute_strategy_frame(self.df, "golden_cross", "sma_20_50")
        return frame

    def test_decision_matches_the_shared_rule_on_every_prefix(self):
        """Parity: live decision == Backtester decision rule on identical completed bars."""
        frame = self.strategy_columns()
        events = [int(i) for i in np.flatnonzero((frame["SP_entry"] | frame["SP_exit_event"]).to_numpy())]
        sampled = set(range(0, len(frame), 6))
        for event in events:  # every bar around a signal is checked exhaustively
            sampled.update(range(max(0, event - 3), min(len(frame), event + 4)))
        for pos in sorted(sampled):
            for has_position in (False, True):
                expected = sp.preset_action(has_position, bool(frame["SP_entry"].iloc[pos]), bool(frame["SP_exit_cond"].iloc[pos])) \
                    if bool(frame["SP_ready"].iloc[pos]) else "HOLD"
                got = self.decide(pos, has_position)
                self.assertEqual(got["action"], expected, (pos, has_position))

    def test_live_signal_bar_equals_the_bar_the_backtest_traded(self):
        start, end = self.df.index[0].to_pydatetime(), self.df.index[-1].to_pydatetime() + timedelta(days=1)
        lowered = self.df.rename(columns=str.lower)
        lowered.attrs.update({"data_source": "alpaca", "feed": "sip", "data_quality": "OK", "adjustment": "split"})
        with patch.object(backtester, "get_historical_data", return_value=lowered):
            bt = backtester.Backtester("SPY", "1Day", start, end, initial_capital=10_000, trade_amount=10_000,
                                       enable_cooldown=False, strategy_preset="golden_cross",
                                       strategy_preset_variant="sma_20_50")
            res = bt.run()
        trade = sorted(res["trades"], key=lambda t: t["entry_time"])[0]
        buy_bars = [pos for pos in range(len(self.df)) if self.decide(pos, False)["action"] == "BUY"]
        self.assertEqual(len(buy_bars), 1)
        self.assertEqual(self.df.index[buy_bars[0]].strftime("%Y-%m-%d"), trade["entry_signal_time"][:10])

    def test_manual_indicators_and_thresholds_do_not_influence_the_decision(self):
        baseline = [self.decide(pos)["action"] for pos in range(120, 200)]
        self.use_settings({**PRESET_SETTINGS, "indicators": [], "min_buy_signals": 1, "min_sell_signals": 1})
        self.assertEqual([self.decide(pos)["action"] for pos in range(120, 200)], baseline)

    def test_result_shape_and_state_based_direction(self):
        frame = self.strategy_columns()
        entry = int(np.flatnonzero(frame["SP_entry"].to_numpy())[0])
        decision = self.decide(entry + 2)
        self.assertEqual(decision["evaluation_status"], "valid")
        self.assertEqual(decision["indicator_direction"], "BULLISH")
        self.assertEqual((decision["bullish_count"], decision["bearish_count"]), (1, 0))
        self.assertEqual(decision["buy_mode"], sp.PRESET_MODE)
        self.assertEqual(decision["strategy_preset"]["status"], "READY")
        self.assertEqual(decision["news_alignment"], "ALIGNED")
        self.assertTrue(any(name.startswith("Strategy:") for name in decision["signals"]))

    def test_not_ready_degraded_or_open_candle_is_never_a_valid_neutral_signal(self):
        waiting = self.decide(10)
        self.assertEqual((waiting["action"], waiting["evaluation_status"]), ("HOLD", "waiting"))
        frame = self.strategy_columns()
        entry = int(np.flatnonzero(frame["SP_entry"].to_numpy())[0])
        degraded = self.decide(entry, data_quality="DEGRADED")
        self.assertEqual((degraded["action"], degraded["evaluation_status"]), ("HOLD", "waiting"))
        open_candle = trader.get_confluence_decision(
            "SPY", {"df": self.df.iloc[: entry + 1], "candle_meta": {"candle_status": "waiting"}},
            use_bot_settings=True, timeframe="1Day")
        self.assertEqual((open_candle["action"], open_candle["evaluation_status"]), ("HOLD", "waiting"))
        missing = trader.get_confluence_decision("SPY", {"df": None, "candle_meta": {}}, use_bot_settings=True, timeframe="1Day")
        self.assertEqual((missing["action"], missing["evaluation_status"]), ("HOLD", "waiting"))

    def test_stale_catalog_version_pauses_entries_but_not_exits_in_bots(self):
        self.use_settings({**PRESET_SETTINGS, "strategy_preset_catalog_version": sp.CATALOG_VERSION + 1})
        frame = self.strategy_columns()
        entry = int(np.flatnonzero(frame["SP_entry"].to_numpy())[0])
        exit_pos = int(np.flatnonzero(frame["SP_exit_event"].to_numpy())[0])
        token = active_evaluation_context.set("bots")
        self.addCleanup(active_evaluation_context.reset, token)
        paused = self.decide(entry)
        self.assertEqual(paused["action"], "HOLD")
        self.assertEqual(paused["strategy_preset"]["entries_paused"], "catalog_version")
        self.assertEqual(self.decide(exit_pos, has_position=True)["action"], "SELL")

    def test_options_bots_do_not_use_the_preset_path(self):
        self.use_settings({**PRESET_SETTINGS, "options_trading": True, "indicators": ["RSI"]})
        analysis = {"df": self.df, "candle_meta": {"candle_status": "closed"}, "signals": {}}
        decision = trader.get_confluence_decision("SPY", analysis, use_bot_settings=True, timeframe="1Day")
        self.assertNotIn("strategy_preset", decision)


class AnalysisSettingsTests(UserConfigCase):
    def test_a_preset_requests_no_indicators_and_its_own_warmup(self):
        config = self.use_settings({**PRESET_SETTINGS, "indicator_overrides": {"RSI_PERIOD": 3}})
        settings = trader._analysis_settings_for_trade(config, config.TICKER_SETTINGS["SPY"], True)
        self.assertEqual(settings["enabled_indicators"], ["VWAP", "Auto-AVWAP"])  # reference values for the post-stop reclaim guard only
        self.assertNotIn("RSI_PERIOD", settings)
        self.assertEqual((settings["buy_mode"], settings["sell_mode"]), ("indicator", "indicator"))
        needed = _required_indicator_history_bars(settings)
        self.assertGreaterEqual(needed, sp.warmup_bars("golden_cross", "sma_20_50") + 50)

    def test_manual_settings_path_is_unchanged_when_no_preset_is_active(self):
        config = self.use_settings({"indicators": ["RSI"], "buy_mode": "indicator", "sell_mode": "indicator"})
        settings = trader._analysis_settings_for_trade(config, config.TICKER_SETTINGS["SPY"], True)
        self.assertEqual(settings["enabled_indicators"], ["RSI"])
        self.assertNotIn("strategy_preset_enabled", settings)


class RiskSizingTests(UserConfigCase):
    def size(self, settings, equity=10_000.0, price=100.0, atr=2.0, cash=None, side="long"):
        self.use_settings({**PRESET_SETTINGS, **settings})
        manager = RiskManager()
        return manager.calculate_position_size(account_equity=equity, entry_price=price, atr=atr,
                                               available_cash=cash, ticker="SPY", side=side)

    def test_smallest_limit_decides_like_the_backtest(self):
        out = self.size({"allocation_mode": "fixed", "amount": 500, "risk_per_trade": 0.10, "max_position_pct": 1.0})
        self.assertAlmostEqual(out["notional"], 500.0)
        self.assertEqual(out["sizing_limit"], "amount")
        out = self.size({"allocation_mode": "fixed", "amount": 50_000, "risk_per_trade": 0.01, "max_position_pct": 1.0})
        self.assertEqual(out["sizing_limit"], "risk")
        self.assertAlmostEqual(out["shares"], 10_000 * 0.01 / 6.0, places=4)  # stop = 3 x ATR

    def test_never_exceeds_max_position_or_cash_and_reports_the_protective_stop(self):
        out = self.size({"allocation_mode": "fixed", "amount": 50_000, "risk_per_trade": 1.0, "max_position_pct": 0.25})
        self.assertLessEqual(out["notional"], 2_500.0 + 0.01)
        out = self.size({"allocation_mode": "fixed", "amount": 50_000, "risk_per_trade": 1.0, "max_position_pct": 1.0}, cash=1_000.0)
        self.assertLessEqual(out["notional"], 980.0 + 0.01)
        self.assertEqual(out["sizing_limit"], "buying_power")
        self.assertAlmostEqual(out["stop_distance"], 6.0)
        self.assertAlmostEqual(out["protective_stop"], 94.0)
        self.assertIsNone(out["stop_loss"])  # no broker-side stop is attached to the order

    def test_short_side_is_rejected_because_presets_are_long_only(self):
        out = self.size({"amount": 500, "max_position_pct": 1.0}, side="short")
        self.assertEqual(out["shares"], 0)
        self.assertIn("long-only", out["error"])

    def test_risk_management_disabled_still_respects_the_max_position(self):
        out = self.size({"enable_risk_management": False, "allocation_mode": "fixed", "amount": 50_000, "max_position_pct": 0.5})
        self.assertLessEqual(out["notional"], 5_000.0 + 0.01)


if __name__ == "__main__":
    unittest.main()
