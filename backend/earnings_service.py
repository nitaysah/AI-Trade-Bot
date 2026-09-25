"""Structured earnings calendar data for the independent Screener.

Earnings are event risk, not a technical signal.  This module keeps calendar
fetching, date validation, evidence summaries, and risk wording separate from
the technical scanner and from the news/LLM pipeline.
"""

from __future__ import annotations

import copy
import math
import time
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

from instrument_types import is_known_etf

from cache_manager import TTLCacheStore


CENTRAL_TZ = ZoneInfo("America/Chicago")
_RAW_CACHE = TTLCacheStore(maxsize=512)
_RAW_CACHE_TTL_SECONDS = 300


def _safe_number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if hasattr(value, "item"):
            value = value.item()
        number = float(value)
        return round(number, 6) if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _clean_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit]


def _is_crypto(ticker: str) -> bool:
    value = str(ticker or "").upper().replace("/", "")
    return any(value.endswith(suffix) for suffix in ("USD", "USDT", "USDC", "BTC", "ETH"))


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().date()
        except (TypeError, ValueError, AttributeError):
            pass
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).date()
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    text = str(value).strip()
    for parser in (datetime.fromisoformat,):
        try:
            return parser(text.replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            continue
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def _date_text(value: date | None) -> str:
    return value.isoformat() if value else ""


def _table_row(table: Any, labels: tuple[str, ...]) -> dict[str, Any]:
    if table is None:
        return {}
    for label in labels:
        try:
            row = table.loc[label]
            if hasattr(row, "to_dict"):
                return dict(row.to_dict())
            if isinstance(row, dict):
                return dict(row)
        except (KeyError, TypeError, AttributeError, IndexError):
            continue
    return {}


def _first_number(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _safe_number(mapping.get(key))
        if value is not None:
            return value
    return None


def _percent_value(value: Any) -> float | None:
    """Normalize provider fractions (0.0674) and percentages (6.74) alike."""
    number = _safe_number(value)
    if number is None:
        return None
    return round(number * 100, 4) if abs(number) <= 2 else round(number, 4)


def _estimate_block(mapping: dict[str, Any], prefix: str) -> dict[str, Any]:
    if not mapping:
        return {}
    result = {
        "low": _first_number(mapping, "low", "Low", f"{prefix}Low", f"{prefix} Low"),
        "average": _first_number(mapping, "avg", "average", "Average", f"{prefix}Average", f"{prefix} Average"),
        "high": _first_number(mapping, "high", "High", f"{prefix}High", f"{prefix} High"),
        "prior_year": _first_number(mapping, "yearAgoEps", "yearAgoRevenue", "prior_year"),
        "growth": _first_number(mapping, "growth"),
        "analysts": _first_number(mapping, "numberOfAnalysts", "analysts"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _history_records(table: Any) -> list[dict[str, Any]]:
    if table is None or not hasattr(table, "iterrows"):
        return []
    records: list[dict[str, Any]] = []
    try:
        rows = list(table.tail(4).iterrows())
    except (TypeError, AttributeError, IndexError):
        return []
    for index, row in rows:
        event_date = _as_date(index)
        if not event_date:
            continue
        values = row.to_dict() if hasattr(row, "to_dict") else {}
        record = {
            "date": _date_text(event_date),
            "eps_estimate": _first_number(values, "epsEstimate", "EPS Estimate"),
            "eps_actual": _first_number(values, "epsActual", "Reported EPS", "EPS Actual"),
            "eps_difference": _first_number(values, "epsDifference", "EPS Difference"),
            "surprise_pct": _percent_value(values.get("surprisePercent", values.get("Surprise %"))),
        }
        records.append({key: value for key, value in record.items() if value is not None})
    return list(reversed(records))


def _fetch_raw(ticker: str) -> dict[str, Any]:
    symbol = str(ticker or "").upper().strip()
    if not symbol:
        raise ValueError("Ticker is required")
    if _is_crypto(symbol) or is_known_etf(symbol):
        return {"symbol": symbol, "not_applicable": True, "status": "not_applicable"}

    yf_ticker = yf.Ticker(symbol)
    calendar = yf_ticker.get_calendar()
    if not isinstance(calendar, dict):
        calendar = {}
    raw_dates = calendar.get("Earnings Date") or calendar.get("Earnings date") or []
    if not isinstance(raw_dates, (list, tuple, set)):
        raw_dates = [raw_dates]
    today = datetime.now(CENTRAL_TZ).date()
    event_dates = sorted({_as_date(value) for value in raw_dates if _as_date(value)})
    future_dates = [value for value in event_dates if value >= today]
    event_date = future_dates[0] if future_dates else None

    raw = {
        "symbol": symbol,
        "status": "current" if event_date else "unavailable",
        "event_date": _date_text(event_date),
        "event_dates": [_date_text(value) for value in event_dates],
        "timing": "UNKNOWN",
        "timing_source": "not_provided",
        "exchange_timezone": "America/New_York",
        "eps_estimate": _estimate_block(calendar, "Earnings"),
        "revenue_estimate": {
            key: value for key, value in {
                "low": _first_number(calendar, "Revenue Low"),
                "average": _first_number(calendar, "Revenue Average"),
                "high": _first_number(calendar, "Revenue High"),
            }.items() if value is not None
        },
        "history": [],
        "eps_revisions": {},
        "source": "Yahoo Finance",
    }

    # Supplementary fields are best effort.  A calendar date remains useful
    # when Yahoo does not expose estimates/history for a particular symbol.
    try:
        raw["eps_estimate"].update(_estimate_block(_table_row(yf_ticker.get_earnings_estimate(), ("0q", "+1q")), "Eps"))
    except Exception:
        pass
    try:
        raw["revenue_estimate"].update(_estimate_block(_table_row(yf_ticker.get_revenue_estimate(), ("0q", "+1q")), "Revenue"))
    except Exception:
        pass
    try:
        raw["history"] = _history_records(yf_ticker.get_earnings_history())
    except Exception:
        pass
    try:
        revisions = _table_row(yf_ticker.get_eps_revisions(), ("0q", "+1q"))
        raw["eps_revisions"] = {
            key: value for key, value in {
                "up_7d": _first_number(revisions, "upLast7days"),
                "up_30d": _first_number(revisions, "upLast30days"),
                "down_7d": _first_number(revisions, "downLast7days"),
                "down_30d": _first_number(revisions, "downLast30days"),
            }.items() if value is not None
        }
    except Exception:
        pass
    return raw


def _get_raw(ticker: str, force: bool = False) -> dict[str, Any]:
    symbol = str(ticker or "").upper().strip()
    key = ("earnings", symbol)
    if not force:
        cached = _RAW_CACHE.get(key, ttl_seconds=_RAW_CACHE_TTL_SECONDS)
        if cached is not None:
            return copy.deepcopy(cached)
    lock = _RAW_CACHE.with_inflight_lock(key)
    with lock:
        if not force:
            cached = _RAW_CACHE.get(key, ttl_seconds=_RAW_CACHE_TTL_SECONDS)
            if cached is not None:
                return copy.deepcopy(cached)
        value = _fetch_raw(symbol)
        _RAW_CACHE.set(key, copy.deepcopy(value))
        return value


def _build_analysis(raw: dict[str, Any], days_to_event: int | None, options_profile: bool = False) -> dict[str, Any]:
    bullish: list[str] = []
    bearish: list[str] = []
    uncertain: list[str] = []
    estimates = raw.get("eps_estimate") or {}
    growth = _safe_number(estimates.get("growth"))
    if growth is not None:
        growth_pct = growth * 100 if abs(growth) <= 2 else growth
        if growth_pct >= 5:
            bullish.append(f"EPS growth estimate is {growth_pct:.1f}% versus the comparable period.")
        elif growth_pct <= -5:
            bearish.append(f"EPS growth estimate is {growth_pct:.1f}% versus the comparable period.")
        else:
            uncertain.append("EPS growth estimate is close to flat.")
    revisions = raw.get("eps_revisions") or {}
    up = (_safe_number(revisions.get("up_7d")) or 0) + (_safe_number(revisions.get("up_30d")) or 0)
    down = (_safe_number(revisions.get("down_7d")) or 0) + (_safe_number(revisions.get("down_30d")) or 0)
    if up > down:
        bullish.append(f"Analyst EPS revisions favor increases ({int(up)} up versus {int(down)} down).")
    elif down > up:
        bearish.append(f"Analyst EPS revisions favor decreases ({int(down)} down versus {int(up)} up).")
    elif up or down:
        uncertain.append("Recent analyst EPS revisions are balanced.")
    surprises = [_safe_number(item.get("surprise_pct")) for item in raw.get("history") or []]
    surprises = [value for value in surprises if value is not None]
    if len(surprises) >= 2:
        average_surprise = sum(surprises) / len(surprises)
        if average_surprise >= 3:
            bullish.append(f"Recent reported EPS surprises averaged {average_surprise:.1f}% above estimates.")
        elif average_surprise <= -3:
            bearish.append(f"Recent reported EPS surprises averaged {average_surprise:.1f}% below estimates.")
        else:
            uncertain.append("Recent EPS surprises have been close to estimates.")
    elif raw.get("history"):
        uncertain.append("Only limited historical earnings results are available.")
    estimate_low = _safe_number(estimates.get("low"))
    estimate_high = _safe_number(estimates.get("high"))
    estimate_avg = _safe_number(estimates.get("average"))
    if estimate_low is not None and estimate_high is not None and estimate_avg:
        spread_pct = abs(estimate_high - estimate_low) / abs(estimate_avg) * 100
        if spread_pct >= 10:
            uncertain.append(f"The EPS estimate range spans {spread_pct:.1f}% of the consensus estimate.")
    if not (bullish or bearish):
        outlook = "INSUFFICIENT"
    elif len(bullish) >= len(bearish) + 2:
        outlook = "BULLISH"
    elif len(bearish) >= len(bullish) + 2:
        outlook = "BEARISH"
    else:
        outlook = "MIXED"
    if days_to_event is None:
        risk_level = "UNKNOWN"
        risk_text = "The next earnings date is not available, so event timing risk cannot be measured."
    elif days_to_event <= 0:
        risk_level = "CRITICAL"
        risk_text = "The scheduled earnings date is today or has passed; verify the release status before relying on the date."
    elif days_to_event <= 2:
        risk_level = "CRITICAL"
        risk_text = f"Earnings are {days_to_event} day(s) away, creating a high overnight gap-risk window."
    elif days_to_event <= 7:
        risk_level = "ELEVATED"
        risk_text = f"Earnings are {days_to_event} day(s) away, so overnight gap risk is elevated."
    elif days_to_event <= 14:
        risk_level = "MODERATE"
        risk_text = f"Earnings are {days_to_event} days away; begin planning for event volatility."
    else:
        risk_level = "LOW"
        risk_text = f"Earnings are {days_to_event} days away; event risk is not yet near-term."
    if options_profile:
        uncertain.append("Options can experience implied-volatility repricing around earnings.")
    evidence_items = (bullish + bearish + uncertain)[:3]
    evidence = " ".join(item.rstrip(".") + "." for item in evidence_items)
    summary = f"Pre-earnings outlook is {outlook.lower()}. {evidence or 'There is not enough structured evidence for a directional outlook.'} {risk_text}"
    return {
        "outlook": outlook,
        "bullish_factors": bullish,
        "bearish_factors": bearish,
        "uncertainties": uncertain,
        "risk_level": risk_level,
        "risk_text": risk_text,
        "analysis_summary": _clean_text(summary, 600),
    }


def build_earnings_watch(ticker: str, previous: dict | None = None, options_profile: bool = False, force: bool = False) -> dict:
    """Return a validated, display-ready earnings event and evidence summary."""
    previous = dict(previous or {})
    checked = datetime.now(timezone.utc)
    checked_at = checked.isoformat().replace("+00:00", "Z")
    try:
        raw = _get_raw(ticker, force=force)
        if raw.get("not_applicable"):
            return {
                "enabled": True,
                "status": "not_applicable",
                "ticker": str(ticker or "").upper(),
                "checked_at": checked_at,
                "next_check_at": time.time() + _RAW_CACHE_TTL_SECONDS,
                "source": "Yahoo Finance",
            }
        event_date = _as_date(raw.get("event_date"))
        today = checked.astimezone(CENTRAL_TZ).date()
        days_to_event = (event_date - today).days if event_date else None
        analysis = _build_analysis(raw, days_to_event, options_profile=options_profile)
        payload = {
            "data_version": 2,
            "enabled": True,
            "initialized": True,
            "status": raw.get("status", "unavailable"),
            "ticker": str(ticker or "").upper(),
            "event_date": raw.get("event_date", ""),
            "event_dates": raw.get("event_dates", []),
            "event_status": "estimated" if event_date else "not_published",
            "timing": raw.get("timing", "UNKNOWN"),
            "timing_source": raw.get("timing_source", "not_provided"),
            "exchange_timezone": raw.get("exchange_timezone", "America/New_York"),
            "days_to_event": days_to_event,
            "eps_estimate": raw.get("eps_estimate", {}),
            "revenue_estimate": raw.get("revenue_estimate", {}),
            "history": raw.get("history", []),
            "eps_revisions": raw.get("eps_revisions", {}),
            "source": raw.get("source", "Yahoo Finance"),
            "checked_at": checked_at,
            "last_success_at": checked_at,
            "next_check_at": time.time() + earnings_refresh_seconds(days_to_event),
            "error": "",
            **analysis,
        }
        return payload
    except Exception as exc:
        stale = copy.deepcopy(previous)
        stale.update({
            "enabled": True,
            "status": "stale" if previous.get("initialized") else "unavailable",
            "ticker": str(ticker or "").upper(),
            "checked_at": checked_at,
            "error": f"{type(exc).__name__}: {_clean_text(exc, 180)}",
            "next_check_at": time.time() + 1800,
        })
        return stale


def earnings_refresh_seconds(days_to_event: int | None) -> int:
    if days_to_event is None:
        return 6 * 3600
    if days_to_event <= 0:
        return 3600
    if days_to_event <= 7:
        return 4 * 3600
    if days_to_event <= 30:
        return 12 * 3600
    return 24 * 3600


def get_earnings_watch_detail(ticker: str, cached: dict | None = None, options_profile: bool = False) -> dict:
    """Return popup-ready details, refreshing a missing/stale cache once."""
    if cached and cached.get("initialized") and cached.get("status") in {"current", "stale"}:
        return copy.deepcopy(cached)
    return build_earnings_watch(ticker, cached, options_profile=options_profile, force=True)


def clear_earnings_cache(ticker: str | None = None) -> None:
    if ticker:
        _RAW_CACHE.delete(("earnings", str(ticker).upper().strip()))
    else:
        _RAW_CACHE.clear()
