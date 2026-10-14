"""Live executor for Malik allocation bots (Bots page; Alpaca paper unless live trading is armed).

Once per trading day, at the bot's execution time, it reads the benchmark's completed daily
closes (Webull) plus the current price, decides the regime with ``allocation_strategy`` and
rebalances the chosen ETF legs. Sells go first for every allocation bot, buys only after the
sells have filled. It never places an order for the bot's own ticker (the benchmark).

Safety rules (AGENTS.md):
* Missing, stale or inconsistent data holds the current allocation; nothing is liquidated.
* A global halt, a paused bot or a hit bot loss limit block increases; reductions still run.
* Every order has a stable client order id; accepted-but-unfilled orders are reconciled with the
  broker before anything else happens for that leg; nothing is blindly resubmitted.
* The bot only trades the quantity it owns (its ledger); extra shares you hold are left alone.
* There are no stop orders: the strategy depends on this process running at the decision time.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import allocation_strategy as A
import nyse_calendar
import strategy_presets as sp

STATE_KEY = "allocation_state"
STATUS_REFRESH_SECONDS = 300
ORDER_CUTOFF = timedelta(minutes=5)  # no new orders in the last 5 minutes of the session
QUOTE_MAX_AGE_SECONDS = 120
QUOTE_MAX_DIVERGENCE = 0.005
EPS = 1e-6


def _f(value, default=0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def is_allocation_bot(t_settings: Optional[dict]) -> bool:
    t = t_settings or {}
    return bool(sp.is_allocation_active(t)) and not (t.get("options_trading") or t.get("is_option"))


def state_of(t_settings: dict) -> dict:
    st = t_settings.get(STATE_KEY)
    if not isinstance(st, dict):
        st = {}
        t_settings[STATE_KEY] = st
    st.setdefault("legs", {})
    st.setdefault("pending", {})
    st.setdefault("realized_pl", 0.0)
    return st


def _now_et(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(nyse_calendar.EASTERN)).astimezone(nyse_calendar.EASTERN)


def previous_trading_day(day: date) -> date:
    d = day - timedelta(days=1)
    while not nyse_calendar.is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_decision(t_settings: dict, now: datetime) -> Optional[datetime]:
    """Next decision time (ET-aware) at or after ``now`` that has not been decided yet."""
    st = state_of(t_settings)
    exec_time = A.normalized_settings(t_settings.get("strategy_preset"), t_settings)["allocation_exec_time"]
    day = now.date()
    for _ in range(10):
        dt = A.decision_time(day, exec_time)
        if dt is not None and st.get("decided_day") != day.isoformat() and now < dt["close"] - ORDER_CUTOFF:
            return max(dt["at"], now) if dt["at"] <= now else dt["at"]
        day = day + timedelta(days=1)
    return None


def seconds_until_next_decision(engine, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds until the earliest allocation decision; the engine caps its loop sleep with this."""
    now = _now_et(now)
    waits = []
    for ticker in list(dict.fromkeys(engine.config.TRADELIST)):
        t = engine.config.TICKER_SETTINGS.get(ticker) or {}
        if not is_allocation_bot(t):
            continue
        at = next_decision(t, now)
        if at is None:
            continue
        st = state_of(t)
        wait = max(0.0, (at - now).total_seconds())
        if wait <= 0:
            # Due now but not decided (data issue): retry at most every 30 seconds.
            wait = max(0.0, 30.0 - (now.timestamp() - _f(st.get("last_attempt_at"))))
        if st.get("pending"):
            wait = min(wait, 10.0)
        waits.append(wait)
    return min(waits) if waits else None


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def _position(positions: List[dict], symbol: str) -> Optional[dict]:
    sym = symbol.upper()
    return next((p for p in positions or [] if str(p.get("symbol", "")).replace("/", "").upper() == sym), None)


LEDGER_CONFIRM_SECONDS = 60


def reconcile_ledger(t_settings: dict, positions: List[dict], now_iso: str, skip: Optional[set] = None,
                     now_ts: Optional[float] = None) -> List[str]:
    """Match the bot's owned quantities with the broker. Returns human-readable notes.

    * Broker holds less than the ledger (manual sale, reverse split): the ledger follows the broker,
      using the broker's average price (Alpaca adjusts it for splits).
    * Broker holds more: the extra is yours; with the "adopt" policy and an empty ledger the bot
      takes it over once, otherwise it leaves it alone."""
    st = state_of(t_settings)
    notes = []
    policy = str(t_settings.get("strategy_preset_position_policy") or "unmanaged").lower()
    legs = A.resolve_legs(t_settings.get("strategy_preset"), t_settings)
    symbols = set(st["legs"]) | {s for s in legs.values() if s}
    seen = st.setdefault("ledger_mismatch", {})
    for sym in symbols:
        if sym in st["pending"] or sym in (skip or set()):
            seen.pop(sym, None)
            continue  # an order in flight or just booked: the positions snapshot may predate it
        leg = st["legs"].get(sym) or {}
        owned = _f(leg.get("qty"))
        pos = _position(positions, sym)
        broker_qty = _f(pos.get("qty")) if pos else 0.0
        if owned > broker_qty + EPS:
            # Act only when the shortfall is still there on a later pass (snapshots can lag a fill).
            first = seen.get(sym)
            if now_ts is not None and (first is None or now_ts - _f(first) < LEDGER_CONFIRM_SECONDS):
                seen.setdefault(sym, now_ts)
                continue
            seen.pop(sym, None)
            if broker_qty <= EPS:
                notes.append(f"{sym}: the broker shows no position; the bot's {owned:.6f} shares are cleared.")
                st["legs"].pop(sym, None)
            else:
                notes.append(f"{sym}: the broker holds {broker_qty:.6f}, less than the bot's {owned:.6f}; following the broker.")
                st["legs"][sym] = {**leg, "qty": broker_qty, "avg": _f(pos.get("avg_price"), _f(leg.get("avg")))}
        elif broker_qty > owned + EPS and owned <= EPS and policy == "adopt" and not st.get("adopted", {}).get(sym):
            seen.pop(sym, None)
            st["legs"][sym] = {"qty": broker_qty, "avg": _f(pos.get("avg_price")), "opened_at": now_iso, "adopted": True}
            st.setdefault("adopted", {})[sym] = True
            notes.append(f"{sym}: adopted the existing {broker_qty:.6f} shares (position policy: adopt).")
    return notes


def _leg_values(st: dict, prices: Dict[str, float]) -> Dict[str, float]:
    return {s: _f(l.get("qty")) * _f(prices.get(s)) for s, l in st["legs"].items() if _f(l.get("qty")) > EPS}


def sleeve_for(engine, t_settings: dict, st: dict, prices: Dict[str, float]) -> float:
    base = engine._allocation_amount(t_settings, "regular")
    mv = sum(_leg_values(st, prices).values())
    cost = sum(_f(l.get("qty")) * _f(l.get("avg")) for l in st["legs"].values())
    # compound_amount already holds amount + realized P/L (engine._apply_compound_pl on each sell)
    return A.sleeve_value(t_settings.get("allocation_mode", "fixed"), base, 0.0, mv, cost)


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

async def _quote(engine, symbol: str) -> Dict[str, Any]:
    """Fresh price from the app's quote helper (Webull first), cross-checked with Alpaca."""
    out = {"symbol": symbol, "price": None, "sources": {}, "issue": None}
    try:
        from main import _get_cached_quote_detail
        detail = await asyncio.to_thread(_get_cached_quote_detail, engine, symbol, 15, 0)
    except Exception as exc:
        detail = {"price": None, "source": f"error:{type(exc).__name__}"}
    primary = _f(detail.get("price"), 0.0) if not detail.get("stale") else 0.0
    if primary > 0 and _f(detail.get("cache_age"), 0.0) <= QUOTE_MAX_AGE_SECONDS:
        out["sources"][str(detail.get("source") or "quote")] = primary
    try:
        q = await asyncio.to_thread(engine.broker.get_latest_quote, symbol) if hasattr(engine.broker, "get_latest_quote") else {}
    except Exception:
        q = {}
    bid, ask = _f((q or {}).get("bid")), _f((q or {}).get("ask"))
    ts = (q or {}).get("timestamp")
    try:
        age = (datetime.now(nyse_calendar.EASTERN) - ts.astimezone(nyse_calendar.EASTERN)).total_seconds() if ts else None
    except Exception:
        age = None
    if age is not None and age > QUOTE_MAX_AGE_SECONDS:
        bid = ask = 0.0  # a stale IEX quote is not a cross-check
    if bid > 0 and ask > 0 and ask >= bid and (ask - bid) / ((ask + bid) / 2) < 0.02:
        out["sources"]["alpaca"] = (ask + bid) / 2
    prices = list(out["sources"].values())
    if not prices:
        out["issue"] = f"No current price for {symbol}."
        return out
    if len(prices) > 1 and abs(prices[0] / prices[1] - 1) > QUOTE_MAX_DIVERGENCE:
        out["issue"] = f"{symbol} price sources disagree by more than {QUOTE_MAX_DIVERGENCE * 100:.1f}% ({', '.join(f'{k} {v:.2f}' for k, v in out['sources'].items())})."
        return out
    out["price"] = prices[0]
    return out


async def _completed_closes(engine, ticker: str, t_settings: dict, today: date) -> Dict[str, Any]:
    """Completed daily closes (Webull) through the previous trading day, or an issue."""
    import trader
    from indicators import get_full_analysis
    settings = trader._analysis_settings_for_trade(None, t_settings, True)
    try:
        analysis = await asyncio.to_thread(get_full_analysis, ticker, "1Day", "webull", settings)
    except Exception as exc:
        return {"issue": f"Daily data unavailable ({type(exc).__name__})."}
    analysis = analysis or {}
    if str(analysis.get("data_quality") or "OK").upper() == "DEGRADED":
        return {"issue": "Daily data is degraded (unadjusted split or fallback feed).", "analysis": analysis}
    df = analysis.get("df")
    if df is None or not len(df):
        return {"issue": "No daily bars available.", "analysis": analysis}
    completed = sp.completed_bars(df, "1Day")
    if not len(completed):
        return {"issue": "No completed daily bars.", "analysis": analysis}
    import pandas as pd
    last_day = (pd.Timestamp(completed.index[-1]).tz_localize(None) if pd.Timestamp(completed.index[-1]).tzinfo else pd.Timestamp(completed.index[-1])).date()
    expected = previous_trading_day(today)
    if last_day < expected:
        return {"issue": f"Daily closes are stale: latest completed {last_day}, expected {expected}.", "analysis": analysis}
    closes = [float(x) for x in sp._column(completed, "close").tolist()]
    if last_day >= today:  # never use today's bar at decision time
        closes = closes[:-1]
    return {"closes": closes, "last_day": last_day.isoformat(), "analysis": analysis}


# ---------------------------------------------------------------------------
# Orders and bookkeeping
# ---------------------------------------------------------------------------

def _intent(engine, ticker: str, side: str, day_key: str, symbol: str) -> str:
    return engine._order_intent_id(ticker, side, {"candle_meta": {"candle_key": f"alloc:{day_key}"}, "level": f"alloc-{symbol}"}, symbol)


def _log(engine, ticker: str, action: str, reason: str, *, symbol: Optional[str] = None, price: float = 0.0,
         qty: float = 0.0, extra: Optional[dict] = None) -> dict:
    record = {
        "time": engine.get_now().isoformat(), "ticker": ticker, "action": action, "option_intent": "HOLD",
        "price_raw": float(price or 0.0), "price": f"${price:.2f}" if price else "$0.00",
        "qty": float(qty or 0.0), "total_cost": round(float(qty or 0.0) * float(price or 0.0), 2),
        "reason": reason, "timeframe": "1Day", "signals": {}, "bullish_count": 0, "bearish_count": 0,
        "total_signals": 0, "buy_mode": sp.PRESET_MODE, "sell_mode": sp.PRESET_MODE, "log_type": "Active Bot",
        "order_symbol": symbol, "allocation_event": action,
    }
    if extra:
        record.update(extra)
    engine.trade_log.insert(0, record)
    if len(engine.trade_log) > 200:
        del engine.trade_log[200:]
    return record


async def _book_fill(engine, ticker: str, t_settings: dict, st: dict, symbol: str, side: str, qty: float, price: float,
                     day_key: str, reason: str, order_id: str = "") -> None:
    leg = dict(st["legs"].get(symbol) or {})
    held = _f(leg.get("qty"))
    if side == "buy":
        new_qty = held + qty
        leg["avg"] = (held * _f(leg.get("avg")) + qty * price) / new_qty if new_qty > 0 else 0.0
        leg["qty"] = new_qty
        leg.setdefault("opened_at", engine.get_now().isoformat())
        leg["last_buy_day"] = day_key
        st["legs"][symbol] = leg
        _log(engine, ticker, "BUY", f"✅ BOUGHT {qty:.6f} {symbol} at ${price:.2f} · {reason}", symbol=symbol, price=price, qty=qty,
             extra={"order_id": order_id})
        await engine._increment_trade_count(ticker, entry=held <= EPS)
    else:
        sell_qty = min(qty, held) if held > 0 else qty
        avg = _f(leg.get("avg"))
        pl = (price - avg) * sell_qty if avg > 0 else 0.0
        st["realized_pl"] = round(_f(st.get("realized_pl")) + pl, 2)
        engine._apply_compound_pl(t_settings, pl, "regular")
        remaining = held - sell_qty
        if remaining <= EPS:
            st["legs"].pop(symbol, None)
        else:
            leg["qty"] = remaining
            st["legs"][symbol] = leg
        _log(engine, ticker, "SELL", f"✅ SOLD {sell_qty:.6f} {symbol} at ${price:.2f} (avg ${avg:.2f}, P/L ${pl:+.2f}) · {reason}",
             symbol=symbol, price=price, qty=sell_qty,
             extra={"order_id": order_id, "pl": round(pl, 2), "pl_pct": round((price / avg - 1) * 100, 2) if avg > 0 else None})
        await engine._increment_trade_count(ticker)


async def _handle_order_result(engine, ticker: str, t_settings: dict, st: dict, symbol: str, side: str, res: dict,
                               est_price: float, day_key: str, reason: str) -> str:
    """Returns "filled", "pending", "rejected" or "unknown"."""
    from engine import _order_status, DEAD_ORDER_STATUSES
    res = res if isinstance(res, dict) else {}
    if res.get("success"):
        res = await engine._confirm_fill(res)
        status = _order_status(res.get("status"))
        filled = _f(res.get("filled_qty")) or (_f(res.get("qty")) if status == "filled" else 0.0)
        if status == "filled" and filled > 0:
            price = _f(res.get("filled_avg_price")) or est_price
            await _book_fill(engine, ticker, t_settings, st, symbol, side, filled, price, day_key, reason, str(res.get("order_id") or ""))
            return "filled"
        if status in DEAD_ORDER_STATUSES and filled <= 0:
            _log(engine, ticker, "HOLD", f"{side.upper()} {symbol} order {status} without a fill.", symbol=symbol)
            return "rejected"
        st["pending"][symbol] = {"side": side, "order_id": str(res.get("order_id") or ""),
                                 "client_order_id": str(res.get("client_order_id") or ""), "day": day_key,
                                 "est_price": est_price, "reason": reason[:200], "submitted_at": engine.get_now().isoformat()}
        return "pending"
    if not res.get("success") and str(res.get("error") or "").startswith("No position"):
        _log(engine, ticker, "HOLD", f"{side.upper()} {symbol}: the broker has no position to sell; nothing was sent.", symbol=symbol)
        return "rejected"
    if res.get("rejected"):
        engine._note_order_rejected(ticker, side, {"candle_meta": {"candle_key": f"alloc:{day_key}"}, "level": f"alloc-{symbol}"}, res, symbol)
        _log(engine, ticker, "HOLD", f"{side.upper()} {symbol} rejected by the broker: {str(res.get('error') or '')[:200]}", symbol=symbol)
        return "rejected"
    # Unknown outcome: track by client order id and reconcile before doing anything else with this leg.
    st["pending"][symbol] = {"side": side, "order_id": "", "client_order_id": str(res.get("client_order_id") or res.get("_client_order_id") or ""),
                             "day": day_key, "est_price": est_price, "reason": reason[:200], "submitted_at": engine.get_now().isoformat(),
                             "uncertain": True}
    _log(engine, ticker, "HOLD", f"{side.upper()} {symbol}: broker outcome unknown; reconciling by client order id before any retry.", symbol=symbol)
    return "unknown"


async def reconcile_pending(engine, ticker: str, t_settings: dict, st: dict) -> set:
    from engine import _order_status, DEAD_ORDER_STATUSES
    get_order = getattr(engine.broker, "get_order", None)
    booked = set()
    if not callable(get_order):
        return booked
    for symbol, pending in list(st["pending"].items()):
        try:
            if pending.get("order_id"):
                info = await asyncio.to_thread(get_order, pending["order_id"])
            elif pending.get("client_order_id"):
                info = await asyncio.to_thread(get_order, None, pending["client_order_id"])
            else:
                info = None
        except Exception:
            info = None
        if not isinstance(info, dict):
            age = 0.0
            try:
                age = (engine.get_now() - datetime.fromisoformat(pending.get("submitted_at"))).total_seconds()
            except Exception:
                pass
            if pending.get("uncertain") and (not pending.get("client_order_id") or age > 600):
                # The broker has no order under this id after 10 minutes: nothing was created.
                st["pending"].pop(symbol, None)
                _log(engine, ticker, "HOLD", f"{str(pending.get('side', '')).upper()} {symbol}: no broker order found for the uncertain submission; nothing was filled.", symbol=symbol)
            continue
        status = _order_status(info.get("status"))
        if status != "filled" and status not in DEAD_ORDER_STATUSES:
            continue
        st["pending"].pop(symbol, None)
        booked.add(symbol)
        filled = _f(info.get("filled_qty"))
        if filled > 0:
            price = _f(info.get("filled_avg_price")) or _f(pending.get("est_price"))
            await _book_fill(engine, ticker, t_settings, st, symbol, pending.get("side", "buy"), filled, price,
                             pending.get("day") or "", f"{pending.get('reason') or 'Rebalance'} (fill confirmed later)",
                             str(info.get("order_id") or ""))
        else:
            _log(engine, ticker, "HOLD", f"{str(pending.get('side', '')).upper()} {symbol} order {status} without a fill.", symbol=symbol)
    return booked


# ---------------------------------------------------------------------------
# Cycle
# ---------------------------------------------------------------------------

def _risk_flags(engine, ticker: str, t_settings: dict, st: dict, prices: Dict[str, float], day_key: str) -> Dict[str, Any]:
    """Increases are blocked by a global halt, a paused bot or the bot's own daily loss limit."""
    from trader import get_risk_manager
    reasons = []
    try:
        if get_risk_manager().trading_halted:
            reasons.append("global risk halt")
    except Exception:
        reasons.append("risk state unavailable")
    if t_settings.get("paused"):
        reasons.append(f"bot paused ({t_settings.get('pause_reason') or 'by you'})")
    limit = _f(t_settings.get("max_daily_drawdown"))
    if t_settings.get("enable_risk_management", True) and limit > 0:
        value = sum(_leg_values(st, prices).values()) + _f(st.get("realized_pl"))
        if st.get("day_start_day") != day_key:
            st["day_start_day"], st["day_start_value"] = day_key, value
        allocated = engine._allocation_amount(t_settings, "regular")
        if allocated > 0 and value - _f(st.get("day_start_value"), value) <= -limit * allocated:
            reasons.append(f"bot daily loss limit ({limit * 100:.1f}% of ${allocated:,.0f}) reached")
    return {"block_increases": bool(reasons), "reasons": reasons}


def _status_record(engine, ticker: str, t_settings: dict, st: dict, info: Optional[dict], now: datetime,
                   prices: Optional[Dict[str, float]] = None, issue: Optional[str] = None, action: str = "HOLD") -> dict:
    prices = prices or {}
    legs_cfg = A.resolve_legs(t_settings.get("strategy_preset"), t_settings)
    sleeve = sleeve_for(engine, t_settings, st, prices) if prices else None
    holdings = []
    for sym in sorted(set(st["legs"]) | {s for s in legs_cfg.values() if s}):
        qty = _f((st["legs"].get(sym) or {}).get("qty"))
        px = _f(prices.get(sym))
        value = qty * px if px else None
        holdings.append({"symbol": sym, "qty": round(qty, 6), "price": round(px, 4) if px else None,
                         "value": round(value, 2) if value is not None else None,
                         "pct": round(value / sleeve * 100, 1) if value is not None and sleeve else None,
                         "pending": sym in st["pending"]})
    nxt = next_decision(t_settings, now)
    info = dict(info or {})
    info["allocation"] = {
        "holdings": holdings, "sleeve": round(sleeve, 2) if sleeve else None,
        "targets": (st.get("decision") or {}).get("targets") if st.get("decided_day") == now.date().isoformat() else info.get("targets"),
        "decided_day": st.get("decided_day"), "phase": st.get("phase"),
        "next_decision_at": nxt.isoformat() if nxt else None,
        "last_decision": st.get("decision"), "realized_pl": st.get("realized_pl"),
        "data_issue": issue, "pending": list(st["pending"]),
    }
    reason = issue or info.get("reason") or "Waiting for the next decision."
    record = {
        "time": engine.get_now().isoformat(), "ticker": ticker, "action": action, "option_intent": "HOLD",
        "price_raw": _f((info.get("values") or {}).get("price") or (info.get("provisional") or {}).get("price")),
        "reason": reason, "summary": reason, "timeframe": "1Day", "signals": {}, "bullish_count": 0, "bearish_count": 0,
        "total_signals": 0, "evaluation_status": "valid" if info.get("status") == "READY" and not issue else "waiting",
        "strategy_preset": info, "is_option": False, "status": "DEGRADED" if issue else "OK",
    }
    return record


def _publish(engine, ticker: str, record: dict) -> None:
    engine.scan_log_state[ticker] = record
    engine.bot_scans[ticker] = record
    engine.bot_scans_by_tf.setdefault("1Day", {})[ticker] = record


async def _status_info(engine, ticker: str, t_settings: dict) -> Optional[dict]:
    """Regime view for the card (confirmed + provisional), never an order."""
    import trader
    data = await _completed_closes(engine, ticker, t_settings, _now_et().date())
    analysis = data.get("analysis") or {}
    try:
        return trader._allocation_preset_info(ticker, analysis, t_settings)
    except Exception as exc:
        print(f"[alloc-{engine.uid}] {ticker} status failed: {type(exc).__name__}")
        return None


async def _prices_for(engine, symbols: List[str], positions: List[dict]) -> Dict[str, Any]:
    prices, issues = {}, []
    for sym in symbols:
        q = await _quote(engine, sym)
        if q["price"]:
            prices[sym] = q["price"]
        else:
            pos = _position(positions, sym)
            if pos and _f(pos.get("current_price")) > 0 and not q["issue"].startswith(f"{sym} price sources disagree"):
                prices[sym] = _f(pos.get("current_price"))
            else:
                issues.append(q["issue"])
    return {"prices": prices, "issues": issues}


async def _decide(engine, ticker: str, t_settings: dict, st: dict, positions: List[dict], now: datetime, dt: dict) -> Optional[dict]:
    """Make today's decision. Returns a plan or None (data issue: hold and retry until the cutoff)."""
    day_key = now.date().isoformat()
    preset_id = t_settings.get("strategy_preset")
    direction = A.normalize_direction(t_settings.get("strategy_preset_variant"))
    data = await _completed_closes(engine, ticker, t_settings, now.date())
    if data.get("issue"):
        return {"issue": data["issue"]}
    q = await _quote(engine, ticker)
    if not q["price"]:
        return {"issue": q["issue"]}
    decision = A.evaluate(data["closes"], q["price"], direction)
    if decision["status"] != A.STATUS_READY:
        return {"issue": decision["reason"]}
    legs = A.resolve_legs(preset_id, t_settings)
    targets = {legs["bull"]: decision["weights"]["bull"]}
    if legs["bear"]:
        targets[legs["bear"]] = decision["weights"]["bear"]
    for sym in st["legs"]:  # a leg the bot no longer uses (ETF or direction changed) goes to 0%
        targets.setdefault(sym, 0.0)
    pq = await _prices_for(engine, sorted(targets), positions)
    needed = [s for s, w in targets.items() if w > 0 or _f((st["legs"].get(s) or {}).get("qty")) > EPS]
    missing = [s for s in needed if s not in pq["prices"]]
    if missing:
        return {"issue": "; ".join(pq["issues"]) or f"No price for {', '.join(missing)}."}
    sleeve = sleeve_for(engine, t_settings, st, pq["prices"])
    orders = A.rebalance_orders(targets, {s: _f(l.get("qty")) for s, l in st["legs"].items()}, pq["prices"], sleeve)
    target_list = [{"symbol": s, "pct": round(w * 100)} for s, w in targets.items() if w > 0]
    st["decided_day"] = day_key
    st["phase"] = "sells"
    st["targets_w"] = targets
    st["decision"] = {
        "day": day_key, "at": now.isoformat(), "scheduled": dt["at"].isoformat(), "adjusted_for_early_close": dt["adjusted"],
        "regime": decision["regime"], "regime_label": decision["regime_label"], "values": decision["values"],
        "price": q["price"], "price_sources": q["sources"], "closes_through": data["last_day"],
        "targets": target_list, "sleeve": round(sleeve, 2),
        "orders": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in o.items()} for o in orders],
    }
    return {"orders": orders, "prices": pq["prices"], "decision": decision, "targets": targets, "sleeve": sleeve}


async def _submit_sells(engine, ticker: str, t_settings: dict, st: dict, plan: dict, positions: List[dict], day_key: str) -> None:
    reason = f"Rebalance: {plan['decision']['regime_label']}"
    for o in [o for o in plan["orders"] if o["side"] == "SELL"]:
        sym = o["symbol"]
        if sym in st["pending"]:
            continue
        leg = st["legs"].get(sym) or {}
        owned = _f(leg.get("qty"))
        if owned <= EPS:
            continue
        price = plan["prices"].get(sym) or 0.0
        cid = _intent(engine, ticker, "sell", day_key, sym)
        pos = _position(positions, sym)
        broker_qty = _f(pos.get("qty")) if pos else 0.0
        qty = owned if o["close_all"] else min(owned, _f(o["qty"]))
        if o["close_all"] and broker_qty <= owned + EPS:
            res = await asyncio.to_thread(engine.broker.close_position, sym, "Regular", price or None, cid)
        else:
            res = await asyncio.to_thread(engine.broker.place_order, sym, round(qty * price, 2), "sell", 0, 0, "Regular", price or None, cid)
        if isinstance(res, dict):
            res.setdefault("_client_order_id", cid)
        await _handle_order_result(engine, ticker, t_settings, st, sym, "sell", res, price, day_key,
                                   f"{reason} · target {round(_f(plan['targets'].get(sym)) * 100)}% {sym}")


async def _submit_buys(engine, ticker: str, t_settings: dict, st: dict, positions: List[dict], day_key: str,
                       buying_power: float, account_equity: float) -> float:
    """Buys toward today's targets with fresh prices. Returns the buying power left."""
    targets = st.get("targets_w") or {}
    pq = await _prices_for(engine, sorted(targets), positions)
    if any(s not in pq["prices"] for s, w in targets.items() if w > 0):
        st["last_issue"] = "; ".join(pq["issues"]) or "No price for a leg to buy."
        return buying_power
    prices = pq["prices"]
    risk = _risk_flags(engine, ticker, t_settings, st, prices, day_key)
    sleeve = sleeve_for(engine, t_settings, st, prices)
    orders = [o for o in A.rebalance_orders(targets, {s: _f(l.get("qty")) for s, l in st["legs"].items()}, prices, sleeve) if o["side"] == "BUY"]
    if orders and risk["block_increases"]:
        _log(engine, ticker, "HOLD", f"Increases blocked ({'; '.join(risk['reasons'])}); reductions still run.")
        st["phase"] = "done"
        return buying_power
    from trader import get_risk_manager
    rm = get_risk_manager()
    regime_label = (st.get("decision") or {}).get("regime_label") or "Rebalance"
    for o in orders:
        sym = o["symbol"]
        if sym in st["pending"]:
            continue
        price = prices[sym]
        want, limit = _f(o["notional"]), "target"
        if t_settings.get("enable_risk_management", True):
            max_pct = _f(t_settings.get("max_position_pct"), _f(getattr(rm, "max_position_pct", 1.0), 1.0))
            max_pct = max_pct / 100.0 if max_pct > 1.0 else max_pct
            cap = max_pct * account_equity - _f((st["legs"].get(sym) or {}).get("qty")) * price
            if cap < want:
                want, limit = max(0.0, cap), "max position"
        if buying_power < want:
            want, limit = max(0.0, buying_power), "buying power"
        if want < A.MIN_ORDER_NOTIONAL:
            if limit != "target":
                _log(engine, ticker, "HOLD", f"{sym} buy skipped: capped by {limit}.", symbol=sym)
            continue
        cid = _intent(engine, ticker, "buy", day_key, sym)
        res = await asyncio.to_thread(engine.broker.place_order, sym, round(want, 2), "buy", 0, 0, "Regular", price, cid)
        if isinstance(res, dict):
            res.setdefault("_client_order_id", cid)
        note = f"Rebalance: {regime_label} · target {round(_f(targets.get(sym)) * 100)}% {sym}" + (f" (capped by {limit})" if limit != "target" else "")
        outcome = await _handle_order_result(engine, ticker, t_settings, st, sym, "buy", res, price, day_key, note)
        if outcome in ("filled", "pending", "unknown"):
            buying_power -= want
    st["phase"] = "done"
    return buying_power


async def run_cycle(engine, positions: List[dict], now: Optional[datetime] = None) -> None:
    """Process every active allocation bot once. Called by the engine loop on each pass."""
    tickers = [t for t in dict.fromkeys(engine.config.TRADELIST) if is_allocation_bot(engine.config.TICKER_SETTINGS.get(t))]
    if not tickers:
        return
    now = _now_et(now)
    day_key = now.date().isoformat()
    now_iso = engine.get_now().isoformat()
    plans, changed = [], False
    for ticker in tickers:
        t = engine.config.TICKER_SETTINGS[ticker]
        st = state_of(t)
        try:
            booked = set()
            if st["pending"]:
                booked = await reconcile_pending(engine, ticker, t, st)
                changed = True
            for note in reconcile_ledger(t, positions, now_iso, skip=booked, now_ts=now.timestamp()):
                _log(engine, ticker, "HOLD", f"Ledger: {note}")
                changed = True
            exec_time = A.normalized_settings(t.get("strategy_preset"), t)["allocation_exec_time"]
            dt = A.decision_time(now.date(), exec_time)
            forced = engine.force_scan_tickers.pop(ticker.upper(), None) is not None if hasattr(engine, "force_scan_tickers") else False
            in_window = dt is not None and dt["at"] <= now < dt["close"] - ORDER_CUTOFF
            if dt is not None and now >= dt["close"] - ORDER_CUTOFF and st.get("decided_day") != day_key and st.get("missed_day") != day_key and st.get("window_seen_day") == day_key:
                st["missed_day"] = day_key
                _log(engine, ticker, "HOLD", f"No rebalance today: {st.get('last_issue') or 'the decision window passed without usable data'}. Holding the current allocation.")
                changed = True
            if (dt is not None and now >= dt["close"] - ORDER_CUTOFF and st.get("decided_day") == day_key
                    and st.get("phase") in ("sells", "buys") and st.get("missed_day") != day_key):
                st["missed_day"] = day_key
                st["phase"] = "done"
                _log(engine, ticker, "HOLD", "Buys skipped today: the sells had not settled before the order cutoff (5 minutes before the close).")
                changed = True
            if in_window:
                st["window_seen_day"] = day_key
            if in_window and st.get("decided_day") != day_key:
                if now.timestamp() - _f(st.get("last_attempt_at")) < 25:
                    continue  # data retry backoff
                st["last_attempt_at"] = now.timestamp()
                plan = await _decide(engine, ticker, t, st, positions, now, dt)
                if plan.get("issue"):
                    st["last_issue"] = plan["issue"]
                    _publish(engine, ticker, _status_record(engine, ticker, t, st, await _status_info(engine, ticker, t), now, issue=plan["issue"]))
                    st["last_status_at"] = now.timestamp()
                    continue
                st["last_issue"] = None
                changed = True
                n = len(plan["orders"])
                target_text = ", ".join("{}% {}".format(x["pct"], x["symbol"]) for x in st["decision"]["targets"]) or "cash"
                _log(engine, ticker, "REBALANCE" if n else "HOLD",
                     f"{plan['decision']['regime_label']}: target {target_text} · {n} order(s)"
                     + ("" if n else f" (every leg within {A.REBALANCE_BAND * 100:.0f}% of the sleeve)"),
                     price=plan["decision"]["values"].get("price", 0.0), extra={"allocation_decision": st["decision"]})
                plans.append((ticker, t, st, plan))
                if not n:
                    st["phase"] = "done"
            elif in_window and st.get("decided_day") == day_key and st.get("phase") in ("sells", "buys"):
                plans.append((ticker, t, st, None))
            if forced or not st.get("last_status_at") or now.timestamp() - _f(st.get("last_status_at")) >= STATUS_REFRESH_SECONDS:
                st["last_status_at"] = now.timestamp()
                info = await _status_info(engine, ticker, t)
                held = sorted(st["legs"])
                pq = await _prices_for(engine, held, positions) if held else {"prices": {}}
                _publish(engine, ticker, _status_record(engine, ticker, t, st, info, now, pq["prices"], issue=st.get("last_issue")))
        except Exception as exc:
            print(f"[alloc-{engine.uid}] {ticker} cycle error: {type(exc).__name__}: {exc}")

    # Phase 1: sells for every bot.
    for ticker, t, st, plan in plans:
        if plan is not None and st.get("phase") == "sells":
            try:
                await _submit_sells(engine, ticker, t, st, plan, positions, day_key)
            except Exception as exc:
                print(f"[alloc-{engine.uid}] {ticker} sell error: {type(exc).__name__}")
        if st.get("phase") == "sells" and not any(p.get("side") == "sell" for p in st["pending"].values()):
            st["phase"] = "buys"
    # Phase 2: buys, only for bots whose sells have all settled; buying power is read after the sells.
    buyers = [(ticker, t, st) for ticker, t, st, _ in plans if st.get("phase") == "buys"]
    if buyers:
        try:
            account = await asyncio.to_thread(engine.broker.get_account_info)
        except Exception:
            account = {}
        cash_account = _f(account.get("buying_power")) <= _f(account.get("cash")) + EPS  # no margin multiplier
        buying_power = _f(account.get("non_marginable_buying_power"), _f(account.get("cash"))) if cash_account else _f(account.get("cash"))
        equity = _f(account.get("equity"))
        fresh_positions = await asyncio.to_thread(engine.broker.get_positions)
        for ticker, t, st in buyers:
            try:
                buying_power = await _submit_buys(engine, ticker, t, st, fresh_positions, day_key, buying_power, equity)
            except Exception as exc:
                print(f"[alloc-{engine.uid}] {ticker} buy error: {type(exc).__name__}")
    for ticker, t, st, _ in plans:
        info = await _status_info(engine, ticker, t)
        pq = await _prices_for(engine, sorted(st["legs"]), positions) if st["legs"] else {"prices": {}}
        _publish(engine, ticker, _status_record(engine, ticker, t, st, info, now, pq["prices"], action="REBALANCE"))
        st["last_status_at"] = now.timestamp()
    if plans or changed:
        await engine.save_settings()
        await engine.save_history_throttled(force=True)


async def liquidate_legs(engine, ticker: str, t_settings: dict) -> Dict[str, Any]:
    """Close every leg the bot owns (your explicit "remove and liquidate"). Confirms each fill."""
    st = state_of(t_settings)
    positions = await asyncio.to_thread(engine.broker.get_positions)
    results, ok = {}, True
    day_key = _now_et().date().isoformat()
    for sym, leg in list(st["legs"].items()):
        owned = _f(leg.get("qty"))
        if owned <= EPS:
            continue
        pos = _position(positions, sym)
        broker_qty = _f(pos.get("qty")) if pos else 0.0
        price = _f(pos.get("current_price")) if pos else 0.0
        cid = _intent(engine, ticker, "sell", f"liquidate:{day_key}", sym)
        if broker_qty <= owned + EPS:
            res = await asyncio.to_thread(engine.broker.close_position, sym, "Regular", price or None, cid)
        else:
            res = await asyncio.to_thread(engine.broker.place_order, sym, round(owned * price, 2), "sell", 0, 0, "Regular", price or None, cid)
        outcome = await _handle_order_result(engine, ticker, t_settings, st, sym, "sell", res, price, day_key, "Liquidated on bot removal")
        results[sym] = outcome
        ok = ok and outcome == "filled"
    await engine.save_settings()
    return {"success": ok, "legs": results, "pending": list(st["pending"])}
