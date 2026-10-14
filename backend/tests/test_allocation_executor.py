"""Malik allocation executor against a stateful mock that behaves like Alpaca.

Orders come back ``accepted``; the fill is reported a moment later through get_order (or only when the
test says so). Positions and cash change only on fills."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import allocation_executor as X  # noqa: E402
import engine  # noqa: E402
import test_engine_strategy_preset as harness  # noqa: E402
from test_engine_strategy_preset import Step  # noqa: E402

ET = ZoneInfo("America/New_York")
THU = datetime(2026, 10, 8, 15, 46, tzinfo=ET)  # just after the 15:45 decision time


class FakeAlpaca:
    simulation_mode = False
    order_owner_tag = "t"

    def __init__(self, prices):
        self.prices = dict(prices)
        self.positions = {}
        self.orders = {}
        self.cash = 100_000.0
        self.auto_fill = True
        self.calls = []
        self.halted = False

    def _new(self, symbol, side, qty, cid):
        oid = f"o{len(self.orders) + 1}"
        self.orders[oid] = {"order_id": oid, "client_order_id": cid, "status": "accepted", "filled_qty": 0.0,
                            "filled_avg_price": None, "qty": None, "symbol": symbol, "_side": side, "_qty": qty}
        self.calls.append((side, symbol, round(qty, 6), cid))
        return {"success": True, "order_id": oid, "client_order_id": cid, "status": "accepted", "qty": None}

    def place_order(self, symbol, notional, side="buy", stop_loss=0, take_profit=0, market_session="Regular",
                    current_price=None, client_order_id=None):
        return self._new(symbol, side, notional / self.prices[symbol], client_order_id)

    def close_position(self, symbol, market_session="Regular", current_price=None, client_order_id=None):
        pos = self.positions.get(symbol)
        if not pos:
            return {"success": False, "error": f"No position in {symbol}"}
        return self._new(symbol, "sell", pos["qty"], client_order_id)

    def fill(self, oid):
        o = self.orders[oid]
        if o["status"] != "accepted":
            return
        px, qty, sym = self.prices[o["symbol"]], o["_qty"], o["symbol"]
        pos = self.positions.setdefault(sym, {"qty": 0.0, "avg": 0.0})
        if o["_side"] == "buy":
            pos["avg"] = (pos["avg"] * pos["qty"] + px * qty) / (pos["qty"] + qty)
            pos["qty"] += qty
            self.cash -= qty * px
        else:
            qty = min(qty, pos["qty"])
            pos["qty"] -= qty
            self.cash += qty * px
            if pos["qty"] <= 1e-9:
                self.positions.pop(sym)
        o.update(status="filled", filled_qty=qty, filled_avg_price=px)

    def get_order(self, order_id=None, client_order_id=None):
        o = self.orders.get(order_id) or next((x for x in self.orders.values() if x["client_order_id"] == client_order_id), None)
        if o is None:
            return None
        if self.auto_fill:
            self.fill(o["order_id"])
        return {k: v for k, v in o.items() if not k.startswith("_")}

    def get_positions(self):
        return [{"symbol": s, "qty": p["qty"], "avg_price": p["avg"], "current_price": self.prices[s]} for s, p in self.positions.items()]

    def get_account_info(self):
        mv = sum(p["qty"] * self.prices[s] for s, p in self.positions.items())
        return {"equity": self.cash + mv, "cash": self.cash, "buying_power": self.cash, "non_marginable_buying_power": self.cash}

    def get_latest_quote(self, symbol):
        return {}


def bot(**kw):
    s = {"strategy_preset_enabled": True, "strategy_preset": "malik_qqq", "strategy_preset_variant": "long_only",
         "allocation_bull_etf": "TQQQ", "allocation_bear_etf": "SQQQ", "allocation_exec_time": "close-15",
         "amount": 1000.0, "allocation_mode": "fixed", "enable_risk_management": True, "max_position_pct": 1.0,
         "timeframe": "1Day", "market_session": "Regular"}
    s.update(kw)
    return s


UP = [100 + i * 0.2 for i in range(300)]  # steady uptrend: Bull rider at 160+
DOWN = [160 - i * 0.2 for i in range(300)]  # downtrend: bear regimes


class Harness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in (("FILL_CONFIRM_SECONDS", 0.2), ("FILL_POLL_SECONDS", 0.01)):
            p = patch.object(engine, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.rm = MagicMock(trading_halted=False, max_position_pct=1.0)
        p = patch("trader.get_risk_manager", return_value=self.rm)
        p.start()
        self.addCleanup(p.stop)
        self.closes = UP
        self.signal_price = 160.0
        self.data_issue = None

    def make(self, settings=None, prices=None):
        broker = FakeAlpaca(prices or {"TQQQ": 50.0, "SQQQ": 20.0, "QLD": 80.0, "QID": 30.0})
        with patch("engine.create_broker", return_value=broker):
            eng = engine.UserEngine("alloc-test", MagicMock())
        eng.broker = broker
        eng.config.TRADELIST = ["QQQ"]
        eng.config.TICKER_SETTINGS = {"QQQ": settings or bot()}
        eng.save_settings, eng.save_history, eng.save_history_throttled = AsyncMock(), AsyncMock(), AsyncMock()
        return eng, broker

    async def cycle(self, eng, now):
        async def closes(engine_, ticker, t, today):
            if self.data_issue:
                return {"issue": self.data_issue}
            return {"closes": list(self.closes), "last_day": (today - timedelta(days=1)).isoformat(), "analysis": {}}

        async def quote(engine_, symbol):
            price = self.signal_price if symbol == "QQQ" else eng.broker.prices.get(symbol)
            return {"symbol": symbol, "price": price, "sources": {"webull": price}, "issue": None if price else f"No current price for {symbol}."}

        with patch.object(X, "_completed_closes", side_effect=closes), patch.object(X, "_quote", side_effect=quote), \
                patch.object(X, "_status_info", AsyncMock(return_value={"status": "READY", "reason": "test"})), \
                patch.object(engine.UserEngine, "get_now", lambda self: now):
            await X.run_cycle(eng, eng.broker.get_positions(), now)

    def st(self, eng):
        return eng.config.TICKER_SETTINGS["QQQ"][X.STATE_KEY]


class DecisionTests(Harness):
    async def test_buys_the_bull_leg_once_at_the_decision_time_and_never_the_benchmark(self):
        eng, broker = self.make()
        await self.cycle(eng, THU - timedelta(minutes=10))  # before 15:45: nothing
        self.assertEqual(broker.calls, [])
        await self.cycle(eng, THU)
        self.assertEqual([(c[0], c[1]) for c in broker.calls], [("buy", "TQQQ")])
        self.assertAlmostEqual(broker.positions["TQQQ"]["qty"], 1000 / 50.0)
        self.assertAlmostEqual(self.st(eng)["legs"]["TQQQ"]["qty"], 20.0)
        self.assertEqual(self.st(eng)["decision"]["regime"], "BULL_RIDER")
        self.assertTrue(broker.calls[0][3].startswith("bb-"))
        await self.cycle(eng, THU + timedelta(minutes=2))  # same day again: no second decision
        self.assertEqual(len(broker.calls), 1)
        self.assertNotIn("QQQ", {c[1] for c in broker.calls})
        actions = [r["action"] for r in eng.trade_log]
        self.assertIn("REBALANCE", actions)
        self.assertIn("BUY", actions)

    async def test_next_day_cash_regime_sells_everything_and_books_pl(self):
        eng, broker = self.make()
        await self.cycle(eng, THU)
        broker.prices["TQQQ"] = 55.0
        self.closes, self.signal_price = DOWN, 100.0
        await self.cycle(eng, THU + timedelta(days=1))  # Friday 15:46
        self.assertEqual(broker.calls[-1][:2], ("sell", "TQQQ"))
        self.assertNotIn("TQQQ", broker.positions)
        self.assertEqual(self.st(eng)["legs"], {})
        self.assertAlmostEqual(self.st(eng)["realized_pl"], 20 * 5.0)

    async def test_inverse_switch_sells_first_and_waits_for_the_fill_before_buying(self):
        eng, broker = self.make(bot(strategy_preset_variant="long_inverse"))
        await self.cycle(eng, THU)
        broker.auto_fill = False  # the sell is accepted but not filled yet
        self.closes, self.signal_price = DOWN, 100.0
        friday = THU + timedelta(days=1)
        await self.cycle(eng, friday)
        self.assertEqual([c[:2] for c in broker.calls[1:]], [("sell", "TQQQ")])  # no buy while the sell is working
        self.assertIn("TQQQ", self.st(eng)["pending"])
        broker.auto_fill = True
        await self.cycle(eng, friday + timedelta(minutes=1))
        self.assertEqual([c[:2] for c in broker.calls[1:]], [("sell", "TQQQ"), ("buy", "SQQQ")])
        self.assertEqual(len([c for c in broker.calls if c[0] == "sell"]), 1)  # never resubmitted
        self.assertAlmostEqual(self.st(eng)["legs"]["SQQQ"]["qty"] * 20.0, 600.0, places=2)  # 60% of $1,000

    async def test_halt_blocks_increases_but_reductions_run(self):
        eng, broker = self.make()
        await self.cycle(eng, THU)
        self.rm.trading_halted = True
        self.closes, self.signal_price = DOWN, 100.0
        eng.config.TICKER_SETTINGS["QQQ"]["strategy_preset_variant"] = "long_inverse"
        await self.cycle(eng, THU + timedelta(days=1))
        self.assertEqual([c[:2] for c in broker.calls], [("buy", "TQQQ"), ("sell", "TQQQ")])
        self.assertTrue(any("Increases blocked" in r["reason"] for r in eng.trade_log))

    async def test_stale_data_holds_and_logs_a_missed_day_after_the_cutoff(self):
        eng, broker = self.make()
        self.data_issue = "Daily closes are stale: latest completed 2026-10-06, expected 2026-10-07."
        await self.cycle(eng, THU)
        await self.cycle(eng, THU + timedelta(minutes=1))
        self.assertEqual(broker.calls, [])
        self.assertEqual(eng.bot_scans["QQQ"]["status"], "DEGRADED")
        await self.cycle(eng, THU.replace(hour=15, minute=56))
        self.assertTrue(any(r["reason"].startswith("No rebalance today: Daily closes are stale") for r in eng.trade_log))

    async def test_inside_the_band_nothing_trades(self):
        eng, broker = self.make()
        await self.cycle(eng, THU)
        broker.prices["TQQQ"] = 51.0  # +2% drift < 5% band
        await self.cycle(eng, THU + timedelta(days=1))
        self.assertEqual(len(broker.calls), 1)


class SafetyTests(Harness):
    async def test_restart_with_a_pending_leg_reconciles_instead_of_resubmitting(self):
        eng, broker = self.make()
        broker.auto_fill = False
        await self.cycle(eng, THU)
        self.assertIn("TQQQ", self.st(eng)["pending"])
        saved = eng.config.TICKER_SETTINGS["QQQ"]
        eng2, _ = self.make(saved)  # "restart": a new engine with the persisted settings and the same broker
        eng2.broker = broker
        broker.auto_fill = True
        await self.cycle(eng2, THU + timedelta(minutes=3))
        self.assertEqual(len(broker.calls), 1)
        self.assertEqual(self.st(eng2)["pending"], {})
        self.assertAlmostEqual(self.st(eng2)["legs"]["TQQQ"]["qty"], 20.0)

    async def test_ledger_follows_the_broker_down_and_ignores_your_extra_shares(self):
        eng, broker = self.make()
        await self.cycle(eng, THU)
        broker.positions["TQQQ"]["qty"] = 25.0  # you bought 5 more yourself
        self.closes, self.signal_price = DOWN, 100.0
        await self.cycle(eng, THU + timedelta(days=1))
        self.assertEqual(broker.calls[-1][:3], ("sell", "TQQQ", 20.0))  # only the bot's 20, by notional, not close_position
        self.assertAlmostEqual(broker.positions["TQQQ"]["qty"], 5.0)
        self.closes, self.signal_price = UP, 160.0
        eng3, broker3 = self.make()
        await self.cycle(eng3, THU)
        broker3.positions["TQQQ"]["qty"] = 10.0  # you sold half
        settings3 = eng3.config.TICKER_SETTINGS["QQQ"]
        X.reconcile_ledger(settings3, broker3.get_positions(), "now", now_ts=1000.0)
        self.assertAlmostEqual(self.st(eng3)["legs"]["TQQQ"]["qty"], 20.0)  # one snapshot is not enough
        X.reconcile_ledger(settings3, broker3.get_positions(), "now", now_ts=1061.0)
        self.assertAlmostEqual(self.st(eng3)["legs"]["TQQQ"]["qty"], 10.0)  # still short a minute later: follow the broker

    async def test_a_leg_bought_today_can_be_sold_today(self):
        eng, broker = self.make()
        settings = eng.config.TICKER_SETTINGS["QQQ"]
        X.state_of(settings)["legs"] = {"TQQQ": {"qty": 20.0, "avg": 50.0, "last_buy_day": THU.date().isoformat()}}
        broker.positions["TQQQ"] = {"qty": 20.0, "avg": 50.0}
        self.closes, self.signal_price = DOWN, 100.0
        await self.cycle(eng, THU)
        self.assertEqual(broker.calls[-1][:2], ("sell", "TQQQ"))       # no day-trade guard any more
        self.assertFalse(any("day-trade" in r["reason"] for r in eng.trade_log))

    async def test_remove_with_liquidate_closes_the_legs(self):
        eng, broker = self.make()
        await self.cycle(eng, THU)
        with patch.object(engine.UserEngine, "get_now", lambda self: THU):
            res = await X.liquidate_legs(eng, "QQQ", eng.config.TICKER_SETTINGS["QQQ"])
        self.assertTrue(res["success"])
        self.assertEqual(broker.calls[-1][:2], ("sell", "TQQQ"))
        self.assertEqual(broker.positions, {})

    async def test_sleep_is_capped_by_the_next_decision(self):
        eng, broker = self.make()
        wait = X.seconds_until_next_decision(eng, THU.replace(hour=15, minute=40))
        self.assertAlmostEqual(wait, 300.0, places=0)


class EngineLoopTests(unittest.IsolatedAsyncioTestCase):
    run_steps = harness.PresetLoopTests.run_steps
    settings_of = harness.PresetLoopTests.settings_of

    async def test_the_engine_hands_allocation_bots_to_the_executor_and_never_evaluates_their_ticker(self):
        settings = {**bot(), "strategy_preset": "malik_qqq"}
        with patch.object(engine.allocation_executor, "run_cycle", AsyncMock()) as run_cycle:
            eng, broker = await self.run_steps([Step(harness.preset_result("BUY", key=harness.SIGNAL_A))], settings=settings)
        self.assertTrue(run_cycle.await_count >= 1)
        self.assertEqual(eng.evals, [])
        broker.place_order.assert_not_called()
        broker.close_position.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class CardViewTests(Harness):
    async def test_scan_log_shows_regime_targets_holdings_and_next_decision(self):
        import strategy_scan_log
        eng, broker = self.make()
        await self.cycle(eng, THU)
        settings = eng.config.TICKER_SETTINGS["QQQ"]
        record = X._status_record(eng, "QQQ", settings, self.st(eng),
                                  {"status": "READY", "regime": "BULL_RIDER", "regime_label": "Bull rider",
                                   "targets": [{"symbol": "TQQQ", "pct": 100}], "values": {"price": 160.0, "sma_slow": 130.0,
                                                                                         "sma_fast": 155.0, "roc10": 1.2}},
                                  THU, {"TQQQ": 50.0})
        view = strategy_scan_log.scan_view(settings, record, None, None, [])
        self.assertEqual(view["kind"], "allocation")
        self.assertIn("Did not pass", view["untested"])
        self.assertEqual(view["allocation"]["legs"][0]["symbol"], "TQQQ")
        self.assertEqual(view["allocation"]["legs"][0]["target_pct"], 100)
        self.assertAlmostEqual(view["allocation"]["legs"][0]["held_pct"], 100.0)
        self.assertIn("CT", view["allocation"]["next_decision"])
        self.assertNotIn("ET", view["allocation"]["next_decision"])
        rows = [strategy_scan_log.row_view(settings, r) for r in eng.trade_log]
        self.assertIn("REBALANCE", [r["event"] for r in rows if r])
        self.assertIn("BUY TQQQ", [r["event"] for r in rows if r])
