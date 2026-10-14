"""Session Rider schedule rules (pure; shared by Backtest, Bots and Screener).

A Session Rider bot buys at one time of day and sells at another. Buy earlier than sell is an
intraday hold; buy later than sell holds overnight and sells at the next session's sell time.
Times are exchange (ET) times:

* ``open+N`` / ``close-N`` (N = 1..5) follow the real open/close, so early closes move them.
* ``HH:MM`` clock times between 9:31 and 15:59 (1-minute steps). On an early-close day a clock
  time later than close - 5 runs at close - 5.

Nothing here does I/O; every function takes the clock as an argument.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

import nyse_calendar

KIND = "schedule"
PRESET_ID = "session_rider"

BUY_KEY = "session_buy_time"
SELL_KEY = "session_sell_time"
SKIP_WEEKEND_KEY = "session_skip_weekend"
LOSS_PAUSE_KEY = "session_loss_pause_after"
SETTING_KEYS = (BUY_KEY, SELL_KEY, SKIP_WEEKEND_KEY, LOSS_PAUSE_KEY)

BUY_DEFAULT = "close-5"
SELL_DEFAULT = "open+5"
ANCHOR_STEPS = (1, 2, 3, 4, 5)
TIGHT_STEPS = (1, 2, 3, 4)  # shown with a timing-risk note
EARLIEST = time(9, 31)
LATEST = time(15, 59)
CLOCK_MAX_BEFORE_CLOSE = timedelta(minutes=5)
ENTRY_WINDOW = timedelta(minutes=5)
SUBMIT_CUTOFF = timedelta(seconds=30)  # never submit later than close - 30s
PREWAKE = timedelta(seconds=20)
ALERT_GRACE = timedelta(minutes=10)  # Screener: an event older than this is not alerted
LOSS_PAUSE_DEFAULT = 3
LOSS_PAUSE_MIN = 3
LOSS_PAUSE_MAX = 50

MODE_INTRADAY = "intraday"
MODE_OVERNIGHT = "overnight"

_NOMINAL_DAY = date(2000, 1, 3)
_NOMINAL_OPEN = datetime.combine(_NOMINAL_DAY, nyse_calendar.REGULAR_OPEN)
_NOMINAL_CLOSE = datetime.combine(_NOMINAL_DAY, nyse_calendar.REGULAR_CLOSE)
_SCAN_DAYS = 12
CENTRAL = ZoneInfo("America/Chicago")


def normalize_time(value: Any) -> Optional[str]:
    """``open+N``, ``close-N`` (N = 1..5) or ``HH:MM`` ET in [9:31, 15:59]; else None."""
    v = str(value if value is not None else "").strip().lower().replace(" ", "").replace("−", "-")
    for anchor, sign in (("open", "+"), ("close", "-")):
        if v.startswith(anchor):
            rest = v[len(anchor):]
            if not rest.startswith(sign):
                return None
            try:
                n = int(rest[1:])
            except ValueError:
                return None
            return f"{anchor}{sign}{n}" if n in ANCHOR_STEPS else None
    try:
        hh, mm = v.split(":")
        t = time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None
    if not (EARLIEST <= t <= LATEST):
        return None
    return f"{t.hour:02d}:{t.minute:02d}"


def _nominal(setting: str) -> datetime:
    """The time on a regular 9:30-16:00 day (used for mode, labels and data resolution)."""
    if setting.startswith("open+"):
        return _NOMINAL_OPEN + timedelta(minutes=int(setting[5:]))
    if setting.startswith("close-"):
        return _NOMINAL_CLOSE - timedelta(minutes=int(setting[6:]))
    hh, mm = (int(x) for x in setting.split(":"))
    return datetime.combine(_NOMINAL_DAY, time(hh, mm))


def needs_minute_bars(*settings: str) -> bool:
    """True when any time is off the 5-minute grid (the backtest then needs 1-minute bars)."""
    return any(_nominal(s).minute % 5 for s in settings if s)


def et_ct_label(setting: str) -> str:
    et = _nominal(setting)
    ct = et - timedelta(hours=1)
    return f"{ct.strftime('%-I:%M %p')} CT"


def is_tight(setting: str) -> bool:
    return any(setting == f"{a}{s}{n}" for a, s in (("open", "+"), ("close", "-")) for n in TIGHT_STEPS)


def describe(setting: str) -> str:
    if setting.startswith("open+"):
        return f"Open + {setting[5:]} min"
    if setting.startswith("close-"):
        return f"Close − {setting[6:]} min"
    return "Custom"


def time_options() -> List[Dict[str, Any]]:
    """Dropdown options: open+1..5, the 15-minute grid 9:45-15:45, close-5..1."""
    values = [f"open+{n}" for n in ANCHOR_STEPS]
    grid = _NOMINAL_OPEN + timedelta(minutes=15)
    while grid.time() <= time(15, 45):
        values.append(grid.strftime("%H:%M"))
        grid += timedelta(minutes=15)
    values += [f"close-{n}" for n in sorted(ANCHOR_STEPS, reverse=True)]
    out = []
    for v in values:
        head = describe(v) if not v[0].isdigit() else ""
        label = f"{head} · {et_ct_label(v)}" if head else et_ct_label(v)
        if v == BUY_DEFAULT:
            label += " · default buy"
        elif v == SELL_DEFAULT:
            label += " · default sell"
        out.append({"value": v, "label": label, "tight": is_tight(v)})
    return out


def classify_mode(buy: Optional[str], sell: Optional[str]) -> Optional[str]:
    if not buy or not sell:
        return None
    b, s = _nominal(buy), _nominal(sell)
    if b == s:
        return None
    return MODE_INTRADAY if b < s else MODE_OVERNIGHT


def _loss_pause(value: Any) -> Optional[int]:
    if value in (None, ""):
        return LOSS_PAUSE_DEFAULT
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if LOSS_PAUSE_MIN <= n <= LOSS_PAUSE_MAX else None


def normalized_settings(settings: Mapping[str, Any]) -> Dict[str, Any]:
    buy = normalize_time(settings.get(BUY_KEY) or BUY_DEFAULT)
    sell = normalize_time(settings.get(SELL_KEY) or SELL_DEFAULT)
    return {
        BUY_KEY: buy,
        SELL_KEY: sell,
        SKIP_WEEKEND_KEY: bool(settings.get(SKIP_WEEKEND_KEY, False)),
        LOSS_PAUSE_KEY: _loss_pause(settings.get(LOSS_PAUSE_KEY)),
        "mode": classify_mode(buy, sell),
    }


def validate(settings: Mapping[str, Any]) -> List[str]:
    errors = []
    raw_buy, raw_sell = settings.get(BUY_KEY) or BUY_DEFAULT, settings.get(SELL_KEY) or SELL_DEFAULT
    buy, sell = normalize_time(raw_buy), normalize_time(raw_sell)
    if buy is None:
        errors.append(f"Buy time '{raw_buy}' is not valid: use Open + 1-5, Close − 1-5 or a time between 9:31 and 15:59 ET.")
    if sell is None:
        errors.append(f"Sell time '{raw_sell}' is not valid: use Open + 1-5, Close − 1-5 or a time between 9:31 and 15:59 ET.")
    if buy and sell and classify_mode(buy, sell) is None:
        errors.append("Buy time and sell time must be different.")
    if _loss_pause(settings.get(LOSS_PAUSE_KEY)) is None:
        errors.append(f"Pause after losses in a row must be a whole number from {LOSS_PAUSE_MIN} to {LOSS_PAUSE_MAX}.")
    return errors


def resolve(day: date, setting: str) -> Optional[Dict[str, Any]]:
    """ET-aware time for ``setting`` on ``day`` (None on a holiday/weekend)."""
    bounds = nyse_calendar.session_bounds(day)
    if bounds is None:
        return None
    open_et, close_et = bounds
    if setting.startswith("open+"):
        at, adjusted = open_et + timedelta(minutes=int(setting[5:])), False
    elif setting.startswith("close-"):
        at, adjusted = close_et - timedelta(minutes=int(setting[6:])), False
    else:
        hh, mm = (int(x) for x in setting.split(":"))
        at = datetime.combine(day, time(hh, mm), tzinfo=nyse_calendar.EASTERN)
        # A clock time past an early close runs at close - 5; on a normal day 9:31-15:59 all stand.
        early = close_et.time() < nyse_calendar.REGULAR_CLOSE
        latest = close_et - CLOCK_MAX_BEFORE_CLOSE
        adjusted = early and at > latest
        if adjusted:
            at = latest
    return {"at": at, "adjusted": adjusted, "open": open_et, "close": close_et}


def skip_reason(day: date, settings: Mapping[str, Any]) -> Optional[str]:
    """Why ``day`` has no entry (None when it does). ``settings`` must be normalized."""
    buy, sell, mode = settings[BUY_KEY], settings[SELL_KEY], settings["mode"]
    if not nyse_calendar.is_trading_day(day):
        return "market closed"
    if mode == MODE_INTRADAY:
        b, s = resolve(day, buy), resolve(day, sell)
        if b["at"] >= s["at"]:
            return "early close leaves no time between buy and sell"
    elif settings.get(SKIP_WEEKEND_KEY) and (nyse_calendar.next_trading_day(day) - day).days > 1:
        return "skip entries before weekends/holidays is on"
    return None


def buy_event(day: date, settings: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Entry on ``day`` with its window and paired sell, or None when the day has no entry."""
    if settings.get("mode") is None or skip_reason(day, settings):
        return None
    b = resolve(day, settings[BUY_KEY])
    window_end = min(b["at"] + ENTRY_WINDOW, b["close"] - SUBMIT_CUTOFF)
    return {
        "session": day.isoformat(),
        "at": b["at"],
        "window_end": window_end,
        "adjusted": b["adjusted"],
        "sell": sell_event(day, settings),
    }


def sell_event(entry_day: date, settings: Mapping[str, Any]) -> Dict[str, Any]:
    """The sell that closes an entry made on ``entry_day``."""
    day = entry_day if settings["mode"] == MODE_INTRADAY else nyse_calendar.next_trading_day(entry_day)
    s = resolve(day, settings[SELL_KEY])
    return {"session": day.isoformat(), "at": s["at"], "adjusted": s["adjusted"], "close": s["close"]}


def _et(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(nyse_calendar.EASTERN)


def next_buy_event(now: datetime, settings: Mapping[str, Any], *, include_open_window: bool = True) -> Optional[Dict[str, Any]]:
    """The current (window still open) or next entry."""
    local = _et(now)
    for i in range(_SCAN_DAYS):
        ev = buy_event(local.date() + timedelta(days=i), settings)
        if ev is None:
            continue
        if ev["at"] >= local or (include_open_window and ev["at"] <= local < ev["window_end"]):
            return ev
    return None


def last_buy_event(now: datetime, settings: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    local = _et(now)
    for i in range(_SCAN_DAYS):
        ev = buy_event(local.date() - timedelta(days=i), settings)
        if ev is not None and ev["at"] <= local:
            return ev
    return None


def entry_due(now: datetime, settings: Mapping[str, Any], armed_at: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """Today's entry when ``now`` is inside its window and the window opened after ``armed_at``."""
    local = _et(now)
    ev = buy_event(local.date(), settings)
    if ev is None or not (ev["at"] <= local < ev["window_end"]):
        return None
    if armed_at is not None and ev["at"] < _et(armed_at):
        return None
    return ev


def sell_due_at(entry_session: Optional[str], now: datetime, settings: Mapping[str, Any]) -> Optional[datetime]:
    """When a held position must be sold. Unknown entry day: today's sell time (or the next one)."""
    local = _et(now)
    if entry_session:
        try:
            return sell_event(date.fromisoformat(entry_session), settings)["at"]
        except (ValueError, TypeError):
            pass
    day = local.date()
    for _ in range(_SCAN_DAYS):
        s = resolve(day, settings[SELL_KEY])
        if s is not None:
            return s["at"]
        day += timedelta(days=1)
    return None


def schedule_phase(now: datetime, settings: Mapping[str, Any]) -> Dict[str, Any]:
    """Where the schedule is now (independent of real positions): in a hold window or flat."""
    local = _et(now)
    mode = settings.get("mode")
    if mode is None:
        return {"phase": "INVALID", "next_event": None, "next_at": None}
    last = last_buy_event(local, settings)
    if last is not None and local < last["sell"]["at"]:
        phase = "HOLDING_INTRADAY" if mode == MODE_INTRADAY else "HOLDING_OVERNIGHT"
        return {"phase": phase, "next_event": "SELL", "next_at": last["sell"]["at"], "entry": last}
    nxt = next_buy_event(local, settings, include_open_window=False)
    return {"phase": "FLAT", "next_event": "BUY" if nxt else None, "next_at": nxt["at"] if nxt else None, "entry": nxt}


def due_alerts(now: datetime, settings: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Schedule events that happened within the last ``ALERT_GRACE`` (Screener alerts)."""
    local = _et(now)
    out = []
    for i in range(_SCAN_DAYS):
        ev = buy_event(local.date() - timedelta(days=i), settings)
        if ev is None:
            continue
        for kind, at, session in (("BUY", ev["at"], ev["session"]), ("SELL", ev["sell"]["at"], ev["sell"]["session"])):
            if at <= local < at + ALERT_GRACE:
                out.append({"event": kind, "at": at, "key": f"{session}:{kind}", "entry_session": ev["session"]})
    return sorted(out, key=lambda e: e["at"])


def next_event_time(now: datetime, settings: Mapping[str, Any]) -> Optional[datetime]:
    """The next buy or sell time strictly after ``now`` (Screener wake-up)."""
    local = _et(now)
    times = []
    for i in range(-1, _SCAN_DAYS):
        ev = buy_event(local.date() + timedelta(days=i), settings)
        if ev is None:
            continue
        times += [t for t in (ev["at"], ev["sell"]["at"]) if t > local]
    return min(times) if times else None


def fmt_et_ct(at: Optional[datetime]) -> str:
    if at is None:
        return ""
    ct = at.astimezone(CENTRAL)
    return f"{ct.strftime('%a %b %-d, %-I:%M %p')} CT"
