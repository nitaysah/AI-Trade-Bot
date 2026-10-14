"""Per-bot scan interval for the Bots engine (pure; no I/O).

``scan_interval_auto`` (follow the bot's trading timeframe) or a fixed ``scan_interval_seconds``.
A bot with neither keeps the engine default (60s, or 10s for options), so existing bots do not change.
While a bot has an order working or a fill pending, or holds a position whose exit is PRICE-based (stop, target, trailing,
levels, grid, options, a strategy preset), the interval never exceeds the engine default: the software exit watchers and
fill reconciliation depend on frequent scans. A position whose exit is purely indicator-based only changes on a completed
candle, so it follows the bot's own interval.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

MIN_SECONDS = 10
MAX_SECONDS = 14400
ALLOWED_FIXED = (10, 30, 60, 300, 600, 900, 1800, 3600, 14400)

_UNITS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hour": 3600, "d": 86400, "day": 86400}


def timeframe_seconds(timeframe: Any) -> int:
    match = re.fullmatch(r"(\d+)\s*(sec|s|min|m|hour|h|day|d)", str(timeframe or "").strip().lower())
    if not match:
        return 15 * 60
    return max(1, int(match.group(1))) * _UNITS[match.group(2)]


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "auto"}
    return bool(value)


def is_configured(settings: Mapping[str, Any]) -> bool:
    return _truthy(settings.get("scan_interval_auto")) or settings.get("scan_interval_seconds") not in (None, "", 0, "0")


def validate(settings: Mapping[str, Any]) -> Optional[str]:
    """Error text for an invalid fixed interval, else None."""
    if _truthy(settings.get("scan_interval_auto")):
        return None
    raw = settings.get("scan_interval_seconds")
    if raw in (None, "", 0, "0"):
        return None
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return "Scan interval must be a number of seconds."
    if not MIN_SECONDS <= value <= MAX_SECONDS:
        return f"Scan interval must be between {MIN_SECONDS} and {MAX_SECONDS} seconds."
    return None


def effective_seconds(settings: Mapping[str, Any], default: int, *, busy: bool = False) -> int:
    """Seconds between scans of one bot. ``default`` is the engine's own interval for this bot type."""
    default = max(1, int(default))
    if not is_configured(settings) or validate(settings):
        return default
    if _truthy(settings.get("scan_interval_auto")):
        seconds = timeframe_seconds(settings.get("timeframe"))
    else:
        seconds = int(float(settings.get("scan_interval_seconds")))
    seconds = max(MIN_SECONDS, min(MAX_SECONDS, seconds))
    return min(seconds, default) if busy else seconds


def label(settings: Mapping[str, Any], default: int) -> str:
    seconds = effective_seconds(settings, default)
    mode = "auto (timeframe)" if _truthy(settings.get("scan_interval_auto")) else ("custom" if is_configured(settings) else "default")
    return f"{seconds}s · {mode}"


def exit_watch_kind(settings: Mapping[str, Any], *, is_option: bool = False) -> str:
    """How an open position is watched for its exit: ``"indicator"`` (only a completed candle can trigger it) or ``"price"``.

    Only a plain Sell mode of ``indicator`` on a stock/crypto bot with no strategy preset is indicator-only. Everything else
    (fixed or ATR levels, percentage, pivot, high/low, swing, hybrids, trailing stops, grids, AI Autopilot, options, a
    strategy preset) can act between candles, so it is watched on the fast scan. Unknown values count as price: the safe side.
    """
    if is_option or settings.get("options_trading") or settings.get("is_option"):
        return "price"
    if _truthy(settings.get("strategy_preset_enabled")):
        return "price"
    mode = str(settings.get("sell_mode", "indicator") or "indicator").strip().lower()
    return "indicator" if mode == "indicator" else "price"


def needs_fast_scan(settings: Mapping[str, Any], *, has_position: bool, has_order: bool, is_option: bool = False) -> bool:
    """True when the bot's interval must be capped at the engine default (see the module note)."""
    if has_order:
        return True
    return bool(has_position) and exit_watch_kind(settings, is_option=is_option) == "price"
