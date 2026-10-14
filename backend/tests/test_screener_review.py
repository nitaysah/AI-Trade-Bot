import asyncio
import copy
from types import SimpleNamespace
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
        # The snapshot reads the dashboard watchlist; give it a real (empty) one instead of an auto-created Mock.
        engine = SimpleNamespace(config=SimpleNamespace(WATCHLIST=[]))
        patcher = patch.object(main, "_dashboard_engine_for_screener", AsyncMock(return_value=engine))
        patcher.start()
        self.addCleanup(patcher.stop)
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

    async def _scan_with_probe(self, side_effect):
        self.state.evaluation_state["BTCUSD"]["next_due_at"] = 0
        with patch.object(main, "get_latest_completed_candle_metadata", side_effect=side_effect) as probe, \
                patch.object(main.random, "random", return_value=0.0):
            await main._scan_screener_state(self.state)
        return probe

    async def test_one_failed_probe_is_retried_and_does_not_degrade_the_card(self):
        probe = await self._scan_with_probe(lambda *a, **k: {"candle_key": None})
        self.assertEqual(probe.call_count, 2)  # retried once before counting as a failure
        scan = self.state.latest_scans["BTCUSD"]
        self.assertNotEqual(scan["data_status"], "degraded")
        self.assertEqual(scan["direction"], "BULLISH")
        evaluation = self.state.evaluation_state["BTCUSD"]
        self.assertEqual(evaluation["probe_failures"], 1)
        # a quick retry (seconds), not a whole scan interval
        self.assertLess(evaluation["next_due_at"] - time.time(), 20)
        self.assertEqual(self.state.notifications, [])

    async def test_a_transient_probe_failure_that_recovers_on_retry_is_invisible(self):
        results = iter([TimeoutError(), dict(self.meta)])

        def flaky(*args, **kwargs):
            outcome = next(results)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        probe = await self._scan_with_probe(flaky)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(self.state.evaluation_state["BTCUSD"].get("probe_failures", 0), 0)
        self.assertNotEqual(self.state.latest_scans["BTCUSD"]["data_status"], "degraded")

    async def test_repeated_probe_failures_mark_the_scan_degraded_and_exclude_the_setup(self):
        for _ in range(main._PROBE_DEGRADE_AFTER):
            await self._scan_with_probe(lambda *a, **k: {"candle_key": None})
        scan = self.state.latest_scans["BTCUSD"]
        self.assertEqual(scan["data_status"], "degraded")
        self.assertEqual(scan["direction"], "BULLISH")
        self.assertEqual(main._screener_summary(self.state, self.state.order)["setups"]["bullish"], 0)
        self.assertEqual(self.state.notifications, [])

    async def test_a_successful_probe_resets_the_failure_count(self):
        await self._scan_with_probe(lambda *a, **k: {"candle_key": None})
        self.assertEqual(self.state.evaluation_state["BTCUSD"]["probe_failures"], 1)
        await self._scan_with_probe(lambda *a, **k: dict(self.meta))
        self.assertEqual(self.state.evaluation_state["BTCUSD"]["probe_failures"], 0)

    def test_probe_retry_delay_grows_and_is_capped(self):
        with patch.object(main.random, "random", return_value=0.5):
            delays = [main._probe_retry_delay(n) for n in (1, 2, 3, 4, 5, 9)]
        self.assertEqual(delays, sorted(delays))
        self.assertLessEqual(max(delays), 120.0 * 1.15 + 0.01)
        self.assertGreaterEqual(min(delays), 8.0)

    async def test_probes_do_not_share_worker_slots_with_full_evaluations(self):
        self.assertIsNot(main.screener_manager.probe_semaphore, main.screener_manager.evaluation_semaphore)

    async def test_failed_forced_scan_sets_retry_deadline_and_retains_analysis(self):
        for error in (TimeoutError(), RuntimeError("provider unavailable")):
            with self.subTest(error=type(error).__name__), patch.object(main, "evaluate_screener_profile", side_effect=error):
                await main._scan_screener_state(self.state, force_scan=True)
                self.assertEqual(self.state.latest_scans["BTCUSD"]["data_status"], "degraded")
                self.assertGreater(self.state.evaluation_state["BTCUSD"]["next_due_at"], time.time())
                self.assertEqual(self.state.latest_scans["BTCUSD"]["price_raw"], 100.0)

    async def _failed_scan(self, error, times=1, **kw):
        for _ in range(times):
            self.state.evaluation_state["BTCUSD"]["next_due_at"] = 0
            with patch.object(main, "evaluate_screener_profile", side_effect=error), \
                    patch.object(main, "get_latest_completed_candle_metadata", return_value=dict(self.meta)), \
                    patch.object(main.random, "random", return_value=0.5):
                await main._scan_screener_state(self.state, force_scan=True)
        return self.state.evaluation_state["BTCUSD"]

    async def test_a_timed_out_analysis_retries_soon_instead_of_after_the_whole_interval(self):
        # Regression: a timeout left "Analysis timed out" on the card until the NEXT scheduled scan (up to hours on 1H).
        evaluation = await self._failed_scan(TimeoutError())
        wait = evaluation["next_due_at"] - time.time()
        self.assertTrue(20 < wait < 45, wait)                      # ~30s, not a scan interval
        self.assertEqual(evaluation["evaluation_failures"], 1)
        self.assertEqual(self.state.latest_scans["BTCUSD"]["data_status"], "degraded")
        self.assertEqual(self.state.latest_scans["BTCUSD"]["price_raw"], 100.0)   # last valid scan retained
        self.assertEqual(self.state.latest_scans["BTCUSD"]["scan_log"]["next_due_at"], evaluation["next_due_at"])
        self.assertEqual(self.state.notifications, [])

    async def test_provider_errors_back_off_and_stop_hammering_after_the_limit(self):
        waits = []
        for _ in range(main._EVAL_RETRY_LIMIT + 2):
            evaluation = await self._failed_scan(RuntimeError("provider unavailable"))
            waits.append(evaluation["next_due_at"] - time.time())
        quick = waits[:main._EVAL_RETRY_LIMIT]
        self.assertEqual(quick, sorted(quick))                    # growing
        self.assertLessEqual(max(quick), 300.0 * 1.15 + 1)
        scan_interval = main._screener_scan_interval_seconds(self.state.profiles["BTCUSD"])
        self.assertTrue(all(abs(w - scan_interval) < 2 for w in waits[main._EVAL_RETRY_LIMIT:]), (waits, scan_interval))

    async def test_a_still_running_attempt_is_looked_at_again_shortly_without_counting_as_a_failure(self):
        from screener_tasks import ScreenerWorkBusy
        evaluation = await self._failed_scan(ScreenerWorkBusy("previous operation is still running"))
        self.assertTrue(8 < evaluation["next_due_at"] - time.time() < 20)
        self.assertEqual(evaluation.get("evaluation_failures", 0), 0)
        self.assertEqual(self.state.latest_scans["BTCUSD"]["data_status"], "degraded")

    async def test_no_valid_candle_also_retries_soon(self):
        evaluation = await self._failed_scan(None)                 # evaluator returned nothing usable
        self.assertEqual(evaluation["evaluation_failures"], 1)
        self.assertLess(evaluation["next_due_at"] - time.time(), 45)

    async def test_a_valid_analysis_ends_the_retry_streak(self):
        await self._failed_scan(TimeoutError(), times=2)
        self.assertEqual(self.state.evaluation_state["BTCUSD"]["evaluation_failures"], 2)
        good = {"evaluation_status": "valid", "direction": "NEUTRAL", "candle_meta": dict(self.meta), "price_raw": 101.0,
                "ticker": "BTCUSD", "timeframe": "5Min", "signals": {}, "indicators": []}
        with patch.object(main, "evaluate_screener_profile", return_value=good), \
                patch.object(main, "get_latest_completed_candle_metadata", return_value=dict(self.meta)):
            await main._scan_screener_state(self.state, force_scan=True)
        evaluation = self.state.evaluation_state["BTCUSD"]
        self.assertNotIn("evaluation_failures", evaluation)
        self.assertNotEqual(self.state.latest_scans["BTCUSD"].get("data_status"), "degraded")

    def test_retry_helper_bounds(self):
        state = {}
        with patch.object(main.random, "random", return_value=0.5):
            seen = [main._schedule_evaluation_retry(state, 3600) for _ in range(8)]
        self.assertEqual(seen, list(range(1, 9)))
        self.assertGreater(state["next_due_at"] - time.time(), 3000)          # beyond the limit: the normal cadence
        short = {}
        with patch.object(main.random, "random", return_value=0.5):
            main._schedule_evaluation_retry(short, 20)
        self.assertLessEqual(short["next_due_at"] - time.time(), 20.5)         # never later than the interval itself

    async def test_snapshot_rechecks_freshness_without_changing_durable_state(self):
        self.state.latest_scans["BTCUSD"]["candle_meta"]["candle_close_ts"] = time.time() - 86400
        before = copy.deepcopy(self.state.latest_scans)
        response = await main.get_screener_snapshot(Response(), state=self.state)
        self.assertEqual(response["bot_scans"]["BTCUSD"]["data_status"], "degraded")
        self.assertEqual(response["screener_summary"]["setups"]["valid"], 0)
        self.assertEqual(self.state.latest_scans, before)
        self.state.save.assert_not_awaited()

    async def test_snapshot_with_nan_in_a_cached_scan_is_json_safe(self):
        import json
        self.state.latest_scans["BTCUSD"]["vwap"] = float("nan")
        self.state.latest_scans["BTCUSD"]["nested"] = {"values": [1.0, float("inf")]}
        response = await main.get_screener_snapshot(Response(), state=self.state)
        text = json.dumps(response, allow_nan=False)  # raised "Out of range float values" before the fix
        self.assertIn('"vwap":null', text.replace(" ", ""))

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
