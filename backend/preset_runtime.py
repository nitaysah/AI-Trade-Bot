"""Deterministic live-bot rules for strategy presets (pure; no broker, no I/O).

The engine calls these helpers around the shared decision from
``strategy_presets``. Keeping them here makes the failure paths testable with
plain dicts and keeps the large trading loop edits small.

Software-only protection: the protective stop is watched by the engine loop,
so it depends on the process staying up and on a usable live quote. No
broker-side stop order is attached.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Tuple

import nyse_calendar
from corporate_actions import SplitEvent, has_pending_order, quantity_matches, rescale_price_levels

OWNS_KEY = "strategy_preset_owns_position"
POLICY_KEY = "strategy_preset_position_policy"  # "adopt" | "unmanaged" (default)
LAST_ENTRY_KEY = "strategy_preset_last_entry_key"
STOP_KEY = "strategy_preset_stop"
PAUSE_KEY = "corporate_action_pause"
LAST_CORPORATE_ACTION_KEY = "corporate_action_last_key"
LAST_QTY_KEY = "last_known_position_qty"

# A just-accepted entry order takes a moment to show up as a position. Ownership
# recorded at acceptance must survive that gap.
ENTRY_GRACE_SECONDS = 600

STATE_FLAT = "flat"
STATE_MANAGED = "managed"
STATE_UNMANAGED = "unmanaged"


EXTENDED_OPEN = time(4, 0)
EXTENDED_CLOSE = time(20, 0)
EXTENDED_EARLY_CLOSE = time(17, 0)


def _ct_hhmm(moment: datetime) -> str:
    """An Eastern ``datetime`` as a Central ``HH:MM`` (one hour earlier)."""
    return f"{moment - timedelta(hours=1):%H:%M}"


def window_reason(now: datetime, session: str = "Regular") -> Optional[str]:
    """Why an order may not be sent right now, or None inside the chosen session.

    Orders are never sent overnight, on exchange holidays or after an early
    close. With the default Regular session they are sent only between the
    regular open and close; with the Extended session the pre-market and
    after-hours windows are also allowed (04:00-20:00 Eastern, 17:00 on early closes).
    """
    local = now.astimezone(nyse_calendar.EASTERN)
    bounds = nyse_calendar.session_bounds(local.date())
    if bounds is None:
        return "Waiting for the next trading session (the exchange is closed today)."
    if str(session or "Regular").strip().lower() == "extended":
        early = nyse_calendar.is_early_close(local.date())
        start = datetime.combine(local.date(), EXTENDED_OPEN, tzinfo=nyse_calendar.EASTERN)
        end = datetime.combine(local.date(), EXTENDED_EARLY_CLOSE if early else EXTENDED_CLOSE, tzinfo=nyse_calendar.EASTERN)
        if local < start:
            return f"Waiting for the extended session to open ({_ct_hhmm(start)} CT)."
        if local >= end:
            return "The extended session is closed; the order waits for the next one."
        return None
    if local < bounds[0]:
        return f"Waiting for the regular session to open ({_ct_hhmm(bounds[0])} CT)."
    if local >= bounds[1]:
        return "The regular session is closed; the order waits for the next open."
    return None


# ---------------------------------------------------------------------------
# Scan timing (strategy ``per_candle`` scan policy)
# ---------------------------------------------------------------------------
# A daily candle is complete five minutes after the session close (same rule as
# ``strategy_presets.completed_bars``). While a bot is flat the signal cannot change before
# the next daily candle completes, so one evaluation per completed candle is enough.
# Anything that needs live prices (an open position, a pending order) keeps the normal
# refresh while the session is open; nothing here weakens protective-stop monitoring.
DAILY_COMPLETION_DELAY = timedelta(minutes=5)


def latest_completed_daily_date(now: datetime) -> date:
    """Trading date of the newest completed daily candle at ``now``."""
    local = now.astimezone(nyse_calendar.EASTERN)
    day = local.date()
    for _ in range(14):
        bounds = nyse_calendar.session_bounds(day)
        if bounds is not None and local >= bounds[1] + DAILY_COMPLETION_DELAY:
            return day
        day -= timedelta(days=1)
    return day


def _window_start(day: date, session: str) -> Optional[datetime]:
    bounds = nyse_calendar.session_bounds(day)
    if bounds is None:
        return None
    if str(session or "Regular").strip().lower() == "extended":
        return datetime.combine(day, EXTENDED_OPEN, tzinfo=nyse_calendar.EASTERN)
    return bounds[0]


def next_evaluation_at(now: datetime, session: str = "Regular") -> datetime:
    """When a flat bot next needs an evaluation: the next daily candle completion, or - if that moment
    falls outside the bot's trading window - the next time the window opens."""
    local = now.astimezone(nyse_calendar.EASTERN)
    day = local.date()
    ready = None
    for _ in range(14):
        bounds = nyse_calendar.session_bounds(day)
        if bounds is not None and bounds[1] + DAILY_COMPLETION_DELAY > local:
            ready = bounds[1] + DAILY_COMPLETION_DELAY
            break
        day += timedelta(days=1)
    if ready is None:
        return local + timedelta(hours=1)
    if window_reason(ready, session) is None:
        return ready
    day = ready.date()
    for _ in range(14):
        start = _window_start(day, session)
        if start is not None and start >= ready:
            return start
        day += timedelta(days=1)
    return ready


def scan_fingerprint(settings: Mapping[str, Any]) -> Tuple[str, str, str, str]:
    """What the idle marker depends on: if any of these change the bot is evaluated again."""
    return (
        str(settings.get("strategy_preset") or ""),
        str(settings.get("strategy_preset_variant") or ""),
        str(settings.get("timeframe") or ""),
        str(settings.get("market_session") or "Regular").lower(),
    )


def _candle_trading_date(meta: Mapping[str, Any]) -> Optional[date]:
    """Trading date of the candle an evaluation used (daily stamps may be midnight ET or midnight UTC)."""
    raw = meta.get("candle_open_ts")
    if raw is None:
        raw = str(meta.get("candle_key") or "").rsplit(":", 1)[-1]
    try:
        stamp = float(raw)
        moment = datetime.fromtimestamp(stamp, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return (moment + timedelta(hours=6)).astimezone(nyse_calendar.EASTERN).date()


def can_mark_scan_done(
    result: Mapping[str, Any], state: str, *, has_position: bool, has_open_order: bool, now: datetime,
) -> bool:
    """True only when a flat bot genuinely has nothing to do until the next candle.

    A valid READY evaluation whose decision is HOLD, made on the newest completed daily candle. A blocked
    BUY/SELL (halt, window, guard), missing, stale or lagging data, a position, a pending order or a paused
    split reconcile all keep the normal refresh."""
    info = result.get("strategy_preset") or {}
    if not (
        state == STATE_FLAT
        and not has_position
        and not has_open_order
        and info.get("status") == "READY"
        and info.get("action") == "HOLD"
        and result.get("action") == "HOLD"
        and result.get("evaluation_status", "valid") == "valid"
    ):
        return False
    meta = result.get("candle_meta") or {}
    if meta.get("candle_status", "closed") != "closed":
        return False
    candle_date = _candle_trading_date(meta)
    return candle_date is not None and candle_date >= latest_completed_daily_date(now)


def scan_marker(settings: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    session = settings.get("market_session") or "Regular"
    return {
        "fingerprint": scan_fingerprint(settings),
        "epoch": latest_completed_daily_date(now).isoformat(),
        "marked_at": now.isoformat(),
        "next_evaluation_at": next_evaluation_at(now, session).isoformat(),
    }


def scan_should_skip(
    marker: Optional[Mapping[str, Any]],
    settings: Mapping[str, Any],
    now: datetime,
    *,
    has_position: bool,
    has_open_order: bool,
) -> bool:
    """Skip this scan: the bot is flat, idle, and no new daily candle has completed since the marker."""
    if not marker or has_position or has_open_order or settings.get(PAUSE_KEY):
        return False
    if tuple(marker.get("fingerprint") or ()) != scan_fingerprint(settings):
        return False
    return marker.get("epoch") == latest_completed_daily_date(now).isoformat()


def _entry_is_recent(settings: Mapping[str, Any], now: Optional[datetime]) -> bool:
    if now is None:
        return False
    stored = (settings.get(STOP_KEY) or {}).get("entry_time")
    try:
        entered = datetime.fromisoformat(str(stored))
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=now.tzinfo)
        return 0 <= (now - entered).total_seconds() < ENTRY_GRACE_SECONDS
    except (TypeError, ValueError):
        return False


def sync_position_state(
    settings: MutableMapping[str, Any],
    has_position: bool,
    qty: float = 0.0,
    *,
    has_open_buy_order: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """Track whether the strategy owns the open position.

    * flat: ownership and the stored stop are cleared, unless an entry order is
      still working or was accepted within the last few minutes (the position
      may not be visible yet).
    * holding a position the strategy opened: managed.
    * holding a position it did not open: managed only if the user chose
      ``adopt``; otherwise left alone (``unmanaged``).
    """
    if not has_position:
        if has_open_buy_order or (settings.get(OWNS_KEY) and _entry_is_recent(settings, now)):
            return STATE_FLAT
        settings[OWNS_KEY] = False
        settings.pop(STOP_KEY, None)
        settings[LAST_QTY_KEY] = 0.0
        return STATE_FLAT
    settings[LAST_QTY_KEY] = float(qty or 0.0)
    if settings.get(OWNS_KEY):
        return STATE_MANAGED
    if str(settings.get(POLICY_KEY) or "unmanaged").lower() == "adopt":
        settings[OWNS_KEY] = True
        return STATE_MANAGED
    return STATE_UNMANAGED


def _hold(result: MutableMapping[str, Any], reason: str) -> None:
    result["action"] = "HOLD"
    result["reason"] = reason


def gate_result(
    settings: MutableMapping[str, Any],
    result: MutableMapping[str, Any],
    *,
    state: str,
    now: datetime,
) -> None:
    """Apply the preset's runtime gates to an evaluation result (in place).

    Protective exits are handled separately and are never blocked here.
    """
    info = result.get("strategy_preset") or {}
    action = result.get("action")
    if state == STATE_UNMANAGED and action == "SELL":
        _hold(result, "Existing position is left unmanaged: the strategy only manages positions it opens.")
        return
    if action in ("BUY", "SELL"):
        reason = window_reason(now, settings.get("market_session") or "Regular")
        if reason:
            _hold(result, reason)
            return
    if result.get("action") == "BUY":
        key = info.get("signal_key")
        if key and key == settings.get(LAST_ENTRY_KEY):
            _hold(result, "This entry signal was already acted on; waiting for a fresh signal.")
        elif settings.get(PAUSE_KEY):
            _hold(result, "Entries are paused while a stock split is being reconciled with the broker.")


def protective_stop(
    settings: Mapping[str, Any], info: Mapping[str, Any], average_cost: float
) -> Optional[Tuple[float, float, float]]:
    """``(stop_price, atr_multiple, atr)`` from the stored entry ATR.

    The stop is stored as an ATR distance and re-anchored to the broker's
    average cost, so it stays correct across stock splits.
    """
    stored = settings.get(STOP_KEY) or {}
    try:
        multiple = float(stored.get("stop_atr_mult") or info.get("stop_atr_mult") or 3.0)
        atr = float(stored.get("entry_atr") or info.get("atr") or 0.0)
        cost = float(average_cost or 0.0)
    except (TypeError, ValueError):
        return None
    if atr <= 0 or cost <= 0 or multiple <= 0:
        return None
    return cost - multiple * atr, multiple, atr


def ensure_stop_recorded(settings: MutableMapping[str, Any], info: Mapping[str, Any], now_iso: str) -> None:
    """Store the entry ATR for an adopted position that has none yet."""
    if settings.get(STOP_KEY) or not info.get("atr"):
        return
    settings[STOP_KEY] = {
        "stop_atr_mult": info.get("stop_atr_mult"),
        "entry_atr": info.get("atr"),
        "entry_time": now_iso,
        "adopted": True,
    }


def record_entry(settings: MutableMapping[str, Any], result: Mapping[str, Any], now_iso: str) -> None:
    """Persist ownership, the signal key and the entry ATR after an accepted buy."""
    info = result.get("strategy_preset") or {}
    settings[LAST_ENTRY_KEY] = info.get("signal_key")
    settings[OWNS_KEY] = True
    settings[STOP_KEY] = {
        "stop_atr_mult": info.get("stop_atr_mult"),
        "entry_atr": info.get("atr"),
        "entry_time": now_iso,
    }


# ── Stock splits ────────────────────────────────────────────────────────────

def baseline_corporate_actions(settings: MutableMapping[str, Any], events: List[SplitEvent], today) -> bool:
    """First time a bot is seen: mark already-effective splits as processed.

    Saved state created after a split is already on the new basis, so those
    events must not be applied. Returns True if the settings changed.
    """
    if settings.get(LAST_CORPORATE_ACTION_KEY):
        return False
    past = [event for event in events if event.ex_date <= today]
    if not past:
        return False
    settings[LAST_CORPORATE_ACTION_KEY] = max(past, key=lambda e: (e.ex_date, e.key)).key
    return True


def apply_split_event(
    settings: MutableMapping[str, Any],
    event: SplitEvent,
    *,
    broker_qty: float,
    has_position: bool,
) -> Dict[str, Any]:
    """Rescale saved prices/quantities for one split and verify the broker.

    Returns ``{"status": "applied" | "review", "changes": {...}, "message": str}``.
    On ``review`` nothing is rescaled and the event stays unprocessed, so the
    next scan retries (the broker may not have booked the split yet) while
    entries remain paused. This never liquidates anything.
    """
    rescaled, changes = rescale_price_levels(settings, event)
    problems: List[str] = []
    if has_pending_order(settings):
        problems.append("an order was in flight when the split took effect")
    if has_position:
        previous = float(settings.get(LAST_QTY_KEY) or 0.0)
        if previous > 0:
            expected = previous * event.ratio
            if not quantity_matches(expected, broker_qty):
                problems.append(f"broker quantity {broker_qty:g} does not match the expected {expected:g}")
    if problems:
        settings[PAUSE_KEY] = event.key
        return {
            "status": "review",
            "changes": {},
            "message": f"{event.symbol} {event.label} ({event.ex_date}): entries stay paused for review: " + "; ".join(problems) + ".",
        }
    for key, (_, after) in changes.items():
        if "." in key:
            parent, child = key.split(".", 1)
            if isinstance(settings.get(parent), dict):
                settings[parent][child] = after
        else:
            settings[key] = after
    settings[LAST_QTY_KEY] = float(broker_qty) if has_position else 0.0
    settings[LAST_CORPORATE_ACTION_KEY] = event.key
    settings.pop(PAUSE_KEY, None)
    detail = f"{len(changes)} saved level(s) rescaled" if changes else "no saved price levels needed rescaling"
    position = f"; position now {broker_qty:g} sh" if has_position else ""
    return {
        "status": "applied",
        "changes": changes,
        "message": f"{event.symbol} {event.label} effective {event.ex_date}: {detail}{position}.",
    }
