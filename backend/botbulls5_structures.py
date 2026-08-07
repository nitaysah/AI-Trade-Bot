"""Causal structural-pattern recognizers used by BotBulls5.

These rules intentionally favor explicit completion levels over visual
guessing.  Every candidate is built from bars that existed before the signal
bar; the current close is used only to confirm or reject the breakout.
"""

from __future__ import annotations

import math

import numpy as np


def _finite_median(values, fallback=0.0, positive=False):
    finite = values[np.isfinite(values)]
    if positive:
        finite = finite[finite > 0]
    if not finite.size:
        return float(fallback)
    middle = finite.size // 2
    partitioned = np.partition(finite, middle)
    if finite.size % 2:
        return float(partitioned[middle])
    return float((np.max(partitioned[:middle]) + partitioned[middle]) / 2.0)


def _line(values):
    x = np.arange(len(values), dtype=float)
    x_mean = float(np.mean(x))
    y_mean = float(np.mean(values))
    denominator = float(np.sum(np.square(x - x_mean)))
    slope = float(np.sum((x - x_mean) * (values - y_mean)) / denominator) if denominator else 0.0
    intercept = y_mean - slope * x_mean
    fitted = intercept + slope * x
    residual = float(np.sqrt(np.mean(np.square(values - fitted))))
    return float(slope), float(intercept), residual


def _line_points(points, origin):
    """Fit a boundary to actual swing touches, not every candle extreme."""
    x = np.asarray([index - origin for index, _ in points], dtype=float)
    y = np.asarray([value for _, value in points], dtype=float)
    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    denominator = float(np.sum(np.square(x - x_mean)))
    slope = float(np.sum((x - x_mean) * (y - y_mean)) / denominator) if denominator else 0.0
    intercept = y_mean - slope * x_mean
    fitted = intercept + slope * x
    residual = float(np.sqrt(np.mean(np.square(y - fitted))))
    return float(slope), float(intercept), residual


def _pivot_points(high, low, start, end, span):
    highs, lows = [], []
    for i in range(start + span, end - span + 1):
        high_window = high[i - span : i + span + 1]
        low_window = low[i - span : i + span + 1]
        if np.isfinite(high[i]) and high[i] >= np.max(high_window):
            highs.append((i, float(high[i])))
        if np.isfinite(low[i]) and low[i] <= np.min(low_window):
            lows.append((i, float(low[i])))
    return highs, lows


def precompute_pivots(high, low, span):
    """Precompute confirmed swing points; callers still time-filter them."""
    return _pivot_points(high, low, 0, len(high) - 1, span)


def _crossed(close, index, level, direction, buffer_value):
    if index < 1 or not math.isfinite(level):
        return False
    trigger = level + direction * buffer_value
    return (
        close[index] > trigger and close[index - 1] <= trigger
        if direction > 0
        else close[index] < trigger and close[index - 1] >= trigger
    )


def _still_forming(close, index, level, direction, buffer_value):
    """A forming setup must remain on the pre-breakout side of its trigger."""
    if index < 0 or not math.isfinite(level):
        return False
    trigger = level + direction * buffer_value
    return close[index] <= trigger if direction > 0 else close[index] >= trigger


def _pattern_reason(pattern):
    readable = pattern.replace("_BULLISH", "").replace("_BEARISH", "").replace("_", " ").lower()
    if "triangle" in pattern.lower():
        return f"{readable} boundary convergence"
    if "wedge" in pattern.lower():
        return f"{readable} converging swing boundaries"
    if "rectangle" in pattern.lower() or "channel" in pattern.lower():
        return f"{readable} repeated support/resistance touches"
    if "top" in pattern.lower() or "bottom" in pattern.lower() or "shoulders" in pattern.lower():
        return f"{readable} swing symmetry and neckline"
    if "rounding" in pattern.lower() or "cup" in pattern.lower():
        return f"{readable} rounded base and rim"
    if "measured" in pattern.lower():
        return f"{readable} impulse, retracement, and continuation level"
    return f"{readable} qualified structure"


def _candidate(
    pattern,
    direction,
    confidence,
    upper,
    lower,
    target,
    invalidation,
    breakout,
    *,
    formation_bars=0,
    formation_start=None,
    formation_end=None,
    pole_start=None,
    pole_end=None,
    upper_start=None,
    lower_start=None,
    reasons=None,
):
    breakout_level = upper if direction > 0 else lower
    reason_parts = list(reasons or [_pattern_reason(pattern)])
    reason_parts.append(
        "fresh completed-close breakout"
        if breakout
        else "waiting for completed-close breakout"
    )
    return {
        "pattern": pattern,
        "direction": int(direction),
        "confidence": float(min(99.0, max(0.0, confidence + (7.0 if breakout else 0.0)))),
        "upper": float(upper),
        "lower": float(lower),
        "target": float(target),
        "invalidation": float(invalidation),
        "breakout": bool(breakout),
        "breakout_level": float(breakout_level),
        "formation_bars": max(0, int(formation_bars or 0)),
        "formation_start": None if formation_start is None else int(formation_start),
        "formation_end": None if formation_end is None else int(formation_end),
        "pole_start": None if pole_start is None else int(pole_start),
        "pole_end": None if pole_end is None else int(pole_end),
        "upper_start": float(upper if upper_start is None else upper_start),
        "lower_start": float(lower if lower_start is None else lower_start),
        "reasons": "; ".join(reason_parts),
        "consolidation_bars": max(0, int(formation_bars or 0)),
    }


def _prior_move(close, pivot_index, start, direction, atr_value):
    earlier = max(start, pivot_index - 18)
    return direction * (close[pivot_index] - close[earlier]) >= 2.0 * atr_value


def _multi_peak_candidates(
    close, high_pivots, low_pivots, start, index, atr_value, tolerance, buffer_value
):
    candidates = []
    for count, top_name, bottom_name in (
        (3, "TRIPLE_TOP_REVERSAL", "TRIPLE_BOTTOM_REVERSAL"),
        (2, "DOUBLE_TOP_REVERSAL", "DOUBLE_BOTTOM_REVERSAL"),
    ):
        if len(high_pivots) >= count:
            peaks = high_pivots[-count:]
            values = np.array([value for _, value in peaks])
            spacing_ok = all(peaks[j + 1][0] - peaks[j][0] >= 3 for j in range(count - 1))
            if (
                spacing_ok
                and index - peaks[-1][0] <= 14
                and np.ptp(values) <= max(tolerance * np.mean(values), 1.5 * atr_value)
                and _prior_move(close, peaks[0][0], start, 1, atr_value)
            ):
                between = [
                    value for pivot_index, value in low_pivots
                    if peaks[0][0] < pivot_index < peaks[-1][0]
                ]
                if between:
                    neckline = min(between)
                    height = float(np.mean(values) - neckline)
                    if height >= 1.5 * atr_value:
                        broke = _crossed(close, index, neckline, -1, buffer_value)
                        if broke or _still_forming(close, index, neckline, -1, buffer_value):
                            match_limit = max(tolerance * np.mean(values), 1.5 * atr_value)
                            symmetry = max(0.0, 1.0 - np.ptp(values) / max(match_limit, 1e-9))
                            confidence = 64 + (2 if count == 3 else 0) + 5 * symmetry + min(6, height / atr_value)
                            candidates.append(_candidate(
                                top_name, -1, confidence,
                                float(np.mean(values)), neckline,
                                neckline - height, float(np.max(values)) + buffer_value, broke,
                                formation_bars=index - peaks[0][0] + 1,
                                formation_start=peaks[0][0],
                                formation_end=index,
                            ))
        if len(low_pivots) >= count:
            troughs = low_pivots[-count:]
            values = np.array([value for _, value in troughs])
            spacing_ok = all(troughs[j + 1][0] - troughs[j][0] >= 3 for j in range(count - 1))
            if (
                spacing_ok
                and index - troughs[-1][0] <= 14
                and np.ptp(values) <= max(tolerance * np.mean(values), 1.5 * atr_value)
                and _prior_move(close, troughs[0][0], start, -1, atr_value)
            ):
                between = [
                    value for pivot_index, value in high_pivots
                    if troughs[0][0] < pivot_index < troughs[-1][0]
                ]
                if between:
                    neckline = max(between)
                    height = float(neckline - np.mean(values))
                    if height >= 1.5 * atr_value:
                        broke = _crossed(close, index, neckline, 1, buffer_value)
                        if broke or _still_forming(close, index, neckline, 1, buffer_value):
                            match_limit = max(tolerance * np.mean(values), 1.5 * atr_value)
                            symmetry = max(0.0, 1.0 - np.ptp(values) / max(match_limit, 1e-9))
                            confidence = 64 + (2 if count == 3 else 0) + 5 * symmetry + min(6, height / atr_value)
                            candidates.append(_candidate(
                                bottom_name, 1, confidence,
                                neckline, float(np.mean(values)),
                                neckline + height, float(np.min(values)) - buffer_value, broke,
                                formation_bars=index - troughs[0][0] + 1,
                                formation_start=troughs[0][0],
                                formation_end=index,
                            ))
    return candidates


def _head_shoulders_candidates(
    close, high_pivots, low_pivots, start, index, atr_value, tolerance, buffer_value
):
    candidates = []
    if len(high_pivots) >= 3:
        left, head, right = high_pivots[-3:]
        shoulders_close = abs(left[1] - right[1]) <= max(tolerance * ((left[1] + right[1]) / 2), 1.5 * atr_value)
        if (
            left[0] < head[0] < right[0]
            and index - right[0] <= 14
            and head[1] >= max(left[1], right[1]) + 0.75 * atr_value
            and shoulders_close
            and _prior_move(close, left[0], start, 1, atr_value)
        ):
            reactions = [(p, v) for p, v in low_pivots if left[0] < p < right[0]]
            if len(reactions) >= 2:
                a, b = reactions[0], reactions[-1]
                slope = (b[1] - a[1]) / max(1, b[0] - a[0])
                neckline = a[1] + slope * (index - a[0])
                previous_neckline = a[1] + slope * (index - 1 - a[0])
                height = head[1] - neckline
                broke = (
                    close[index] < neckline - buffer_value
                    and close[index - 1] >= previous_neckline - buffer_value
                )
                if broke or _still_forming(close, index, neckline, -1, buffer_value):
                    candidates.append(_candidate(
                        "HEAD_AND_SHOULDERS_TOP", -1, 74,
                        head[1], neckline, neckline - height,
                        max(left[1], right[1]) + buffer_value, broke,
                        formation_bars=index - left[0] + 1,
                        formation_start=left[0],
                        formation_end=index,
                    ))
    if len(low_pivots) >= 3:
        left, head, right = low_pivots[-3:]
        shoulders_close = abs(left[1] - right[1]) <= max(tolerance * ((left[1] + right[1]) / 2), 1.5 * atr_value)
        if (
            left[0] < head[0] < right[0]
            and index - right[0] <= 14
            and head[1] <= min(left[1], right[1]) - 0.75 * atr_value
            and shoulders_close
            and _prior_move(close, left[0], start, -1, atr_value)
        ):
            reactions = [(p, v) for p, v in high_pivots if left[0] < p < right[0]]
            if len(reactions) >= 2:
                a, b = reactions[0], reactions[-1]
                slope = (b[1] - a[1]) / max(1, b[0] - a[0])
                neckline = a[1] + slope * (index - a[0])
                previous_neckline = a[1] + slope * (index - 1 - a[0])
                height = neckline - head[1]
                broke = (
                    close[index] > neckline + buffer_value
                    and close[index - 1] <= previous_neckline + buffer_value
                )
                if broke or _still_forming(close, index, neckline, 1, buffer_value):
                    candidates.append(_candidate(
                        "HEAD_AND_SHOULDERS_BOTTOM", 1, 74,
                        neckline, head[1], neckline + height,
                        min(left[1], right[1]) - buffer_value, broke,
                        formation_bars=index - left[0] + 1,
                        formation_start=left[0],
                        formation_end=index,
                    ))
    return candidates


def _boundary_candidates(
    close, volume, high_pivots, low_pivots, start, end, index, atr_value, buffer_value,
    min_convergence, parallel_tolerance,
):
    candidates = []
    upper_points = high_pivots[-5:]
    lower_points = low_pivots[-5:]
    if len(upper_points) < 2 or len(lower_points) < 2:
        return candidates
    structure_start = min(upper_points[0][0], lower_points[0][0])
    length = end - structure_start + 1
    if length < 10:
        return candidates
    upper_slope, upper_intercept, upper_residual = _line_points(upper_points, structure_start)
    lower_slope, lower_intercept, lower_residual = _line_points(lower_points, structure_start)
    current_x = index - structure_start
    previous_x = end - structure_start
    upper = upper_intercept + upper_slope * current_x
    lower = lower_intercept + lower_slope * current_x
    previous_upper = upper_intercept + upper_slope * previous_x
    previous_lower = lower_intercept + lower_slope * previous_x
    initial_width = upper_intercept - lower_intercept
    width = upper - lower
    if initial_width <= atr_value or width <= atr_value * 0.35:
        return candidates
    if upper_residual > 1.25 * atr_value or lower_residual > 1.25 * atr_value:
        return candidates
    convergence = (initial_width - width) / initial_width
    slope_unit = max(atr_value / max(length, 1), 1e-9)
    upper_n = upper_slope / slope_unit
    lower_n = lower_slope / slope_unit
    residual_quality = max(0.0, 1.0 - (upper_residual + lower_residual) / (2.5 * atr_value))
    volume_start = _finite_median(
        volume[structure_start : structure_start + max(3, length // 3)]
    )
    volume_end = _finite_median(
        volume[end - max(2, length // 3) + 1 : end + 1]
    )
    volume_contracts = volume_start > 0 and volume_end <= volume_start
    touch_quality = min(1.0, (len(upper_points) + len(lower_points) - 4) / 4.0)
    quality = 58 + 10 * residual_quality + 6 * touch_quality + (4 if volume_contracts else 0)

    pattern, expected_direction = None, 0
    near_flat_upper = abs(upper_n) <= 0.35
    near_flat_lower = abs(lower_n) <= 0.35
    if convergence >= min_convergence and upper_n < -0.20 and lower_n > 0.20:
        pattern = "SYMMETRICAL_TRIANGLE"
    elif convergence >= min_convergence and near_flat_upper and lower_n > 0.25:
        pattern, expected_direction = "ASCENDING_TRIANGLE", 1
    elif convergence >= min_convergence and upper_n < -0.25 and near_flat_lower:
        pattern, expected_direction = "DESCENDING_TRIANGLE", -1
    elif convergence >= min_convergence and upper_n < -0.15 and lower_n < -0.05 and upper_slope < lower_slope:
        pattern, expected_direction = "FALLING_WEDGE", 1
    elif convergence >= min_convergence and upper_n > 0.05 and lower_n > 0.15 and upper_slope < lower_slope:
        pattern, expected_direction = "RISING_WEDGE", -1
    elif near_flat_upper and near_flat_lower and abs(upper_slope - lower_slope) / slope_unit <= parallel_tolerance:
        pattern = "RECTANGLE"
    elif abs(upper_slope - lower_slope) / slope_unit <= parallel_tolerance and abs((upper_slope + lower_slope) / 2) > slope_unit * 0.25:
        pattern = "PRICE_CHANNEL"

    if pattern:
        for direction, level in ((1, upper), (-1, lower)):
            if expected_direction and direction != expected_direction:
                continue
            previous_level = previous_upper if direction > 0 else previous_lower
            trigger = level + direction * buffer_value
            previous_trigger = previous_level + direction * buffer_value
            broke = (
                close[index] > trigger and close[index - 1] <= previous_trigger
                if direction > 0
                else close[index] < trigger and close[index - 1] >= previous_trigger
            )
            if not broke and not _still_forming(close, index, level, direction, buffer_value):
                continue
            height = max(initial_width, width)
            candidates.append(_candidate(
                pattern if expected_direction else f"{pattern}_{'BULLISH' if direction > 0 else 'BEARISH'}",
                direction, quality + (3 if expected_direction else 0),
                upper, lower, level + direction * height,
                lower - buffer_value if direction > 0 else upper + buffer_value, broke,
                formation_bars=current_x + 1,
                formation_start=structure_start,
                formation_end=index,
                upper_start=upper_intercept,
                lower_start=lower_intercept,
                reasons=[
                    _pattern_reason(pattern),
                    f"{len(upper_points) + len(lower_points)} confirmed boundary touches",
                    f"{max(0.0, convergence) * 100:.0f}% boundary convergence"
                    if convergence >= 0
                    else f"{abs(convergence) * 100:.0f}% boundary expansion",
                ],
            ))

    # Broadening formations need expanding, alternating swing structure.  The
    # breakout direction stays neutral until price actually leaves the range.
    if convergence <= -0.20 and upper_slope > 0 and lower_slope < 0:
        if len(upper_points) < 3 or len(lower_points) < 3:
            return candidates
        for direction, level in ((1, upper), (-1, lower)):
            previous_level = previous_upper if direction > 0 else previous_lower
            trigger = level + direction * buffer_value
            previous_trigger = previous_level + direction * buffer_value
            broke = (
                close[index] > trigger and close[index - 1] <= previous_trigger
                if direction > 0
                else close[index] < trigger and close[index - 1] >= previous_trigger
            )
            if not broke and not _still_forming(close, index, level, direction, buffer_value):
                continue
            candidates.append(_candidate(
                f"BROADENING_TOP_{'BULLISH' if direction > 0 else 'BEARISH'}",
                direction, quality - 5, upper, lower,
                level + direction * max(initial_width, width),
                lower - buffer_value if direction > 0 else upper + buffer_value, broke,
                formation_bars=current_x + 1,
                formation_start=structure_start,
                formation_end=index,
                upper_start=upper_intercept,
                lower_start=lower_intercept,
                reasons=[
                    "broadening alternating swing boundaries",
                    f"{len(upper_points) + len(lower_points)} confirmed boundary touches",
                    f"{abs(convergence) * 100:.0f}% boundary expansion",
                ],
            ))
    return candidates


def _rounded_candidates(close, high, low, start, end, index, atr_value, buffer_value):
    candidates = []
    values = close[start : end + 1]
    length = len(values)
    if length < 24:
        return candidates
    quarter = max(3, length // 4)
    middle_low = float(np.min(values[quarter : length - quarter]))
    rim_hint = float(min(np.max(values[:quarter]), np.max(values[-quarter:])))
    if rim_hint - middle_low < 2 * atr_value or values[-1] < rim_hint - 1.5 * atr_value:
        return candidates
    x = np.linspace(-1.0, 1.0, length)
    coefficient = np.polyfit(x, values, 2)
    fitted = np.polyval(coefficient, x)
    denominator = np.sum(np.square(values - np.mean(values)))
    r2 = 1.0 - np.sum(np.square(values - fitted)) / denominator if denominator > 0 else 0.0
    low_pos = int(np.argmin(fitted))
    rim = float(min(np.max(values[: max(3, length // 4)]), np.max(values[-max(3, length // 4) :])))
    depth = rim - float(np.min(values))
    near_rim = values[-1] >= rim - 1.5 * atr_value
    if coefficient[0] > atr_value and 0.25 * length <= low_pos <= 0.70 * length and r2 >= 0.68 and depth >= 2 * atr_value and near_rim:
        broke = _crossed(close, index, rim, 1, buffer_value)
        if broke or _still_forming(close, index, rim, 1, buffer_value):
            candidates.append(_candidate(
                "ROUNDING_BOTTOM", 1, 62 + 14 * min(1.0, r2),
                rim, float(np.min(values)), rim + depth,
                float(np.min(values)) - buffer_value, broke,
                formation_bars=index - start + 1,
                formation_start=start,
                formation_end=index,
            ))

        handle_bars = max(4, min(10, length // 5))
        cup = values[:-handle_bars]
        handle = values[-handle_bars:]
        left_rim = float(np.max(cup[: max(3, len(cup) // 4)]))
        right_rim = float(np.max(cup[-max(3, len(cup) // 4) :]))
        cup_depth = min(left_rim, right_rim) - float(np.min(cup))
        rim_tolerance = max(2 * atr_value, 0.003 * ((left_rim + right_rim) / 2))
        handle_retrace = right_rim - float(np.min(handle))
        resistance = max(left_rim, right_rim)
        if (
            abs(left_rim - right_rim) <= rim_tolerance
            and cup_depth >= 2 * atr_value
            and handle_retrace <= cup_depth / 3.0 + atr_value * 0.25
            and values[-1] >= resistance - 1.5 * atr_value
        ):
            broke = _crossed(close, index, resistance, 1, buffer_value)
            if broke or _still_forming(close, index, resistance, 1, buffer_value):
                candidates.append(_candidate(
                    "CUP_WITH_HANDLE", 1, 74,
                    resistance, float(np.min(handle)), resistance + cup_depth,
                    float(np.min(handle)) - buffer_value, broke,
                    formation_bars=index - start + 1,
                    formation_start=start,
                    formation_end=index,
                ))
    return candidates


def _measured_move_candidates(
    close, high_pivots, low_pivots, start, index, atr_value, buffer_value
):
    candidates = []
    # Low -> high -> higher low, followed by a break above the first leg.
    for low1 in low_pivots[-4:]:
        highs_after = [point for point in high_pivots if point[0] > low1[0]]
        if not highs_after:
            continue
        high1 = highs_after[0]
        lows_after = [point for point in low_pivots if point[0] > high1[0]]
        if not lows_after:
            continue
        low2 = lows_after[-1]
        leg = high1[1] - low1[1]
        retrace = (high1[1] - low2[1]) / leg if leg > 0 else 0
        if leg >= 3 * atr_value and 0.30 <= retrace <= 0.70 and index - low2[0] <= 12:
            broke = _crossed(close, index, high1[1], 1, buffer_value)
            if broke or _still_forming(close, index, high1[1], 1, buffer_value):
                candidates.append(_candidate(
                    "MEASURED_MOVE_BULLISH", 1, 70,
                    high1[1], low2[1], low2[1] + leg,
                    low2[1] - buffer_value, broke,
                    formation_bars=index - low1[0] + 1,
                    formation_start=low1[0],
                    formation_end=index,
                ))
            break
    # High -> low -> lower high, followed by a break below the first leg.
    for high1 in high_pivots[-4:]:
        lows_after = [point for point in low_pivots if point[0] > high1[0]]
        if not lows_after:
            continue
        low1 = lows_after[0]
        highs_after = [point for point in high_pivots if point[0] > low1[0]]
        if not highs_after:
            continue
        high2 = highs_after[-1]
        leg = high1[1] - low1[1]
        retrace = (high2[1] - low1[1]) / leg if leg > 0 else 0
        if leg >= 3 * atr_value and 0.30 <= retrace <= 0.70 and index - high2[0] <= 12:
            broke = _crossed(close, index, low1[1], -1, buffer_value)
            if broke or _still_forming(close, index, low1[1], -1, buffer_value):
                candidates.append(_candidate(
                    "MEASURED_MOVE_BEARISH", -1, 70,
                    high2[1], low1[1], high2[1] - leg,
                    high2[1] + buffer_value, broke,
                    formation_bars=index - high1[0] + 1,
                    formation_start=high1[0],
                    formation_end=index,
                ))
            break
    return candidates


def _bump_run_candidate(close, start, end, index, atr_value, buffer_value):
    values = close[start : end + 1]
    if len(values) < 24:
        return None
    split = max(8, len(values) // 2)
    peak_offset = int(np.argmax(values[split:]))
    peak_index = split + peak_offset
    if peak_index >= len(values) - 2:
        return None
    lead_slope, lead_intercept, lead_residual = _line(values[:split])
    bump_slope, _, _ = _line(values[split : peak_index + 1])
    projected = lead_intercept + lead_slope * (len(values) - 1)
    excess = float(np.max(values[split:]) - projected)
    if (
        lead_slope > 0
        and bump_slope > 1.45 * lead_slope
        and excess >= max(2.0 * lead_residual, 2.5 * atr_value)
    ):
        support = lead_intercept + lead_slope * len(values)
        previous_support = lead_intercept + lead_slope * (len(values) - 1)
        broke = (
            close[index] < support - buffer_value
            and close[index - 1] >= previous_support - buffer_value
        )
        peak = float(np.max(values))
        if broke or _still_forming(close, index, support, -1, buffer_value):
            return _candidate(
                "BUMP_AND_RUN_REVERSAL", -1, 72,
                peak, support, support - (peak - support),
                peak + buffer_value, broke,
                formation_bars=index - start + 1,
                formation_start=start,
                formation_end=index,
            )
    return None


def find_structural_patterns(
    *, close, high, low, volume, atr, index, breakout, settings, pivots=None
):
    """Return all qualified structural candidates for one causal bar."""
    lookback = int(settings.get("structure_lookback", 48))
    span = int(settings.get("pivot_span", 2))
    # Geometry always ends on the prior completed bar. The current close is
    # used only as the confirmation observation, preventing self-fitting.
    end = index - 1
    start = max(0, end - lookback + 1)
    if end - start + 1 < max(14, span * 4 + 5):
        return []
    atr_slice = atr[start : end + 1]
    atr_value = _finite_median(atr_slice, abs(close[end]) * 0.005)
    if not math.isfinite(atr_value) or atr_value <= 0:
        return []
    buffer_value = float(settings.get("breakout_buffer_atr", 0.10)) * atr_value
    tolerance = float(settings.get("level_tolerance", 0.0008))
    if pivots is None:
        high_pivots, low_pivots = _pivot_points(high, low, start, end, span)
    else:
        # A pivot is not known until ``span`` later bars have completed.
        # The current candle remains reserved for the fresh breakout test.
        high_pivots = [
            point for point in pivots[0]
            if start <= point[0] and point[0] + span <= end
        ]
        low_pivots = [
            point for point in pivots[1]
            if start <= point[0] and point[0] + span <= end
        ]
    candidates = []

    if settings.get("allow_reversals", True):
        candidates.extend(_multi_peak_candidates(
            close, high_pivots, low_pivots, start, index, atr_value, tolerance, buffer_value
        ))
        candidates.extend(_head_shoulders_candidates(
            close, high_pivots, low_pivots, start, index, atr_value, tolerance, buffer_value
        ))
        bump = _bump_run_candidate(close, start, end, index, atr_value, buffer_value)
        if bump:
            candidates.append(bump)

    if settings.get("allow_triangles", True) or settings.get("allow_ranges", True):
        boundary = _boundary_candidates(
            close, volume, high_pivots, low_pivots, start, end, index, atr_value, buffer_value,
            float(settings.get("min_convergence", 0.25)),
            float(settings.get("parallel_tolerance", 0.35)),
        )
        for candidate in boundary:
            pattern = candidate["pattern"]
            if (
                ("TRIANGLE" in pattern and not settings.get("allow_triangles", True))
                or (any(token in pattern for token in ("RECTANGLE", "PRICE_CHANNEL", "BROADENING")) and not settings.get("allow_ranges", True))
                or ("WEDGE" in pattern and not settings.get("allow_wedges", True))
            ):
                continue
            candidates.append(candidate)

    if settings.get("allow_rounded", True):
        candidates.extend(_rounded_candidates(
            close, high, low, start, end, index, atr_value, buffer_value
        ))

    if settings.get("allow_measured_moves", True):
        candidates.extend(_measured_move_candidates(
            close, high_pivots, low_pivots, start, index, atr_value, buffer_value
        ))

    require_volume = bool(settings.get("require_volume", False))
    breakout_volume_multiple = float(settings.get("breakout_volume_multiple", 1.20))
    prior_volume = volume[max(start, end - 9) : end + 1]
    baseline_volume = _finite_median(prior_volume, 0.0, positive=True)
    current_volume = float(volume[index]) if math.isfinite(float(volume[index])) else 0.0
    volume_ratio = current_volume / baseline_volume if baseline_volume > 0 else 0.0

    qualified = []
    for candidate in candidates:
        if not candidate.get("formation_bars"):
            formation_start = candidate.get("formation_start")
            if formation_start is None:
                formation_start = start
                candidate["formation_start"] = formation_start
            candidate["formation_end"] = index
            candidate["formation_bars"] = max(2, index - formation_start + 1)
            candidate["consolidation_bars"] = candidate["formation_bars"]
        if candidate["breakout"]:
            if require_volume and (
                baseline_volume <= 0
                or volume_ratio < breakout_volume_multiple
            ):
                continue
            if baseline_volume > 0:
                candidate["reasons"] += f"; breakout volume {volume_ratio:.2f}x recent median"
                candidate["confidence"] = min(
                    99.0,
                    max(
                        0.0,
                        candidate["confidence"]
                        + (3.0 if volume_ratio >= breakout_volume_multiple else -3.0),
                    ),
                )
        elif baseline_volume > 0:
            candidate["reasons"] += "; volume available for breakout confirmation"
        qualified.append(candidate)

    if breakout is None:
        return qualified
    return [candidate for candidate in qualified if candidate["breakout"] == breakout]
