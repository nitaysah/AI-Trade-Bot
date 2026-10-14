"""Order lifecycle against a mocked Alpaca that behaves like the real one.

Real Alpaca answers a market order with status ``accepted`` (an SDK enum whose ``str()`` is
``"OrderStatus.ACCEPTED"``) and reports the fill a moment later. These tests drive the real trading loop and
the real broker adapter through that sequence for both bot types (strategy preset and manual Entry/Exit).
"""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderStatus
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

import alpaca_broker
import engine
import preset_runtime as rt
import strategy_presets as sp
import test_engine_strategy_preset as harness
from test_engine_strategy_preset import MONDAY_10AM, SIGNAL_A, SIGNAL_B, Step, bot_settings, position, preset_result


def order_info(order_id, status, qty=0.0, price=None):
    return {"order_id": order_id, "client_order_id": f"c-{order_id}", "status": status,
            "filled_qty": qty, "filled_avg_price": price, "qty": None, "symbol": "SPY"}


def accepted(order_id):
    return {"success": True, "order_id": order_id, "client_order_id": f"c-{order_id}", "status": "accepted", "qty": None}


class FastFillPolling(unittest.IsolatedAsyncioTestCase):
    run_steps = harness.PresetLoopTests.run_steps

    def setUp(self):
        for name, value in (("FILL_CONFIRM_SECONDS", 0.2), ("FILL_POLL_SECONDS", 0.02)):
            patcher = patch.object(engine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def broker_with(self, fills):
        """fills: order_id -> order_info the broker reports for it (mutable, so a test can change it later)."""
        def configure(broker):
            broker.place_order.return_value = accepted("b1")
            broker.close_position.return_value = accepted("s1")
            broker.get_order.side_effect = lambda order_id=None, client_order_id=None: fills.get(order_id)
            broker.cancel_exit_orders.return_value = {"success": True, "cancelled": 0}
            broker.get_exit_order_levels.return_value = {}
            broker.place_equity_oco_exit.return_value = {"success": False}
        return configure


class StrategyBotFillTests(FastFillPolling):
    async def test_a_buy_that_fills_within_seconds_is_booked_once_from_the_real_fill(self):
        fills = {"b1": order_info("b1", "filled", 5.0, 100.25)}
        eng, broker = await self.run_steps([Step(preset_result("BUY", key=SIGNAL_A))], configure_broker=self.broker_with(fills))
        settings = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual(broker.place_order.call_count, 1)
        self.assertEqual((settings["entry_trades"], settings["total_trades"]), (1, 1))     # counted (was never counted before)
        self.assertNotIn(engine.PENDING_FILL_KEY, settings)
        logged = eng.trade_log[0]
        self.assertIn("✅ BOUGHT 5.000000 shares at $100.25", logged["reason"])            # real fill, not the candle close
        self.assertNotIn("failed", logged["reason"].lower())
        self.assertTrue(settings[rt.OWNS_KEY])

    async def test_a_buy_still_working_is_pending_never_resubmitted_and_booked_when_it_fills(self):
        fills = {"b1": order_info("b1", "accepted")}
        open_buy = [{"symbol": "SPY", "side": "buy", "status": "accepted"}]

        def broker_fills(broker, eng):
            fills["b1"] = order_info("b1", "filled", 5.0, 100.4)

        steps = [
            Step(preset_result("BUY", key=SIGNAL_A)),                                        # accepted, not filled yet
            Step(preset_result("BUY", key=SIGNAL_A), orders=open_buy),                     # still working: no duplicate
            Step(preset_result("HOLD", price=101.0), positions=[position(5, 100.4, 101.0)], hook=broker_fills),
        ]
        snapshots = []
        eng, broker = await self.run_steps(steps, configure_broker=self.broker_with(fills),
                                           engine_hook=lambda e: snapshots.append(e))
        settings = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual(broker.place_order.call_count, 1)
        self.assertEqual(settings.get("entry_trades"), 1)
        self.assertNotIn(engine.PENDING_FILL_KEY, settings)
        booked = [log for log in eng.trade_log if log.get("fill_confirmed_later")]
        self.assertEqual(len(booked), 1)
        self.assertIn("$100.40", booked[0]["reason"])

    async def test_a_pending_fill_saved_before_a_restart_is_booked_after_it(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, engine.PENDING_FILL_KEY: {
            "side": "buy", "order_id": "b9", "client_order_id": "c-b9", "signal_price": 100.0, "reason": "Golden Cross entry",
            "entry_price": 0.0, "position_qty": 0.0, "closes_position": True, "signal_key": SIGNAL_A, "timeframe": "1Day"}})
        fills = {"b9": order_info("b9", "filled", 5.0, 99.9)}
        eng, _ = await self.run_steps([Step(preset_result("HOLD"), positions=[position(5, 99.9, 100.0)])],
                                      settings=settings, configure_broker=self.broker_with(fills))
        self.assertEqual(eng.config.TICKER_SETTINGS["SPY"].get("entry_trades"), 1)
        self.assertNotIn(engine.PENDING_FILL_KEY, eng.config.TICKER_SETTINGS["SPY"])

    async def test_a_strategy_exit_books_realized_pl_and_compounding_from_the_fill_price(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, "allocation_mode": "compound", "compound_amount": 500.0})
        fills = {"s1": order_info("s1", "filled", 5.0, 110.5)}
        eng, broker = await self.run_steps(
            [Step(preset_result("SELL", price=110.0, key="exit"), positions=[position(5, 100.0, 110.4)])],
            settings=settings, configure_broker=self.broker_with(fills))
        settings = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual(broker.close_position.call_count, 1)
        self.assertEqual(settings["realized_pl"], 52.5)                 # (110.50 - 100) x 5, the broker's fill
        self.assertEqual(settings["compound_amount"], 552.5)
        self.assertEqual(settings["consecutive_losses"], 0)
        self.assertIn("✅ CLOSED", eng.trade_log[0]["reason"])

    async def test_a_losing_stop_exit_arms_the_loss_guards(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}})
        fills = {"s1": order_info("s1", "filled", 5.0, 93.4)}
        eng, _ = await self.run_steps(
            [Step(preset_result("HOLD", price=99.0), positions=[position(5, 100.0, 93.5)])],
            settings=settings, configure_broker=self.broker_with(fills))
        settings = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual(settings["realized_pl"], -33.0)
        self.assertEqual(settings["daily_realized_pl"], -33.0)
        self.assertEqual(settings["consecutive_losses"], 1)
        self.assertIn("post_stop_state", settings)

    async def test_the_protective_stop_uses_the_live_price_not_yesterdays_close(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}})  # stop 94
        fills = {"s1": order_info("s1", "filled", 5.0, 93.5)}
        eng, broker = await self.run_steps(
            [Step(preset_result("HOLD", price=99.0), positions=[position(5, 100.0, 93.5)])],   # candle 99, live 93.5
            settings=settings, configure_broker=self.broker_with(fills))
        self.assertEqual(broker.close_position.call_count, 1)
        self.assertIn("Stop Loss Hit ($93.50", eng.trade_log[0]["reason"])
        eng, broker = await self.run_steps(
            [Step(preset_result("HOLD", price=93.0), positions=[position(5, 100.0, 96.0)])],   # candle 93, live 96
            settings=bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}}),
            configure_broker=self.broker_with(fills))
        self.assertEqual(broker.close_position.call_count, 0)

    async def test_an_entry_cancelled_without_a_fill_changes_nothing_and_may_act_again(self):
        fills = {"b1": order_info("b1", "canceled")}
        eng, broker = await self.run_steps([Step(preset_result("BUY", key=SIGNAL_A))], configure_broker=self.broker_with(fills))
        settings = eng.config.TICKER_SETTINGS["SPY"]
        self.assertNotIn("entry_trades", settings)
        self.assertNotIn(rt.LAST_ENTRY_KEY, settings)                  # the same signal may be acted on again
        self.assertIn("canceled by the broker without a fill", eng.trade_log[0]["reason"])

    async def test_a_rejected_order_retries_with_a_new_client_order_id(self):
        def configure(broker):
            self.broker_with({})(broker)
            broker.place_order.return_value = {"success": False, "rejected": True, "error": "Broker rejected the order: insufficient buying power"}
        steps = [Step(preset_result("BUY", key=SIGNAL_A)), Step(preset_result("BUY", key=SIGNAL_A))]
        eng, broker = await self.run_steps(steps, configure_broker=configure)
        ids = [call.kwargs["client_order_id"] for call in broker.place_order.call_args_list]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])
        self.assertIn("insufficient buying power", eng.trade_log[0]["reason"])

    async def test_an_uncertain_outcome_keeps_the_same_id_so_a_retry_cannot_double_buy(self):
        def configure(broker):
            self.broker_with({})(broker)
            broker.place_order.return_value = {"success": False, "indeterminate": True, "error": "unknown"}
        eng, broker = await self.run_steps([Step(preset_result("BUY", key=SIGNAL_A)), Step(preset_result("BUY", key=SIGNAL_A))],
                                           configure_broker=configure)
        ids = [call.kwargs["client_order_id"] for call in broker.place_order.call_args_list]
        self.assertEqual(len(set(ids)), 1)


def manual_settings(**extra):
    settings = bot_settings(strategy_preset_enabled=False, strategy_preset="none", buy_mode="indicator", sell_mode="percentage",
                            regular_stop_loss_pct=0.05, regular_take_profit_pct=0.5, enable_pct_trail=False, regular_trailing_pct=0.03,
                            min_sell_signals=99, timeframe="1Hour")
    settings.pop("strategy_preset_variant", None)
    settings.update(extra)
    return settings


def manual_result(action="HOLD", price=99.0):
    result = preset_result(action, price=price)
    result.pop("strategy_preset", None)
    result["buy_mode"], result["sell_mode"] = "indicator", "percentage"
    result["timeframe"] = "1Hour"
    return result


class ManualBotTests(FastFillPolling):
    async def test_a_manual_buy_is_booked_from_the_real_fill(self):
        fills = {"b1": order_info("b1", "filled", 4.0, 250.0)}
        eng, broker = await self.run_steps([Step(manual_result("BUY", price=249.0))], settings=manual_settings(),
                                           configure_broker=self.broker_with(fills))
        settings = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual(settings.get("entry_trades"), 1)
        self.assertIn("✅ BOUGHT 4.000000 shares at $250.00", eng.trade_log[0]["reason"])

    async def test_a_manual_stop_uses_the_live_price(self):
        fills = {"s1": order_info("s1", "filled", 5.0, 94.0)}
        eng, broker = await self.run_steps([Step(manual_result("HOLD", price=99.0), positions=[position(5, 100.0, 94.0)])],
                                           settings=manual_settings(), configure_broker=self.broker_with(fills))
        self.assertEqual(broker.close_position.call_count, 1)             # stop 95: live 94 hits it, the candle close 99 would not
        settings = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual(settings["realized_pl"], -30.0)
        self.assertEqual(settings["consecutive_losses"], 1)

    async def test_a_trailing_stop_survives_a_restart_for_the_same_position(self):
        fills = {"s1": order_info("s1", "filled", 5.0, 116.0)}
        settings = manual_settings(enable_pct_trail=True)
        eng, broker = await self.run_steps([Step(manual_result("HOLD", price=119.0), positions=[position(5, 100.0, 120.0)])],
                                           settings=settings, configure_broker=self.broker_with(fills))
        saved = eng.config.TICKER_SETTINGS["SPY"][engine.TRAILING_STOPS_KEY]
        self.assertEqual((saved["stop"], saved["avg"]), (116.4, 100.0))       # 120 x (1 - 3%)
        # "restart": a new engine with the saved settings; the price slips to 116, below the restored trail
        eng2, broker2 = await self.run_steps([Step(manual_result("HOLD", price=117.0), positions=[position(5, 100.0, 116.0)])],
                                             settings=eng.config.TICKER_SETTINGS["SPY"], configure_broker=self.broker_with(fills))
        self.assertEqual(broker2.close_position.call_count, 1)
        self.assertIn("Percentage Stop Loss Hit", eng2.trade_log[0]["reason"])

    async def test_a_restored_trailing_stop_from_another_position_is_dropped(self):
        settings = manual_settings(enable_pct_trail=True, **{engine.TRAILING_STOPS_KEY: {"stop": 116.4, "avg": 90.0}})
        eng, broker = await self.run_steps([Step(manual_result("HOLD", price=117.0), positions=[position(5, 100.0, 116.0)])],
                                           settings=settings, configure_broker=self.broker_with({}))
        self.assertEqual(broker.close_position.call_count, 0)                # new position (avg 100): the old trail does not apply


class ExitProtectionTests(unittest.TestCase):
    def engine(self):
        broker = MagicMock()
        broker.simulation_mode = False
        with patch("engine.create_broker", return_value=broker):
            eng = engine.UserEngine("oco", MagicMock())
        return eng, broker

    def test_a_fractional_position_never_attempts_a_broker_oco(self):
        eng, broker = self.engine()
        settings = {"sell_mode": "percentage", "regular_stop_loss_pct": 0.05, "regular_take_profit_pct": 0.1}
        with patch.object(engine.UserEngine, "_build_exit_protection_targets", return_value={"take_profit": 110.0, "stop_loss": 95.0, "qty": 1.2939}):
            status = eng._sync_exit_protection("SPY", {}, {"qty": 1.2939, "avg_price": 100.0}, settings)
        self.assertEqual(status["protection_type"], "watcher")
        self.assertIn("fractional position", status["status"])
        broker.place_equity_oco_exit.assert_not_called()
        broker.cancel_exit_orders.assert_not_called()


class StartupResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bots_resume_at_boot_without_waiting_for_the_user(self):
        db = MagicMock()
        db.collection.return_value.stream.return_value = [SimpleNamespace(id="with-bots"), SimpleNamespace(id="no-bots")]
        started = []

        async def fake_load(self, *a, **k):
            self.loaded_from_cloud = True
            self.config.TRADELIST = ["SPY"] if self.uid == "with-bots" else []
            return True

        async def fake_loop(self):
            started.append(self.uid)

        with patch("engine.create_broker", return_value=MagicMock()), \
                patch.object(engine.UserEngine, "load_from_cloud", fake_load), \
                patch.object(engine.UserEngine, "trading_loop", fake_loop):
            manager = engine.UserManager(db)
            await manager.start_all()
            await asyncio.sleep(0)
        self.assertEqual(started, ["with-bots"])


def broker_live():
    broker = alpaca_broker.AlpacaBroker()
    broker.simulation_mode, broker.paper_mode = False, True
    broker.client = MagicMock()
    broker.client.get_asset.return_value = SimpleNamespace(fractionable=True)
    return broker


def sdk_order(status, order_id="o1", qty=None, filled_qty=None, price=None):
    return SimpleNamespace(id=order_id, client_order_id="c1", status=status, qty=qty, filled_qty=filled_qty,
                           filled_avg_price=price, symbol="SPY")


def api_error(status_code, message="nope"):
    http_error = SimpleNamespace(response=SimpleNamespace(status_code=status_code), request=None)
    return APIError(f'{{"code": 1, "message": "{message}"}}', http_error=http_error)


class AlpacaAdapterTests(unittest.TestCase):
    def test_statuses_are_normalized_from_the_sdk_enum(self):
        broker = broker_live()
        broker.client.submit_order.return_value = sdk_order(OrderStatus.ACCEPTED)
        result = broker.place_order("SPY", 1000.0, "buy", client_order_id="c1")
        self.assertEqual(result["status"], "accepted")
        broker.client.submit_order.return_value = sdk_order(OrderStatus.FILLED, filled_qty="5", price="100.25")
        result = broker.place_order("SPY", 500.0, "buy", client_order_id="c2")
        self.assertEqual((result["status"], result["qty"], result["filled_avg_price"]), ("filled", 5.0, 100.25))
        self.assertEqual(alpaca_broker.order_status_value(OrderStatus.PARTIALLY_FILLED), "partially_filled")

    def test_get_order_by_id_and_by_client_id(self):
        broker = broker_live()
        broker.client.get_order_by_id.return_value = sdk_order(OrderStatus.FILLED, filled_qty=2, price=50)
        broker.client.get_order_by_client_id.return_value = sdk_order(OrderStatus.NEW)
        self.assertEqual(broker.get_order("o1")["status"], "filled")
        self.assertEqual(broker.get_order(client_order_id="c1")["status"], "new")
        broker.client.get_order_by_id.side_effect = api_error(404)
        self.assertIsNone(broker.get_order("missing"))

    def test_failures_are_classified(self):
        broker = broker_live()
        broker.client.get_order_by_client_id.side_effect = api_error(404)
        broker.client.submit_order.side_effect = api_error(403, "insufficient buying power")
        rejected = broker.place_order("SPY", 1000.0, "buy", client_order_id="c1")
        self.assertTrue(rejected["rejected"])
        self.assertFalse(rejected["success"])
        self.assertIn("insufficient buying power", rejected["error"])
        broker.client.submit_order.side_effect = TimeoutError("read timed out")
        unknown = broker.place_order("SPY", 1000.0, "buy", client_order_id="c1")
        self.assertTrue(unknown["indeterminate"])
        self.assertNotIn("rejected", unknown)

    def test_a_reused_client_id_returns_the_existing_order_or_asks_for_a_new_id(self):
        broker = broker_live()
        broker.client.submit_order.side_effect = api_error(422, "client_order_id must be unique")
        broker.client.get_order_by_client_id.return_value = sdk_order(OrderStatus.FILLED, filled_qty=5, price=100)
        existing = broker.place_order("SPY", 500.0, "buy", client_order_id="c1")
        self.assertTrue(existing["success"])
        self.assertTrue(existing["duplicate"])
        self.assertEqual(existing["status"], "filled")
        broker.client.get_order_by_client_id.return_value = sdk_order(OrderStatus.CANCELED)
        dead = broker.place_order("SPY", 500.0, "buy", client_order_id="c1")
        self.assertFalse(dead["success"])
        self.assertTrue(dead["retry_new_id"])

    def test_non_fractionable_assets_are_bought_in_whole_shares(self):
        broker = broker_live()
        broker.client.get_asset.return_value = SimpleNamespace(fractionable=False)
        broker.client.submit_order.return_value = sdk_order(OrderStatus.ACCEPTED)
        with patch.object(alpaca_broker, "in_regular_session", return_value=True):
            broker.place_order("XYZ", 1000.0, "buy", current_price=300.0, client_order_id="c1")
            request = broker.client.submit_order.call_args.kwargs["order_data"]
            self.assertIsInstance(request, MarketOrderRequest)
            self.assertEqual((request.qty, request.notional), (3, None))
            too_small = broker.place_order("XYZ", 100.0, "buy", current_price=300.0, client_order_id="c2")
        self.assertFalse(too_small["success"])
        self.assertIn("not fractionable", too_small["error"])

    def test_extended_session_orders_use_a_live_quote_and_only_outside_regular_hours(self):
        broker = broker_live()
        broker.client.submit_order.return_value = sdk_order(OrderStatus.ACCEPTED)
        broker.get_latest_quote = lambda symbol: {"bid": 99.0, "ask": 100.0}
        with patch.object(alpaca_broker, "in_regular_session", return_value=True):
            broker.place_order("SPY", 1000.0, "buy", market_session="Extended", current_price=90.0, client_order_id="c1")
        self.assertIsInstance(broker.client.submit_order.call_args.kwargs["order_data"], MarketOrderRequest)
        with patch.object(alpaca_broker, "in_regular_session", return_value=False):
            result = broker.place_order("SPY", 1000.0, "buy", market_session="Extended", current_price=90.0, client_order_id="c2")
            request = broker.client.submit_order.call_args.kwargs["order_data"]
            self.assertIsInstance(request, LimitOrderRequest)
            self.assertEqual(float(request.limit_price), 100.2)              # ask + 0.2%, not the stale $90 candle close
            self.assertEqual(request.qty, 9.980039)                          # fractional shares (rounded down) are accepted after hours with a limit
            self.assertTrue(request.extended_hours)
            broker.get_positions = lambda: [{"symbol": "SPY", "qty": 9.37, "avg_price": 95.0}]
            broker.close_position("SPY", market_session="Extended", current_price=120.0, client_order_id="c3")
            sell = broker.client.submit_order.call_args.kwargs["order_data"]
            self.assertEqual(float(sell.limit_price), 98.8)                  # bid - 0.2%
            self.assertEqual(sell.qty, 9.37)                                 # the whole fractional position, not just 9 shares
            self.assertIsInstance(sell, LimitOrderRequest)
            self.assertEqual(sell.order_class, None)                         # a simple limit order: Alpaca refuses advanced (OCO/bracket) orders after hours
        self.assertEqual(result["order_type"], "limit")


class AfterHoursOrderTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(alpaca_broker, "in_regular_session", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.broker = broker_live()
        self.broker.client.submit_order.return_value = sdk_order(OrderStatus.ACCEPTED)
        self.broker.get_latest_quote = lambda symbol: {"bid": 99.0, "ask": 100.0}

    def last_request(self):
        return self.broker.client.submit_order.call_args.kwargs["order_data"]

    def test_an_amount_below_the_share_price_buys_a_fraction_after_hours(self):
        result = self.broker.place_order("SPY", 50.0, "buy", market_session="Extended", client_order_id="c1")
        self.assertTrue(result["success"])
        request = self.last_request()
        self.assertEqual(request.qty, 0.499001)                              # $50 / $100.20, rounded down
        self.assertTrue(request.extended_hours)
        self.assertEqual(request.time_in_force.value, "day")

    def test_a_non_fractionable_stock_buys_whole_shares_after_hours(self):
        self.broker.client.get_asset.return_value = SimpleNamespace(fractionable=False)
        self.broker.place_order("XYZ", 1000.0, "buy", market_session="Extended", client_order_id="c1")
        self.assertEqual(self.last_request().qty, 9)
        too_small = self.broker.place_order("XYZ", 50.0, "buy", market_session="Extended", client_order_id="c2")
        self.assertFalse(too_small["success"])
        self.assertIn("not fractionable", too_small["error"])

    def test_a_regular_session_bot_never_sends_extended_hours_orders(self):
        self.broker.place_order("SPY", 1000.0, "buy", market_session="Regular", client_order_id="c1")
        self.assertIsInstance(self.last_request(), MarketOrderRequest)


class PartialCloseBookingTests(FastFillPolling):
    async def test_a_close_that_fills_only_part_of_the_position_is_not_a_completed_trade(self):
        settings = bot_settings(**{rt.OWNS_KEY: True, rt.STOP_KEY: {"stop_atr_mult": 3.0, "entry_atr": 2.0}})
        fills = {"s1": order_info("s1", "filled", 1.0, 93.5)}               # 1 of 1.29 shares
        eng, _ = await self.run_steps(
            [Step(preset_result("HOLD", price=99.0), positions=[position(1.29, 100.0, 93.5)])],
            settings=settings, configure_broker=self.broker_with(fills))
        settings = eng.config.TICKER_SETTINGS["SPY"]
        self.assertEqual(settings["realized_pl"], -6.5)
        self.assertNotIn("post_stop_state", settings)                       # not a completed stop-out yet
        self.assertEqual(int(settings.get("consecutive_losses", 0) or 0), 0)
        self.assertIn("PARTIALLY SOLD", eng.trade_log[0]["reason"])


class TenMinuteTimeframeTests(unittest.TestCase):
    def test_ten_minutes_is_understood_everywhere_a_bot_needs_it(self):
        import indicators
        self.assertEqual(engine.parse_timeframe_to_seconds("10Min"), 600)
        self.assertEqual(indicators._timeframe_seconds("10Min"), 600)
        self.assertIn("10Min", sp.PRESETS["golden_cross"].allowed_timeframes)   # signal strategies may run on 10 minutes (flagged untested)


if __name__ == "__main__":
    unittest.main()
