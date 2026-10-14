"""Plain-language "where is this bot in its cycle" for the Bots scan log.

A manual (indicator or structure) bot walks one loop:

    ENTRY WATCH -> (position opens) -> EXIT WATCH -> (position closes) -> RE-ENTRY GATES -> ENTRY WATCH ...

* ENTRY WATCH   flat, no gate is blocking: which entry signals are on, which are missing and how close the bot is.
* EXIT WATCH    holding a position: how far price is from the stop, the target and any indicator exit.
* RE-ENTRY      flat after an exit, and at least one gate (post-stop wait, cooldown, signal reset, smart re-entry filter)
                is still blocking. Every gate is listed with its own status.

This module only DESCRIBES. It never changes an action, a size or a gate: the engine computes the gate states with the
same rules it enforces and passes them in, and the result is stored as ``scan_phase`` next to the unchanged ``reason``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

PHASE_ENTRY = "ENTRY WATCH"
PHASE_EXIT = "EXIT WATCH"
PHASE_REENTRY = "RE-ENTRY"
PHASE_GATE = "ENTRY GATE"  # a gate (for example the smart re-entry filter) holds a first entry, no exit happened yet

GATE_LABELS = {
    "post_stop": "Post-stop wait",
    "cooldown": "Cooldown",
    "signal_reset": "Signal reset",
    "smart_reentry": "Smart re-entry",
}
GATE_ORDER = ("post_stop", "cooldown", "signal_reset", "smart_reentry")
# Short action-column label per blocking gate.
GATE_ACTION = {
    "post_stop": "POST-STOP",
    "cooldown": "COOLDOWN",
    "signal_reset": "RESET",
    "smart_reentry": "SMART RE-ENTRY",
}


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number not in (float("inf"), float("-inf")) else default


def _money(value: float) -> str:
    return f"${value:,.2f}" if abs(value) >= 1 else f"${value:,.4f}"


def _pct(delta: float) -> str:
    return f"{delta:+.1f}%"


def _threshold(settings: Mapping[str, Any], keys: Iterable[str], default: int = 1) -> int:
    for key in keys:
        value = settings.get(key)
        if value in (None, ""):
            continue
        try:
            return max(1, int(float(value)))
        except (TypeError, ValueError):
            continue
    return default


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds}s"


def duration_text(seconds: float) -> str:
    return _duration(seconds)


def entry_mode_kind(settings: Mapping[str, Any]) -> str:
    """'indicator', 'hybrid' or 'structure' (ATR, percentage, pivot, high/low, swing, custom price)."""
    mode = str(settings.get("buy_mode") or "").lower()
    if mode in ("", "indicator"):
        return "indicator"
    if mode.startswith("hybrid"):
        return "hybrid"
    return "structure"


def _signal_states(signals: Optional[Mapping[str, Any]]):
    """(bullish names, bearish names, neutral names) over the signals the bot actually uses."""
    bullish: List[str] = []
    bearish: List[str] = []
    neutral: List[str] = []
    for name, info in (signals or {}).items():
        if not isinstance(info, Mapping) or info.get("enabled") is False:
            continue
        state = str(info.get("signal") or "NEUTRAL").upper()
        (bullish if state == "BULLISH" else bearish if state == "BEARISH" else neutral).append(str(name))
    return bullish, bearish, neutral


def entry_progress(result: Mapping[str, Any], settings: Mapping[str, Any]) -> Dict[str, Any]:
    need = _threshold(settings, ("min_buy_signals", "threshold", "buy_threshold"))
    bullish, bearish, neutral = _signal_states(result.get("signals"))
    have = len(bullish)
    return {
        "have": have,
        "need": need,
        "remaining": max(0, need - have),
        "bullish": bullish,
        # Neutral signals are one step from bullish; bearish ones are further away.
        "missing": [{"name": name, "state": "neutral"} for name in neutral] + [{"name": name, "state": "bearish"} for name in bearish],
    }


def _entry_headline(result: Mapping[str, Any], settings: Mapping[str, Any], progress: Dict[str, Any],
                    held_back: str = "") -> Dict[str, Any]:
    kind = entry_mode_kind(settings)
    have, need = progress["have"], progress["need"]
    if kind == "structure":
        mode = str(settings.get("buy_mode") or "structure").replace("_", " ")
        return {"headline": f"Entry watch · {mode} entry: {str(result.get('reason') or 'waiting for the price level').rstrip('.')}.",
                "chip": "ENTRY"}

    parts: List[str] = []
    if need and have >= need:
        parts.append(f"All {need} entry signals are on" if need == 1 else f"All entry signals are on ({have}/{need})")
    else:
        parts.append(f"{have}/{need} bullish signals")
        if have:
            parts.append("on: " + ", ".join(progress["bullish"]))
        if progress["missing"]:
            neutral = [m["name"] for m in progress["missing"] if m["state"] == "neutral"]
            bearish = [m["name"] for m in progress["missing"] if m["state"] == "bearish"]
            pieces = []
            if neutral:
                pieces.append("neutral: " + ", ".join(neutral))
            if bearish:
                pieces.append("bearish: " + ", ".join(bearish))
            parts.append("not yet (" + "; ".join(pieces) + ")")
        if progress["remaining"] == 1:
            parts.append("1 more signal to enter")
        elif progress["remaining"] > 1:
            parts.append(f"{progress['remaining']} more needed")
    if kind == "hybrid":
        structure = "confirmed" if result.get("structure_ok") else "not confirmed yet"
        parts.append(f"price structure {structure}")
    if held_back:
        parts.append(f"held back: {held_back.rstrip('.')}")
    return {"headline": "Entry watch · " + " · ".join(parts) + ".", "chip": f"{have}/{need}"}


def _exit_headline(result: Mapping[str, Any], settings: Mapping[str, Any]) -> Dict[str, Any]:
    details = result.get("exit_watch_details") if isinstance(result.get("exit_watch_details"), Mapping) else {}
    price = _num(result.get("price_raw"))  # the current price, so the distances match the price column
    pieces: List[str] = []
    if price:
        pieces.append(_money(price))
    stop = _num(details.get("stop_loss"))
    target = _num(details.get("take_profit"))
    if stop and price:
        pieces.append(f"stop {_money(stop)} ({_pct((stop / price - 1.0) * 100.0)})")
    if target and price:
        pieces.append(f"target {_money(target)} ({_pct((target / price - 1.0) * 100.0)})")
    if details.get("trail_enabled"):
        pieces.append("trailing stop on")
    sell_mode = str(settings.get("sell_mode") or "").lower()
    if sell_mode in ("indicator", "hybrid") or sell_mode.startswith("hybrid"):
        need = _threshold(settings, ("min_sell_signals", "sell_threshold"))
        bearish = int(_num(result.get("bearish_count"), 0) or 0)
        pieces.append(f"{bearish}/{need} bearish exit signals")
    label = str(result.get("exit_signal_label") or "").replace("Exit", "").strip() or "EXIT"
    if not pieces:
        return {"headline": "Exit watch · position open; waiting for an exit rule to trigger.", "chip": label}
    return {"headline": "Exit watch · " + " · ".join(pieces) + ".", "chip": label}


def describe(result: Mapping[str, Any], settings: Mapping[str, Any], *, has_position: bool,
             gates: Optional[List[Dict[str, Any]]] = None, held_back: str = "", after_exit: bool = True) -> Dict[str, Any]:
    """Return the phase for one scan row. ``gates`` are the engine's own gate states, in GATE_ORDER:
    ``{"key", "state": "blocking"|"clear"|"off", "detail"}``."""
    if has_position:
        out = _exit_headline(result, settings)
        return {"phase": PHASE_EXIT, "label": "EXIT WATCH", "gate": None, "gates": [], **out}

    gates = [g for g in (gates or []) if g.get("key") in GATE_LABELS]
    gates.sort(key=lambda g: GATE_ORDER.index(g["key"]))
    blocking = [g for g in gates if g.get("state") == "blocking"]
    if blocking:
        first = blocking[0]
        checklist = []
        for gate in gates:
            if gate.get("state") == "off":
                continue
            label = GATE_LABELS[gate["key"]]
            if gate.get("state") == "blocking":
                detail = f": {gate['detail']}" if gate.get("detail") else " waiting"
                checklist.append(f"{label}{detail}")
            else:
                checklist.append(f"{label} ✓")
        title = "Re-entry gates" if after_exit else "Entry gate"
        headline = f"{title} · " + " → ".join(checklist) + " → then entry signals."
        return {"phase": PHASE_REENTRY if after_exit else PHASE_GATE, "label": GATE_ACTION[first["key"]], "gate": first["key"], "gates": gates,
                "headline": headline, "chip": "gate"}

    progress = entry_progress(result, settings)
    out = _entry_headline(result, settings, progress, held_back=held_back)
    return {"phase": PHASE_ENTRY, "label": "ENTRY WATCH", "gate": None, "gates": gates,
            "progress": {"have": progress["have"], "need": progress["need"]}, **out}
