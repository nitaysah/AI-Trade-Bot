"""Pure stock-split / reverse-split helpers (no I/O).

Backtests, bots and the screener must all reason about prices on the same
split-adjusted basis, otherwise a split looks like a crash (or a spike) and
produces false moving-average crosses and false stops.

Conventions
* ``SplitEvent.ratio`` is ``new_shares / old_shares``. A 4-for-1 split is 4.0
  and a 1-for-10 reverse split is 0.1.
* Prices before the ex-date are divided by the ratio; volume is multiplied.
* Prices are *split-adjusted only* (dividends are not added back), so
  historical prices match what was actually tradable.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

import numpy as np
import pandas as pd

# A jump bigger than this between two bars is suspicious even without a
# known corporate action.
SUSPECT_JUMP_LOW = 0.55
SUSPECT_JUMP_HIGH = 1.8
# Tolerance when matching a price jump against a split ratio.
MATCH_TOLERANCE_LOG = 0.15
# Splits smaller than ~10% cannot be told apart from normal price moves.
MIN_DETECTABLE_LOG_RATIO = 0.10
COMMON_RATIOS = (2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 15.0, 20.0, 1.5,
                 0.5, 1 / 3, 0.25, 0.2, 0.125, 0.1, 1 / 15, 0.05, 0.04, 0.02, 0.01)

# Saved bot / screener state that holds absolute prices (divide by the ratio).
PRICE_KEYS = (
    "custom_buy_price",
    "custom_stop_loss_price",
    "custom_take_profit_price",
    "custom_underlying_stop_loss_price",
    "custom_underlying_take_profit_price",
    "entry_grid_anchor_price",
    "entry_grid_last_fill_price",
    "entry_grid_high_water_price",
    "entry_grid_pending_fill_price",
    "exit_grid_anchor_price",
    "exit_grid_last_fill_price",
    "exit_grid_high_water_price",
    "exit_grid_runner_high_water_price",
    "exit_grid_pending_fill_price",
    "exit_grid_pending_entry_price",
)
# Price-denominated distances (ATR) saved with the grid state.
ATR_KEYS = (
    "entry_grid_anchor_atr",
    "exit_grid_anchor_atr",
    "entry_grid_pending_atr",
    "exit_grid_pending_atr",
)
# Saved share quantities (multiply by the ratio).
QUANTITY_KEYS = (
    "exit_grid_anchor_qty",
    "entry_grid_pending_before_qty",
    "exit_grid_pending_before_qty",
)
# Order ids that mean a broker order was in flight when the split happened.
PENDING_ORDER_KEYS = ("entry_grid_pending_order_id", "exit_grid_pending_order_id")

STATUS_RAW = "raw"
STATUS_ADJUSTED = "adjusted"
STATUS_INCONCLUSIVE = "inconclusive"
STATUS_OUTSIDE = "outside_window"


@dataclass(frozen=True)
class SplitEvent:
    symbol: str
    ex_date: date
    ratio: float

    @property
    def key(self) -> str:
        return f"{self.symbol.upper()}:{self.ex_date.isoformat()}:{self.ratio:.6f}"

    @property
    def label(self) -> str:
        if self.ratio >= 1:
            return f"{self.ratio:g}-for-1 split"
        return f"1-for-{1 / self.ratio:g} reverse split"


def make_split_event(symbol: str, ex_date: Any, ratio: Any) -> Optional[SplitEvent]:
    """Build an event from loosely typed provider data, or None if unusable."""
    try:
        ratio = float(ratio)
        day = pd.Timestamp(ex_date).date()
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ratio) or ratio <= 0 or abs(ratio - 1.0) < 1e-9:
        return None
    return SplitEvent(str(symbol or "").upper(), day, ratio)


def _bar_dates(index: pd.DatetimeIndex) -> np.ndarray:
    """Calendar date of each bar in exchange-local (Eastern) time."""
    index = pd.DatetimeIndex(index)
    if index.tz is not None:
        index = index.tz_convert("America/New_York")
    return np.asarray(index.date)


def _price_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if str(c).lower() in {"open", "high", "low", "close", "adj_close", "adj close"}]


def _volume_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if str(c).lower() == "volume"]


def _close_column(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        if str(c).lower() == "close":
            return c
    return None


def apply_split_adjustment(df: pd.DataFrame, events: Iterable[SplitEvent]) -> pd.DataFrame:
    """Return a split-adjusted copy. Events already applied are skipped
    (tracked in ``df.attrs['split_events_applied']``), so calling it twice is a
    no-op."""
    out = df.copy()
    out.attrs = dict(getattr(df, "attrs", {}) or {})
    applied = set(out.attrs.get("split_events_applied", ()))
    if out.empty:
        return out
    dates = _bar_dates(out.index)
    price_cols, volume_cols = _price_columns(out), _volume_columns(out)
    for col in price_cols + volume_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    for event in sorted(events, key=lambda e: e.ex_date):
        if event.key in applied:
            continue
        before = dates < event.ex_date
        if before.any():
            out.loc[before, price_cols] = out.loc[before, price_cols] / event.ratio
            if volume_cols:
                out.loc[before, volume_cols] = out.loc[before, volume_cols] * event.ratio
        applied.add(event.key)
    out.attrs["split_events_applied"] = sorted(applied)
    return out


def classify_split(df: pd.DataFrame, event: SplitEvent) -> Dict[str, Any]:
    """Decide whether ``df`` is still raw across ``event`` or already adjusted.

    Compares the close-to-close ratio across the ex-date with ``1/ratio``
    (raw) and ``1`` (adjusted).
    """
    close_col = _close_column(df)
    if df is None or df.empty or close_col is None:
        return {"status": STATUS_OUTSIDE, "event": event.key}
    dates = _bar_dates(df.index)
    before_mask, after_mask = dates < event.ex_date, dates >= event.ex_date
    if not before_mask.any() or not after_mask.any():
        return {"status": STATUS_OUTSIDE, "event": event.key}
    closes = pd.to_numeric(df[close_col], errors="coerce").to_numpy(dtype=float)
    before_close = closes[before_mask][-1]
    after_close = closes[after_mask][0]
    if not (np.isfinite(before_close) and np.isfinite(after_close)) or before_close <= 0 or after_close <= 0:
        return {"status": STATUS_INCONCLUSIVE, "event": event.key, "reason": "non-finite closes"}
    if abs(math.log(event.ratio)) < MIN_DETECTABLE_LOG_RATIO:
        return {"status": STATUS_INCONCLUSIVE, "event": event.key, "reason": "ratio too small to detect"}
    jump = after_close / before_close
    d_raw = abs(math.log(jump * event.ratio))
    d_adj = abs(math.log(jump))
    if d_raw < d_adj and d_raw < MATCH_TOLERANCE_LOG:
        status = STATUS_RAW
    elif d_adj < d_raw and d_adj < MATCH_TOLERANCE_LOG:
        status = STATUS_ADJUSTED
    else:
        status = STATUS_INCONCLUSIVE
    return {"status": status, "event": event.key, "jump": jump}


def reconcile_split_adjustment(
    df: pd.DataFrame, events: Iterable[SplitEvent]
) -> Tuple[pd.DataFrame, str, List[Dict[str, Any]]]:
    """Make ``df`` split-adjusted regardless of how the provider delivered it.

    Returns ``(frame, quality, notes)``. ``quality`` is ``"OK"`` or
    ``"DEGRADED"`` (an event could not be classified).
    """
    notes: List[Dict[str, Any]] = []
    quality = "OK"
    out = df
    for event in sorted(events, key=lambda e: e.ex_date):
        verdict = classify_split(out, event)
        notes.append({**verdict, "label": event.label, "ex_date": event.ex_date.isoformat()})
        if verdict["status"] == STATUS_RAW:
            out = apply_split_adjustment(out, [event])
        elif verdict["status"] == STATUS_INCONCLUSIVE:
            quality = "DEGRADED"
    return out, quality, notes


def suspect_split_jumps(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Bar-to-bar closes that look like an unrecorded split.

    Used when corporate-action data is unavailable: a jump that is both large
    and close to a common split ratio marks the data DEGRADED.
    """
    close_col = _close_column(df) if df is not None else None
    if close_col is None or len(df) < 2:
        return []
    closes = pd.to_numeric(df[close_col], errors="coerce").to_numpy(dtype=float)
    out = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if not (np.isfinite(prev) and np.isfinite(cur)) or prev <= 0 or cur <= 0:
            continue
        ratio = cur / prev
        if SUSPECT_JUMP_LOW < ratio < SUSPECT_JUMP_HIGH:
            continue
        near = min(abs(math.log(ratio * r)) for r in COMMON_RATIOS)
        if near < MATCH_TOLERANCE_LOG:
            out.append({"timestamp": df.index[i], "jump": ratio})
    return out


def rescale_price_levels(settings: Mapping[str, Any], event: SplitEvent) -> Tuple[Dict[str, Any], Dict[str, Tuple[Any, Any]]]:
    """Rescale saved absolute prices / ATR distances / quantities.

    Returns ``(new_settings, changes)`` where ``changes`` maps key to
    ``(before, after)``. Percent and ATR-multiple settings are scale-free and
    are left alone.
    """
    out = copy.deepcopy(dict(settings or {}))
    changes: Dict[str, Tuple[Any, Any]] = {}

    def convert(key: str, factor: float) -> None:
        value = out.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if not math.isfinite(float(value)) or float(value) == 0.0:
            return
        new_value = float(value) * factor
        out[key] = new_value
        changes[key] = (value, new_value)

    for key in PRICE_KEYS + ATR_KEYS:
        convert(key, 1.0 / event.ratio)
    for key in QUANTITY_KEYS:
        convert(key, event.ratio)

    stop = out.get("strategy_preset_stop")
    if isinstance(stop, dict) and isinstance(stop.get("entry_atr"), (int, float)):
        before = stop["entry_atr"]
        stop["entry_atr"] = float(before) / event.ratio
        changes["strategy_preset_stop.entry_atr"] = (before, stop["entry_atr"])
    return out, changes


def rescale_quantity(quantity: float, event: SplitEvent) -> float:
    return float(quantity) * event.ratio


def has_pending_order(settings: Mapping[str, Any]) -> bool:
    return any((settings or {}).get(key) for key in PENDING_ORDER_KEYS)


def quantity_matches(expected: float, actual: float, tolerance: float = 1.0) -> bool:
    """True if a broker quantity is within ``tolerance`` shares of expectation
    (a reverse split pays cash in lieu for fractional shares)."""
    return abs(float(expected) - float(actual)) <= tolerance
