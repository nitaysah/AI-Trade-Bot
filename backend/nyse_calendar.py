"""NYSE regular-session calendar.

Used to decide when a completed-candle strategy signal can be executed. When
the ``exchange_calendars`` package (already a project dependency) is
installed, its official XNYS calendar is used, which also covers special
closures. Otherwise (or for dates outside its supported range) a
deterministic rule-based fallback covers weekends, the standard full-day
holidays with weekend observance, and the scheduled early closes.
Unscheduled closures cannot be predicted by the fallback; callers that act on
the result must still confirm the broker session is open.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from dateutil.easter import easter

EASTERN = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


_CALENDAR = None
_CALENDAR_LOADED = False


def _calendar():
    """The official XNYS calendar, or None when the package is unavailable."""
    global _CALENDAR, _CALENDAR_LOADED
    if not _CALENDAR_LOADED:
        _CALENDAR_LOADED = True
        try:
            import exchange_calendars

            _CALENDAR = exchange_calendars.get_calendar("XNYS")
        except Exception:
            _CALENDAR = None
    return _CALENDAR


def _official_session(day: date):
    """``(is_session, open, close)`` from the library, or None to use the rules."""
    calendar = _calendar()
    if calendar is None:
        return None
    try:
        label = day.isoformat()
        if not calendar.is_session(label):
            return False, None, None
        return (
            True,
            calendar.session_open(label).tz_convert(EASTERN).to_pydatetime(),
            calendar.session_close(label).tz_convert(EASTERN).to_pydatetime(),
        )
    except Exception:
        return None  # outside the library's supported range


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the n-th ``weekday`` (Mon=0) of a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(holiday: date) -> Optional[date]:
    """Apply NYSE weekend observance (Saturday -> Friday, Sunday -> Monday)."""
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> frozenset:
    """Full-day NYSE closures that fall inside ``year``."""
    days = set()
    new_year = date(year, 1, 1)
    # NYSE does not observe New Year's Day on the preceding Friday when Jan 1
    # is a Saturday.
    if new_year.weekday() != 5:
        days.add(_observed(new_year))
    days.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr. Day
    days.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday
    days.add(easter(year) - timedelta(days=2))  # Good Friday
    days.add(_last_weekday(year, 5, 0))  # Memorial Day
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days.add(_observed(date(year, 7, 4)))  # Independence Day
    days.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    days.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    days.add(_observed(date(year, 12, 25)))  # Christmas
    return frozenset(d for d in days if d is not None and d.year == year)


def is_trading_day(day: date) -> bool:
    official = _official_session(day)
    if official is not None:
        return official[0]
    if day.weekday() >= 5:
        return False
    return day not in nyse_holidays(day.year)


def is_early_close(day: date) -> bool:
    """Scheduled early closes (before 4:00 PM ET)."""
    official = _official_session(day)
    if official is not None:
        return bool(official[0] and official[2].time() < REGULAR_CLOSE)
    if not is_trading_day(day):
        return False
    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    if day == thanksgiving + timedelta(days=1):
        return True
    if day.month == 7 and day.day == 3 and day.weekday() < 4:
        return True
    if day.month == 12 and day.day == 24 and day.weekday() < 4:
        return True
    return False


def session_bounds(day: date) -> Optional[Tuple[datetime, datetime]]:
    """Return (open, close) as Eastern-aware datetimes, or None if closed."""
    official = _official_session(day)
    if official is not None:
        return (official[1], official[2]) if official[0] else None
    if not is_trading_day(day):
        return None
    close_time = EARLY_CLOSE if is_early_close(day) else REGULAR_CLOSE
    return (
        datetime.combine(day, REGULAR_OPEN, tzinfo=EASTERN),
        datetime.combine(day, close_time, tzinfo=EASTERN),
    )


def next_trading_day(day: date) -> date:
    """First trading day strictly after ``day``."""
    candidate = day + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def next_session_open(after: datetime) -> datetime:
    """First regular-session open at or after ``after`` (timezone-aware)."""
    local = after.astimezone(EASTERN)
    bounds = session_bounds(local.date())
    if bounds is not None and local <= bounds[0]:
        return bounds[0]
    return session_bounds(next_trading_day(local.date()))[0]
