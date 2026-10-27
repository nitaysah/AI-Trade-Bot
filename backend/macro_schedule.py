"""Official release schedules (BLS, BEA, Federal Reserve) and calendar history for the macro modal.

The ForexFactory feed only covers the current week and carries forecasts. Government agencies publish
their schedules months ahead, so this module adds the next days of *scheduled* releases (no forecast
values) and keeps a rolling history of past events so the modal can show the last two weeks.

Pure functions (parse_*, merge_*, prune_*) take text/data and are tested without network access.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html import unescape
import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Callable
from urllib import request
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
# Agencies ask automated clients to identify themselves. The contact address comes from the environment so no
# personal address lives in the code; without one the request still identifies the app.
_CONTACT = os.getenv("MACRO_FETCH_CONTACT", "").strip()
USER_AGENT = f"BotBulls/2.0 (macro calendar{'; contact ' + _CONTACT if _CONTACT else ''})"

BLS_MONTH_URL = "https://www.bls.gov/schedule/{year}/{month:02d}_sched.htm"
BEA_ICS_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
FED_CALENDAR_URL = "https://www.federalreserve.gov/json/calendar.json"

HISTORY_DAYS = 14
AHEAD_DAYS = 7

# (substring in lower-cased official title, display title, impact, family). First match wins.
_BLS_RULES = (
    ("employment situation", "Employment Situation (Jobs Report)", "High", "jobs"),
    ("consumer price index", "Consumer Price Index (CPI)", "High", "cpi"),
    ("producer price index", "Producer Price Index (PPI)", "High", "ppi"),
    ("job openings and labor turnover", "JOLTS Job Openings", "Medium", "jolts"),
    ("employment cost index", "Employment Cost Index", "Medium", "eci"),
    ("import and export price", "Import/Export Prices", "Medium", "import"),
    ("productivity and costs", "Productivity and Costs", "Medium", "productivity"),
)
_BEA_RULES = (
    ("gross domestic product", "GDP", "High", "gdp"),
    ("gdp (", "GDP", "High", "gdp"),
    ("personal income and outlays", "Personal Income and Outlays (PCE)", "High", "pce"),
    ("international trade in goods and services", "Trade Balance", "Medium", "trade"),
)
# Fed titles that matter for markets; speeches are filtered to the Chair separately.
_FED_RULES = (
    ("fomc press conference", "FOMC Press Conference", "High", "fomc"),
    ("fomc minutes", "FOMC Minutes", "High", "fomc_minutes"),
    ("beige book", "Beige Book", "Medium", "beige"),
    ("industrial production", "Industrial Production", "Medium", "indpro"),
)

# Keywords that tie a ForexFactory title to the same family as an official event.
_FAMILY_KEYWORDS = (
    ("claims", ("unemployment claims", "jobless claims")),
    ("jobs", ("non-farm", "nonfarm", "unemployment rate", "average hourly earnings")),
    ("cpi", ("cpi",)),
    ("ppi", ("ppi",)),
    ("jolts", ("jolts",)),
    ("eci", ("employment cost",)),
    ("import", ("import price", "export price")),
    ("productivity", ("productivity", "unit labor")),
    ("gdp", ("gdp",)),
    ("pce", ("pce", "personal income", "personal spending")),
    ("trade", ("trade balance",)),
    ("fomc_minutes", ("fomc meeting minutes", "fomc minutes")),
    ("fomc", ("fomc press", "federal funds rate", "fomc statement")),
    ("beige", ("beige book",)),
    ("indpro", ("industrial production",)),
)


def _classify(title: str, rules: tuple) -> tuple[str, str, str] | None:
    lowered = title.lower()
    for needle, display, impact, family in rules:
        if needle in lowered:
            return display, impact, family
    return None


def event_family(title: str) -> str | None:
    lowered = str(title or "").lower()
    for family, keywords in _FAMILY_KEYWORDS:
        if any(k in lowered for k in keywords):
            return family
    return None


def _make_event(title: str, when: datetime, impact: str, family: str, source: str, period: str = "") -> dict[str, Any]:
    return {
        "title": title,
        "country": "USD",
        "date": when.astimezone(EASTERN).isoformat(),
        "impact": impact,
        "forecast": "",
        "previous": "",
        "source": source,
        "family": family,
        "scheduled_only": True,
        **({"period": period} if period else {}),
    }


# ── BLS (HTML month calendar) ───────────────────────────────────────────────────────────────────────

_BLS_CELL = re.compile(r'<td([^>]*)id="d(\d{2})(\d{2})"[^>]*>(.*?)</td>', re.S)
_BLS_ENTRY = re.compile(r"<strong>(.*?)</strong>(.*?)(?=<strong>|$)", re.S)
_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.I)


def parse_bls_month(html: str, year: int, month: int) -> list[dict[str, Any]]:
    """Parse one BLS monthly schedule page. Times on the page are Eastern."""
    events = []
    for attrs, mm, dd, body in _BLS_CELL.findall(html):
        if "other-month" in attrs:
            continue  # the neighbouring month's own page is the source for those days
        try:
            day = date(year, int(mm), int(dd))
        except ValueError:
            continue
        for raw_title, tail in _BLS_ENTRY.findall(body):
            title = unescape(re.sub(r"<[^>]*>", " ", raw_title)).strip()
            time_match = _TIME.search(tail)
            if not title or not time_match:
                continue  # holidays and entries without a release time
            # Releases outside the market-moving list are still scheduled macro data, so they show as Low impact.
            classified = _classify(title, _BLS_RULES) or (title, "Low", "bls_" + re.sub(r"\W+", "_", title.lower()).strip("_"))
            hour = int(time_match.group(1)) % 12 + (12 if time_match.group(3).upper() == "PM" else 0)
            when = datetime(day.year, day.month, day.day, hour, int(time_match.group(2)), tzinfo=EASTERN)
            period_text = unescape(re.sub(r"<[^>]*>", " ", tail[: time_match.start()])).strip()
            display, impact, family = classified
            events.append(_make_event(display, when, impact, family, "BLS", period_text))
    return events


# ── BEA (iCalendar) ─────────────────────────────────────────────────────────────────────────────────

def parse_bea_ics(text: str) -> list[dict[str, Any]]:
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S):
        summary = re.search(r"SUMMARY:(.*)", block)
        start = re.search(r"DTSTART[^:]*:(\d{8}T\d{6})Z", block)
        if not summary or not start:
            continue
        title = summary.group(1).replace("\\,", ",").strip()
        classified = _classify(title, _BEA_RULES)
        if not classified:
            continue
        when = datetime.strptime(start.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        display, impact, family = classified
        events.append(_make_event(display, when, impact, family, "BEA", title))
    return events


# ── Federal Reserve (JSON) ──────────────────────────────────────────────────────────────────────────

_FED_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", re.I)
# The Chair only: "Vice Chair" / "Vice Chairman for Supervision" speeches are not the market event.
_FED_CHAIR = re.compile(r"(?<!vice )\bchair(man|woman)?\b", re.I)


def parse_fed_calendar(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for item in payload.get("events", []) if isinstance(payload, dict) else []:
        title = str(item.get("title") or "").strip()
        month = str(item.get("month") or "")
        time_match = _FED_TIME.search(str(item.get("time") or ""))
        if not title or not time_match or not re.fullmatch(r"\d{4}-\d{2}", month):
            continue
        classified = _classify(title, _FED_RULES)
        if classified:
            display, impact, family = classified
        elif item.get("type") == "Speeches" and title.lower().startswith(("speech", "discussion", "testimony")) and _FED_CHAIR.search(title):
            display, impact, family = title, "Medium", "fed_speech"
        elif title.lower() == "fomc meeting":
            continue  # the press conference is the market event; the meeting row has no new time
        else:
            continue
        hour = int(time_match.group(1)) % 12 + (12 if time_match.group(3).lower() == "p" else 0)
        for day_text in re.findall(r"\d+", str(item.get("days") or "")):
            try:
                when = datetime(int(month[:4]), int(month[5:7]), int(day_text), hour, int(time_match.group(2)), tzinfo=EASTERN)
            except ValueError:
                continue
            events.append(_make_event(display, when, impact, family, "Federal Reserve"))
    return events


def is_thursday_federal_holiday(day: date) -> bool:
    """Federal holidays that can fall on a Thursday: fixed-date ones on their date, and Thanksgiving (4th Thursday
    of November). A fixed-date holiday is only observed on a Thursday when the date itself is a Thursday."""
    if day.weekday() != 3:
        return False
    if (day.month, day.day) in {(1, 1), (6, 19), (7, 4), (11, 11), (12, 25)}:
        return True
    return day.month == 11 and 22 <= day.day <= 28


def weekly_claims_events(today: date, end: date) -> list[dict[str, Any]]:
    """Initial jobless claims are released Thursday at 8:30 ET, or Wednesday when Thursday is a federal holiday.

    This is a rule-based fallback; any claims release from the calendar feed in the same week replaces it
    (see _cover_key), so an irregular shift the rule does not know about is corrected once the feed lists it."""
    events = []
    day = today
    while day <= end + timedelta(days=1):  # a holiday Thursday just past the window moves its release inside it
        if day.weekday() == 3:
            release = day - timedelta(days=1) if is_thursday_federal_holiday(day) else day
            if today <= release <= end:
                when = datetime(release.year, release.month, release.day, 8, 30, tzinfo=EASTERN)
                events.append(_make_event("Unemployment Claims", when, "Medium", "claims", CLAIMS_SOURCE))
        day += timedelta(days=1)
    return events


# ── Fetching (network; each source fails independently) ────────────────────────────────────────────

def _get_text(url: str, timeout: int = 12) -> str:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    with request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


# Agency schedules change a few times a month, so a successful download is reused for TEXT_CACHE_TTL. When a source
# is down, a copy up to TEXT_CACHE_MAX_STALE old is served and the status says so. Failures are not cached.
TEXT_CACHE_TTL = 12 * 3600
TEXT_CACHE_MAX_STALE = 3 * 86400
_text_cache: dict[str, tuple[float, str]] = {}
_text_cache_lock = threading.Lock()


class StaleText(str):
    """A cached download served because the live fetch failed."""


def cached_get_text(url: str, fetch: Callable[[str], str] | None = None, now: float | None = None) -> str:
    now = time.time() if now is None else now
    with _text_cache_lock:
        hit = _text_cache.get(url)
    if hit and now - hit[0] < TEXT_CACHE_TTL:
        return hit[1]
    try:
        text = (fetch or _get_text)(url)
    except Exception:
        if hit and now - hit[0] < TEXT_CACHE_MAX_STALE:
            return StaleText(hit[1])
        raise
    with _text_cache_lock:
        _text_cache[url] = (now, text)
    return text


def fetch_official_events(
    today: date,
    ahead_days: int = AHEAD_DAYS,
    fetch_text: Callable[[str], str] = cached_get_text,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return scheduled events from today through today+ahead_days and a per-source status map.

    Sources are downloaded in parallel and fail independently, so one slow agency costs one timeout, not the sum.
    A status of "ok" means the source's whole window was fetched live (or from a fresh cache); only then may
    callers treat its absence of an event as a cancellation."""
    end = today + timedelta(days=ahead_days)
    events: list[dict[str, Any]] = []
    status: dict[str, str] = {}

    months = sorted({(today.year, today.month), (end.year, end.month)})
    jobs: dict[str, list[tuple[str, Callable[[str], list[dict[str, Any]]]]]] = {
        "BLS": [(BLS_MONTH_URL.format(year=y, month=m), lambda text, y=y, m=m: parse_bls_month(text, y, m)) for y, m in months],
        "BEA": [(BEA_ICS_URL, parse_bea_ics)],
        "Federal Reserve": [(FED_CALENDAR_URL, lambda text: parse_fed_calendar(json.loads(text.lstrip("﻿"))))],
    }
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="macro-schedule") as pool:
        futures = {name: [pool.submit(fetch_text, url) for url, _ in items] for name, items in jobs.items()}
        for name, items in jobs.items():
            source_events, stale = [], False
            try:
                for (url, parse), future in zip(items, futures[name]):
                    text = future.result()
                    stale = stale or isinstance(text, StaleText)
                    source_events += parse(text)
                status[name] = "stale (cached copy)" if stale else "ok"
            except Exception as exc:
                status[name] = f"unavailable ({type(exc).__name__})"
            events += source_events

    events += weekly_claims_events(today, end)

    def in_window(event: dict[str, Any]) -> bool:
        return today <= datetime.fromisoformat(event["date"]).astimezone(EASTERN).date() <= end

    return [e for e in events if in_window(e)], status


# ── Merge and history ───────────────────────────────────────────────────────────────────────────────

def _event_day(event: dict[str, Any]) -> date | None:
    try:
        return datetime.fromisoformat(str(event.get("date"))).astimezone(EASTERN).date()
    except (TypeError, ValueError):
        return None


def _cover_key(family: str | None, day: date | None) -> tuple:
    """Weekly claims are matched by ISO week, so a feed release moved to Wednesday for a holiday (or any other
    shift) still replaces the rule-based Thursday entry. Every other family is matched by Eastern date."""
    if family == "claims" and day is not None:
        return family, tuple(day.isocalendar())[:2]
    return family, day


def merge_official_into_calendar(primary: list[dict[str, Any]], official: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add official events the primary (ForexFactory) feed lacks. The primary event wins when both
    describe the same release family on the same Eastern date, because it carries forecast values."""
    covered = {_cover_key(event_family(e.get("title", "")), _event_day(e)) for e in primary if e.get("country") == "USD"}
    merged = list(primary)
    seen = set()
    for event in official:
        key = _cover_key(event["family"], _event_day(event))
        if key in covered or (event["title"], event["date"]) in seen:
            continue
        seen.add((event["title"], event["date"]))
        merged.append(event)
    return merged


def _identity(event: dict[str, Any]) -> tuple[str, str, str]:
    return str(event.get("title", "")), str(event.get("country", "")), str(event.get("date", ""))


FEED_SOURCE = "feed"
CLAIMS_SOURCE = "DOL (weekly schedule)"


def fetch_coverage(today: date, feed_events: list[dict[str, Any]] | None, official_status: dict[str, str],
                   ahead_days: int = AHEAD_DAYS) -> dict[str, tuple[date, date]]:
    """Date ranges that each successful fetch covered in full, for merge_history.

    The feed range ends at the last day it actually listed, so no assumption about its week boundaries is needed.
    Pass feed_events=None when the feed failed. Official sources count only with status "ok" (a stale cached copy
    cannot prove a cancellation)."""
    end = today + timedelta(days=ahead_days)
    coverage = {name: (today, end) for name, state in official_status.items() if state == "ok"}
    coverage[CLAIMS_SOURCE] = (today, end)  # rule-generated, so always complete for the window
    feed_days = [day for day in (_event_day(e) for e in feed_events or []) if day]
    if feed_events is not None and feed_days:
        coverage[FEED_SOURCE] = (today, max(feed_days))
    return coverage


def source_key(event: dict[str, Any]) -> str:
    """Which fetch an event came from: the calendar feed, or the official source named on the entry."""
    return str(event.get("source") or "") if event.get("scheduled_only") else FEED_SOURCE


def _event_dt(event: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(event.get("date")))
        return value if value.tzinfo else value.replace(tzinfo=EASTERN)
    except (TypeError, ValueError):
        return None


def merge_history(stored: list[dict[str, Any]], fresh: list[dict[str, Any]], now: datetime | None = None,
                  coverage: dict[str, tuple[date, date]] | None = None) -> list[dict[str, Any]]:
    """Combine stored events with a fresh fetch and keep HISTORY_DAYS back.

    Fresh data replaces stored data for the same event, but a stored actual is kept when the fresh
    copy has none, so a published actual is never lost to a later feed that omitted it.

    ``coverage`` maps a source (see source_key) to the Eastern date range its fetch just returned in full. A stored
    entry from that source that is still in the future, falls in that range and is missing from the fresh fetch was
    rescheduled, retimed or cancelled, so it is dropped. Past entries and sources that failed to fetch are kept."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now.astimezone(EASTERN) - timedelta(days=HISTORY_DAYS)).date()
    fresh_ids = {_identity(e) for e in fresh}

    def superseded(event: dict[str, Any]) -> bool:
        window = (coverage or {}).get(source_key(event))
        when, day = _event_dt(event), _event_day(event)
        return bool(window and when and day and when > now and window[0] <= day <= window[1]
                    and _identity(event) not in fresh_ids)

    combined = {_identity(e): e for e in stored if isinstance(e, dict) and not superseded(e)}
    for event in fresh:
        key = _identity(event)
        previous = combined.get(key) or {}
        merged = {**previous, **event}
        for field in ("actual", "actual_source", "actual_period", "surprise", "macro_read", "actual_note"):
            if previous.get(field) and not event.get(field):
                merged[field] = previous[field]
        combined[key] = merged
    kept = [e for e in combined.values() if (_event_day(e) or date.min) >= cutoff]
    kept = drop_covered_official(kept)
    kept.sort(key=lambda e: str(e.get("date", "")))
    return kept


def drop_covered_official(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove official schedule-only entries once a calendar-feed (forecast-bearing) USD event covers the same
    release family on the same Eastern date. merge_official_into_calendar does this for a fresh fetch; this also
    catches official copies saved to history before the feed published that release."""
    covered = {
        _cover_key(event_family(e.get("title", "")), _event_day(e))
        for e in events
        if not e.get("scheduled_only") and e.get("country") == "USD"
    }
    return [e for e in events if not (e.get("scheduled_only") and _cover_key(e.get("family"), _event_day(e)) in covered)]


def prune_stored_for_display(events: list[dict[str, Any]], now: datetime | None = None, ahead_days: int = AHEAD_DAYS) -> list[dict[str, Any]]:
    """Window used by the modal: HISTORY_DAYS back to ahead_days forward (Eastern dates)."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(EASTERN).date()
    low, high = today - timedelta(days=HISTORY_DAYS), today + timedelta(days=ahead_days)
    return [e for e in events if (day := _event_day(e)) and low <= day <= high]


# ── History file (no TTL; the generic card cache expires, history must not) ───────────────────────

def load_history(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
        events = doc.get("events") if isinstance(doc, dict) else None
        return events if isinstance(events, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        return []  # a corrupt file must not break the calendar; the next save replaces it


def save_history(path: str, events: list[dict[str, Any]]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".macro_history.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"saved_at": datetime.now(timezone.utc).isoformat(), "events": events}, handle, separators=(",", ":"))
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
