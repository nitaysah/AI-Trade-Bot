"""Shared, deterministic entry guards for live bots and historical backtests."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Tuple


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def is_stop_exit(reason: Any) -> bool:
    text = str(reason or "").lower()
    return "stop" in text and any(token in text for token in ("loss", "hit", "break", "invalid"))


def opening_blackout_reason(timestamp: datetime, minutes: int, *, enabled: bool = True) -> Optional[str]:
    """Return a blocker during the US regular-session opening window."""
    if not enabled or minutes <= 0 or timestamp.weekday() >= 5:
        return None
    try:
        from zoneinfo import ZoneInfo

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("America/New_York"))
        et = timestamp.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        et = timestamp
    opened = et.replace(hour=9, minute=30, second=0, microsecond=0)
    elapsed = (et - opened).total_seconds()
    if 0 <= elapsed < minutes * 60:
        remaining = max(1, int((minutes * 60 - elapsed + 59) // 60))
        return f"Market-open filter active ({remaining}m remaining)"
    return None


def stabilization_reason(
    action: str,
    bar: Mapping[str, Any],
    stop_state: Optional[Mapping[str, Any]],
    elapsed_bars: int,
    required_bars: int,
) -> Optional[str]:
    """Require time plus directional structure and VWAP/AVWAP recovery after a stop."""
    if not stop_state or required_bars <= 0:
        return None
    stopped_direction = str(stop_state.get("direction") or "").upper()
    action = str(action or "").upper()
    if stopped_direction and action != stopped_direction:
        return None
    if elapsed_bars < required_bars:
        return f"Post-stop stabilization active ({required_bars - elapsed_bars} completed candles remaining)"

    close = _number(bar.get("Close", bar.get("close")))
    open_price = _number(bar.get("Open", bar.get("open")))
    exit_price = _number(stop_state.get("exit_price"))
    reference = _number(bar.get("Auto_AVWAP"))
    if reference is None or reference <= 0:
        reference = _number(bar.get("VWAP", bar.get("vwap")))
    if close is None or close <= 0:
        return "Post-stop stabilization active (waiting for a valid completed candle)"

    if action == "SELL":
        structure_ok = (exit_price is None or close < exit_price) and (open_price is None or close < open_price)
        reclaim_ok = reference is None or reference <= 0 or close < reference
        if not structure_ok or not reclaim_ok:
            return "Post-stop stabilization active (waiting for bearish structure and VWAP/AVWAP breakdown)"
    else:
        structure_ok = (exit_price is None or close > exit_price) and (open_price is None or close > open_price)
        reclaim_ok = reference is None or reference <= 0 or close > reference
        if not structure_ok or not reclaim_ok:
            return "Post-stop stabilization active (waiting for bullish structure and VWAP/AVWAP reclaim)"
    return None


def quote_liquidity_reason(
    *,
    bid: Any,
    ask: Any,
    max_spread_pct: float,
    quote_age_seconds: Optional[float] = None,
    max_quote_age_seconds: float = 15.0,
    volume: Any = None,
    min_volume: float = 0.0,
    open_interest: Any = None,
    min_open_interest: float = 0.0,
) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    bid_value = _number(bid, 0.0) or 0.0
    ask_value = _number(ask, 0.0) or 0.0
    if bid_value <= 0 or ask_value <= 0 or ask_value < bid_value:
        return "Liquidity filter: valid two-sided bid/ask unavailable", None, None
    midpoint = (bid_value + ask_value) / 2.0
    spread_pct = ((ask_value - bid_value) / midpoint) * 100.0
    if quote_age_seconds is not None and quote_age_seconds > max_quote_age_seconds:
        return f"Liquidity filter: quote is stale ({quote_age_seconds:.1f}s)", spread_pct, midpoint
    if spread_pct > max(0.0, float(max_spread_pct)):
        return f"Liquidity filter: spread {spread_pct:.2f}% exceeds {max_spread_pct:.2f}%", spread_pct, midpoint
    volume_value = _number(volume)
    if min_volume > 0 and volume_value is not None and volume_value < min_volume:
        return f"Liquidity filter: volume {volume_value:g} below {min_volume:g}", spread_pct, midpoint
    oi_value = _number(open_interest)
    if min_open_interest > 0 and oi_value is not None and oi_value < min_open_interest:
        return f"Liquidity filter: open interest {oi_value:g} below {min_open_interest:g}", spread_pct, midpoint
    return None, spread_pct, midpoint


def historical_volume_reason(
    current_volume: Any,
    recent_volumes: Sequence[Any],
    minimum_relative_volume: float = 0.10,
) -> Optional[str]:
    """Backtest-safe liquidity proxy when historical bid/ask is unavailable."""
    current = _number(current_volume, 0.0) or 0.0
    valid = sorted(v for item in recent_volumes if (v := _number(item)) is not None and v > 0)
    if current <= 0:
        return "Liquidity filter: completed candle has no traded volume"
    if len(valid) < 5:
        return None
    median = valid[len(valid) // 2]
    threshold = median * max(0.0, float(minimum_relative_volume))
    if current < threshold:
        return f"Liquidity filter: volume {current:g} below {minimum_relative_volume:.0%} of recent median"
    return None
