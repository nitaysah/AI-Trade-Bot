"""BotBulls5 causal chart-pattern detector.

The detector deliberately uses only completed bars at or before each row.  It
turns subjective flag/pennant/wedge geometry into ATR-normalized, testable
rules and does not emit a directional confluence vote until a candle closes
through the fitted boundary.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from botbulls5_structures import find_structural_patterns, precompute_pivots


BOTBULLS5_COLUMNS = (
    "BB5_Upper",
    "BB5_Lower",
    "BB5_Target",
    "BB5_Invalidation",
    "BB5_Breakout_Level",
    "BB5_Upper_Start",
    "BB5_Lower_Start",
    "BB5_Formation_Start_Time",
    "BB5_Formation_End_Time",
    "BB5_Pole_Start_Time",
    "BB5_Pole_End_Time",
    "BB5_Formation_Bars",
    "BB5_Confidence",
    "BB5_Direction",
    "BB5_Reasons",
    "BB5_Breakout_Bull",
    "BB5_Breakout_Bear",
    "BB5_Signal",
    "BB5_Signal_Confidence",
    "BB5_Pattern",
    "BB5_State",
    "BB5_Signal_Pattern",
)


def _bounded_number(param: Callable, key: str, fallback: float, low: float, high: float) -> float:
    try:
        value = float(param(key, fallback))
    except (TypeError, ValueError):
        value = fallback
    return min(high, max(low, value))


def _fit_line(values: np.ndarray) -> tuple[float, float, float]:
    x = np.arange(len(values), dtype=float)
    slope, intercept = np.polyfit(x, values, 1)
    fitted = intercept + slope * x
    residual = float(np.sqrt(np.mean(np.square(values - fitted))))
    return float(slope), float(intercept), residual


def _finite_median(values: np.ndarray, fallback: float) -> float:
    finite = values[np.isfinite(values)]
    if not finite.size:
        return fallback
    middle = finite.size // 2
    partitioned = np.partition(finite, middle)
    if finite.size % 2:
        return float(partitioned[middle])
    return float((np.max(partitioned[:middle]) + partitioned[middle]) / 2.0)


def _candidate(
    *,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    atr: np.ndarray,
    index: int,
    consolidation_bars: int,
    pole_bars: int,
    breakout: bool,
    pole_atr_multiple: float,
    max_retrace: float,
    max_width: float,
    parallel_tolerance: float,
    min_convergence: float,
    breakout_buffer_atr: float,
    volume_contraction_max: float,
    breakout_volume_multiple: float,
    require_volume: bool,
    allow_pennants: bool,
    allow_wedges: bool,
) -> dict | None:
    consolidation_end = index - 1 if breakout else index
    consolidation_start = consolidation_end - consolidation_bars + 1
    pole_end = consolidation_start - 1
    pole_start = pole_end - pole_bars + 1
    if pole_start < 0 or consolidation_start <= pole_start:
        return None

    pole_base = float(close[pole_start])
    pole_tip = float(close[pole_end])
    direction = 1 if pole_tip > pole_base else -1
    pole_height = direction * (pole_tip - pole_base)
    atr_value = _finite_median(atr[pole_start : consolidation_end + 1], abs(pole_tip) * 0.005)
    if not math.isfinite(atr_value) or atr_value <= 0:
        return None
    pole_strength = pole_height / atr_value
    if pole_strength < pole_atr_multiple:
        return None

    pole_path = np.abs(np.diff(close[pole_start : pole_end + 1])).sum()
    efficiency = pole_height / pole_path if pole_path > 0 else 0.0
    if efficiency < 0.45:
        return None

    segment_high = high[consolidation_start : consolidation_end + 1]
    segment_low = low[consolidation_start : consolidation_end + 1]
    if not (np.isfinite(segment_high).all() and np.isfinite(segment_low).all()):
        return None

    upper_slope, upper_intercept, upper_residual = _fit_line(segment_high)
    lower_slope, lower_intercept, lower_residual = _fit_line(segment_low)
    x_last = consolidation_bars - 1
    upper_start = upper_intercept
    lower_start = lower_intercept
    upper_end = upper_intercept + upper_slope * x_last
    lower_end = lower_intercept + lower_slope * x_last
    start_width = upper_start - lower_start
    end_width = upper_end - lower_end
    mean_width = (start_width + end_width) / 2.0
    if start_width <= 0 or end_width <= 0 or mean_width < atr_value * 0.35:
        return None
    width_ratio = mean_width / pole_height
    if width_ratio > max_width:
        return None

    if direction > 0:
        retrace = max(0.0, pole_tip - float(np.min(segment_low))) / pole_height
    else:
        retrace = max(0.0, float(np.max(segment_high)) - pole_tip) / pole_height
    if retrace > max_retrace:
        return None

    slope_scale = max(pole_height / max(x_last, 1), atr_value * 0.1)
    parallel_error = abs(upper_slope - lower_slope) / slope_scale
    average_slope = (upper_slope + lower_slope) / 2.0
    directional_slope = direction * average_slope / slope_scale
    convergence = (start_width - end_width) / start_width

    pattern = None
    shape_quality = 0.0
    if parallel_error <= parallel_tolerance and -0.70 <= directional_slope <= 0.20:
        pattern = "BULL_FLAG" if direction > 0 else "BEAR_FLAG"
        shape_quality = 1.0 - min(1.0, parallel_error / max(parallel_tolerance, 0.01))
    elif (
        allow_pennants
        and convergence >= min_convergence
        and upper_slope < 0
        and lower_slope > 0
    ):
        pattern = "BULL_PENNANT" if direction > 0 else "BEAR_PENNANT"
        shape_quality = min(1.0, convergence / 0.60)
    elif (
        allow_wedges
        and convergence >= min_convergence
        and direction * average_slope < 0
    ):
        pattern = "BULL_WEDGE_CONTINUATION" if direction > 0 else "BEAR_WEDGE_CONTINUATION"
        shape_quality = min(1.0, convergence / 0.55)
    if pattern is None:
        return None

    projected_x = consolidation_bars if breakout else x_last
    projected_upper = upper_intercept + upper_slope * projected_x
    projected_lower = lower_intercept + lower_slope * projected_x
    buffer_value = breakout_buffer_atr * atr_value
    price_now = float(close[index])
    previous_price = float(close[index - 1])
    previous_upper = upper_intercept + upper_slope * x_last
    previous_lower = lower_intercept + lower_slope * x_last
    broke_direction = (
        price_now > projected_upper + buffer_value
        and previous_price <= previous_upper + buffer_value
        if direction > 0
        else price_now < projected_lower - buffer_value
        and previous_price >= previous_lower - buffer_value
    )
    broke_opposite = (
        price_now < projected_lower - buffer_value
        if direction > 0
        else price_now > projected_upper + buffer_value
    )
    if breakout and not broke_direction:
        return None
    if not breakout and (broke_direction or broke_opposite):
        return None

    pole_volume = _finite_median(volume[pole_start : pole_end + 1], 0.0)
    consolidation_volume = _finite_median(
        volume[consolidation_start : consolidation_end + 1], 0.0
    )
    has_volume = pole_volume > 0 and consolidation_volume > 0
    contraction_ratio = consolidation_volume / pole_volume if has_volume else 1.0
    breakout_ratio = (
        float(volume[index]) / consolidation_volume
        if breakout and has_volume and float(volume[index]) > 0
        else 1.0
    )
    volume_ok = (
        has_volume
        and contraction_ratio <= volume_contraction_max
        and (not breakout or breakout_ratio >= breakout_volume_multiple)
    )
    if require_volume and (not has_volume or not volume_ok):
        return None

    residual_ratio = (upper_residual + lower_residual) / (2.0 * atr_value)
    fit_quality = max(0.0, 1.0 - residual_ratio / 1.25)
    compact_quality = max(0.0, 1.0 - width_ratio / max(max_width, 0.01))
    retrace_quality = max(0.0, 1.0 - retrace / max(max_retrace, 0.01))
    impulse_quality = min(1.0, pole_strength / max(pole_atr_multiple * 1.8, 0.01))
    efficiency_quality = min(1.0, max(0.0, (efficiency - 0.45) / 0.45))
    volume_quality = (
        max(0.0, 1.0 - contraction_ratio / max(volume_contraction_max, 0.01))
        if has_volume
        else 0.5
    )
    confidence = (
        18.0 * impulse_quality
        + 12.0 * efficiency_quality
        + 14.0 * retrace_quality
        + 10.0 * compact_quality
        + 12.0 * fit_quality
        + 12.0 * shape_quality
        + 10.0 * volume_quality
        + 4.0
    )
    if breakout:
        breakout_distance = (
            (price_now - projected_upper) / atr_value
            if direction > 0
            else (projected_lower - price_now) / atr_value
        )
        confidence += min(5.0, max(0.0, breakout_distance) * 5.0)
        confidence += 3.0 if volume_ok else 0.0
    confidence = float(min(99.0, max(0.0, confidence)))

    breakout_level = projected_upper if direction > 0 else projected_lower
    target = breakout_level + direction * pole_height
    invalidation = projected_lower - buffer_value if direction > 0 else projected_upper + buffer_value
    shape_reason = (
        f"parallel channel ({parallel_error:.2f} normalized error)"
        if "FLAG" in pattern
        else f"converging boundaries ({max(0.0, convergence) * 100:.0f}%)"
    )
    reason_parts = [
        f"strong pole ({pole_strength:.1f} ATR)",
        f"{retrace * 100:.0f}% retracement",
        shape_reason,
    ]
    if has_volume:
        reason_parts.append(f"volume contracted {max(0.0, 1.0 - contraction_ratio) * 100:.0f}%")
        if breakout:
            reason_parts.append(f"breakout volume {breakout_ratio:.2f}x consolidation median")
    else:
        reason_parts.append("volume unavailable; price geometry used")
    reason_parts.append(
        "fresh completed-close breakout"
        if breakout
        else "waiting for completed-close breakout"
    )
    return {
        "direction": direction,
        "pattern": pattern,
        "confidence": confidence,
        "upper": float(projected_upper),
        "lower": float(projected_lower),
        "target": float(target),
        "invalidation": float(invalidation),
        "breakout_level": float(breakout_level),
        "upper_start": float(upper_start),
        "lower_start": float(lower_start),
        "formation_start": int(consolidation_start),
        "formation_end": int(index),
        "pole_start": int(pole_start),
        "pole_end": int(pole_end),
        "formation_bars": consolidation_bars + (1 if breakout else 0),
        "reasons": "; ".join(reason_parts),
        "breakout": breakout,
        "consolidation_bars": consolidation_bars,
    }


def calculate_botbulls5(
    df: pd.DataFrame,
    param: Callable,
    evaluation_start: int | None = None,
) -> pd.DataFrame:
    """Attach BotBulls5 pattern geometry and causal signal columns to ``df``.

    ``evaluation_start`` is an internal live-chart optimization. Candles before
    that position remain available as causal lookback data, but the expensive
    per-candle pattern search begins at the requested position. Backtests omit
    it and continue evaluating the complete history.
    """
    # Internal geometry is deliberately fixed. Exposing every coefficient made
    # the model easy to overfit and produced inconsistent behavior between
    # tickers. Only the five high-impact controls below remain user-adjustable.
    pole_bars = 12
    min_bars = 5
    max_bars = 18
    pole_atr_multiple = 3.0
    max_retrace = 0.50
    max_width = 0.45
    parallel_tolerance = 0.35
    min_convergence = 0.25
    breakout_buffer_atr = _bounded_number(param, "BOTBULLS5_BREAKOUT_BUFFER_ATR", 0.10, 0.0, 1.0)
    volume_contraction_max = 0.85
    breakout_volume_multiple = 1.20
    min_confidence = _bounded_number(param, "BOTBULLS5_MIN_CONFIDENCE", 70.0, 40.0, 95.0)
    hold_bars = int(_bounded_number(param, "BOTBULLS5_SIGNAL_HOLD_BARS", 3, 1, 20))
    require_volume = bool(int(_bounded_number(param, "BOTBULLS5_REQUIRE_VOLUME", 0, 0, 1)))
    allow_pennants = True
    allow_wedges = True
    structure_settings = {
        "structure_lookback": int(_bounded_number(param, "BOTBULLS5_STRUCTURE_LOOKBACK", 48, 20, 120)),
        "pivot_span": 2,
        # Price-relative matching is intentionally tight; ATR remains the
        # adaptive floor for volatile symbols and higher timeframes.
        "level_tolerance": 0.0008,
        "breakout_buffer_atr": breakout_buffer_atr,
        "breakout_volume_multiple": breakout_volume_multiple,
        "require_volume": require_volume,
        "min_convergence": min_convergence,
        "parallel_tolerance": parallel_tolerance,
        "allow_wedges": allow_wedges,
        "allow_reversals": True,
        "allow_triangles": True,
        "allow_ranges": True,
        "allow_rounded": True,
        "allow_measured_moves": True,
    }

    n = len(df)
    numeric_defaults = {
        "BB5_Upper": np.nan,
        "BB5_Lower": np.nan,
        "BB5_Target": np.nan,
        "BB5_Invalidation": np.nan,
        "BB5_Breakout_Level": np.nan,
        "BB5_Upper_Start": np.nan,
        "BB5_Lower_Start": np.nan,
        "BB5_Formation_Start_Time": np.nan,
        "BB5_Formation_End_Time": np.nan,
        "BB5_Pole_Start_Time": np.nan,
        "BB5_Pole_End_Time": np.nan,
        "BB5_Formation_Bars": 0,
        "BB5_Confidence": 0.0,
        "BB5_Direction": 0,
        "BB5_Signal": 0,
        "BB5_Signal_Confidence": 0.0,
    }
    for key, default in numeric_defaults.items():
        df[key] = default
    df["BB5_Breakout_Bull"] = False
    df["BB5_Breakout_Bear"] = False
    df["BB5_Pattern"] = "NONE"
    df["BB5_State"] = "NONE"
    df["BB5_Signal_Pattern"] = "NONE"
    df["BB5_Reasons"] = ""
    if n < pole_bars + min_bars + 1:
        return df

    close = pd.to_numeric(df["Close"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(df["High"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(df["Low"], errors="coerce").to_numpy(dtype=float)
    volume = pd.to_numeric(df["Volume"], errors="coerce").fillna(0).to_numpy(dtype=float)
    if "ATR" in df:
        atr = pd.to_numeric(df["ATR"], errors="coerce").to_numpy(dtype=float)
    else:
        previous_close = np.roll(close, 1)
        previous_close[0] = close[0]
        true_range = np.maximum(high - low, np.maximum(abs(high - previous_close), abs(low - previous_close)))
        atr = pd.Series(true_range).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()

    # Four geometries cover short-to-long consolidations without turning chart
    # refreshes into an exhaustive optimization pass.
    length_step = max(1, int(math.ceil((max_bars - min_bars) / 3)))
    length_choices = list(range(min_bars, max_bars + 1, length_step))
    if max_bars not in length_choices:
        length_choices.append(max_bars)

    active_direction = 0
    active_confidence = 0.0
    active_pattern = "NONE"
    active_upper = np.nan
    active_lower = np.nan
    active_target = np.nan
    active_invalidation = np.nan
    active_breakout_level = np.nan
    active_upper_start = np.nan
    active_lower_start = np.nan
    active_formation_start_time = np.nan
    active_pole_start_time = np.nan
    active_pole_end_time = np.nan
    active_formation_bars = 0
    active_reasons = ""
    remaining = 0
    breakout_cooldown_until = -1
    structure_pivots = precompute_pivots(
        high,
        low,
        structure_settings["pivot_span"],
    )
    index_times = np.asarray([
        float(pd.Timestamp(value).timestamp())
        for value in df.index
    ], dtype=float)

    def candidate_time(candidate, key, fallback):
        try:
            position = int(candidate.get(key))
        except (TypeError, ValueError):
            position = int(fallback)
        position = max(0, min(n - 1, position))
        return float(index_times[position])

    minimum_start = pole_bars + min_bars
    try:
        loop_start = max(minimum_start, min(n, int(evaluation_start)))
    except (TypeError, ValueError):
        loop_start = minimum_start

    for i in range(loop_start, n):
        candidates = []
        for consolidation_bars in length_choices:
            for breakout in (True, False):
                # One closed-bar confirmation starts one finite signal window.
                # Do not relabel every subsequent continuation candle as a new
                # breakout while that window is still active.
                if breakout and (active_direction or i <= breakout_cooldown_until):
                    continue
                candidate = _candidate(
                    close=close,
                    high=high,
                    low=low,
                    volume=volume,
                    atr=atr,
                    index=i,
                    consolidation_bars=consolidation_bars,
                    pole_bars=pole_bars,
                    breakout=breakout,
                    pole_atr_multiple=pole_atr_multiple,
                    max_retrace=max_retrace,
                    max_width=max_width,
                    parallel_tolerance=parallel_tolerance,
                    min_convergence=min_convergence,
                    breakout_buffer_atr=breakout_buffer_atr,
                    volume_contraction_max=volume_contraction_max,
                    breakout_volume_multiple=breakout_volume_multiple,
                    require_volume=require_volume,
                    allow_pennants=allow_pennants,
                    allow_wedges=allow_wedges,
                )
                if candidate and candidate["confidence"] >= min_confidence:
                    candidates.append(candidate)

        structural = find_structural_patterns(
            close=close,
            high=high,
            low=low,
            volume=volume,
            atr=atr,
            index=i,
            breakout=None,
            settings=structure_settings,
            pivots=structure_pivots,
        )
        candidates.extend(
            candidate for candidate in structural
            if candidate["confidence"] >= min_confidence
            and (
                not candidate["breakout"]
                or (not active_direction and i > breakout_cooldown_until)
            )
        )

        best = max(
            candidates,
            key=lambda item: (
                1 if item["breakout"] else 0,
                item["confidence"],
                item["consolidation_bars"],
            ),
            default=None,
        )
        if best:
            df.iat[i, df.columns.get_loc("BB5_Upper")] = best["upper"]
            df.iat[i, df.columns.get_loc("BB5_Lower")] = best["lower"]
            df.iat[i, df.columns.get_loc("BB5_Target")] = best["target"]
            df.iat[i, df.columns.get_loc("BB5_Invalidation")] = best["invalidation"]
            df.iat[i, df.columns.get_loc("BB5_Breakout_Level")] = best["breakout_level"]
            df.iat[i, df.columns.get_loc("BB5_Upper_Start")] = best["upper_start"]
            df.iat[i, df.columns.get_loc("BB5_Lower_Start")] = best["lower_start"]
            formation_start_time = candidate_time(best, "formation_start", i - best["formation_bars"] + 1)
            formation_end_time = candidate_time(best, "formation_end", i)
            pole_start_time = candidate_time(best, "pole_start", i)
            pole_end_time = candidate_time(best, "pole_end", i)
            df.iat[i, df.columns.get_loc("BB5_Formation_Start_Time")] = formation_start_time
            df.iat[i, df.columns.get_loc("BB5_Formation_End_Time")] = formation_end_time
            df.iat[i, df.columns.get_loc("BB5_Pole_Start_Time")] = (
                pole_start_time if best.get("pole_start") is not None else np.nan
            )
            df.iat[i, df.columns.get_loc("BB5_Pole_End_Time")] = (
                pole_end_time if best.get("pole_end") is not None else np.nan
            )
            df.iat[i, df.columns.get_loc("BB5_Formation_Bars")] = best["formation_bars"]
            df.iat[i, df.columns.get_loc("BB5_Confidence")] = round(best["confidence"], 2)
            df.iat[i, df.columns.get_loc("BB5_Direction")] = best["direction"]
            df.iat[i, df.columns.get_loc("BB5_Reasons")] = best["reasons"]
            df.iat[i, df.columns.get_loc("BB5_Pattern")] = best["pattern"]
            df.iat[i, df.columns.get_loc("BB5_State")] = (
                "CONFIRMED_BULLISH"
                if best["breakout"] and best["direction"] > 0
                else "CONFIRMED_BEARISH"
                if best["breakout"]
                else "FORMING"
            )
            if best["breakout"]:
                bull = best["direction"] > 0
                df.iat[i, df.columns.get_loc("BB5_Breakout_Bull")] = bull
                df.iat[i, df.columns.get_loc("BB5_Breakout_Bear")] = not bull
                active_direction = best["direction"]
                active_confidence = best["confidence"]
                active_pattern = best["pattern"]
                active_upper = best["upper"]
                active_lower = best["lower"]
                active_target = best["target"]
                active_invalidation = best["invalidation"]
                active_breakout_level = best["breakout_level"]
                active_upper_start = best["upper_start"]
                active_lower_start = best["lower_start"]
                active_formation_start_time = formation_start_time
                active_pole_start_time = (
                    pole_start_time if best.get("pole_start") is not None else np.nan
                )
                active_pole_end_time = (
                    pole_end_time if best.get("pole_end") is not None else np.nan
                )
                active_formation_bars = best["formation_bars"]
                active_reasons = best["reasons"]
                remaining = hold_bars
                breakout_cooldown_until = i + max_bars

        if active_direction:
            invalidated = (
                close[i] <= active_invalidation
                if active_direction > 0
                else close[i] >= active_invalidation
            )
            target_hit = high[i] >= active_target if active_direction > 0 else low[i] <= active_target
            if invalidated:
                df.iat[i, df.columns.get_loc("BB5_Upper")] = active_upper
                df.iat[i, df.columns.get_loc("BB5_Lower")] = active_lower
                df.iat[i, df.columns.get_loc("BB5_Target")] = active_target
                df.iat[i, df.columns.get_loc("BB5_Invalidation")] = active_invalidation
                df.iat[i, df.columns.get_loc("BB5_Breakout_Level")] = active_breakout_level
                df.iat[i, df.columns.get_loc("BB5_Upper_Start")] = active_upper_start
                df.iat[i, df.columns.get_loc("BB5_Lower_Start")] = active_lower_start
                df.iat[i, df.columns.get_loc("BB5_Formation_Start_Time")] = active_formation_start_time
                df.iat[i, df.columns.get_loc("BB5_Formation_End_Time")] = index_times[i]
                df.iat[i, df.columns.get_loc("BB5_Pole_Start_Time")] = active_pole_start_time
                df.iat[i, df.columns.get_loc("BB5_Pole_End_Time")] = active_pole_end_time
                df.iat[i, df.columns.get_loc("BB5_Formation_Bars")] = active_formation_bars
                df.iat[i, df.columns.get_loc("BB5_Confidence")] = round(active_confidence, 2)
                df.iat[i, df.columns.get_loc("BB5_Direction")] = active_direction
                df.iat[i, df.columns.get_loc("BB5_Reasons")] = active_reasons
                df.iat[i, df.columns.get_loc("BB5_Pattern")] = active_pattern
                df.iat[i, df.columns.get_loc("BB5_State")] = "FAILED"
                active_direction = 0
                remaining = 0
            else:
                df.iat[i, df.columns.get_loc("BB5_Upper")] = active_upper
                df.iat[i, df.columns.get_loc("BB5_Lower")] = active_lower
                df.iat[i, df.columns.get_loc("BB5_Target")] = active_target
                df.iat[i, df.columns.get_loc("BB5_Invalidation")] = active_invalidation
                df.iat[i, df.columns.get_loc("BB5_Breakout_Level")] = active_breakout_level
                df.iat[i, df.columns.get_loc("BB5_Upper_Start")] = active_upper_start
                df.iat[i, df.columns.get_loc("BB5_Lower_Start")] = active_lower_start
                df.iat[i, df.columns.get_loc("BB5_Formation_Start_Time")] = active_formation_start_time
                df.iat[i, df.columns.get_loc("BB5_Formation_End_Time")] = index_times[i]
                df.iat[i, df.columns.get_loc("BB5_Pole_Start_Time")] = active_pole_start_time
                df.iat[i, df.columns.get_loc("BB5_Pole_End_Time")] = active_pole_end_time
                df.iat[i, df.columns.get_loc("BB5_Formation_Bars")] = active_formation_bars
                df.iat[i, df.columns.get_loc("BB5_Confidence")] = round(active_confidence, 2)
                df.iat[i, df.columns.get_loc("BB5_Direction")] = active_direction
                df.iat[i, df.columns.get_loc("BB5_Reasons")] = active_reasons
                df.iat[i, df.columns.get_loc("BB5_Pattern")] = active_pattern
                df.iat[i, df.columns.get_loc("BB5_State")] = (
                    "CONFIRMED_BULLISH" if active_direction > 0 else "CONFIRMED_BEARISH"
                )
                df.iat[i, df.columns.get_loc("BB5_Signal")] = active_direction
                df.iat[i, df.columns.get_loc("BB5_Signal_Confidence")] = round(active_confidence, 2)
                df.iat[i, df.columns.get_loc("BB5_Signal_Pattern")] = active_pattern
                remaining -= 1
                if target_hit or remaining <= 0:
                    active_direction = 0
                    remaining = 0
                else:
                    active_formation_bars += 1

    return df
