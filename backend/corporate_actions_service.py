"""Split-event lookup (network I/O) for the pure helpers in ``corporate_actions``.

Primary source is the Alpaca Corporate Actions API using the same keys as
market data (the user's linked keys first, then the server's keys). Yahoo
Finance is the fallback. Results are cached per symbol. Any failure returns
``status="unavailable"`` with no events; the caller must treat that as
unknown, never as "no split".

All functions are synchronous and blocking. Async callers must run them in an
executor (``asyncio.to_thread``).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from corporate_actions import SplitEvent, make_split_event

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 12 * 3600
FAILURE_TTL_SECONDS = 5 * 60
REQUEST_TIMEOUT_SECONDS = 10.0
FUTURE_WINDOW_DAYS = 14  # ex-dates are announced ahead of time

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"

_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="corp-actions")


@dataclass
class SplitLookup:
    status: str
    source: str
    events: List[SplitEvent] = field(default_factory=list)
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.status == STATUS_OK


def is_crypto_symbol(symbol: str) -> bool:
    clean = str(symbol or "").upper().replace("/", "").replace("-", "")
    return any(clean.endswith(base) for base in ("USD", "USDT", "USDC")) and len(clean) > 4


def _call_with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    future = _executor.submit(fn)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        raise TimeoutError(f"timed out after {timeout:g}s")


def _fetch_alpaca(symbol: str, start: date, end: date) -> List[SplitEvent]:
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest
    from broker_factory import get_alpaca_data_credentials

    api_key, secret_key = get_alpaca_data_credentials()
    if not api_key:
        raise RuntimeError("alpaca keys not configured")
    client = CorporateActionsClient(api_key, secret_key)
    response = client.get_corporate_actions(CorporateActionsRequest(
        symbols=[symbol],
        types=["forward_split", "reverse_split"],
        start=start,
        end=end,
    ))
    data = getattr(response, "data", response) or {}
    events: List[SplitEvent] = []
    for action_type in ("forward_splits", "reverse_splits", "forward_split", "reverse_split"):
        for item in data.get(action_type, []) or []:
            old_rate = float(getattr(item, "old_rate", 0) or 0)
            new_rate = float(getattr(item, "new_rate", 0) or 0)
            if old_rate <= 0 or new_rate <= 0:
                continue
            event = make_split_event(getattr(item, "symbol", symbol) or symbol, getattr(item, "ex_date", None), new_rate / old_rate)
            if event is not None:
                events.append(event)
    return events


def _fetch_yahoo(symbol: str, start: date, end: date) -> List[SplitEvent]:
    import yfinance as yf

    splits = yf.Ticker(symbol).splits
    events: List[SplitEvent] = []
    if splits is None or len(splits) == 0:
        return events
    for stamp, ratio in splits.items():
        ex_date = pd.Timestamp(stamp).date()
        if start <= ex_date <= end:
            event = make_split_event(symbol, ex_date, ratio)
            if event is not None:
                events.append(event)
    return events


def _dedupe(events: List[SplitEvent]) -> List[SplitEvent]:
    unique: Dict[str, SplitEvent] = {}
    for event in events:
        unique[event.key] = event
    return sorted(unique.values(), key=lambda e: e.ex_date)


def get_split_events(
    symbol: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    *,
    use_cache: bool = True,
    now: Optional[float] = None,
) -> SplitLookup:
    """Splits and reverse splits for ``symbol`` between ``start`` and ``end``."""
    clean = str(symbol or "").strip().upper()
    if not clean:
        return SplitLookup(STATUS_UNAVAILABLE, "none", [], "empty symbol")
    if is_crypto_symbol(clean):
        return SplitLookup(STATUS_OK, "crypto", [], "crypto has no splits")

    today = date.today()
    start = start or date(2000, 1, 1)
    end = end or (today + timedelta(days=FUTURE_WINDOW_DAYS))
    clock = now if now is not None else time.time()

    with _lock:
        cached = _cache.get(clean)
    if use_cache and cached and cached["expires"] > clock:
        events = [e for e in cached["events"] if start <= e.ex_date <= end]
        return SplitLookup(cached["status"], cached["source"], events, cached.get("detail", ""))

    # Always fetch the full history once so one cache entry serves every window.
    full_start, full_end = date(2000, 1, 1), today + timedelta(days=FUTURE_WINDOW_DAYS)
    errors = []
    for source, fetch in (("alpaca", _fetch_alpaca), ("yahoo", _fetch_yahoo)):
        try:
            events = _dedupe(_call_with_timeout(lambda f=fetch: f(clean, full_start, full_end), REQUEST_TIMEOUT_SECONDS))
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}")
            continue
        entry = {"status": STATUS_OK, "source": source, "events": events,
                 "expires": clock + CACHE_TTL_SECONDS, "detail": ""}
        with _lock:
            _cache[clean] = entry
        return SplitLookup(STATUS_OK, source, [e for e in events if start <= e.ex_date <= end])

    detail = "; ".join(errors)
    logger.warning("Split lookup unavailable for %s (%s)", clean, detail)
    entry = {"status": STATUS_UNAVAILABLE, "source": "none", "events": [],
             "expires": clock + FAILURE_TTL_SECONDS, "detail": detail}
    with _lock:
        _cache[clean] = entry
    return SplitLookup(STATUS_UNAVAILABLE, "none", [], detail)


def latest_split_date(symbol: str) -> Optional[date]:
    """Newest known ex-date for ``symbol`` (None if none or unavailable)."""
    lookup = get_split_events(symbol)
    return max((e.ex_date for e in lookup.events), default=None)


def events_after(symbol: str, last_processed_key: Optional[str]) -> SplitLookup:
    """Events the caller has not processed yet, oldest first.

    ``last_processed_key`` is the ``SplitEvent.key`` of the newest handled
    event; keys sort by ex-date so everything strictly newer is returned.
    """
    lookup = get_split_events(symbol=symbol)
    if not lookup.available or not last_processed_key:
        return lookup
    pending = [e for e in lookup.events if e.key > last_processed_key and e.ex_date >= _key_date(last_processed_key)]
    return SplitLookup(lookup.status, lookup.source, [e for e in pending if e.key != last_processed_key], lookup.detail)


def _key_date(key: str) -> date:
    try:
        return date.fromisoformat(key.split(":")[1])
    except Exception:
        return date(2000, 1, 1)


def clear_cache(symbol: Optional[str] = None) -> None:
    with _lock:
        if symbol is None:
            _cache.clear()
        else:
            _cache.pop(str(symbol).upper(), None)
