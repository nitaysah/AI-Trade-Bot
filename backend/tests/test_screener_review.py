import asyncio
import copy
import time
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
from fastapi import Response

import indicators
import main
from screener_service import ScreenerUserState
from user_config import active_evaluation_context


def bars(count=100, end=None):
    index = pd.date_range(end=end or pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=10), periods=count, freq="5min")
    close = 100 + np.arange(count) * 0.1 + np.sin(np.arange(count))
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 100.0}, index=index)


class CandleReviewTests(unittest.TestCase):
    def test_duplicate_and_out_of_order_bars_normalize_without_mutating_input(self):
        clean = bars(120)
        unordered = pd.concat([clean.iloc[::-1], clean.iloc[[-1]]])
        original = unordered.copy(deep=True)
        token = active_evaluation_context.set('screener')
        try:
            settings = {'enabled_indicators': ['RSI']}
            result = indicators.calculate_indicators_for_df(unordered, '5Min', 'BTCUSD', settings)
            baseline = indicators.calculate_indicators_for_df(clean, '5Min', 'BTCUSD', settings)
            self.assertEqual(result['signals'], baseline['signals'])
            self.assertEqual(result['candle_meta'], baseline['candle_meta'])
            pd.testing.assert_frame_equal(unordered, original)
        finally:
            active_evaluation_context.reset(token)

    def test_missing_crypto_bar_rejects_live_analysis(self):
        frame = bars(120)
        frame = frame.drop(frame.index[-10])
        token = active_evaluation_context.set('screener')
        try:
            self.assertIsNone(indicators.calculate_indicators_for_df(frame, '5Min', 'BTCUSD', {'enabled_indicators': ['RSI']}))
        finally:
            active_evaluation_context.reset(token)

    def test_full_calculation_path_preserves_bollinger_warmup(self):
        token = active_evaluation_context.set('backtest')
        try:
            result = indicators.calculate_indicators_for_df(bars(150), '5Min', 'BTCUSD', {'enabled_indicators': ['Bollinger', 'EMA Cross']})
            self.assertIsNotNone(result)
            self.assertTrue(pd.isna(result['df']['BOLL_Middle'].iloc[0]))
        finally:
            active_evaluation_context.reset(token)

    def test_regular_session_boundaries_follow_eastern_dst(self):
        for day, utc_open in [('2026-03-06', '14:30'), ('2026-03-09', '13:30')]:
            with self.subTest(day=day):
                open_time = pd.Timestamp(f'{day} {utc_open}', tz='UTC')
                frame = bars(3, open_time + pd.Timedelta(minutes=5))
                filtered = indicators._filter_market_session_df(frame, 'AAPL', '5Min', {'market_session': 'Regular'})
                self.assertEqual(list(filtered.index), list(frame.index[-2:]))

    def test_exchange_scheduler_skips_holiday_and_early_close_afternoon(self):
        for timestamp, expected in [('2026-11-26 10:00', False), ('2026-11-27 12:00', True), ('2026-11-27 14:00', False)]:
            with self.subTest(timestamp=timestamp):
                status = main._screener_market_status('AAPL', {'market_session': 'Regular'}, pd.Timestamp(timestamp, tz='America/New_York').to_pydatetime())
                self.assertEqual(status['is_active_window'], expected)

    def test_a_single_forming_candle_is_not_completed(self):
        frame = bars(1, pd.Timestamp.now(tz="UTC"))
        self.assertTrue(indicators._completed_candles_for_live_chart(frame, "5Min", ticker="BTCUSD").empty)

    def test_all_future_bars_do_not_fall_back_to_unclosed_history(self):
        frame = bars(3, pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=1))
        self.assertTrue(indicators._completed_candles_for_live_chart(frame, "5Min", ticker="BTCUSD").empty)

    def test_regular_session_with_only_premarket_data_is_empty(self):
        frame = bars(40, pd.Timestamp("2026-09-21 09:00", tz="America/New_York"))
        filtered = indicators._filter_market_session_df(frame, "AAPL", "5Min", {"market_session": "Regular"})
        self.assertTrue(filtered.empty)

    def test_invalid_ohlcv_is_rejected(self):
        for column, value in [("Close", float("inf")), ("Volume", -1), ("Low", -1), ("High", 1)]:
            with self.subTest(column=column, value=value):
                frame = bars()
                frame.iloc[-1, frame.columns.get_loc(column)] = value
                self.assertTrue(indicators._validate_live_candle_data(frame, "BTCUSD", "5Min"))

    def test_forming_candle_cannot_satisfy_warmup(self):
        frame = bars(65, pd.Timestamp.now(tz="UTC"))
        token = active_evaluation_context.set("screener")
        try:
            result = indicators.calculate_indicators_for_df(frame, "5Min", "BTCUSD", {"enabled_indicators": ["RSI"]})
            self.assertIsNone(result)
        finally:
            active_evaluation_context.reset(token)

    def test_fast_path_does_not_backfill_future_indicator_values(self):
        token = active_evaluation_context.set("backtest")
        try:
            result = indicators.calculate_indicators_for_df(bars(), "5Min", "BTCUSD", {"enabled_indicators": ["RSI", "Bollinger"]})
            self.assertIsNotNone(result)
            self.assertTrue(pd.isna(result["df"]["RSI"].iloc[0]))
            self.assertTrue(pd.isna(result["df"]["BOLL_Middle"].iloc[0]))
        finally:
            active_evaluation_context.reset(token)

    def test_completed_signal_is_invariant_to_forming_candle_prices(self):
        frame = bars(120)
        forming = bars(1, pd.Timestamp.now(tz="UTC"))
        settings = {"enabled_indicators": ["RSI", "Bollinger", "MACD"]}
        results = []
        for context in ("dashboard", "bots", "screener"):
            token = active_evaluation_context.set(context)
            try:
                baseline = indicators.calculate_indicators_for_df(frame.copy(), "5Min", "BTCUSD", settings)
                changed = indicators.calculate_indicators_for_df(pd.concat([frame, forming * 100]), "5Min", "BTCUSD", settings)
                self.assertEqual(baseline["signals"], changed["signals"])
                self.assertEqual(baseline["candle_meta"]["candle_key"], changed["candle_meta"]["candle_key"])
                results.append(changed["signals"])
            finally:
                active_evaluation_context.reset(token)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])


class ScreenerFailureReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = ScreenerUserState("review-user", None)
        self.state.profiles = {"BTCUSD": {"enabled": True, "timeframe": "5Min", "indicators": []}}
        self.state.order = ["BTCUSD"]
        self.state.save = AsyncMock()
        self.state.refresh_from_persistence = AsyncMock(return_value=False)
        self.state.try_acquire_process_scan_lock = AsyncMock(return_value=True)
        self.state.release_process_scan_lock = AsyncMock()
        self.key = f"5MIN:{int(time.time()) - 600}"
        self.meta = {"candle_key": self.key, "candle_close_ts": time.time() - 300, "candle_status": "closed"}
        self.state.evaluation_state["BTCUSD"] = {"last_candle_key": self.key, "baseline_required": False}
        self.state.latest_scans["BTCUSD"] = {"ticker": "BTCUSD", "evaluation_status": "valid", "data_status": "current", "direction": "BULLISH", "price_raw": 100.0, "candle_meta": dict(self.meta), "scan_log": {"status": "WAITING", "summary": "retained"}}

    async def test_probe_failure_marks_retained_scan_degraded_and_excludes_setup(self):
        with patch.object(main, "get_latest_completed_candle_metadata", return_value={"candle_key": None}):
            await main._scan_screener_state(self.state)
        scan = self.state.latest_scans["BTCUSD"]
        self.assertEqual(scan["data_status"], "degraded")
        self.assertEqual(scan["direction"], "BULLISH")
        self.assertEqual(main._screener_summary(self.state, self.state.order)["setups"]["bullish"], 0)
        self.assertEqual(self.state.notifications, [])

    async def test_failed_forced_scan_sets_retry_deadline_and_retains_analysis(self):
        for error in (TimeoutError(), RuntimeError("provider unavailable")):
            with self.subTest(error=type(error).__name__), patch.object(main, "evaluate_screener_profile", side_effect=error):
                await main._scan_screener_state(self.state, force_scan=True)
                self.assertEqual(self.state.latest_scans["BTCUSD"]["data_status"], "degraded")
                self.assertGreater(self.state.evaluation_state["BTCUSD"]["next_due_at"], time.time())
                self.assertEqual(self.state.latest_scans["BTCUSD"]["price_raw"], 100.0)

    async def test_snapshot_rechecks_freshness_without_changing_durable_state(self):
        self.state.latest_scans["BTCUSD"]["candle_meta"]["candle_close_ts"] = time.time() - 86400
        before = copy.deepcopy(self.state.latest_scans)
        response = await main.get_screener_snapshot(Response(), state=self.state)
        self.assertEqual(response["bot_scans"]["BTCUSD"]["data_status"], "degraded")
        self.assertEqual(response["screener_summary"]["setups"]["valid"], 0)
        self.assertEqual(self.state.latest_scans, before)
        self.state.save.assert_not_awaited()

    async def test_same_candle_recovery_rechecks_failed_analysis(self):
        self.state.latest_scans["BTCUSD"]["data_status"] = "degraded"
        with patch.object(main, "get_latest_completed_candle_metadata", return_value=self.meta), patch.object(main, "evaluate_screener_profile", return_value={"evaluation_status": "waiting"}) as evaluate:
            await main._scan_screener_state(self.state)
        evaluate.assert_called_once()

    def test_future_and_nonfinite_candle_timestamps_are_rejected(self):
        for timestamp in (float("nan"), float("inf"), time.time() + 60):
            with self.subTest(timestamp=timestamp):
                self.assertFalse(main._screener_candle_is_usable({"candle_close_ts": timestamp}, "30Sec", True))

    def test_thirty_second_timeframe_is_not_treated_as_fifteen_minutes(self):
        self.assertEqual(main._screener_timeframe_seconds("30Sec"), 30)


if __name__ == "__main__":
    unittest.main()
