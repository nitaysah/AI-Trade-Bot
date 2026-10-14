"""Per-bot scan interval: pure rules, API validation and the real engine loop gate."""
import os
import sys
import unittest
from datetime import timedelta
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_scan_interval as I  # noqa: E402
import test_engine_strategy_preset as harness  # noqa: E402
from test_engine_strategy_preset import MONDAY_10AM, Step, bot_settings, position, preset_result  # noqa: E402

setUpModule, tearDownModule = harness.setUpModule, harness.tearDownModule


class PureRules(unittest.TestCase):
    def test_unconfigured_bots_keep_the_engine_default(self):
        self.assertEqual(I.effective_seconds({}, 60), 60)
        self.assertEqual(I.effective_seconds({}, 10), 10)
        self.assertEqual(I.effective_seconds({"scan_interval_auto": None, "scan_interval_seconds": None}, 60), 60)
        self.assertEqual(I.effective_seconds({"scan_interval_auto": False, "scan_interval_seconds": 0}, 60), 60)

    def test_fixed_and_auto(self):
        self.assertEqual(I.effective_seconds({"scan_interval_seconds": 300}, 60), 300)
        self.assertEqual(I.effective_seconds({"scan_interval_seconds": "900"}, 60), 900)
        self.assertEqual(I.effective_seconds({"scan_interval_auto": True, "timeframe": "5Min"}, 60), 300)
        self.assertEqual(I.effective_seconds({"scan_interval_auto": True, "timeframe": "1Hour"}, 60), 3600)
        self.assertEqual(I.effective_seconds({"scan_interval_auto": True, "timeframe": "1Min"}, 60), 60)
        self.assertEqual(I.effective_seconds({"scan_interval_auto": True, "timeframe": "1Day"}, 60), 14400)  # clamped
        self.assertEqual(I.effective_seconds({"scan_interval_auto": True, "timeframe": "weird"}, 60), 900)
        self.assertEqual(I.effective_seconds({"scan_interval_auto": "true", "scan_interval_seconds": 30, "timeframe": "5Min"}, 60), 300)

    def test_busy_bots_never_slow_below_the_default(self):
        self.assertEqual(I.effective_seconds({"scan_interval_seconds": 3600}, 60, busy=True), 60)
        self.assertEqual(I.effective_seconds({"scan_interval_auto": True, "timeframe": "1Day"}, 60, busy=True), 60)
        self.assertEqual(I.effective_seconds({"scan_interval_seconds": 10}, 60, busy=True), 10)  # faster is fine
        self.assertEqual(I.effective_seconds({"scan_interval_seconds": 3600}, 10, busy=True), 10)  # options default

    def test_exit_kind_only_a_plain_indicator_sell_is_indicator_only(self):
        self.assertEqual(I.exit_watch_kind({"sell_mode": "indicator"}), "indicator")
        self.assertEqual(I.exit_watch_kind({}), "indicator")      # the bot default is an indicator exit
        for mode in ("sltp", "atr", "hybrid", "hybrid_atr", "percentage", "hybrid_pct", "pivot_points", "high_low",
                     "swing_points", "custom_sltp", "ai_autopilot", "trend_rider", "some_future_mode"):
            self.assertEqual(I.exit_watch_kind({"sell_mode": mode}), "price", mode)
        self.assertEqual(I.exit_watch_kind({"sell_mode": "indicator", "options_trading": True}), "price")
        self.assertEqual(I.exit_watch_kind({"sell_mode": "indicator"}, is_option=True), "price")
        self.assertEqual(I.exit_watch_kind({"sell_mode": "indicator", "strategy_preset_enabled": True}), "price")

    def test_fast_scan_rules(self):
        ind, price = {"sell_mode": "indicator"}, {"sell_mode": "sltp"}
        self.assertFalse(I.needs_fast_scan(ind, has_position=False, has_order=False))
        self.assertFalse(I.needs_fast_scan(price, has_position=False, has_order=False))
        self.assertFalse(I.needs_fast_scan(ind, has_position=True, has_order=False))     # indicator exit: own interval
        self.assertTrue(I.needs_fast_scan(price, has_position=True, has_order=False))    # price exit: fast scan
        self.assertTrue(I.needs_fast_scan(ind, has_position=False, has_order=True))      # an order always needs it
        self.assertTrue(I.needs_fast_scan(ind, has_position=True, has_order=True))
        self.assertTrue(I.needs_fast_scan(ind, has_position=True, has_order=False, is_option=True))

    def test_validation(self):
        self.assertIsNone(I.validate({}))
        self.assertIsNone(I.validate({"scan_interval_auto": True, "scan_interval_seconds": "junk"}))
        self.assertIsNone(I.validate({"scan_interval_seconds": 10}))
        self.assertIsNone(I.validate({"scan_interval_seconds": 14400}))
        for bad in (5, 9, 14401, -60, "abc", float("nan")):
            self.assertTrue(I.validate({"scan_interval_seconds": bad}), bad)
        self.assertEqual(I.effective_seconds({"scan_interval_seconds": 5}, 60), 60)  # an invalid value never takes effect


class ApiValidation(unittest.TestCase):
    def test_save_rejects_a_bad_interval(self):
        import main
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            main._validate_bot_scan_interval({"scan_interval_seconds": 5})
        self.assertEqual(caught.exception.status_code, 400)
        main._validate_bot_scan_interval({"scan_interval_seconds": 300})
        main._validate_bot_scan_interval({"scan_interval_auto": True})
        main._validate_bot_scan_interval({})

    def test_card_reports_the_bots_own_interval(self):
        import main
        eng = MagicMock()
        eng.config.SCAN_INTERVAL_SECONDS, eng.config.OPTIONS_SCAN_INTERVAL_SECONDS = 60, 10
        self.assertEqual(main._bot_scan_interval_defaults(eng, {}), 60)
        self.assertEqual(main._bot_scan_interval_defaults(eng, {"options_trading": True}), 10)
        self.assertEqual(I.effective_seconds({"scan_interval_seconds": 300}, main._bot_scan_interval_defaults(eng, {})), 300)


class EngineLoopGate(unittest.IsolatedAsyncioTestCase):
    """The real trading loop: scans two minutes apart, 15-minute bot (the daily per-candle policy does not apply)."""
    run_steps = harness.PresetLoopTests.run_steps

    def steps(self, n, **kw):
        return [Step(preset_result("HOLD"), at=MONDAY_10AM + timedelta(minutes=2 * i), **kw) for i in range(n)]

    async def test_default_bot_is_scanned_every_pass(self):
        eng, _ = await self.run_steps(self.steps(5), settings=bot_settings(timeframe="15Min"), forced=False)
        self.assertEqual(eng.evals, [0, 1, 2, 3, 4])

    async def test_five_minute_interval_skips_passes_in_between(self):
        eng, _ = await self.run_steps(self.steps(7), settings=bot_settings(timeframe="15Min", scan_interval_seconds=300), forced=False)
        self.assertEqual(eng.evals, [0, 3, 6])  # 0s, 360s, 720s

    async def test_auto_follows_the_trading_timeframe(self):
        eng, _ = await self.run_steps(self.steps(10), settings=bot_settings(timeframe="15Min", scan_interval_auto=True), forced=False)
        self.assertEqual(eng.evals, [0, 8])  # 15 minutes = 900s; passes are 120s apart

    async def test_a_held_position_is_still_checked_every_minute(self):
        steps = [Step(preset_result("HOLD"), positions=[position(5, 100.0, 100.0)], at=MONDAY_10AM + timedelta(minutes=2 * i)) for i in range(5)]
        settings = bot_settings(timeframe="15Min", scan_interval_seconds=3600, strategy_preset_position_policy="adopt")
        eng, _ = await self.run_steps(steps, settings=settings, forced=False)
        self.assertEqual(eng.evals, [0, 1, 2, 3, 4])

    def manual(self, **kw):
        return bot_settings(strategy_preset_enabled=False, buy_mode="indicator", timeframe="15Min", scan_interval_seconds=3600, **kw)

    async def test_indicator_exit_with_a_position_follows_the_bots_own_interval(self):
        steps = [Step(preset_result("HOLD"), positions=[position(5, 100.0, 100.0)], at=MONDAY_10AM + timedelta(minutes=2 * i)) for i in range(5)]
        eng, _ = await self.run_steps(steps, settings=self.manual(sell_mode="indicator"), forced=False)
        self.assertEqual(eng.evals, [0])

    async def test_price_exit_with_a_position_is_watched_every_pass(self):
        steps = [Step(preset_result("HOLD"), positions=[position(5, 100.0, 100.0)], at=MONDAY_10AM + timedelta(minutes=2 * i)) for i in range(5)]
        eng, _ = await self.run_steps(steps, settings=self.manual(sell_mode="sltp"), forced=False)
        self.assertEqual(eng.evals, [0, 1, 2, 3, 4])

    async def test_indicator_exit_with_a_working_order_is_still_every_pass(self):
        steps = [Step(preset_result("HOLD"), positions=[position(5, 100.0, 100.0)], orders=[{"id": "o1", "symbol": "SPY", "side": "sell", "status": "new"}],
                      at=MONDAY_10AM + timedelta(minutes=2 * i)) for i in range(5)]
        eng, _ = await self.run_steps(steps, settings=self.manual(sell_mode="indicator"), forced=False)
        self.assertEqual(eng.evals, [0, 1, 2, 3, 4])

    async def test_a_forced_scan_ignores_the_interval(self):
        steps = [Step(preset_result("HOLD"), at=MONDAY_10AM + timedelta(minutes=2 * i)) for i in range(3)]
        steps[1].force = "settings saved"
        eng, _ = await self.run_steps(steps, settings=bot_settings(timeframe="15Min", scan_interval_seconds=3600), forced=False)
        self.assertEqual(eng.evals, [0, 1])


if __name__ == "__main__":
    unittest.main()
