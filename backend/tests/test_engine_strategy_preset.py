"""Drive the real UserEngine.trading_loop with a mocked broker and mocked evaluations.

No network, no broker, no storage: ``evaluate_trade`` is replaced with crafted
results, the broker is a mock and the clock is controlled by the test.
"""
import asyncio
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from zoneinfo import ZoneInfo

import engine
import nyse_calendar
import preset_runtime as rt
import strategy_presets as sp
from corporate_actions import SplitEvent
from corporate_actions_service import SplitLookup

ET = ZoneInfo("America/New_York")
SIGNAL_A = "golden_cross:sma_50_200:1Day:2026-10-02T00:00:00:BUY"
SIGNAL_B = "golden_cross:sma_50_200:1Day:2026-10-09T00:00:00:BUY"
MONDAY_10AM = datetime(2026, 10, 5, 10, 0, tzinfo=ET)
# Daily bar the evaluations are made on: Friday 2 Oct 2026, the newest completed candle at Monday 10:00 ET.
CANDLE_TS = int(datetime(2026, 10, 2, tzinfo=ET).timestamp())


def setUpModule():
    global _calendar_patch
    _calendar_patch = patch.object(nyse_calendar, "_calendar", return_value=None)
    _calendar_patch.start()


def tearDownModule():
    _calendar_patch.stop()


class StopLoop(Exception):
    pass


def bot_settings(**overrides):
    settings = {
        "strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_variant": "sma_50_200",
        "strategy_preset_catalog_version": sp.CATALOG_VERSION, "buy_mode": sp.PRESET_MODE, "sell_mode": sp.PRESET_MODE,
        "timeframe": "1Day", "market_session": "Regular", "allocation_mode": "fixed", "amount": 500.0,
        "max_position_pct": 1.0, "risk_per_trade": 0.1, "enable_cooldown": False, "enable_liquidity_filter": False,
        "enable_signal_reset": True, "enable_smart_reentry": False, "paused": False,
    }
    settings.update(overrides)
    return settings


def preset_result(action="HOLD", price=100.0, key=None, atr=2.0, status="READY", reason="", candle_ts=None):
    return {
        "time": MONDAY_10AM.isoformat(), "ticker": "SPY", "action": action, "option_intent": "HOLD",
        "reason": reason or action, "price": f"${price:.2f}", "price_raw": price, "signals": {},
        "bullish_count": 0, "bearish_count": 0, "total_signals": 0, "atr": atr, "rsi": None,
        "position_sizing": {"shares": 5.0, "notional": 500.0, "stop_loss": None, "take_profit": None,
                            "risk_amount": 30.0, "stop_distance": 6.0},
        "price_history": [], "risk_status": {}, "timeframe": "1Day",
        "evaluation_status": "valid" if status == "READY" else "waiting",
        "candle_meta": {"candle_key": f"1DAY:{candle_ts or CANDLE_TS}", "candle_status": "closed"},
        "buy_mode": sp.PRESET_MODE, "sell_mode": sp.PRESET_MODE,
        "strategy_preset": {"status": status, "action": action, "signal_key": key if action in ("BUY", "SELL") else None,
                            "atr": atr, "stop_atr_mult": 3.0},
    }


def position(qty=5.0, avg=100.0, current=100.0):
    return {"symbol": "SPY", "qty": qty, "avg_price": avg, "current_price": current, "unrealized_pl": (current - avg) * qty}


def eng_client_id(eng, broker):
    """The id the engine derives for the entry: stable across retries of the same candle."""
    return eng._order_intent_id("SPY", "buy", {"candle_meta": {"candle_key": f"1DAY:{CANDLE_TS}"}})


class Step:
    def __init__(self, result=None, positions=(), orders=(), at=None, lookup=None, market_open=True, force=None, hook=None):
        self.result, self.positions, self.orders, self.at, self.lookup = result, list(positions), list(orders), at, lookup
        self.market_open = market_open
        self.force = force  # a per-bot forced-scan request made just before this pass (as a settings save does)
        self.hook = hook    # optional callable(broker, engine) run just before this pass (e.g. the broker now reports a fill)


class PresetLoopTests(unittest.IsolatedAsyncioTestCase):
    async def run_steps(self, steps, settings=None, locked_direction=None, forced=True, configure_broker=None, engine_hook=None):
        broker = MagicMock()
        broker.simulation_mode = False
        broker.order_owner_tag = "t"
        broker.get_account_info.return_value = {"simulation": False, "equity": 10_000.0, "cash": 10_000.0,
                                                "_risk_account_key": "acct", "daily_starting_equity": 10_000.0}
        broker.place_order.return_value = {"success": True, "order_id": "o1", "qty": None, "status": "accepted", "client_order_id": "c1"}
        broker.close_position.return_value = {"success": True, "order_id": "x1", "status": "filled", "qty": 5.0, "proceeds": 465.0}
        broker.cancel_orders_for_ticker.return_value = {"success": True}
        broker.cancel_all_entry_orders.return_value = {"success": True}
        broker.cancel_exit_orders.return_value = {"success": True, "cancelled": 0}
        broker.get_latest_quote.return_value = None
        if configure_broker:
            configure_broker(broker)
        with patch("engine.create_broker", return_value=broker):
            eng = engine.UserEngine("preset-loop", MagicMock())
        eng.config.TRADELIST = ["SPY"]
        eng.config.TICKER_SETTINGS = {"SPY": settings if settings is not None else bot_settings()}
        eng.save_settings, eng.save_history, eng.save_history_throttled = AsyncMock(), AsyncMock(), AsyncMock()
        if engine_hook:
            engine_hook(eng)
        if locked_direction:
            eng.signal_locked_direction["SPY"] = locked_direction
        # Each scan happens two minutes after the previous one unless a step pins its own time
        # (the engine skips a ticker scanned less than a minute ago).
        for index, step in enumerate(steps):
            if step.at is None:
                step.at = MONDAY_10AM + timedelta(minutes=2 * index)
        clock = {"now": steps[0].at}
        state = {"i": 0}

        class Trigger:
            def is_set(self):
                return forced

            def set(self):
                pass  # the shared event is not what these tests are about; the per-bot request dict is

            def clear(self):
                pass

            async def wait(self_inner):
                state["i"] += 1
                if state["i"] >= len(steps):
                    raise StopLoop()
                clock["now"] = steps[state["i"]].at
                apply(steps[state["i"]])

        def apply(step):
            broker.get_positions.return_value = step.positions
            broker.get_open_orders.return_value = step.orders
            if step.force:
                eng.request_forced_scan("SPY", step.force)
            if step.hook:
                step.hook(broker, eng)

        eng.force_scan_trigger = Trigger()
        apply(steps[0])

        evals = []

        def fake_evaluate(ticker, **kwargs):
            evals.append(state["i"])
            step = steps[min(state["i"], len(steps) - 1)]
            self.assertTrue(kwargs.get("use_bot_settings"))
            return dict(step.result)

        def lookup_for(symbol, last_key):
            step = steps[min(state["i"], len(steps) - 1)]
            return step.lookup or SplitLookup("ok", "alpaca", [])

        open_status = {"is_active_window": True, "is_open": True, "market_session_label": "Regular",
                       "seconds_to_open": 0, "seconds_to_pre_open": 0, "next_open_str": ""}
        closed_status = {"is_active_window": False, "is_open": False, "market_session_label": "Regular",
                         "seconds_to_open": 40000, "seconds_to_pre_open": 39880, "next_open_str": "Friday at 9:30 AM ET"}

        def market_status(self_engine, session="Regular"):
            return open_status if steps[min(state["i"], len(steps) - 1)].market_open else closed_status
        # Unforced scans honour the refresh interval, which the engine measures with time.time().
        wall_clock = patch("engine.time.time", side_effect=lambda: clock["now"].timestamp()) if not forced else patch("engine.time.sleep")
        with wall_clock, patch("engine.evaluate_trade", side_effect=fake_evaluate), \
                patch.object(engine.UserEngine, "get_now", lambda self: clock["now"]), \
                patch.object(engine.UserEngine, "get_stock_market_status", market_status), \
                patch("engine.split_events_after", side_effect=lookup_for):
            with self.assertRaises(StopLoop):
                await eng.trading_loop()
        eng.broker = broker
        eng.evals = evals  # indexes of the steps that triggered a full evaluation
        return eng, broker

    def settings_of(self, eng):
        return eng.config.TICKER_SETTINGS["SPY"]

    async def test_entry_is_placed_once_inside_the_session_with_no_broker_side_stop(self):
        eng, broker = await self.run_steps([Step(preset_result("BUY", key=SIGNAL_A))])
        self.assertEqual(broker.place_order.call_count, 1)
        kwargs = broker.place_order.call_args.kwargs
        self.assertEqual((kwargs["symbol"], kwargs["side"]), ("SPY", "buy"))
        self.assertIsNone(kwargs["stop_loss"])  # software-only protection
        self.assertTrue(kwargs["client_order_id"].startswith("bb-"))
        self.assertEqual(kwargs["client_order_id"], eng_client_id(eng, broker))
        settings = self.settings_of(eng)
        self.assertTrue(settings[rt.OWNS_KEY])
        self.assertEqual(settings[rt.LAST_ENTRY_KEY], SIGNAL_A)
        self.assertEqual(settings[rt.STOP_KEY]["entry_atr"], 2.0)

    async def test_the_same_signal_is_not_ordered_again_while_the_order_is_pending_or_after_a_stop_out(self):
        steps = [
            Step(preset_result("BUY", key=SIGNAL_A)),
            Step(preset_result("BUY", key=SIGNAL_A), orders=[{"symbol": "SPY", "side": "buy", "status": "accepted"}]),
            Step(preset_result("HOLD", price=93.0), positions=[position(5, 100.0, 93.0)]),      # stop 94 -> exit
            Step(preset_result("BUY", key=SIGNAL_A)),                                              # same signal after the stop-out
        ]
        eng, broker = await self.run_steps(steps)
        self.assertEqual(broker.place_order.call_count, 1)
        self.assertEqual(broker.close_position.call_count, 1)

    async def test_protective_stop_sells_a_managed_position_and_uses_the_entry_atr(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}})
        eng, broker = await self.run_steps(
            [Step(preset_result("HOLD", price=95.0, atr=9.0), positions=[position(5, 100.0, 95.0)])], settings=settings)
        self.assertEqual(broker.close_position.call_count, 0)  # 95 > 94 (stop uses entry ATR 2, not today's 9)
        eng, broker = await self.run_steps(
            [Step(preset_result("HOLD", price=93.5, atr=9.0), positions=[position(5, 100.0, 93.5)])], settings=settings)
        self.assertEqual(broker.close_position.call_count, 1)
        self.assertIn("Stop Loss Hit", eng.trade_log[0]["reason"])

    async def test_strategy_exit_sells_a_managed_position(self):
        settings = bot_settings(**{rt.OWNS_KEY: True})
        eng, broker = await self.run_steps(
            [Step(preset_result("SELL", price=110.0, key="exit"), positions=[position(5, 100.0, 110.0)])], settings=settings)
        self.assertEqual(broker.close_position.call_count, 1)

    async def test_compound_amount_follows_realized_pl_after_a_strategy_exit_and_after_a_protective_stop(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, "allocation_mode": "compound", "compound_amount": 500.0})
        eng, _ = await self.run_steps(
            [Step(preset_result("SELL", price=110.0, key="exit"), positions=[position(5, 100.0, 110.0)])], settings=settings)
        self.assertEqual(self.settings_of(eng)["compound_amount"], 550.0)  # 500 + (110-100)*5
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0},
                                   "allocation_mode": "compound", "compound_amount": 500.0})
        eng, _ = await self.run_steps(
            [Step(preset_result("HOLD", price=93.5, atr=9.0), positions=[position(5, 100.0, 93.5)])], settings=settings)
        self.assertEqual(self.settings_of(eng)["compound_amount"], 467.5)  # 500 + (93.5-100)*5

    async def test_fixed_amount_is_not_changed_by_a_closed_trade(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, "allocation_mode": "fixed", "amount": 500.0})
        eng, _ = await self.run_steps(
            [Step(preset_result("SELL", price=110.0, key="exit"), positions=[position(5, 100.0, 110.0)])], settings=settings)
        self.assertEqual(self.settings_of(eng)["amount"], 500.0)
        self.assertNotIn("compound_amount", self.settings_of(eng))

    async def test_a_legacy_cooldown_setting_does_not_delay_a_strategy_reentry(self):
        # saved before the manual re-entry rules were switched off for strategies: cooldown still "on"
        settings = bot_settings(**{rt.OWNS_KEY: True, "enable_cooldown": True, "enable_signal_reset": True})
        steps = [
            Step(preset_result("SELL", price=110.0, key="exit"), positions=[position(5, 100.0, 110.0)]),   # exit
            Step(preset_result("BUY", key=SIGNAL_B)),                                                       # fresh signal 2 minutes later
        ]
        eng, broker = await self.run_steps(steps, settings=settings)
        self.assertEqual(broker.close_position.call_count, 1)
        self.assertEqual(broker.place_order.call_count, 1)
        self.assertNotIn(eng._get_signal_reset_lock("SPY"), ("BUY", "SELL"))

    async def test_existing_position_is_left_alone_unless_adopted(self):
        pos = [position(5, 100.0, 80.0)]  # deeply under water: still not touched when unmanaged
        sell = preset_result("SELL", price=80.0, key="exit")
        eng, broker = await self.run_steps([Step(sell, positions=pos)], settings=bot_settings())
        self.assertEqual(broker.close_position.call_count, 0)
        self.assertFalse(self.settings_of(eng).get(rt.OWNS_KEY))
        eng, broker = await self.run_steps([Step(sell, positions=pos)], settings=bot_settings(**{rt.POLICY_KEY: "adopt"}))
        self.assertEqual(broker.close_position.call_count, 1)
        self.assertTrue(self.settings_of(eng)[rt.OWNS_KEY])

    async def test_nothing_is_ordered_outside_the_regular_session_or_on_a_holiday(self):
        for when in (datetime(2026, 10, 5, 8, 0, tzinfo=ET), datetime(2026, 10, 5, 17, 0, tzinfo=ET),
                     datetime(2026, 11, 26, 11, 0, tzinfo=ET)):
            eng, broker = await self.run_steps([Step(preset_result("BUY", key=SIGNAL_A), at=when)])
            self.assertEqual(broker.place_order.call_count, 0, when)

    async def test_stale_or_missing_strategy_data_blocks_entries_but_the_protective_stop_still_works(self):
        eng, broker = await self.run_steps([Step(preset_result("HOLD", status="WAITING_DATA"))])
        self.assertEqual(broker.place_order.call_count, 0)
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}})
        eng, broker = await self.run_steps(
            [Step(preset_result("HOLD", price=90.0, status="WAITING_DATA"), positions=[position(5, 100.0, 90.0)])], settings=settings)
        self.assertEqual(broker.close_position.call_count, 1)

    async def test_signal_reset_lock_does_not_block_a_strategy_entry(self):
        eng, broker = await self.run_steps([Step(preset_result("BUY", key=SIGNAL_A))], locked_direction="BUY")
        self.assertEqual(broker.place_order.call_count, 1)

    async def test_a_fresh_signal_after_a_closed_position_can_enter_again(self):
        steps = [
            Step(preset_result("BUY", key=SIGNAL_A)),
            Step(preset_result("HOLD", price=100.0), positions=[position()]),
            Step(preset_result("SELL", price=105.0, key="exit"), positions=[position(5, 100.0, 105.0)]),
            Step(preset_result("BUY", key=SIGNAL_B)),
        ]
        eng, broker = await self.run_steps(steps)
        self.assertEqual(broker.place_order.call_count, 2)
        self.assertEqual(self.settings_of(eng)[rt.LAST_ENTRY_KEY], SIGNAL_B)

    async def test_global_risk_halt_blocks_the_entry_but_not_a_strategy_exit(self):
        from trader import get_risk_manager
        manager = get_risk_manager()
        with patch.object(type(manager), "trading_halted", new_callable=PropertyMock, return_value=True), \
                patch.object(type(manager), "halt_reason", new_callable=PropertyMock, return_value="test halt"):
            eng, broker = await self.run_steps([Step(preset_result("BUY", key=SIGNAL_A))])
            self.assertEqual(broker.place_order.call_count, 0)
            settings = bot_settings(**{rt.OWNS_KEY: True})
            eng, broker = await self.run_steps(
                [Step(preset_result("SELL", price=110.0, key="exit"), positions=[position(5, 100.0, 110.0)])], settings=settings)
            self.assertEqual(broker.close_position.call_count, 1)


TUESDAY_10AM = MONDAY_10AM + timedelta(days=1)
MONDAY_CANDLE_TS = int(datetime(2026, 10, 5, tzinfo=ET).timestamp())  # the candle that completes after Monday's close


class ScanPolicyLoopTests(unittest.IsolatedAsyncioTestCase):
    """A strategy's per-candle scan policy: a flat bot is evaluated once per completed daily candle."""
    run_steps = PresetLoopTests.run_steps

    def mins(self, base, minutes):
        return base + timedelta(minutes=minutes)

    async def test_a_flat_bot_with_nothing_to_do_is_evaluated_once_not_every_refresh(self):
        steps = [Step(preset_result("HOLD"), at=self.mins(MONDAY_10AM, 2 * i)) for i in range(5)]
        eng, _ = await self.run_steps(steps, forced=False)
        self.assertEqual(eng.evals, [0])
        marker = eng.preset_scan_markers["SPY"]
        self.assertEqual(marker["epoch"], "2026-10-02")
        self.assertTrue(marker["next_evaluation_at"].startswith("2026-10-06T09:30"))  # next regular open after the Monday close

    async def test_it_is_evaluated_again_when_the_next_daily_candle_has_completed(self):
        steps = [
            Step(preset_result("HOLD"), at=MONDAY_10AM),
            Step(preset_result("HOLD"), at=self.mins(MONDAY_10AM, 2)),
            Step(preset_result("HOLD", candle_ts=MONDAY_CANDLE_TS), at=TUESDAY_10AM),
            Step(preset_result("HOLD", candle_ts=MONDAY_CANDLE_TS), at=self.mins(TUESDAY_10AM, 2)),
        ]
        eng, _ = await self.run_steps(steps, forced=False)
        self.assertEqual(eng.evals, [0, 2])

    async def test_lagging_candle_data_never_idles_the_bot(self):
        steps = [Step(preset_result("HOLD"), at=self.mins(TUESDAY_10AM, 2 * i)) for i in range(3)]  # still Friday's candle on Tuesday
        eng, _ = await self.run_steps(steps, forced=False)
        self.assertEqual(eng.evals, [0, 1, 2])
        self.assertNotIn("SPY", eng.preset_scan_markers)

    async def test_an_open_position_keeps_the_normal_refresh_for_the_protective_stop(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}})
        steps = [Step(preset_result("HOLD", price=99.0), positions=[position(5, 100.0, 99.0)], at=self.mins(MONDAY_10AM, 2 * i)) for i in range(4)]
        eng, broker = await self.run_steps(steps, settings=settings, forced=False)
        self.assertEqual(eng.evals, [0, 1, 2, 3])
        self.assertNotIn("SPY", eng.preset_scan_markers)
        self.assertEqual(broker.close_position.call_count, 0)

    async def test_the_protective_stop_still_sells_on_a_later_scan_while_holding(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}})
        steps = [
            Step(preset_result("HOLD", price=99.0), positions=[position(5, 100.0, 99.0)], at=MONDAY_10AM),
            Step(preset_result("HOLD", price=93.0), positions=[position(5, 100.0, 93.0)], at=self.mins(MONDAY_10AM, 2)),
        ]
        eng, broker = await self.run_steps(steps, settings=settings, forced=False)
        self.assertEqual(eng.evals, [0, 1])
        self.assertEqual(broker.close_position.call_count, 1)

    async def test_a_pending_order_keeps_the_normal_refresh(self):
        order = [{"symbol": "SPY", "side": "buy", "status": "accepted"}]
        steps = [Step(preset_result("HOLD"), orders=order, at=self.mins(MONDAY_10AM, 2 * i)) for i in range(3)]
        eng, _ = await self.run_steps(steps, forced=False)
        self.assertEqual(eng.evals, [0, 1, 2])

    async def test_a_blocked_entry_is_retried_at_the_normal_refresh(self):
        settings = bot_settings(**{rt.LAST_ENTRY_KEY: SIGNAL_A})  # the signal was already acted on -> the BUY is held back
        steps = [Step(preset_result("BUY", key=SIGNAL_A), at=self.mins(MONDAY_10AM, 2 * i)) for i in range(3)]
        eng, broker = await self.run_steps(steps, settings=settings, forced=False)
        self.assertEqual(eng.evals, [0, 1, 2])
        self.assertEqual(broker.place_order.call_count, 0)

    async def test_missing_or_waiting_data_is_retried_not_treated_as_nothing_to_do(self):
        steps = [Step(preset_result("HOLD", status="WAITING_DATA"), at=self.mins(MONDAY_10AM, 2 * i)) for i in range(3)]
        eng, _ = await self.run_steps(steps, forced=False)
        self.assertEqual(eng.evals, [0, 1, 2])

    async def test_a_new_entry_signal_is_still_acted_on_after_an_idle_stretch(self):
        steps = [
            Step(preset_result("HOLD"), at=MONDAY_10AM),
            Step(preset_result("HOLD"), at=self.mins(MONDAY_10AM, 2)),                       # skipped
            Step(preset_result("BUY", key=SIGNAL_B, candle_ts=MONDAY_CANDLE_TS), at=TUESDAY_10AM),
        ]
        eng, broker = await self.run_steps(steps, forced=False)
        self.assertEqual(eng.evals, [0, 2])
        self.assertEqual(broker.place_order.call_count, 1)

    async def test_an_intraday_timeframe_keeps_the_normal_refresh(self):
        settings = bot_settings(timeframe="5Min")
        steps = [Step(preset_result("HOLD"), at=self.mins(MONDAY_10AM, 2 * i)) for i in range(3)]
        eng, _ = await self.run_steps(steps, settings=settings, forced=False)
        self.assertEqual(eng.evals, [0, 1, 2])

    async def test_a_forced_scan_always_evaluates(self):
        steps = [Step(preset_result("HOLD"), at=self.mins(MONDAY_10AM, 2 * i)) for i in range(3)]
        eng, _ = await self.run_steps(steps, forced=True)
        self.assertEqual(eng.evals, [0, 1, 2])

    async def test_a_paused_split_reconcile_keeps_the_normal_refresh(self):
        settings = bot_settings(**{rt.PAUSE_KEY: True})
        steps = [Step(preset_result("HOLD"), at=self.mins(MONDAY_10AM, 2 * i)) for i in range(3)]
        eng, _ = await self.run_steps(steps, settings=settings, forced=False)
        self.assertEqual(eng.evals, [0, 1, 2])


class ForcedScanLoopTests(unittest.IsolatedAsyncioTestCase):
    """A settings save asks the loop to re-evaluate that bot on its very next pass."""
    run_steps = PresetLoopTests.run_steps

    def steps(self, forced_at, count=4, step_seconds=10, market_open=True):
        return [Step(preset_result("HOLD"), at=MONDAY_10AM + timedelta(seconds=step_seconds * i),
                     force="settings_changed" if i == forced_at else None, market_open=market_open) for i in range(count)]

    async def test_without_a_request_the_refresh_interval_still_applies(self):
        eng, _ = await self.run_steps(self.steps(forced_at=None), settings=bot_settings(timeframe="5Min"), forced=False)
        self.assertEqual(eng.evals, [0])                                   # passes 10 s apart: only the first is due

    async def test_a_forced_request_makes_the_next_pass_re_evaluate_that_bot_immediately(self):
        eng, _ = await self.run_steps(self.steps(forced_at=2), settings=bot_settings(timeframe="5Min"), forced=False)
        self.assertEqual(eng.evals, [0, 2])                                # pass 2 is forced; pass 3 is back to the interval
        self.assertEqual(eng.force_scan_tickers, {})                       # the request is consumed
        tag = eng.bot_scans["SPY"]["forced_scan"]
        self.assertEqual((tag["reason"], tag["status"]), ("settings_changed", "ok"))

    async def test_a_forced_request_also_beats_a_strategys_once_per_candle_idling(self):
        steps = [Step(preset_result("HOLD"), at=MONDAY_10AM + timedelta(minutes=2 * i), force="settings_changed" if i == 2 else None) for i in range(4)]
        eng, _ = await self.run_steps(steps, forced=False)                 # 1Day strategy: idles after the first evaluation
        self.assertEqual(eng.evals, [0, 2])

    async def test_a_forced_request_evaluates_even_while_the_market_is_closed(self):
        steps = [Step(preset_result("HOLD"), at=MONDAY_10AM, market_open=True)] + [
            Step(preset_result("HOLD"), at=MONDAY_10AM.replace(hour=20) + timedelta(seconds=10 * i),
                 market_open=False, force="created" if i == 1 else None) for i in range(3)]
        eng, _ = await self.run_steps(steps, settings=bot_settings(timeframe="5Min"), forced=False)
        self.assertEqual(eng.evals, [0, 2])                                # closed passes are skipped, the forced one is evaluated
        self.assertEqual(eng.bot_scans["SPY"]["forced_scan"]["reason"], "created")

    async def test_the_request_is_per_bot_and_ignores_blank_tickers(self):
        broker = MagicMock()
        with patch("engine.create_broker", return_value=broker):
            eng = engine.UserEngine("force-unit", MagicMock())
        eng.request_forced_scan("spy", "created")
        eng.request_forced_scan("  ", "created")
        eng.request_forced_scan(None)
        self.assertEqual(eng.force_scan_tickers, {"SPY": "created"})
        self.assertTrue(eng.force_scan_trigger.is_set())


class ClosedMarketKeepsLastScanTests(unittest.IsolatedAsyncioTestCase):
    run_steps = PresetLoopTests.run_steps

    async def test_the_last_real_scan_survives_market_closed_scans_without_candle_history(self):
        real = preset_result("HOLD")
        real["price_history"] = [{"time": 1, "close": 1.0}] * 50
        steps = [
            Step(real, at=MONDAY_10AM),                                              # market open: a real scan
            Step(preset_result("HOLD"), at=MONDAY_10AM.replace(hour=20), market_open=False),   # evening: closed placeholder
            Step(preset_result("HOLD"), at=MONDAY_10AM.replace(hour=21), market_open=False),   # still closed: carried forward
        ]
        eng, _ = await self.run_steps(steps, forced=False)
        closed = eng.bot_scans["SPY"]
        self.assertTrue(closed["is_stock_market_closed"])
        last = closed["last_real_scan"]
        self.assertEqual(last["strategy_preset"]["status"], "READY")
        self.assertNotIn("price_history", last)                                       # not persisted with the scan state
        self.assertNotIn("last_real_scan", last)                                      # no nesting from scan to scan
        self.assertNotIn("is_stock_market_closed", last)


class SplitDayLoopTests(unittest.IsolatedAsyncioTestCase):
    helper = PresetLoopTests.run_steps
    settings_of = PresetLoopTests.settings_of

    def split_lookup(self):
        event = SplitEvent("SPY", date(2026, 10, 5), 4.0)
        return event, SplitLookup("ok", "alpaca", [event])

    async def test_split_today_rescales_levels_logs_it_and_never_liquidates(self):
        event, lookup = self.split_lookup()
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.LAST_QTY_KEY: 10.0, rt.LAST_CORPORATE_ACTION_KEY: "SPY:2020-08-31:4.000000",
                                   "custom_buy_price": 400.0, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 8.0}})
        steps = [Step(preset_result("HOLD", price=100.0, atr=2.0), positions=[position(40, 100.0, 100.0)], lookup=lookup)]
        eng, broker = await self.helper(steps, settings=settings)
        saved = self.settings_of(eng)
        self.assertEqual(saved["custom_buy_price"], 100.0)
        self.assertEqual(saved[rt.STOP_KEY]["entry_atr"], 2.0)
        self.assertEqual(saved[rt.LAST_CORPORATE_ACTION_KEY], event.key)
        self.assertEqual(broker.close_position.call_count, 0)
        self.assertEqual(broker.place_order.call_count, 0)
        self.assertTrue(any(entry.get("log_type") == "Corporate Action" for entry in eng.trade_log))

    async def test_broker_not_yet_updated_keeps_entries_paused_then_resolves(self):
        event, lookup = self.split_lookup()
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.LAST_QTY_KEY: 10.0, rt.LAST_CORPORATE_ACTION_KEY: "SPY:2020-08-31:4.000000",
                                   "custom_buy_price": 400.0})
        steps = [
            Step(preset_result("BUY", key=SIGNAL_A), positions=[position(10, 400.0, 400.0)], lookup=lookup),  # still 10 sh
            Step(preset_result("BUY", key=SIGNAL_A), positions=[position(10, 400.0, 400.0)], lookup=lookup),
        ]
        eng, broker = await self.helper(steps, settings=settings)
        saved = self.settings_of(eng)
        self.assertEqual(saved[rt.PAUSE_KEY], event.key)
        self.assertEqual(saved["custom_buy_price"], 400.0)
        self.assertEqual(broker.place_order.call_count, 0)
        review_logs = [e for e in eng.trade_log if e.get("log_type") == "Corporate Action"]
        self.assertEqual(len(review_logs), 1)  # reported once, retried silently
        steps.append(Step(preset_result("HOLD", price=100.0), positions=[position(40, 100.0, 100.0)], lookup=lookup))
        eng, broker = await self.helper(steps, settings=settings)
        resolved = self.settings_of(eng)
        self.assertNotIn(rt.PAUSE_KEY, resolved)
        self.assertEqual(resolved["custom_buy_price"], 100.0)

    async def test_unavailable_split_data_changes_nothing_and_blocks_nothing(self):
        settings = bot_settings(**{rt.LAST_CORPORATE_ACTION_KEY: "SPY:2020-08-31:4.000000", "custom_buy_price": 400.0})
        steps = [Step(preset_result("BUY", key=SIGNAL_A), lookup=SplitLookup("unavailable", "none", []))]
        eng, broker = await self.helper(steps, settings=settings)
        self.assertEqual(self.settings_of(eng)["custom_buy_price"], 400.0)
        self.assertEqual(broker.place_order.call_count, 1)


if __name__ == "__main__":
    unittest.main()
