"""Result analytics for backtests.

Pure functions (no I/O, no broker or storage access) so every backtest path
(indicator/structure modes, strategy presets, allocation strategies) reports
the same numbers the same way. The Backtester feeds plain data in and merges
the returned dictionaries into its result.

Conventions
- Equity points are indexed by the exchange-local bar time (naive), the same
  clock the Backtester uses for its candles, so dates group by trading day.
- Returns are in percent. Ratios are rounded to 2 decimals.
- Risk-free rate is 0 for Sharpe and Sortino.
- Nothing here adds fees, slippage or any assumption the simulation did not make.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

METRICS_VERSION = 2
STOCK_PERIODS_PER_YEAR = 252
CRYPTO_PERIODS_PER_YEAR = 365
MIN_DAILY_RETURNS_FOR_RATIOS = 5
PROJECTION_THRESHOLD_DAYS = 365
_EPS = 1e-12


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 2) -> Optional[float]:
    number = _finite(value)
    return None if number is None else round(number, digits)


# ── Equity series ────────────────────────────────────────────────────────────
def equity_series(points: Iterable[Mapping[str, Any]]) -> pd.Series:
    """Sorted, de-duplicated equity indexed by bar time (later duplicate wins)."""
    times: List[Any] = []
    values: List[float] = []
    for point in points or []:
        value = _finite(point.get("equity"))
        stamp = point.get("time")
        if value is None or stamp is None:
            continue
        times.append(pd.Timestamp(stamp))
        values.append(value)
    if not values:
        return pd.Series(dtype=float)
    series = pd.Series(values, index=pd.DatetimeIndex(times), dtype=float)
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series


def basis_days(index: Sequence[Any]) -> float:
    """Calendar days covered by the tested bars (at least one day)."""
    if index is None or len(index) < 2:
        return 1.0
    span = (pd.Timestamp(index[-1]) - pd.Timestamp(index[0])).total_seconds() / 86400.0
    return max(1.0, span)


# ── Annualised return ────────────────────────────────────────────────────────
def annualized_return(roi_pct: Any, days: float, allocation_mode: str) -> Dict[str, Any]:
    """Annualise a period return the way the sizing method actually grows money.

    Fixed sizing does not reinvest profits, so the yearly figure is a straight
    line (simple). Compound sizing reinvests, so the figure is compounded.
    A window shorter than a year is a projection, flagged for the UI.
    """
    roi = _finite(roi_pct) or 0.0
    days = max(float(days or 1.0), 1.0)
    compound = str(allocation_mode or "fixed").lower() == "compound"
    value: Optional[float]
    if compound:
        growth = 1.0 + roi / 100.0
        if growth <= 0:
            value = -100.0
        else:
            try:
                value = (growth ** (365.0 / days) - 1.0) * 100.0
            except OverflowError:
                value = None
    else:
        value = roi * 365.0 / days
    value = _finite(value)
    return {
        "annualised_roi_pct": None if value is None else round(value, 2),
        "annualised_roi_method": "compounded" if compound else "simple",
        "annualised_basis_days": round(days, 1),
        "annualised_is_projection": days < PROJECTION_THRESHOLD_DAYS,
    }


# ── Sharpe / Sortino from the equity curve ───────────────────────────────────
def daily_returns(equity: pd.Series, initial_capital: float) -> np.ndarray:
    """Day-over-day account returns (last equity of each trading day)."""
    if equity is None or equity.empty:
        return np.array([], dtype=float)
    daily = equity.groupby(equity.index.normalize()).last()
    start = _finite(initial_capital)
    values = daily.to_numpy(dtype=float)
    if start is not None and start > 0:
        values = np.concatenate([[start], values])
    if len(values) < 2:
        return np.array([], dtype=float)
    previous = values[:-1]
    current = values[1:]
    valid = previous > 0
    return (current[valid] / previous[valid]) - 1.0


def risk_ratios(equity: pd.Series, initial_capital: float, is_crypto: bool) -> Dict[str, Any]:
    periods = CRYPTO_PERIODS_PER_YEAR if is_crypto else STOCK_PERIODS_PER_YEAR
    returns = daily_returns(equity, initial_capital)
    out: Dict[str, Any] = {
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "risk_ratio_basis": {
            "method": "daily_equity_returns",
            "daily_returns": int(len(returns)),
            "periods_per_year": periods,
            "risk_free_rate": 0.0,
        },
    }
    if len(returns) < MIN_DAILY_RETURNS_FOR_RATIOS:
        out["risk_ratio_basis"]["note"] = (
            f"Needs at least {MIN_DAILY_RETURNS_FOR_RATIOS} trading days; this test has {len(returns)}."
        )
        return out
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    if std > _EPS:
        out["sharpe_ratio"] = _round(mean / std * math.sqrt(periods))
    downside = float(np.sqrt(np.mean(np.minimum(returns, 0.0) ** 2)))
    if downside > _EPS:
        out["sortino_ratio"] = _round(mean / downside * math.sqrt(periods))
    return out


# ── Drawdown ─────────────────────────────────────────────────────────────────
def max_drawdown_pct(values: Sequence[float]) -> float:
    """Largest peak-to-trough fall, as a positive percent."""
    arr = np.asarray([v for v in values if _finite(v) is not None], dtype=float)
    if arr.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(arr)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peaks > 0, (peaks - arr) / peaks, 0.0)
    return float(np.max(dd) * 100.0)


def drawdown_profile(equity: pd.Series, fmt: Callable[[Any], str]) -> Dict[str, Any]:
    """Worst drawdown (peak, trough, recovery) and the longest time under water."""
    empty = {"worst_drawdown": None, "longest_underwater": None}
    if equity is None or len(equity) < 2:
        return empty
    values = equity.to_numpy(dtype=float)
    times = list(equity.index)
    peaks = np.maximum.accumulate(values)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peaks > 0, values / peaks - 1.0, 0.0)

    def days_between(a: int, b: int) -> float:
        return round((pd.Timestamp(times[b]) - pd.Timestamp(times[a])).total_seconds() / 86400.0, 1)

    trough = int(np.argmin(dd))
    worst = None
    if dd[trough] < -_EPS:
        peak_value = peaks[trough]
        peak = int(np.flatnonzero(values[: trough + 1] >= peak_value - _EPS)[-1])
        later = np.flatnonzero(values[trough + 1:] >= peak_value - _EPS)
        recovery = int(trough + 1 + later[0]) if later.size else None
        worst = {
            "depth_pct": round(float(-dd[trough] * 100.0), 2),
            "peak_time": fmt(times[peak]),
            "trough_time": fmt(times[trough]),
            "recovery_time": fmt(times[recovery]) if recovery is not None else None,
            "days_to_trough": days_between(peak, trough),
            "days_to_recover": days_between(trough, recovery) if recovery is not None else None,
            "recovered": recovery is not None,
        }

    longest = None
    start: Optional[int] = None
    for i in range(1, len(values)):
        underwater = dd[i] < -_EPS
        if underwater and start is None:
            start = i - 1  # the peak it fell from
        elif not underwater and start is not None:
            span = days_between(start, i)
            if longest is None or span > longest["days"]:
                longest = {"start_time": fmt(times[start]), "end_time": fmt(times[i]), "days": span, "ongoing": False}
            start = None
    if start is not None:
        span = days_between(start, len(values) - 1)
        if longest is None or span > longest["days"]:
            longest = {"start_time": fmt(times[start]), "end_time": None, "days": span, "ongoing": True}
    return {"worst_drawdown": worst, "longest_underwater": longest}


# ── Buy & hold ───────────────────────────────────────────────────────────────
def buy_hold_curve(closes: pd.Series, initial_capital: float) -> pd.Series:
    """Equity of holding from the first tested close (same basis as buy_hold_equity)."""
    if closes is None or closes.empty:
        return pd.Series(dtype=float)
    clean = pd.to_numeric(closes, errors="coerce").dropna()
    clean = clean[clean > 0]
    if clean.empty:
        return pd.Series(dtype=float)
    return clean / float(clean.iloc[0]) * float(initial_capital)


def _return_per_drawdown(roi_pct: float, dd_pct: float) -> Optional[float]:
    if dd_pct > _EPS:
        return round(roi_pct / dd_pct, 2)
    return None


def _edge_score(roi_pct: float, dd_pct: float) -> float:
    if dd_pct > _EPS:
        return roi_pct / dd_pct
    if roi_pct > 0:
        return math.inf
    return 0.0 if roi_pct == 0 else -math.inf


def edge_vs_buy_hold(strategy_roi: Any, strategy_dd: Any, bh_roi: Any, bh_dd: Any) -> Dict[str, Any]:
    """Compare return ÷ max drawdown on the same window and starting capital."""
    s_roi, s_dd = _finite(strategy_roi) or 0.0, abs(_finite(strategy_dd) or 0.0)
    b_roi, b_dd = _finite(bh_roi) or 0.0, abs(_finite(bh_dd) or 0.0)
    s_score, b_score = _edge_score(s_roi, s_dd), _edge_score(b_roi, b_dd)
    if s_score > b_score:
        verdict = "beats"
    elif s_score < b_score:
        verdict = "lags"
    else:
        verdict = "tie"
    return {
        "strategy_return_pct": round(s_roi, 2),
        "strategy_max_drawdown_pct": round(s_dd, 2),
        "strategy_return_per_drawdown": _return_per_drawdown(s_roi, s_dd),
        "buy_hold_return_pct": round(b_roi, 2),
        "buy_hold_max_drawdown_pct": round(b_dd, 2),
        "buy_hold_return_per_drawdown": _return_per_drawdown(b_roi, b_dd),
        "verdict": verdict,
        "rule": "Return ÷ max drawdown on the same window. A strategy that does not beat buy & hold on this should be rejected, not tuned.",
    }


# ── Exposure ────────────────────────────────────────────────────────────────
def exposure_pct(bar_unix: Sequence[int], intervals: Iterable[Sequence[Optional[int]]]) -> Optional[float]:
    """Share of tested bars with a position open (union of [entry, exit) intervals)."""
    bars = np.asarray(list(bar_unix), dtype=np.int64)
    if bars.size == 0:
        return None
    order = np.argsort(bars, kind="stable")
    bars = bars[order]
    held = np.zeros(bars.size, dtype=bool)
    for entry, exit_ in intervals:
        if entry is None:
            continue
        lo = int(np.searchsorted(bars, int(entry), side="left"))
        hi = int(np.searchsorted(bars, int(exit_), side="left")) if exit_ is not None else bars.size
        if hi > lo:
            held[lo:hi] = True
    return round(float(held.mean() * 100.0), 2)


# ── Monthly returns ─────────────────────────────────────────────────────────
def monthly_returns(equity: pd.Series, initial_capital: float) -> List[Dict[str, Any]]:
    if equity is None or equity.empty:
        return []
    month_end = equity.groupby([equity.index.year, equity.index.month]).last()
    previous = _finite(initial_capital) or float(equity.iloc[0])
    rows: List[Dict[str, Any]] = []
    for (year, month), value in month_end.items():
        ret = (value / previous - 1.0) * 100.0 if previous > 0 else None
        rows.append({"year": int(year), "month": int(month), "return_pct": _round(ret)})
        previous = float(value)
    return rows


# ── First half vs second half ───────────────────────────────────────────────
def period_split(
    equity: pd.Series,
    equity_unix: Sequence[int],
    initial_capital: float,
    trades: Iterable[Mapping[str, Any]],
    trade_exit_unix: Callable[[Mapping[str, Any]], Optional[int]],
    fmt: Callable[[Any], str],
) -> List[Dict[str, Any]]:
    """Split the tested window in two equal halves by time and report each."""
    if equity is None or len(equity) < 2 or len(equity_unix) != len(equity):
        return []
    times = equity.index
    midpoint = times[0] + (times[-1] - times[0]) / 2
    mid_i = int(np.searchsorted(times.values, np.datetime64(midpoint), side="right") - 1)
    mid_i = min(max(mid_i, 0), len(equity) - 2)
    mid_unix = int(equity_unix[mid_i])
    start_value = _finite(initial_capital) or float(equity.iloc[0])
    mid_value = float(equity.iloc[mid_i])
    end_value = float(equity.iloc[-1])

    halves = [
        {"label": "First half", "start_time": fmt(times[0]), "end_time": fmt(times[mid_i]),
         "start_equity": start_value, "end_equity": mid_value, "trades": 0, "wins": 0, "pl": 0.0},
        {"label": "Second half", "start_time": fmt(times[mid_i]), "end_time": fmt(times[-1]),
         "start_equity": mid_value, "end_equity": end_value, "trades": 0, "wins": 0, "pl": 0.0},
    ]
    for trade in trades or []:
        exit_unix = trade_exit_unix(trade)
        pl = _finite(trade.get("pl"))
        if exit_unix is None or pl is None:
            continue
        half = halves[0] if exit_unix <= mid_unix else halves[1]
        half["trades"] += 1
        half["wins"] += 1 if pl > 0 else 0
        half["pl"] += pl
    out = []
    for half in halves:
        start, end = half.pop("start_equity"), half.pop("end_equity")
        half["return_pct"] = _round((end / start - 1.0) * 100.0) if start > 0 else None
        half["win_rate_pct"] = _round(half["wins"] / half["trades"] * 100.0, 1) if half["trades"] else None
        half["pl"] = round(half["pl"], 2)
        out.append(half)
    return out


# ── Trade classification ────────────────────────────────────────────────────
EXIT_CATEGORIES = ("stop", "target", "signal", "grid", "end", "other")


def classify_exit(reason: Any, side: str = "long") -> str:
    """Bucket a free-text exit reason into stop / target / signal / grid / end / other."""
    text = str(reason or "").lower()
    if not text:
        return "other"
    if "end of backtest" in text:
        return "end"
    if "grid" in text:
        return "grid"
    if "stop" in text and any(token in text for token in ("loss", "hit", "trail", "protective")):
        return "stop"
    if "take profit" in text or "target" in text:
        return "target"
    short = str(side or "long").lower() == "short"
    if "prior high breakout" in text:
        return "stop" if short else "target"
    if "prior low breakdown" in text:
        return "target" if short else "stop"
    if any(token in text for token in ("exit", "signal", "opposite", "bearish", "bullish", "sell", "trend rider")):
        return "signal"
    return "other"


def hold_hours(entry_iso: Any, exit_iso: Any) -> Optional[float]:
    try:
        entry = pd.Timestamp(entry_iso)
        exit_ = pd.Timestamp(exit_iso)
    except (TypeError, ValueError):
        return None
    if pd.isna(entry) or pd.isna(exit_):
        return None
    return round((exit_ - entry).total_seconds() / 3600.0, 2)


# ── Run record ──────────────────────────────────────────────────────────────
def jsonable(value: Any) -> Any:
    """Convert numpy/pandas values so a run record serialises cleanly."""
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return _finite(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


_RUN_SETTING_FIELDS = (
    "ticker", "timeframe", "start_date", "end_date", "initial_capital", "allocation_mode", "trade_amount",
    "market_session", "ext_hours", "strategy_preset", "strategy_preset_variant",
    "buy_mode", "sell_mode", "threshold", "sell_threshold", "enabled_indicators", "indicator_settings",
    "custom_buy_price", "custom_stop_loss_price", "custom_take_profit_price",
    "entry_atr_multiplier", "entry_atr_style", "entry_pct_move", "entry_pct_style",
    "swing_entry_mode", "swing_entry_lookback_days", "swing_exit_lookback_days",
    "atr_stop_multiplier", "atr_take_profit_multiplier", "atr_trail_multiplier", "enable_atr_trail",
    "regular_stop_loss_pct", "regular_take_profit_pct", "regular_trailing_pct", "enable_pct_trail",
    "enable_cooldown", "cooldown_minutes", "enable_signal_reset", "enable_smart_reentry", "smart_reentry_type",
    "post_stop_stabilization_bars", "max_consecutive_losses", "max_daily_loss_pct",
    "enable_liquidity_filter", "min_relative_volume", "enable_risk", "risk_per_trade", "max_pos_pct",
    "trade_direction_mode",
)


def run_settings(backtester: Any, fill_model: str = "next_bar_open") -> Dict[str, Any]:
    """The effective settings this run used, after server-side normalisation."""
    record: Dict[str, Any] = {}
    for field in _RUN_SETTING_FIELDS:
        if hasattr(backtester, field):
            record[field] = getattr(backtester, field)
    grid = getattr(backtester, "continuous_grid_settings", None) or {}
    if getattr(backtester, "adaptive_continuous_grid", False) or getattr(backtester, "adaptive_atr_grid", False):
        record["continuous_grid_settings"] = {
            k: v for k, v in grid.items()
            if not str(k).startswith(("_", "entry_grid_", "exit_grid_")) or str(k).endswith(("_mode", "_enabled"))
        }
    record["fill_model"] = fill_model
    record["metrics_version"] = METRICS_VERSION
    return jsonable(record)
