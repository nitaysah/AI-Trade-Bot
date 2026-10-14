"""The one-time forced scan that runs when a bot is created or its settings are saved (analysis only)."""
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main
import strategy_presets as sp


def fake_engine(scans=None, settings=None):
    eng = SimpleNamespace()
    eng.broker = MagicMock()
    eng.broker.get_account_info.return_value = {"equity": 10_000.0, "cash": 9_000.0}
    eng.broker.get_positions.return_value = []
    eng.config = SimpleNamespace(TICKER_SETTINGS={"SPY": settings if settings is not None else {"timeframe": "1Day", "buy_mode": "indicator", "sell_mode": "indicator"}},
                                 DEFAULT_TIMEFRAME="1Day", SCAN_INTERVAL_SECONDS=60)
    eng.bot_scans = dict(scans or {})
    eng._record_bot_scan = lambda result: eng.bot_scans.__setitem__(result["ticker"], result)
    eng.last_trade_timestamps = {}
    eng.preset_scan_markers = {}
    eng._get_signal_reset_lock = lambda *a, **k: None
    eng.get_now = main.get_now
    return eng            # note: no market-status helper at all: a forced scan must not need one


def real_scan(**extra):
    scan = {"time": main.get_now().isoformat(), "ticker": "SPY", "action": "HOLD", "price_raw": 100.0, "price": "$100.00", "timeframe": "1Day",
            "reason": "waiting", "signals": {}, "bullish_count": 0, "bearish_count": 0, "total_signals": 0}
    scan.update(extra)
    return scan


class ForcedScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_forced_scan_records_a_tagged_result_and_never_touches_orders(self):
        eng = fake_engine()
        with patch.object(main, "evaluate_trade", return_value=real_scan()) as evaluate:
            result = await main._run_forced_bot_scan(eng, "spy", "created")
        self.assertEqual(result["forced_scan"]["reason"], "created")
        stored = eng.bot_scans["SPY"]
        self.assertEqual((stored["forced_scan"]["reason"], stored["forced_scan"]["status"]), ("created", "ok"))
        self.assertTrue(stored["forced_scan"]["at"])
        kwargs = evaluate.call_args.kwargs
        self.assertTrue(kwargs["use_bot_settings"])
        self.assertTrue(kwargs["ignore_cache"])                           # fresh data, not a cached evaluation
        self.assertEqual(kwargs["timeframe"], "1Day")
        for order_call in ("place_order", "close_position", "cancel_orders_for_ticker", "cancel_all_entry_orders", "cancel_exit_orders"):
            getattr(eng.broker, order_call).assert_not_called()

    async def test_it_does_not_mutate_the_evaluation_it_was_given(self):
        original = real_scan()
        with patch.object(main, "evaluate_trade", return_value=original):
            await main._run_forced_bot_scan(fake_engine(), "SPY", "settings_changed")
        self.assertNotIn("forced_scan", original)

    async def test_the_default_timeouts_are_generous(self):
        self.assertEqual(main.FORCED_SCAN_EVALUATION_TIMEOUT_SECONDS, 20.0)
        self.assertGreaterEqual(main.FORCED_SCAN_ACCOUNT_TIMEOUT_SECONDS, 10.0)

    async def test_a_slow_evaluation_is_recorded_as_a_visible_failure_not_dropped(self):
        eng = fake_engine()
        with patch.object(main, "FORCED_SCAN_EVALUATION_TIMEOUT_SECONDS", 0.05), \
                patch.object(main, "evaluate_trade", side_effect=lambda *a, **k: (time.sleep(0.4), real_scan())[1]):
            result = await main._run_forced_bot_scan(eng, "SPY", "created")
        self.assertIsNone(result)
        failed = eng.bot_scans["SPY"]
        self.assertEqual(failed["forced_scan"]["status"], "failed")
        self.assertEqual(failed["forced_scan"]["reason"], "created")
        self.assertEqual(failed["reason"], "Initial scan failed, will retry")
        self.assertEqual(failed["price_raw"], 0.0)

    async def test_an_exception_or_an_empty_result_is_also_a_visible_failure(self):
        for evaluation in (MagicMock(side_effect=RuntimeError("boom")), MagicMock(return_value=None)):
            eng = fake_engine()
            with patch.object(main, "evaluate_trade", evaluation):
                self.assertIsNone(await main._run_forced_bot_scan(eng, "SPY", "settings_changed"))
            self.assertEqual(eng.bot_scans["SPY"]["forced_scan"]["status"], "failed")

    async def test_a_failure_never_overwrites_a_newer_real_scan(self):
        newer = real_scan(time=(main.get_now() + timedelta(seconds=30)).isoformat(), reason="the loop got there first")
        eng = fake_engine({"SPY": newer})
        with patch.object(main, "evaluate_trade", side_effect=RuntimeError("boom")):
            await main._run_forced_bot_scan(eng, "SPY", "created")
        self.assertEqual(eng.bot_scans["SPY"]["reason"], "the loop got there first")

    async def test_a_failure_does_replace_an_older_scan_made_before_the_settings_changed(self):
        older = real_scan(time=(main.get_now() - timedelta(minutes=5)).isoformat(), reason="old status")
        eng = fake_engine({"SPY": older})
        with patch.object(main, "evaluate_trade", side_effect=RuntimeError("boom")):
            await main._run_forced_bot_scan(eng, "SPY", "settings_changed")
        self.assertEqual(eng.bot_scans["SPY"]["forced_scan"]["status"], "failed")

    async def test_the_scan_log_shows_the_failure_and_the_forced_tag(self):
        eng = fake_engine()
        eng.get_stock_market_status = lambda session="Regular": {"is_active_window": False, "is_open": False, "market_session_label": "Regular",
                                                                  "seconds_to_open": 5, "seconds_to_pre_open": 3, "next_open_str": "tomorrow"}
        with patch.object(main, "evaluate_trade", side_effect=RuntimeError("boom")):
            await main._run_forced_bot_scan(eng, "SPY", "created")
        settings = eng.config.TICKER_SETTINGS["SPY"]
        failed = main._build_scan_log("SPY", main._format_scan_for_ui(eng.bot_scans["SPY"]), settings, {}, [], {"equity": 1}, {}, eng, [])
        self.assertEqual(failed["status"], "SCAN_FAILED")            # shown even while the market is closed
        self.assertEqual(failed["summary"], "Initial scan failed, will retry")
        self.assertEqual(failed["forced_scan"]["status"], "failed")
        self.assertEqual(failed["blockers"], [])

        eng.get_stock_market_status = lambda session="Regular": {"is_active_window": True, "is_open": True, "market_session_label": "Regular",
                                                                  "seconds_to_open": 0, "seconds_to_pre_open": 0, "next_open_str": ""}
        eng.config.MAX_OPEN_POSITIONS = 5
        eng.config.get = lambda key, default=None: default
        with patch.object(main, "evaluate_trade", return_value=real_scan()):
            await main._run_forced_bot_scan(eng, "SPY", "settings_changed")
        ok = main._build_scan_log("SPY", main._format_scan_for_ui(eng.bot_scans["SPY"]), settings, {}, [], {"equity": 1}, {}, eng, [])
        self.assertEqual(ok["forced_scan"]["reason"], "settings_changed")
        self.assertEqual(ok["forced_scan"]["status"], "ok")

    async def test_a_strategy_bots_forced_scan_carries_its_evaluation_for_the_card(self):
        settings = {"strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_variant": "sma_50_200",
                    "buy_mode": sp.PRESET_MODE, "sell_mode": sp.PRESET_MODE, "timeframe": "1Day", "market_session": "Regular"}
        eng = fake_engine(settings=settings)
        scan = real_scan(strategy_preset={"status": "READY", "action": "HOLD", "state": "FLAT", "values": {"close": 100.0, "fast": 410.2, "slow": 421.7},
                                           "warmup": {"required": 202, "available": 640}})
        with patch.object(main, "evaluate_trade", return_value=scan):
            await main._run_forced_bot_scan(eng, "SPY", "created")
        stored = eng.bot_scans["SPY"]
        self.assertEqual(stored["strategy_preset"]["status"], "READY")
        self.assertEqual(stored["forced_scan"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
