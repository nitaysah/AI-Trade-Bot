from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional

import pytz

import config as global_config
from user_config import get_user_config


CENTRAL_TZ = pytz.timezone("America/Chicago")
UTC_TZ = pytz.UTC


def _safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_high_low_buffer_pct(settings: Optional[dict] = None, fallback: Optional[float] = None) -> float:
    """Resolve the Prior Day High/Low breakout buffer as percentage points."""
    sources = []
    if isinstance(settings, dict):
        sources.extend(
            [
                settings,
                settings.get("indicator_overrides") if isinstance(settings.get("indicator_overrides"), dict) else {},
                settings.get("indicator_parameters") if isinstance(settings.get("indicator_parameters"), dict) else {},
            ]
        )

    for source in sources:
        value = source.get("DAILY_HIGH_LOW_BUFFER_PCT")
        if value not in (None, ""):
            return max(0.0, _safe_float(value, 0.0) or 0.0)

    if fallback is not None:
        return max(0.0, _safe_float(fallback, 0.0) or 0.0)

    try:
        return max(0.0, _safe_float(getattr(get_user_config(), "DAILY_HIGH_LOW_BUFFER_PCT", None), 0.0) or 0.0)
    except Exception:
        return max(0.0, _safe_float(getattr(global_config, "DAILY_HIGH_LOW_BUFFER_PCT", 0.0), 0.0) or 0.0)


def _timestamp_to_central_date(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    try:
        if isinstance(ts, datetime):
            dt = ts
        else:
            text = str(ts).strip()
            try:
                raw = float(text)
                if raw > 10**10:
                    raw /= 1000.0
                dt = datetime.fromtimestamp(raw, UTC_TZ)
            except (TypeError, ValueError):
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = UTC_TZ.localize(dt)
        return dt.astimezone(CENTRAL_TZ).strftime("%Y-%m-%d")
    except Exception:
        return None


def price_history_days_central(price_history: Iterable[dict]) -> list[tuple[str, list[dict]]]:
    days: dict[str, list[dict]] = {}
    for candle in price_history or []:
        day_key = _timestamp_to_central_date(candle.get("time"))
        if not day_key and candle.get("dt_ct") is not None:
            day_key = _timestamp_to_central_date(candle.get("dt_ct"))
        if not day_key and candle.get("dt_et") is not None:
            day_key = _timestamp_to_central_date(candle.get("dt_et"))
        if not day_key:
            continue
        days.setdefault(day_key, []).append(candle)
    return [(day, days[day]) for day in sorted(days)]


def ohlc_from_candles(candles: Iterable[dict]) -> Optional[Dict[str, float]]:
    items = list(candles or [])
    highs = [_safe_float(c.get("high"), None) for c in items]
    lows = [_safe_float(c.get("low"), None) for c in items]
    highs = [v for v in highs if v is not None]
    lows = [v for v in lows if v is not None]
    close = None
    for candle in reversed(items):
        close = _safe_float(candle.get("close"), None)
        if close is not None:
            break
    if not highs or not lows or close is None:
        return None
    return {"high": max(highs), "low": min(lows), "close": close}


def prior_day_ohlc_from_history(price_history: Iterable[dict]) -> Optional[Dict[str, Any]]:
    days = price_history_days_central(price_history)
    if len(days) < 2:
        return None
    prior_date, prior_candles = days[-2]
    ohlc = ohlc_from_candles(prior_candles)
    if not ohlc:
        return None
    return {**ohlc, "date": prior_date}


def buffered_prior_day_levels(prior_day: Optional[dict], buffer_pct: float = 0.0) -> Optional[Dict[str, Any]]:
    if not prior_day:
        return None
    high = _safe_float(prior_day.get("high"), None)
    low = _safe_float(prior_day.get("low"), None)
    close = _safe_float(prior_day.get("close"), None)
    if high is None or low is None or close is None or high <= 0 or low <= 0 or high <= low:
        return None

    buffer_pct = max(0.0, _safe_float(buffer_pct, 0.0) or 0.0)
    high_trigger = high * (1.0 + buffer_pct / 100.0)
    low_trigger = low * (1.0 - buffer_pct / 100.0)
    return {
        "high": high,
        "low": low,
        "close": close,
        "date": prior_day.get("date"),
        "buffer_pct": buffer_pct,
        "high_trigger": high_trigger,
        "low_trigger": low_trigger,
    }


def prior_day_levels_from_history(
    price_history: Iterable[dict],
    buffer_pct: Optional[float] = None,
    settings: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    resolved_buffer = resolve_high_low_buffer_pct(settings, fallback=buffer_pct)
    return buffered_prior_day_levels(prior_day_ohlc_from_history(price_history), resolved_buffer)


def dynamic_high_low_exit_levels(
    prev_h: float,
    prev_l: float,
    current_price: float,
    buffer_pct: float = 0.0,
) -> Optional[Dict[str, float]]:
    levels = buffered_prior_day_levels({"high": prev_h, "low": prev_l, "close": current_price}, buffer_pct)
    if not levels:
        return None

    day_range = max(float(levels["high"]) - float(levels["low"]), 0.0)
    if day_range <= 0:
        return None

    high_trigger = float(levels["high_trigger"])
    low_trigger = float(levels["low_trigger"])
    if float(current_price) >= high_trigger:
        tp = high_trigger + day_range
        sl = high_trigger
    else:
        tp = high_trigger
        sl = low_trigger
    return {
        "tp": tp,
        "sl": sl,
        "high_trigger": high_trigger,
        "low_trigger": low_trigger,
        "high": float(levels["high"]),
        "low": float(levels["low"]),
        "buffer_pct": float(levels["buffer_pct"]),
    }
