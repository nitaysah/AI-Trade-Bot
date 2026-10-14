"""Live executor for Session Rider bots (Bots page; Alpaca paper unless live trading is armed).

A Session Rider bot buys its own ticker at the Buy time and sells it at the Sell time
(rules in ``session_schedule``). The engine calls ``run_cycle`` on every pass, before the
per-ticker signal loop, which skips these bots.

Safety rules (AGENTS.md):
* Entries happen only inside a short window after the Buy time, only in a window that opened
  after the bot was saved, and never late. Halts, pauses, loss limits, a pending split, the
  a closed market or a stale quote skip the entry and say why.
* Exits are never skipped: once the Sell time has passed, a held position is sold as soon as
  the market is open, even when entries are paused or halted.
* Every order has a stable client order id (session + side) and the session marker is saved
  before submission, so a restart or timeout cannot buy twice. Accepted-but-unfilled orders
  are booked once by the engine's pending-fill reconciliation.
* There are no stop orders: the sell depends on this process running at the Sell time.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import nyse_calendar
import preset_runtime
import session_schedule as S
import strategy_presets as sp

STATE_KEY = "session_state"
PENDING_FILL_KEY = "pending_fill"
STATUS_REFRESH_SECONDS = 300
PRECISE_WAIT_MAX = timedelta(seconds=25)
EXIT_LATE_AFTER = timedelta(minutes=2)


def _f(value, default=0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def is_session_bot(t_settings: Optional[dict]) -> bool:
    return (isinstance(t_settings, dict) and sp.is_schedule_active(t_settings)
            and not t_settings.get("options_trading"))


def state_of(t_settings: dict) -> dict:
    st = t_settings.get(STATE_KEY)
    if not isinstance(st, dict):
        st = {}
        t_settings[STATE_KEY] = st
    return st


def _now_et(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(nyse_calendar.EASTERN)).astimezone(nyse_calendar.EASTERN)


def _parse(ts: Any) -> Optional[datetime]:
    try:
        out = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=nyse_calendar.EASTERN)


def _bots(engine) -> List[str]:
    return [t for t in dict.fromkeys(engine.config.TRADELIST) if is_session_bot(engine.config.TICKER_SETTINGS.get(t))]


def _event_times(t_settings: dict, now: datetime) -> List[datetime]:
    """The next buy and (when holding) sell times for one bot."""
    cfg = S.normalized_settings(t_settings)
    if cfg["mode"] is None:
        return []
    st = state_of(t_settings)
    out = []
    if t_settings.get(preset_runtime.OWNS_KEY):
        due = S.sell_due_at(st.get("entry_session"), now, cfg)
        if due is not None:
            out.append(due)
    ev = S.next_buy_event(now, cfg, include_open_window=False)
    if ev is not None:
        out.append(ev["at"])
    return out


def seconds_until_wake(engine, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds until the engine should wake for a Session Rider bot: 20s before an event, then at it."""
    now = _now_et(now)
    targets = []
    for ticker in _bots(engine):
        for at in _event_times(engine.config.TICKER_SETTINGS[ticker], now):
            targets += [t for t in (at - S.PREWAKE, at) if t > now]
    if not targets:
        return None
    return max(0.0, (min(targets) - now).total_seconds())


def _intent(engine, ticker: str, side: str, session: str) -> str:
    return engine._order_intent_id(ticker, side, {"candle_meta": {"candle_key": f"sr:{session}"}, "level": f"sr-{side}"})


def _intent_result(session: str, side: str) -> dict:
    return {"candle_meta": {"candle_key": f"sr:{session}"}, "level": f"sr-{side}"}


def _record(engine, ticker: str, action: str, reason: str, *, price: float = 0.0, qty: float = 0.0,
            event: Optional[str] = None) -> dict:
    return {
        "time": engine.get_now().isoformat(), "ticker": ticker, "action": action, "option_intent": "HOLD",
        "price_raw": float(price or 0.0), "price": f"${price:.2f}" if price else "$0.00",
        "qty": float(qty or 0.0), "total_cost": round(float(qty or 0.0) * float(price or 0.0), 2),
        "reason": reason, "timeframe": "1Day", "signals": {}, "bullish_count": 0, "bearish_count": 0,
        "total_signals": 0, "buy_mode": sp.PRESET_MODE, "sell_mode": sp.PRESET_MODE, "log_type": "Active Bot",
        "session_event": event or action,
    }


def _log(engine, ticker: str, action: str, reason: str, **kw) -> dict:
    record = _record(engine, ticker, action, reason, **kw)
    engine.trade_log.insert(0, record)
    if len(engine.trade_log) > 200:
        del engine.trade_log[200:]
    return record


def _log_once(engine, ticker: str, st: dict, key: str, action: str, reason: str) -> bool:
    """Log a skip/miss once per session and reason."""
    if st.get("last_note_key") == key:
        return False
    st["last_note_key"] = key
    st["last_note"] = reason
    _log(engine, ticker, action, reason, event=action)
    return True


def _status_record(engine, ticker: str, t_settings: dict, st: dict, now: datetime, *, holding: float = 0.0,
                   issue: Optional[str] = None, action: str = "HOLD") -> dict:
    cfg = S.normalized_settings(t_settings)
    preset = sp.get_preset(S.PRESET_ID)
    owns = bool(t_settings.get(preset_runtime.OWNS_KEY)) and holding > 0
    if owns:
        due = S.sell_due_at(st.get("entry_session"), now, cfg)
        phase = "EXIT_OVERDUE" if due and now >= due + EXIT_LATE_AFTER else (
            "HOLDING_INTRADAY" if cfg["mode"] == S.MODE_INTRADAY else "HOLDING_OVERNIGHT")
        next_event, next_at = "SELL", due
    else:
        ev = S.next_buy_event(now, cfg)
        phase, next_event, next_at = "FLAT", ("BUY" if ev else None), (ev["at"] if ev else None)
    info = {
        "id": S.PRESET_ID, "label": preset.label if preset else "Session Rider", "kind": S.KIND,
        "status": "INVALID_PRESET" if cfg["mode"] is None else "READY",
        "schedule": {
            "mode": cfg["mode"], "buy_time": cfg[S.BUY_KEY], "sell_time": cfg[S.SELL_KEY],
            "buy_label": S.et_ct_label(cfg[S.BUY_KEY]) if cfg[S.BUY_KEY] else None,
            "sell_label": S.et_ct_label(cfg[S.SELL_KEY]) if cfg[S.SELL_KEY] else None,
            "phase": phase, "next_event": next_event,
            "next_at": next_at.isoformat() if next_at else None, "next_label": S.fmt_et_ct(next_at),
            "holding_qty": round(holding, 6), "entry_session": st.get("entry_session"),
            "last_action": st.get("last_action"), "last_note": st.get("last_note"),
            "armed_at": st.get("armed_at"), "skip_weekend": cfg[S.SKIP_WEEKEND_KEY],
            "loss_pause_after": cfg[S.LOSS_PAUSE_KEY], "consecutive_losses": int(_f(t_settings.get("consecutive_losses"))),
            "issue": issue,
        },
    }
    if phase == "EXIT_OVERDUE":
        reason = f"Exit overdue: the sell time was {S.fmt_et_ct(next_at)}. Retrying every scan."
    elif owns:
        reason = f"Holding {holding:g} shares · sell {S.fmt_et_ct(next_at)}"
    elif next_at:
        reason = f"Flat · next buy {S.fmt_et_ct(next_at)}"
    else:
        reason = "Flat · no buy scheduled in the next days"
    if issue:
        reason = f"{reason} · {issue}"
    return {
        "time": engine.get_now().isoformat(), "ticker": ticker, "action": action, "option_intent": "HOLD",
        "price_raw": 0.0, "reason": reason, "summary": reason, "timeframe": "1Day", "signals": {},
        "bullish_count": 0, "bearish_count": 0, "total_signals": 0,
        "evaluation_status": "valid" if cfg["mode"] else "waiting",
        "strategy_preset": info, "is_option": False, "status": "DEGRADED" if issue else "OK",
    }


def _publish(engine, ticker: str, record: dict) -> None:
    engine.scan_log_state[ticker] = record
    engine.bot_scans[ticker] = record
    engine.bot_scans_by_tf.setdefault("1Day", {})[ticker] = record


def _market_open(now: datetime, *, for_entry: bool, clock: Optional[dict]) -> Optional[str]:
    """None when orders may be sent now; else why not."""
    bounds = nyse_calendar.session_bounds(now.date())
    if bounds is None:
        return "market closed today"
    open_et, close_et = bounds
    if now < open_et:
        return "market not open yet"
    if for_entry and now >= close_et - S.SUBMIT_CUTOFF:
        return "too close to the close to send a buy"
    if now >= close_et:
        return "market closed"
    if isinstance(clock, dict) and "is_open" in clock and not clock["is_open"]:
        return "the broker says the market is closed (unscheduled closure?)"
    return None


class _Ctx:
    """Per-cycle lazy broker reads (account, clock, positions)."""

    def __init__(self, engine):
        self.engine = engine
        self._account = None
        self._clock = None

    async def account(self) -> dict:
        if self._account is None:
            try:
                self._account = await asyncio.to_thread(self.engine.broker.get_account_info) or {}
            except Exception:
                self._account = {}
        return self._account

    async def clock(self) -> Optional[dict]:
        if self._clock is None:
            getter = getattr(self.engine.broker, "get_clock", None)
            try:
                self._clock = await asyncio.to_thread(getter) if callable(getter) else {}
            except Exception:
                self._clock = {}
        return self._clock


def _open_orders(orders: List[dict], ticker_norm: str) -> Dict[str, bool]:
    mine = [o for o in orders or [] if str(o.get("symbol", "")).replace("/", "").upper() == ticker_norm]
    return {"buy": any(str(o.get("side", "")).lower() == "buy" for o in mine),
            "sell": any(str(o.get("side", "")).lower() == "sell" for o in mine)}


async def _entry_block(engine, ticker: str, t_settings: dict, cfg: dict, now: datetime, ctx: _Ctx,
                       positions: List[dict], amount: float) -> Optional[str]:
    if t_settings.get("paused"):
        return f"bot is paused ({t_settings.get('pause_reason') or 'by you'})"
    if t_settings.get(preset_runtime.PAUSE_KEY):
        return "entries are paused while a stock split is reconciled"
    try:
        from trader import get_risk_manager
        if get_risk_manager().trading_halted:
            return "global risk halt is active"
    except Exception:
        return "risk state unavailable"
    loss_reason = engine._entry_loss_circuit_reason(t_settings, amount)
    if loss_reason:
        return loss_reason
    if t_settings.get("enable_risk_management", True):
        max_pos = getattr(engine.config, "MAX_OPEN_POSITIONS", 5)
        if len(positions or []) >= max_pos:
            return f"max open positions reached ({len(positions)}/{max_pos})"
    reason = _market_open(now, for_entry=True, clock=await ctx.clock())
    if reason:
        return reason
    account = await ctx.account()
    if account.get("error") or _f(account.get("equity")) <= 0:
        return "broker account data unavailable"
    return None


async def _submit_entry(engine, ticker: str, t_settings: dict, st: dict, ev: dict, now: datetime, ctx: _Ctx,
                        positions: List[dict]) -> bool:
    import allocation_executor
    cfg = S.normalized_settings(t_settings)
    amount = engine._allocation_amount(t_settings)
    block = await _entry_block(engine, ticker, t_settings, cfg, now, ctx, positions, amount)
    if block:
        _log_once(engine, ticker, st, f"{ev['session']}:skip:{block}", "SKIPPED", f"Buy skipped: {block}.")
        return True
    quote = await allocation_executor._quote(engine, ticker)
    if quote.get("issue") or not quote.get("price"):
        _log_once(engine, ticker, st, f"{ev['session']}:skip:quote", "SKIPPED",
                  f"Buy skipped for now: {quote.get('issue') or 'no fresh price'} Retrying inside the window.")
        return True
    price = float(quote["price"])
    account = await ctx.account()
    buying_power = _f(account.get("non_marginable_buying_power"), _f(account.get("cash")))
    try:
        from trader import get_risk_manager
        default_max = float(getattr(get_risk_manager(), "max_position_pct", 1.0))
    except Exception:
        default_max = 1.0
    max_frac = _f(t_settings.get("max_position_pct"), default_max) if t_settings.get("enable_risk_management", True) else 1.0
    fractional = True
    checker = getattr(engine.broker, "_is_fractionable", None)
    if callable(checker):
        try:
            fractional = bool(await asyncio.to_thread(checker, ticker))
        except Exception:
            fractional = True
    sizing = sp.preset_position_size(
        equity=_f(account.get("equity")), price=price, atr=0.0, base_amount=amount, risk_fraction=0.0,
        max_position_fraction=max_frac if 0 < max_frac <= 1 else 1.0,
        buying_power=buying_power * 0.98, fractional=fractional)
    notional = round(float(sizing["notional"]), 2)
    if notional < 1.0:
        _log_once(engine, ticker, st, f"{ev['session']}:skip:size", "SKIPPED",
                  f"Buy skipped: size is ${notional:.2f} (limited by {sizing['limit']}).")
        return True

    # The session marker is saved before the order is sent: a restart cannot buy twice.
    st["entry_session"] = ev["session"]
    st["entry_submitted_at"] = now.isoformat()
    st["last_action"] = f"BUY {ev['session']}"
    await engine.save_settings()
    cid = _intent(engine, ticker, "buy", ev["session"])
    res = await asyncio.to_thread(engine.broker.place_order, symbol=ticker, notional=notional, side="buy",
                                  market_session="Regular", current_price=price, client_order_id=cid)
    if not isinstance(res, dict) or not res.get("success"):
        engine._note_order_rejected(ticker, "buy", _intent_result(ev["session"], "buy"), res if isinstance(res, dict) else {})
        if isinstance(res, dict) and res.get("rejected"):
            st["entry_session"] = None  # nothing was created at the broker: a retry inside the window may try again
        _log(engine, ticker, "SKIPPED", f"Buy not placed: {(res or {}).get('error') if isinstance(res, dict) else 'no broker response'}.",
             price=price, event="SKIPPED")
        return True
    signal = {"strategy_preset": {"signal_key": f"sr:{ev['session']}:buy"}, "price_raw": price, "timeframe": "1Day",
              "reason": f"Session Rider scheduled buy ({S.et_ct_label(cfg[S.BUY_KEY])})"}
    preset_runtime.record_entry(t_settings, signal, engine.get_now().isoformat())
    res = await engine._confirm_fill(res)
    status = str(res.get("status") or "").lower()
    filled_qty = _f(res.get("filled_qty") or res.get("qty"))
    fill_price = _f(res.get("filled_avg_price"), price) or price
    record = _record(engine, ticker, "BUY", signal["reason"], price=fill_price, qty=filled_qty, event="BUY")
    record["order_id"] = res.get("order_id")
    if engine._order_result_is_filled(res):
        await engine._book_buy_fill(ticker, t_settings, record, filled_qty, fill_price, had_position=False)
        engine.trade_log.insert(0, record)
    elif status in ("canceled", "cancelled", "expired", "rejected") and filled_qty <= 0:
        st["entry_session"] = None
        _log(engine, ticker, "SKIPPED", f"Buy order {status} without a fill; nothing changed.", event="SKIPPED")
    else:
        engine._remember_pending_fill(t_settings, "buy", res, signal, position_qty=0.0, closes_position=False)
        _log(engine, ticker, "PENDING", f"Buy order accepted (${notional:.2f}); waiting for the fill.", price=price, event="ORDER_PENDING")
        await engine.save_settings()
    return True


async def _submit_exit(engine, ticker: str, t_settings: dict, st: dict, pos: dict, due: datetime, now: datetime,
                       ctx: _Ctx, orders: Dict[str, bool]) -> bool:
    reason = _market_open(now, for_entry=False, clock=await ctx.clock())
    if reason:
        _log_once(engine, ticker, st, f"exit:{due.date()}:{reason}", "HOLD", f"Sell due {S.fmt_et_ct(due)}: waiting ({reason}).")
        return False
    if orders["sell"]:
        return False  # a sell is already working at the broker
    if orders["buy"]:
        await asyncio.to_thread(engine.broker.cancel_orders_for_ticker, ticker)
    late = now - due > EXIT_LATE_AFTER
    session = due.astimezone(nyse_calendar.EASTERN).date().isoformat()
    qty = _f(pos.get("qty"))
    entry_price = _f(pos.get("avg_price"))
    price = _f(pos.get("current_price")) or None
    cid = _intent(engine, ticker, "sell", session)
    st["last_action"] = f"SELL {session}"
    res = await asyncio.to_thread(engine.broker.close_position, ticker, market_session="Regular",
                                  current_price=price, client_order_id=cid)
    if not isinstance(res, dict) or not res.get("success"):
        engine._note_order_rejected(ticker, "sell", _intent_result(session, "sell"), res if isinstance(res, dict) else {})
        st["exit_failures"] = int(_f(st.get("exit_failures"))) + 1
        _log(engine, ticker, "HOLD", f"Sell not placed ({(res or {}).get('error') if isinstance(res, dict) else 'no broker response'}); "
             "retrying on the next scan.", event="EXIT_RETRY")
        return True
    res = await engine._confirm_fill(res)
    status = str(res.get("status") or "").lower()
    filled_qty = _f(res.get("filled_qty") or res.get("qty"))
    fill_price = _f(res.get("filled_avg_price"), price or 0.0) or (price or 0.0)
    label = "EXIT_LATE" if late else "SELL"
    text = (f"Session Rider scheduled sell{' (late: the sell time was ' + S.fmt_et_ct(due) + ')' if late else ''}")
    record = _record(engine, ticker, "SELL", text, price=fill_price, qty=filled_qty, event=label)
    record["order_id"] = res.get("order_id")
    if engine._order_result_is_filled(res):
        await engine._book_sell_fill(ticker, t_settings, record, filled_qty, fill_price, entry_price=entry_price, closes_position=True)
        engine.trade_log.insert(0, record)
        st["exit_failures"] = 0
    elif status in ("canceled", "cancelled", "expired", "rejected") and filled_qty <= 0:
        _log(engine, ticker, "HOLD", f"Sell order {status} without a fill; retrying on the next scan.", event="EXIT_RETRY")
    else:
        engine._remember_pending_fill(t_settings, "sell", res, {"price_raw": fill_price, "reason": text, "timeframe": "1Day"},
                                      entry_price=entry_price, position_qty=qty, closes_position=True)
        _log(engine, ticker, "PENDING", "Sell order accepted; waiting for the fill.", price=fill_price, event="ORDER_PENDING")
        await engine.save_settings()
    return True


async def _process(engine, ticker: str, t_settings: dict, positions: List[dict], orders: List[dict],
                   now: datetime, ctx: _Ctx, force_status: bool) -> bool:
    st = state_of(t_settings)
    cfg = S.normalized_settings(t_settings)
    ticker_norm = ticker.upper().replace("/", "")
    changed = False
    if cfg["mode"] is None:
        _publish(engine, ticker, _status_record(engine, ticker, t_settings, st, now, issue="Buy and sell times are not valid."))
        return False
    if not st.get("armed_at"):
        st["armed_at"] = now.isoformat()  # first sight (e.g. an older bot): never enter a window that is already open
        changed = True

    if isinstance(t_settings.get(PENDING_FILL_KEY), dict):
        await engine._reconcile_pending_fill(ticker, t_settings)
        changed = True
        if isinstance(t_settings.get(PENDING_FILL_KEY), dict):
            pos = engine._find_position(positions, ticker_norm)
            _publish(engine, ticker, _status_record(engine, ticker, t_settings, st, now, holding=_f((pos or {}).get("qty")),
                                                    issue="Order pending at the broker."))
            return changed
        positions = await asyncio.to_thread(engine.broker.get_positions)

    pos = engine._find_position(positions, ticker_norm)
    qty = _f((pos or {}).get("qty"))
    has_pos = qty > 0
    open_orders = _open_orders(orders, ticker_norm)
    await engine._reconcile_corporate_actions(ticker, ticker_norm, t_settings, pos, has_pos)
    state = preset_runtime.sync_position_state(t_settings, has_pos, qty, has_open_buy_order=open_orders["buy"], now=engine.get_now())

    if state == preset_runtime.STATE_MANAGED:
        due = S.sell_due_at(st.get("entry_session"), now, cfg)
        if due is not None and now >= due:
            changed = await _submit_exit(engine, ticker, t_settings, st, pos, due, now, ctx, open_orders) or changed
        publish = changed or force_status
    else:
        ev = S.entry_due(now, cfg, _parse(st.get("armed_at")))
        today = S.buy_event(now.date(), cfg)
        if state == preset_runtime.STATE_UNMANAGED:
            if ev is not None:
                changed = _log_once(engine, ticker, st, f"{ev['session']}:skip:unmanaged", "SKIPPED",
                                    f"Buy skipped: you already hold {ticker} outside this bot; it only trades a position it opens.") or changed
        elif ev is not None and st.get("entry_session") != ev["session"] and not open_orders["buy"]:
            changed = await _submit_entry(engine, ticker, t_settings, st, ev, now, ctx, positions) or changed
        elif (today is not None and now >= today["window_end"] and st.get("entry_session") != today["session"]
              and (_parse(st.get("armed_at")) or now) <= today["at"]):
            changed = _log_once(engine, ticker, st, f"{today['session']}:missed", "MISSED_ENTRY",
                                f"No buy today: the buy window ({S.fmt_et_ct(today['at'])}) passed"
                                f"{' — ' + st['last_note'] if st.get('last_note') and str(st.get('last_note_key', '')).startswith(today['session']) else ''}.") or changed
        publish = changed or force_status
    last_status = _f(st.get("last_status_ts"))
    if publish or now.timestamp() - last_status >= STATUS_REFRESH_SECONDS:
        st["last_status_ts"] = now.timestamp()
        if changed:  # an order may have filled since the cycle's snapshot
            try:
                positions = await asyncio.to_thread(engine.broker.get_positions)
            except Exception:
                pass
        fresh = engine._find_position(positions, ticker_norm)
        _publish(engine, ticker, _status_record(engine, ticker, t_settings, st, now, holding=_f((fresh or {}).get("qty")),
                                                action="BUY" if str(st.get("last_action", "")).startswith("BUY") and changed else "HOLD"))
    return changed


async def run_cycle(engine, positions: List[dict], orders: Optional[List[dict]] = None,
                    now: Optional[datetime] = None) -> None:
    """Process every active Session Rider bot once. Called by the engine loop on each pass."""
    tickers = _bots(engine)
    if not tickers:
        return
    explicit_now = now is not None
    now = _now_et(now)
    if not explicit_now:
        # Woken 20s early: wait for the exact event time so the order goes out at T, not up to a cycle later.
        upcoming = [at for t in tickers for at in _event_times(engine.config.TICKER_SETTINGS[t], now) if at > now]
        if upcoming and min(upcoming) - now <= PRECISE_WAIT_MAX:
            await asyncio.sleep((min(upcoming) - now).total_seconds())
            now = _now_et()
            positions = await asyncio.to_thread(engine.broker.get_positions)
            orders = await asyncio.to_thread(engine.broker.get_open_orders)
    ctx = _Ctx(engine)
    changed = False
    for ticker in tickers:
        t = engine.config.TICKER_SETTINGS[ticker]
        forced = engine.force_scan_tickers.pop(ticker.upper(), None) is not None if hasattr(engine, "force_scan_tickers") else False
        try:
            changed = await _process(engine, ticker, t, positions, orders or [], now, ctx, forced) or changed
        except Exception as exc:
            print(f"[session-{engine.uid}] {ticker} cycle error: {type(exc).__name__}: {exc}")
    if changed:
        await engine.save_settings()
        await engine.save_history_throttled(force=True)
