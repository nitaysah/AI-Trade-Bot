import asyncio
import hashlib
import os
import tempfile
from datetime import datetime
import math
import pytz
import time

from broker_factory import create_broker
from trader import evaluate_trade
from swing_strategy import build_swing_exit
from options_direction import normalize_options_type, VALID_OPTION_DIRECTIONS, options_type_to_contract_type
from prior_day_high_low import (
    buffered_prior_day_levels,
    dynamic_high_low_exit_levels as _buffered_dynamic_high_low_exit_levels,
    price_history_days_central,
    prior_day_ohlc_from_history as _shared_prior_day_ohlc_from_history,
    resolve_high_low_buffer_pct,
)
from firebase_admin import firestore
import config as global_config
from user_config import UserConfig, set_user_config
from trade_guards import is_stop_exit, opening_blackout_reason, quote_liquidity_reason, stabilization_reason

BOT_CONSECUTIVE_LOSS_PAUSE_THRESHOLD = 3


def _local_settings_path(uid: str) -> str:
    """Use a stable non-identifying filename for the private local fallback."""
    digest = hashlib.sha256(str(uid or "").encode("utf-8")).hexdigest()[:32]
    return os.path.join("data", f"user_settings_{digest}.json")


def _is_global_risk_entry_attempt(result: dict, has_position: bool) -> bool:
    result = result or {}
    return bool(
        result.get("action") == "BUY"
        or (result.get("action") == "SELL" and not has_position)
        or result.get("continuous_grid_side") == "entry"
        or result.get("option_intent") in {"OPEN_CALL", "OPEN_PUT"}
    )

def parse_timeframe_to_seconds(timeframe: str) -> int:
    tf = timeframe.upper().strip()
    if tf in ["1D", "1DAY", "DAY"]:
        return 86400
    
    import re
    match = re.search(r'\d+', tf)
    val = int(match.group()) if match else 5
    
    if "HOUR" in tf or "H" in tf:
        return val * 3600
    elif "MIN" in tf or "M" in tf:
        return val * 60
    elif "SEC" in tf or "S" in tf:
        return val
        
    return 300 # Default fallback (5 mins)


def _safe_float(value, fallback: float = 0.0) -> float:
    try:
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        return float(value)
    except Exception:
        return fallback


GRID_DEFAULT_ENTRY_LEVEL_PCTS = (1.0, 2.0, 3.0, 4.0, 5.0)
GRID_DEFAULT_EXIT_LEVEL_PCTS = (1.0, 2.0, 3.0, 4.0, 5.0)
GRID_DEFAULT_ATR_LEVEL_MULTS = (2.0, 4.0, 6.0, 8.0, 10.0)
GRID_RUNTIME_STATE_KEYS = (
    "grid_cycle_active",
    "grid_flat_reentry_armed",
    "entry_grid_level",
    "entry_grid_anchor_price",
    "entry_grid_anchor_atr",
    "entry_grid_armed_level",
    "entry_grid_last_fill_price",
    "exit_grid_level",
    "exit_grid_anchor_price",
    "exit_grid_anchor_atr",
    "exit_grid_anchor_qty",
    "exit_grid_last_fill_price",
    "entry_grid_trend_score",
    "entry_grid_volatility_ratio",
    "entry_grid_high_water_price",
    "exit_grid_trend_score",
    "exit_grid_volatility_ratio",
    "exit_grid_high_water_price",
    "exit_grid_armed_level",
    "exit_grid_runner_active",
    "exit_grid_runner_high_water_price",
    "entry_grid_pending_order_id",
    "entry_grid_pending_level",
    "entry_grid_pending_before_qty",
    "entry_grid_pending_fill_price",
    "entry_grid_pending_atr",
    "entry_grid_pending_missing_scans",
    "exit_grid_pending_order_id",
    "exit_grid_pending_level",
    "exit_grid_pending_before_qty",
    "exit_grid_pending_fill_price",
    "exit_grid_pending_atr",
    "exit_grid_pending_entry_price",
    "exit_grid_pending_final_level",
    "exit_grid_pending_runner_exit",
    "exit_grid_pending_missing_scans",
    # Legacy shared state is deliberately cleared during migration because it
    # cannot safely describe independent scale-in and scale-out progress.
    "grid_level",
    "grid_last_execution_price",
)


def _continuous_grid_kind(settings: dict) -> str:
    """Return the active ladder unit without mixing saved PCT and ATR fields."""
    settings = settings or {}
    buy_mode = str(settings.get("buy_mode", "") or "").lower()
    sell_mode = str(settings.get("sell_mode", "") or "").lower()
    if (
        settings.get("atr_grid_continuous_mode")
        and settings.get("exit_atr_grid_continuous_mode")
        and buy_mode in {"atr", "hybrid_atr"}
        and sell_mode in {"sltp", "hybrid", "atr", "hybrid_atr"}
    ):
        return "atr"
    if (
        (settings.get("grid_continuous_mode") and buy_mode in {"percentage", "hybrid_pct"})
        or (settings.get("exit_grid_continuous_mode") and sell_mode in {"percentage", "hybrid_pct"})
    ):
        return "pct"
    return ""


def _continuous_grid_pair_enabled(settings: dict) -> bool:
    settings = settings or {}
    kind = _continuous_grid_kind(settings)
    if kind == "atr":
        return bool(settings.get("atr_grid_continuous_mode") and settings.get("exit_atr_grid_continuous_mode"))
    if kind == "pct":
        return bool(settings.get("grid_continuous_mode") and settings.get("exit_grid_continuous_mode"))
    return False


def _grid_max_levels(settings: dict, side: str) -> int:
    return 5


def _grid_level_enabled(settings: dict, side: str, level: int) -> bool:
    level = min(5, max(1, int(level)))
    kind = _continuous_grid_kind(settings)
    if kind == "atr":
        prefix = "grid_atr_entry" if side == "entry" else "grid_atr_exit"
    else:
        prefix = "grid_entry" if side == "entry" else "grid_exit"
    key = f"{prefix}_l{level}_enabled"
    if key in (settings or {}):
        value = settings.get(key)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    legacy_key = "grid_max_levels" if side == "entry" else "exit_grid_max_levels"
    legacy_count = min(5, max(1, int(_safe_float((settings or {}).get(legacy_key), 5))))
    return level <= legacy_count


def _grid_enabled_levels(settings: dict, side: str) -> list:
    return [level for level in range(1, 6) if _grid_level_enabled(settings, side, level)]


def _next_grid_level(settings: dict, side: str, completed: int):
    return next((level for level in _grid_enabled_levels(settings, side) if level > completed), None)


def _rewind_grid_level(settings: dict, side: str, completed: int, steps: int) -> int:
    completed_levels = [level for level in _grid_enabled_levels(settings, side) if level <= completed]
    keep_count = max(0, len(completed_levels) - max(0, int(steps)))
    return completed_levels[keep_count - 1] if keep_count else 0


def _grid_level_pct(settings: dict, side: str, level: int) -> float:
    level = min(5, max(1, int(level)))
    key = f"grid_entry_l{level}_pct" if side == "entry" else f"grid_exit_l{level}_pct"
    defaults = GRID_DEFAULT_ENTRY_LEVEL_PCTS if side == "entry" else GRID_DEFAULT_EXIT_LEVEL_PCTS
    fallback = defaults[level - 1]
    return min(100.0, max(0.1, _safe_float((settings or {}).get(key), fallback)))


def _grid_level_distance(settings: dict, side: str, level: int) -> float:
    """Return a percent distance or ATR multiple for the active ladder."""
    level = min(5, max(1, int(level)))
    if _continuous_grid_kind(settings) == "atr":
        key = f"grid_atr_{side}_l{level}_mult"
        return min(100.0, max(0.1, _safe_float((settings or {}).get(key), GRID_DEFAULT_ATR_LEVEL_MULTS[level - 1])))
    return _grid_level_pct(settings, side, level)


def _continuous_grid_smart_enabled(settings: dict) -> bool:
    value = (settings or {}).get("continuous_grid_adaptive_mode", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _continuous_grid_trailing_enabled(settings: dict) -> bool:
    """Smart mode can ratchet all grid levels from favorable price movement."""
    if not _continuous_grid_smart_enabled(settings):
        return False
    value = (settings or {}).get("continuous_grid_trailing_mode", True)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _pct_grid_regime_mode(settings: dict) -> str:
    """Return the PCT ladder profile selected by the user.

    ``auto`` keeps the neutral allocation in balanced markets and switches to
    a front-loaded buy ladder plus a runner-preserving sell ladder in a
    confirmed uptrend. ``balanced`` is the compatibility profile and
    ``runner`` always keeps inventory for a trailing continuation.
    """
    # Un-normalized test/legacy dictionaries retain the historical balanced
    # behavior; the API normalizer writes ``auto`` for new and upgraded bots.
    value = str((settings or {}).get("pct_grid_regime_mode", "balanced") or "balanced").strip().lower()
    return value if value in {"auto", "balanced", "runner"} else "balanced"


def _pct_grid_runner_pct(settings: dict) -> float:
    return min(60.0, max(0.0, _safe_float((settings or {}).get("pct_grid_runner_pct"), 25.0)))


def _pct_grid_runner_trail_pct(settings: dict) -> float:
    return min(25.0, max(0.1, _safe_float((settings or {}).get("pct_grid_runner_trail_pct"), 3.0)))


def _pct_grid_regime_profile_active(settings: dict, trend_score: float = None) -> bool:
    """Whether PCT execution should use the runner-aware regime profile."""
    if _continuous_grid_kind(settings) != "pct" or not _continuous_grid_smart_enabled(settings):
        return False
    mode = _pct_grid_regime_mode(settings)
    if mode == "runner":
        return True
    if mode != "auto":
        return False
    threshold = min(0.95, max(0.0, _safe_float((settings or {}).get("pct_grid_bullish_trend_threshold"), 0.35)))
    score = _safe_float(
        trend_score,
        _safe_float((settings or {}).get("entry_grid_trend_score"), _safe_float((settings or {}).get("exit_grid_trend_score"), 0.0)),
    )
    return score >= threshold


def _grid_history_rows(price_history) -> list:
    """Return clean OHLC rows from API candle lists or a backtest DataFrame."""
    if price_history is None:
        return []
    try:
        if hasattr(price_history, "to_dict"):
            raw_rows = price_history.tail(80).to_dict("records")
        else:
            raw_rows = list(price_history)[-80:]
    except Exception:
        return []
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        close = _safe_float(raw.get("close", raw.get("Close")), 0.0)
        high = _safe_float(raw.get("high", raw.get("High")), close)
        low = _safe_float(raw.get("low", raw.get("Low")), close)
        if close > 0 and high > 0 and low > 0:
            rows.append({"close": close, "high": max(high, low), "low": min(high, low)})
    return rows


def _grid_ema(values: list, period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (max(1, period) + 1.0)
    ema = float(values[0])
    for value in values[1:]:
        ema += alpha * (float(value) - ema)
    return ema


def _grid_market_context(price_history, current_atr: float = 0.0) -> dict:
    """Estimate a bounded trend/volatility regime from completed candles only."""
    rows = _grid_history_rows(price_history)
    if len(rows) < 22:
        return {"trend_score": 0.0, "volatility_ratio": 1.0, "sample_size": len(rows)}
    closes = [row["close"] for row in rows]
    true_ranges = []
    for index, row in enumerate(rows):
        previous_close = closes[index - 1] if index else row["close"]
        true_ranges.append(max(
            row["high"] - row["low"],
            abs(row["high"] - previous_close),
            abs(row["low"] - previous_close),
        ))
    sorted_ranges = sorted(value for value in true_ranges[-40:] if value > 0)
    median_range = sorted_ranges[len(sorted_ranges) // 2] if sorted_ranges else 0.0
    live_atr = max(0.0, _safe_float(current_atr, 0.0))
    if live_atr <= 0 and true_ranges:
        live_atr = sum(true_ranges[-14:]) / min(14, len(true_ranges))
    volatility_ratio = live_atr / median_range if live_atr > 0 and median_range > 0 else 1.0
    volatility_ratio = min(1.75, max(0.65, volatility_ratio))
    normalization = max(live_atr, median_range, closes[-1] * 0.001, 0.000001)
    trend_score = (_grid_ema(closes[-60:], 8) - _grid_ema(closes[-60:], 21)) / normalization
    return {
        "trend_score": round(min(1.0, max(-1.0, trend_score)), 6),
        "volatility_ratio": round(volatility_ratio, 6),
        "sample_size": len(rows),
    }


def _clear_grid_context(settings: dict, side: str) -> None:
    settings.pop(f"{side}_grid_trend_score", None)
    settings.pop(f"{side}_grid_volatility_ratio", None)


def _ensure_grid_adaptive_context(settings: dict, price_history, current_atr: float) -> bool:
    """Freeze one regime snapshot per anchor so active trigger prices never drift."""
    if not _continuous_grid_smart_enabled(settings):
        return False
    context = _grid_market_context(price_history, current_atr)
    changed = False
    for side in ("entry", "exit"):
        if _safe_float(settings.get(f"{side}_grid_anchor_price"), 0.0) <= 0:
            continue
        trend_key = f"{side}_grid_trend_score"
        volatility_key = f"{side}_grid_volatility_ratio"
        if trend_key not in settings:
            settings[trend_key] = context["trend_score"]
            changed = True
        if volatility_key not in settings:
            settings[volatility_key] = context["volatility_ratio"]
            changed = True
    return changed


def _grid_adaptive_factors(settings: dict, side: str) -> dict:
    strength = min(1.0, max(0.0, _safe_float((settings or {}).get("continuous_grid_adaptation_strength"), 50.0) / 100.0))
    trend = min(1.0, max(-1.0, _safe_float((settings or {}).get(f"{side}_grid_trend_score"), 0.0)))
    volatility = min(1.75, max(0.65, _safe_float((settings or {}).get(f"{side}_grid_volatility_ratio"), 1.0)))
    if not _continuous_grid_smart_enabled(settings):
        return {"smart": False, "strength": strength, "trend_score": trend, "volatility_ratio": volatility, "distance_factor": 1.0}
    if side == "entry":
        trend_factor = 1.0 - (0.25 * strength * max(trend, 0.0)) + (0.55 * strength * max(-trend, 0.0))
    else:
        trend_factor = 1.0 + (0.55 * strength * max(trend, 0.0)) - (0.35 * strength * max(-trend, 0.0))
    volatility_factor = volatility ** (0.75 * strength) if _continuous_grid_kind(settings) == "pct" else 1.0
    return {
        "smart": True,
        "strength": strength,
        "trend_score": trend,
        "volatility_ratio": volatility,
        "distance_factor": min(2.0, max(0.5, trend_factor * volatility_factor)),
    }


def _grid_effective_distance(settings: dict, side: str, level: int, anchor: float, anchor_atr: float = 0.0) -> float:
    """Transform a configured rung while retaining it as the visible baseline."""
    base = _grid_level_distance(settings, side, level)
    factors = _grid_adaptive_factors(settings, side)
    if not factors["smart"]:
        return base
    effective = base * factors["distance_factor"]
    enabled = _grid_enabled_levels(settings, side)
    ordinal = (enabled.index(level) + 1) if level in enabled else max(1, level)
    min_edge_pct = min(10.0, max(0.0, _safe_float((settings or {}).get("continuous_grid_min_edge_pct"), 0.35)))
    if _continuous_grid_kind(settings) == "atr":
        if anchor > 0 and anchor_atr > 0:
            effective = max(effective, (anchor * min_edge_pct / 100.0 * ordinal) / anchor_atr)
    else:
        effective = max(effective, min_edge_pct * ordinal)
    return round(min(100.0, max(0.1, effective)), 6)


def _grid_trailing_gap(settings: dict, side: str, level: int, anchor: float, current_atr: float = 0.0) -> float:
    """Return the protective pullback gap for an armed sell rung.

    PCT ladders use half the effective rung distance plus a volatility floor.
    ATR ladders use half the effective ATR multiple and the bounded runtime
    ATR. The anchor ATR is only a fallback when a live ATR is unavailable, so
    the trail can tighten as volatility contracts without becoming unbounded.
    """
    distance = _grid_effective_distance(settings, side, level, anchor, current_atr)
    min_edge_pct = min(10.0, max(0.0, _safe_float((settings or {}).get("continuous_grid_min_edge_pct"), 0.35)))
    if _continuous_grid_kind(settings) == "atr":
        atr = max(0.0, _safe_float(current_atr, 0.0))
        if atr <= 0:
            atr = max(0.0, _safe_float(settings.get(f"{side}_grid_anchor_atr"), 0.0))
        if atr <= 0 or anchor <= 0:
            return max(0.1, distance * 0.5)
        edge_atr = (anchor * min_edge_pct / 100.0) / atr
        return round(max(0.1, distance * 0.5, edge_atr), 6)
    volatility_pct = (max(0.0, _safe_float(current_atr, 0.0)) / anchor * 100.0) if anchor > 0 else 0.0
    return round(max(0.1, distance * 0.5, min_edge_pct, volatility_pct * 0.75), 6)


def _grid_trailing_threshold(settings: dict, side: str, level: int, peak_price: float, anchor: float, current_atr: float = 0.0) -> float:
    """Sell-side trailing threshold below the favorable high-water mark."""
    if peak_price <= 0:
        return 0.0
    atr = max(0.0, _safe_float(current_atr, 0.0))
    if atr <= 0:
        atr = max(0.0, _safe_float(settings.get(f"{side}_grid_anchor_atr"), 0.0))
    gap = _grid_trailing_gap(settings, side, level, anchor, atr)
    if _continuous_grid_kind(settings) == "atr":
        return peak_price - (gap * atr)
    return peak_price * (1.0 - gap / 100.0)


def _grid_runtime_atr(settings: dict, side: str, current_atr: float) -> float:
    """Use live ATR for trailing grids, bounded around the anchor ATR."""
    live = max(0.0, _safe_float(current_atr, 0.0))
    anchor = max(0.0, _safe_float(settings.get(f"{side}_grid_anchor_atr"), 0.0))
    if not _continuous_grid_trailing_enabled(settings) or live <= 0 or anchor <= 0:
        return anchor or live
    ratio = min(1.5, max(0.75, live / anchor))
    return anchor * ratio


def _grid_level_allocation_pct(settings: dict, side: str, level: int) -> float:
    """Inventory-aware rung weighting normalized to the configured allocation."""
    kind = _continuous_grid_kind(settings)
    prefix = ("grid_atr_entry" if kind == "atr" else "grid_entry") if side == "entry" else ("grid_atr_exit" if kind == "atr" else "grid_exit")
    suffix = "buy_pct" if side == "entry" else "sell_pct"
    default_profile = (40.0, 25.0, 15.0, 10.0, 10.0) if side == "entry" else (10.0, 10.0, 15.0, 20.0, 20.0)
    # Normalized API settings always contain explicit rung values. The
    # compatibility fallback keeps un-normalized legacy dictionaries stable.
    def fallback_for_rung(rung: int) -> float:
        return default_profile[rung - 1] if "pct_grid_regime_mode" in (settings or {}) else (
            20.0 if side == "entry" else _safe_float((settings or {}).get("exit_grid_sell_pct"), 20.0)
        )
    enabled = _grid_enabled_levels(settings, side)
    configured = {
        rung: min(100.0, max(1.0, _safe_float((settings or {}).get(f"{prefix}_l{rung}_{suffix}"), fallback_for_rung(rung))))
        for rung in enabled
    }
    if level not in configured:
        return min(100.0, max(1.0, _safe_float((settings or {}).get(f"{prefix}_l{level}_{suffix}"), fallback_for_rung(level))))
    factors = _grid_adaptive_factors(settings, side)
    if not factors["smart"] or len(enabled) < 2:
        return configured[level]
    trend = factors["trend_score"]
    strength = factors["strength"]
    center = (len(enabled) - 1) / 2.0
    weighted = {}
    for index, rung in enumerate(enabled):
        position = (index - center) / max(1.0, center)
        exponent = (-trend * position if side == "entry" else trend * position) * strength * 0.70
        weighted[rung] = configured[rung] * math.exp(exponent)

    # PCT ladders use a stronger, explicit regime profile than the generic
    # volatility weighting. The neutral profile remains exactly the user's
    # configured percentages; only a confirmed bullish regime changes the
    # inventory curve. This keeps the behavior explainable and bounded.
    if kind == "pct" and _pct_grid_regime_profile_active(settings, trend):
        if side == "entry":
            # Front-load capital in an uptrend so a healthy continuation is
            # not missed while still retaining all five scale-in rungs.
            profile = (40.0, 25.0, 15.0, 10.0, 10.0)
        else:
            # Sell 75% through the ladder and retain a 25% runner for a
            # continuation. The runner is liquidated by its own trailing
            # trigger once the ladder has completed.
            profile = (10.0, 10.0, 15.0, 20.0, 20.0)
        profile_weighted = {
            rung: profile[min(rung - 1, len(profile) - 1)]
            for rung in enabled
        }
        profile_total = sum(profile_weighted.values()) or 1.0
        configured_total = sum(configured.values())
        target_total = configured_total
        if side == "exit":
            target_total = min(configured_total, max(1.0, 100.0 - _pct_grid_runner_pct(settings)))
        # Adaptation strength controls how far the profile moves from the
        # user's saved weights (50% still makes a material but not abrupt
        # change; 100% uses the full quant profile).
        blend = 0.5 + (0.5 * strength)
        profile_values = {
            rung: profile_weighted[rung] * target_total / profile_total
            for rung in enabled
        }
        current_values = {
            rung: configured[rung] * target_total / (configured_total or 1.0)
            for rung in enabled
        }
        return round(
            current_values[level] * (1.0 - blend) + profile_values[level] * blend,
            6,
        )
    configured_total = sum(configured.values())
    weighted_total = sum(weighted.values()) or 1.0
    return round(weighted[level] * configured_total / weighted_total, 6)


def _grid_threshold_price(settings: dict, side: str, anchor: float, distance: float, anchor_atr: float = 0.0) -> float:
    if _continuous_grid_kind(settings) == "atr":
        volatility = max(0.0, _safe_float(anchor_atr, 0.0))
        if volatility <= 0:
            return 0.0
        return anchor + ((-1.0 if side == "entry" else 1.0) * distance * volatility)
    return anchor * (1.0 + ((-1.0 if side == "entry" else 1.0) * distance / 100.0))


def _grid_entry_level_buy_pct(settings: dict, level: int) -> float:
    """Return one scale-in rung as a percentage of the bot allocation."""
    level = min(5, max(1, int(level)))
    return _grid_level_allocation_pct(settings, "entry", level)


def _grid_entry_level_amount(settings: dict, level: int) -> float:
    """Return the scale-in notional derived from Buy % and bot capital."""
    settings = settings or {}
    allocation_mode = str(settings.get("allocation_mode", "fixed") or "fixed").lower()
    if allocation_mode == "compound":
        allocation = _safe_float(settings.get("compound_amount"), 0.0)
    else:
        allocation = _safe_float(settings.get("amount"), 0.0)
    if allocation <= 0:
        allocation = _safe_float(settings.get("amount"), 0.0)
    if allocation <= 0:
        # Temporary compatibility for a bot that has not yet passed through
        # settings normalization after the dollar-to-percent migration.
        allocation = _safe_float(settings.get("grid_buy_allocation"), 0.0)
    return max(0.0, allocation) * _grid_entry_level_buy_pct(settings, level) / 100.0


def _initial_continuous_grid_entry_instruction(settings: dict, price_history=None, current_atr: float = 0.0) -> dict:
    """Return the first adaptive-grid tranche used by a new percentage entry.

    The initial strategy signal is part of the configured 100% buy ladder. It
    must not consume a full bot allocation before the remaining ladder rungs
    are evaluated, otherwise a 5 x 20% ladder can create 200% exposure.
    """
    settings = settings or {}
    buy_mode = str(settings.get("buy_mode", "indicator") or "indicator").lower()
    sell_mode = str(settings.get("sell_mode", "indicator") or "indicator").lower()
    if not (_continuous_grid_pair_enabled(settings) and not settings.get("grid_flat_reentry_armed", False)):
        return {}
    enabled_levels = _grid_enabled_levels(settings, "entry")
    if not enabled_levels:
        return {}
    if _continuous_grid_smart_enabled(settings):
        context = _grid_market_context(price_history, current_atr)
        for side in ("entry", "exit"):
            settings[f"{side}_grid_trend_score"] = context["trend_score"]
            settings[f"{side}_grid_volatility_ratio"] = context["volatility_ratio"]
    level = enabled_levels[0]
    return {
        "action": "BUY",
        "side": "entry",
        "from_level": level,
        "level": level,
        "max_levels": 5,
        "enabled_levels": enabled_levels,
        "grid_kind": _continuous_grid_kind(settings),
        "level_pct": _grid_level_distance(settings, "entry", level),
        "level_amount": _grid_entry_level_amount(settings, level),
        "allocation_pct": _grid_entry_level_buy_pct(settings, level),
    }


def _highest_crossed_grid_level(
    settings: dict,
    side: str,
    completed: int,
    anchor: float,
    price: float,
    anchor_atr: float = 0.0,
) -> int:
    """Return the deepest newly crossed rung, supporting gap catch-up."""
    target = completed
    for level in _grid_enabled_levels(settings, side):
        if level <= completed:
            continue
        level_pct = _grid_effective_distance(settings, side, level, anchor, anchor_atr)
        threshold = _grid_threshold_price(settings, side, anchor, level_pct, anchor_atr)
        if threshold <= 0:
            break
        crossed = price <= threshold if side == "entry" else price >= threshold
        if not crossed:
            break
        target = level
    return target


def _reset_continuous_grid_state(settings: dict) -> bool:
    changed = False
    for key in GRID_RUNTIME_STATE_KEYS:
        if key in settings:
            settings.pop(key, None)
            changed = True
    return changed


def _grid_pending_keys(side: str) -> tuple:
    keys = (
        f"{side}_grid_pending_order_id",
        f"{side}_grid_pending_level",
        f"{side}_grid_pending_before_qty",
        f"{side}_grid_pending_fill_price",
        f"{side}_grid_pending_atr",
        f"{side}_grid_pending_missing_scans",
    )
    if side == "exit":
        keys += (
            "exit_grid_pending_entry_price",
            "exit_grid_pending_final_level",
        )
    return keys


def _clear_continuous_grid_pending(settings: dict, side: str) -> bool:
    changed = False
    for key in _grid_pending_keys(side):
        if key in settings:
            settings.pop(key, None)
            changed = True
    return changed


def _set_continuous_grid_pending(
    settings: dict,
    *,
    side: str,
    order_id,
    level: int,
    before_qty: float,
    fill_price: float,
    current_atr: float = 0.0,
    entry_price: float = 0.0,
    final_level: bool = False,
    runner_exit: bool = False,
) -> None:
    _clear_continuous_grid_pending(settings, side)
    settings[f"{side}_grid_pending_order_id"] = str(order_id)
    settings[f"{side}_grid_pending_level"] = int(level or 0)
    settings[f"{side}_grid_pending_before_qty"] = max(0.0, _safe_float(before_qty, 0.0))
    settings[f"{side}_grid_pending_fill_price"] = max(0.0, _safe_float(fill_price, 0.0))
    settings[f"{side}_grid_pending_atr"] = max(0.0, _safe_float(current_atr, 0.0))
    settings[f"{side}_grid_pending_missing_scans"] = 0
    if side == "exit":
        settings["exit_grid_pending_entry_price"] = max(0.0, _safe_float(entry_price, 0.0))
        settings["exit_grid_pending_final_level"] = bool(final_level)
        settings["exit_grid_pending_runner_exit"] = bool(runner_exit)


def _reconcile_continuous_grid_pending(
    settings: dict,
    *,
    current_qty: float,
    average_entry_price: float,
    current_price: float,
    open_order_ids,
    fill_events: list = None,
) -> bool:
    """Advance a grid rung only after the broker position confirms a fill."""
    changed = False
    qty = max(0.0, _safe_float(current_qty, 0.0))
    open_ids = {str(order_id) for order_id in (open_order_ids or set()) if order_id}

    for pending_side in ("entry", "exit"):
        order_key = f"{pending_side}_grid_pending_order_id"
        order_id = str(settings.get(order_key) or "")
        if not order_id:
            continue
        if order_id in open_ids:
            missing_key = f"{pending_side}_grid_pending_missing_scans"
            if settings.get(missing_key):
                settings[missing_key] = 0
                changed = True
            continue

        before_qty = max(
            0.0,
            _safe_float(settings.get(f"{pending_side}_grid_pending_before_qty"), 0.0),
        )
        pending_level = max(
            0,
            int(_safe_float(settings.get(f"{pending_side}_grid_pending_level"), 0)),
        )
        tolerance = max(0.00000001, before_qty * 0.000001)
        stored_price = _safe_float(
            settings.get(f"{pending_side}_grid_pending_fill_price"), current_price
        )
        stored_atr = _safe_float(settings.get(f"{pending_side}_grid_pending_atr"), 0.0)

        quantity_confirmed = False
        if pending_side == "entry" and qty > before_qty + tolerance:
            quantity_confirmed = True
            filled_qty = qty - before_qty
            _record_continuous_grid_fill(
                settings,
                side="buy",
                fill_price=_safe_float(average_entry_price, stored_price) or stored_price,
                was_grid_order=True,
                had_position=before_qty > tolerance,
                filled_level=pending_level,
                current_atr=stored_atr,
            )
            if fill_events is not None:
                fill_events.append({
                    "side": "entry",
                    "filled_qty": filled_qty,
                    "fill_price": _safe_float(average_entry_price, stored_price) or stored_price,
                    "position_closed": False,
                })
            changed = True
        elif pending_side == "exit" and qty < before_qty - tolerance:
            quantity_confirmed = True
            filled_qty = before_qty - qty
            final_level = bool(settings.get("exit_grid_pending_final_level", False))
            runner_exit = bool(settings.get("exit_grid_pending_runner_exit", False))
            entry_price = _safe_float(settings.get("exit_grid_pending_entry_price"), 0.0)
            if final_level and qty > tolerance:
                # A partially filled final close must retry the final rung for
                # the remainder rather than marking the ladder complete.
                if runner_exit:
                    settings["exit_grid_runner_active"] = True
                    settings["exit_grid_runner_high_water_price"] = max(
                        _safe_float(settings.get("exit_grid_runner_high_water_price"), stored_price),
                        stored_price,
                    )
                else:
                    settings["entry_grid_level"] = max(
                        0, int(_safe_float(settings.get("entry_grid_level"), 0)) - 1
                    )
                if stored_price > 0 and _continuous_grid_pair_enabled(settings):
                    settings["entry_grid_anchor_price"] = stored_price
                    if _continuous_grid_kind(settings) == "atr" and stored_atr > 0:
                        settings["entry_grid_anchor_atr"] = stored_atr
                    settings["entry_grid_armed_level"] = 0
                settings["exit_grid_last_fill_price"] = stored_price
            else:
                _record_continuous_grid_fill(
                    settings,
                    side="sell",
                    fill_price=stored_price,
                    was_grid_order=True,
                    had_position=True,
                    position_closed=qty <= tolerance,
                    continue_cycle=(
                        qty <= tolerance
                        and final_level
                        and _continuous_grid_pair_enabled(settings)
                    ),
                    filled_level=pending_level,
                    current_atr=stored_atr,
                )
            if fill_events is not None:
                fill_events.append({
                    "side": "exit",
                    "filled_qty": filled_qty,
                    "fill_price": stored_price,
                    "entry_price": entry_price,
                    "position_closed": qty <= tolerance,
                })
            changed = True

        if not quantity_confirmed:
            # A single missing-order response may be a transient broker/API
            # read. Require two consecutive scans before treating the order as
            # canceled, which prevents accidental duplicate submissions.
            missing_key = f"{pending_side}_grid_pending_missing_scans"
            missing_scans = max(0, int(_safe_float(settings.get(missing_key), 0))) + 1
            settings[missing_key] = missing_scans
            changed = True
            if missing_scans < 2:
                continue

        # The order is no longer open. If quantity did not change it was
        # canceled/rejected, so clear only pending metadata and retry the rung.
        changed = _clear_continuous_grid_pending(settings, pending_side) or changed

    return changed


def _prepare_continuous_grid_state(
    settings: dict,
    *,
    has_position: bool,
    current_price: float,
    average_entry_price: float = 0.0,
    current_qty: float = 0.0,
    current_atr: float = 0.0,
) -> bool:
    """Initialize an adaptive grid or preserve an intentional flat buy-back cycle."""
    changed = False
    # Migrate away from the old shared counter/last-price implementation.
    for key in ("grid_level", "grid_last_execution_price"):
        if key in settings:
            settings.pop(key, None)
            changed = True

    adaptive_cycle = _continuous_grid_pair_enabled(settings)
    flat_reentry = bool(settings.get("grid_flat_reentry_armed", False))
    if not has_position:
        # Preserve an initial-entry plan while its broker order is still open.
        # Clearing pending metadata here would make the eventual fill impossible
        # to reconcile and could permit a duplicate order on a later scan.
        if adaptive_cycle and settings.get("entry_grid_pending_order_id"):
            settings["grid_cycle_active"] = True
            return changed
        if not (adaptive_cycle and flat_reentry):
            return _reset_continuous_grid_state(settings) or changed
        # A completed grid exit intentionally remains active while flat. Keep
        # the last sell as the buy-back anchor, but clear position-only state.
        for key in (
            "exit_grid_level",
            "exit_grid_anchor_price",
            "exit_grid_anchor_atr",
            "exit_grid_anchor_qty",
            "exit_grid_last_fill_price",
        ):
            if key in settings:
                settings.pop(key, None)
                changed = True
        settings["grid_cycle_active"] = True
        settings["entry_grid_level"] = max(
            0,
            min(5, int(_safe_float(settings.get("entry_grid_level"), 0))),
        )
        if _continuous_grid_trailing_enabled(settings) and _safe_float(settings.get("entry_grid_high_water_price"), 0.0) <= 0:
            settings["entry_grid_high_water_price"] = _safe_float(settings.get("entry_grid_anchor_price"), current_price)
        return changed

    anchor = _safe_float(average_entry_price, 0.0) or _safe_float(current_price, 0.0)
    if anchor <= 0:
        return changed
    if not settings.get("grid_cycle_active"):
        settings["grid_cycle_active"] = True
        settings["grid_flat_reentry_armed"] = False
        changed = True
    if _safe_float(settings.get("entry_grid_anchor_price"), 0.0) <= 0:
        _clear_grid_context(settings, "entry")
        settings["entry_grid_anchor_price"] = anchor
        settings["entry_grid_high_water_price"] = anchor
        settings["entry_grid_level"] = 0
        settings["entry_grid_armed_level"] = 0
        if _continuous_grid_kind(settings) == "atr" and current_atr > 0:
            settings["entry_grid_anchor_atr"] = float(current_atr)
        changed = True
    if (
        _continuous_grid_kind(settings) == "atr"
        and _safe_float(settings.get("entry_grid_anchor_atr"), 0.0) <= 0
        and current_atr > 0
    ):
        settings["entry_grid_anchor_atr"] = float(current_atr)
        changed = True
    exit_anchor = _safe_float(settings.get("exit_grid_anchor_price"), 0.0)
    # A scale-in changes the broker's average cost. Re-anchor the scale-out
    # ladder to that live average so gain targets remain economically correct.
    # Partial sells normally leave average cost unchanged and therefore do not
    # disturb the ladder's progress.
    average_cost_changed = (
        average_entry_price > 0
        and exit_anchor > 0
        and abs(exit_anchor - average_entry_price) / average_entry_price > 0.000001
    )
    if exit_anchor <= 0 or average_cost_changed:
        _clear_grid_context(settings, "exit")
        settings["exit_grid_anchor_price"] = anchor
        settings["exit_grid_high_water_price"] = anchor
        settings["exit_grid_anchor_qty"] = max(0.0, _safe_float(current_qty, 0.0))
        settings["exit_grid_level"] = 0
        settings.pop("exit_grid_armed_level", None)
        if _continuous_grid_kind(settings) == "atr" and current_atr > 0:
            settings["exit_grid_anchor_atr"] = float(current_atr)
        changed = True
    elif _safe_float(settings.get("exit_grid_anchor_qty"), 0.0) <= 0 and current_qty > 0:
        settings["exit_grid_anchor_qty"] = max(0.0, _safe_float(current_qty, 0.0))
        changed = True
    if (
        _continuous_grid_kind(settings) == "atr"
        and _safe_float(settings.get("exit_grid_anchor_atr"), 0.0) <= 0
        and current_atr > 0
    ):
        settings["exit_grid_anchor_atr"] = float(current_atr)
        changed = True
    return changed


def _evaluate_continuous_grid(
    settings: dict,
    *,
    current_price: float,
    has_position: bool,
    average_entry_price: float = 0.0,
    position_qty: float = 0.0,
    current_atr: float = 0.0,
    price_history=None,
) -> dict:
    """Return at most one scale-in/scale-out instruction for the current scan.

    With both sides enabled, sells re-anchor the buy-back ladder and completed
    exits remain active while flat. Entry-only legacy grids keep their
    dip/reclaim behavior. Exit levels remain based on live average cost.
    """
    price = _safe_float(current_price, 0.0)
    response = {"action": None, "state_changed": False}
    response["state_changed"] = _prepare_continuous_grid_state(
        settings,
        has_position=has_position,
        current_price=price,
        average_entry_price=average_entry_price,
        current_qty=position_qty,
        current_atr=current_atr,
    )
    response["state_changed"] = (
        _ensure_grid_adaptive_context(settings, price_history, current_atr)
        or response["state_changed"]
    )
    if price <= 0:
        return response

    buy_mode = str(settings.get("buy_mode", "indicator") or "indicator").lower()
    sell_mode = str(settings.get("sell_mode", "indicator") or "indicator").lower()

    grid_kind = _continuous_grid_kind(settings)
    adaptive_cycle = _continuous_grid_pair_enabled(settings)

    exit_grid_enabled = bool(
        adaptive_cycle
        or (grid_kind == "pct" and settings.get("exit_grid_continuous_mode") and sell_mode in {"percentage", "hybrid_pct"})
    )
    if has_position and exit_grid_enabled:
        enabled_levels = _grid_enabled_levels(settings, "exit")
        max_levels = 5
        completed = min(5, max(0, int(_safe_float(settings.get("exit_grid_level"), 0))))
        runner_active = bool(
            grid_kind == "pct"
            and settings.get("exit_grid_runner_active")
            and enabled_levels
        )
        if runner_active:
            anchor = _safe_float(settings.get("exit_grid_anchor_price"), average_entry_price or price)
            peak = max(
                anchor,
                _safe_float(settings.get("exit_grid_runner_high_water_price"), anchor),
                price,
            )
            history_rows = _grid_history_rows(price_history)
            if history_rows:
                peak = max(peak, history_rows[-1]["high"])
            if peak > _safe_float(settings.get("exit_grid_runner_high_water_price"), 0.0):
                settings["exit_grid_runner_high_water_price"] = peak
                response["state_changed"] = True
            trail_pct = _pct_grid_runner_trail_pct(settings)
            threshold = peak * (1.0 - trail_pct / 100.0)
            if price <= threshold:
                final_level = enabled_levels[-1]
                response.update({
                    "action": "SELL",
                    "side": "exit",
                    "from_level": final_level,
                    "level": final_level,
                    "max_levels": max_levels,
                    "enabled_levels": enabled_levels,
                    "level_pct": trail_pct,
                    "base_level_pct": trail_pct,
                    "smart_dynamic": True,
                    "trailing_enabled": True,
                    "trailing_peak_price": peak,
                    "anchor_price": anchor,
                    "threshold_price": threshold,
                    "final_level": True,
                    "continuous_grid_runner_exit": True,
                    "grid_kind": grid_kind,
                    "reason": (
                        f"PCT runner exit: price ${price:.4f} pulled back {trail_pct:.2f}% "
                        f"from the post-ladder high ${peak:.4f}; closing the remaining runner."
                    ),
                })
                return response
            response["reason"] = (
                f"PCT runner monitoring: peak ${peak:.4f}, trailing exit ${threshold:.4f}; "
                f"retaining {_pct_grid_runner_pct(settings):.1f}% runner inventory."
            )
            # The runner owns the exit side after the ladder completes. Do
            # not allow a same-scan fixed exit or a new buy to override it.
            return response
        if enabled_levels and _next_grid_level(settings, "exit", completed) is not None:
            anchor = _safe_float(settings.get("exit_grid_anchor_price"), average_entry_price or price)
            anchor_atr = _safe_float(settings.get("exit_grid_anchor_atr"), current_atr)
            trigger_atr = _grid_runtime_atr(settings, "exit", current_atr)
            trailing = _continuous_grid_trailing_enabled(settings)
            observed_peak = price
            history_rows = _grid_history_rows(price_history)
            if history_rows:
                observed_peak = max(observed_peak, history_rows[-1]["high"])
            if trailing:
                prior_peak = _safe_float(settings.get("exit_grid_high_water_price"), anchor)
                if observed_peak > prior_peak:
                    settings["exit_grid_high_water_price"] = observed_peak
                    response["state_changed"] = True
                peak = max(prior_peak, observed_peak, anchor)
            else:
                peak = price
            activation_level = _highest_crossed_grid_level(settings, "exit", completed, anchor, peak, trigger_atr)
            armed_level = min(5, max(completed, int(_safe_float(settings.get("exit_grid_armed_level"), 0))))
            if trailing and activation_level > armed_level:
                armed_level = activation_level
                settings["exit_grid_armed_level"] = armed_level
                response["state_changed"] = True
            level = armed_level if trailing else activation_level
            if anchor > 0 and level > completed:
                base_gain = _grid_level_distance(settings, "exit", level)
                gain_pct = _grid_effective_distance(settings, "exit", level, anchor, trigger_atr)
                activation_threshold = _grid_threshold_price(settings, "exit", anchor, gain_pct, trigger_atr)
                threshold = (
                    _grid_trailing_threshold(
                        settings, "exit", level, peak, anchor,
                        trigger_atr if grid_kind == "atr" else current_atr,
                    )
                    if trailing else activation_threshold
                )
                from_level = _next_grid_level(settings, "exit", completed)
                crossed_levels = [rung for rung in enabled_levels if from_level <= rung <= level]
                level_label = "+".join(f"L{rung}" for rung in crossed_levels)
                if (not trailing) or price <= threshold:
                    response.update({
                        "action": "SELL",
                        "side": "exit",
                        "from_level": from_level,
                        "level": level,
                        "max_levels": max_levels,
                        "level_pct": gain_pct,
                        "base_level_pct": base_gain,
                        "smart_dynamic": _continuous_grid_smart_enabled(settings),
                        "trailing_enabled": trailing,
                        "trailing_peak_price": peak if trailing else None,
                        "activation_price": activation_threshold,
                        "grid_kind": grid_kind,
                        "anchor_atr": anchor_atr if grid_kind == "atr" else None,
                        "trigger_atr": trigger_atr if grid_kind == "atr" else None,
                        "anchor_price": anchor,
                        "threshold_price": threshold,
                        "final_level": level == enabled_levels[-1],
                        "enabled_levels": enabled_levels,
                        "reason": (
                            f"Scale-Out {level_label}/{max_levels}: ${price:.4f} "
                            + (f"pulled back to the trailing trigger ${threshold:.4f} after a ${peak:.4f} peak; " if trailing else "crossed through ")
                            + f"level +{gain_pct:.2f}{'x ATR' if grid_kind == 'atr' else '%'} from average-cost anchor ${anchor:.4f}."
                        ),
                    })
                    return response

    entry_grid_enabled = bool(
        adaptive_cycle
        or (grid_kind == "pct" and settings.get("grid_continuous_mode") and buy_mode in {"percentage", "hybrid_pct"})
    )
    if entry_grid_enabled:
        if not has_position and not settings.get("grid_flat_reentry_armed", False):
            response["reason"] = (
                f"Adaptive Grid Monitoring: waiting for the initial {grid_kind.upper()} entry fill; "
                "buy and sell ladders arm after a position opens."
            )
            return response
        enabled_levels = _grid_enabled_levels(settings, "entry")
        max_levels = 5
        completed = min(5, max(0, int(_safe_float(settings.get("entry_grid_level"), 0))))
        level = _next_grid_level(settings, "entry", completed)
        if enabled_levels and level is not None:
            base_anchor = _safe_float(settings.get("entry_grid_anchor_price"), average_entry_price or price)
            anchor_atr = _safe_float(settings.get("entry_grid_anchor_atr"), current_atr)
            trigger_atr = _grid_runtime_atr(settings, "entry", current_atr)
            trailing = _continuous_grid_trailing_enabled(settings)
            observed_peak = price
            history_rows = _grid_history_rows(price_history)
            if history_rows:
                observed_peak = max(observed_peak, history_rows[-1]["high"])
            prior_peak = _safe_float(settings.get("entry_grid_high_water_price"), base_anchor)
            if trailing:
                anchor = max(base_anchor, prior_peak, observed_peak)
                if anchor > prior_peak:
                    settings["entry_grid_high_water_price"] = anchor
                    response["state_changed"] = True
            else:
                anchor = base_anchor
            base_dip = _grid_level_distance(settings, "entry", level)
            dip_pct = _grid_effective_distance(settings, "entry", level, anchor, trigger_atr)
            threshold = _grid_threshold_price(settings, "entry", anchor, dip_pct, trigger_atr)
            armed_level = max(0, int(_safe_float(settings.get("entry_grid_armed_level"), 0)))
            entry_style = str(settings.get("entry_pct_style", "dip_reclaim") or "dip_reclaim").lower()

            if adaptive_cycle:
                # True continuous grids buy each available rung on touch. A
                # sell reduces entry_grid_level, reopening capacity for a
                # later buy-back below the latest sell anchor.
                target_level = _highest_crossed_grid_level(settings, "entry", completed, anchor, price, trigger_atr)
                if anchor > 0 and target_level > completed:
                    from_level = _next_grid_level(settings, "entry", completed)
                    level = target_level
                    base_dip = _grid_level_distance(settings, "entry", level)
                    dip_pct = _grid_effective_distance(settings, "entry", level, anchor, trigger_atr)
                    threshold = _grid_threshold_price(settings, "entry", anchor, dip_pct, trigger_atr)
                    level_amount = sum(
                        _grid_entry_level_amount(settings, rung)
                        for rung in enabled_levels
                        if from_level <= rung <= level
                    )
                    crossed_levels = [rung for rung in enabled_levels if from_level <= rung <= level]
                    level_label = "+".join(f"L{rung}" for rung in crossed_levels)
                    response.update({
                        "action": "BUY",
                        "side": "entry",
                        "from_level": from_level,
                        "level": level,
                        "max_levels": max_levels,
                        "enabled_levels": enabled_levels,
                        "level_pct": dip_pct,
                        "base_level_pct": base_dip,
                        "smart_dynamic": _continuous_grid_smart_enabled(settings),
                        "trailing_enabled": trailing,
                        "trailing_peak_price": anchor if trailing else None,
                        "grid_kind": grid_kind,
                        "anchor_atr": anchor_atr if grid_kind == "atr" else None,
                        "trigger_atr": trigger_atr if grid_kind == "atr" else None,
                        "anchor_price": anchor,
                        "threshold_price": threshold,
                        "level_amount": level_amount,
                        "reason": (
                            f"Adaptive Grid Buy {level_label}/{max_levels}: ${price:.4f} crossed through "
                            f"-{dip_pct:.2f}{'x ATR' if grid_kind == 'atr' else '%'} from "
                            f"{'trailing high-water mark' if trailing else 'last sell anchor'} ${anchor:.4f}."
                        ),
                    })
                    return response
            elif entry_style == "dip_reclaim":
                if armed_level != level and price <= threshold:
                    settings["entry_grid_armed_level"] = level
                    response["state_changed"] = True
                    response.update({
                        "side": "entry",
                        "level": level,
                        "max_levels": max_levels,
                        "enabled_levels": enabled_levels,
                        "level_pct": dip_pct,
                        "anchor_price": anchor,
                        "threshold_price": threshold,
                        "reason": (
                            f"Scale-In L{level}/{max_levels} armed at -{dip_pct:.2f}% "
                            f"(${threshold:.4f}); waiting for reclaim."
                        ),
                    })
                    return response
                reclaim_buffer_pct = min(2.0, max(0.0, _safe_float(settings.get("grid_reclaim_buffer_pct"), 0.15)))
                reclaim_price = threshold * (1.0 + reclaim_buffer_pct / 100.0)
                if armed_level == level and reclaim_price <= price < anchor:
                    response.update({
                        "action": "BUY",
                        "side": "entry",
                        "level": level,
                        "max_levels": max_levels,
                        "level_pct": dip_pct,
                        "anchor_price": anchor,
                        "threshold_price": threshold,
                        "reclaim_price": reclaim_price,
                        "level_amount": _grid_entry_level_amount(settings, level),
                        "reason": (
                            f"Scale-In L{level}/{max_levels}: price reclaimed ${reclaim_price:.4f} "
                            f"after a -{dip_pct:.2f}% dip from ${anchor:.4f}."
                        ),
                    })
                    return response
                if armed_level == level and price >= anchor:
                    settings["entry_grid_armed_level"] = 0
                    response["state_changed"] = True
            elif price <= threshold:
                response.update({
                    "action": "BUY",
                    "side": "entry",
                    "level": level,
                    "max_levels": max_levels,
                    "enabled_levels": enabled_levels,
                    "level_pct": dip_pct,
                    "anchor_price": anchor,
                    "threshold_price": threshold,
                    "level_amount": _grid_entry_level_amount(settings, level),
                    "reason": f"Scale-In L{level}/{max_levels}: -{dip_pct:.2f}% level reached.",
                })
    if not response.get("reason"):
        forecast = _continuous_grid_forecast_state(
            settings,
            current_price=price,
            has_position=has_position,
            average_entry_price=average_entry_price,
            position_qty=position_qty,
            current_atr=current_atr,
            price_history=price_history,
        )
        monitoring = []
        for side, label in (("entry", "Buy"), ("exit", "Sell")):
            state = forecast.get(side) or {}
            if not state.get("enabled"):
                continue
            if state.get("status") == "waiting_position":
                monitoring.append("Sell ladder waiting for a position")
                continue
            if state.get("status") == "all_levels_complete":
                monitoring.append(f"{label} ladder complete")
                continue
            level = state.get("next_level")
            trigger = _safe_float(state.get("trigger_price"), 0.0)
            allocation = _safe_float(state.get("allocation_pct"), 0.0)
            if level and trigger > 0:
                direction = "at/below" if side == "entry" else "at/above"
                monitoring.append(
                    f"Next {label} L{level} {direction} ${trigger:.4f} ({allocation:.1f}%)"
                )
        if monitoring:
            response["reason"] = "Adaptive Grid Monitoring: " + " | ".join(monitoring)
    return response


def _record_continuous_grid_fill(
    settings: dict,
    *,
    side: str,
    fill_price: float,
    was_grid_order: bool,
    had_position: bool,
    position_closed: bool = False,
    continue_cycle: bool = False,
    filled_level: int = None,
    current_atr: float = 0.0,
) -> None:
    price = _safe_float(fill_price, 0.0)
    if position_closed:
        if continue_cycle and price > 0:
            _clear_continuous_grid_pending(settings, "entry")
            _clear_continuous_grid_pending(settings, "exit")
            settings["grid_cycle_active"] = True
            settings["grid_flat_reentry_armed"] = True
            _clear_grid_context(settings, "entry")
            _clear_grid_context(settings, "exit")
            settings["entry_grid_anchor_price"] = price
            settings["entry_grid_high_water_price"] = price
            if _continuous_grid_kind(settings) == "atr" and current_atr > 0:
                settings["entry_grid_anchor_atr"] = float(current_atr)
            settings["entry_grid_level"] = 0
            settings["entry_grid_armed_level"] = 0
            settings["entry_grid_last_fill_price"] = settings.get("entry_grid_last_fill_price", 0.0)
            settings.pop("exit_grid_level", None)
            settings.pop("exit_grid_anchor_price", None)
            settings.pop("exit_grid_anchor_atr", None)
            settings.pop("exit_grid_anchor_qty", None)
            settings.pop("exit_grid_high_water_price", None)
            settings.pop("exit_grid_armed_level", None)
            settings["exit_grid_last_fill_price"] = price
            return
        _reset_continuous_grid_state(settings)
        return
    if side == "buy":
        if not had_position:
            settings["entry_grid_anchor_price"] = price
            settings["entry_grid_high_water_price"] = price
            if _continuous_grid_kind(settings) == "atr" and current_atr > 0:
                settings["entry_grid_anchor_atr"] = float(current_atr)
            if was_grid_order:
                target_level = min(
                    5,
                    max(1, int(_safe_float(filled_level, 1))),
                )
                settings["entry_grid_level"] = min(
                    5,
                    max(
                        max(0, int(_safe_float(settings.get("entry_grid_level"), 0))) + 1,
                        target_level,
                    ),
                )
            else:
                settings["entry_grid_level"] = 0
            settings["entry_grid_armed_level"] = 0
            settings["exit_grid_anchor_price"] = price
            settings["exit_grid_high_water_price"] = price
            if _continuous_grid_kind(settings) == "atr" and current_atr > 0:
                settings["exit_grid_anchor_atr"] = float(current_atr)
            settings["exit_grid_level"] = 0
            settings["grid_cycle_active"] = True
            settings["grid_flat_reentry_armed"] = False
        elif was_grid_order:
            prior_entry_level = max(0, int(_safe_float(settings.get("entry_grid_level"), 0)))
            target_level = min(
                5,
                max(1, int(_safe_float(filled_level, 0))),
            ) if filled_level else None
            settings["entry_grid_level"] = min(
                5,
                max(
                    max(0, int(_safe_float(settings.get("entry_grid_level"), 0))) + 1,
                    target_level or 0,
                ),
            )
            crossed_count = max(
                1,
                len([
                    level for level in _grid_enabled_levels(settings, "entry")
                    if prior_entry_level < level <= settings["entry_grid_level"]
                ]),
            )
            settings["entry_grid_armed_level"] = 0
            settings["exit_grid_level"] = _rewind_grid_level(
                settings,
                "exit",
                int(_safe_float(settings.get("exit_grid_level"), 0)),
                crossed_count,
            )
            settings.pop("exit_grid_armed_level", None)
        settings["entry_grid_last_fill_price"] = price
    elif side == "sell" and was_grid_order:
        prior_exit_level = max(0, int(_safe_float(settings.get("exit_grid_level"), 0)))
        target_level = min(
            5,
            max(prior_exit_level + 1, int(_safe_float(filled_level, prior_exit_level + 1))),
        )
        crossed_count = max(
            1,
            len([
                level for level in _grid_enabled_levels(settings, "exit")
                if prior_exit_level < level <= target_level
            ]),
        )
        settings["exit_grid_level"] = min(
            5,
            target_level,
        )
        settings["entry_grid_level"] = _rewind_grid_level(
            settings,
            "entry",
            int(_safe_float(settings.get("entry_grid_level"), 0)),
            crossed_count,
        )
        settings.pop("exit_grid_armed_level", None)
        settings["exit_grid_high_water_price"] = price
        if (
            _continuous_grid_kind(settings) == "pct"
            and target_level >= (_grid_enabled_levels(settings, "exit") or [target_level])[-1]
            and _pct_grid_regime_profile_active(settings)
            and not position_closed
        ):
            settings["exit_grid_runner_active"] = True
            settings["exit_grid_runner_high_water_price"] = price
        else:
            settings.pop("exit_grid_runner_active", None)
            settings.pop("exit_grid_runner_high_water_price", None)
        if price > 0 and _continuous_grid_pair_enabled(settings):
            # The latest realized sell is the reference for the next adaptive
            # buy-back ladder. This avoids anchoring forever to an old high.
            _clear_grid_context(settings, "entry")
            settings["entry_grid_anchor_price"] = price
            settings["entry_grid_high_water_price"] = price
            if _continuous_grid_kind(settings) == "atr" and current_atr > 0:
                settings["entry_grid_anchor_atr"] = float(current_atr)
            settings["entry_grid_armed_level"] = 0
        settings["exit_grid_last_fill_price"] = price


def _continuous_grid_sell_fraction(settings: dict, result: dict, current_qty: float = 0.0) -> float:
    if result.get("continuous_grid_side") != "exit":
        return 1.0
    if result.get("continuous_grid_runner_exit"):
        # The runner is the final risk-reducing action. Close the remaining
        # position instead of applying the ladder's partial sell percentage.
        return 1.0
    level = min(5, max(1, int(_safe_float(result.get("continuous_grid_level"), 1))))
    from_level = min(level, max(1, int(_safe_float(result.get("continuous_grid_from_level"), level))))
    sell_pct = sum(
        _grid_level_allocation_pct(settings, "exit", rung)
        for rung in _grid_enabled_levels(settings, "exit")
        if from_level <= rung <= level
    )
    anchor_qty = max(0.0, _safe_float(settings.get("exit_grid_anchor_qty"), 0.0))
    live_qty = max(0.0, _safe_float(current_qty, 0.0))
    if anchor_qty > 0 and live_qty > 0:
        return min(1.0, max(0.01, (anchor_qty * sell_pct / 100.0) / live_qty))
    return min(1.0, max(0.01, sell_pct / 100.0))


def _continuous_grid_forecast_state(
    settings: dict,
    *,
    current_price: float,
    has_position: bool,
    average_entry_price: float = 0.0,
    position_qty: float = 0.0,
    current_atr: float = 0.0,
    price_history=None,
) -> dict:
    """Describe the next executable grid rungs without mutating runtime state."""
    settings = dict(settings or {})
    if _continuous_grid_smart_enabled(settings):
        context = _grid_market_context(price_history, current_atr)
        for side in ("entry", "exit"):
            settings.setdefault(f"{side}_grid_trend_score", context["trend_score"])
            settings.setdefault(f"{side}_grid_volatility_ratio", context["volatility_ratio"])
    current = max(0.0, _safe_float(current_price, 0.0))
    average = max(0.0, _safe_float(average_entry_price, 0.0))
    qty = max(0.0, _safe_float(position_qty, 0.0))
    buy_mode = str(settings.get("buy_mode", "indicator") or "indicator").lower()
    sell_mode = str(settings.get("sell_mode", "indicator") or "indicator").lower()
    grid_kind = _continuous_grid_kind(settings)
    entry_enabled = bool(
        grid_kind == "atr"
        or (grid_kind == "pct" and settings.get("grid_continuous_mode") and buy_mode in {"percentage", "hybrid_pct"})
    )
    exit_enabled = bool(
        grid_kind == "atr"
        or (grid_kind == "pct" and settings.get("exit_grid_continuous_mode") and sell_mode in {"percentage", "hybrid_pct"})
    )

    def side_state(side: str) -> dict:
        is_entry = side == "entry"
        enabled = entry_enabled if is_entry else exit_enabled
        enabled_levels = _grid_enabled_levels(settings, side) if enabled else []
        completed = min(5, max(0, int(_safe_float(settings.get(f"{side}_grid_level"), 0))))
        completed_levels = [level for level in enabled_levels if level <= completed]
        next_level = _next_grid_level(settings, side, completed) if enabled_levels else None
        pending_level = min(
            5,
            max(0, int(_safe_float(settings.get(f"{side}_grid_pending_level"), 0))),
        )
        pending_order_id = str(settings.get(f"{side}_grid_pending_order_id") or "")
        display_level = pending_level if pending_order_id and pending_level else next_level
        anchor_key = "entry_grid_anchor_price" if is_entry else "exit_grid_anchor_price"
        anchor = _safe_float(settings.get(anchor_key), 0.0)
        if (
            not is_entry
            and has_position
            and average > 0
            and (anchor <= 0 or abs(anchor - average) / average > 0.000001)
        ):
            anchor = average
        if anchor <= 0:
            anchor = average or current
        anchor_atr = _safe_float(settings.get(f"{side}_grid_anchor_atr"), current_atr)
        trigger_atr = _grid_runtime_atr(settings, side, current_atr)
        trailing = _continuous_grid_trailing_enabled(settings)
        observed_peak = current
        history_rows = _grid_history_rows(price_history)
        if history_rows:
            observed_peak = max(observed_peak, history_rows[-1]["high"])
        high_water = _safe_float(settings.get(f"{side}_grid_high_water_price"), anchor)
        if trailing and is_entry:
            anchor = max(anchor, high_water, observed_peak)
            high_water = anchor
        elif trailing and not is_entry:
            high_water = max(anchor, high_water, observed_peak)
        armed_level = min(5, max(completed, int(_safe_float(settings.get("exit_grid_armed_level"), 0)))) if not is_entry else 0
        runner_active = bool(
            (not is_entry)
            and grid_kind == "pct"
            and settings.get("exit_grid_runner_active")
            and enabled_levels
            and has_position
        )
        runner_trail = _pct_grid_runner_trail_pct(settings) if runner_active else 0.0
        runner_trigger = None
        if runner_active:
            runner_peak = max(
                anchor,
                high_water,
                _safe_float(settings.get("exit_grid_runner_high_water_price"), anchor),
            )
            runner_trigger = runner_peak * (1.0 - runner_trail / 100.0)
            high_water = runner_peak
            display_level = enabled_levels[-1]
        activation_level = None
        activation_price = None
        if trailing and not is_entry and enabled_levels and anchor > 0:
            activation_level = _highest_crossed_grid_level(settings, "exit", completed, anchor, high_water, trigger_atr)
            armed_level = max(armed_level, activation_level)
            if armed_level > completed and not pending_order_id:
                display_level = armed_level
        trigger_level = (armed_level if not is_entry and armed_level > completed else display_level)
        base_level_pct = _grid_level_distance(settings, side, trigger_level) if trigger_level else None
        level_pct = _grid_effective_distance(settings, side, trigger_level, anchor, trigger_atr) if trigger_level else None
        if runner_active:
            base_level_pct = runner_trail
            level_pct = runner_trail
        trigger_price = None
        if anchor > 0 and level_pct is not None:
            activation_price = _grid_threshold_price(settings, side, anchor, level_pct, trigger_atr)
            trigger_price = (
                _grid_trailing_threshold(
                    settings, side, trigger_level, high_water, anchor,
                    trigger_atr if grid_kind == "atr" else current_atr,
                )
                if trailing and not is_entry and armed_level > completed
                else activation_price
            )
            if runner_active:
                trigger_price = runner_trigger
            if trigger_price <= 0:
                trigger_price = None
        allocation_pct = None
        allocation_amount = None
        allocation_levels = []
        if display_level:
            allocation_levels = [
                level for level in enabled_levels
                if completed < level <= display_level
            ] or [display_level]
            if runner_active:
                allocation_pct = 100.0
            elif is_entry:
                allocation_pct = sum(_grid_entry_level_buy_pct(settings, level) for level in allocation_levels)
                allocation_amount = sum(_grid_entry_level_amount(settings, level) for level in allocation_levels)
            else:
                allocation_pct = sum(
                    _grid_level_allocation_pct(settings, "exit", level)
                    for level in allocation_levels
                )
        if not enabled:
            status = "disabled"
        elif not enabled_levels:
            status = "no_levels"
        elif not is_entry and not has_position:
            status = "waiting_position"
        elif pending_order_id:
            status = "order_pending"
        elif is_entry and not has_position and not settings.get("grid_flat_reentry_armed", False):
            status = "waiting_initial_entry"
        elif runner_active:
            status = "runner_monitoring"
        elif next_level is None:
            status = "all_levels_complete"
        else:
            status = "monitoring"
        return {
            "enabled": enabled,
            "enabled_levels": enabled_levels,
            "completed_level": completed,
            "completed_levels": completed_levels,
            "completed_count": len(completed_levels),
            "enabled_count": len(enabled_levels),
            "next_level": next_level,
            "display_level": display_level,
            "trigger_level": trigger_level,
            "level_pct": level_pct,
            "base_level_pct": base_level_pct,
            "smart_dynamic": _continuous_grid_smart_enabled(settings),
            "trailing_enabled": trailing,
            "trailing_peak_price": high_water if trailing else None,
            "armed_level": armed_level if not is_entry and armed_level > completed else None,
            "activation_price": activation_price,
            "adaptive_factors": _grid_adaptive_factors(settings, side),
            "grid_kind": grid_kind,
            "distance_unit": "ATR" if grid_kind == "atr" else "%",
            "anchor_atr": anchor_atr if grid_kind == "atr" else None,
            "trigger_atr": trigger_atr if grid_kind == "atr" else None,
            "anchor_price": anchor,
            "trigger_price": trigger_price,
            "allocation_pct": allocation_pct,
            "allocation_amount": allocation_amount,
            "allocation_levels": allocation_levels,
            "runner_active": runner_active,
            "runner_pct": _pct_grid_runner_pct(settings) if runner_active else None,
            "runner_trail_pct": runner_trail if runner_active else None,
            "pct_regime_mode": _pct_grid_regime_mode(settings) if grid_kind == "pct" else None,
            "pct_regime_profile_active": _pct_grid_regime_profile_active(settings) if grid_kind == "pct" else False,
            "pct_runner_pct_configured": _pct_grid_runner_pct(settings) if grid_kind == "pct" else None,
            "pct_bullish_trend_threshold": _safe_float(settings.get("pct_grid_bullish_trend_threshold"), 0.35) if grid_kind == "pct" else None,
            "from_level": allocation_levels[0] if allocation_levels else None,
            "pending_level": pending_level or None,
            "pending_order_id": pending_order_id or None,
            "status": status,
            "flat_reentry_armed": bool(settings.get("grid_flat_reentry_armed", False)) if is_entry else False,
            "anchor_qty": _safe_float(settings.get("exit_grid_anchor_qty"), qty) if not is_entry else None,
        }

    entry = side_state("entry")
    exit_side = side_state("exit")
    return {
        "enabled": bool(entry["enabled"] or exit_side["enabled"]),
        "adaptive_cycle": bool(entry["enabled"] and exit_side["enabled"]),
        "current_price": current,
        "has_position": bool(has_position),
        "entry": entry,
        "exit": exit_side,
    }


def _price_history_days(price_history):
    return price_history_days_central(price_history)


def _ohlc_from_candles(candles):
    highs = [_safe_float(c.get("high"), None) for c in candles]
    lows = [_safe_float(c.get("low"), None) for c in candles]
    highs = [v for v in highs if v is not None]
    lows = [v for v in lows if v is not None]
    close = None
    for candle in reversed(candles):
        close = _safe_float(candle.get("close"), None)
        if close is not None:
            break
    if not highs or not lows or close is None:
        return None
    return {"high": max(highs), "low": min(lows), "close": close}


def _prior_day_ohlc_from_history(price_history):
    return _shared_prior_day_ohlc_from_history(price_history)


def _current_day_range_from_history(price_history):
    days = _price_history_days(price_history)
    if not days:
        return None
    ohlc = _ohlc_from_candles(days[-1][1])
    if not ohlc:
        return None
    return {"high": ohlc["high"], "low": ohlc["low"]}

FIXED_LEVEL_EXIT_MODES = {
    "custom_sltp",
    "sltp",
    "hybrid",
    "percentage",
    "hybrid_pct",
    "pivot_points",
    "hybrid_pivot",
    "high_low",
    "hybrid_high_low",
    "swing_points",
    "hybrid_swing",
}

HYBRID_EXIT_MODES = {
    "hybrid",
    "hybrid_pct",
    "hybrid_pivot",
    "hybrid_high_low",
    "hybrid_swing",
}


def _is_crypto_symbol(symbol: str) -> bool:
    clean = str(symbol or "").upper().replace("/", "")
    return clean.endswith(("USD", "USDT", "USDC")) or clean in {"BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"}


def _exit_signal_threshold(settings: dict, fallback: int = 1) -> int:
    return max(
        1,
        int(
            _safe_float(
                (settings or {}).get("min_sell_signals", (settings or {}).get("sell_threshold", fallback)),
                fallback,
            )
        ),
    )


def _mark_exit_signal_watch(result: dict, settings: dict, *, is_long: bool = True, prefix: str = "Position open") -> None:
    """Keep open-position scan logs focused on exit monitoring, not fresh entries."""
    threshold = _exit_signal_threshold(settings, getattr(global_config, "MIN_BEARISH_SIGNALS", 1))
    bullish_count = int(_safe_float(result.get("bullish_count"), 0))
    bearish_count = int(_safe_float(result.get("bearish_count"), 0))
    exit_count = bearish_count if is_long else bullish_count
    exit_direction = "BEARISH" if is_long else "BULLISH"
    exit_suffix = "S" if is_long else "B"
    result["action"] = "HOLD"
    result["exit_monitoring"] = True
    result["exit_signal_count"] = exit_count
    result["exit_signal_threshold"] = threshold
    result["exit_signal_direction"] = exit_direction
    result["exit_signal_label"] = f"Exit {exit_count}/{threshold}{exit_suffix}"
    result["reason"] = (
        f"{prefix}. Checking exit signals: {exit_count}/{threshold} {exit_direction} confirmed "
        f"({bullish_count}B / {bearish_count}S)."
    )


def _print_detailed_bot_scan(ticker: str, result: dict, t_settings: dict, has_position: bool, is_opt_bot: bool, ticker_norm: str) -> None:
    # 1. Header & Price Sizing
    price_raw = result.get('price_raw') or 0.0
    price_str = f"${price_raw:.2f}" if price_raw > 0 else "N/A"
    
    alloc_mode = str(t_settings.get('allocation_mode', 'fixed')).capitalize()
    alloc_amt = t_settings.get('compound_amount' if alloc_mode.lower() == 'compound' else 'amount')
    alloc_desc = f"{alloc_mode} ${alloc_amt:.2f}" if alloc_amt is not None else "N/A"
    
    # 2. Status & Asset Type & Timeframe
    is_crypto = any(ticker_norm.endswith(base) for base in ["USD", "USDT", "USDC"]) or ticker.upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
    asset_type = "Options" if is_opt_bot else "Crypto" if is_crypto else "Stocks"
    tf_to_use = t_settings.get('timeframe', '1Hour')
    
    # 3. Entry/Exit Setup
    bullish_count = int(result.get('bullish_count', 0))
    min_buy = int(t_settings.get('min_buy_signals', t_settings.get('threshold', 4)))
    entry_desc = f"{bullish_count}/{min_buy}B"
    
    exit_desc = "N/A"
    sell_mode = t_settings.get('sell_mode', 'indicator')
    if has_position:
        exit_desc = sell_mode
        
    # 4. Running for & Last Trade & Trades Counts
    uptime_desc = "N/A"
    started_at = t_settings.get('started_at') or t_settings.get('created_at')
    if started_at:
        try:
            clean_ts = started_at
            if clean_ts.endswith('Z'):
                clean_ts = clean_ts[:-1]
            diff = datetime.utcnow() - datetime.fromisoformat(clean_ts)
            days = diff.days
            hours = diff.seconds // 3600
            if days > 0:
                uptime_desc = f"{days}d {hours}h"
            else:
                minutes = (diff.seconds // 60) % 60
                if hours > 0:
                    uptime_desc = f"{hours}h {minutes}m"
                else:
                    uptime_desc = f"{minutes}m"
        except Exception:
            uptime_desc = "N/A"
            
    last_trade_desc = "No trades yet"
    last_trade_at = t_settings.get('last_trade_at') or t_settings.get('last_trade_date')
    if last_trade_at:
        try:
            clean_ts = last_trade_at
            if clean_ts.endswith('Z'):
                clean_ts = clean_ts[:-1]
            diff = datetime.utcnow() - datetime.fromisoformat(clean_ts)
            days = diff.days
            hours = diff.seconds // 3600
            if days > 0:
                last_trade_desc = f"{days}d ago"
            elif hours > 0:
                last_trade_desc = f"{hours}h ago"
            else:
                minutes = (diff.seconds // 60) % 60
                last_trade_desc = f"{minutes}m ago"
        except Exception:
            last_trade_desc = "N/A"
            
    today_trades = t_settings.get('today_trades', 0)
    total_trades = t_settings.get('total_trades', 0)
    
    # 5. Forecast Entry Strategy Tooltip/Details
    strategy_mode = "Standard"
    if t_settings.get('strategy_mode') == 'hybrid' or 'hybrid' in str(sell_mode).lower():
        strategy_mode = "Hybrid"
    if t_settings.get('ai_autopilot'):
        strategy_mode = "AI Autopilot"
        
    strategy_trigger_desc = f"Opens long when {min_buy} enabled bullish signals confirm"
    if 'atr' in str(sell_mode).lower():
        stop_mult = t_settings.get('atr_stop_multiplier', 2.0)
        strategy_trigger_desc += f" | Trigger: ATR move x{stop_mult}"
        
    pool_list = [k for k, v in result.get('signals', {}).items() if v.get('enabled')]
    pool_desc = ", ".join(pool_list) if pool_list else "None"
    
    # 6. Scan Log Details
    status_lbl = "POSITION OPEN" if has_position else "WAITING ENTRY"
    decision = result.get('action', 'HOLD')
    reason = result.get('reason', '')
    
    # 7. Confirmed / Waiting / Opposing signals
    confirmed_sigs = []
    waiting_sigs = []
    opposing_sigs = []
    # Exit monitoring evaluates the direction needed to close the position.
    # Keep marker colors tied to that target: bearish confirmations are red
    # and bullish signals opposing a bearish target are green.
    target_dir = str(result.get("exit_signal_direction") or ("BEARISH" if has_position else "BULLISH")).upper()
    opposing_dir = "BEARISH" if target_dir == "BULLISH" else "BULLISH"
    confirmed_marker = "🔴" if target_dir == "BEARISH" else "🟢"
    opposing_marker = "🟢" if target_dir == "BEARISH" else "🔴"
    
    for name, sig_data in result.get('signals', {}).items():
        if not sig_data.get('enabled', True):
            continue
        sig_val = sig_data.get('signal', 'NEUTRAL')
        desc = sig_data.get('description', '')
        sig_desc = f" ({desc})" if desc else ""
        
        if sig_val == target_dir:
            confirmed_sigs.append(f"{confirmed_marker} {name} {sig_val}{sig_desc}")
        elif sig_val == opposing_dir:
            opposing_sigs.append(f"{opposing_marker} {name} {sig_val}{sig_desc}")
        else:
            waiting_sigs.append(f"🟡 {name} {sig_val}{sig_desc}")
            
    signals_line = ""
    if confirmed_sigs:
        signals_line += f"Confirmed: {', '.join(confirmed_sigs)}"
    if waiting_sigs:
        if signals_line: signals_line += " | "
        signals_line += f"Waiting: {', '.join(waiting_sigs)}"
    if opposing_sigs:
        if signals_line: signals_line += " | "
        signals_line += f"Opposing: {', '.join(opposing_sigs)}"

    # 8. Reorder and print captured stdout logs if present
    captured_logs = result.get('_captured_logs', '')
    log_lines = []
    if captured_logs:
        lines = [line.strip() for line in captured_logs.split('\n') if line.strip()]
        perf_lines = [l for l in lines if l.startswith('[perf]')]
        risk_lines = [l for l in lines if l.startswith('[risk]')]
        other_lines = [l for l in lines if not l.startswith('[perf]') and not l.startswith('[risk]')]
        log_lines.extend(perf_lines)
        log_lines.extend(risk_lines)
        log_lines.extend(other_lines)

    # Print unified formatted block to console
    print("-" * 80)
    for l in log_lines:
        print(l)
    if log_lines:
        print() # Spacer line
        
    print(f"🤖 {ticker} Bot Scan: {price_str} | {alloc_desc} | {asset_type} ({tf_to_use})")
    print(f"   Status: ACTIVE | Entry: {entry_desc} | Exit: {exit_desc}")
    print(f"   Uptime: {uptime_desc} | Last trade: {last_trade_desc} | Trades: {today_trades} TODAY / {total_trades} TOTAL")
    print(f"   Strategy: {strategy_mode} | Trigger: {strategy_trigger_desc} | Pool: [{pool_desc}]")
    print(f"   Scan Log: {status_lbl} | Decision: {decision}")
    print(f"   Detail: {reason}")
    if signals_line:
        print(f"   Signals: {signals_line}")
    print("-" * 80)


def _mark_exit_level_watch(result: dict, label: str, reason: str, details: dict = None) -> None:
    result["action"] = "HOLD"
    result["exit_monitoring"] = True
    result["exit_signal_label"] = label
    result["exit_watch_type"] = label
    result["exit_watch_details"] = details or {}
    result["reason"] = reason


def _next_above(levels, current_price, fallback_step):
    valid = sorted([float(v) for v in levels if v is not None and float(v) > current_price])
    if valid:
        return valid[0]
    step = abs(float(fallback_step or 0.0))
    return current_price + step if step > 0 else None


def _nearest_below(levels, current_price):
    valid = sorted([float(v) for v in levels if v is not None and float(v) < current_price], reverse=True)
    return valid[0] if valid else None


def _dynamic_pivot_levels(prev_h, prev_l, prev_c, current_price):
    day_range = max(float(prev_h) - float(prev_l), 0.0)
    if day_range <= 0:
        return None
    p_val = (float(prev_h) + float(prev_l) + float(prev_c)) / 3.0
    r1 = (2 * p_val) - float(prev_l)
    s1 = (2 * p_val) - float(prev_h)
    r2 = p_val + day_range
    s2 = p_val - day_range
    r3 = float(prev_h) + 2 * (p_val - float(prev_l))
    s3 = float(prev_l) - 2 * (float(prev_h) - p_val)
    tp = _next_above([r1, r2, r3, r3 + day_range], current_price, day_range)
    sl = _nearest_below([r3, r2, r1, p_val, s1, s2, s3], current_price)
    return {"tp": tp, "sl": sl, "p": p_val, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}


def _dynamic_high_low_levels(prev_h, prev_l, current_price, buffer_pct=0.0):
    return _buffered_dynamic_high_low_exit_levels(prev_h, prev_l, current_price, buffer_pct)

class UserEngine:
    def __init__(self, uid: str, db):
        self.uid = uid
        self.db = db
        self.last_access_at = time.monotonic()
        self.config = UserConfig(uid)
        self.broker = create_broker(self.uid)
        # Namespace broker orders so shared-account cancellation can be scoped
        # to this authenticated user.
        self.broker.order_owner_tag = hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:10]
        
        self.trade_log = []
        self.latest_scans = {}
        self.latest_scans_by_tf = {}
        self.last_trade_timestamps = {}
        self.last_trailing_stops = {}
        self.bot_scans = {}
        self.bot_scans_by_tf = {}
        self.last_scan_timestamps = {}
        self.last_bot_scan_time = {}
        self.last_history_save_ts = 0
        self._last_cloud_settings_digest = None
        self._last_cloud_history_digest = None
        self._cloud_settings_retry_at = 0.0
        self._cloud_settings_failures = 0
        self._cloud_history_retry_at = 0.0
        self._cloud_history_failures = 0
        self.last_global_risk_save_ts = 0
        self.last_global_risk_signature = None
        self.last_global_halt_cancel_event_id = None
        self.signal_locked_direction = {}
        self.exit_order_state = {}
        self.scan_log_state = {}
        
        self.bot_running = False
        self.task = None
        self.force_scan_trigger = asyncio.Event()
        self.last_scan_time = None
        self.dashboard_primary_ticker = None
        self.loaded_from_cloud = False
        self.legal_settings_loaded = False
        # This is intentionally transient.  Persisted bot settings may be
        # loaded after restart, but live order submission requires a fresh
        # authenticated confirmation in the current process.
        self.live_trading_armed = False
        self.alpaca_load_status = "not_loaded"
        self.alpaca_load_error = ""
        self.load_lock = asyncio.Lock()
        self.broker_connect_lock = asyncio.Lock()
        self.task_start_lock = asyncio.Lock()
        self.settings_save_lock = asyncio.Lock()
        self.history_save_lock = asyncio.Lock()
        
    def get_now(self):
        tz = pytz.timezone(self.config.TIMEZONE if hasattr(self.config, 'TIMEZONE') else 'US/Central')
        return datetime.now(tz)

    def _order_intent_id(self, ticker: str, side: str, result: dict, instrument: str = "") -> str:
        """Return a stable broker id for one strategy decision.

        The candle identity and decision level stay constant across a retry,
        so an uncertain network response cannot become a second broker order.
        """
        candle = (result or {}).get("candle_meta") or {}
        candle_key = candle.get("candle_key") or (result or {}).get("time") or "unknown"
        level = (result or {}).get("level") or (result or {}).get("continuous_grid_side") or "base"
        raw = f"{self.uid}:{ticker}:{side}:{instrument}:{candle_key}:{level}"
        owner_tag = getattr(self.broker, "order_owner_tag", "")
        return f"bb-{owner_tag}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"

    def _order_result_is_filled(self, order_result: dict) -> bool:
        """Do not mutate strategy state from a mere broker submission receipt."""
        if not isinstance(order_result, dict) or not order_result.get("success"):
            return False
        if getattr(self.broker, "simulation_mode", True):
            return True
        status = str(order_result.get("status") or "").lower().replace(" ", "_")
        # A partial fill is still an outstanding intent.  Treating it as a
        # complete fill would overstate position quantity and could submit a
        # duplicate remainder on the next scan.
        return status == "filled"

    def _normalize_signal_lock(self, value):
        direction = str(value or "").upper().strip()
        return direction if direction in {"BUY", "SELL"} else None

    def _hydrate_signal_reset_locks(self):
        hydrated = {}
        for ticker, settings in self.config.TICKER_SETTINGS.items():
            if not isinstance(settings, dict):
                continue
            locked_dir = self._normalize_signal_lock(settings.get("signal_locked_direction"))
            if locked_dir:
                ticker_key = str(ticker or "").upper().strip()
                hydrated[ticker_key] = locked_dir
                settings["signal_locked_direction"] = locked_dir
            else:
                settings.pop("signal_locked_direction", None)
        self.signal_locked_direction = hydrated

    def _set_signal_reset_lock(self, ticker, direction, t_settings=None):
        ticker_key = str(ticker or "").upper().strip()
        locked_dir = self._normalize_signal_lock(direction)
        if not ticker_key or not locked_dir:
            return None
        self.signal_locked_direction[ticker_key] = locked_dir
        settings = t_settings if isinstance(t_settings, dict) else self.config.TICKER_SETTINGS.setdefault(ticker_key, {})
        if isinstance(settings, dict):
            settings["signal_locked_direction"] = locked_dir
        return locked_dir

    def _clear_signal_reset_lock(self, ticker, t_settings=None):
        ticker_key = str(ticker or "").upper().strip()
        changed = False
        if ticker_key:
            changed = self.signal_locked_direction.pop(ticker_key, None) is not None
        settings = t_settings if isinstance(t_settings, dict) else self.config.TICKER_SETTINGS.get(ticker_key, {})
        if isinstance(settings, dict):
            changed = settings.pop("signal_locked_direction", None) is not None or changed
        return changed

    def _get_signal_reset_lock(self, ticker, t_settings=None):
        ticker_key = str(ticker or "").upper().strip()
        locked_dir = self._normalize_signal_lock(self.signal_locked_direction.get(ticker_key))
        if locked_dir:
            return locked_dir
        settings = t_settings if isinstance(t_settings, dict) else self.config.TICKER_SETTINGS.get(ticker_key, {})
        if isinstance(settings, dict):
            locked_dir = self._normalize_signal_lock(settings.get("signal_locked_direction"))
            if locked_dir and ticker_key:
                self.signal_locked_direction[ticker_key] = locked_dir
                settings["signal_locked_direction"] = locked_dir
                return locked_dir
        return None

    def _signal_reset_threshold(self, locked_dir, t_settings):
        if locked_dir == "SELL":
            keys = ("min_sell_signals", "sell_threshold")
            default_value = getattr(self.config, "MIN_BEARISH_SIGNALS", 1)
        else:
            keys = ("min_buy_signals", "threshold", "buy_threshold")
            default_value = getattr(self.config, "MIN_BULLISH_SIGNALS", 1)
        for key in keys:
            value = (t_settings or {}).get(key)
            if value is None or value == "":
                continue
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                continue
        try:
            return max(1, int(default_value))
        except (TypeError, ValueError):
            return 1

    def _signal_reset_count(self, result, locked_dir):
        key = "bearish_count" if locked_dir == "SELL" else "bullish_count"
        try:
            return float(result.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _signal_reset_is_reset(self, result, locked_dir, t_settings):
        # A setup is fully reset only after every same-direction primary vote
        # has dropped out. Falling merely below the entry threshold is not a
        # new signal cycle and must not permit re-entry.
        return self._signal_reset_count(result, locked_dir) <= 0

    def _refresh_daily_guard_state(self, t_settings):
        today = self.get_now().strftime("%Y-%m-%d")
        if t_settings.get("risk_guard_date") != today:
            t_settings["risk_guard_date"] = today
            t_settings["daily_realized_pl"] = 0.0

    def _pause_for_consecutive_losses(self, ticker, t_settings):
        t_settings["max_consecutive_losses"] = BOT_CONSECUTIVE_LOSS_PAUSE_THRESHOLD
        consecutive = max(0, int(_safe_float(t_settings.get("consecutive_losses"), 0)))
        if consecutive < BOT_CONSECUTIVE_LOSS_PAUSE_THRESHOLD or t_settings.get("paused", False):
            return False

        paused_at = self.get_now().isoformat()
        t_settings["paused"] = True
        t_settings["paused_at"] = paused_at
        t_settings["pause_code"] = "consecutive_losses"
        t_settings["pause_requires_review"] = True
        t_settings["pause_event_id"] = f"{str(ticker or '').upper().strip()}:{paused_at}"
        t_settings["pause_reason"] = (
            "Paused after 3 consecutive losing trades. Review the bot settings, "
            "then activate the bot again."
        )
        try:
            self.broker.cancel_orders_for_ticker(ticker)
        except Exception as err:
            print(f"[engine-{self.uid}] Error cancelling orders after loss-streak pause for {ticker}: {err}")
        print(f"[engine-{self.uid}] Auto-paused {ticker} after 3 consecutive losing trades.")
        return True

    def _record_realized_exit(self, ticker, t_settings, pl, reason="", exit_price=None, direction="BUY", complete=True):
        pl = _safe_float(pl, 0.0)
        t_settings["realized_pl"] = round(_safe_float(t_settings.get("realized_pl"), 0.0) + pl, 2)
        self._refresh_daily_guard_state(t_settings)
        t_settings["daily_realized_pl"] = round(_safe_float(t_settings.get("daily_realized_pl"), 0.0) + pl, 2)
        if complete:
            now = self.get_now()
            t_settings["last_exit_at"] = now.isoformat()
            self.last_trade_timestamps[str(ticker or "").upper().strip()] = now
            t_settings["consecutive_losses"] = int(t_settings.get("consecutive_losses", 0) or 0) + 1 if pl < 0 else 0
            # Three completed losing trades in a row is a hard live-bot safety
            # pause. The streak intentionally survives a date change and is
            # reset only by a non-losing completed trade or manual reactivation.
            self._pause_for_consecutive_losses(ticker, t_settings)
            if is_stop_exit(reason):
                t_settings["post_stop_state"] = {
                    "direction": self._normalize_signal_lock(direction) or "BUY",
                    "exit_price": _safe_float(exit_price, 0.0),
                    "time": self.get_now().isoformat(),
                }

    def _hydrate_trade_guard_state(self):
        hydrated = {}
        for ticker, settings in self.config.TICKER_SETTINGS.items():
            if not isinstance(settings, dict):
                continue
            raw = settings.get("last_exit_at")
            if not raw:
                continue
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                now = self.get_now()
                if parsed.tzinfo is None:
                    parsed = now.tzinfo.localize(parsed) if hasattr(now.tzinfo, "localize") else parsed.replace(tzinfo=now.tzinfo)
                hydrated[str(ticker or "").upper().strip()] = parsed.astimezone(now.tzinfo)
            except Exception:
                continue
        self.last_trade_timestamps.update(hydrated)

    def _entry_loss_circuit_reason(self, t_settings, allocated_capital):
        self._refresh_daily_guard_state(t_settings)
        max_losses = BOT_CONSECUTIVE_LOSS_PAUSE_THRESHOLD
        t_settings["max_consecutive_losses"] = max_losses
        consecutive = max(0, int(_safe_float(t_settings.get("consecutive_losses"), 0)))
        if max_losses and consecutive >= max_losses:
            return f"Loss circuit breaker active ({consecutive} consecutive losses)"
        max_daily_loss_pct = max(0.0, _safe_float(t_settings.get("max_daily_drawdown"), 0.0))
        daily_pl = _safe_float(t_settings.get("daily_realized_pl"), 0.0)
        if allocated_capital > 0 and max_daily_loss_pct > 0 and daily_pl <= -(allocated_capital * max_daily_loss_pct):
            return f"Daily strategy-loss limit active (${abs(daily_pl):.2f} loss)"
        return None

    def _post_stop_block_reason(self, ticker, t_settings, result, timeframe):
        state = t_settings.get("post_stop_state")
        if not isinstance(state, dict):
            return None
        try:
            stopped_at = datetime.fromisoformat(str(state.get("time", "")).replace("Z", "+00:00"))
            now = self.get_now()
            if stopped_at.tzinfo is None:
                stopped_at = now.tzinfo.localize(stopped_at) if hasattr(now.tzinfo, "localize") else stopped_at.replace(tzinfo=now.tzinfo)
            elapsed_bars = int(max(0.0, (now - stopped_at).total_seconds()) // max(1, parse_timeframe_to_seconds(timeframe)))
        except Exception:
            elapsed_bars = 0
        history = result.get("price_history") or []
        latest = dict(history[-1]) if history and isinstance(history[-1], dict) else {}
        latest.setdefault("Close", result.get("price_raw"))
        latest.setdefault("Open", latest.get("open", result.get("price_raw")))
        latest.setdefault("VWAP", result.get("vwap"))
        reason = stabilization_reason(
            result.get("action"),
            latest,
            state,
            elapsed_bars,
            max(0, int(_safe_float(t_settings.get("post_stop_stabilization_bars"), 3))),
        )
        if reason is None:
            t_settings.pop("post_stop_state", None)
        return reason

    def _live_liquidity_block_reason(self, ticker, t_settings, is_crypto):
        if not bool(t_settings.get("enable_liquidity_filter", True)):
            return None, None
        quote = self.broker.get_latest_quote(ticker)
        if not quote:
            return "Liquidity filter: current two-sided quote unavailable", None
        age = None
        timestamp = quote.get("timestamp")
        if timestamp is not None:
            try:
                quote_time = timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                now_utc = datetime.now(pytz.utc)
                if quote_time.tzinfo is None:
                    quote_time = pytz.utc.localize(quote_time)
                age = max(0.0, (now_utc - quote_time.astimezone(pytz.utc)).total_seconds())
            except Exception:
                age = None
        max_spread = _safe_float(
            t_settings.get("crypto_max_spread_pct" if is_crypto else "stock_max_spread_pct"),
            0.50 if is_crypto else 0.25,
        )
        reason, spread_pct, midpoint = quote_liquidity_reason(
            bid=quote.get("bid"),
            ask=quote.get("ask"),
            max_spread_pct=max_spread,
            quote_age_seconds=age,
            max_quote_age_seconds=_safe_float(t_settings.get("max_quote_age_seconds"), 15.0),
        )
        return reason, {**quote, "spread_pct": spread_pct, "midpoint": midpoint, "age_seconds": age}
        
    async def save_settings(self):
        async with self.settings_save_lock:
            try:
                import hashlib, time, json, os
                data = {
                    "watchlist": self.config.WATCHLIST,
                    "tradelist": self.config.TRADELIST,
                    "strategy_timeframe": self.config.DEFAULT_TIMEFRAME,
                    "ticker_settings": self.config.TICKER_SETTINGS,
                    "toggles": self.config.toggles,
                    "parameters": self.config.parameters,
                    "contexts": getattr(self.config, 'contexts', {}),
                    "dashboard_indicator_workspace": getattr(self.config, 'dashboard_indicator_workspace', {}),
                    "global_risk_state": getattr(self.config, 'global_risk_state', {}),
                    "legal_acceptance": getattr(self.config, 'legal_acceptance', {}),
                    "saved_at": time.time()
                }
                try:
                    os.makedirs("data", mode=0o700, exist_ok=True)
                    try:
                        os.chmod("data", 0o700)
                    except OSError:
                        pass
                    local_path = _local_settings_path(self.uid)
                    fd, tmp_path = tempfile.mkstemp(prefix=".user-settings.", dir="data", text=True)
                    try:
                        os.fchmod(fd, 0o600)
                        with os.fdopen(fd, "w") as handle:
                            json.dump(data, handle, indent=2)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(tmp_path, local_path)
                        os.chmod(local_path, 0o600)
                    except Exception:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        raise
                except Exception as ex_loc:
                    print(f"[engine-{self.uid}] Failed writing local settings backup: {ex_loc}")

                if not self.db: return
                if time.monotonic() < self._cloud_settings_retry_at:
                    return False
                digest_data = dict(data)
                digest_data.pop("saved_at", None)
                cloud_digest = hashlib.sha256(
                    json.dumps(digest_data, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if cloud_digest == self._last_cloud_settings_digest:
                    return True
                def _sync_save():
                    self.db.collection("users").document(self.uid).collection("settings").document("ui").set(
                        data,
                        timeout=2.5,
                        retry=None,
                    )
                await asyncio.wait_for(asyncio.to_thread(_sync_save), timeout=3.0)
                self._last_cloud_settings_digest = cloud_digest
                self._cloud_settings_failures = 0
                self._cloud_settings_retry_at = 0.0
                return True
            except asyncio.TimeoutError:
                self._cloud_settings_failures += 1
                delay = min(300.0, 30.0 * (2 ** min(self._cloud_settings_failures - 1, 3)))
                self._cloud_settings_retry_at = time.monotonic() + delay
                print(f"[engine-{self.uid}] Firestore save_settings timed out or was quota-limited; backing off {delay:.0f}s. Local backup remains authoritative.")
                return False
            except Exception as e:
                self._cloud_settings_failures += 1
                delay = min(300.0, 30.0 * (2 ** min(self._cloud_settings_failures - 1, 3)))
                self._cloud_settings_retry_at = time.monotonic() + delay
                print(f"[engine-{self.uid}] Error saving settings to Firestore: {e}; backing off {delay:.0f}s. Local backup remains authoritative.")
                return False

    async def save_history(self):
        async with self.history_save_lock:
            try:
                import hashlib, json
                from local_store import save_state
                def _strip_heavy_data(log_list):
                    if isinstance(log_list, list):
                        stripped = []
                        for entry in log_list[:100]:
                            clean_entry = entry.copy()
                            if "price_history" in clean_entry: del clean_entry["price_history"]
                            stripped.append(clean_entry)
                    elif isinstance(log_list, dict):
                        stripped = {}
                        for k, entry in log_list.items():
                            clean_entry = entry.copy()
                            if "price_history" in clean_entry: del clean_entry["price_history"]
                            stripped[k] = clean_entry
                    else:
                        stripped = []
                    return stripped

                cloud_scans = _strip_heavy_data(self.trade_log)
                cloud_bot_scans = _strip_heavy_data(self.bot_scans)
                cloud_payload = {"scans": cloud_scans, "bot_scans": cloud_bot_scans}
                cloud_digest = hashlib.sha256(
                    json.dumps(cloud_payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if cloud_digest == self._last_cloud_history_digest:
                    return True

                await asyncio.to_thread(
                    save_state,
                    self.uid,
                    "bots_history",
                    {"trade_log": cloud_scans, "bot_scans": cloud_bot_scans},
                )
                self._last_cloud_history_digest = cloud_digest
                return True
            except Exception as e:
                print(f"[engine-{self.uid}] Local SQLite history save failed: {e}. History remains active in memory.")
                return False

    async def save_history_throttled(self, force: bool = False, min_interval_seconds: int = 30):
        """Persist scan history without making fast option loops write every heartbeat."""
        now_ts = time.time()
        if not force and (now_ts - self.last_history_save_ts) < min_interval_seconds:
            return
        saved = await self.save_history()
        if saved:
            self.last_history_save_ts = now_ts

    async def load_from_cloud(self, *, connect_broker: bool = True):
        if not self.db:
            # Without a persistence backend there are no saved user choices
            # to restore, so keep first-use indicator activation disabled.
            self.config.disable_indicator_toggles()
            self.alpaca_load_status = "no_firestore"
            self.alpaca_load_error = "Firestore is not configured on the server."
            return False
        self.alpaca_load_status = "loading"
        self.alpaca_load_error = ""
        try:
            # Load Alpaca Settings
            try:
                doc_alpaca = None
                if connect_broker:
                    doc_alpaca = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.db.collection("users").document(self.uid).collection("settings").document("alpaca").get,
                            timeout=2.5,
                            retry=None,
                        ),
                        timeout=3.0,
                    )
                if doc_alpaca is None:
                    self.alpaca_load_status = "deferred"
                    self.alpaca_load_error = "Broker settings are deferred until an authenticated workspace request."
                elif not doc_alpaca.exists:
                    self.alpaca_load_status = "not_configured"
                    self.alpaca_load_error = "No Alpaca settings document exists for this Firebase user."
                else:
                    data = doc_alpaca.to_dict() or {}
                    from main import vault
                    api_cipher = data.get("api_key", "")
                    sec_cipher = data.get("secret_key", "")
                    decrypted_key = vault.decrypt(api_cipher)
                    decrypted_secret = vault.decrypt(sec_cipher)
                    if not decrypted_key or not decrypted_secret:
                        self.alpaca_load_status = "decrypt_failed"
                        self.alpaca_load_error = "Alpaca credentials were found but could not be decrypted with the current server vault key."
                    else:
                        if vault.needs_reencrypt(api_cipher) or vault.needs_reencrypt(sec_cipher):
                            try:
                                rewrapped = {
                                    "api_key": vault.reencrypt(api_cipher),
                                    "secret_key": vault.reencrypt(sec_cipher),
                                    "updated_at": firestore.SERVER_TIMESTAMP,
                                }
                                await asyncio.to_thread(
                                    self.db.collection("users").document(self.uid).collection("settings").document("alpaca").set,
                                    rewrapped,
                                    merge=True,
                                )
                                print(f"[engine-{self.uid}] Re-encrypted stored Alpaca credentials with the active vault version.")
                            except Exception as rewrap_exc:
                                print(f"[engine-{self.uid}] Vault re-encryption deferred: {type(rewrap_exc).__name__}")
                        self.config.ALPACA_API_KEY = decrypted_key
                        self.config.ALPACA_SECRET_KEY = decrypted_secret
                        self.config.ALPACA_PAPER = data.get("paper", True)
                        if connect_broker:
                            try:
                                async with self.broker_connect_lock:
                                    connected = await asyncio.wait_for(
                                        asyncio.to_thread(
                                            self.broker.connect,
                                            self.config.ALPACA_API_KEY,
                                            self.config.ALPACA_SECRET_KEY,
                                            self.config.ALPACA_PAPER,
                                        ),
                                        timeout=15.0,
                                    )
                            except asyncio.TimeoutError:
                                connected = False
                                self.alpaca_load_status = "connect_timeout"
                                self.alpaca_load_error = "Alpaca connection timed out; no broker write was attempted."
                        else:
                            connected = False
                            self.alpaca_load_status = "deferred"
                            self.alpaca_load_error = "Broker connection is deferred until an authenticated workspace request."
                        self.live_trading_armed = False
                        self.broker.live_trading_armed = False
                        if not connect_broker:
                            pass
                        elif connected and self.alpaca_load_status != "connect_timeout":
                            self.alpaca_load_status = "connected"
                            self.alpaca_load_error = ""
                        elif self.alpaca_load_status != "connect_timeout":
                            self.alpaca_load_status = "connect_failed"
                            self.alpaca_load_error = "Alpaca rejected the saved credentials or the broker client is unavailable."
            except Exception as alpaca_exc:
                self.alpaca_load_status = "firestore_error"
                self.alpaca_load_error = f"Could not read Alpaca settings from Firestore: {alpaca_exc}"
                print(f"[engine-{self.uid}] Alpaca settings load error: {alpaca_exc}")

            # Load UI Settings
            ui = {}
            import json, os
            local_ui = {}
            local_path = _local_settings_path(self.uid)
            if os.path.exists(local_path):
                try:
                    with open(local_path, "r") as f:
                        local_ui = json.load(f)
                except Exception as ex_loc:
                    print(f"[engine-{self.uid}] Failed reading local settings backup: {ex_loc}")

            try:
                doc_ui = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.db.collection("users").document(self.uid).collection("settings").document("ui").get,
                        timeout=2.5,
                        retry=None,
                    ),
                    timeout=3.0,
                )
                if doc_ui.exists:
                    ui = doc_ui.to_dict()
            except Exception as ex_fs:
                print(f"[engine-{self.uid}] Could not load UI settings from Firestore ({ex_fs}); checking local backup.")

            if local_ui and (not ui or local_ui.get("saved_at", 0) >= ui.get("saved_at", 0)):
                ui = local_ui
                print(f"[engine-{self.uid}] Loaded latest settings from local disk backup (saved_at: {ui.get('saved_at')}).")
            elif ui:
                print(f"[engine-{self.uid}] Loaded latest settings from Firestore.")

            if ui:
                self.config.WATCHLIST = ui.get("watchlist", self.config.WATCHLIST)
                self.config.TRADELIST = ui.get("tradelist", self.config.TRADELIST)
                self.config.DEFAULT_TIMEFRAME = ui.get("strategy_timeframe", self.config.DEFAULT_TIMEFRAME)
                persisted_toggles = ui.get("toggles", {})
                if isinstance(persisted_toggles, dict):
                    self.config.toggles.update({
                        str(key): bool(value)
                        for key, value in persisted_toggles.items()
                        if str(key).startswith("ENABLE_")
                    })
                persisted_parameters = ui.get("parameters", {})
                if isinstance(persisted_parameters, dict):
                    self.config.parameters.update(persisted_parameters)
                self.config.TICKER_SETTINGS = ui.get("ticker_settings", {})
                # Live bots always retain the server-side risk policy. Older
                # persisted settings may contain a client-supplied opt-out;
                # normalize that state before any loop can evaluate entries.
                for _ticker_settings in self.config.TICKER_SETTINGS.values():
                    if isinstance(_ticker_settings, dict):
                        _ticker_settings["enable_risk_management"] = True
                self.config.legal_acceptance = ui.get("legal_acceptance", {}) if isinstance(ui.get("legal_acceptance", {}), dict) else {}
                workspace = ui.get("dashboard_indicator_workspace", {})
                self.config.dashboard_indicator_workspace = workspace.copy() if isinstance(workspace, dict) else {}
                risk_state = ui.get("global_risk_state", {})
                self.config.global_risk_state = risk_state.copy() if isinstance(risk_state, dict) else {}
                self._hydrate_signal_reset_locks()
                self._hydrate_trade_guard_state()
                
                # Load isolated contexts if present
                if not hasattr(self.config, 'contexts') or not self.config.contexts:
                    self.config.contexts = {
                        "dashboard": { "toggles": {}, "parameters": {} },
                        "bots": { "toggles": {}, "parameters": {} },
                        "backtest": { "toggles": {}, "parameters": {} }
                    }
                cloud_contexts = ui.get("contexts", {})
                for ctx_name, ctx_data in cloud_contexts.items():
                    if ctx_name in self.config.contexts:
                        self.config.contexts[ctx_name]["toggles"].update(ctx_data.get("toggles", {}))
                        self.config.contexts[ctx_name]["parameters"].update(ctx_data.get("parameters", {}))
            else:
                # A first-use account has no saved choices yet. Keep all
                # indicator/AI activation toggles off while retaining the
                # normal parameter defaults above. Existing accounts enter the
                # branch above and keep their persisted settings.
                self.config.disable_indicator_toggles()
                
            # High-frequency scan history is local-first SQLite state. It is
            # loaded independently of Firestore so quota outages cannot erase
            # or delay the Bots page history.
            from local_store import load_state
            local_history = (
                await asyncio.to_thread(load_state, self.uid, "bots_history")
                if connect_broker else None
            )
            if local_history:
                raw_logs = local_history.get("trade_log", [])
                self.trade_log = []
                for log in raw_logs if isinstance(raw_logs, list) else []:
                    if not isinstance(log, dict):
                        continue
                    ticker = log.get('ticker')
                    action = log.get('action')
                    reason = log.get('reason', '')
                    if ticker not in self.config.TRADELIST:
                        # Skip ignored/skipped logs for deactivated bots.
                        if "ignored" in reason.lower() or "skipped" in reason.lower() or action == 'HOLD':
                            continue
                    if ticker in self.config.TRADELIST or action in ['BUY', 'SELL']:
                        self.trade_log.append(log)
                raw_bot_scans = local_history.get("bot_scans", {})
                if isinstance(raw_bot_scans, dict):
                    self.bot_scans.update(raw_bot_scans)
                print(f"[engine-{self.uid}] Loaded Bots history from local SQLite.")
                
            load_ok = self.alpaca_load_status not in {"firestore_error", "cloud_load_failed"}
            self.legal_settings_loaded = load_ok
            self.loaded_from_cloud = load_ok and connect_broker
            return load_ok
        except Exception as e:
            print(f"[engine-{self.uid}] Load error: {e}")
            if self.alpaca_load_status == "loading":
                self.alpaca_load_status = "cloud_load_failed"
                self.alpaca_load_error = str(e)
            return False

    def _record_scan(self, result: dict):
        if not result: return
        ticker = result.get("ticker", "").upper()
        timeframe = result.get("timeframe", self.config.DEFAULT_TIMEFRAME)
        if not ticker: return
        self.latest_scans[ticker] = result
        tf_bucket = self.latest_scans_by_tf.setdefault(timeframe, {})
        tf_bucket[ticker] = result

    def _record_bot_scan(self, result: dict):
        if not result: return
        ticker = result.get("ticker", "").upper()
        timeframe = result.get("timeframe", self.config.DEFAULT_TIMEFRAME)
        if not ticker: return
        self.bot_scans[ticker] = result
        tf_bucket = self.bot_scans_by_tf.setdefault(timeframe, {})
        tf_bucket[ticker] = result

    def _pick_scan(self, ticker: str, timeframe: str, prefer_bot: bool = True, ignore_freshness: bool = False):
        ticker = ticker.upper()
        
        def _is_fresh(scan):
            if not scan: return False
            if ignore_freshness: return True
            try:
                scan_time = datetime.fromisoformat(scan.get("time", ""))
                now = self.get_now()
                return (now - scan_time).total_seconds() < 55
            except Exception:
                return False

        scan = None
        if prefer_bot:
            scan = self.bot_scans_by_tf.get(timeframe, {}).get(ticker)
            if _is_fresh(scan): return scan
            
        scan = self.latest_scans_by_tf.get(timeframe, {}).get(ticker)
        if _is_fresh(scan): return scan
        
        # Fallback to non-timeframe specific caches just in case, but verify timeframe match
        if prefer_bot:
            scan = self.bot_scans.get(ticker)
            if scan and scan.get("timeframe") == timeframe and _is_fresh(scan):
                return scan
                
        scan = self.latest_scans.get(ticker)
        if scan and scan.get("timeframe") == timeframe and _is_fresh(scan):
            return scan
            
        return None

    def is_market_open(self):
        import pytz
        et_now = datetime.now(pytz.timezone("US/Eastern"))
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
        if et_now < market_open or et_now > market_close or et_now.weekday() >= 5:
            return False
        return True

    def get_options_market_status(self) -> dict:
        import pytz
        from datetime import datetime, timedelta
        et_now = datetime.now(pytz.timezone("US/Eastern"))
        
        # RTH for options: 09:30 to 16:00 ET, Monday to Friday
        # Scan window starts 2 minutes early at 09:28 ET
        is_weekday = et_now.weekday() < 5
        scan_start = et_now.replace(hour=9, minute=28, second=0, microsecond=0)
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
        
        is_active_window = is_weekday and (scan_start <= et_now <= market_close)
        is_open = is_weekday and (market_open <= et_now <= market_close)
        
        next_open = market_open
        next_scan = scan_start
        if not is_weekday or et_now > market_close:
            days_add = 1
            if et_now.weekday() == 4:  # Friday after close -> Monday
                days_add = 3
            elif et_now.weekday() == 5:  # Saturday -> Monday
                days_add = 2
            elif et_now.weekday() == 6 and et_now > market_close:  # Sunday -> Monday
                days_add = 1
            next_open = (et_now + timedelta(days=days_add)).replace(hour=9, minute=30, second=0, microsecond=0)
            next_scan = (et_now + timedelta(days=days_add)).replace(hour=9, minute=28, second=0, microsecond=0)
        elif et_now < scan_start:
            next_open = market_open
            next_scan = scan_start
            
        seconds_to_open = max(0, int((next_open - et_now).total_seconds()))
        seconds_to_pre_open = max(0, int((next_scan - et_now).total_seconds()))
        
        open_day_name = next_open.strftime("%A") if next_open.date() != et_now.date() else "Today"
        next_open_str = f"{open_day_name} at 9:30 AM ET"
        
        return {
            "is_active_window": is_active_window,
            "is_open": is_open,
            "seconds_to_open": seconds_to_open,
            "seconds_to_pre_open": seconds_to_pre_open,
            "next_open_str": next_open_str,
        }

    def get_stock_market_status(self, market_session: str = "Regular") -> dict:
        import pytz
        from datetime import datetime, timedelta
        et_now = datetime.now(pytz.timezone("US/Eastern"))
        
        session = str(market_session or "Regular").strip().lower()
        is_extended = session in {"extended", "24h", "crypto24", "all"}
        
        open_hour, open_minute = (4, 0) if is_extended else (9, 30)
        scan_hour, scan_minute = (3, 58) if is_extended else (9, 28)
        close_hour, close_minute = (20, 0) if is_extended else (16, 0)
        
        is_weekday = et_now.weekday() < 5
        scan_start = et_now.replace(hour=scan_hour, minute=scan_minute, second=0, microsecond=0)
        market_open = et_now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
        market_close = et_now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
        
        is_active_window = is_weekday and (scan_start <= et_now <= market_close)
        is_open = is_weekday and (market_open <= et_now <= market_close)
        
        next_open = market_open
        next_scan = scan_start
        if not is_weekday or et_now > market_close:
            days_add = 1
            if et_now.weekday() == 4:
                days_add = 3
            elif et_now.weekday() == 5:
                days_add = 2
            elif et_now.weekday() == 6 and et_now > market_close:
                days_add = 1
            next_open = (et_now + timedelta(days=days_add)).replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
            next_scan = (et_now + timedelta(days=days_add)).replace(hour=scan_hour, minute=scan_minute, second=0, microsecond=0)
        elif et_now < scan_start:
            if not is_weekday:
                days_add = 2 if et_now.weekday() == 5 else 1
                next_open = (et_now + timedelta(days=days_add)).replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
                next_scan = (et_now + timedelta(days=days_add)).replace(hour=scan_hour, minute=scan_minute, second=0, microsecond=0)
            else:
                next_open = market_open
                next_scan = scan_start
            
        seconds_to_open = max(0, int((next_open - et_now).total_seconds()))
        seconds_to_pre_open = max(0, int((next_scan - et_now).total_seconds()))
        
        open_day_name = next_open.strftime("%A") if next_open.date() != et_now.date() else "Today"
        time_label = "4:00 AM ET" if is_extended else "9:30 AM ET"
        next_open_str = f"{open_day_name} at {time_label}"
        
        return {
            "is_active_window": is_active_window,
            "is_open": is_open,
            "seconds_to_open": seconds_to_open,
            "seconds_to_pre_open": seconds_to_pre_open,
            "next_open_str": next_open_str,
            "market_session_label": "Extended" if is_extended else "Regular",
        }

    def _format_duration_compact(self, seconds: float) -> str:
        remaining = max(0, int(seconds))
        hours, rem = divmod(remaining, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes}m {secs}s"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def _find_position(self, positions, ticker_norm: str):
        ticker_norm = ticker_norm.upper().replace("/", "")
        for p in positions or []:
            if p.get('symbol', '').replace("/", "").upper() == ticker_norm:
                return p
        return None

    def _exit_level_change_threshold(self, price: float) -> float:
        try:
            return max(0.01, abs(float(price)) * 0.0005)
        except Exception:
            return 0.01

    def _allocation_amount(self, t_settings: dict, mode: str = 'regular') -> float:
        def bounded_amount(value) -> float:
            amount = _safe_float(value, 0.0)
            if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000.0:
                return 0.0
            return amount

        if mode == 'options':
            allocation_mode = str(t_settings.get('options_allocation_mode', t_settings.get('allocation_mode', 'fixed')) or 'fixed').lower()
            base_key = 'options_trade_amount'
            compound_key = 'options_compound_amount'
        else:
            allocation_mode = str(t_settings.get('allocation_mode', 'fixed') or 'fixed').lower()
            base_key = 'amount'
            compound_key = 'compound_amount'

        if allocation_mode == 'compound':
            amount = bounded_amount(t_settings.get(compound_key))
            if amount <= 0:
                amount = bounded_amount(t_settings.get(base_key))
            return amount
        return bounded_amount(t_settings.get(base_key))

    def _apply_compound_pl(self, t_settings: dict, pl: float, mode: str = 'regular'):
        if mode == 'options':
            allocation_mode = str(t_settings.get('options_allocation_mode', t_settings.get('allocation_mode', 'fixed')) or 'fixed').lower()
            base_key = 'options_trade_amount'
            compound_key = 'options_compound_amount'
        else:
            allocation_mode = str(t_settings.get('allocation_mode', 'fixed') or 'fixed').lower()
            base_key = 'amount'
            compound_key = 'compound_amount'

        if allocation_mode != 'compound':
            return None
        current = _safe_float(t_settings.get(compound_key), 0.0)
        if current <= 0:
            current = _safe_float(t_settings.get(base_key), 0.0)
        next_amount = max(1.0, round(current + _safe_float(pl, 0.0), 2))
        t_settings[compound_key] = next_amount
        return next_amount

    def _normalize_pct_value(self, value, fallback: float) -> float:
        pct = _safe_float(value, fallback)
        # Accept both UI decimal values (0.10) and defensive whole-percent values (10).
        if pct > 1:
            pct = pct / 100.0
        return max(0.0, pct)

    def _option_pct_exit_settings(self, t_settings: dict):
        stop_loss_pct = self._normalize_pct_value(
            t_settings.get('options_stop_loss_pct', t_settings.get('regular_stop_loss_pct', 0.10)),
            0.10
        )
        take_profit_pct = self._normalize_pct_value(
            t_settings.get('options_take_profit_pct', t_settings.get('regular_take_profit_pct', 0.20)),
            0.20
        )
        trailing_pct = self._normalize_pct_value(
            t_settings.get('options_trailing_pct', t_settings.get('regular_trailing_pct', 0.10)),
            0.10
        )
        return stop_loss_pct, take_profit_pct, trailing_pct

    def _get_option_exit_price(self, option_symbol: str, fallback_price: float) -> dict:
        fallback_price = _safe_float(fallback_price, 0.0)
        quote = {
            'price': fallback_price,
            'source': 'alpaca_position_current_price',
            'bid': None,
            'ask': None,
            'mid': None,
        }
        if self.broker.simulation_mode:
            return quote
        if not getattr(self.config, 'ALPACA_API_KEY', None) or not getattr(self.config, 'ALPACA_SECRET_KEY', None):
            return quote
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.requests import OptionLatestQuoteRequest
            data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
            req = OptionLatestQuoteRequest(symbol_or_symbols=[option_symbol])
            quotes = data_client.get_option_latest_quote(req)
            latest = quotes.get(option_symbol) if hasattr(quotes, 'get') else quotes[option_symbol]
            bid = _safe_float(getattr(latest, 'bid_price', None), 0.0)
            ask = _safe_float(getattr(latest, 'ask_price', None), 0.0)
            mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else 0.0
            # Long option exits are sell orders, so bid is the most honest trigger price.
            exit_price = bid if bid > 0 else (mid if mid > 0 else fallback_price)
            quote.update({
                'price': exit_price,
                'source': 'alpaca_option_latest_quote_bid' if bid > 0 else 'alpaca_option_latest_quote_mid',
                'bid': bid if bid > 0 else None,
                'ask': ask if ask > 0 else None,
                'mid': mid if mid > 0 else None,
            })
        except Exception as exc:
            print(f"[options-exit] Latest quote unavailable for {option_symbol}: {exc}. Using position current_price={fallback_price:.2f}")
        return quote

    def _buy_option_contract(
        self,
        ticker: str,
        option_type: str,
        result: dict,
        t_settings: dict,
        opt_expiry_days,
        opt_strike_type,
        opt_trade_amount: float,
    ) -> tuple[bool, dict]:
        option_type = str(option_type or "").upper()
        if option_type not in {"CALL", "PUT"}:
            result["reason"] = "Skipped: invalid option contract direction."
            return False, result

        from option_selector import get_option_chain, select_contract
        chain = get_option_chain(
            underlying=ticker,
            expiration_limit_days=opt_expiry_days,
            current_price=result['price_raw'],
            client=self.broker.client
        )
        selection_data_client = None
        if not self.broker.simulation_mode and self.config.ALPACA_API_KEY and self.config.ALPACA_SECRET_KEY:
            try:
                from alpaca.data.historical.option import OptionHistoricalDataClient
                selection_data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
            except Exception as data_client_err:
                print(f"[options] Delta selection unavailable: {data_client_err}")
        contract = select_contract(
            contracts=chain,
            current_underlying_price=result['price_raw'],
            option_type=option_type,
            strike_type=opt_strike_type,
            target_expiry_days=opt_expiry_days,
            data_client=selection_data_client
        )
        if not contract:
            result['reason'] = f"Skipped: no suitable {option_type} contract found."
            return False, result

        import math
        quote_bid = quote_ask = quote_spread = quote_spread_pct = None
        if self.broker.simulation_mode:
            from options_pricing import black_scholes
            import datetime
            days = (contract['expiration'] - datetime.date.today()).days
            t_years = max(0.0001, days / 365.0)
            est_premium = black_scholes(
                result['price_raw'],
                contract['strike'],
                t_years,
                r=0.05,
                sigma=0.20,
                option_type=option_type,
            )
            est_premium = max(0.05, est_premium)
        else:
            try:
                from alpaca.data.historical.option import OptionHistoricalDataClient
                from alpaca.data.requests import OptionLatestQuoteRequest
                data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                req = OptionLatestQuoteRequest(symbol_or_symbols=[contract['symbol']])
                quotes = data_client.get_option_latest_quote(req)
                quote = quotes[contract['symbol']]
                quote_bid = _safe_float(getattr(quote, 'bid_price', None), 0.0)
                quote_ask = _safe_float(getattr(quote, 'ask_price', None), 0.0)
                est_premium = quote_ask
                if quote_bid > 0 and quote_ask > 0:
                    quote_spread = quote_ask - quote_bid
                    quote_mid = (quote_bid + quote_ask) / 2.0
                    quote_spread_pct = (quote_spread / quote_mid) * 100.0 if quote_mid > 0 else None
            except Exception as quote_err:
                print(f"[options] Quote fetch failed (no OPRA?): {quote_err}. Falling back to theoretical pricing.")
                from options_pricing import black_scholes
                import datetime
                days = (contract['expiration'] - datetime.date.today()).days
                t_years = max(0.0001, days / 365.0)
                est_premium_bs = black_scholes(
                    result['price_raw'],
                    contract['strike'],
                    t_years,
                    r=0.05,
                    sigma=0.24,
                    option_type=option_type,
                    q=0.013,
                )
                est_premium = max(0.01, est_premium_bs + 0.015)

        spread_limit_pct = _safe_float(t_settings.get('options_max_entry_spread_pct'), 5.0)
        if not self.broker.simulation_mode:
            quote_age_seconds = None
            try:
                quote_timestamp = getattr(quote, 'timestamp', None)
                if quote_timestamp is not None:
                    if quote_timestamp.tzinfo is None:
                        quote_timestamp = pytz.utc.localize(quote_timestamp)
                    quote_age_seconds = max(0.0, (datetime.now(pytz.utc) - quote_timestamp.astimezone(pytz.utc)).total_seconds())
            except Exception:
                quote_age_seconds = None
            liquidity_reason, quote_spread_pct, quote_mid = quote_liquidity_reason(
                bid=quote_bid,
                ask=quote_ask,
                max_spread_pct=spread_limit_pct,
                quote_age_seconds=quote_age_seconds,
                max_quote_age_seconds=_safe_float(t_settings.get('options_max_quote_age_seconds'), 15.0),
                volume=contract.get('volume'),
                min_volume=_safe_float(t_settings.get('options_min_volume'), 1.0),
                open_interest=contract.get('open_interest'),
                min_open_interest=_safe_float(t_settings.get('options_min_open_interest'), 1.0),
            )
            if liquidity_reason:
                result['reason'] = f"Skipped {contract['symbol']}: {liquidity_reason}."
                return False, result
            # Begin at the midpoint and allow only a configurable fraction of
            # the half-spread toward the ask. A later scan may retry from a
            # fresh quote after an unfilled order is cancelled.
            aggression = min(1.0, max(0.0, _safe_float(t_settings.get('options_limit_aggression'), 0.25)))
            est_premium = quote_mid + ((quote_ask - quote_mid) * aggression)
        if est_premium <= 0:
            result['reason'] = "Skipped: option premium estimate could not be retrieved."
            return False, result

        allocated = opt_trade_amount
        qty = math.floor(allocated / (est_premium * 100.0))
        if qty <= 0:
            result['reason'] = f"Skipped: single contract cost (${est_premium * 100:.2f}) exceeds allocation (${allocated:.2f})"
            return False, result

        order_result = self.broker.place_option_order(
            option_symbol=contract['symbol'],
            qty=qty,
            side='buy',
            limit_price=est_premium,
            client_order_id=self._order_intent_id(ticker, "buy", result, contract['symbol']),
        )
        if not order_result.get('success'):
            result['reason'] = f"Option buy failed: {order_result.get('error')}"
            return False, result

        if not self._order_result_is_filled(order_result):
            pending_intent = {
                'client_order_id': order_result.get('client_order_id') or self._order_intent_id(ticker, "buy", result, contract['symbol']),
                'order_id': order_result.get('order_id'),
                'contract': contract['symbol'],
                'qty': qty,
                'side': 'buy',
                'submitted_at': self.get_now().isoformat(),
            }
            t_settings['options_pending_intent'] = pending_intent
            result['order'] = order_result
            result['order_id'] = order_result.get('order_id')
            result['action'] = 'PENDING'
            result['option_intent'] = 'PENDING'
            result['reason'] = 'Option buy submitted; awaiting broker confirmation before strategy state changes.'
            asyncio.create_task(self.save_settings())
            return False, result

        result['order'] = order_result
        result['order_id'] = order_result.get('order_id')
        result['qty'] = qty
        result['total_cost'] = qty * est_premium * 100.0
        result['fees'] = 0.0
        result['option_entry_quote'] = {
            'bid': quote_bid,
            'ask': quote_ask,
            'spread': quote_spread,
            'spread_pct': quote_spread_pct,
            'limit_price': est_premium,
        }
        spread_note = f" | bid/ask ${quote_bid:.2f}/${quote_ask:.2f}, spread {quote_spread_pct:.1f}%" if quote_spread_pct is not None else ""
        selection_note = f" | {contract.get('selection_reason')}" if contract.get('selection_reason') else ""
        result['reason'] = f"BOUGHT {qty} {option_type} contracts ({contract['symbol']}) with limit ${est_premium:.2f}{spread_note}{selection_note}: {result['reason']}"
        t_settings['options_underlying_entry_price'] = float(result.get('price_raw') or 0.0)
        t_settings['options_active_contract'] = contract['symbol']
        t_settings['options_active_cycle_id'] = order_result.get('client_order_id') or order_result.get('order_id')
        try:
            fresh_positions = self.broker.get_positions()
            fresh_option_pos = self._find_position(
                fresh_positions,
                contract['symbol'].replace("/", "").upper(),
            )
            if fresh_option_pos:
                from main import _ensure_exit_forecast_snapshot
                _ensure_exit_forecast_snapshot(
                    t_settings,
                    result,
                    fresh_option_pos,
                    ticker,
                )
        except Exception as snapshot_err:
            print(f"[forecast] {ticker} {option_type} snapshot deferred until position fill is visible: {snapshot_err}")
        return True, result

    def _close_option_with_limit(self, pos_info, qty=None, ticker=None, result=None, t_settings=None):
        """Submit one reconciled option-exit intent at a controlled limit."""
        option_symbol = pos_info['symbol']
        close_qty = max(1, int(float(qty if qty is not None else pos_info.get('qty', 0))))
        quote = self._get_option_exit_price(option_symbol, pos_info.get('current_price'))
        bid = _safe_float(quote.get('bid'), 0.0)
        limit_price = bid if bid > 0 else _safe_float(quote.get('price'), 0.0)
        if limit_price <= 0:
            return {'success': False, 'error': 'No executable option bid available for controlled limit exit.'}

        parsed_symbol = str(option_symbol).replace('/', '').upper()
        position_cycle = (t_settings or {}).get('options_active_cycle_id') if isinstance(t_settings, dict) else None
        position_identity = position_cycle or "|".join([
            parsed_symbol,
            str(_safe_float(pos_info.get('avg_price'), 0.0)),
            str(_safe_float(pos_info.get('qty'), 0.0)),
        ])
        exit_level = str((result or {}).get('continuous_grid_level') or (result or {}).get('level') or 'full')
        raw_intent = f"{self.uid}|{getattr(self.broker, '_risk_account_key', '')}|{position_identity}|{close_qty}|{exit_level}"
        owner_tag = getattr(self.broker, 'order_owner_tag', '')
        client_order_id = f"bb-{owner_tag}-opt-exit-" + hashlib.sha256(raw_intent.encode('utf-8')).hexdigest()[:20]

        reconcile = getattr(self.broker, 'find_order_by_client_id', None)
        if not callable(reconcile) and not getattr(self.broker, 'simulation_mode', True):
            return {
                'success': False,
                'indeterminate': True,
                'error': 'Broker cannot reconcile option-exit intent; no retry was submitted.',
                'client_order_id': client_order_id,
            }
        if callable(reconcile) and not getattr(self.broker, 'simulation_mode', True):
            try:
                prior = reconcile(client_order_id)
            except Exception as exc:
                return {
                    'success': False,
                    'indeterminate': True,
                    'error': 'Broker intent lookup failed; no retry was submitted.',
                    'client_order_id': client_order_id,
                    'detail': type(exc).__name__,
                }
            if prior:
                prior = dict(prior)
                prior['reconciled'] = True
                return prior

        return self.broker.place_option_order(
            option_symbol=option_symbol,
            qty=close_qty,
            side='sell',
            limit_price=limit_price,
            client_order_id=client_order_id,
        )

    def _build_exit_protection_targets(self, ticker: str, result: dict, pos_info: dict, t_settings: dict):
        sell_mode = t_settings.get('sell_mode', 'indicator')
        if sell_mode not in FIXED_LEVEL_EXIT_MODES:
            return None

        ticker_norm = ticker.upper().replace("/", "")
        is_crypto = _is_crypto_symbol(ticker_norm)
        entry_price = _safe_float(pos_info.get('avg_price'), _safe_float(result.get('price_raw'), 0.0))
        current_price = _safe_float(result.get('price_raw'), _safe_float(pos_info.get('current_price'), 0.0))
        qty = abs(_safe_float(pos_info.get('qty'), 0.0))
        precision = 6 if entry_price < 1.0 or is_crypto else 2
        tp = sl = None
        source = sell_mode

        if qty <= 0 or entry_price <= 0:
            return None

        if sell_mode == 'custom_sltp':
            sl = _safe_float(t_settings.get('custom_stop_loss_price'), 0.0)
            tp = _safe_float(t_settings.get('custom_take_profit_price'), 0.0)

        elif sell_mode in ('percentage', 'hybrid_pct'):
            sl_pct = float(t_settings.get('regular_stop_loss_pct', 0.05))
            tp_pct = float(t_settings.get('regular_take_profit_pct', 0.10))
            trail_pct = float(t_settings.get('regular_trailing_pct', 0.03))
            enable_trail = bool(t_settings.get('enable_pct_trail', False))
            base_stop = round(entry_price * (1 - sl_pct), precision)
            if enable_trail:
                if ticker not in self.last_trailing_stops:
                    self.last_trailing_stops[ticker] = base_stop
                if current_price > 0:
                    candidate = round(current_price * (1 - trail_pct), precision)
                    if candidate > self.last_trailing_stops[ticker]:
                        self.last_trailing_stops[ticker] = candidate
                sl = self.last_trailing_stops[ticker]
            else:
                sl = base_stop
            if t_settings.get('exit_grid_continuous_mode', False):
                completed = max(0, int(_safe_float(t_settings.get('exit_grid_level'), 0)))
                max_levels = _grid_max_levels(t_settings, 'exit')
                next_level = min(max_levels, completed + 1)
                next_gain = _grid_level_pct(t_settings, 'exit', next_level)
                grid_anchor = _safe_float(t_settings.get('exit_grid_anchor_price'), entry_price)
                tp = round(grid_anchor * (1 + next_gain / 100.0), precision)
                source = f'{sell_mode}_scale_out_l{next_level}'
            else:
                tp = round(entry_price * (1 + tp_pct), precision)

        elif sell_mode in ('sltp', 'hybrid'):
            atr = _safe_float(result.get('atr'), 0.0)
            if atr <= 0:
                return None
            stop_mult = float(t_settings.get('atr_stop_multiplier', 2.0))
            trail_mult = float(t_settings.get('atr_trail_multiplier', 3.0))
            tp_mult = float(t_settings.get('take_profit_multiplier', 4.0))
            enable_atr_trail = bool(t_settings.get('enable_atr_trail', False))
            base_stop = entry_price - (atr * stop_mult)
            if enable_atr_trail:
                if ticker not in self.last_trailing_stops:
                    self.last_trailing_stops[ticker] = base_stop
                if current_price > 0:
                    candidate = current_price - (atr * trail_mult)
                    if candidate > self.last_trailing_stops[ticker]:
                        self.last_trailing_stops[ticker] = round(candidate, precision)
                sl = round(max(base_stop, self.last_trailing_stops[ticker]), precision)
            else:
                sl = round(base_stop, precision)
            tp = round(entry_price + (atr * tp_mult), precision)

        elif sell_mode in ('pivot_points', 'hybrid_pivot'):
            prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
            if not prior_day:
                return None
            prev_h = prior_day['high']
            prev_l = prior_day['low']
            prev_c = prior_day['close']
            pivot_levels = _dynamic_pivot_levels(prev_h, prev_l, prev_c, current_price)
            if not pivot_levels:
                return None
            tp = round(pivot_levels['tp'], precision)
            sl = round(pivot_levels['sl'], precision)

        elif sell_mode in ('high_low', 'hybrid_high_low'):
            prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
            if not prior_day:
                return None
            hl_buffer = resolve_high_low_buffer_pct(t_settings)
            hl_levels = _dynamic_high_low_levels(prior_day['high'], prior_day['low'], current_price, hl_buffer)
            if not hl_levels:
                return None
            tp = round(hl_levels['tp'], precision)
            sl = round(hl_levels['sl'], precision)

        elif sell_mode in ('swing_points', 'hybrid_swing'):
            swing_exit = build_swing_exit(
                result.get('price_history', []),
                current_price=current_price,
                is_long=True,
                lookback_days=int(t_settings.get('swing_exit_lookback_days', t_settings.get('swing_lookback_days', 20)) or 20),
                market_session=t_settings.get('market_session', 'Regular'),
                is_crypto=is_crypto,
            )
            tp = swing_exit.get('take_profit')
            sl = swing_exit.get('stop_loss')

        if tp is None or sl is None:
            return None

        tp = round(float(tp), precision)
        sl = round(float(sl), precision)
        if tp <= 0 or sl <= 0 or tp <= sl:
            return None

        return {
            'symbol': ticker_norm,
            'qty': qty,
            'take_profit': tp,
            'stop_loss': sl,
            'strategy': source,
            'asset_class': 'crypto' if is_crypto else 'stock',
            'protection_type': 'watcher',
            'current_price': current_price,
        }

    def _sync_exit_protection(self, ticker: str, result: dict, pos_info: dict, t_settings: dict, force: bool = False):
        ticker_norm = ticker.upper().replace("/", "")
        sell_mode = t_settings.get('sell_mode', 'indicator')
        if not pos_info or _safe_float(pos_info.get('qty'), 0.0) <= 0:
            return None

        if (
            (t_settings.get('exit_grid_continuous_mode', False) and sell_mode in {'percentage', 'hybrid_pct'})
            or (t_settings.get('exit_atr_grid_continuous_mode', False) and sell_mode in {'sltp', 'hybrid', 'atr', 'hybrid_atr'})
        ):
            self.broker.cancel_exit_orders(ticker_norm)
            return None

        if sell_mode not in FIXED_LEVEL_EXIT_MODES:
            cancel_res = self.broker.cancel_exit_orders(ticker_norm)
            if cancel_res.get('cancelled', 0) > 0:
                print(f"[exit-protect] {ticker_norm} cancelled stale broker exit order because exit mode is {sell_mode}")
            return None

        targets = self._build_exit_protection_targets(ticker, result, pos_info, t_settings)
        if not targets:
            return None

        if t_settings.get('exit_protection_enabled', True) is False:
            return None

        # Do not cancel and recreate an unchanged broker OCO on every HOLD
        # heartbeat.  Keeping the existing order preserves protection and
        # avoids unnecessary broker writes/rate-limit pressure.
        existing_levels = {}
        get_levels = getattr(self.broker, 'get_exit_order_levels', None)
        if callable(get_levels):
            try:
                existing_levels = get_levels(ticker_norm) or {}
            except Exception as exc:
                print(f"[exit-protect] {ticker_norm} existing OCO lookup failed: {type(exc).__name__}")
        try:
            level_match = (
                bool(existing_levels.get('orders'))
                and abs(float(existing_levels.get('take_profit')) - float(targets['take_profit'])) <= max(0.01, targets['take_profit'] * 0.0005)
                and abs(float(existing_levels.get('stop_loss')) - float(targets['stop_loss'])) <= max(0.01, targets['stop_loss'] * 0.0005)
                and abs(float(existing_levels.get('qty') or 0.0) - float(targets['qty'])) <= max(0.000001, float(targets['qty']) * 0.0005)
            )
        except (TypeError, ValueError):
            level_match = False
        if level_match:
            return {
                'status': 'Active | Broker OCO | TP ${:.2f} | SL ${:.2f}'.format(
                    targets['take_profit'], targets['stop_loss']
                ),
                'active': True,
                'protection_type': 'broker_oco',
                **targets,
                'order_id': (existing_levels.get('orders') or [None])[0],
                'unchanged': True,
            }

        cancel_res = self.broker.cancel_exit_orders(ticker_norm)
        if cancel_res.get('cancelled', 0) > 0:
            print(f"[exit-protect] {ticker_norm} cancelled stale broker exit order; watcher protection is active")

        # Prefer broker-side OCO protection for live Alpaca equity positions.
        # The in-process watcher remains the fallback for crypto, options, and
        # brokers that do not expose a compatible OCO interface.
        if (
            not self.broker.simulation_mode
            and not _is_crypto_symbol(ticker_norm)
            and callable(getattr(self.broker, "place_equity_oco_exit", None))
        ):
            oco_result = self.broker.place_equity_oco_exit(
                ticker_norm,
                _safe_float(pos_info.get('qty'), 0.0),
                targets['take_profit'],
                targets['stop_loss'],
            )
            if oco_result.get('success'):
                return {
                    'status': 'Active | Broker OCO | TP ${:.2f} | SL ${:.2f}'.format(
                        targets['take_profit'], targets['stop_loss']
                    ),
                    'active': True,
                    'protection_type': 'broker_oco',
                    **targets,
                    'order_id': oco_result.get('order_id'),
                }
            print(f"[exit-protect] {ticker_norm} broker OCO unavailable; retaining watcher fallback")

        precision = 6 if _is_crypto_symbol(ticker_norm) or min(targets['take_profit'], targets['stop_loss']) < 1 else 2
        status = f"Active | Watcher | TP ${targets['take_profit']:.{precision}f} | SL ${targets['stop_loss']:.{precision}f}"
        return {'status': status, 'active': True, **targets}

    def _cancel_exit_protection(self, ticker: str, reason: str = ""):
        ticker_norm = ticker.upper().replace("/", "")
        res = self.broker.cancel_exit_orders(ticker_norm)
        if res.get('cancelled', 0) > 0:
            detail = f" ({reason})" if reason else ""
            print(f"[exit-protect] {ticker_norm} cancelled broker exit order{detail}")
        return res

    async def _warm_timeframe_scans(self, timeframe: str, primary_ticker: str = None, limit: int = 4):
        set_user_config(self.config)
        if not self.config.WATCHLIST: return
        ordered = [t.upper() for t in self.config.WATCHLIST]
        if primary_ticker and primary_ticker.upper() in ordered:
            ordered.remove(primary_ticker.upper())
            ordered.insert(0, primary_ticker.upper())
        targets = ordered[:max(1, limit)]
        account = self.broker.get_account_info()
        if account.get('simulation', True): return
        
        for ticker in targets:
            try:
                is_crypto = any(c in ticker for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in ticker
                avail_cash = account.get('non_marginable_buying_power', account['cash']) if is_crypto else account['cash']
                warmed = await asyncio.to_thread(
                    evaluate_trade,
                    ticker,
                    account_equity=account['equity'],
                    available_cash=avail_cash,
                    timeframe=timeframe,
                    use_bot_settings=False
                )
                self._record_scan(warmed)
            except Exception as exc:
                print(f"[warmup-{self.uid}] {ticker} {timeframe} failed: {exc}")


    async def trading_loop(self):
        self.bot_running = True
        print(f"[scheduler-{self.uid}] Trading loop started.")
        from user_config import active_evaluation_context
        active_evaluation_context.set("bots")
        while self.bot_running:
            set_user_config(self.config)
            try:
                account = self.broker.get_account_info()
                if account.get('simulation', True):
                    self.last_scan_time = "Disconnected (Alpaca Link Required)"
                    await asyncio.sleep(10)
                    continue
                equity = account['equity']
                risk_account_key = account.get('_risk_account_key')
                daily_reference_equity = account.get('daily_starting_equity')
                
                from trader import get_risk_manager
                risk_mgr = get_risk_manager()

                was_globally_halted = risk_mgr.trading_halted
                risk_mgr.check_drawdown(
                    equity,
                    account_key=risk_account_key,
                    daily_reference_equity=daily_reference_equity,
                )
                global_risk_status = risk_mgr.get_global_status(
                    equity,
                    account_key=risk_account_key,
                    daily_reference_equity=daily_reference_equity,
                )
                is_globally_halted = bool(global_risk_status.get("halted"))
                newly_globally_halted = is_globally_halted and not was_globally_halted

                # A global halt cancels entry orders only. Existing positions
                # and their protective/exit orders remain managed by the loop.
                halt_event_id = global_risk_status.get("halt_event_id") or (
                    f"legacy:{global_risk_status.get('halted_at') or 'global-halt'}"
                )
                if is_globally_halted and halt_event_id != self.last_global_halt_cancel_event_id:
                    cancellation_complete = False
                    cancel_all_entries = getattr(self.broker, "cancel_all_entry_orders", None)
                    if callable(cancel_all_entries):
                        try:
                            cancel_result = cancel_all_entries()
                            cancellation_complete = bool(cancel_result.get("success", False))
                            if not cancel_result.get("success", False):
                                print(f"[risk-{self.uid}] Some entry orders could not be cancelled: {cancel_result}")
                        except Exception as err:
                            print(f"[risk-{self.uid}] Failed cancelling global entry orders: {err}")
                    else:
                        cancellation_complete = True
                        for active_ticker in list(dict.fromkeys(self.config.TRADELIST)):
                            try:
                                cancel_result = self.broker.cancel_orders_for_ticker(active_ticker, side_filter="buy")
                                cancellation_complete = cancellation_complete and bool(cancel_result.get("success", False))
                            except Exception as err:
                                cancellation_complete = False
                                print(f"[risk-{self.uid}] Failed cancelling entry orders for {active_ticker}: {err}")
                    if cancellation_complete:
                        self.last_global_halt_cancel_event_id = halt_event_id

                if newly_globally_halted:
                    halt_log = {
                        "time": self.get_now().isoformat(),
                        "action": "HALT",
                        "ticker": "ALL BOTS",
                        "price": f"${float(equity):.2f}",
                        "price_raw": float(equity),
                        "reason": f"🛑 GLOBAL RISK HALT: {global_risk_status.get('halt_reason', '')}",
                        "signals": {},
                        "bullish_count": 0,
                        "bearish_count": 0,
                        "total_signals": 0,
                        "log_type": "Global Risk",
                    }
                    self.trade_log.insert(0, halt_log)
                    await self.save_history()

                risk_signature = (
                    risk_account_key,
                    global_risk_status.get("daily_baseline_date"),
                    global_risk_status.get("daily_starting_equity"),
                    global_risk_status.get("equity_high_watermark"),
                    global_risk_status.get("halt_event_id"),
                    is_globally_halted,
                )
                now_ts_for_risk = time.time()
                baseline_changed = (
                    self.last_global_risk_signature is None
                    or risk_signature[:3] != self.last_global_risk_signature[:3]
                )
                if risk_signature != self.last_global_risk_signature and (
                    newly_globally_halted
                    or baseline_changed
                    or (now_ts_for_risk - self.last_global_risk_save_ts) >= 60
                ):
                    await self.save_settings()
                    self.last_global_risk_save_ts = now_ts_for_risk
                    self.last_global_risk_signature = risk_signature

                all_positions = self.broker.get_positions()
                all_orders = self.broker.get_open_orders()
                portfolio_count = len(all_positions)

                # Watchlist rows and dashboard charts have their own data refresh.
                # The 60s trading loop is dedicated strictly to active bots in TRADELIST
                # and prioritized over dashboard data refresh.
                scan_order = list(dict.fromkeys(self.config.TRADELIST))
                force_triggered = self.force_scan_trigger.is_set()
                
                for ticker in scan_order:
                    try:
                        ticker_norm = ticker.upper().replace("/", "")
                        t_settings = self.config.TICKER_SETTINGS.get(ticker, {})
                        is_active = ticker in self.config.TRADELIST

                        # Migrate any persisted three-loss streak immediately,
                        # even before another entry signal is evaluated.
                        if self._pause_for_consecutive_losses(ticker, t_settings):
                            result = {
                                "time": self.get_now().isoformat(),
                                "action": "HALT",
                                "ticker": ticker,
                                "price": "$0.00",
                                "price_raw": 0.0,
                                "reason": f"🛑 BOT AUTO-PAUSED: {t_settings['pause_reason']}",
                                "signals": {},
                                "bullish_count": 0,
                                "bearish_count": 0,
                                "total_signals": 0,
                                "log_type": "Active Bot",
                            }
                            self.trade_log.insert(0, result)
                            await self.save_history()
                            await self.save_settings()
                            continue
                        
                        if t_settings.get('paused', False):
                            continue

                        is_opt_bot = t_settings.get('options_trading', t_settings.get('is_option', False))

                        # A max-trades limit counts successful entry orders. Do
                        # not pause while a position is still open: the bot
                        # must remain able to manage and close that position.
                        max_trades = max(0, int(_safe_float(t_settings.get('max_trades'), 0)))
                        entry_trades = max(0, int(_safe_float(t_settings.get('entry_trades'), 0)))
                        if is_active and max_trades > 0 and entry_trades >= max_trades:
                            if is_opt_bot:
                                from option_selector import parse_occ_symbol
                                has_limit_position = any(
                                    parse_occ_symbol(str(p.get('symbol', '')).upper().replace('/', ''))
                                    and parse_occ_symbol(str(p.get('symbol', '')).upper().replace('/', '')).get('underlying') == ticker_norm
                                    and _safe_float(p.get('qty'), 0) > 0
                                    for p in all_positions
                                )
                                has_limit_order = any(
                                    parse_occ_symbol(str(o.get('symbol', '')).upper().replace('/', ''))
                                    and parse_occ_symbol(str(o.get('symbol', '')).upper().replace('/', '')).get('underlying') == ticker_norm
                                    for o in all_orders
                                )
                            else:
                                has_limit_position = any(
                                    str(p.get('symbol', '')).replace('/', '').upper() == ticker_norm
                                    and _safe_float(p.get('qty'), 0) > 0
                                    for p in all_positions
                                )
                                has_limit_order = any(
                                    str(o.get('symbol', '')).replace('/', '').upper() == ticker_norm
                                    for o in all_orders
                                )

                            if not has_limit_position and not has_limit_order:
                                t_settings['paused'] = True
                                t_settings['pause_reason'] = (
                                    f"Trade limit reached ({entry_trades}/{max_trades} entry trades)."
                                )
                                try:
                                    self.broker.cancel_orders_for_ticker(ticker)
                                except Exception as err:
                                    print(f"[engine-{self.uid}] Error cancelling limit orders for {ticker}: {err}")
                                result = {
                                    "time": self.get_now().isoformat(),
                                    "action": "HALT",
                                    "ticker": ticker,
                                    "price": "$0.00",
                                    "price_raw": 0.0,
                                    "reason": f"🛑 BOT AUTO-PAUSED: {t_settings['pause_reason']}",
                                    "signals": {},
                                    "bullish_count": 0,
                                    "bearish_count": 0,
                                    "total_signals": 0,
                                    "log_type": "Active Bot",
                                }
                                self.trade_log.insert(0, result)
                                await self.save_history()
                                await self.save_settings()
                                continue

                        required_interval = getattr(self.config, 'OPTIONS_SCAN_INTERVAL_SECONDS', 10) if is_opt_bot else getattr(self.config, 'SCAN_INTERVAL_SECONDS', 60)
                        now_ts = time.time()
                        last_ts = self.last_bot_scan_time.get(ticker, 0)
                        if not force_triggered and (now_ts - last_ts) < (required_interval - 1.0):
                            continue
                        self.last_bot_scan_time[ticker] = now_ts

                        if is_opt_bot:
                            opt_market_status = self.get_options_market_status()
                            if not opt_market_status["is_active_window"] and not force_triggered:
                                closed_log = {
                                    "time": self.get_now().isoformat(),
                                    "action": "HOLD",
                                    "ticker": ticker,
                                    "price": "$0.00",
                                    "price_raw": 0.0,
                                    "reason": f"🛑 Options Market Closed. Starts scanning 2m before market open ({opt_market_status['next_open_str']}).",
                                    "summary": f"🛑 Options Market Closed. Starts scanning 2m before open ({opt_market_status['next_open_str']}).",
                                    "detail": f"Starts scanning in {self._format_duration_compact(opt_market_status['seconds_to_pre_open'])} (2m prior to market opening).",
                                    "status": "MARKET_CLOSED",
                                    "is_options_market_closed": True,
                                    "seconds_to_open": opt_market_status["seconds_to_open"],
                                    "seconds_to_pre_open": opt_market_status["seconds_to_pre_open"],
                                    "next_open_str": opt_market_status["next_open_str"],
                                    "refresh_rate_sec": max(10, min(3600, opt_market_status["seconds_to_pre_open"])),
                                    "signals": {},
                                    "bullish_count": 0,
                                    "bearish_count": 0,
                                    "total_signals": 0,
                                    "is_option": True,
                                }
                                self.scan_log_state[ticker] = closed_log
                                self.bot_scans[ticker] = closed_log
                                tf_bucket = self.bot_scans_by_tf.setdefault(t_settings.get('timeframe', self.config.DEFAULT_TIMEFRAME), {})
                                tf_bucket[ticker] = closed_log
                                continue
                        elif not _is_crypto_symbol(ticker_norm):
                            market_session = str(t_settings.get("market_session", "Regular")).strip().lower()
                            stock_market_status = self.get_stock_market_status(market_session)
                            if not stock_market_status["is_active_window"] and not force_triggered:
                                session_label = stock_market_status["market_session_label"]
                                closed_log = {
                                    "time": self.get_now().isoformat(),
                                    "action": "HOLD",
                                    "ticker": ticker,
                                    "price": "$0.00",
                                    "price_raw": 0.0,
                                    "reason": f"🛑 {session_label} Market Closed. Starts scanning 2m before market open ({stock_market_status['next_open_str']}).",
                                    "summary": f"🛑 {session_label} Market Closed. Starts scanning 2m before open ({stock_market_status['next_open_str']}).",
                                    "detail": f"Starts scanning in {self._format_duration_compact(stock_market_status['seconds_to_pre_open'])} (2m prior to market opening).",
                                    "status": "MARKET_CLOSED",
                                    "is_stock_market_closed": True,
                                    "market_session": session_label,
                                    "seconds_to_open": stock_market_status["seconds_to_open"],
                                    "seconds_to_pre_open": stock_market_status["seconds_to_pre_open"],
                                    "next_open_str": stock_market_status["next_open_str"],
                                    "refresh_rate_sec": max(60, min(3600, stock_market_status["seconds_to_pre_open"])),
                                    "signals": {},
                                    "bullish_count": 0,
                                    "bearish_count": 0,
                                    "total_signals": 0,
                                    "is_option": False,
                                }
                                self.scan_log_state[ticker] = closed_log
                                self.bot_scans[ticker] = closed_log
                                tf_bucket = self.bot_scans_by_tf.setdefault(t_settings.get('timeframe', self.config.DEFAULT_TIMEFRAME), {})
                                tf_bucket[ticker] = closed_log
                                continue

                        # --- Bot Loss Limit (Drawdown Limit on Allocated Bot Capital) ---
                        enable_risk = t_settings.get('enable_risk_management', True)
                        allocated_capital = self._allocation_amount(t_settings, 'options' if t_settings.get('options_trading') else 'regular') or float(t_settings.get('amount', 0.0))
                        loss_limit_pct = float(t_settings.get('max_daily_drawdown', 0.0))
                        
                        if is_active and enable_risk and allocated_capital > 0 and loss_limit_pct > 0:
                            self._refresh_daily_guard_state(t_settings)
                            realized_pl = float(t_settings.get('daily_realized_pl', 0.0))
                            
                            # Find if there is an active position for this ticker
                            if is_opt_bot:
                                from option_selector import parse_occ_symbol
                                pos_info = next((
                                    p for p in all_positions
                                    if (parsed := parse_occ_symbol(str(p.get('symbol', '')).replace('/', '').upper()))
                                    and parsed.get('underlying') == ticker_norm
                                ), None)
                            else:
                                pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                            unrealized_pl = float(pos_info['unrealized_pl']) if pos_info else 0.0
                            
                            total_bot_pl = realized_pl + unrealized_pl
                            allowed_loss = allocated_capital * loss_limit_pct
                            
                            if total_bot_pl < 0 and abs(total_bot_pl) >= allowed_loss:
                                print(f"[engine-{self.uid}] Loss limit breached for {ticker}: -${abs(total_bot_pl):.2f} >= limit -${allowed_loss:.2f} ({loss_limit_pct*100:.1f}% of ${allocated_capital:.2f})")
                                
                                # Liquidate active position if any
                                if pos_info:
                                    print(f"[engine-{self.uid}] Liquidating active position in {ticker} due to loss limit breach...")
                                    if is_opt_bot:
                                        close_res = self._close_option_with_limit(
                                            pos_info, ticker=ticker, result={"level": "loss-limit"}, t_settings=t_settings
                                        )
                                    else:
                                        close_res = self.broker.close_position(
                                            ticker,
                                            market_session=t_settings.get('market_session', 'Regular'),
                                            current_price=pos_info.get('current_price') if pos_info else None,
                                            client_order_id=self._order_intent_id(ticker, "sell", {"level": "loss-limit"})
                                        )
                                    if close_res.get('success'):
                                        realized_pl += unrealized_pl
                                        total_bot_pl = realized_pl
                                
                                # Auto-pause the bot permanently
                                t_settings['paused'] = True
                                t_settings['pause_reason'] = f"Loss limit exceeded (-${abs(total_bot_pl):.2f} / -{(abs(total_bot_pl)/allocated_capital)*100:.1f}%)"
                                t_settings['daily_realized_pl'] = round(total_bot_pl, 2)
                                
                                # Cancel any pending orders
                                try:
                                    self.broker.cancel_orders_for_ticker(ticker)
                                except Exception as err:
                                    print(f"[engine-{self.uid}] Error cancelling orders for {ticker}: {err}")
                                
                                # Add log entry for the auto-pause
                                result = {
                                    "time": self.get_now().isoformat(),
                                    "action": "HALT",
                                    "ticker": ticker,
                                    "price": f"${pos_info['current_price']}" if pos_info else "$0.00",
                                    "price_raw": pos_info['current_price'] if pos_info else 0.0,
                                    "reason": f"🛑 BOT AUTO-PAUSED: Loss limit breached. Total Loss: -${abs(total_bot_pl):.2f} ({loss_limit_pct*100:.1f}% limit exceeded).",
                                    "signals": {},
                                    "bullish_count": 0,
                                    "bearish_count": 0,
                                    "total_signals": 0,
                                    "atr": 0.0,
                                    "rsi": None,
                                    "position_sizing": {},
                                    "log_type": "Active Bot"
                                }
                                self.trade_log.insert(0, result)
                                await self.save_history()
                                await self.save_settings()
                                continue

                        is_crypto = any(ticker_norm.endswith(base) for base in ["USD", "USDT", "USDC"]) or ticker.upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
                        sell_mode = t_settings.get('sell_mode', 'indicator')

                        if is_active and sell_mode == 'indicator' and not self.broker.simulation_mode:
                            self.broker.cancel_orders_for_ticker(ticker, side_filter='sell')

                        now = self.get_now()
                        if ticker in self.last_scan_timestamps:
                            if (now - self.last_scan_timestamps[ticker]).total_seconds() < max(1, required_interval - 1):
                                continue
                        self.last_scan_timestamps[ticker] = now

                        avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
                        tf_to_use = t_settings.get('timeframe') or self.config.DEFAULT_TIMEFRAME
                        
                        is_options_enabled = t_settings.get('options_trading', getattr(self.config, 'ENABLE_OPTIONS_TRADING', False)) or t_settings.get('is_option', False)
                        pos_info = None
                        has_this_pos = False
                        if is_options_enabled:
                            from option_selector import parse_occ_symbol
                            for p in all_positions:
                                p_sym = p['symbol'].upper()
                                parsed = parse_occ_symbol(p_sym)
                                if parsed and parsed['underlying'] == ticker_norm:
                                    pos_info = p
                                    has_this_pos = True
                                    break
                        else:
                            has_this_pos = any(p['symbol'].replace("/", "").upper() == ticker_norm and float(p.get('qty', 0)) > 0 for p in all_positions)
                            pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)

                        result = None
                        if is_active:
                            if t_settings.get('ai_autopilot', False):
                                from ai_indicator import get_ai_decision_for_bar
                                
                                ai_pos = None
                                if pos_info:
                                    ai_pos = {
                                        'entry_price': float(pos_info.get('avg_price', 0.0)),
                                        'qty': float(pos_info.get('qty', 0.0)),
                                        'stop_loss': float(pos_info.get('stop_loss', 0.0)) or self.last_trailing_stops.get(ticker, 0.0),
                                        'take_profit': float(pos_info.get('take_profit', 0.0)),
                                    }
                                
                                ai_decision = await asyncio.to_thread(
                                    get_ai_decision_for_bar,
                                    ticker=ticker,
                                    bar_data=None,
                                    account_equity=equity,
                                    available_cash=avail_cash,
                                    position=ai_pos,
                                    timeframe=tf_to_use,
                                    model=t_settings.get('ai_model')
                                )
                                
                                price_raw = ai_decision.get('price') or ai_decision.get('entry_price') or 0.0
                                action = ai_decision.get('signal', 'HOLD')
                                
                                stop_loss = ai_decision.get('stop_loss') or 0.0
                                take_profit = ai_decision.get('sell_target') or 0.0
                                pos_pct = ai_decision.get('position_size_pct', 0.10)
                                if action == 'BUY' and pos_pct <= 0:
                                    pos_pct = 0.10
                                elif action != 'BUY':
                                    pos_pct = 0.0
                                    
                                configured_allocation = self._allocation_amount(t_settings, 'regular')
                                notional_size = configured_allocation if configured_allocation > 0 else equity * pos_pct
                                notional_size = min(notional_size, avail_cash)
                                
                                position_sizing = {
                                    "qty": notional_size / price_raw if price_raw > 0 else 0,
                                    "notional": notional_size,
                                    "stop_loss": stop_loss,
                                    "take_profit": take_profit,
                                    "risk_pct": pos_pct,
                                    "stop_distance": abs(price_raw - stop_loss) if stop_loss > 0 else 0.0,
                                }
                                
                                bot_result = {
                                    "time": self.get_now().isoformat(),
                                    "action": action,
                                    "ticker": ticker,
                                    "price": f"${price_raw:.4f}" if price_raw < 10 else f"${price_raw:.2f}",
                                    "price_raw": price_raw,
                                    "reason": ai_decision.get('reason') or ai_decision.get('summary', 'AI Autopilot Decision'),
                                    "sentiment_score": ai_decision.get('sentiment_score', 0.0),
                                    "sentiment_confidence": ai_decision.get('sentiment_confidence', 0.0),
                                    "sentiment_pct": ai_decision.get('sentiment_pct', 50.0),
                                    "sentiment_direction": ai_decision.get('sentiment_direction', 'NEUTRAL'),
                                    "news_alignment": ai_decision.get('news_alignment', 'ALIGNED'),
                                    "news_alignment_applied": ai_decision.get('news_alignment_applied', False),
                                    "news_alignment_reason": ai_decision.get('news_alignment_reason', ''),
                                    "sentiment_summary": ai_decision.get('sentiment_summary', ai_decision.get('summary', '')),
                                    "sentiment_key_factor": ai_decision.get('sentiment_key_factor', ai_decision.get('key_catalyst', '')),
                                    "signals": {
                                        "AI Autopilot": {
                                            "signal": "BULLISH" if action == 'BUY' else "BEARISH" if action == 'SELL' else "NEUTRAL",
                                            "weight": 5,
                                            "description": ai_decision.get('summary', '')
                                        }
                                    },
                                    "bullish_count": 5 if action == 'BUY' else 0,
                                    "bearish_count": 5 if action == 'SELL' else 0,
                                    "total_signals": 1,
                                    "atr": ai_decision.get('atr', price_raw * 0.02),
                                    "rsi": ai_decision.get('rsi'),
                                    "position_sizing": position_sizing,
                                    "price_history": [],
                                    "risk_status": {},
                                    "is_custom": True,
                                    "timeframe": tf_to_use
                                }
                                self._record_bot_scan(bot_result)
                                result = bot_result
                            else:
                                import io
                                from contextlib import redirect_stdout
                                f = io.StringIO()
                                with redirect_stdout(f):
                                    bot_result = await asyncio.to_thread(
                                        evaluate_trade,
                                        ticker, 
                                        account_equity=equity, 
                                        available_cash=avail_cash,
                                        timeframe=tf_to_use,
                                        use_bot_settings=True,
                                        ignore_cache=is_opt_bot or force_triggered,
                                        has_position=has_this_pos
                                    )
                                if bot_result:
                                    bot_result['_captured_logs'] = f.getvalue()
                                    self._record_bot_scan(bot_result)
                                    result = bot_result
                        if result:
                            if float(result.get('price_raw', 0.0)) <= 0:
                                result['action'] = 'HOLD'
                                result['reason'] = 'Live quote unavailable ($0.00) - Trade evaluation skipped'
                            trade_executed = False

                            if is_active and float(result.get('price_raw', 0.0)) > 0:
                                market_session = str(t_settings.get('market_session', 'Regular')).strip().lower()
                                is_options_enabled = t_settings.get('options_trading', getattr(self.config, 'ENABLE_OPTIONS_TRADING', False)) or t_settings.get('is_option', False)
                                if is_crypto:
                                    is_market_closed_skip = False
                                elif is_options_enabled:
                                    is_market_closed_skip = self.config.get('MARKET_HOURS_ONLY', True) and not self.get_options_market_status()["is_open"]
                                else:
                                    is_market_closed_skip = self.config.get('MARKET_HOURS_ONLY', True) and not self.get_stock_market_status(market_session)["is_open"]
                                
                                if is_market_closed_skip:
                                    result['reason'] = "Market Closed. Trade execution skipped."
                                    if result['action'] in ['BUY', 'SELL']:
                                        result['action'] = 'HOLD'

                                tf_seconds = parse_timeframe_to_seconds(tf_to_use)
                                
                                # Check options trading toggle
                                is_options_enabled = t_settings.get('options_trading', getattr(self.config, 'ENABLE_OPTIONS_TRADING', False))
                                
                                # Resolve options configurations from ticker settings with config fallback
                                opt_stop_loss, opt_take_profit, opt_trailing_pct = self._option_pct_exit_settings(t_settings)
                                # sell_mode is the visible UI setting. Prefer it over legacy/stale options_exit_strategy.
                                opt_exit_strategy = t_settings.get('sell_mode') or t_settings.get('options_exit_strategy') or getattr(self.config, 'OPTIONS_EXIT_STRATEGY', 'hybrid')
                                try:
                                    opt_expiry_days = max(0, min(365, int(t_settings.get('options_expiry_days', getattr(self.config, 'OPTIONS_EXPIRY_DAYS', 7)))))
                                except (TypeError, ValueError):
                                    opt_expiry_days = 7
                                opt_strike_type = t_settings.get('options_strike_type', getattr(self.config, 'OPTIONS_STRIKE_TYPE', 'ATM'))
                                opt_trade_amount = self._allocation_amount(t_settings, 'options') or float(getattr(self.config, 'OPTIONS_TRADE_AMOUNT', 100.0))
                                
                                # pos_info and has_this_pos are pre-computed at scan loop entry
                                has_open_order = False
                                has_open_sell_order = False
                                if is_options_enabled:
                                    from option_selector import parse_occ_symbol
                                    for pending in all_orders:
                                        parsed_pending = parse_occ_symbol(str(pending.get('symbol', '')).upper().replace('/', ''))
                                        pending_side = str(pending.get('side', '')).lower()
                                        if not parsed_pending or parsed_pending.get('underlying') != ticker_norm or pending_side not in {'buy', 'sell'}:
                                            continue
                                        stale_after = max(5, int(_safe_float(
                                            t_settings.get('options_entry_order_timeout_seconds' if pending_side == 'buy' else 'options_exit_order_timeout_seconds'),
                                            20 if pending_side == 'buy' else 10,
                                        )))
                                        try:
                                            created_raw = pending.get('created_at') or pending.get('submitted_at')
                                            created_at = created_raw if isinstance(created_raw, datetime) else datetime.fromisoformat(str(created_raw).replace('Z', '+00:00'))
                                            if created_at.tzinfo is None:
                                                created_at = pytz.utc.localize(created_at)
                                            pending_age = (datetime.now(pytz.utc) - created_at.astimezone(pytz.utc)).total_seconds()
                                        except Exception:
                                            pending_age = 0.0
                                        if pending_age >= stale_after and pending.get('id'):
                                            cancel_result = self.broker.cancel_order_by_id(str(pending['id']))
                                            if cancel_result.get('success'):
                                                print(f"[options] Cancelled unfilled {ticker_norm} {pending_side} after {pending_age:.0f}s; next scan will reprice from a fresh quote.")
                                    has_open_order = any(
                                        parse_occ_symbol(o['symbol']) and 
                                        parse_occ_symbol(o['symbol'])['underlying'] == ticker_norm and 
                                        o['side'].lower() == 'buy' 
                                        for o in all_orders
                                    )
                                    has_open_sell_order = any(
                                        parse_occ_symbol(o['symbol'])
                                        and parse_occ_symbol(o['symbol'])['underlying'] == ticker_norm
                                        and o['side'].lower() == 'sell'
                                        for o in all_orders
                                    )
                                else:
                                    has_open_order = any(o['symbol'] == ticker_norm and o['side'].lower() == 'buy' for o in all_orders)
                                    has_open_sell_order = any(o['symbol'] == ticker_norm and o['side'].lower() == 'sell' for o in all_orders)

                                result["has_position"] = has_this_pos
                                if not has_this_pos:
                                    result["exit_monitoring"] = False

                                # --- CONTINUOUS SCALE-IN / SCALE-OUT GRID ---
                                entry_grid_continuous = bool(
                                    t_settings.get("grid_continuous_mode", False)
                                    or t_settings.get("atr_grid_continuous_mode", False)
                                )
                                exit_grid_continuous = bool(
                                    t_settings.get("exit_grid_continuous_mode", False)
                                    or t_settings.get("exit_atr_grid_continuous_mode", False)
                                )
                                if not is_options_enabled and (entry_grid_continuous or exit_grid_continuous):
                                    current_p = float(result.get('price_raw', 0.0))
                                    current_grid_atr = max(0.0, _safe_float(result.get('atr'), 0.0))
                                    average_entry = _safe_float(pos_info.get('avg_price'), 0.0) if pos_info else 0.0
                                    position_qty = _safe_float(pos_info.get('qty'), 0.0) if pos_info else 0.0
                                    open_order_ids = {
                                        str(order.get('id') or order.get('order_id'))
                                        for order in all_orders
                                        if order.get('id') or order.get('order_id')
                                    }
                                    grid_fill_events = []
                                    if _reconcile_continuous_grid_pending(
                                        t_settings,
                                        current_qty=position_qty,
                                        average_entry_price=average_entry,
                                        current_price=current_p,
                                        open_order_ids=open_order_ids,
                                        fill_events=grid_fill_events,
                                    ):
                                        for fill_event in grid_fill_events:
                                            self.last_trade_timestamps[ticker] = self.get_now()
                                            if fill_event.get('side') == 'entry':
                                                await self._increment_trade_count(ticker, entry=True)
                                                continue
                                            filled_qty = _safe_float(fill_event.get('filled_qty'), 0.0)
                                            fill_price = _safe_float(fill_event.get('fill_price'), current_p)
                                            entry_price = _safe_float(fill_event.get('entry_price'), average_entry)
                                            if filled_qty > 0 and entry_price > 0:
                                                confirmed_pl = (fill_price - entry_price) * filled_qty
                                                self._record_realized_exit(
                                                    ticker,
                                                    t_settings,
                                                    confirmed_pl,
                                                    reason='Continuous grid exit',
                                                    exit_price=fill_price,
                                                    direction='BUY',
                                                    complete=bool(fill_event.get('position_closed')),
                                                )
                                                self._apply_compound_pl(t_settings, confirmed_pl, 'regular')
                                            if fill_event.get('position_closed'):
                                                from main import _clear_forecast_exit_snapshots
                                                _clear_forecast_exit_snapshots(t_settings)
                                                if (
                                                    t_settings.get('enable_signal_reset', True)
                                                    and not t_settings.get('grid_flat_reentry_armed', False)
                                                ):
                                                    self._set_signal_reset_lock(ticker, 'BUY', t_settings)
                                            await self._increment_trade_count(ticker)
                                        await self.save_settings()
                                    grid_decision = _evaluate_continuous_grid(
                                        t_settings,
                                        current_price=current_p,
                                        has_position=has_this_pos,
                                        average_entry_price=average_entry,
                                        position_qty=position_qty,
                                        current_atr=current_grid_atr,
                                        price_history=result.get('price_history') or [],
                                    )
                                    if grid_decision.get("state_changed"):
                                        await self.save_settings()
                                    if (
                                        not grid_decision.get("action")
                                        and not has_this_pos
                                        and not has_open_order
                                        and result.get("action") == "BUY"
                                    ):
                                        initial_grid = _initial_continuous_grid_entry_instruction(
                                            t_settings,
                                            price_history=result.get('price_history') or [],
                                            current_atr=current_grid_atr,
                                        )
                                        if initial_grid:
                                            signal_reason = str(result.get("reason") or "Entry setup confirmed")
                                            initial_grid["reason"] = (
                                                f"Adaptive Grid Initial Buy L{initial_grid['level']}/5: "
                                                f"{initial_grid['allocation_pct']:.1f}% of bot capital "
                                                f"(${initial_grid['level_amount']:.2f}). {signal_reason}"
                                            )
                                            grid_decision = initial_grid
                                    if grid_decision.get("action") == "BUY" and not has_open_order:
                                        result['action'] = 'BUY'
                                        result['reason'] = grid_decision['reason']
                                        configured_grid_notional = max(
                                            0.0,
                                            _safe_float(
                                                grid_decision.get("level_amount"),
                                                _grid_entry_level_amount(
                                                    t_settings,
                                                    grid_decision.get("level", 1),
                                                ),
                                            ),
                                        )
                                        cash_limited_notional = min(
                                            configured_grid_notional,
                                            max(0.0, _safe_float(avail_cash, 0.0) * 0.98),
                                        )
                                        max_position_pct = _safe_float(t_settings.get("max_position_pct"), 0.0)
                                        if t_settings.get("enable_risk_management", True) and max_position_pct > 0:
                                            current_position_value = abs(_safe_float(
                                                pos_info.get("market_value") if pos_info else 0.0,
                                                0.0,
                                            ))
                                            if current_position_value <= 0 and pos_info:
                                                current_position_value = abs(
                                                    _safe_float(pos_info.get("qty"), 0.0) * current_p
                                                )
                                            remaining_position_budget = max(
                                                0.0,
                                                (_safe_float(equity, 0.0) * max_position_pct) - current_position_value,
                                            )
                                            cash_limited_notional = min(
                                                cash_limited_notional,
                                                remaining_position_budget,
                                            )
                                        if configured_grid_notional < 1.0:
                                            result['action'] = 'HOLD'
                                            result['reason'] = (
                                                'Scale-In skipped: Buy % produces less than the $1.00 minimum order.'
                                            )
                                        elif cash_limited_notional + 0.01 >= configured_grid_notional:
                                            result['position_sizing']['notional'] = configured_grid_notional
                                        else:
                                            result['action'] = 'HOLD'
                                            result['reason'] = (
                                                f'Scale-In skipped: ${configured_grid_notional:.2f} is required for '
                                                'the crossed grid levels, but available cash or position-risk '
                                                f'budget permits only ${cash_limited_notional:.2f}.'
                                            )
                                    elif grid_decision.get("action") == "SELL" and not has_open_sell_order:
                                        result['action'] = 'SELL'
                                        result['reason'] = grid_decision['reason']
                                    elif grid_decision.get("reason") and result.get('action') == 'HOLD':
                                        result['reason'] = grid_decision['reason']

                                    if grid_decision.get("action"):
                                        result['continuous_grid_side'] = grid_decision.get("side")
                                        result['continuous_grid_from_level'] = grid_decision.get(
                                            "from_level",
                                            grid_decision.get("level"),
                                        )
                                        result['continuous_grid_level'] = grid_decision.get("level")
                                        result['continuous_grid_max_levels'] = grid_decision.get("max_levels")
                                        result['continuous_grid_anchor_price'] = grid_decision.get("anchor_price")
                                        result['continuous_grid_threshold_price'] = grid_decision.get("threshold_price")
                                        result['continuous_grid_final_level'] = bool(grid_decision.get("final_level"))
                                        result['continuous_grid_runner_exit'] = bool(grid_decision.get("continuous_grid_runner_exit"))
                                enable_cooldown = t_settings.get("enable_cooldown", True)
                                if enable_cooldown and not has_this_pos:
                                    # Use custom cooldown if set, otherwise default to 4 candles
                                    custom_cooldown_min = t_settings.get("cooldown_minutes")
                                    if custom_cooldown_min is not None:
                                        cooldown_secs = custom_cooldown_min * 60
                                    else:
                                        adaptive_flat_buy = bool(
                                            result.get('continuous_grid_side') == 'entry'
                                            and _continuous_grid_pair_enabled(t_settings)
                                            and t_settings.get('grid_flat_reentry_armed')
                                        )
                                        cooldown_secs = (
                                            max(1, int(getattr(self.config, 'SCAN_INTERVAL_SECONDS', 60)))
                                            if adaptive_flat_buy
                                            else tf_seconds * 4
                                        )

                                    cooldown_key = ticker_norm
                                    if cooldown_key in self.last_trade_timestamps:
                                        elapsed = (self.get_now() - self.last_trade_timestamps[cooldown_key]).total_seconds()
                                        if elapsed < cooldown_secs:
                                            # ONLY block entry orders during cooldown to prevent trapping the bot in a bad position
                                            blocked_entry = False
                                            if is_options_enabled and result['action'] in ['BUY', 'SELL']:
                                                blocked_entry = True
                                            elif not is_options_enabled and result['action'] == 'BUY':
                                                blocked_entry = True
                                                
                                            if blocked_entry:
                                                result['action'] = 'HOLD'
                                                rem = int(cooldown_secs - elapsed)
                                                if rem >= 3600:
                                                    result['reason'] = f"Trade Cooldown Active ({rem // 3600}h {(rem % 3600) // 60}m remaining)"
                                                elif rem >= 60:
                                                    result['reason'] = f"Trade Cooldown Active ({rem // 60}m {rem % 60}s remaining)"
                                                else:
                                                    result['reason'] = f"Trade Cooldown Active ({rem}s remaining)"

                                if not has_this_pos:
                                    self.last_trailing_stops.pop(ticker, None)

                                    # Get Smart Re-entry Settings
                                    enable_signal_reset = t_settings.get('enable_signal_reset', True)
                                    enable_smart_reentry = t_settings.get('enable_smart_reentry', getattr(self.config, 'ENABLE_SMART_REENTRY', False))
                                    smart_reentry_type = str(t_settings.get('smart_reentry_type', getattr(self.config, 'SMART_REENTRY_TYPE', 'RSI')) or 'RSI').upper()
                                    smart_reentry_rsi_call_max = _safe_float(t_settings.get('smart_reentry_rsi_call_max'), _safe_float(getattr(self.config, 'SMART_REENTRY_RSI_CALL_MAX', 70), 70.0))
                                    smart_reentry_rsi_put_min = _safe_float(t_settings.get('smart_reentry_rsi_put_min'), _safe_float(getattr(self.config, 'SMART_REENTRY_RSI_PUT_MIN', 30), 30.0))
                                    smart_reentry_vwap_call_max_dist_pct = _safe_float(t_settings.get('smart_reentry_vwap_call_max_dist_pct'), _safe_float(getattr(self.config, 'SMART_REENTRY_VWAP_CALL_MAX_DIST_PCT', 0.5), 0.5))
                                    smart_reentry_vwap_put_max_dist_pct = _safe_float(t_settings.get('smart_reentry_vwap_put_max_dist_pct'), _safe_float(getattr(self.config, 'SMART_REENTRY_VWAP_PUT_MAX_DIST_PCT', 0.5), 0.5))

                                    # Apply Signal Reset Check/Clear
                                    adaptive_grid_reentry = bool(
                                        result.get('continuous_grid_side') == 'entry'
                                        and _continuous_grid_pair_enabled(t_settings)
                                        and t_settings.get('grid_flat_reentry_armed')
                                    )
                                    if enable_signal_reset and not adaptive_grid_reentry:
                                        action = result.get('action', 'HOLD')
                                        locked_dir = self._get_signal_reset_lock(ticker, t_settings)
                                        if locked_dir:
                                            if action != locked_dir and self._signal_reset_is_reset(result, locked_dir, t_settings):
                                                if self._clear_signal_reset_lock(ticker, t_settings):
                                                    await self.save_settings()
                                            elif action == locked_dir:
                                                result['action'] = 'HOLD'
                                                reset_count = self._signal_reset_count(result, locked_dir)
                                                result['reason'] = f"Signal Reset Active: Blocked stale {locked_dir} re-entry until all same-direction primary signals reset (now {reset_count:g})."
                                            else:
                                                result['reason'] = f"Signal Reset Active: waiting for all stale {locked_dir} primary signals to reset."
                                    else:
                                        if self._clear_signal_reset_lock(ticker, t_settings):
                                            await self.save_settings()

                                    # Apply Smart Re-entry Pullback Filters
                                    if enable_smart_reentry:
                                        action = result.get('action', 'HOLD')
                                        if action in ['BUY', 'SELL']:
                                            if smart_reentry_type == 'RSI':
                                                rsi = _safe_float(result.get('_internal_rsi', result.get('rsi')), None)
                                                if rsi is not None:
                                                    if action == 'BUY' and rsi > smart_reentry_rsi_call_max:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry RSI Filter: Blocked BUY since RSI ({rsi:.1f}) > {smart_reentry_rsi_call_max} (Overbought)."
                                                    elif action == 'SELL' and rsi < smart_reentry_rsi_put_min:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry RSI Filter: Blocked SELL since RSI ({rsi:.1f}) < {smart_reentry_rsi_put_min} (Oversold)."
                                            elif smart_reentry_type == 'VWAP':
                                                vwap = _safe_float(result.get('vwap'), 0.0)
                                                price = _safe_float(result.get('price_raw'), 0.0)
                                                if vwap is not None and vwap > 0 and price > 0:
                                                    dist_pct = abs(price - vwap) / vwap * 100.0
                                                    if action == 'BUY' and dist_pct > smart_reentry_vwap_call_max_dist_pct:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry VWAP Filter: Blocked BUY since price is too far from VWAP ({dist_pct:.2f}% > {smart_reentry_vwap_call_max_dist_pct}%)."
                                                    elif action == 'SELL' and dist_pct > smart_reentry_vwap_put_max_dist_pct:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry VWAP Filter: Blocked SELL since price is too far from VWAP ({dist_pct:.2f}% > {smart_reentry_vwap_put_max_dist_pct}%)."

                                    # Cooldown, signal reset, stabilization, and loss limits are
                                    # cumulative AND gates. Passing one never bypasses another.
                                    entry_action = result.get('action')
                                    if entry_action in {'BUY', 'SELL'}:
                                        allocated_guard_capital = self._allocation_amount(
                                            t_settings,
                                            'options' if is_options_enabled else 'regular',
                                        ) or float(t_settings.get('amount', 0.0) or 0.0)
                                        guard_reason = self._entry_loss_circuit_reason(t_settings, allocated_guard_capital)
                                        if not guard_reason:
                                            guard_reason = self._post_stop_block_reason(ticker, t_settings, result, tf_to_use)
                                        if not guard_reason and is_options_enabled:
                                            guard_reason = opening_blackout_reason(
                                                self.get_now(),
                                                max(0, int(_safe_float(t_settings.get('options_market_open_blackout_minutes'), 15))),
                                                enabled=bool(t_settings.get('enable_options_market_open_filter', True)),
                                            )
                                        if not guard_reason and not is_options_enabled:
                                            guard_reason, quote_guard = self._live_liquidity_block_reason(ticker, t_settings, is_crypto)
                                            if quote_guard:
                                                result['entry_quote_guard'] = quote_guard
                                        if guard_reason:
                                            result['action'] = 'HOLD'
                                            result['reason'] = guard_reason
                                            if guard_reason.startswith(('Loss circuit breaker', 'Daily strategy-loss limit')):
                                                t_settings['paused'] = True
                                                t_settings['pause_reason'] = guard_reason
                                                await self.save_settings()
                                
                                # Sell Mode Logic
                                if has_this_pos and pos_info:
                                    # Ensure indicator exits trigger when holding open positions
                                    if sell_mode in ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_atr', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing']:
                                        min_sell_req = int(t_settings.get('min_sell_signals', t_settings.get('sell_threshold', t_settings.get('threshold', getattr(self.config, 'MIN_BEARISH_SIGNALS', 1)))) or 1)
                                        bearish_cnt = result.get('bearish_count', 0)
                                        bullish_cnt = result.get('bullish_count', 0)
                                        if bearish_cnt >= min_sell_req and bearish_cnt >= bullish_cnt and result.get('action') != 'SELL':
                                            result['action'] = 'SELL'
                                            result['reason'] = f"SELL Triggered (Indicator Exit): {bearish_cnt} bearish signals confirmed"
                                    if is_options_enabled:
                                        # Options Exits
                                        from option_selector import parse_occ_symbol
                                        parsed_occ = parse_occ_symbol(pos_info['symbol'])
                                        opt_type = parsed_occ['type'] if parsed_occ else 'CALL'

                                        exit_triggered = False
                                        exit_reason = ""
                                        swing_exit_lookback_days = int(t_settings.get('swing_exit_lookback_days', t_settings.get('swing_lookback_days', 20)) or 20)
                                        swing_session = t_settings.get('market_session', 'Regular')
                                        is_crypto_underlying = any(str(ticker_norm).endswith(base) for base in ["USD", "USDT", "USDC"]) or ticker.upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
                                        current_price = result['price_raw']
                                        
                                        # 1. Premium-based check
                                        quote_info = self._get_option_exit_price(pos_info['symbol'], pos_info.get('current_price'))
                                        opt_curr = float(quote_info['price'])
                                        result['option_exit_check'] = {
                                            'premium': opt_curr,
                                            'source': quote_info.get('source'),
                                            'bid': quote_info.get('bid'),
                                            'ask': quote_info.get('ask'),
                                            'mid': quote_info.get('mid'),
                                            'strategy': opt_exit_strategy,
                                        }
                                        opt_avg = float(pos_info['avg_price'])
                                        stop_loss_pct = opt_stop_loss
                                        take_profit_pct = opt_take_profit
                                        option_trail_pct = opt_trailing_pct
                                        
                                        # Strategy A: Premium percent triggers
                                        if opt_exit_strategy in ['premium_pct', 'percentage', 'hybrid_pct']:
                                            base_stop = opt_avg * (1 - stop_loss_pct)
                                            stop_loss_price = base_stop
                                            trail_key = pos_info.get('symbol', ticker)
                                            if bool(t_settings.get('enable_pct_trail', False)) and option_trail_pct > 0:
                                                if trail_key not in self.last_trailing_stops:
                                                    self.last_trailing_stops[trail_key] = base_stop
                                                candidate = opt_curr * (1 - option_trail_pct)
                                                if candidate > self.last_trailing_stops[trail_key]:
                                                    self.last_trailing_stops[trail_key] = candidate
                                                stop_loss_price = self.last_trailing_stops[trail_key]
                                            if opt_curr <= stop_loss_price:
                                                exit_triggered = True
                                                exit_reason = (
                                                    f"Option Premium SL Hit (${opt_curr:.2f} <= ${stop_loss_price:.2f}; "
                                                    f"entry ${opt_avg:.2f}, SL {stop_loss_pct*100:.1f}%, trail {option_trail_pct*100:.1f}%, "
                                                    f"price_source={quote_info['source']})"
                                                )
                                            elif opt_curr >= opt_avg * (1 + take_profit_pct):
                                                exit_triggered = True
                                                exit_reason = (
                                                    f"Option Premium TP Hit (${opt_curr:.2f} >= ${opt_avg*(1+take_profit_pct):.2f}; "
                                                    f"entry ${opt_avg:.2f}, TP {take_profit_pct*100:.1f}%, "
                                                    f"price_source={quote_info['source']})"
                                                )

                                        # Strategy A1: Underlying ATR trigger, anchored to the underlying entry when known.
                                        if not exit_triggered and opt_exit_strategy in ['sltp', 'hybrid']:
                                            atr_val = _safe_float(result.get('atr'), 0.0)
                                            entry_underlying = _safe_float(t_settings.get('options_underlying_entry_price'), current_price)
                                            stop_mult = _safe_float(t_settings.get('atr_stop_multiplier'), 2.0)
                                            trail_mult = _safe_float(t_settings.get('atr_trail_multiplier'), 3.0)
                                            tp_mult = _safe_float(t_settings.get('take_profit_multiplier'), 4.0)
                                            if atr_val > 0 and entry_underlying > 0:
                                                trail_key = pos_info.get('symbol', ticker)
                                                if opt_type == 'CALL':
                                                    atr_sl = entry_underlying - (atr_val * stop_mult)
                                                    atr_tp = entry_underlying + (atr_val * tp_mult)
                                                    effective_sl = atr_sl
                                                    if bool(t_settings.get('enable_atr_trail', False)) and trail_mult > 0:
                                                        candidate = current_price - (atr_val * trail_mult)
                                                        previous = self.last_trailing_stops.get(trail_key, atr_sl)
                                                        effective_sl = max(atr_sl, previous, candidate)
                                                        self.last_trailing_stops[trail_key] = effective_sl
                                                    if current_price <= effective_sl:
                                                        exit_triggered = True
                                                        exit_reason = f"Option ATR SL Hit (Underlying {current_price:.2f} <= {effective_sl:.2f})"
                                                    elif current_price >= atr_tp:
                                                        exit_triggered = True
                                                        exit_reason = f"Option ATR TP Hit (Underlying {current_price:.2f} >= {atr_tp:.2f})"
                                                else:
                                                    atr_sl = entry_underlying + (atr_val * stop_mult)
                                                    atr_tp = entry_underlying - (atr_val * tp_mult)
                                                    effective_sl = atr_sl
                                                    if bool(t_settings.get('enable_atr_trail', False)) and trail_mult > 0:
                                                        candidate = current_price + (atr_val * trail_mult)
                                                        previous = self.last_trailing_stops.get(trail_key, atr_sl)
                                                        effective_sl = min(atr_sl, previous, candidate)
                                                        self.last_trailing_stops[trail_key] = effective_sl
                                                    if current_price >= effective_sl:
                                                        exit_triggered = True
                                                        exit_reason = f"Option ATR SL Hit (Underlying {current_price:.2f} >= {effective_sl:.2f})"
                                                    elif current_price <= atr_tp:
                                                        exit_triggered = True
                                                        exit_reason = f"Option ATR TP Hit (Underlying {current_price:.2f} <= {atr_tp:.2f})"

                                        # Strategy A2: Custom premium SL/TP triggers
                                        if not exit_triggered and opt_exit_strategy == 'custom_sltp':
                                            custom_sl = _safe_float(t_settings.get('custom_stop_loss_price'), 0.0)
                                            custom_tp = _safe_float(t_settings.get('custom_take_profit_price'), 0.0)
                                            if custom_sl > 0 and opt_curr <= custom_sl:
                                                exit_triggered = True
                                                exit_reason = f"Option Custom SL Hit (${opt_curr:.2f} <= ${custom_sl:.2f})"
                                            elif custom_tp > 0 and opt_curr >= custom_tp:
                                                exit_triggered = True
                                                exit_reason = f"Option Custom TP Hit (${opt_curr:.2f} >= ${custom_tp:.2f})"

                                        # Strategy A3: Custom underlying-price SL/TP triggers
                                        if not exit_triggered and opt_exit_strategy == 'custom_underlying_sltp':
                                            custom_u_sl = _safe_float(t_settings.get('custom_underlying_stop_loss_price'), 0.0)
                                            custom_u_tp = _safe_float(t_settings.get('custom_underlying_take_profit_price'), 0.0)
                                            if opt_type == 'CALL':
                                                if custom_u_sl > 0 and current_price <= custom_u_sl:
                                                    exit_triggered = True
                                                    exit_reason = f"Option Custom Underlying SL Hit (Underlying ${current_price:.2f} <= ${custom_u_sl:.2f})"
                                                elif custom_u_tp > 0 and current_price >= custom_u_tp:
                                                    exit_triggered = True
                                                    exit_reason = f"Option Custom Underlying TP Hit (Underlying ${current_price:.2f} >= ${custom_u_tp:.2f})"
                                            else:
                                                if custom_u_sl > 0 and current_price >= custom_u_sl:
                                                    exit_triggered = True
                                                    exit_reason = f"Option Custom Underlying SL Hit (Underlying ${current_price:.2f} >= ${custom_u_sl:.2f})"
                                                elif custom_u_tp > 0 and current_price <= custom_u_tp:
                                                    exit_triggered = True
                                                    exit_reason = f"Option Custom Underlying TP Hit (Underlying ${current_price:.2f} <= ${custom_u_tp:.2f})"
                                                
                                        # Reuse the already-fetched OHLCV candles for pivot & range exits.
                                        if not exit_triggered and opt_exit_strategy in ['pivot_points', 'hybrid_pivot', 'high_low', 'hybrid_high_low']:
                                            prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
                                            if prior_day:
                                                prev_h = prior_day['high']
                                                prev_l = prior_day['low']
                                                prev_c = prior_day['close']
                                            else:
                                                prev_h = prev_l = prev_c = result['price_raw']

                                            # Strategy B: Pivot points
                                            if opt_exit_strategy in ['pivot_points', 'hybrid_pivot']:
                                                P = (prev_h + prev_l + prev_c) / 3.0
                                                R1 = 2 * P - prev_l
                                                S1 = 2 * P - prev_h

                                                if opt_type == 'CALL':
                                                    if current_price >= R1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot TP Hit (Underlying {current_price:.2f} >= R1 {R1:.2f})"
                                                    elif current_price <= S1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot SL Hit (Underlying {current_price:.2f} <= S1 {S1:.2f})"
                                                else:  # PUT
                                                    if current_price <= S1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot TP Hit (Underlying {current_price:.2f} <= S1 {S1:.2f})"
                                                    elif current_price >= R1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot SL Hit (Underlying {current_price:.2f} >= R1 {R1:.2f})"
                                                        
                                            # Strategy C: Prior Day High/Low ranges
                                            if not exit_triggered and opt_exit_strategy in ['high_low', 'hybrid_high_low']:
                                                hl_levels = buffered_prior_day_levels(
                                                    {"high": prev_h, "low": prev_l, "close": prev_c},
                                                    resolve_high_low_buffer_pct(t_settings),
                                                )
                                                if opt_type == 'CALL' and hl_levels:
                                                    if current_price >= hl_levels['high_trigger']:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day High Breakout (Underlying {current_price:.2f} >= Trigger {hl_levels['high_trigger']:.2f}, PDH {hl_levels['high']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                                                    elif current_price <= hl_levels['low_trigger']:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day Low Breakdown (Underlying {current_price:.2f} <= Trigger {hl_levels['low_trigger']:.2f}, PDL {hl_levels['low']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                                                elif opt_type == 'PUT' and hl_levels:
                                                    if current_price <= hl_levels['low_trigger']:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day Low Breakout (Underlying {current_price:.2f} <= Trigger {hl_levels['low_trigger']:.2f}, PDL {hl_levels['low']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                                                    elif current_price >= hl_levels['high_trigger']:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day High Breakdown (Underlying {current_price:.2f} >= Trigger {hl_levels['high_trigger']:.2f}, PDH {hl_levels['high']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"

                                        # Strategy C2: Swing points exits
                                        if not exit_triggered and opt_exit_strategy in ['swing_points', 'hybrid_swing']:
                                            swing_exit = build_swing_exit(
                                                result.get('price_history', []),
                                                current_price=result.get('price_raw', current_price),
                                                is_long=(opt_type == 'CALL'),
                                                lookback_days=swing_exit_lookback_days,
                                                market_session=swing_session,
                                                is_crypto=is_crypto_underlying,
                                            )
                                            tp = swing_exit.get('take_profit')
                                            sl = swing_exit.get('stop_loss')
                                            if tp is not None and sl is not None:
                                                if opt_type == 'CALL':
                                                    if current_price >= tp:
                                                        exit_triggered = True
                                                        exit_reason = f"Swing TP Hit (Underlying {current_price:.2f} >= Resistance {tp:.2f})"
                                                    elif current_price <= sl:
                                                        exit_triggered = True
                                                        exit_reason = f"Swing SL Hit (Underlying {current_price:.2f} <= Support {sl:.2f})"
                                                else:
                                                    if current_price <= tp:
                                                        exit_triggered = True
                                                        exit_reason = f"Swing TP Hit (Underlying {current_price:.2f} <= Support {tp:.2f})"
                                                    elif current_price >= sl:
                                                        exit_triggered = True
                                                        exit_reason = f"Swing SL Hit (Underlying {current_price:.2f} >= Resistance {sl:.2f})"
                                                        
                                        # Strategy D: Expiration Safeguard
                                        if not exit_triggered and parsed_occ:
                                            import datetime
                                            if parsed_occ['expiration'] <= datetime.date.today():
                                                # If it is expiration day, force exit 2 hours before market close
                                                now_dt = datetime.datetime.now()
                                                if now_dt.hour >= 13: # 13:00 Central (2 hours before 15:00 close)
                                                    exit_triggered = True
                                                    exit_reason = f"Expiration Safeguard (2 hours before close on expiration day)"
                                                    
                                        # Technical Indicator exit
                                        if not exit_triggered and opt_exit_strategy in ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing']:
                                            exit_signal_threshold = int(_safe_float(
                                                t_settings.get('min_sell_signals', t_settings.get('sell_threshold', 1)),
                                                1
                                            ))
                                            bullish_count = int(_safe_float(result.get('bullish_count'), 0))
                                            bearish_count = int(_safe_float(result.get('bearish_count'), 0))
                                            call_exit_signal = bearish_count >= exit_signal_threshold
                                            put_exit_signal = bullish_count >= exit_signal_threshold
                                            if opt_type == 'CALL' and call_exit_signal:
                                                exit_triggered = True
                                                exit_reason = f"Technical Signal reversal to BEARISH ({bearish_count}/{exit_signal_threshold})"
                                            elif opt_type == 'PUT' and put_exit_signal:
                                                exit_triggered = True
                                                exit_reason = f"Technical Signal reversal to BULLISH ({bullish_count}/{exit_signal_threshold})"
                                                
                                        if exit_triggered:
                                            result['action'] = 'SELL'
                                            result['option_intent'] = 'CLOSE_OPTION'
                                            result['reason'] = exit_reason
                                        else:
                                            result['option_intent'] = 'HOLD'
                                            if not result.get('exit_monitoring'):
                                                if opt_exit_strategy in ['premium_pct', 'percentage', 'hybrid_pct']:
                                                    base_stop = opt_avg * (1 - stop_loss_pct)
                                                    stop_loss_price = base_stop
                                                    trail_key = pos_info.get('symbol', ticker)
                                                    if bool(t_settings.get('enable_pct_trail', False)) and option_trail_pct > 0:
                                                        stop_loss_price = self.last_trailing_stops.get(trail_key, base_stop)
                                                    tp_price = opt_avg * (1 + take_profit_pct)
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "Option PCT Exit",
                                                        f"Option position open. Checking Premium PCT exits: Premium ${opt_curr:.2f}, SL ${stop_loss_price:.2f} ({stop_loss_pct*100:.1f}%), TP ${tp_price:.2f} ({take_profit_pct*100:.1f}%).",
                                                        {"price": opt_curr, "stop_loss": stop_loss_price, "take_profit": tp_price},
                                                    )
                                                elif opt_exit_strategy in ['sltp', 'hybrid']:
                                                    atr_val = _safe_float(result.get('atr'), 0.0)
                                                    entry_underlying = _safe_float(t_settings.get('options_underlying_entry_price'), current_price)
                                                    stop_mult = _safe_float(t_settings.get('atr_stop_multiplier'), 2.0)
                                                    tp_mult = _safe_float(t_settings.get('take_profit_multiplier'), 4.0)
                                                    atr_sl = entry_underlying - (atr_val * stop_mult) if opt_type == 'CALL' else entry_underlying + (atr_val * stop_mult)
                                                    atr_tp = entry_underlying + (atr_val * tp_mult) if opt_type == 'CALL' else entry_underlying - (atr_val * tp_mult)
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "Option ATR Exit",
                                                        f"Option position open. Checking ATR exits: Underlying ${current_price:.2f}, SL ${atr_sl:.2f}, TP ${atr_tp:.2f}.",
                                                        {"price": current_price, "stop_loss": atr_sl, "take_profit": atr_tp},
                                                    )
                                                elif opt_exit_strategy == 'custom_sltp':
                                                    custom_sl = _safe_float(t_settings.get('custom_stop_loss_price'), 0.0)
                                                    custom_tp = _safe_float(t_settings.get('custom_take_profit_price'), 0.0)
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "Option Custom SL/TP",
                                                        f"Option position open. Checking Custom exits: Premium ${opt_curr:.2f}, SL ${custom_sl:.2f}, TP ${custom_tp:.2f}.",
                                                        {"price": opt_curr, "stop_loss": custom_sl, "take_profit": custom_tp},
                                                    )
                                                elif opt_exit_strategy == 'custom_underlying_sltp':
                                                    custom_u_sl = _safe_float(t_settings.get('custom_underlying_stop_loss_price'), 0.0)
                                                    custom_u_tp = _safe_float(t_settings.get('custom_underlying_take_profit_price'), 0.0)
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "Option Custom Underlying SL/TP",
                                                        f"Option position open. Checking Custom Underlying exits: Price ${current_price:.2f}, SL ${custom_u_sl:.2f}, TP ${custom_u_tp:.2f}.",
                                                        {"price": current_price, "stop_loss": custom_u_sl, "take_profit": custom_u_tp},
                                                    )
                                                else:
                                                    _mark_exit_signal_watch(
                                                        result,
                                                        t_settings,
                                                        is_long=(opt_type == 'CALL'),
                                                        prefix="Option position open",
                                                    )

                                    else:
                                        # Standard Stock/Crypto Sell Mode Logic
                                        entry_price = float(pos_info.get('avg_price', result['price_raw']))
                                        current_price = float(result.get('price_raw', 0.0))
                                        if current_price <= 0:
                                            result['action'] = 'HOLD'
                                            result['reason'] = 'Live quote unavailable ($0.00)'
                                            break
                                        
                                        # Read exit strategy parameters
                                        sl_pct    = t_settings.get('regular_stop_loss_pct', 0.05)
                                        tp_pct    = t_settings.get('regular_take_profit_pct', 0.10)
                                        trail_pct = t_settings.get('regular_trailing_pct', 0.03)
                                        precision = 6 if entry_price < 1.0 else 2  # Crypto precision fix
                                        
                                        if t_settings.get('ai_autopilot', False):
                                            stop_loss = result['position_sizing'].get('stop_loss') or self.last_trailing_stops.get(ticker)
                                            take_profit = result['position_sizing'].get('take_profit')

                                            if sell_mode == 'sltp' and result['action'] == 'SELL':
                                                result['action'] = 'HOLD'
                                                result['reason'] = 'AI Dynamic Sell Signal Ignored (Fixed SL/TP Mode)'
                                            
                                            if sell_mode in ['sltp', 'hybrid', 'ai_autopilot']:
                                                if stop_loss:
                                                    self.last_trailing_stops[ticker] = stop_loss
                                                    if current_price <= stop_loss:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'AI Stop Loss Hit (${current_price:.2f} <= ${stop_loss:.2f})'
                                                if take_profit:
                                                    if current_price >= take_profit:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'AI Take Profit Hit (${current_price:.2f} >= ${take_profit:.2f})'
                                                if result.get('action') != 'SELL' and not result.get('exit_monitoring'):
                                                    watch_lbl = "AI SL/TP" if t_settings.get('ai_autopilot', False) else "SL/TP Exit"
                                                    _mark_exit_level_watch(
                                                        result,
                                                        watch_lbl,
                                                        f"Position open. Checking {watch_lbl} exits: Price ${current_price:.2f}, SL ${stop_loss if stop_loss else 0:.2f}, TP ${take_profit if take_profit else 0:.2f}.",
                                                        {"price": current_price, "stop_loss": stop_loss, "take_profit": take_profit},
                                                    )
                                        else:
                                            if sell_mode == 'custom_sltp':
                                                custom_sl = _safe_float(t_settings.get('custom_stop_loss_price'), 0.0)
                                                custom_tp = _safe_float(t_settings.get('custom_take_profit_price'), 0.0)
                                                if result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Custom SL/TP Mode)'
                                                if custom_sl > 0 and current_price <= custom_sl:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Custom Stop Loss Hit (${current_price:.{precision}f} <= ${custom_sl:.{precision}f})'
                                                elif custom_tp > 0 and current_price >= custom_tp:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Custom Take Profit Hit (${current_price:.{precision}f} >= ${custom_tp:.{precision}f})'
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "Custom SL/TP",
                                                        f"Position open. Checking custom exits: Price ${current_price:.{precision}f}, SL ${custom_sl:.{precision}f}, TP ${custom_tp:.{precision}f}.",
                                                        {"price": current_price, "stop_loss": custom_sl, "take_profit": custom_tp},
                                                    )

                                            elif sell_mode in ['percentage', 'hybrid_pct']:
                                                grid_exit_active = bool(
                                                    t_settings.get("exit_grid_continuous_mode", False)
                                                    and sell_mode in ['percentage', 'hybrid_pct']
                                                )
                                                if grid_exit_active:
                                                    # The adaptive ladder replaces fixed percentage SL, TP,
                                                    # and trailing-stop controls. Global risk limits and hybrid
                                                    # indicator exits remain independent safeguards.
                                                    self.last_trailing_stops.pop(ticker, None)
                                                    if (
                                                        sell_mode == 'percentage'
                                                        and result['action'] == 'SELL'
                                                        and result.get('continuous_grid_side') != 'exit'
                                                    ):
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = 'Sell signal ignored: adaptive percentage ladder is active.'
                                                    if result.get('continuous_grid_side') != 'exit' and result.get('action') != 'SELL':
                                                        completed = max(0, int(_safe_float(t_settings.get('exit_grid_level'), 0)))
                                                        next_level = _next_grid_level(t_settings, 'exit', completed)
                                                        if next_level is None:
                                                            _mark_exit_level_watch(
                                                                result,
                                                                'PCT Scale-Out',
                                                                'Position open. All enabled adaptive sell levels are complete; waiting for a buy-back cycle or another configured exit.',
                                                                {'price': current_price, 'next_grid_level': None},
                                                            )
                                                        else:
                                                            next_gain = _grid_level_pct(t_settings, 'exit', next_level)
                                                            anchor = _safe_float(t_settings.get('exit_grid_anchor_price'), entry_price)
                                                            next_target = anchor * (1 + next_gain / 100.0)
                                                            _mark_exit_level_watch(
                                                                result,
                                                                'PCT Scale-Out',
                                                                f'Position open. Enabled scale-out L{next_level}/5 waits at ${next_target:.{precision}f} (+{next_gain:.2f}%).',
                                                                {
                                                                    'price': current_price,
                                                                    'next_grid_level': next_level,
                                                                    'next_grid_target': next_target,
                                                                    'grid_anchor': anchor,
                                                                },
                                                            )
                                                else:
                                                    base_stop = round(entry_price * (1 - sl_pct), precision)
                                                    take_profit = round(entry_price * (1 + tp_pct), precision)
                                                    enable_trail = bool(t_settings.get('enable_pct_trail', False))
                                                    if enable_trail:
                                                        if ticker not in self.last_trailing_stops:
                                                            self.last_trailing_stops[ticker] = base_stop
                                                        candidate = round(current_price * (1 - trail_pct), precision)
                                                        if candidate > self.last_trailing_stops[ticker]:
                                                            self.last_trailing_stops[ticker] = candidate
                                                        stop_loss = self.last_trailing_stops[ticker]
                                                        sl_desc = f"Trail {trail_pct*100:.1f}%"
                                                    else:
                                                        stop_loss = base_stop
                                                        sl_desc = f"Fixed SL {sl_pct*100:.1f}%"

                                                    if current_price <= stop_loss:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Percentage Stop Loss Hit (${current_price:.{precision}f} <= ${stop_loss:.{precision}f}, {sl_desc})'
                                                        result['continuous_grid_side'] = 'risk'
                                                        result['continuous_grid_final_level'] = True
                                                    elif current_price >= take_profit:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Percentage Take Profit Hit (${current_price:.{precision}f} >= ${take_profit:.{precision}f}, TP {tp_pct*100:.1f}%)'
                                                    elif result.get('action') != 'SELL':
                                                        _mark_exit_level_watch(
                                                            result,
                                                            "PCT Exit",
                                                            f"Position open. Checking PCT exits: Price ${current_price:.{precision}f}, SL ${stop_loss:.{precision}f} ({sl_desc}), TP ${take_profit:.{precision}f} ({tp_pct*100:.1f}%).",
                                                            {
                                                                "price": current_price,
                                                                "stop_loss": stop_loss,
                                                                "take_profit": take_profit,
                                                                "trail_enabled": enable_trail,
                                                                "trail_pct": trail_pct if enable_trail else None,
                                                            },
                                                        )
                                        
                                            elif sell_mode in ['pivot_points', 'hybrid_pivot']:
                                                # Pivot point exit from the scan's OHLCV history.
                                                prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
                                                if prior_day:
                                                    prev_h = prior_day['high']
                                                    prev_l = prior_day['low']
                                                    prev_c = prior_day['close']
                                                else:
                                                    prev_h = prev_l = prev_c = current_price
                                                
                                                P  = (prev_h + prev_l + prev_c) / 3.0
                                                R1 = round(2 * P - prev_l, precision)
                                                R2 = round(P + (prev_h - prev_l), precision)
                                                S1 = round(2 * P - prev_h, precision)
                                                S2 = round(P - (prev_h - prev_l), precision)
                                                dynamic_pivots = _dynamic_pivot_levels(prev_h, prev_l, prev_c, current_price)
                                                pivot_tp = round(dynamic_pivots['tp'], precision) if dynamic_pivots else R1
                                                pivot_sl = round(dynamic_pivots['sl'], precision) if dynamic_pivots else S1
                                                
                                                # In pivot mode: silence indicator SELL signals, rely on pivot levels
                                                if sell_mode == 'pivot_points' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Pivot Points Mode)'
                                                
                                                if current_price <= S2:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Stop Loss Hit (${current_price:.{precision}f} <= S2 ${S2:.{precision}f})'
                                                elif current_price <= pivot_sl:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Stop Loss Hit (${current_price:.{precision}f} <= ${pivot_sl:.{precision}f})'
                                                elif current_price >= pivot_tp:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Take Profit Hit (${current_price:.{precision}f} >= ${pivot_tp:.{precision}f})'
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "Pivot Exit",
                                                        f"Position open. Checking Pivot exits: Price ${current_price:.{precision}f}, SL ${pivot_sl:.{precision}f}, TP ${pivot_tp:.{precision}f}.",
                                                        {"price": current_price, "stop_loss": pivot_sl, "take_profit": pivot_tp, "pivot": P},
                                                    )

                                            elif sell_mode in ['high_low', 'hybrid_high_low']:
                                                prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
                                                if prior_day:
                                                    prev_h = round(prior_day['high'], precision)
                                                    prev_l = round(prior_day['low'], precision)
                                                else:
                                                    prev_h = prev_l = current_price

                                                if sell_mode == 'high_low' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (High/Low Mode)'

                                                hl_buffer = resolve_high_low_buffer_pct(t_settings)
                                                dynamic_hl = _dynamic_high_low_levels(prev_h, prev_l, current_price, hl_buffer)
                                                hl_tp = round(dynamic_hl['tp'], precision) if dynamic_hl else prev_h
                                                hl_sl = round(dynamic_hl['sl'], precision) if dynamic_hl else prev_l

                                                if current_price >= hl_tp:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'High/Low Take Profit Hit (${current_price:.{precision}f} >= ${hl_tp:.{precision}f}, Buffer {hl_buffer:.2f}%)'
                                                elif current_price <= hl_sl:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'High/Low Stop Loss Hit (${current_price:.{precision}f} <= ${hl_sl:.{precision}f}, Buffer {hl_buffer:.2f}%)'
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "PDH/PDL Exit",
                                                        f"Position open. Checking PDH/PDL exits: Price ${current_price:.{precision}f}, SL ${hl_sl:.{precision}f}, TP ${hl_tp:.{precision}f}, Buffer {hl_buffer:.2f}%.",
                                                        {"price": current_price, "stop_loss": hl_sl, "take_profit": hl_tp, "buffer_pct": hl_buffer},
                                                    )

                                            elif sell_mode in ['swing_points', 'hybrid_swing']:
                                                swing_exit = build_swing_exit(
                                                    result.get('price_history', []),
                                                    current_price=current_price,
                                                    is_long=True,
                                                    lookback_days=int(t_settings.get('swing_exit_lookback_days', t_settings.get('swing_lookback_days', 20)) or 20),
                                                    market_session=t_settings.get('market_session', 'Regular'),
                                                    is_crypto=is_crypto,
                                                )
                                                tp = swing_exit.get('take_profit')
                                                sl = swing_exit.get('stop_loss')
                                                if sell_mode == 'swing_points' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Swing Points Mode)'

                                                if tp is not None and sl is not None:
                                                    if current_price >= tp:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Swing Take Profit Hit (${current_price:.{precision}f} >= ${tp:.{precision}f})'
                                                    elif current_price <= sl:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Swing Stop Loss Hit (${current_price:.{precision}f} <= ${sl:.{precision}f})'
                                                    elif result.get('action') != 'SELL':
                                                        _mark_exit_level_watch(
                                                            result,
                                                            "Swing Exit",
                                                            f"Position open. Checking Swing exits: Price ${current_price:.{precision}f}, SL ${sl:.{precision}f}, TP ${tp:.{precision}f}.",
                                                            {"price": current_price, "stop_loss": sl, "take_profit": tp},
                                                        )
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_signal_watch(result, t_settings, is_long=True, prefix="Position open. Swing exit levels unavailable")

                                            else:
                                                # ── EXISTING: ATR-based logic (unchanged) ───────────────────
                                                stop_mult = t_settings.get('atr_stop_multiplier', 2.0)
                                                trail_mult = t_settings.get('atr_trail_multiplier', 3.0)
                                                tp_mult = t_settings.get('take_profit_multiplier', 4.0)
                                                atr_value = _safe_float(result.get('atr'), 0.0)
                                                
                                                base_stop_loss = entry_price - (atr_value * stop_mult)
                                                take_profit = entry_price + (atr_value * tp_mult)
                                                
                                                enable_atr_trail = bool(t_settings.get('enable_atr_trail', False))
                                                if enable_atr_trail:
                                                    if ticker not in self.last_trailing_stops:
                                                        self.last_trailing_stops[ticker] = base_stop_loss
                                                    trail_distance = atr_value * trail_mult
                                                    candidate_stop = current_price - trail_distance
                                                    if candidate_stop > self.last_trailing_stops[ticker]:
                                                        self.last_trailing_stops[ticker] = round(candidate_stop, precision)
                                                    stop_loss = max(base_stop_loss, self.last_trailing_stops[ticker])
                                                else:
                                                    stop_loss = base_stop_loss
                                                
                                                if sell_mode == 'sltp' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Fixed SL/TP Mode)'

                                                if sell_mode in ['sltp', 'hybrid', 'atr', 'hybrid_atr'] and not t_settings.get('exit_atr_grid_continuous_mode', False):
                                                    if current_price <= stop_loss:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Stop Loss Hit (${current_price:.{precision}f} <= ${stop_loss:.{precision}f})'
                                                    elif current_price >= take_profit:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Take Profit Hit (${current_price:.{precision}f} >= ${take_profit:.{precision}f})'
                                                    elif result.get('action') != 'SELL':
                                                        trail_desc = f", Trail {trail_mult:.2f}x ATR" if enable_atr_trail else ""
                                                        watch_lbl = "ATR Exit" if sell_mode == 'atr' else ("ATR SL/TP Exit" if sell_mode == 'hybrid_atr' else "SL/TP Exit")
                                                        _mark_exit_level_watch(
                                                            result,
                                                            watch_lbl,
                                                            f"Position open. Checking {watch_lbl}s: Price ${current_price:.{precision}f}, SL ${stop_loss:.{precision}f}, TP ${take_profit:.{precision}f}{', ATR $' + f'{atr_value:.{precision}f}' + trail_desc if sell_mode in ['atr', 'hybrid_atr'] else ''}.",
                                                            {
                                                                "price": current_price,
                                                                "stop_loss": stop_loss,
                                                                "take_profit": take_profit,
                                                                "atr": atr_value,
                                                                "trail_enabled": enable_atr_trail,
                                                                "trail_multiplier": trail_mult if enable_atr_trail else None,
                                                            },
                                                        )

                                        if result.get('action') != 'SELL' and not result.get('exit_monitoring'):
                                            _mark_exit_signal_watch(result, t_settings, is_long=True, prefix="Position open")

                                if has_this_pos and pos_info and not is_options_enabled and not is_market_closed_skip:
                                    if result['action'] == 'SELL':
                                        self._cancel_exit_protection(ticker, result.get('reason', 'sell signal'))
                                        # A full/risk exit outranks an in-flight scale order.
                                        # Cancel both sides first so an old buy cannot refill a
                                        # position after liquidation and an old partial sell
                                        # cannot block the emergency close as a duplicate.
                                        if result.get('continuous_grid_side') != 'exit' and hasattr(self.broker, 'cancel_orders_for_ticker'):
                                            cancel_res = self.broker.cancel_orders_for_ticker(ticker)
                                            if cancel_res.get('success'):
                                                has_open_order = False
                                                has_open_sell_order = False
                                    elif result['action'] == 'HOLD':
                                        protection = self._sync_exit_protection(ticker, result, pos_info, t_settings)
                                        if protection:
                                            result['exit_protection'] = protection

                                # Once the entry limit is reached, block all
                                # flat-account entries, but allow exits for an
                                # already-open position.
                                max_trades = max(0, int(_safe_float(t_settings.get('max_trades'), 0)))
                                entry_trades = max(0, int(_safe_float(t_settings.get('entry_trades'), 0)))
                                if (
                                    max_trades > 0
                                    and entry_trades >= max_trades
                                    and result.get('action') in {'BUY', 'SELL'}
                                    and (
                                        not has_this_pos
                                        or result.get('continuous_grid_side') == 'entry'
                                    )
                                ):
                                    result['action'] = 'HOLD'
                                    result['reason'] = (
                                        f"Trade limit reached ({entry_trades}/{max_trades} entry trades). "
                                        "Bot will pause after any open position is closed."
                                    )

                                # Global risk is an account-wide entry gate. A
                                # BUY always increases exposure; a flat SELL
                                # can open a PUT. Existing-position SELL exits
                                # remain allowed so a halt never traps risk.
                                global_entry_attempt = _is_global_risk_entry_attempt(result, has_this_pos)
                                if risk_mgr.trading_halted and global_entry_attempt:
                                    result['action'] = 'HOLD'
                                    result['option_intent'] = 'HOLD'
                                    result['reason'] = f"Global risk halt active: {risk_mgr.halt_reason}"

                                options_execution_handled = False
                                pending_option_intent = t_settings.get('options_pending_intent') if is_options_enabled else None
                                if is_options_enabled and isinstance(pending_option_intent, dict):
                                    pending_client_id = str(pending_option_intent.get('client_order_id') or '').strip()
                                    if has_this_pos:
                                        # The broker position is authoritative. Promote the
                                        # pending entry only after it is visible there.
                                        t_settings.pop('options_pending_intent', None)
                                        t_settings['options_active_cycle_id'] = pending_client_id or t_settings.get('options_active_cycle_id')
                                    else:
                                        reconcile = getattr(self.broker, 'find_order_by_client_id', None)
                                        pending_order = None
                                        pending_lookup_error = None
                                        if callable(reconcile) and pending_client_id:
                                            try:
                                                pending_order = reconcile(pending_client_id)
                                            except Exception as exc:
                                                pending_lookup_error = type(exc).__name__
                                        status = str((pending_order or {}).get('status') or '').lower()
                                        terminal_failure = status in {'canceled', 'cancelled', 'rejected', 'expired', 'order_status_canceled'}
                                        if terminal_failure:
                                            t_settings.pop('options_pending_intent', None)
                                        else:
                                            options_execution_handled = True
                                            result['action'] = 'PENDING'
                                            result['option_intent'] = 'PENDING'
                                            result['reason'] = (
                                                'Option entry intent remains pending; broker reconciliation is required before retrying.'
                                                if not pending_lookup_error else
                                                'Option entry intent lookup failed; no duplicate order was submitted.'
                                            )
                                if is_options_enabled and not is_market_closed_skip:
                                    option_side = normalize_options_type(t_settings.get("options_type", "calls_only"), default="calls_only")
                                    option_intent = result.get("option_intent", "HOLD")
                                    if (
                                        max_trades > 0
                                        and entry_trades >= max_trades
                                        and not has_this_pos
                                        and option_intent in {"OPEN_CALL", "OPEN_PUT"}
                                    ):
                                        option_intent = "HOLD"
                                        result['action'] = 'HOLD'
                                        result['option_intent'] = option_intent
                                        result['reason'] = (
                                            f"Trade limit reached ({entry_trades}/{max_trades} entry trades). "
                                            "Bot will pause after any open position is closed."
                                        )
                                    if has_this_pos and result.get('action') == 'SELL':
                                        option_intent = "CLOSE_OPTION"
                                        result["option_intent"] = option_intent
                                    elif not has_this_pos and result.get('action') not in ['BUY', 'SELL'] and option_intent in {"OPEN_CALL", "OPEN_PUT"}:
                                        option_intent = "HOLD"
                                        result["option_intent"] = option_intent
                                    if option_side not in VALID_OPTION_DIRECTIONS and not has_this_pos:
                                        result['action'] = 'HOLD'
                                        result['option_intent'] = 'DISABLED'
                                        result['reason'] = "HOLD: Both direction is disabled. Choose CALL or PUT."
                                        options_execution_handled = True

                                    if not options_execution_handled and option_intent in {"OPEN_CALL", "OPEN_PUT"}:
                                        options_execution_handled = True
                                        expected_contract = "CALL" if option_intent == "OPEN_CALL" else "PUT"
                                        configured_contract = options_type_to_contract_type(option_side)
                                        enable_risk = t_settings.get('enable_risk_management', True)
                                        max_pos = getattr(self.config, 'MAX_OPEN_POSITIONS', 5) if enable_risk else 999999
                                        if configured_contract != expected_contract:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"HOLD: {expected_contract} signal ignored because bot direction is {configured_contract or 'disabled'}."
                                        elif portfolio_count >= max_pos and not has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                        elif has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = "Options position already open."
                                        elif has_open_order:
                                            result['action'] = 'PENDING'
                                            result['option_intent'] = 'PENDING'
                                            result['reason'] = "Options buy order pending."
                                        else:
                                            success, result = self._buy_option_contract(
                                                ticker=ticker,
                                                option_type=expected_contract,
                                                result=result,
                                                t_settings=t_settings,
                                                opt_expiry_days=opt_expiry_days,
                                                opt_strike_type=opt_strike_type,
                                                opt_trade_amount=opt_trade_amount,
                                            )
                                            if success:
                                                trade_executed = True
                                                if not has_this_pos:
                                                    portfolio_count += 1
                                                result['action'] = 'BUY'
                                                result['option_intent'] = option_intent
                                                result['reason'] = f"✅ {result['reason']}"
                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                await self._increment_trade_count(ticker, entry=True)
                                                await self.save_settings()

                                    elif option_intent == "CLOSE_OPTION" and has_this_pos and pos_info:
                                        options_execution_handled = True

                                        if has_open_sell_order:
                                            result['action'] = 'PENDING'
                                            result['option_intent'] = 'PENDING_EXIT'
                                            result['reason'] = 'Options exit order pending; duplicate close suppressed.'
                                            continue

                                        # Continuous grids are currently defined for the
                                        # underlying stock/crypto position only. Never turn an
                                        # unrelated options close signal into an accidental
                                        # partial close merely because a stale flag exists.
                                        if result.get("continuous_grid_side") == "exit":
                                            grid_sell_pct = float(t_settings.get("exit_grid_sell_pct", 25.0)) / 100.0
                                            total_qty = int(float(pos_info['qty']))
                                            sell_qty = max(1, int(total_qty * grid_sell_pct))

                                            if grid_sell_pct >= 0.99 or (total_qty - sell_qty) == 0:
                                                order_result = self._close_option_with_limit(
                                                    pos_info, ticker=ticker, result=result, t_settings=t_settings
                                                )
                                            else:
                                                order_result = self._close_option_with_limit(
                                                    pos_info, sell_qty, ticker=ticker, result=result, t_settings=t_settings
                                                )
                                        else:
                                            order_result = self._close_option_with_limit(
                                                pos_info, ticker=ticker, result=result, t_settings=t_settings
                                            )
                                        if self._order_result_is_filled(order_result):
                                            result['order'] = order_result
                                            result['order_id'] = order_result.get('order_id')
                                            trade_executed = True
                                            portfolio_count -= 1

                                            qty = float(pos_info['qty'])
                                            exit_check_price = _safe_float(result.get('option_exit_check', {}).get('premium'), pos_info.get('current_price'))
                                            val = qty * exit_check_price * 100.0
                                            cost = qty * float(pos_info['avg_price']) * 100.0
                                            pl = val - cost
                                            pl_pct = (pl / cost * 100.0) if cost > 0 else 0.0

                                            result['action'] = 'SELL'
                                            result['option_intent'] = 'CLOSE_OPTION'
                                            result['qty'] = qty
                                            result['total_cost'] = val
                                            result['fees'] = 0.0
                                            result['pl'] = round(pl, 2)
                                            result['pl_pct'] = round(pl_pct, 2)
                                            result['reason'] = f"✅ CLOSED option position ({pos_info['symbol']}) at checked premium ${exit_check_price:.2f} (est. P/L: ${pl:+.2f}): {result['reason']}"
                                            t_settings.pop('options_underlying_entry_price', None)
                                            t_settings.pop('options_active_contract', None)
                                            t_settings.pop('options_active_cycle_id', None)
                                            from main import _clear_forecast_exit_snapshots
                                            _clear_forecast_exit_snapshots(t_settings)
                                            self.last_trailing_stops.pop(pos_info['symbol'], None)
                                            self.last_trade_timestamps[ticker] = self.get_now()
                                            await self._increment_trade_count(ticker)

                                            from option_selector import parse_occ_symbol
                                            parsed_closed = parse_occ_symbol(pos_info['symbol'])
                                            closed_type = parsed_closed['type'] if parsed_closed else 'CALL'
                                            locked_dir = 'BUY' if closed_type == 'CALL' else 'SELL'
                                            self._record_realized_exit(
                                                ticker, t_settings, pl, result['reason'],
                                                result.get('price_raw'), locked_dir, complete=True,
                                            )
                                            self._apply_compound_pl(t_settings, pl, 'options')
                                            await self.save_settings()

                                            enable_signal_reset = t_settings.get('enable_signal_reset', True)
                                            if enable_signal_reset:
                                                self._set_signal_reset_lock(ticker, locked_dir, t_settings)
                                                await self.save_settings()
                                                print(f"[engine-{self.uid}] Signal Reset Activated: Locked {locked_dir} re-entry for {ticker}")
                                        else:
                                            result['reason'] = f"Option sell failed: {order_result.get('error')}"

                                # Buy Execution
                                if not options_execution_handled and result['action'] == 'BUY' and not is_market_closed_skip:
                                    if is_options_enabled:
                                        enable_risk = t_settings.get('enable_risk_management', True)
                                        max_pos = getattr(self.config, 'MAX_OPEN_POSITIONS', 5) if enable_risk else 999999
                                        if portfolio_count >= max_pos and not has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                        elif has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = "Options position already open."
                                        elif has_open_order:
                                            result['action'] = 'PENDING'
                                            result['reason'] = "Options buy order pending."
                                        elif t_settings.get("options_type", "calls_only") == "puts_only":
                                            result['reason'] = "Strategy is configured for Puts Only."
                                        else:
                                            # Option contract selection
                                            from option_selector import get_option_chain, select_contract
                                            chain = get_option_chain(
                                                underlying=ticker,
                                                expiration_limit_days=opt_expiry_days,
                                                current_price=result['price_raw'],
                                                client=self.broker.client
                                            )
                                            selection_data_client = None
                                            if not self.broker.simulation_mode and self.config.ALPACA_API_KEY and self.config.ALPACA_SECRET_KEY:
                                                try:
                                                    from alpaca.data.historical.option import OptionHistoricalDataClient
                                                    selection_data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                                                except Exception as data_client_err:
                                                    print(f"[options] Delta selection unavailable: {data_client_err}")
                                            contract = select_contract(
                                                contracts=chain,
                                                current_underlying_price=result['price_raw'],
                                                option_type="CALL",
                                                strike_type=opt_strike_type,
                                                target_expiry_days=opt_expiry_days,
                                                data_client=selection_data_client
                                            )
                                            
                                            if contract:
                                                # Fetch estimate premium for sizing
                                                import math
                                                quote_bid = quote_ask = quote_spread = quote_spread_pct = None
                                                if self.broker.simulation_mode:
                                                    from options_pricing import black_scholes
                                                    import datetime
                                                    days = (contract['expiration'] - datetime.date.today()).days
                                                    t_years = max(0.0001, days / 365.0)
                                                    est_premium = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.20, option_type="CALL")
                                                    est_premium = max(0.05, est_premium)
                                                else:
                                                    try:
                                                        from alpaca.data.historical.option import OptionHistoricalDataClient
                                                        from alpaca.data.requests import OptionLatestQuoteRequest
                                                        data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                                                        req = OptionLatestQuoteRequest(symbol_or_symbols=[contract['symbol']])
                                                        quotes = data_client.get_option_latest_quote(req)
                                                        quote = quotes[contract['symbol']]
                                                        quote_bid = _safe_float(getattr(quote, 'bid_price', None), 0.0)
                                                        quote_ask = _safe_float(getattr(quote, 'ask_price', None), 0.0)
                                                        est_premium = quote_ask
                                                        if quote_bid > 0 and quote_ask > 0:
                                                            quote_spread = quote_ask - quote_bid
                                                            quote_mid = (quote_bid + quote_ask) / 2.0
                                                            quote_spread_pct = (quote_spread / quote_mid) * 100.0 if quote_mid > 0 else None
                                                    except Exception as quote_err:
                                                        print(f"[options] Quote fetch failed (no OPRA?): {quote_err}. Falling back to theoretical pricing.")
                                                        from options_pricing import black_scholes
                                                        import datetime
                                                        days = (contract['expiration'] - datetime.date.today()).days
                                                        t_years = max(0.0001, days / 365.0)
                                                        est_premium_bs = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.24, option_type="CALL", q=0.013)
                                                        est_premium = max(0.01, est_premium_bs + 0.015)

                                                spread_limit_pct = _safe_float(
                                                    t_settings.get('options_max_entry_spread_pct'),
                                                    5.0
                                                )
                                                if not self.broker.simulation_mode and (_safe_float(quote_bid, 0.0) <= 0 or _safe_float(quote_ask, 0.0) <= 0):
                                                    result['reason'] = f"Skipped: live option bid/ask unavailable for {contract['symbol']}."
                                                elif not self.broker.simulation_mode and quote_spread_pct is not None and quote_spread_pct > spread_limit_pct:
                                                    result['reason'] = f"Skipped: option spread too wide for {contract['symbol']} ({quote_spread_pct:.1f}% > {spread_limit_pct:.1f}%)."
                                                elif est_premium > 0:
                                                    if not self.broker.simulation_mode and quote_bid > 0 and quote_ask > 0:
                                                        quote_mid = (quote_bid + quote_ask) / 2.0
                                                        aggression = min(1.0, max(0.0, _safe_float(t_settings.get('options_limit_aggression'), 0.25)))
                                                        est_premium = quote_mid + ((quote_ask - quote_mid) * aggression)
                                                    allocated = opt_trade_amount
                                                    qty = math.floor(allocated / (est_premium * 100.0))
                                                    if qty > 0:
                                                        order_result = self.broker.place_option_order(
                                                            option_symbol=contract['symbol'],
                                                            qty=qty,
                                                            side='buy',
                                                            limit_price=est_premium,
                                                            client_order_id=self._order_intent_id(ticker, "buy", result, contract['symbol']),
                                                        )
                                                        if self._order_result_is_filled(order_result):
                                                            result['order'] = order_result
                                                            result['order_id'] = order_result.get('order_id')
                                                            trade_executed = True
                                                            portfolio_count += 1

                                                            result['qty'] = qty
                                                            result['total_cost'] = qty * est_premium * 100.0
                                                            result['fees'] = 0.0
                                                            result['option_entry_quote'] = {
                                                                'bid': quote_bid,
                                                                'ask': quote_ask,
                                                                'spread': quote_spread,
                                                                'spread_pct': quote_spread_pct,
                                                                'limit_price': est_premium,
                                                            }
                                                            spread_note = f" | bid/ask ${quote_bid:.2f}/${quote_ask:.2f}, spread {quote_spread_pct:.1f}%" if quote_spread_pct is not None else ""
                                                            selection_note = f" | {contract.get('selection_reason')}" if contract.get('selection_reason') else ""
                                                            result['reason'] = f"✅ BOUGHT {qty} CALL contracts ({contract['symbol']}) with limit ${est_premium:.2f}{spread_note}{selection_note}: {result['reason']}"
                                                            t_settings['options_underlying_entry_price'] = float(result.get('price_raw') or 0.0)
                                                            t_settings['options_active_contract'] = contract['symbol']
                                                            t_settings['options_active_cycle_id'] = order_result.get('client_order_id') or order_result.get('order_id')
                                                            try:
                                                                fresh_positions = self.broker.get_positions()
                                                                fresh_option_pos = self._find_position(
                                                                    fresh_positions,
                                                                    contract['symbol'].replace("/", "").upper(),
                                                                )
                                                                if fresh_option_pos:
                                                                    from main import _ensure_exit_forecast_snapshot
                                                                    _ensure_exit_forecast_snapshot(
                                                                        t_settings,
                                                                        result,
                                                                        fresh_option_pos,
                                                                        ticker,
                                                                    )
                                                            except Exception as snapshot_err:
                                                                print(f"[forecast] {ticker} CALL snapshot deferred until position fill is visible: {snapshot_err}")
                                                            self.last_trade_timestamps[ticker] = self.get_now()
                                                            await self._increment_trade_count(ticker, entry=True)
                                                            await self.save_settings()
                                                        else:
                                                            result['reason'] = f"Option buy failed: {order_result.get('error')}"
                                                    else:
                                                        result['reason'] = f"Skipped: single contract cost (${est_premium * 100:.2f}) exceeds allocation (${allocated:.2f})"
                                                else:
                                                    result['reason'] = "Skipped: option premium estimate could not be retrieved."
                                            else:
                                                result['reason'] = "Skipped: no suitable CALL contract found."
                                    else:
                                        # Standard Stock/Crypto Buy execution
                                        sizing = result['position_sizing']
                                        enable_risk = t_settings.get('enable_risk_management', True)
                                        max_pos = getattr(self.config, 'MAX_OPEN_POSITIONS', 5) if enable_risk else 999999
                                        if portfolio_count >= max_pos and not has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                        elif has_this_pos and result.get('continuous_grid_side') != 'entry':
                                            result['action'] = 'HOLD'
                                            result['reason'] = "Position already open. Waiting for the next armed scale-in level."
                                        elif has_open_order:
                                            result['action'] = 'PENDING'
                                            result['reason'] = "Buy order pending."
                                        elif sizing['notional'] > 0:
                                            order_result = self.broker.place_order(
                                                symbol=ticker, notional=sizing['notional'], side='buy',
                                                stop_loss=sizing['stop_loss'], take_profit=sizing['take_profit'],
                                                market_session=t_settings.get('market_session', 'Regular'),
                                                current_price=result.get('price_raw'),
                                                client_order_id=self._order_intent_id(ticker, "buy", result),
                                            )
                                            if self._order_result_is_filled(order_result):
                                                result['order'] = order_result
                                                result['order_id'] = order_result.get('order_id')
                                                trade_executed = True
                                                if not has_this_pos:
                                                    portfolio_count += 1
                                                
                                                # Reset trailing stop so new strategy starts fresh from entry price
                                                self.last_trailing_stops.pop(ticker, None)                                                
                                                order_qty = order_result.get('qty')
                                                if (order_qty is None or float(order_qty) == 0) and result['price_raw'] > 0:
                                                    order_qty = sizing['notional'] / result['price_raw']
                                                    
                                                result['qty'] = float(order_qty) if order_qty else 0
                                                result['total_cost'] = float(order_result.get('total_cost', sizing['notional']))
                                                result['fees'] = float(order_result.get('fees', 0))
                                                result['reason'] = f"✅ BOUGHT {result['qty']:.6f} shares at {result['price']}: {result['reason']}"
                                                self.last_trade_timestamps[ticker] = self.get_now()

                                                defer_grid_buy_confirmation = bool(
                                                    result.get('continuous_grid_side') == 'entry'
                                                    and not self.broker.simulation_mode
                                                    and order_result.get('order_id')
                                                )
                                                if entry_grid_continuous or exit_grid_continuous:
                                                    is_grid_buy = result.get('continuous_grid_side') == 'entry'
                                                    if defer_grid_buy_confirmation:
                                                        _set_continuous_grid_pending(
                                                            t_settings,
                                                            side='entry',
                                                            order_id=order_result.get('order_id'),
                                                            level=result.get('continuous_grid_level'),
                                                            before_qty=_safe_float(pos_info.get('qty'), 0.0) if pos_info else 0.0,
                                                            fill_price=float(result['price_raw']),
                                                            current_atr=max(0.0, _safe_float(result.get('atr'), 0.0)),
                                                        )
                                                    else:
                                                        _record_continuous_grid_fill(
                                                            t_settings,
                                                            side="buy",
                                                            fill_price=float(result['price_raw']),
                                                            was_grid_order=is_grid_buy,
                                                            had_position=has_this_pos,
                                                            filled_level=result.get('continuous_grid_level'),
                                                            current_atr=max(0.0, _safe_float(result.get('atr'), 0.0)),
                                                        )
                                                    await self.save_settings()

                                                if not defer_grid_buy_confirmation:
                                                    await self._increment_trade_count(ticker, entry=True)
                                                try:
                                                    fresh_positions = self.broker.get_positions()
                                                    fresh_pos = self._find_position(fresh_positions, ticker_norm)
                                                    if fresh_pos:
                                                        from main import _ensure_exit_forecast_snapshot
                                                        _ensure_exit_forecast_snapshot(
                                                            t_settings,
                                                            result,
                                                            fresh_pos,
                                                            ticker,
                                                        )
                                                        protection = self._sync_exit_protection(ticker, result, fresh_pos, t_settings, force=True)
                                                        if protection:
                                                            result['exit_protection'] = protection
                                                    else:
                                                        print(f"[exit-protect] {ticker_norm} buy accepted; watcher and exit snapshot will be applied after position fill is visible")
                                                except Exception as protect_err:
                                                    print(f"[exit-protect] {ticker_norm} post-buy protection error: {protect_err}")
                                            else:
                                                result['action'] = 'HOLD'
                                                result['reason'] = f"Buy order failed: {order_result.get('error', 'Broker Error')}"
                                                print(f"[engine] Buy order failed for {ticker}: {result['reason']}")
                                                
                                # Sell Execution (or PUT option entry)
                                elif not options_execution_handled and result['action'] == 'SELL' and not is_market_closed_skip:
                                    if is_options_enabled:
                                        if has_this_pos:
                                            if has_open_sell_order:
                                                result['action'] = 'PENDING'
                                                result['reason'] = 'Options exit order pending; duplicate close suppressed.'
                                                continue
                                            # Close active option position
                                            order_result = self._close_option_with_limit(
                                                pos_info, ticker=ticker, result=result, t_settings=t_settings
                                            )
                                            if self._order_result_is_filled(order_result):
                                                result['order'] = order_result
                                                result['order_id'] = order_result.get('order_id')
                                                trade_executed = True
                                                portfolio_count -= 1
                                                
                                                qty = float(pos_info['qty'])
                                                exit_check_price = _safe_float(result.get('option_exit_check', {}).get('premium'), pos_info.get('current_price'))
                                                val = qty * exit_check_price * 100.0
                                                cost = qty * float(pos_info['avg_price']) * 100.0
                                                pl = val - cost
                                                pl_pct = (pl / cost * 100.0) if cost > 0 else 0.0
                                                
                                                result['qty'] = qty
                                                result['total_cost'] = val
                                                result['fees'] = 0.0
                                                result['pl'] = round(pl, 2)
                                                result['pl_pct'] = round(pl_pct, 2)
                                                
                                                result['reason'] = f"✅ CLOSED option position ({pos_info['symbol']}) at checked premium ${exit_check_price:.2f} (est. P/L: ${pl:+.2f}): {result['reason']}"
                                                t_settings.pop('options_underlying_entry_price', None)
                                                t_settings.pop('options_active_contract', None)
                                                t_settings.pop('options_active_cycle_id', None)
                                                from main import _clear_forecast_exit_snapshots
                                                _clear_forecast_exit_snapshots(t_settings)
                                                self.last_trailing_stops.pop(pos_info['symbol'], None)
                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                await self._increment_trade_count(ticker)
                                                
                                                # Accumulate realized P/L
                                                locked_dir = 'BUY' if opt_type == 'CALL' else 'SELL'
                                                self._record_realized_exit(
                                                    ticker, t_settings, pl, result['reason'],
                                                    result.get('price_raw'), locked_dir, complete=True,
                                                )
                                                self._apply_compound_pl(t_settings, pl, 'options')
                                                await self.save_settings()

                                                enable_signal_reset = t_settings.get('enable_signal_reset', True)
                                                if enable_signal_reset:
                                                    self._set_signal_reset_lock(ticker, locked_dir, t_settings)
                                                    await self.save_settings()
                                                    print(f"[engine-{self.uid}] Signal Reset Activated: Locked {locked_dir} re-entry for {ticker}")
                                            else:
                                                result['reason'] = f"Option sell failed: {order_result.get('error')}"
                                        else:
                                            # No option position, bearish signal -> BUY PUT option!
                                            enable_risk = t_settings.get('enable_risk_management', True)
                                            max_pos = getattr(self.config, 'MAX_OPEN_POSITIONS', 5) if enable_risk else 999999
                                            if portfolio_count >= max_pos:
                                                result['action'] = 'HOLD'
                                                result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                            elif has_open_order:
                                                result['action'] = 'PENDING'
                                                result['reason'] = "Options buy order pending."
                                            elif t_settings.get("options_type", "calls_only") == "calls_only":
                                                result['reason'] = "Strategy is configured for Calls Only."
                                            else:
                                                # Option contract selection (PUT)
                                                from option_selector import get_option_chain, select_contract
                                                chain = get_option_chain(
                                                    underlying=ticker,
                                                    expiration_limit_days=opt_expiry_days,
                                                    current_price=result['price_raw'],
                                                    client=self.broker.client
                                                )
                                                selection_data_client = None
                                                if not self.broker.simulation_mode and self.config.ALPACA_API_KEY and self.config.ALPACA_SECRET_KEY:
                                                    try:
                                                        from alpaca.data.historical.option import OptionHistoricalDataClient
                                                        selection_data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                                                    except Exception as data_client_err:
                                                        print(f"[options] Delta selection unavailable: {data_client_err}")
                                                contract = select_contract(
                                                    contracts=chain,
                                                    current_underlying_price=result['price_raw'],
                                                    option_type="PUT",
                                                    strike_type=opt_strike_type,
                                                    target_expiry_days=opt_expiry_days,
                                                    data_client=selection_data_client
                                                )
                                                
                                                if contract:
                                                    # Fetch estimate premium for sizing
                                                    import math
                                                    quote_bid = quote_ask = quote_spread = quote_spread_pct = None
                                                    if self.broker.simulation_mode:
                                                        from options_pricing import black_scholes
                                                        import datetime
                                                        days = (contract['expiration'] - datetime.date.today()).days
                                                        t_years = max(0.0001, days / 365.0)
                                                        est_premium = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.20, option_type="PUT")
                                                        est_premium = max(0.05, est_premium)
                                                    else:
                                                        try:
                                                            from alpaca.data.historical.option import OptionHistoricalDataClient
                                                            from alpaca.data.requests import OptionLatestQuoteRequest
                                                            data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                                                            req = OptionLatestQuoteRequest(symbol_or_symbols=[contract['symbol']])
                                                            quotes = data_client.get_option_latest_quote(req)
                                                            quote = quotes[contract['symbol']]
                                                            quote_bid = _safe_float(getattr(quote, 'bid_price', None), 0.0)
                                                            quote_ask = _safe_float(getattr(quote, 'ask_price', None), 0.0)
                                                            est_premium = quote_ask
                                                            if quote_bid > 0 and quote_ask > 0:
                                                                quote_spread = quote_ask - quote_bid
                                                                quote_mid = (quote_bid + quote_ask) / 2.0
                                                                quote_spread_pct = (quote_spread / quote_mid) * 100.0 if quote_mid > 0 else None
                                                        except Exception as quote_err:
                                                            print(f"[options] Quote fetch failed (no OPRA?): {quote_err}. Falling back to theoretical pricing.")
                                                            from options_pricing import black_scholes
                                                            import datetime
                                                            days = (contract['expiration'] - datetime.date.today()).days
                                                            t_years = max(0.0001, days / 365.0)
                                                            est_premium_bs = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.24, option_type="PUT", q=0.013)
                                                            est_premium = max(0.01, est_premium_bs + 0.015)

                                                    spread_limit_pct = _safe_float(
                                                        t_settings.get('options_max_entry_spread_pct'),
                                                        5.0
                                                    )
                                                    if not self.broker.simulation_mode and (_safe_float(quote_bid, 0.0) <= 0 or _safe_float(quote_ask, 0.0) <= 0):
                                                        result['reason'] = f"Skipped: live option bid/ask unavailable for {contract['symbol']}."
                                                    elif not self.broker.simulation_mode and quote_spread_pct is not None and quote_spread_pct > spread_limit_pct:
                                                        result['reason'] = f"Skipped: option spread too wide for {contract['symbol']} ({quote_spread_pct:.1f}% > {spread_limit_pct:.1f}%)."
                                                    elif est_premium > 0:
                                                        if not self.broker.simulation_mode and quote_bid > 0 and quote_ask > 0:
                                                            quote_mid = (quote_bid + quote_ask) / 2.0
                                                            aggression = min(1.0, max(0.0, _safe_float(t_settings.get('options_limit_aggression'), 0.25)))
                                                            est_premium = quote_mid + ((quote_ask - quote_mid) * aggression)
                                                        allocated = opt_trade_amount
                                                        qty = math.floor(allocated / (est_premium * 100.0))
                                                        if qty > 0:
                                                            order_result = self.broker.place_option_order(
                                                                option_symbol=contract['symbol'],
                                                                qty=qty,
                                                                side='buy',
                                                                limit_price=est_premium,
                                                                client_order_id=self._order_intent_id(ticker, "buy", result, contract['symbol']),
                                                            )
                                                            if self._order_result_is_filled(order_result):
                                                                result['order'] = order_result
                                                                result['order_id'] = order_result.get('order_id')
                                                                trade_executed = True
                                                                portfolio_count += 1

                                                                result['qty'] = qty
                                                                result['total_cost'] = qty * est_premium * 100.0
                                                                result['fees'] = 0.0
                                                                result['option_entry_quote'] = {
                                                                    'bid': quote_bid,
                                                                    'ask': quote_ask,
                                                                    'spread': quote_spread,
                                                                    'spread_pct': quote_spread_pct,
                                                                    'limit_price': est_premium,
                                                                }
                                                                spread_note = f" | bid/ask ${quote_bid:.2f}/${quote_ask:.2f}, spread {quote_spread_pct:.1f}%" if quote_spread_pct is not None else ""
                                                                selection_note = f" | {contract.get('selection_reason')}" if contract.get('selection_reason') else ""
                                                                result['reason'] = f"✅ BOUGHT {qty} PUT contracts ({contract['symbol']}) with limit ${est_premium:.2f}{spread_note}{selection_note}: {result['reason']}"
                                                                t_settings['options_underlying_entry_price'] = float(result.get('price_raw') or 0.0)
                                                                t_settings['options_active_contract'] = contract['symbol']
                                                                t_settings['options_active_cycle_id'] = order_result.get('client_order_id') or order_result.get('order_id')
                                                                try:
                                                                    fresh_positions = self.broker.get_positions()
                                                                    fresh_option_pos = self._find_position(
                                                                        fresh_positions,
                                                                        contract['symbol'].replace("/", "").upper(),
                                                                    )
                                                                    if fresh_option_pos:
                                                                        from main import _ensure_exit_forecast_snapshot
                                                                        _ensure_exit_forecast_snapshot(
                                                                            t_settings,
                                                                            result,
                                                                            fresh_option_pos,
                                                                            ticker,
                                                                        )
                                                                except Exception as snapshot_err:
                                                                    print(f"[forecast] {ticker} PUT snapshot deferred until position fill is visible: {snapshot_err}")
                                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                                await self._increment_trade_count(ticker, entry=True)
                                                                await self.save_settings()
                                                            else:
                                                                result['reason'] = f"Option buy failed: {order_result.get('error')}"
                                                        else:
                                                            result['reason'] = f"Skipped: single contract cost (${est_premium * 100:.2f}) exceeds allocation (${allocated:.2f})"
                                                    else:
                                                        result['reason'] = "Skipped: option premium estimate could not be retrieved."
                                                else:
                                                    result['reason'] = "Skipped: no suitable PUT contract found."
                                    else:
                                        # Standard Stock/Crypto Sell execution
                                        if has_this_pos:
                                            pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                                            if has_open_sell_order:
                                                result['action'] = 'PENDING'
                                                result['reason'] = 'Sell order pending; no duplicate scale-out submitted.'
                                            else:
                                                grid_scale_out = result.get('continuous_grid_side') == 'exit'
                                                total_qty = float(pos_info['qty']) if pos_info else 0.0
                                                sell_fraction = _continuous_grid_sell_fraction(
                                                    t_settings,
                                                    result,
                                                    current_qty=total_qty,
                                                )
                                                sell_qty = total_qty * sell_fraction
                                                closes_position = (
                                                    not grid_scale_out
                                                    or sell_fraction >= 0.99
                                                    or (total_qty - sell_qty) < 0.0001
                                                )

                                                if closes_position:
                                                    order_result = self.broker.close_position(
                                                        ticker,
                                                        market_session=t_settings.get('market_session', 'Regular'),
                                                        current_price=result.get('price_raw'),
                                                        client_order_id=self._order_intent_id(ticker, "sell", result)
                                                    )
                                                else:
                                                    sell_notional = sell_qty * float(result.get('price_raw', 0.0))
                                                    order_result = self.broker.place_order(
                                                        symbol=ticker, notional=sell_notional, side='sell',
                                                        market_session=t_settings.get('market_session', 'Regular'),
                                                        current_price=result.get('price_raw'),
                                                        client_order_id=self._order_intent_id(ticker, "sell", result)
                                                    )
                                                if self._order_result_is_filled(order_result):
                                                    result['order'] = order_result
                                                    result['order_id'] = order_result.get('order_id')
                                                    trade_executed = True
                                                    defer_grid_sell_confirmation = bool(
                                                        grid_scale_out
                                                        and not self.broker.simulation_mode
                                                        and order_result.get('order_id')
                                                    )
                                                    if closes_position and not defer_grid_sell_confirmation:
                                                        portfolio_count -= 1

                                                    order_qty = _safe_float(order_result.get('qty'), 0.0)
                                                    if order_qty <= 0:
                                                        order_qty = total_qty if closes_position else sell_qty
                                                    result['qty'] = order_qty
                                                    exit_price = float(result.get('price_raw', 0.0))
                                                    result['total_cost'] = _safe_float(order_result.get('proceeds'), order_qty * exit_price)
                                                    result['fees'] = float(order_result.get('fees', 0))

                                                    if pos_info:
                                                        entry_price = float(pos_info['avg_price'])
                                                        pl = (exit_price - entry_price) * order_qty
                                                        pl_pct = ((exit_price / entry_price) - 1) * 100
                                                        result['pl'] = round(pl, 2)
                                                        result['pl_pct'] = round(pl_pct, 2)
                                                        fill_label = 'CLOSED' if closes_position else f'SCALED OUT {sell_fraction * 100:.1f}%'
                                                        result['reason'] = f"✅ {fill_label} at {result['price']} (Entry: ${entry_price:.4f}): {result['reason']}"

                                                        if not defer_grid_sell_confirmation:
                                                            self._record_realized_exit(
                                                                ticker, t_settings, pl, result['reason'], exit_price,
                                                                'BUY', complete=bool(closes_position),
                                                            )
                                                            self._apply_compound_pl(t_settings, pl, 'regular')
                                                        if defer_grid_sell_confirmation:
                                                            _set_continuous_grid_pending(
                                                                t_settings,
                                                                side='exit',
                                                                order_id=order_result.get('order_id'),
                                                                level=result.get('continuous_grid_level'),
                                                                before_qty=total_qty,
                                                                fill_price=exit_price,
                                                                entry_price=entry_price,
                                                                                final_level=closes_position,
                                                                runner_exit=bool(result.get('continuous_grid_runner_exit')),
                                                                                current_atr=max(0.0, _safe_float(result.get('atr'), 0.0)),
                                                            )
                                                        else:
                                                            _record_continuous_grid_fill(
                                                                t_settings,
                                                                side='sell',
                                                                fill_price=exit_price,
                                                                was_grid_order=grid_scale_out,
                                                                had_position=True,
                                                                position_closed=closes_position,
                                                                continue_cycle=(
                                                                    grid_scale_out
                                                                    and closes_position
                                                                    and entry_grid_continuous
                                                                    and exit_grid_continuous
                                                                ),
                                                                filled_level=result.get('continuous_grid_level'),
                                                                current_atr=max(0.0, _safe_float(result.get('atr'), 0.0)),
                                                            )
                                                        if closes_position and not defer_grid_sell_confirmation:
                                                            from main import _clear_forecast_exit_snapshots
                                                            _clear_forecast_exit_snapshots(t_settings)
                                                        await self.save_settings()
                                                    else:
                                                        result['reason'] = f"✅ SOLD at {result['price']}: {result['reason']}"
                                                    self.last_trade_timestamps[ticker] = self.get_now()
                                                    if not (pos_info and defer_grid_sell_confirmation):
                                                        await self._increment_trade_count(ticker)

                                                    enable_signal_reset = t_settings.get('enable_signal_reset', True)
                                                    if (
                                                        closes_position
                                                        and enable_signal_reset
                                                        and not grid_scale_out
                                                        and not (pos_info and defer_grid_sell_confirmation)
                                                    ):
                                                        self._set_signal_reset_lock(ticker, 'BUY', t_settings)
                                                        await self.save_settings()
                                                        print(f"[engine-{self.uid}] Signal Reset Activated: Locked BUY re-entry for standard asset {ticker}")
                                                else:
                                                    result['reason'] = f"Sell order failed: {order_result.get('error', 'Broker Error')}"
                                        else:
                                            result['reason'] = "Signal SELL ignored: No open position detected."
                                            result['action'] = "HOLD"
                            
                            result['log_type'] = "Active Bot" if is_active else "Watchlist"
                            log_bucket_format = "%Y-%m-%dT%H:%M:%S" if is_opt_bot else "%Y-%m-%dT%H:%M"
                            log_time_bucket = self.get_now().strftime(log_bucket_format)
                            log_key = f"{log_time_bucket}_{ticker}_{result['action']}"
                            
                            def _get_log_key(log_entry):
                                try:
                                    t_str = datetime.fromisoformat(log_entry.get('time', '')).strftime(log_bucket_format)
                                    return f"{t_str}_{log_entry.get('ticker','')}_{log_entry.get('action','')}"
                                except: return ""
                                
                            if is_active:
                                self._record_bot_scan(result)
                                _print_detailed_bot_scan(ticker, result, t_settings, has_this_pos, is_opt_bot, ticker_norm)
                            else:
                                self._record_scan(result)
                            if not any(_get_log_key(log) == log_key for log in self.trade_log):
                                if is_active:
                                    self.trade_log.insert(0, result)
                                if trade_executed and is_active:
                                    await self.save_history_throttled(force=True)

                    except Exception as e:
                        print(f"[scheduler-{self.uid}] Error scanning {ticker}: {e}")

                if len(self.trade_log) > 200:
                    self.trade_log = self.trade_log[:200]

                self.last_scan_time = self.get_now().isoformat()
                await self.save_history_throttled()

            except Exception as e:
                print(f"[scheduler-{self.uid}] Loop error: {e}")

            try:
                min_sleep = getattr(self.config, 'OPTIONS_SCAN_INTERVAL_SECONDS', 10) if any(
                    self.config.TICKER_SETTINGS.get(t, {}).get('options_trading', self.config.TICKER_SETTINGS.get(t, {}).get('is_option', False))
                    for t in self.config.TRADELIST
                ) else getattr(self.config, 'SCAN_INTERVAL_SECONDS', 60)
                await asyncio.wait_for(self.force_scan_trigger.wait(), timeout=min_sleep)
                self.force_scan_trigger.clear()
            except asyncio.TimeoutError:
                self.force_scan_trigger.clear()

    async def _increment_trade_count(self, ticker: str, entry: bool = False):
        t_settings = self.config.TICKER_SETTINGS.get(ticker, {})
        if not t_settings:
            return
            
        now = self.get_now()
        today_str = now.strftime("%Y-%m-%d")
        
        # Increment total trades
        t_settings['total_trades'] = t_settings.get('total_trades', 0) + 1
        if entry:
            t_settings['entry_trades'] = t_settings.get('entry_trades', 0) + 1
        t_settings['last_trade_at'] = now.isoformat()
        
        # Increment today trades
        last_trade_date = t_settings.get('last_trade_date', '')
        if last_trade_date == today_str:
            t_settings['today_trades'] = t_settings.get('today_trades', 0) + 1
        else:
            t_settings['last_trade_date'] = today_str
            t_settings['today_trades'] = 1
            
        await self.save_settings()


class UserManager:
    MAX_ENGINES = 1024
    ENGINE_IDLE_SECONDS = 3600.0

    def __init__(self, db):
        self.db = db
        self.engines = {}

    def _evict_idle_engines(self):
        now = time.monotonic()
        stale = sorted(
            (getattr(engine, "last_access_at", 0.0), uid, engine)
            for uid, engine in self.engines.items()
            if not getattr(engine, "bot_running", False)
            and not (getattr(engine, "task", None) and not engine.task.done())
            and now - getattr(engine, "last_access_at", 0.0) > self.ENGINE_IDLE_SECONDS
        )
        for _, uid, engine in stale:
            self.engines.pop(uid, None)
            clear_credentials = getattr(getattr(engine, "broker", None), "clear_credentials", None)
            if callable(clear_credentials):
                clear_credentials()

    def get_engine(self, uid: str) -> UserEngine:
        uid = str(uid or "").strip()
        if not uid:
            raise ValueError("Authenticated user id is required")
        self._evict_idle_engines()
        if uid not in self.engines:
            if len(self.engines) >= self.MAX_ENGINES:
                raise RuntimeError("User engine capacity is temporarily full; retry shortly.")
            print(f"[UserManager] Spawning engine for {uid}")
            engine = UserEngine(uid, self.db)
            self.engines[uid] = engine
        engine = self.engines[uid]
        engine.last_access_at = time.monotonic()
        return engine

    async def start_user(self, uid: str):
        engine = self.get_engine(uid)
        async with engine.task_start_lock:
            if engine.bot_running or (engine.task and not engine.task.done()):
                return
            await engine.load_from_cloud()
            engine.task = asyncio.create_task(engine.trading_loop())
            
    async def start_all(self):
        print("[UserManager] Loading all user configurations...")
        if not self.db: return
        try:
            users_ref = self.db.collection("users").stream(timeout=3.0)
            for doc in users_ref:
                uid = doc.id
                engine = self.get_engine(uid)
                async with engine.load_lock:
                    if not engine.loaded_from_cloud:
                        await engine.load_from_cloud()
        except Exception as e:
            print(f"[UserManager] Error streaming users from Firestore: {e}")
        print(f"[UserManager] Started {len(self.engines)} user engines.")
        
    async def stop_all(self):
        for uid, engine in self.engines.items():
            if engine.bot_running:
                engine.bot_running = False
                if engine.task: engine.task.cancel()
