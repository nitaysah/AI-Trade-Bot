"""Plain-language Scan Log content for bots running a strategy preset (pure; no I/O).

The generic Scan Log is built around indicator votes, thresholds and the manual re-entry rules
(cooldown, signal reset, smart re-entry). A strategy owns its entries and exits and switches those
re-entry rules off, so its log shows what the strategy actually looks at instead: its own values, whether
it is waiting for data, flat or holding, the protective stop, and when it next evaluates.

Everything here is derived from the evaluation the strategy already produced
(``strategy_presets.evaluate_strategy_preset``); nothing is computed or decided here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

import preset_runtime
import strategy_presets

STATUS_LABELS = {
    "STRATEGY_ERROR": "Strategy error",
    "WAITING_DATA": "Waiting for data",
    "DEGRADED": "Data degraded",
    "BLOCKED": "Blocked",
    "ENTRY_READY": "Entry ready",
    "EXIT_SIGNAL": "Exit signal",
    "HOLDING": "Holding",
    "IDLE": "Idle",
    "WATCHING": "Watching",
    "SCHEDULED": "Scheduled",
    "ORDER_PENDING": "Order pending",
    "REBALANCED": "Rebalanced today",
    "HOLDING_OVERNIGHT": "Holding overnight",
    "HOLDING_INTRADAY": "Holding intraday",
    "EXIT_OVERDUE": "Exit overdue",
    "FLAT": "Flat · waiting for buy time",
}


def _hhmm_et_as_ct(value: Any) -> str:
    """A stored ``HH:MM`` Eastern time shown as Central (``2:30 PM CT``); anything else is returned unchanged."""
    from datetime import datetime, timedelta
    try:
        parsed = datetime.strptime(str(value), "%H:%M") - timedelta(hours=1)
    except ValueError:
        return str(value)
    return f"{parsed.strftime('%-I:%M %p')} CT"


def _et_label(iso: Any) -> Optional[str]:
    """``Thu Oct 8, 2:45 PM CT`` for an ISO time (Central time only)."""
    from datetime import datetime, timedelta
    try:
        at = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    import nyse_calendar
    ct = at.astimezone(nyse_calendar.EASTERN) - timedelta(hours=1)
    return f"{ct.strftime('%a %b %-d, %-I:%M %p')} CT"


def schedule_summary(settings: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The bot's own Buy and Sell times for a Session Rider card (display only), or None for any other bot."""
    import session_schedule as S
    if not strategy_presets.is_schedule_active(settings) or settings.get("options_trading"):
        return None
    cfg = S.normalized_settings(settings)

    def label(key):
        value = cfg.get(key)
        if not value:
            return ""
        return f"{S.describe(value)} · {S.et_ct_label(value)}" if not value[0].isdigit() else S.et_ct_label(value)

    mode = {S.MODE_OVERNIGHT: "Overnight", S.MODE_INTRADAY: "Intraday"}.get(cfg.get("mode"), "")
    return {"mode": mode, "buy_label": label(S.BUY_KEY), "sell_label": label(S.SELL_KEY), "skip_weekend": bool(cfg.get(S.SKIP_WEEKEND_KEY))}


def _allocation_view(settings: Mapping[str, Any], scan: Mapping[str, Any], info: Mapping[str, Any]) -> Dict[str, Any]:
    """Scan Log content for a Malik allocation bot: regime, target vs held per leg, next decision."""
    import allocation_strategy
    alloc = info.get("allocation") or {}
    status_raw = str(info.get("status") or "")
    from datetime import datetime
    import nyse_calendar
    today = datetime.now(nyse_calendar.EASTERN).date().isoformat()
    if alloc.get("data_issue"):
        status = "DEGRADED"
    elif alloc.get("pending"):
        status = "ORDER_PENDING"
    elif status_raw == "WAITING_DATA":
        status = "WAITING_DATA"
    elif status_raw == "DEGRADED":
        status = "DEGRADED"
    elif alloc.get("decided_day") == today and alloc.get("phase") == "done":
        status = "REBALANCED"
    else:
        status = "SCHEDULED"
    targets = alloc.get("targets") if alloc.get("decided_day") == today and alloc.get("targets") is not None else info.get("targets") or []
    target_text = ", ".join(f"{t['pct']}% {t['symbol']}" for t in targets) or "cash"
    nxt = _et_label(alloc.get("next_decision_at"))
    regime = info.get("regime_label") or "—"
    if status == "WAITING_DATA":
        head = str(info.get("reason") or "Waiting for data.")
    elif status == "DEGRADED":
        head = f"{alloc.get('data_issue') or info.get('reason') or 'Data is degraded.'} Holding the current allocation; nothing is sold because of a data problem."
    elif status == "REBALANCED":
        head = f"{(alloc.get('last_decision') or {}).get('regime_label') or regime}: rebalanced today to {target_text}."
    elif status == "ORDER_PENDING":
        head = f"Order(s) for {', '.join(alloc.get('pending') or [])} are working at the broker; buys follow once sells fill."
    else:
        head = f"{regime} as of the last close: target {target_text}."
    prov = info.get("provisional") or {}
    if prov and status not in ("REBALANCED", "WAITING_DATA") and prov.get("regime") != info.get("regime"):
        head += f" With the live price it would be {prov.get('regime_label')} ({prov.get('target_text')}); the decision is made at the execution time."
    if nxt and status != "WAITING_DATA":
        head += f" Next decision: {nxt}."
    values = info.get("values") or {}
    price, slow, fast = _num(values.get("price")), _num(values.get("sma_slow")), _num(values.get("sma_fast"))
    metrics_out = [
        {"label": "Regime", "value": regime},
        {"label": "Price vs 250-day", "value": _pct(_gap_pct(price, slow)), "tone": "up" if (price or 0) > (slow or 0) else "down"},
        {"label": "Price vs 50-day", "value": _pct(_gap_pct(price, fast)), "tone": "up" if (price or 0) > (fast or 0) else "down"},
        {"label": "10-day change", "value": _pct(_num(values.get("roc10"))), "tone": "up" if (_num(values.get("roc10")) or 0) >= 0 else "down"},
    ] if values else []
    target_pct = {t["symbol"]: t["pct"] for t in targets}
    legs = []
    for h in alloc.get("holdings") or []:
        legs.append({"symbol": h["symbol"], "target_pct": target_pct.get(h["symbol"], 0), "held_pct": h.get("pct"),
                     "qty": h.get("qty"), "value": h.get("value"), "pending": h.get("pending")})
    preset = strategy_presets.get_preset(settings.get("strategy_preset"))
    meta = _params(settings)
    exec_setting = allocation_strategy.normalized_settings(settings.get("strategy_preset"), settings)["allocation_exec_time"]
    exec_label = next((o["label"] for o in allocation_strategy.exec_time_options() if o["value"] == exec_setting), _hhmm_et_as_ct(exec_setting))
    return {
        "kind": "allocation",
        "rules": {"entry": preset.entry_rule if preset else "", "exit": preset.exit_rule if preset else "",
                  "stop": "No stop orders: the strategy only acts once a day at the execution time, so it depends on this app running then."},
        "untested": allocation_strategy.research_note(settings.get("strategy_preset")),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status.title()),
        "strategy": f"{meta['label']} · {meta.get('variant', '')}".strip(" ·"),
        "headline": head,
        "metrics": metrics_out,
        "position": None,
        "allocation": {"legs": legs, "sleeve": alloc.get("sleeve"), "execution_time": exec_label,
                       "next_decision": nxt, "last_decision": alloc.get("last_decision"),
                       "realized_pl": alloc.get("realized_pl"), "provisional": prov or None},
        "timing": {"candle": None, "idle": False, "next_evaluation_at": alloc.get("next_decision_at")},
    }


def _schedule_view(settings: Mapping[str, Any], scan: Mapping[str, Any], info: Mapping[str, Any],
                   position: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Scan Log content for a Session Rider bot: buy/sell times, where the schedule is, next action."""
    import session_schedule as S
    cfg = S.normalized_settings(settings)
    sched = info.get("schedule") or {}
    phase = str(sched.get("phase") or "FLAT")
    status = "ORDER_PENDING" if isinstance(settings.get("pending_fill"), dict) else phase
    if sched.get("issue") and status not in ("ORDER_PENDING", "EXIT_OVERDUE"):
        status = "DEGRADED" if "pending" not in str(sched.get("issue")).lower() else "ORDER_PENDING"
    mode_text = "Overnight (buy late, sell next session)" if cfg["mode"] == S.MODE_OVERNIGHT else "Intraday (flat overnight)"
    nxt = sched.get("next_label") or _et_label(sched.get("next_at"))
    if status == "EXIT_OVERDUE":
        head = f"The sell time has passed and the position is still open. The bot retries the sell every scan. {sched.get('last_note') or ''}".strip()
    elif status == "ORDER_PENDING":
        head = "An order is working at the broker; it is booked once the broker reports the fill."
    elif phase.startswith("HOLDING"):
        head = f"Holding {sched.get('holding_qty') or ''} shares. Scheduled sell: {nxt}.".replace("Holding  shares", "Holding")
    elif nxt:
        head = f"Flat. Next buy: {nxt} (window {int(S.ENTRY_WINDOW.total_seconds() // 60)} minutes; a missed window is skipped, never bought late)."
    else:
        head = "Flat. No buy is scheduled in the next days."
    if sched.get("last_note") and status not in ("EXIT_OVERDUE",):
        head += f" Last note: {sched['last_note']}"
    metrics_out = [
        {"label": "Mode", "value": "Overnight" if cfg["mode"] == S.MODE_OVERNIGHT else "Intraday"},
        {"label": "Buy", "value": f"{S.describe(cfg[S.BUY_KEY])} · {S.et_ct_label(cfg[S.BUY_KEY])}" if cfg[S.BUY_KEY] and not cfg[S.BUY_KEY][0].isdigit() else S.et_ct_label(cfg[S.BUY_KEY]) if cfg[S.BUY_KEY] else "—"},
        {"label": "Sell", "value": f"{S.describe(cfg[S.SELL_KEY])} · {S.et_ct_label(cfg[S.SELL_KEY])}" if cfg[S.SELL_KEY] and not cfg[S.SELL_KEY][0].isdigit() else S.et_ct_label(cfg[S.SELL_KEY]) if cfg[S.SELL_KEY] else "—"},
        {"label": "Losses in a row", "value": f"{sched.get('consecutive_losses', 0)} of {cfg[S.LOSS_PAUSE_KEY]} (pause)"},
    ]
    preset = strategy_presets.get_preset(settings.get("strategy_preset"))
    pos_block = None
    if position:
        entry, current = _num(position.get("avg_price")), _num(position.get("current_price"))
        pos_block = {"entry": _money(entry), "current": _money(current), "qty": _num(position.get("qty")),
                     "pl_pct": None if not entry or current is None else round((current / entry - 1.0) * 100.0, 2),
                     "stop": None, "stop_distance_pct": None,
                     "stop_note": "No stop: the position is held until the scheduled sell, including any overnight gap."}
    exit_rows: List[Dict[str, Any]] = []
    if position and sched.get("next_event") == "SELL" and sched.get("next_at"):
        state = settings.get("session_state") if isinstance(settings.get("session_state"), dict) else {}
        exit_rows.append({
            "kind": "time", "label": "Scheduled sell", "value": str(nxt or ""), "until": sched.get("next_at"),
            "since": state.get("entry_submitted_at"),
            "detail": "A time-based exit: no stop-loss, take-profit or trailing exit. Overnight gaps are fully exposed.",
        })
    return {
        "kind": "schedule",
        "titles": {"metrics": "Schedule", "rules": "Rules"},
        "exit_watch": exit_rows,
        "rules": {"entry": preset.entry_rule if preset else "", "exit": preset.exit_rule if preset else "",
                  "stop": "No stop-loss, take-profit or trailing exits. The sell depends on this app running at the sell time; it is not a broker order."},
        "untested": "",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status.replace("_", " ").title()),
        "strategy": f"Session Rider · {mode_text}",
        "headline": head,
        "metrics": metrics_out,
        "position": pos_block,
        "schedule": {"mode": cfg["mode"], "phase": phase, "next_event": sched.get("next_event"), "next_label": nxt,
                     "skip_weekend": cfg[S.SKIP_WEEKEND_KEY], "armed_at": sched.get("armed_at")},
        "timing": {"candle": None, "idle": False, "next_evaluation_at": sched.get("next_at")},
    }


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _money(value: Any) -> str:
    number = _num(value)
    return "—" if number is None else f"{number:,.2f}"


def _pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def _gap_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None or b == 0 else (a - b) / b * 100.0


def _params(settings: Mapping[str, Any]) -> Dict[str, Any]:
    enabled, preset_id, variant_id = strategy_presets.normalize_strategy_preset(settings)
    preset = strategy_presets.get_preset(preset_id) if enabled else None
    if preset is None:
        return {"preset_id": "", "label": "Strategy"}
    variant = strategy_presets.get_variant(preset, variant_id)
    return {"preset_id": preset.id, "label": preset.label, "variant": variant.label.replace(" · Recommended", ""),
            "params": dict(variant.params)}


def _names(meta: Mapping[str, Any]) -> Dict[str, str]:
    params = meta.get("params") or {}
    kind = str(params.get("ma", "sma")).upper()
    return {"fast": f"{kind}{params.get('fast', '')}", "slow": f"{kind}{params.get('slow', '')}",
            "ema": f"EMA{params.get('ema', 200)}"}


def metrics(settings: Mapping[str, Any], info: Mapping[str, Any], holding: bool = False) -> List[Dict[str, str]]:
    """The strategy's own numbers, labelled the way the strategy names them."""
    meta = _params(settings)
    values = info.get("values") or {}
    if not values:
        return []
    close = _num(values.get("close"))
    if meta["preset_id"] == "golden_cross":
        names = _names(meta)
        fast, slow = _num(values.get("fast")), _num(values.get("slow"))
        gap = _gap_pct(fast, slow)
        rows = [
            {"label": names["fast"], "value": _money(fast)},
            {"label": names["slow"], "value": _money(slow)},
        ]
        if not holding:  # while holding, the gap is shown as the exit watch bar
            rows.append({"label": "Gap", "value": _pct(gap), "tone": "up" if (gap or 0) > 0 else "down"})
        return rows
    if meta["preset_id"] == "supertrend_ema200":
        ema_name = _names(meta)["ema"]
        st, ema = _num(values.get("st")), _num(values.get("ema"))
        bullish = (_num(values.get("st_dir")) or 0) > 0
        rows = [
            {"label": "Close", "value": _money(close)},
            {"label": "Supertrend", "value": f"{_money(st)} {'bullish' if bullish else 'bearish'}", "tone": "up" if bullish else "down"},
            {"label": ema_name, "value": _money(ema)},
        ]
        if not holding:  # while holding, the distance is shown as the exit watch bar
            rows.append({"label": f"Close vs {ema_name}", "value": _pct(_gap_pct(close, ema)), "tone": "up" if (close or 0) > (ema or 0) else "down"})
        return rows
    return []


def compact(settings: Mapping[str, Any], info: Mapping[str, Any]) -> str:
    """One short chip for a log row, e.g. ``SMA50 410.20 < SMA200 421.70 (-2.70%)``."""
    meta = _params(settings)
    values = info.get("values") or {}
    if not values:
        warm = info.get("warmup") or {}
        if info.get("status") == "WAITING_DATA" and warm.get("required"):
            return f"{warm.get('available', 0)}/{warm.get('required')} bars"
        return ""
    if meta["preset_id"] == "golden_cross":
        names = _names(meta)
        fast, slow = _num(values.get("fast")), _num(values.get("slow"))
        sign = ">" if (fast or 0) > (slow or 0) else "<"
        return f"{names['fast']} {_money(fast)} {sign} {names['slow']} {_money(slow)} ({_pct(_gap_pct(fast, slow))})"
    if meta["preset_id"] == "supertrend_ema200":
        bullish = (_num(values.get("st_dir")) or 0) > 0
        close, ema = _num(values.get("close")), _num(values.get("ema"))
        sign = ">" if (close or 0) > (ema or 0) else "<"
        return f"ST {'bullish' if bullish else 'bearish'} · close {_money(close)} {sign} {_names(meta)['ema']} {_money(ema)}"
    return ""


def _waiting_phrase(settings: Mapping[str, Any]) -> str:
    meta = _params(settings)
    if meta["preset_id"] == "golden_cross":
        names = _names(meta)
        return f"{names['fast']} to cross above {names['slow']}"
    if meta["preset_id"] == "supertrend_ema200":
        return f"Supertrend to flip bullish with the close above {_names(meta)['ema']}"
    return "the next entry signal"


def _exit_phrase(settings: Mapping[str, Any]) -> str:
    meta = _params(settings)
    if meta["preset_id"] == "golden_cross":
        names = _names(meta)
        return f"{names['fast']} falls below {names['slow']}"
    if meta["preset_id"] == "supertrend_ema200":
        return f"Supertrend turns bearish or the close falls below {_names(meta)['ema']}"
    return "the strategy's exit condition"


def classify(info: Mapping[str, Any], decision: str, has_position: bool, blocked: bool, idle: bool) -> str:
    status = str(info.get("status") or "")
    if status == "INVALID_PRESET":
        return "STRATEGY_ERROR"
    if status == "WAITING_DATA":
        return "WAITING_DATA"
    if status == "DEGRADED":
        return "DEGRADED"
    wanted = str(info.get("action") or "HOLD").upper()
    if wanted in ("BUY", "SELL") and blocked:
        return "BLOCKED"
    if has_position:
        return "EXIT_SIGNAL" if wanted == "SELL" or decision == "SELL" else "HOLDING"
    if wanted == "BUY" or decision == "BUY":
        return "ENTRY_READY"
    return "IDLE" if idle else "WATCHING"


def headline(settings: Mapping[str, Any], info: Mapping[str, Any], status: str, has_position: bool) -> str:
    warm = info.get("warmup") or {}
    if status == "STRATEGY_ERROR":
        return "The saved strategy is not valid: re-save the bot's settings."
    if status == "WAITING_DATA":
        if warm.get("required"):
            return f"Waiting for data: {warm.get('available', 0)}/{warm.get('required')} completed bars. No entries until it is ready."
        return str(info.get("reason") or "Waiting for data. No entries until it is ready.")
    if status == "DEGRADED":
        return f"{info.get('reason') or 'Data is degraded.'} Entries are blocked; an open position's protective stop is still watched."
    if has_position:
        if status == "EXIT_SIGNAL":
            return f"Exit condition met ({_exit_phrase(settings)}): sells at the next trading window."
        return f"Holding · sells when {_exit_phrase(settings)}."
    age = info.get("signal_age_bars")
    if status == "BLOCKED":
        return "The strategy signalled an entry on the completed candle, but a gate is holding the order back (see below)."
    if str(info.get("state") or "").upper() == "LONG":
        since = f" for {age} bar{'s' if age != 1 else ''}" if isinstance(age, int) else ""
        return f"Trend is up{since}, but the bot only enters on a fresh signal: waiting for {_waiting_phrase(settings)} again."
    if status == "ENTRY_READY":
        return "Entry signal on the completed candle: the order goes out inside the trading window."
    return f"Flat · waiting for {_waiting_phrase(settings)}."


def position_block(settings: Mapping[str, Any], info: Mapping[str, Any], position: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not position:
        return None
    entry, current = _num(position.get("avg_price")), _num(position.get("current_price"))
    block: Dict[str, Any] = {
        "entry": _money(entry), "current": _money(current), "qty": _num(position.get("qty")),
        "pl_pct": None if not entry or current is None else round((current / entry - 1.0) * 100.0, 2),
        "stop": None, "stop_distance_pct": None,
        "stop_note": "Software stop: checked at each refresh while the market is open. It is not a broker order.",
    }
    stop = preset_runtime.protective_stop(settings, info, entry or 0.0)
    if stop:
        price = stop[0]
        block["stop"] = _money(price)
        block["stop_rule"] = f"{stop[1]:g}x ATR below your average cost"
        if current:
            block["stop_distance_pct"] = round((current / price - 1.0) * 100.0, 2) if price > 0 else None
    return block


# How "near" an exit is shown on the card: the bar is full when the exit triggers and empty at SCALE or further away.
# The numbers next to each bar are the real distances; the scale only sets how quickly the bar fills.
_GAP_SCALE_PCT = 3.0    # moving-average gap and Supertrend line
_EMA_SCALE_PCT = 5.0    # close vs the long EMA


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _near(distance_pct: Optional[float], scale: float) -> Optional[int]:
    """0-100: how close an exit is, from its distance in % (0 or negative = triggered)."""
    if distance_pct is None:
        return None
    return int(round((1.0 - _clamp01(distance_pct / scale)) * 100))


def exit_watch(settings: Mapping[str, Any], info: Mapping[str, Any], position: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Live rows for an open position: every exit the strategy has, how far away it is, and how near (0-100).

    Preset exits are the strategy's own exit rule plus the software ATR stop; they have no take-profit,
    stop-loss % or trailing stop. Nothing here decides or places an order."""
    if not position:
        return []
    meta = _params(settings)
    values = info.get("values") or {}
    entry, current = _num(position.get("avg_price")), _num(position.get("current_price"))
    rows: List[Dict[str, Any]] = []
    close = _num(values.get("close")) or current
    if meta["preset_id"] == "golden_cross" and values:
        names = _names(meta)
        gap = _gap_pct(_num(values.get("fast")), _num(values.get("slow")))
        if gap is not None:
            rows.append({
                "kind": "strategy", "label": f"{names['fast']} vs {names['slow']}",
                "value": f"{gap:+.2f}% · exits at 0%" if gap > 0 else f"{gap:+.2f}% · exit condition met",
                "near_pct": _near(gap, _GAP_SCALE_PCT),
                "detail": f"Sells when {names['fast']} closes below {names['slow']} on a completed daily bar.",
            })
    elif meta["preset_id"] == "supertrend_ema200" and values:
        ema_name = _names(meta)["ema"]
        st, ema = _num(values.get("st")), _num(values.get("ema"))
        bullish = (_num(values.get("st_dir")) or 0) > 0
        if st and close:
            dist = (close - st) / close * 100.0 if bullish else 0.0
            rows.append({"kind": "strategy", "label": "Supertrend line",
                         "value": f"{_money(st)} · price {dist:.2f}% above" if bullish else f"{_money(st)} · turned bearish",
                         "near_pct": _near(dist, _GAP_SCALE_PCT), "detail": "Sells when Supertrend turns bearish."})
        if ema and close:
            dist = (close - ema) / ema * 100.0
            rows.append({"kind": "strategy", "label": ema_name,
                         "value": f"{_money(ema)} · price {dist:+.2f}% vs it",
                         "near_pct": _near(dist, _EMA_SCALE_PCT), "detail": f"Sells when the close falls below {ema_name}."})
    if meta["preset_id"] in ("golden_cross", "supertrend_ema200") or info.get("stop_atr_mult"):
        stop = preset_runtime.protective_stop(settings, info, entry or 0.0)
        row: Dict[str, Any] = {"kind": "stop", "label": "Protective stop", "near_pct": None,
                               "detail": "Software stop: checked at each refresh while the market is open. It is not a broker order."}
        if stop and current:
            price = stop[0]
            below = (current / price - 1.0) * 100.0 if price > 0 else None
            span = (entry - price) if entry else 0.0
            row["value"] = f"{_money(price)} · {below:.1f}% below price" if below is not None and below >= 0 else f"{_money(price)} · reached"
            row["detail"] = f"{stop[1]:g}x ATR below your average cost. " + row["detail"]
            if entry and span > 0:
                row["near_pct"] = int(round(_clamp01((entry - current) / span) * 100))
                row["near_text"] = "of the way from entry to stop"
        else:
            row["value"] = "not recorded yet"
        rows.append(row)
    return rows


def scan_view(settings: Mapping[str, Any], scan: Mapping[str, Any], position: Optional[Mapping[str, Any]],
              scan_policy: Optional[Mapping[str, Any]], real_blockers: List[str]) -> Optional[Dict[str, Any]]:
    """The strategy Scan Log content, or None when the bot is not running a strategy."""
    info = scan.get("strategy_preset")
    if not strategy_presets.is_strategy_preset_active(settings) or settings.get("options_trading"):
        return None
    info = info if isinstance(info, Mapping) else {}
    if strategy_presets.is_allocation(settings.get("strategy_preset")):
        return _allocation_view(settings, scan, info)
    if strategy_presets.is_schedule(settings.get("strategy_preset")):
        return _schedule_view(settings, scan, info, position)
    has_position = bool(position)
    idle = bool(scan_policy and scan_policy.get("idle"))
    decision = str(scan.get("action") or "HOLD").upper()
    status = classify(info, decision, has_position, bool(real_blockers), idle)
    meta = _params(settings)
    candle = preset_runtime._candle_trading_date(scan.get("candle_meta") or {})
    preset = strategy_presets.get_preset(meta.get("preset_id"))
    rules = {"entry": preset.entry_rule, "exit": preset.exit_rule,
             "stop": f"Protective stop: {preset.protective_stop_atr_mult:g}x ATR({preset.protective_atr_period}) below your average cost, watched by software at each refresh (not a broker order)."} if preset else {}
    return {
        "rules": rules,
        "untested": strategy_presets.untested_settings_note(
            meta.get("preset_id"), settings.get("timeframe"), settings.get("market_session"), settings.get("strategy_preset_variant")),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status.title()),
        "strategy": f"{meta['label']} · {meta.get('variant', '')}".strip(" ·"),
        "headline": headline(settings, info, status, has_position),
        "metrics": metrics(settings, info, holding=has_position),
        "position": position_block(settings, info, position or {}),
        "exit_watch": exit_watch(settings, info, position),
        "timing": {
            "candle": candle.isoformat() if candle else None,
            "idle": idle,
            "next_evaluation_at": (scan_policy or {}).get("next_evaluation_at") if idle else None,
        },
    }


def row_view(settings: Mapping[str, Any], trade: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """What a log row for a strategy bot shows instead of indicator counts (None for manual bots)."""
    info = trade.get("strategy_preset")
    if strategy_presets.is_allocation_active(settings) and trade.get("allocation_event"):
        action = str(trade.get("action") or "HOLD").upper()
        symbol = trade.get("order_symbol")
        decision = trade.get("allocation_decision") or {}
        chip = ", ".join(f"{t['pct']}% {t['symbol']}" for t in decision.get("targets") or []) if decision else (symbol or "")
        event = {"REBALANCE": "REBALANCE", "BUY": f"BUY {symbol or ''}".strip(), "SELL": f"SELL {symbol or ''}".strip()}.get(action, "NOTE")
        return {"event": event, "chip": chip or ("cash" if decision else "")}
    if strategy_presets.is_schedule_active(settings) and trade.get("session_event"):
        event = str(trade.get("session_event")).replace("_", " ")
        chip = trade.get("price") if str(trade.get("action") or "").upper() in ("BUY", "SELL") else ""
        return {"event": event, "chip": chip or ""}
    if not isinstance(info, Mapping) or not strategy_presets.is_strategy_preset_active(settings):
        return None
    action = str(trade.get("action") or "HOLD").upper()
    reason = str(trade.get("reason") or "")
    status = str(info.get("status") or "")
    if action == "BUY":
        event = "ENTRY"
    elif action == "SELL":
        event = "STOP EXIT" if "Stop Loss Hit" in reason else "STRATEGY EXIT"
    elif status == "WAITING_DATA":
        event = "WAITING DATA"
    elif status == "DEGRADED":
        event = "DEGRADED"
    elif str(info.get("action") or "").upper() in ("BUY", "SELL"):
        event = "HELD BACK"
    else:
        event = "WATCHING"
    return {"event": event, "chip": compact(settings, info)}
