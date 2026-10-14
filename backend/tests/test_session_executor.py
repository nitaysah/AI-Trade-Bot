"""Session Rider executor against a stateful mock that behaves like Alpaca.

Orders come back ``accepted``; the fill is reported through get_order (auto_fill) or only when the test
says so. Positions and cash change only on fills. Isolated: no network, no Firestore."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402
import session_executor as X  # noqa: E402
from corporate_actions_service import SplitLookup  # noqa: E402

ET = ZoneInfo("America/New_York")
THU_BUY = datetime(2026, 9, 17, 15, 55, 5, tzinfo=ET)
FRI_SELL = datetime(2026, 9, 18, 9, 35, 5, tzinfo=ET)


class FakeAlpaca:
    simulation_mode = False
    order_owner_tag = "t"

    def __init__(self, price=100.0):
        self.price = price
        self.positions = {}
        self.orders = {}
        self.cash = 100_000.0
        self.auto_fill = True
        self.calls = []
        self.reject_next_sell = False
        self.daytrade_count = 0
        self.clock_open = True
        self.cancelled = []

    def _new(self, symbol, side, qty, cid):
        if cid in {o["client_order_id"] for o in self.orders.values()}:
            return {"success": False, "rejected": True, "error": "client_order_id must be unique"}
        oid = f"o{len(self.orders) + 1}"
        self.orders[oid] = {"order_id": oid, "client_order_id": cid, "status": "accepted", "filled_qty": 0.0,
                            "filled_avg_price": None, "qty": None, "symbol": symbol, "_side": side, "_qty": qty}
        self.calls.append((side, symbol, round(qty, 6), cid))
        return {"success": True, "order_id": oid, "client_order_id": cid, "status": "accepted", "qty": None}

    def place_order(self, symbol, notional, side="buy", stop_loss=0, take_profit=0, market_session="Regular",
                    current_price=None, client_order_id=None):
        return self._new(symbol, side, notional / self.price, client_order_id)

    def close_position(self, symbol, market_session="Regular", current_price=None, client_order_id=None):
        if self.reject_next_sell:
            self.reject_next_sell = False
            self.calls.append(("sell-rejected", symbol, 0, client_order_id))
            return {"success": False, "rejected": True, "error": "rejected by test"}
        pos = self.positions.get(symbol)
        if not pos:
            return {"success": False, "error": f"No position in {symbol}"}
        return self._new(symbol, "sell", pos["qty"], client_order_id)

    def fill(self, oid):
        o = self.orders[oid]
        if o["status"] != "accepted":
            return
        qty, sym = o["_qty"], o["symbol"]
        pos = self.positions.setdefault(sym, {"qty": 0.0, "avg": 0.0})
        if o["_side"] == "buy":
            pos["avg"] = (pos["avg"] * pos["qty"] + self.price * qty) / (pos["qty"] + qty)
            pos["qty"] += qty
            self.cash -= qty * self.price
        else:
            qty = min(qty, pos["qty"])
            pos["qty"] -= qty
            self.cash += qty * self.price
            if pos["qty"] <= 1e-9:
                self.positions.pop(sym)
        o.update(status="filled", filled_qty=qty, filled_avg_price=self.price)

    def get_order(self, order_id=None, client_order_id=None):
        o = self.orders.get(order_id) or next((x for x in self.orders.values() if x["client_order_id"] == client_order_id), None)
        if o is None:
            return None
        if self.auto_fill:
            self.fill(o["order_id"])
        return {k: v for k, v in o.items() if not k.startswith("_")}

    def get_positions(self):
        return [{"symbol": s, "qty": p["qty"], "avg_price": p["avg"], "current_price": self.price} for s, p in self.positions.items()]

    def get_open_orders(self):
        return [{"symbol": o["symbol"], "side": o["_side"]} for o in self.orders.values() if o["status"] == "accepted"]

    def get_account_info(self):
        mv = sum(p["qty"] * self.price for p in self.positions.values())
        return {"equity": self.cash + mv, "cash": self.cash, "buying_power": self.cash, "non_marginable_buying_power": self.cash,
                "daytrade_count": self.daytrade_count, "pattern_day_trader": False}

    def get_clock(self):
        return {"is_open": self.clock_open}

    def cancel_orders_for_ticker(self, symbol):
        self.cancelled.append(symbol)
        for o in self.orders.values():
            if o["symbol"] == symbol and o["status"] == "accepted":
                o["status"] = "canceled"
        return {"success": True}

    def cancel_exit_orders(self, symbol):
        return {"cancelled": 0}

    def _is_fractionable(self, symbol):
        return True

    def get_latest_quote(self, symbol):
        return {}


def bot(**kw):
    s = {"strategy_preset_enabled": True, "strategy_preset": "session_rider", "strategy_preset_variant": "scheduled",
         "session_buy_time": "close-5", "session_sell_time": "open+5", "amount": 1000.0, "allocation_mode": "fixed",
         "enable_risk_management": True, "max_position_pct": 1.0, "timeframe": "1Day", "market_session": "Regular",
         "buy_mode": "strategy_preset", "sell_mode": "strategy_preset", "strategy_preset_position_policy": "unmanaged",
         "session_state": {"armed_at": "2026-09-01T00:00:00-04:00"}}
    s.update(kw)
    return s


class Harness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in (("FILL_CONFIRM_SECONDS", 0.2), ("FILL_POLL_SECONDS", 0.01)):
            p = patch.object(engine, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.rm = MagicMock(trading_halted=False, max_position_pct=1.0)
        for target in ("trader.get_risk_manager",):
            p = patch(target, return_value=self.rm)
            p.start()
            self.addCleanup(p.stop)
        p = patch("engine.split_events_after", return_value=SplitLookup("ok", "alpaca", []))
        p.start()
        self.addCleanup(p.stop)
        self.quote_issue = None

    def make(self, settings=None, broker=None):
        broker = broker or FakeAlpaca()
        with patch("engine.create_broker", return_value=broker):
            eng = engine.UserEngine("session-test", MagicMock())
        eng.broker = broker
        eng.config.TRADELIST = ["SPY"]
        eng.config.TICKER_SETTINGS = {"SPY": settings or bot()}
        eng.save_settings, eng.save_history, eng.save_history_throttled = AsyncMock(), AsyncMock(), AsyncMock()
        return eng, broker

    async def cycle(self, eng, now):
        async def quote(engine_, symbol):
            if self.quote_issue:
                return {"symbol": symbol, "price": None, "sources": {}, "issue": self.quote_issue}
            return {"symbol": symbol, "price": eng.broker.price, "sources": {"webull": eng.broker.price}, "issue": None}

        with patch("allocation_executor._quote", side_effect=quote), \
                patch.object(engine.UserEngine, "get_now", lambda self: now):
            await X.run_cycle(eng, eng.broker.get_positions(), eng.broker.get_open_orders(), now=now)

    def settings(self, eng):
        return eng.config.TICKER_SETTINGS["SPY"]

    def events(self, eng):
        return [r.get("session_event") for r in eng.trade_log]


class EntryTests(Harness):
    async def test_buys_inside_the_window_and_books_the_fill(self):
        eng, broker = self.make()
        await self.cycle(eng, THU_BUY)
        self.assertEqual([c[0] for c in broker.calls], ["buy"])
        self.assertAlmostEqual(broker.positions["SPY"]["qty"], 10.0)
        t = self.settings(eng)
        self.assertEqual(t["session_state"]["entry_session"], "2026-09-17")
        self.assertTrue(t["strategy_preset_owns_position"])
        self.assertEqual(t.get("today_trades"), 1)
        self.assertIn("BUY", self.events(eng))

    async def test_no_second_buy_in_the_same_session(self):
        eng, broker = self.make()
        await self.cycle(eng, THU_BUY)
        await self.cycle(eng, THU_BUY + timedelta(minutes=2))
        self.assertEqual(len([c for c in broker.calls if c[0] == "buy"]), 1)

    async def test_restart_with_accepted_order_never_buys_twice(self):
        broker = FakeAlpaca()
        broker.auto_fill = False
        eng, _ = self.make(broker=broker)
        await self.cycle(eng, THU_BUY)
        self.assertIsInstance(self.settings(eng).get("pending_fill"), dict)
        saved = dict(self.settings(eng))
        # restart: a new engine with the saved settings; the order fills at the broker meanwhile
        broker.fill("o1")
        eng2, _ = self.make(settings=saved, broker=broker)
        await self.cycle(eng2, THU_BUY + timedelta(minutes=1))
        self.assertEqual(len([c for c in broker.calls if c[0] == "buy"]), 1)
        self.assertNotIn("pending_fill", self.settings(eng2))
        self.assertTrue(self.settings(eng2)["strategy_preset_owns_position"])

    async def test_window_open_at_save_is_not_used(self):
        eng, broker = self.make(bot(session_state={"armed_at": "2026-09-17T15:56:00-04:00"}))
        await self.cycle(eng, THU_BUY + timedelta(minutes=1, seconds=30))
        self.assertEqual(broker.calls, [])
        await self.cycle(eng, datetime(2026, 9, 18, 15, 55, 2, tzinfo=ET))  # next day's window
        self.assertEqual([c[0] for c in broker.calls], ["buy"])

    async def test_never_buys_late_and_logs_the_miss_once(self):
        eng, broker = self.make()
        late = datetime(2026, 9, 17, 15, 59, 40, tzinfo=ET)
        await self.cycle(eng, late)
        await self.cycle(eng, late + timedelta(seconds=10))
        self.assertEqual(broker.calls, [])
        self.assertEqual(self.events(eng).count("MISSED_ENTRY"), 1)

    async def test_halt_pause_stale_quote_and_closed_clock_skip_the_entry(self):
        for setup in ("halt", "pause", "quote", "clock"):
            eng, broker = self.make(bot(paused=setup == "pause"))
            self.rm.trading_halted = setup == "halt"
            self.quote_issue = "No current price for SPY." if setup == "quote" else None
            broker.clock_open = setup != "clock"
            await self.cycle(eng, THU_BUY)
            self.assertEqual(broker.calls, [], setup)
            self.assertIn("SKIPPED", self.events(eng), setup)
        self.rm.trading_halted = False
        self.quote_issue = None

    async def test_no_day_trade_limit_applies_to_intraday_buys(self):
        broker = FakeAlpaca()
        broker.cash = 10_000.0
        broker.daytrade_count = 3          # the old day-trade limit is gone: the broker's count no longer blocks a buy
        eng, _ = self.make(bot(session_buy_time="open+5", session_sell_time="close-5"), broker=broker)
        await self.cycle(eng, datetime(2026, 9, 17, 9, 35, 5, tzinfo=ET))
        self.assertEqual([c[0] for c in broker.calls], ["buy"])
        self.assertFalse(any("day-trade" in str(r.get("reason")) for r in eng.trade_log))

    async def test_position_you_hold_yourself_blocks_the_entry(self):
        broker = FakeAlpaca()
        broker.positions["SPY"] = {"qty": 3.0, "avg": 90.0}
        eng, _ = self.make(broker=broker)
        await self.cycle(eng, THU_BUY)
        self.assertEqual(broker.calls, [])
        self.assertTrue(any("outside this bot" in str(r.get("reason")) for r in eng.trade_log))


class ExitTests(Harness):
    async def bought(self):
        eng, broker = self.make()
        await self.cycle(eng, THU_BUY)
        return eng, broker

    async def test_sells_at_the_next_sessions_sell_time_and_books_pl(self):
        eng, broker = await self.bought()
        await self.cycle(eng, FRI_SELL - timedelta(minutes=10))
        self.assertEqual([c[0] for c in broker.calls], ["buy"])  # not yet
        broker.price = 102.0
        await self.cycle(eng, FRI_SELL)
        self.assertEqual([c[0] for c in broker.calls], ["buy", "sell"])
        self.assertNotIn("SPY", broker.positions)
        t = self.settings(eng)
        self.assertAlmostEqual(t["realized_pl"], 20.0, places=2)
        sell = next(r for r in eng.trade_log if r.get("session_event") == "SELL")
        self.assertAlmostEqual(sell["pl"], 20.0, places=2)

    async def test_missed_sell_is_done_late_after_a_restart(self):
        eng, broker = await self.bought()
        saved = dict(self.settings(eng))
        eng2, _ = self.make(settings=saved, broker=broker)
        await self.cycle(eng2, datetime(2026, 9, 18, 11, 0, tzinfo=ET))
        self.assertEqual([c[0] for c in broker.calls], ["buy", "sell"])
        self.assertIn("EXIT_LATE", self.events(eng2))

    async def test_halt_and_pause_never_block_the_exit(self):
        eng, broker = await self.bought()
        self.rm.trading_halted = True
        self.settings(eng)["paused"] = True
        await self.cycle(eng, FRI_SELL)
        self.rm.trading_halted = False
        self.assertEqual([c[0] for c in broker.calls], ["buy", "sell"])

    async def test_rejected_sell_is_retried_with_a_new_client_id(self):
        eng, broker = await self.bought()
        broker.reject_next_sell = True
        await self.cycle(eng, FRI_SELL)
        await self.cycle(eng, FRI_SELL + timedelta(minutes=1))
        rejected = next(c for c in broker.calls if c[0] == "sell-rejected")
        sold = next(c for c in broker.calls if c[0] == "sell")
        self.assertNotEqual(rejected[3], sold[3])
        self.assertIn("EXIT_RETRY", self.events(eng))
        self.assertNotIn("SPY", broker.positions)

    async def test_no_exit_before_the_open(self):
        eng, broker = await self.bought()
        await self.cycle(eng, datetime(2026, 9, 18, 9, 0, tzinfo=ET))
        self.assertEqual([c[0] for c in broker.calls], ["buy"])


class EngineHooks(Harness):
    async def test_wake_up_is_twenty_seconds_before_the_buy(self):
        eng, _ = self.make()
        wait = X.seconds_until_wake(eng, datetime(2026, 9, 17, 15, 50, tzinfo=ET))
        self.assertAlmostEqual(wait, 4 * 60 + 40, places=3)
        self.assertAlmostEqual(X.seconds_until_wake(eng, datetime(2026, 9, 17, 15, 54, 45, tzinfo=ET)), 15.0, places=3)

    def test_session_bots_are_recognized_and_options_are_not(self):
        self.assertTrue(X.is_session_bot(bot()))
        self.assertFalse(X.is_session_bot(bot(options_trading=True)))
        self.assertFalse(X.is_session_bot({"strategy_preset_enabled": True, "strategy_preset": "golden_cross"}))

    async def test_loss_pause_uses_the_bot_setting_never_below_three(self):
        eng, _ = self.make(bot(session_loss_pause_after=5))
        t = self.settings(eng)
        self.assertEqual(eng._loss_pause_threshold(t), 5)
        t["consecutive_losses"] = 4
        self.assertFalse(eng._pause_for_consecutive_losses("SPY", t))
        t["consecutive_losses"] = 5
        self.assertTrue(eng._pause_for_consecutive_losses("SPY", t))
        self.assertIn("5 consecutive", t["pause_reason"])
        self.assertEqual(eng._loss_pause_threshold(bot(session_loss_pause_after=1)), 3)
        self.assertEqual(eng._loss_pause_threshold({"strategy_preset_enabled": False}), 3)

    async def test_split_reconciliation_runs_for_the_bot(self):
        eng, _ = self.make()
        with patch.object(engine.UserEngine, "_reconcile_corporate_actions", AsyncMock()) as spy:
            await self.cycle(eng, THU_BUY)
        self.assertEqual(spy.await_args.args[0], "SPY")

    async def test_card_view_is_published(self):
        eng, _ = self.make()
        await self.cycle(eng, THU_BUY)
        record = eng.bot_scans["SPY"]
        self.assertEqual(record["strategy_preset"]["kind"], "schedule")
        self.assertEqual(record["strategy_preset"]["schedule"]["next_event"], "SELL")


if __name__ == "__main__":
    unittest.main()
