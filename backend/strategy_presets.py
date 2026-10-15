"""Pure strategy-preset catalog and evaluators (no I/O, no user settings reads).

A *strategy preset* is a fixed, documented rule set that owns its own indicator
values. It is independent of the Entry/Exit strategy families, the indicator
boxes, their thresholds and any per-indicator overrides.

Design rules (see AGENTS.md):
* Signals use completed candles only. ``completed_bars`` removes the forming
  candle and ``evaluate_strategy_preset`` refuses to act on anything that is
  not ready, degraded or incomplete (never a neutral BUY/SELL).
* Every column in ``compute_strategy_frame`` looks only backwards, so
  evaluating a prefix of the data gives the same answer as evaluating the full
  frame at that bar. Backtest, Bots and Screener share these functions.
* v1 strategies are long-only: BUY opens, SELL closes a long.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

import allocation_strategy
import nyse_calendar
import session_schedule

CATALOG_VERSION = 1
PRESET_MODE = "strategy_preset"  # sentinel stored in buy_mode / sell_mode
DISABLED = "none"

# Only these settings may be overridden by a strategy. Risk and sizing fields
# are deliberately absent: a strategy can never loosen them.
ALLOWED_OVERRIDE_KEYS = ("timeframe", "market_session", "ext_hours")
SUGGESTED_SIZING_KEYS = ("allocation_mode", "trade_amount")
# Scan timing a strategy may define for live bots. "interval" = the global scan refresh (no
# override). "per_candle" = while flat, evaluate once per completed candle; an open position or
# pending order is still watched at the normal refresh while the session is open (the software
# protective stop needs live prices). ``timeframes`` limits where the policy applies: elsewhere
# (for example an intraday timeframe the user picked) the normal refresh is used.
SCAN_POLICY_MODES = ("interval", "per_candle")
SCAN_POLICY_TIMEFRAMES = ("1Day",)

# Settings that are backed up when a preset is enabled and restored when it is
# disabled.
# A strategy decides its own entries and exits, so the manual re-entry rules do not apply to it: they are
# switched off while it is enabled (the UI greys them out) and restored from the backup when it is turned off.
# Post-stop candles, spread limits, loss limits and every other risk guard are unaffected.
PRESET_FORCED_OFF = {"enable_cooldown": False, "enable_signal_reset": False, "enable_smart_reentry": False}
_FORCED_OFF_DEFAULTS = {"enable_cooldown": True, "enable_signal_reset": True, "enable_smart_reentry": False}

MANUAL_BACKUP_KEYS = (
    "enable_cooldown",
    "enable_signal_reset",
    "enable_smart_reentry",
    "buy_mode",
    "sell_mode",
    "indicators",
    "min_buy_signals",
    "min_sell_signals",
    "threshold",
    "sell_threshold",
) + ALLOWED_OVERRIDE_KEYS

STATUS_READY = "READY"
STATUS_WAITING = "WAITING_DATA"
STATUS_DEGRADED = "DEGRADED"
STATUS_INVALID = "INVALID_PRESET"

# Daily bars complete shortly after the closing auction; this settle delay
# keeps the signal off a still-printing final tick.
DAILY_SETTLE_MINUTES = 5

_TIMEFRAME_SECONDS = {
    "1Min": 60, "5Min": 300, "10Min": 600, "15Min": 900, "30Min": 1800,
    "1Hour": 3600, "2Hour": 7200, "4Hour": 14400, "1Day": 86400,
}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PresetVariant:
    id: str
    label: str
    description: str
    params: Mapping[str, Any]
    # Optional per-variant settings. Anything left empty is inherited from the strategy,
    # so a sub-option can carry its own timeframe, session or trade allocation.
    supported_timeframes: Optional[Tuple[str, ...]] = None
    supported_sessions: Optional[Tuple[str, ...]] = None
    default_timeframe: Optional[str] = None
    suggested_sizing: Mapping[str, Any] = field(default_factory=dict)
    scan_policy: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyPreset:
    id: str
    family: str
    label: str
    summary: str
    entry_rule: str
    exit_rule: str
    variants: Tuple[PresetVariant, ...]
    default_variant: str
    supported_timeframes: Tuple[str, ...]
    default_timeframe: str
    supported_sessions: Tuple[str, ...] = ("Regular",)
    # ``supported_*`` is what the strategy was designed and validated for (its
    # recommended defaults). ``allowed_*`` is everything the engine can run; the
    # user may choose any allowed value, and anything outside ``supported_*`` is
    # flagged as untested.
    allowed_timeframes: Tuple[str, ...] = ("1Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "4Hour", "1Day")
    allowed_sessions: Tuple[str, ...] = ("Regular", "Extended")
    overrides: Mapping[str, Any] = field(default_factory=dict)
    # Optional trade-allocation values the strategy itself defines (``allocation_mode``
    # fixed|compound, ``trade_amount`` in dollars). Empty when a strategy defines none:
    # no capital is ever invented. The UI fills these fields when the strategy is picked
    # and flags any manual change; the server treats them as ordinary user input, so every
    # risk and sizing limit still applies. Never a risk limit.
    suggested_sizing: Mapping[str, Any] = field(default_factory=dict)
    # Optional scan timing the strategy defines (see SCAN_POLICY_MODES); empty = the user's refresh.
    scan_policy: Mapping[str, Any] = field(default_factory=dict)
    long_only: bool = True
    protective_stop_atr_mult: float = 3.0
    protective_atr_period: int = 14
    # "signal": BUY/SELL on the bot's own ticker (Golden Cross, Supertrend).
    # "allocation": target-% rebalancing of leveraged ETFs from a benchmark signal
    # (Malik; rules in allocation_strategy.py). Variants are the direction sub-option.
    # "schedule": buy and sell at clock times on the bot's own ticker (Session Rider;
    # rules in session_schedule.py). No indicators.
    kind: str = "signal"


def _golden_cross_warmup(params: Mapping[str, Any]) -> int:
    slow = int(params["slow"])
    return (3 * slow + 1 if params["ma"] == "ema" else slow + 1) + 1


def _supertrend_warmup(params: Mapping[str, Any]) -> int:
    ema = int(params["ema"])
    return max(3 * ema + 1, int(params["atr_period"]) + 2) + 1


PRESETS: Dict[str, StrategyPreset] = {
    "golden_cross": StrategyPreset(
        id="golden_cross",
        family="Trend",
        label="Golden Cross",
        summary="Long when the fast moving average crosses above the slow one; flat when it falls back below.",
        entry_rule="Fast average crosses above the slow average on a completed daily bar.",
        exit_rule="Fast average is below the slow average on a completed daily bar.",
        variants=(
            PresetVariant("sma_50_200", "Classic 50/200 SMA · Recommended",
                          "Simple moving averages, 50 over 200 days.",
                          {"ma": "sma", "fast": 50, "slow": 200}),
            PresetVariant("ema_50_200", "50/200 EMA",
                          "Exponential moving averages, 50 over 200 days.",
                          {"ma": "ema", "fast": 50, "slow": 200}),
            PresetVariant("sma_20_50", "Fast 20/50 SMA",
                          "Simple moving averages, 20 over 50 days. Trades more often.",
                          {"ma": "sma", "fast": 20, "slow": 50}),
        ),
        default_variant="sma_50_200",
        supported_timeframes=("1Day",),
        default_timeframe="1Day",
        overrides={},
        # Daily signal on a completed candle, acted on at the next session open: re-evaluating
        # every minute cannot change it, so a flat bot evaluates once per completed daily candle.
        scan_policy={"mode": "per_candle", "timeframes": ("1Day",)},
    ),
    "supertrend_ema200": StrategyPreset(
        id="supertrend_ema200",
        family="Trend",
        label="Supertrend + 200 EMA",
        summary="Long when Supertrend turns bullish while price is above the 200 EMA.",
        entry_rule="Supertrend flips bullish on a completed bar and the close is above the 200 EMA.",
        exit_rule="Supertrend turns bearish, or the close falls below the 200 EMA, whichever comes first.",
        variants=(
            PresetVariant("classic_10_3", "Classic ATR 10 × 3 · Recommended",
                          "Supertrend with ATR period 10 and multiplier 3.",
                          {"atr_period": 10, "multiplier": 3.0, "ema": 200}),
            PresetVariant("aggressive_7_2", "Aggressive ATR 7 × 2",
                          "Supertrend with ATR period 7 and multiplier 2. Reacts faster.",
                          {"atr_period": 7, "multiplier": 2.0, "ema": 200}),
        ),
        default_variant="classic_10_3",
        # Daily only in v1: Alpaca and Webull align hourly bars differently, so an
        # hourly signal could differ between backtest and live.
        supported_timeframes=("1Day",),
        default_timeframe="1Day",
        overrides={},
        scan_policy={"mode": "per_candle", "timeframes": ("1Day",)},
    ),
}


def _malik_preset(preset_id: str, label: str) -> StrategyPreset:
    spec = allocation_strategy.SPECS[preset_id]
    return StrategyPreset(
        id=preset_id,
        family="Leveraged trend (Malik)",
        label=label,
        summary=(f"Reads {spec.signal} and holds a target % of a leveraged {spec.signal} ETF: 100% in a strong uptrend, "
                 "30–50% when momentum fades, 20% when defensive, cash below the 250-day average. Rebalances once a day before the close."),
        entry_rule=(f"Once a day at the execution time: {spec.signal} price vs its 250- and 50-day averages (completed closes plus the "
                    "current price) and its 5/10-day momentum set the regime and the target %."),
        exit_rule=("A leg is trimmed or sold when its target falls by more than 5% of the sleeve; below the 250-day average it goes to cash "
                   "(or into the inverse ETF in Long + inverse mode). No stop orders."),
        variants=(
            PresetVariant(allocation_strategy.DIRECTION_LONG_ONLY, "Long-only + cash · Recommended",
                          "Bear regimes hold cash.", {"direction": allocation_strategy.DIRECTION_LONG_ONLY}),
            PresetVariant(allocation_strategy.DIRECTION_INVERSE, "Long + inverse",
                          "Bear regimes buy the inverse ETF (60% in a crash, 20% in chop). Lowered results in every tested pair.",
                          {"direction": allocation_strategy.DIRECTION_INVERSE}),
        ),
        default_variant=allocation_strategy.DIRECTION_LONG_ONLY,
        supported_timeframes=("1Day",),
        default_timeframe="1Day",
        allowed_timeframes=("1Day",),
        supported_sessions=("Regular",),
        allowed_sessions=("Regular",),
        overrides={},
        kind=allocation_strategy.KIND,
    )


for _pid, _label in (("malik_qqq", "Malik QQQ"), ("malik_soxx", "Malik SOXX"), ("malik_iwm", "Malik IWM")):
    PRESETS[_pid] = _malik_preset(_pid, _label)


PRESETS[session_schedule.PRESET_ID] = StrategyPreset(
    id=session_schedule.PRESET_ID,
    family="Session / time of day",
    label="Session Rider",
    summary=("Buys at a set time and sells at a set time, every trading day. Buy late and sell the next morning to hold "
             "overnight, or buy early and sell later the same day to hold intraday. No indicators."),
    entry_rule=("Market buy at the Buy time (default 5 minutes before the close). Only inside a 5-minute window; "
                "a missed window is skipped, never bought late."),
    exit_rule=("Market sell at the Sell time (default 5 minutes after the next open). A missed sell is done as soon as "
               "possible. No stop-loss, take-profit or trailing exits."),
    variants=(PresetVariant("scheduled", "Scheduled buy and sell", "Your Buy time and Sell time decide everything.", {}),),
    default_variant="scheduled",
    # Scheduled entries need an intraday bar to honor the configured clock time.
    # 1Day remains available as the compatibility mode that automatically chooses
    # 1Min or 5Min bars from the schedule precision.
    supported_timeframes=("1Min", "5Min", "10Min", "15Min", "30Min", "1Day"),
    default_timeframe="1Day",
    allowed_timeframes=("1Min", "5Min", "10Min", "15Min", "30Min", "1Day"),
    supported_sessions=("Regular",),
    allowed_sessions=("Regular",),
    overrides={},
    kind=session_schedule.KIND,
)


def is_schedule(preset_or_id: Any) -> bool:
    preset = preset_or_id if isinstance(preset_or_id, StrategyPreset) else get_preset(preset_or_id)
    return preset is not None and preset.kind == session_schedule.KIND


def is_schedule_active(settings: Mapping[str, Any]) -> bool:
    enabled, preset_id, _ = normalize_strategy_preset(settings)
    return enabled and is_schedule(preset_id)


def is_allocation(preset_or_id: Any) -> bool:
    preset = preset_or_id if isinstance(preset_or_id, StrategyPreset) else get_preset(preset_or_id)
    return preset is not None and preset.kind == allocation_strategy.KIND


def is_allocation_active(settings: Mapping[str, Any]) -> bool:
    enabled, preset_id, _ = normalize_strategy_preset(settings)
    return enabled and is_allocation(preset_id)


def variant_settings(preset: StrategyPreset, variant: PresetVariant) -> Dict[str, Any]:
    """Effective timeframe/session/sizing a variant defines: its own, else the strategy's."""
    timeframes = tuple(variant.supported_timeframes or preset.supported_timeframes)
    sessions = tuple(variant.supported_sessions or preset.supported_sessions)
    default_tf = variant.default_timeframe or (preset.default_timeframe if preset.default_timeframe in timeframes else timeframes[0])
    return {
        "supported_timeframes": timeframes,
        "supported_sessions": sessions,
        "default_timeframe": default_tf,
        "suggested_sizing": {**dict(preset.suggested_sizing), **dict(variant.suggested_sizing)},
        "scan_policy": {**dict(preset.scan_policy), **dict(variant.scan_policy)},
    }


def _check_sizing(owner: str, sizing: Mapping[str, Any]) -> None:
    bad = [k for k in sizing if k not in SUGGESTED_SIZING_KEYS]
    if bad:
        raise ValueError(f"{owner} suggests disallowed sizing keys: {bad}")
    mode = sizing.get("allocation_mode")
    if mode is not None and mode not in ("fixed", "compound"):
        raise ValueError(f"{owner} suggests an unknown allocation mode: {mode}")
    amount = sizing.get("trade_amount")
    if amount is not None and not (isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0):
        raise ValueError(f"{owner} suggests an invalid trade amount: {amount}")


def _check_scan_policy(owner: str, policy: Mapping[str, Any]) -> None:
    if not policy:
        return
    unknown = [k for k in policy if k not in ("mode", "timeframes")]
    if unknown:
        raise ValueError(f"{owner} scan policy has unknown keys: {unknown}")
    if policy.get("mode") not in SCAN_POLICY_MODES:
        raise ValueError(f"{owner} scan policy has an unknown mode: {policy.get('mode')}")
    frames = tuple(policy.get("timeframes") or ())
    if policy.get("mode") == "per_candle" and (not frames or not set(frames) <= set(SCAN_POLICY_TIMEFRAMES)):
        raise ValueError(f"{owner} per_candle scan policy supports only {SCAN_POLICY_TIMEFRAMES}")


def _validate_catalog() -> None:
    """Fail fast at import time if a catalog entry is malformed."""
    for pid, preset in PRESETS.items():
        if preset.id != pid:
            raise ValueError(f"preset key mismatch: {pid}")
        bad = [k for k in preset.overrides if k not in ALLOWED_OVERRIDE_KEYS]
        if bad:
            raise ValueError(f"preset {pid} overrides disallowed keys: {bad}")
        if preset.default_variant not in {v.id for v in preset.variants}:
            raise ValueError(f"preset {pid} default variant missing")
        _check_sizing(f"preset {pid}", preset.suggested_sizing)
        _check_scan_policy(f"preset {pid}", preset.scan_policy)
        if preset.default_timeframe not in preset.supported_timeframes:
            raise ValueError(f"preset {pid} default timeframe unsupported")
        if not preset.supported_sessions:
            raise ValueError(f"preset {pid} needs at least one supported session")
        if not set(preset.supported_timeframes) <= set(preset.allowed_timeframes):
            raise ValueError(f"preset {pid} recommends a timeframe it does not allow")
        if not {x.lower() for x in preset.supported_sessions} <= {x.lower() for x in preset.allowed_sessions}:
            raise ValueError(f"preset {pid} recommends a session it does not allow")
        for variant in preset.variants:
            eff = variant_settings(preset, variant)
            owner = f"preset {pid} variant {variant.id}"
            _check_sizing(owner, variant.suggested_sizing)
            _check_scan_policy(owner, variant.scan_policy)
            if eff["default_timeframe"] not in eff["supported_timeframes"]:
                raise ValueError(f"{owner} default timeframe unsupported")
            if not set(eff["supported_timeframes"]) <= set(preset.allowed_timeframes):
                raise ValueError(f"{owner} recommends a timeframe the engine does not allow")
            if not {x.lower() for x in eff["supported_sessions"]} <= {x.lower() for x in preset.allowed_sessions}:
                raise ValueError(f"{owner} recommends a session the engine does not allow")
        if not preset.long_only:
            raise ValueError("v1 supports long-only presets")


_validate_catalog()


def get_preset(preset_id: Any) -> Optional[StrategyPreset]:
    return PRESETS.get(str(preset_id or "").strip().lower())


def get_variant(preset: StrategyPreset, variant_id: Any) -> PresetVariant:
    wanted = str(variant_id or "").strip().lower()
    for variant in preset.variants:
        if variant.id == wanted:
            return variant
    for variant in preset.variants:
        if variant.id == preset.default_variant:
            return variant
    return preset.variants[0]


def warmup_bars(preset_id: Any, variant_id: Any = None) -> int:
    """Completed bars required before the strategy may emit a signal."""
    preset = get_preset(preset_id)
    if preset is None:
        return 0
    if preset.kind == allocation_strategy.KIND:
        return allocation_strategy.WARMUP_BARS
    if preset.kind == session_schedule.KIND:
        return 0
    params = get_variant(preset, variant_id).params
    base = _golden_cross_warmup(params) if preset.id == "golden_cross" else _supertrend_warmup(params)
    return max(base, preset.protective_atr_period + 2)


def strategy_preset_options() -> List[Dict[str, Any]]:
    """Serializable catalog for the UI (single source of truth)."""
    options = []
    for preset in PRESETS.values():
        options.append({
            "id": preset.id,
            "family": preset.family,
            "label": preset.label,
            "summary": preset.summary,
            "entry_rule": preset.entry_rule,
            "exit_rule": preset.exit_rule,
            "direction": "long_only",
            "default_variant": preset.default_variant,
            "supported_timeframes": list(preset.supported_timeframes),
            "supported_sessions": list(preset.supported_sessions),
            "allowed_timeframes": list(preset.allowed_timeframes),
            "allowed_sessions": list(preset.allowed_sessions),
            "default_timeframe": preset.default_timeframe,
            "overrides": dict(preset.overrides),
            "suggested_sizing": dict(preset.suggested_sizing),
            "protective_stop_atr_mult": preset.protective_stop_atr_mult,
            "catalog_version": CATALOG_VERSION,
            "kind": preset.kind,
            "allocation": _allocation_options(preset.id) if preset.kind == allocation_strategy.KIND else None,
            "schedule": _schedule_options() if preset.kind == session_schedule.KIND else None,
            "variants": [
                {
                    "id": v.id,
                    "label": v.label,
                    "description": v.description,
                    "warmup_bars": warmup_bars(preset.id, v.id),
                    **{k: (list(val) if isinstance(val, tuple) else val) for k, val in variant_settings(preset, v).items()},
                }
                for v in preset.variants
            ],
        })
    return options


def _allocation_options(preset_id: str) -> Dict[str, Any]:
    spec = allocation_strategy.SPECS[preset_id]
    return {
        "signal": spec.signal,
        "bull_options": [{"symbol": o.symbol, "leverage": o.leverage, "label": o.label} for o in spec.bull],
        "bear_options": [{"symbol": o.symbol, "leverage": o.leverage, "label": o.label} for o in spec.bear],
        "exec_time_options": allocation_strategy.exec_time_options(),
        "default_exec_time": allocation_strategy.EXEC_DEFAULT,
        "rebalance_band_pct": allocation_strategy.REBALANCE_BAND * 100,
        "regimes": allocation_strategy.regime_table(),
        "research": allocation_strategy.RESEARCH.get(preset_id),
        "research_note": allocation_strategy.research_note(preset_id),
        "research_report_url": allocation_strategy.RESEARCH_REPORT_URL,
    }


def _schedule_options() -> Dict[str, Any]:
    S = session_schedule
    return {
        "time_options": S.time_options(),
        "default_buy": S.BUY_DEFAULT,
        "default_sell": S.SELL_DEFAULT,
        "custom_earliest": S.EARLIEST.strftime("%H:%M"),
        "custom_latest": S.LATEST.strftime("%H:%M"),
        "entry_window_minutes": int(S.ENTRY_WINDOW.total_seconds() // 60),
        "loss_pause_default": S.LOSS_PAUSE_DEFAULT,
        "loss_pause_min": S.LOSS_PAUSE_MIN,
        "loss_pause_max": S.LOSS_PAUSE_MAX,
        "keys": {"buy": S.BUY_KEY, "sell": S.SELL_KEY, "skip_weekend": S.SKIP_WEEKEND_KEY, "loss_pause": S.LOSS_PAUSE_KEY},
    }


def effective_scan_policy(settings: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The scan timing the enabled strategy imposes on a live bot, or None for the normal refresh.

    Applies only while a strategy is enabled, its policy is ``per_candle`` and the bot's timeframe is
    one the policy covers; any other timeframe (an intraday one the user chose) keeps the normal refresh."""
    enabled, preset_id, variant_id = normalize_strategy_preset(settings)
    preset = get_preset(preset_id) if enabled else None
    if preset is None or preset.kind in (allocation_strategy.KIND, session_schedule.KIND):
        return None  # these bots run on their own clock (allocation_executor / session_executor)
    policy = variant_settings(preset, get_variant(preset, variant_id)).get("scan_policy") or {}
    if policy.get("mode") != "per_candle":
        return None
    if str((settings or {}).get("timeframe") or "") not in tuple(policy.get("timeframes") or ()):
        return None
    return {"mode": "per_candle", "timeframes": list(policy["timeframes"])}


def untested_settings_note(preset_id: Any, timeframe: Any, session: Any = None, variant_id: Any = None) -> str:
    """Empty when the settings are what the strategy was designed for, else a plain warning."""
    preset = get_preset(preset_id)
    if preset is None:
        return ""
    eff = variant_settings(preset, get_variant(preset, variant_id))
    notes = []
    if timeframe and str(timeframe) not in eff["supported_timeframes"]:
        notes.append(f"timeframe {timeframe} (designed for {', '.join(eff['supported_timeframes'])})")
    if session and str(session).lower() not in {x.lower() for x in eff["supported_sessions"]}:
        notes.append(f"{session} session (designed for {', '.join(eff['supported_sessions'])})")
    if not notes:
        return ""
    return "Not what this strategy was designed for: " + "; ".join(notes) + ". It still runs, but its behaviour and results there are untested."


def strategy_preset_label(settings: Mapping[str, Any]) -> str:
    enabled, preset_id, variant_id = normalize_strategy_preset(settings)
    preset = get_preset(preset_id) if enabled else None
    if preset is None:
        return ""
    variant = get_variant(preset, variant_id)
    return f"{preset.label} · {variant.label.replace(' · Recommended', '')}"


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def normalize_strategy_preset(settings: Mapping[str, Any]) -> Tuple[bool, str, str]:
    """Return ``(enabled, preset_id, variant_id)``. Unknown ids disable the preset."""
    settings = settings or {}
    preset = get_preset(settings.get("strategy_preset"))
    if not _truthy(settings.get("strategy_preset_enabled")) or preset is None:
        return False, DISABLED, ""
    variant = get_variant(preset, settings.get("strategy_preset_variant"))
    return True, preset.id, variant.id


def is_strategy_preset_active(settings: Mapping[str, Any]) -> bool:
    return normalize_strategy_preset(settings)[0]


def validate_strategy_preset(
    settings: Mapping[str, Any],
    *,
    ticker: str = "",
    options_trading: bool = False,
    is_crypto: bool = False,
) -> List[str]:
    """Return human-readable errors for combinations v1 cannot run."""
    enabled, preset_id, _ = normalize_strategy_preset(settings)
    if not enabled:
        return []
    errors = []
    if options_trading or _truthy((settings or {}).get("options_trading")):
        errors.append("Strategy presets support stocks and ETFs only, not options, in this version.")
    if is_crypto:
        errors.append("Strategy presets support stocks and ETFs only, not crypto, in this version.")
    if is_allocation(preset_id):
        errors.extend(allocation_strategy.validate_settings(preset_id, settings, ticker))
    if is_schedule(preset_id):
        errors.extend(session_schedule.validate(settings))
        if str(ticker or "").strip().upper() == "USD":
            errors.append("USD (ProShares Ultra Semiconductors) is not supported yet: the app treats tickers ending in USD as crypto.")
    return errors


def resolve_overrides(preset: StrategyPreset, settings: Mapping[str, Any], variant: Optional[PresetVariant] = None) -> Dict[str, Any]:
    """Allow-listed settings applied by the server while a strategy is enabled.

    The timeframe and market session are the user's own fields: any value the
    engine can run (``allowed_*``) is kept as chosen. Only a value the engine
    cannot run is replaced by the strategy's recommended one. (The UI sets the
    recommended value when a strategy is picked from the dropdown; the user may
    then change it.)
    """
    resolved = {k: v for k, v in preset.overrides.items() if k in ALLOWED_OVERRIDE_KEYS}
    eff = variant_settings(preset, variant or PresetVariant("", "", "", {}))  # no variant: the strategy's own values
    current_tf = str((settings or {}).get("timeframe") or "")
    if "timeframe" not in resolved:
        resolved["timeframe"] = current_tf if current_tf in preset.allowed_timeframes else eff["default_timeframe"]
    if "market_session" not in resolved:
        current_session = str((settings or {}).get("market_session") or "")
        match = next((x for x in preset.allowed_sessions if x.lower() == current_session.lower()), None)
        resolved["market_session"] = match or eff["supported_sessions"][0]
    if "ext_hours" not in resolved and str(resolved["market_session"]).lower() == "regular":
        resolved["ext_hours"] = False
    return resolved


def apply_strategy_preset(
    settings: Mapping[str, Any],
    prior_settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a copy of ``settings`` with the preset resolved.

    Enabling snapshots the manual Entry/Exit configuration and the overridden
    fields into ``_strategy_manual_backup`` (never the sentinel itself);
    disabling restores the Entry/Exit ones (timeframe/session stay as the user has them). Risk and sizing fields are never touched.
    """
    out = copy.deepcopy(dict(settings or {}))
    prior = dict(prior_settings or {})
    enabled, preset_id, variant_id = normalize_strategy_preset(out)
    backup = out.pop("_strategy_manual_backup", None)
    if not isinstance(backup, dict):
        prior_backup = prior.get("_strategy_manual_backup")
        backup = prior_backup if isinstance(prior_backup, dict) else None

    if not enabled:
        if backup:
            for key in MANUAL_BACKUP_KEYS:
                # Timeframe/session/ext-hours are plain user fields: whatever the form holds
                # when the strategy is turned off stays, it is never rolled back.
                if key in backup and key not in ALLOWED_OVERRIDE_KEYS:
                    out[key] = copy.deepcopy(backup[key])
        else:
            for key in ("buy_mode", "sell_mode"):
                if out.get(key) == PRESET_MODE:
                    out[key] = "indicator"
        out["strategy_preset_enabled"] = False
        out["strategy_preset"] = DISABLED
        out.pop("strategy_preset_variant", None)
        out.pop("_strategy_manual_backup", None)
        return out

    preset = PRESETS[preset_id]
    if backup is None:
        prior_is_manual = not is_strategy_preset_active(prior) and prior.get("buy_mode") != PRESET_MODE
        source = prior if (prior and prior_is_manual) else out
        backup = {
            key: copy.deepcopy(source[key])
            for key in MANUAL_BACKUP_KEYS
            if key in source and source[key] != PRESET_MODE
        }
        for key, default in _FORCED_OFF_DEFAULTS.items():
            backup.setdefault(key, default)  # an unset toggle means "on" for the bot, so remember that
    out["_strategy_manual_backup"] = backup
    out["strategy_preset_enabled"] = True
    out["strategy_preset"] = preset_id
    out["strategy_preset_variant"] = variant_id
    out["strategy_preset_catalog_version"] = CATALOG_VERSION
    out["buy_mode"] = PRESET_MODE
    out["sell_mode"] = PRESET_MODE
    out["min_buy_signals"] = 0
    out["min_sell_signals"] = 0
    out["threshold"] = 0
    out["sell_threshold"] = 0
    out["indicators"] = []
    out.update(PRESET_FORCED_OFF)
    out.update(resolve_overrides(preset, out, get_variant(preset, variant_id)))
    if preset.kind == allocation_strategy.KIND:
        out.update(allocation_strategy.normalized_settings(preset_id, out))
    if preset.kind == session_schedule.KIND:
        normalized = session_schedule.normalized_settings(out)
        out.update({k: v for k, v in normalized.items() if k in session_schedule.SETTING_KEYS and v is not None})
    return out


def preset_backtest_padding_days(preset_id: Any, variant_id: Any, timeframe: str) -> int:
    """Calendar days of history to fetch before the window so warmup is complete."""
    bars = warmup_bars(preset_id, variant_id)
    if bars <= 0:
        return 0
    if str(timeframe) == "1Day":
        return int(math.ceil(bars * 7.0 / 5.0)) + 10
    per_day = 6.5 if str(timeframe) == "1Hour" else 6.5 * 3600 / max(_TIMEFRAME_SECONDS.get(str(timeframe), 3600), 1)
    return int(math.ceil(bars / max(per_day, 0.5) * 7.0 / 5.0)) + 10


# ---------------------------------------------------------------------------
# Completed-bar handling
# ---------------------------------------------------------------------------

def _column(df: pd.DataFrame, name: str) -> pd.Series:
    for col in df.columns:
        if str(col).lower() == name:
            return pd.to_numeric(df[col], errors="coerce")
    raise KeyError(f"missing column {name}")


def _utc_index(index: pd.DatetimeIndex, is_crypto: bool) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(index)
    if index.tz is None:
        local = "UTC" if is_crypto else "America/New_York"
        index = index.tz_localize(local, ambiguous="NaT", nonexistent="shift_forward")
    return index.tz_convert("UTC")


def bar_completion_time(open_utc: pd.Timestamp, timeframe: str, is_crypto: bool = False) -> pd.Timestamp:
    """UTC time at which a bar that opened at ``open_utc`` is complete."""
    seconds = _TIMEFRAME_SECONDS.get(str(timeframe), 86400)
    default = open_utc + pd.Timedelta(seconds=seconds)
    if is_crypto:
        return default
    et = open_utc.tz_convert(nyse_calendar.EASTERN)
    # Providers stamp daily bars at midnight ET, midnight UTC (7-8 PM ET the
    # previous evening) or at the 9:30 open. Shifting by 6 hours maps every one
    # of those stamps to the session date the bar actually describes.
    session_date = (et + pd.Timedelta(hours=6)).date() if str(timeframe) == "1Day" else et.date()
    bounds = nyse_calendar.session_bounds(session_date)
    if bounds is None:
        return default
    close_utc = pd.Timestamp(bounds[1]).tz_convert("UTC")
    if str(timeframe) == "1Day":
        return close_utc + pd.Timedelta(minutes=DAILY_SETTLE_MINUTES)
    return min(default, close_utc)


def completed_bars(
    df: pd.DataFrame,
    timeframe: str,
    now_utc: Optional[datetime] = None,
    is_crypto: bool = False,
) -> pd.DataFrame:
    """Sorted, de-duplicated, finite OHLC rows whose bar is fully closed.

    ``now_utc`` is injectable for deterministic tests. The function is
    idempotent. Naive timestamps are treated as America/New_York for stocks
    (the backtest convention) and UTC for crypto.
    """
    if df is None or len(df) == 0:
        return df.iloc[:0] if isinstance(df, pd.DataFrame) else pd.DataFrame()
    frame = df.copy()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    for name in ("open", "high", "low", "close"):
        frame[[c for c in frame.columns if str(c).lower() == name][0]] = _column(frame, name)
    ohlc = pd.concat([_column(frame, n) for n in ("open", "high", "low", "close")], axis=1)
    frame = frame[np.isfinite(ohlc.to_numpy(dtype=float)).all(axis=1)]
    if frame.empty:
        return frame
    opens_utc = _utc_index(frame.index, is_crypto)
    valid = ~pd.isna(opens_utc)
    frame = frame[np.asarray(valid)]
    opens_utc = opens_utc[np.asarray(valid)]
    now = pd.Timestamp(now_utc if now_utc is not None else datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    # Completion time rises with bar open time, so the closed bars are a prefix:
    # binary-search its length instead of testing every row (this runs on every
    # live evaluation of a 1,000+ bar frame).
    low, high = 0, len(opens_utc)
    while low < high:
        mid = (low + high) // 2
        if bar_completion_time(opens_utc[mid], timeframe, is_crypto) <= now:
            low = mid + 1
        else:
            high = mid
    return frame.iloc[:low]


# ---------------------------------------------------------------------------
# Strategy computation (strictly causal)
# ---------------------------------------------------------------------------

def _moving_average(close: pd.Series, kind: str, period: int) -> pd.Series:
    if kind == "ema":
        return close.ewm(span=period, adjust=False, min_periods=period).mean()
    return close.rolling(period, min_periods=period).mean()


def _supertrend(high: pd.Series, low: pd.Series, close: pd.Series, period: int, multiplier: float):
    """Textbook Supertrend with a Wilder ATR (SMA seed). Returns (line, direction).

    Direction is +1 (bullish) or -1 (bearish); NaN until the ATR exists.
    """
    from indicators import _wilder_atr

    atr = _wilder_atr(high, low, close, period).to_numpy(dtype=float)
    h, l, c = high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float)
    n = len(c)
    line = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    for i in range(n):
        if not np.isfinite(atr[i]):
            continue
        mid = (h[i] + l[i]) / 2.0
        upper = mid + multiplier * atr[i]
        lower = mid - multiplier * atr[i]
        if i == 0 or not np.isfinite(final_upper[i - 1]):
            final_upper[i], final_lower[i] = upper, lower
            direction[i] = -1.0
            line[i] = upper
            continue
        final_upper[i] = upper if (upper < final_upper[i - 1] or c[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        final_lower[i] = lower if (lower > final_lower[i - 1] or c[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
        if direction[i - 1] == -1.0:
            direction[i] = 1.0 if c[i] > final_upper[i] else -1.0
        else:
            direction[i] = -1.0 if c[i] < final_lower[i] else 1.0
        line[i] = final_lower[i] if direction[i] == 1.0 else final_upper[i]
    return pd.Series(line, index=close.index), pd.Series(direction, index=close.index)


def _replay_state(ready: np.ndarray, entry: np.ndarray, exit_cond: np.ndarray):
    """Replay LONG/FLAT/UNKNOWN and bars since the last event."""
    n = len(ready)
    state = np.empty(n, dtype=object)
    since = np.full(n, np.nan)
    current, last_event = "UNKNOWN", None
    for i in range(n):
        if not ready[i]:
            current, last_event = "UNKNOWN", None
            state[i] = current
            continue
        if entry[i]:
            current, last_event = "LONG", i
        elif exit_cond[i]:
            if current != "FLAT":
                last_event = i
            current = "FLAT"
        state[i] = current
        if last_event is not None:
            since[i] = i - last_event
    return state, since


def compute_strategy_frame(df: pd.DataFrame, preset_id: Any, variant_id: Any = None) -> pd.DataFrame:
    """Return a copy of ``df`` with causal ``SP_*`` strategy columns.

    ``df`` must already contain only completed bars (``completed_bars``).
    """
    preset = get_preset(preset_id)
    if preset is None:
        raise ValueError(f"unknown strategy preset: {preset_id}")
    if preset.kind == allocation_strategy.KIND:
        raise ValueError(f"{preset.label} is an allocation strategy; use allocation_strategy")
    if preset.kind == session_schedule.KIND:
        raise ValueError(f"{preset.label} is a schedule strategy; use session_schedule")
    params = get_variant(preset, variant_id).params
    frame = df.copy()
    high, low, close = _column(frame, "high"), _column(frame, "low"), _column(frame, "close")
    n = len(frame)

    from indicators import _wilder_atr
    frame["SP_atr"] = _wilder_atr(high, low, close, preset.protective_atr_period)

    if preset.id == "golden_cross":
        fast = _moving_average(close, params["ma"], int(params["fast"]))
        slow = _moving_average(close, params["ma"], int(params["slow"]))
        diff = (fast - slow).to_numpy(dtype=float)
        sign = np.sign(diff)
        sign[sign == 0] = np.nan
        sign = pd.Series(sign, index=frame.index).ffill().to_numpy(dtype=float)
        prev = np.concatenate([[np.nan], sign[:-1]])
        bull_cross = (sign == 1.0) & (prev == -1.0)
        exit_cond = sign == -1.0
        bear_cross = (sign == -1.0) & (prev == 1.0)
        frame["SP_fast"], frame["SP_slow"] = fast, slow
        entry = bull_cross
        exit_event = bear_cross
        finite = np.isfinite(fast.to_numpy(dtype=float)) & np.isfinite(slow.to_numpy(dtype=float))
    else:
        line, direction = _supertrend(high, low, close, int(params["atr_period"]), float(params["multiplier"]))
        ema = _moving_average(close, "ema", int(params["ema"]))
        d = direction.to_numpy(dtype=float)
        prev_d = np.concatenate([[np.nan], d[:-1]])
        c = close.to_numpy(dtype=float)
        e = ema.to_numpy(dtype=float)
        with np.errstate(invalid="ignore"):
            entry = (d == 1.0) & (prev_d == -1.0) & (c > e)
            exit_cond = (d == -1.0) | (c < e)
        prev_exit = np.concatenate([[False], exit_cond[:-1]])
        exit_event = exit_cond & ~prev_exit
        frame["SP_st"], frame["SP_st_dir"], frame["SP_ema"] = line, direction, ema
        finite = np.isfinite(d) & np.isfinite(e)

    required = warmup_bars(preset.id, variant_id)
    position = np.arange(n)
    atr_ok = np.isfinite(frame["SP_atr"].to_numpy(dtype=float))
    prev_finite = np.concatenate([[False], finite[:-1]])
    ready = (position >= required - 1) & finite & prev_finite & atr_ok
    entry = np.asarray(entry, dtype=bool) & ready
    exit_cond = np.asarray(exit_cond, dtype=bool) & ready
    exit_event = np.asarray(exit_event, dtype=bool) & ready
    state, since = _replay_state(ready, entry, exit_cond)

    frame["SP_ready"] = ready
    frame["SP_entry"] = entry
    frame["SP_exit_cond"] = exit_cond
    frame["SP_exit_event"] = exit_event
    frame["SP_state"] = state
    frame["SP_bars_since_event"] = since
    return frame


# ---------------------------------------------------------------------------
# Evaluation (position-aware for Bots/Backtest, state-based for Screener)
# ---------------------------------------------------------------------------

def _iso(ts: Any) -> str:
    try:
        return pd.Timestamp(ts).isoformat()
    except Exception:
        return str(ts)


def _blank_result(preset_id: Any, variant_id: Any, status: str, reason: str, **extra: Any) -> Dict[str, Any]:
    result = {
        "preset_id": str(preset_id or ""),
        "variant_id": str(variant_id or ""),
        "label": "",
        "catalog_version": CATALOG_VERSION,
        "status": status,
        "action": "HOLD",
        "alert_direction": "NEUTRAL",
        "entry_event": False,
        "exit_event": False,
        "exit_condition": False,
        "state": "UNKNOWN",
        "signal_bar_ts": None,
        "signal_age_bars": None,
        "signal_key": None,
        "protective_stop_distance": None,
        "stop_atr_mult": None,
        "atr": None,
        "values": {},
        "warmup": {"required": 0, "available": 0},
        "reason": reason,
    }
    result.update(extra)
    return result


def preset_action(has_position: bool, entry_event: bool, exit_condition: bool) -> str:
    """The one decision rule shared by Backtest, Bots and Screener.

    Long-only: SELL only closes an open long (state-based, so it survives
    missed bars); BUY only opens on a fresh entry event while flat.
    """
    if has_position and exit_condition:
        return "SELL"
    if (not has_position) and entry_event:
        return "BUY"
    return "HOLD"


def evaluate_strategy_preset(
    frame: Optional[pd.DataFrame],
    preset_id: Any,
    variant_id: Any = None,
    *,
    timeframe: str = "1Day",
    has_position: bool = False,
    candle_ok: bool = True,
    data_quality: str = "OK",
) -> Dict[str, Any]:
    """Evaluate the newest bar of a ``compute_strategy_frame`` result.

    Anything that is not ready, is degraded, or is not a closed candle yields
    HOLD/NEUTRAL with an explicit reason; it is never a neutral BUY or SELL.
    """
    preset = get_preset(preset_id)
    if preset is None:
        return _blank_result(preset_id, variant_id, STATUS_INVALID, "Unknown strategy preset.")
    variant = get_variant(preset, variant_id)
    if preset.kind == allocation_strategy.KIND:
        return _blank_result(preset.id, variant.id, STATUS_INVALID,
                             f"{preset.label} is an allocation strategy and has no BUY/SELL signal.", label=preset.label)
    if preset.kind == session_schedule.KIND:
        return _blank_result(preset.id, variant.id, STATUS_INVALID,
                             f"{preset.label} trades on a clock schedule and has no indicator signal.", label=preset.label)
    if str(timeframe) not in preset.allowed_timeframes:
        return _blank_result(
            preset.id, variant.id, STATUS_INVALID,
            f"{preset.label} runs on {', '.join(preset.allowed_timeframes)} (got {timeframe}).",
            label=preset.label,
        )
    required = warmup_bars(preset.id, variant.id)
    available = 0 if frame is None else int(len(frame))
    base = dict(
        label=preset.label,
        stop_atr_mult=preset.protective_stop_atr_mult,
        warmup={"required": required, "available": available},
    )
    if frame is None or available == 0:
        return _blank_result(preset.id, variant.id, STATUS_WAITING, "No completed bars available.", **base)
    if str(data_quality).upper() == "DEGRADED":
        return _blank_result(preset.id, variant.id, STATUS_DEGRADED,
                             "Market data is degraded (unadjusted split or fallback feed); no new signals.", **base)
    if not candle_ok:
        return _blank_result(preset.id, variant.id, STATUS_WAITING, "Latest candle is not closed yet.", **base)

    last = frame.iloc[-1]
    if not bool(last.get("SP_ready", False)):
        return _blank_result(
            preset.id, variant.id, STATUS_WAITING,
            f"Waiting for data: {available}/{required} completed bars.", **base,
        )

    atr = float(last["SP_atr"])
    entry_event = bool(last["SP_entry"])
    exit_condition = bool(last["SP_exit_cond"])
    exit_event = bool(last["SP_exit_event"])
    state = str(last["SP_state"])
    bar_ts = frame.index[-1]
    bar_iso = _iso(bar_ts)

    action = preset_action(has_position, entry_event, exit_condition)
    direction = {"LONG": "BULLISH", "FLAT": "BEARISH"}.get(state, "NEUTRAL")

    since = last.get("SP_bars_since_event")
    age = None if since is None or not np.isfinite(float(since)) else int(since)
    if action == "BUY":
        reason = f"{preset.label}: entry signal on the completed bar {bar_iso}."
    elif action == "SELL":
        reason = f"{preset.label}: exit condition met on the completed bar {bar_iso}."
    elif state == "LONG":
        reason = f"{preset.label}: long regime for {age} bars; no new entry."
    elif state == "FLAT":
        reason = f"{preset.label}: flat since {age} bars; waiting for a fresh entry signal."
    else:
        reason = (f"{preset.label}: no entry or exit event since the data window began "
                  f"(state unknown until the next signal).")

    values = {"close": float(_column(frame.iloc[[-1]], "close").iloc[0])}
    for key in ("SP_fast", "SP_slow", "SP_st", "SP_st_dir", "SP_ema"):
        if key in frame.columns and np.isfinite(float(last[key])):
            values[key.replace("SP_", "")] = float(last[key])

    result = _blank_result(preset.id, variant.id, STATUS_READY, reason, **base)
    result.update({
        "action": action,
        "alert_direction": direction,
        "entry_event": entry_event,
        "exit_event": exit_event,
        "exit_condition": exit_condition,
        "state": state,
        "signal_bar_ts": bar_iso if action in ("BUY", "SELL") else None,
        "signal_age_bars": age,
        "signal_key": f"{preset.id}:{variant.id}:{timeframe}:{bar_iso}:{action}" if action != "HOLD" else None,
        "atr": atr,
        "protective_stop_distance": preset.protective_stop_atr_mult * atr,
        "values": values,
    })
    return result


# ---------------------------------------------------------------------------
# Execution window (shared by Backtest and live Bots)
# ---------------------------------------------------------------------------

def next_execution_window(
    signal_bar_open: Any,
    timeframe: str,
    *,
    blackout_minutes: int = 0,
    is_crypto: bool = False,
) -> Dict[str, Any]:
    """When a signal decided on a completed bar may be executed.

    Daily signals execute at the next regular session open (plus the opening
    blackout, if enabled). Intraday signals execute as soon as the bar is
    complete when that is inside a regular session, otherwise at the next
    session open. Orders are never sent overnight or in extended hours.
    """
    open_utc = pd.Timestamp(signal_bar_open)
    open_utc = open_utc.tz_localize("America/New_York") if open_utc.tzinfo is None else open_utc
    open_utc = open_utc.tz_convert("UTC")
    completes = bar_completion_time(open_utc, timeframe, is_crypto)
    if is_crypto:
        return {"window_start": completes.to_pydatetime(), "kind": "next_bar_open", "completes_at": completes.to_pydatetime()}

    completes_et = completes.tz_convert(nyse_calendar.EASTERN).to_pydatetime()
    blackout = timedelta(minutes=max(0, int(blackout_minutes or 0)))
    bounds = nyse_calendar.session_bounds(completes_et.date())
    kind = "next_session_open"
    if str(timeframe) != "1Day" and bounds is not None and bounds[0] <= completes_et < bounds[1]:
        start, kind = completes_et, "next_bar_open"
    else:
        start = nyse_calendar.next_session_open(completes_et)
    start = start + blackout if kind == "next_session_open" else start
    session = nyse_calendar.session_bounds(start.astimezone(nyse_calendar.EASTERN).date())
    if session is not None and start >= session[1]:
        start = nyse_calendar.next_session_open(session[1])
    return {"window_start": start, "kind": kind, "completes_at": completes_et}


# ---------------------------------------------------------------------------
# Position sizing (shared by Backtest and live Bots)
# ---------------------------------------------------------------------------

def preset_position_size(
    *,
    equity: float,
    price: float,
    atr: float,
    base_amount: float,
    risk_fraction: float,
    max_position_fraction: float,
    stop_atr_mult: float = 3.0,
    buying_power: Optional[float] = None,
    fractional: bool = True,
) -> Dict[str, Any]:
    """Smallest of the fixed/compounded amount, risk-based size, max position
    and buying power. Reports which limit decided the size."""
    equity, price, atr = float(equity or 0), float(price or 0), float(atr or 0)
    if equity <= 0 or price <= 0:
        return {"qty": 0.0, "notional": 0.0, "limit": "invalid_input"}
    limits: Dict[str, float] = {}
    if base_amount and base_amount > 0:
        limits["amount"] = float(base_amount) / price
    if risk_fraction and risk_fraction > 0 and atr > 0 and stop_atr_mult > 0:
        limits["risk"] = equity * float(risk_fraction) / (float(stop_atr_mult) * atr)
    if max_position_fraction and max_position_fraction > 0:
        limits["max_position"] = equity * float(max_position_fraction) / price
    if buying_power is not None:
        limits["buying_power"] = max(0.0, float(buying_power)) / price
    if not limits:
        return {"qty": 0.0, "notional": 0.0, "limit": "no_limits"}
    limit, qty = min(limits.items(), key=lambda item: item[1])
    if not fractional:
        qty = math.floor(qty)
    qty = max(0.0, qty)
    if not fractional and qty < 1:
        limit = f"{limit}_below_one_share"
    return {"qty": float(qty), "notional": float(qty * price), "limit": limit}
