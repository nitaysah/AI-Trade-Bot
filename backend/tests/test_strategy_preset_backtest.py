import threading
import unittest
from datetime import timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

import backtester
import strategy_presets as sp
from backtester import Backtester


def one_cross_closes():
    return np.concatenate([np.linspace(100, 60, 120), np.linspace(60, 140, 120), np.linspace(140, 70, 120)])


def make_bars(closes, start="2020-01-02", open_gap=0.01, source="alpaca", feed="sip", quality="OK"):
    closes = np.asarray(closes, dtype=float)
    index = pd.bdate_range(start=start, periods=len(closes))
    # Each bar opens `open_gap` above the previous close, so a next-open fill is distinguishable from the signal close.
    opens = np.concatenate([[closes[0]], closes[:-1] * (1.0 + open_gap)])
    frame = pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) + 0.5, "low": np.minimum(opens, closes) - 0.5,
         "close": closes, "volume": 1_000_000.0},
        index=index,
    )
    frame.attrs.update({"data_source": source, "feed": feed, "data_quality": quality, "adjustment": "split"})
    return frame


def run_preset(frame, preset="golden_cross", variant="sma_20_50", timeframe="1Day", **kwargs):
    start = frame.index[0].to_pydatetime()
    end = frame.index[-1].to_pydatetime() + timedelta(days=1)
    params = dict(initial_capital=10_000, trade_amount=10_000, allocation_mode="fixed", risk_per_trade=0.5,
                  max_pos_pct=1.0, enable_cooldown=False, strategy_preset=preset, strategy_preset_variant=variant)
    params.update(kwargs)
    with patch.object(backtester, "get_historical_data", return_value=frame):
        bt = Backtester("TEST", timeframe, start, end, **params)
        return bt, bt.run()


class PresetBacktestTests(unittest.TestCase):
    def setUp(self):
        self.frame = make_bars(one_cross_closes())
        self.bt, self.res = run_preset(self.frame)
        self.summary = self.res["summary"]
        self.sp_frame = self.bt.df  # strategy columns, sliced to the window

    def positions(self, column):
        return list(np.flatnonzero(self.sp_frame[column].to_numpy(dtype=bool)))

    def test_entry_is_filled_at_the_next_bar_open_not_the_signal_close(self):
        entry_pos = self.positions("SP_entry")[0]
        trade = self.res["trades"][-1]  # trades are returned newest first
        trade = sorted(self.res["trades"], key=lambda t: t["entry_time"])[0]
        signal_ts, fill_ts = self.sp_frame.index[entry_pos], self.sp_frame.index[entry_pos + 1]
        self.assertAlmostEqual(trade["entry_price"], round(float(self.sp_frame["Open"].iloc[entry_pos + 1]), 2), places=2)
        self.assertEqual(trade["entry_time"][:10], fill_ts.strftime("%Y-%m-%d"))
        self.assertEqual(trade["entry_signal_time"][:10], signal_ts.strftime("%Y-%m-%d"))
        self.assertNotAlmostEqual(trade["entry_price"], float(self.sp_frame["Close"].iloc[entry_pos]), places=2)

    def test_exit_is_a_state_based_strategy_exit_filled_at_the_next_open(self):
        exit_pos = self.positions("SP_exit_event")[0]
        trade = sorted(self.res["trades"], key=lambda t: t["entry_time"])[0]
        self.assertTrue(trade["reason"].startswith("Strategy exit"))
        self.assertEqual(trade["exit_time"][:10], self.sp_frame.index[exit_pos + 1].strftime("%Y-%m-%d"))
        self.assertAlmostEqual(trade["exit_price"], round(float(self.sp_frame["Open"].iloc[exit_pos + 1]), 2), places=2)
        self.assertEqual(trade["exit_signal_time"][:10], self.sp_frame.index[exit_pos].strftime("%Y-%m-%d"))

    def test_backtest_signals_match_the_shared_evaluator_on_every_prefix(self):
        """Parity: the bar where evaluate_strategy_preset says BUY/SELL is the bar the backtest acted on."""
        entry_signal_bar = self.positions("SP_entry")[0]
        for pos in range(len(self.sp_frame)):
            prefix = self.sp_frame.iloc[: pos + 1]
            buy = sp.evaluate_strategy_preset(prefix, "golden_cross", "sma_20_50", timeframe="1Day", has_position=False)["action"] == "BUY"
            self.assertEqual(buy, pos == entry_signal_bar, pos)
        exit_bar = self.positions("SP_exit_event")[0]
        sell = sp.evaluate_strategy_preset(self.sp_frame.iloc[: exit_bar + 1], "golden_cross", "sma_20_50",
                                           timeframe="1Day", has_position=True)["action"]
        self.assertEqual(sell, "SELL")

    def test_summary_reports_the_strategy_data_provenance_and_variants(self):
        info = self.summary["strategy_preset"]
        self.assertEqual((info["id"], info["variant_id"], info["fill_model"]), ("golden_cross", "sma_20_50", "next_bar_open"))
        self.assertEqual(info["direction"], "long_only")
        self.assertTrue(info["low_sample_warning"])  # 1 trade is far below 30
        self.assertEqual([v["id"] for v in info["variants"]], ["sma_50_200", "ema_50_200", "sma_20_50"])
        self.assertEqual([v["selected"] for v in info["variants"]], [False, False, True])
        selected = [v for v in info["variants"] if v["selected"]][0]
        self.assertEqual(selected["total_trades"], self.summary["total_trades"])
        self.assertEqual((self.summary["data_source"], self.summary["data_feed"], self.summary["data_quality"]),
                         ("alpaca", "sip", "OK"))
        self.assertEqual(self.summary["price_adjustment"], "split")
        self.assertGreater(info["exposure_pct"], 0)

    def test_equity_curve_starts_at_initial_capital_and_matches_final_equity(self):
        curve = self.res["equity_curve"]
        self.assertAlmostEqual(curve[0]["equity"], 10_000, places=2)
        self.assertAlmostEqual(curve[-1]["equity"], self.summary["final_equity"], delta=1.0)

    def test_manual_entry_exit_settings_and_indicators_are_ignored(self):
        bt, res = run_preset(self.frame, buy_mode="swing_points", sell_mode="hybrid", threshold=5, sell_threshold=3,
                             enabled_indicators=["RSI", "MACD"], enable_signal_reset=True, enable_smart_reentry=True)
        self.assertEqual((bt.buy_mode, bt.sell_mode), (sp.PRESET_MODE, sp.PRESET_MODE))
        self.assertEqual(bt.enabled_indicators, [])
        self.assertFalse(bt.enable_signal_reset or bt.enable_smart_reentry)
        self.assertEqual(res["summary"]["total_trades"], self.summary["total_trades"])
        self.assertEqual(res["summary"]["roi_pct"], self.summary["roi_pct"])


class ProtectiveStopTests(unittest.TestCase):
    def build(self, edit):
        frame = make_bars(one_cross_closes())
        bt, res = run_preset(frame)
        entry_pos = int(np.flatnonzero(bt.df["SP_entry"].to_numpy(dtype=bool))[0])
        atr = float(bt.df["SP_atr"].iloc[entry_pos])
        fill_open = float(frame["open"].iloc[entry_pos + 1])
        stop = fill_open - 3.0 * atr
        edit(frame, entry_pos, stop, fill_open)
        return run_preset(frame), entry_pos, stop, fill_open

    def test_intrabar_stop_fills_at_the_stop_price(self):
        def edit(frame, pos, stop, fill_open):
            target = pos + 3
            frame.iloc[target, frame.columns.get_loc("low")] = stop - 2.0  # trades through the stop; open stays above it
        (bt, res), pos, stop, fill_open = self.build(edit)
        trade = sorted(res["trades"], key=lambda t: t["entry_time"])[0]
        self.assertIn("Stop Loss Hit", trade["reason"])
        self.assertAlmostEqual(trade["exit_price"], round(stop, 2), places=2)

    def test_gap_below_the_stop_fills_at_the_open_not_the_stop(self):
        def edit(frame, pos, stop, fill_open):
            target = pos + 3
            gap_open = stop - 5.0
            frame.iloc[target, frame.columns.get_loc("open")] = gap_open
            frame.iloc[target, frame.columns.get_loc("low")] = gap_open - 1.0
            frame.iloc[target, frame.columns.get_loc("close")] = gap_open
            frame.iloc[target, frame.columns.get_loc("high")] = gap_open + 0.5
        (bt, res), pos, stop, fill_open = self.build(edit)
        trade = sorted(res["trades"], key=lambda t: t["entry_time"])[0]
        self.assertIn("Stop Loss Hit", trade["reason"])
        self.assertLess(trade["exit_price"], stop)
        self.assertAlmostEqual(trade["exit_price"], round(stop - 5.0, 2), places=2)


class SizingAndGuardTests(unittest.TestCase):
    def test_size_is_the_smallest_limit_and_never_exceeds_max_position(self):
        frame = make_bars(one_cross_closes())
        _, res = run_preset(frame, trade_amount=2_000, allocation_mode="fixed")
        trade = sorted(res["trades"], key=lambda t: t["entry_time"])[0]
        self.assertAlmostEqual(trade["entry_cost"], 2_000, delta=1.0)
        _, res = run_preset(frame, trade_amount=50_000, max_pos_pct=0.25, risk_per_trade=1.0)
        trade = sorted(res["trades"], key=lambda t: t["entry_time"])[0]
        self.assertLessEqual(trade["entry_cost"], 10_000 * 0.25 + 1.0)

    def test_compound_sizing_grows_with_realised_profit(self):
        closes = np.concatenate([np.linspace(100, 60, 120), np.linspace(60, 140, 120), np.linspace(140, 70, 80),
                                 np.linspace(70, 150, 120), np.linspace(150, 80, 100)])
        _, res = run_preset(make_bars(closes), allocation_mode="compound", trade_amount=10_000)
        trades = sorted(res["trades"], key=lambda t: t["entry_time"])
        self.assertGreaterEqual(len(trades), 2)
        self.assertGreater(trades[0]["pl"], 0)
        self.assertGreater(trades[1]["entry_cost"], trades[0]["entry_cost"] + 1)

    def test_entry_blocked_by_a_guard_is_reported_and_not_retried(self):
        frame = make_bars(one_cross_closes())
        with patch.object(Backtester, "_entry_guard_allows", return_value=(False, "Test guard")):
            _, res = run_preset(frame)
        self.assertEqual(res["summary"]["total_trades"], 0)
        self.assertEqual(res["summary"]["strategy_preset"]["blocked_entries"][0]["reason"], "Test guard")
        self.assertIn("no completed trade", res["summary"]["no_trade_reason"])


class NoLookaheadGuardTests(unittest.TestCase):
    """A next-open fill happens before its own bar's close/volume exist, so entry guards must judge the signal bar."""

    def entry_pos(self, frame):
        bt, _ = run_preset(frame)
        return int(np.flatnonzero(bt.df["SP_entry"].to_numpy(dtype=bool))[0])

    def test_the_entry_guard_receives_the_completed_signal_bar_not_the_fill_bar(self):
        frame = make_bars(one_cross_closes())
        entry_pos = self.entry_pos(frame)
        seen = []

        def spy(self_, action, bar, price, timestamp, side="long"):
            seen.append((bar.name, self_.current_bar_index, timestamp))
            return True, None

        with patch.object(Backtester, "_entry_guard_allows", spy):
            bt, res = run_preset(frame)
        signal_ts, fill_ts = bt.df.index[entry_pos], bt.df.index[entry_pos + 1]
        self.assertEqual(seen[0][0], signal_ts)
        self.assertEqual(seen[0][1], entry_pos)
        self.assertEqual(seen[0][2], fill_ts)  # the order itself is still placed at the next open

    def test_corrupting_the_fill_bars_own_close_and_volume_cannot_change_the_trades(self):
        frame = make_bars(one_cross_closes())
        entry_pos = self.entry_pos(frame)
        _, baseline = run_preset(frame)
        edited = frame.copy()
        fill_label = edited.index[entry_pos + 1]
        edited.loc[fill_label, "close"] = float(edited.loc[fill_label, "open"]) * 0.5   # bar "closes" far below its open
        edited.loc[fill_label, "low"] = float(edited.loc[fill_label, "close"]) - 1.0     # keep OHLC consistent (stop is separate)
        edited.loc[fill_label, "volume"] = 0.0                                           # and trades no volume at all
        _, changed = run_preset(edited)
        entries = lambda r: sorted((t["entry_time"], t["entry_price"], t["qty"]) for t in r["trades"])
        self.assertGreaterEqual(len(baseline["trades"]), 1)
        self.assertEqual(entries(changed)[0], entries(baseline)[0])  # the entry is decided before that bar exists


class PendingAndErrorTests(unittest.TestCase):
    def test_a_signal_on_the_last_bar_is_reported_pending_not_filled(self):
        full = make_bars(one_cross_closes())
        bt, _ = run_preset(full)
        entry_pos = int(np.flatnonzero(bt.df["SP_entry"].to_numpy(dtype=bool))[0])
        truncated = full.iloc[: entry_pos + 1]
        _, res = run_preset(truncated)
        self.assertEqual(res["summary"]["total_trades"], 0)
        pending = res["summary"]["strategy_preset"]["pending_signal"]
        self.assertEqual(pending["action"], "BUY")

    def test_degraded_data_is_refused_instead_of_trading(self):
        _, res = run_preset(make_bars(one_cross_closes(), quality="DEGRADED"))
        self.assertIn("split-adjustment", res["error"])

    def test_unsupported_timeframe_crypto_and_unknown_preset(self):
        frame = make_bars(one_cross_closes())
        _, res = run_preset(frame, timeframe="2Min")
        self.assertIn("runs on", res["error"])
        start, end = frame.index[0].to_pydatetime(), frame.index[-1].to_pydatetime()
        with patch.object(backtester, "get_historical_data", return_value=frame):
            crypto = Backtester("BTCUSD", "1Day", start, end, strategy_preset="golden_cross").run()
        self.assertIn("stocks and ETFs", crypto["error"])
        self.assertIsNone(Backtester("TEST", "1Day", start, end, strategy_preset="nope").strategy_preset)

    def test_cancellation_stops_the_run(self):
        event = threading.Event()
        event.set()
        _, res = run_preset(make_bars(one_cross_closes()), cancel_event=event)
        self.assertIn("cancelled", res["error"])

    def test_short_history_reports_a_warmup_warning_not_a_signal(self):
        _, res = run_preset(make_bars(np.linspace(100, 120, 40)))
        info = res["summary"]["strategy_preset"]
        self.assertIsNone(info["first_ready_date"])
        self.assertIn("Not enough history", info["warning"])
        self.assertEqual(res["summary"]["total_trades"], 0)


def hourly_bars(days=360):
    """Hourly bars from 08:00 to 19:00 ET each business day, so regular-session filtering is observable."""
    closes_daily = np.concatenate([np.linspace(100, 60, days // 3), np.linspace(60, 140, days // 3), np.linspace(140, 70, days - 2 * (days // 3))])
    stamps, closes = [], []
    for day, close in zip(pd.bdate_range("2020-01-02", periods=days), closes_daily):
        for hour in range(8, 20):
            stamps.append(day + pd.Timedelta(hours=hour))
            closes.append(close)
    closes = np.asarray(closes)
    opens = np.concatenate([[closes[0]], closes[:-1] * 1.0005])
    frame = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) + 0.2, "low": np.minimum(opens, closes) - 0.2,
                          "close": closes, "volume": 1e5}, index=pd.DatetimeIndex(stamps))
    frame.attrs.update({"data_source": "alpaca", "feed": "sip", "data_quality": "OK", "adjustment": "split"})
    return frame


class GuardReferenceDataTests(unittest.TestCase):
    def test_the_prepared_frame_carries_real_vwap_not_a_copy_of_close(self):
        frame = hourly_bars(120)
        wiggle = np.tile(np.linspace(0, 1.2, 12), len(frame) // 12)
        frame["close"] = frame["close"] + wiggle
        frame["open"] = frame["open"] + wiggle
        frame["high"] = frame[["open", "close"]].max(axis=1) + 0.2
        frame["low"] = frame[["open", "close"]].min(axis=1) - 0.2
        bt, _ = run_preset(frame, timeframe="1Hour", market_session="Regular")
        prepared = bt._preset_prepared["df"]
        for column in ("VWAP", "Auto_AVWAP"):
            self.assertFalse(np.allclose(prepared[column].to_numpy(dtype=float), prepared["Close"].to_numpy(dtype=float)),
                             f"{column} is only a copy of Close, so 'close above VWAP' can never be true")


class IntradayTests(unittest.TestCase):
    def test_an_intraday_timeframe_runs_on_regular_session_bars_only_and_is_flagged_untested(self):
        bt, res = run_preset(hourly_bars(), timeframe="1Hour", market_session="Regular")
        self.assertNotIn("error", res, res.get("error"))
        hours = set(bt.df.index.hour)
        self.assertTrue(hours <= set(range(10, 16)), hours)  # 09:00 and after-hours bars are dropped
        info = res["summary"]["strategy_preset"]
        self.assertIn("1Hour", info["untested_note"])
        self.assertIn("completed 1Hour bar", info["execution_note"])
        self.assertGreaterEqual(res["summary"]["total_trades"], 1)

    def test_the_extended_session_keeps_extended_bars(self):
        bt, res = run_preset(hourly_bars(), timeframe="1Hour", market_session="Extended")
        self.assertNotIn("error", res, res.get("error"))
        self.assertTrue({8, 9, 16, 17, 18, 19} <= set(bt.df.index.hour))
        self.assertIn("Extended", res["summary"]["strategy_preset"]["untested_note"])

    def test_the_designed_settings_carry_no_warning(self):
        _, res = run_preset(make_bars(one_cross_closes()), market_session="Regular")
        self.assertEqual(res["summary"]["strategy_preset"]["untested_note"], "")


class ProvenanceTests(unittest.TestCase):
    def test_capture_provenance_reads_the_frame_attrs(self):
        start = pd.Timestamp("2026-01-05").to_pydatetime()
        bt = Backtester("TEST", "1Day", start, start + timedelta(days=30))
        frame = make_bars(np.linspace(1, 2, 5), source="Yahoo", feed="yahoo", quality="degraded")
        bt._capture_provenance(frame)
        self.assertEqual(bt._provenance_summary(),
                         {"data_feed": "yahoo", "data_quality": "DEGRADED", "price_adjustment": "split"})
        self.assertEqual(bt.data_source, "yahoo")


if __name__ == "__main__":
    unittest.main()
