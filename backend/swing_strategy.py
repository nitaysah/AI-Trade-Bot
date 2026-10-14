from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any, Dict, List, Optional

import pytz

from prior_day_high_low import prior_day_levels_from_history, prior_day_ohlc_from_history


NY_TZ = pytz.timezone("America/New_York")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_et_dt(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    try:
        if isinstance(ts, datetime):
            dt = ts
        else:
            ts_text = str(ts).strip()
            try:
                raw = float(ts_text)
                if raw > 10**10:
                    raw = raw / 1000.0
                dt = datetime.fromtimestamp(raw, tz=pytz.UTC)
            except (TypeError, ValueError):
                dt = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        return dt.astimezone(NY_TZ)
    except Exception:
        return None


def _is_regular_session(dt_et: Optional[datetime]) -> bool:
    if dt_et is None:
        return True
    if dt_et.weekday() >= 5:
        return False
    session_start = dt_et.replace(hour=9, minute=30, second=0, microsecond=0)
    session_end = dt_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return session_start <= dt_et <= session_end


def _normalize_candle(candle: dict) -> Optional[dict]:
    if not isinstance(candle, dict):
        return None
    high = _safe_float(candle.get("high"), None)
    low = _safe_float(candle.get("low"), None)
    close = _safe_float(candle.get("close"), None)
    open_ = _safe_float(candle.get("open"), close)
    ts = candle.get("time")
    dt_et = candle.get("dt_et")
    if dt_et is None:
        dt_et = _to_et_dt(ts)
    if high is None or low is None or close is None:
        return None
    if high < low:
        high, low = low, high
    volume = _safe_float(candle.get("volume"), 0.0)
    return {
        "time": ts,
        "dt_et": dt_et,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": max(0.0, volume),
    }


def _session_filter(candles: List[dict], market_session: str = "Regular", is_crypto: bool = False) -> List[dict]:
    if is_crypto:
        return candles
    if (market_session or "").strip().lower() in {"extended", "24h", "crypto24", "all"}:
        return candles
    return [c for c in candles if _is_regular_session(c.get("dt_et"))]


def _lookback_filter(candles: List[dict], lookback_days: int) -> List[dict]:
    if not candles:
        return []
    if not lookback_days or lookback_days <= 0:
        return candles
    latest_dt = max((c.get("dt_et") for c in candles if c.get("dt_et")), default=None)
    if latest_dt is None:
        return candles
    cutoff = latest_dt - timedelta(days=int(lookback_days))
    return [c for c in candles if c.get("dt_et") is None or c["dt_et"] >= cutoff]


def _candidate_score(candle: dict, idx: int, total: int, side: str, avg_volume: float, average_range: float) -> float:
    volume = max(0.0, _safe_float(candle.get("volume"), 0.0))
    open_ = _safe_float(candle.get("open"), candle["close"])
    close = _safe_float(candle.get("close"), candle["close"])
    high = _safe_float(candle.get("high"), candle["close"])
    low = _safe_float(candle.get("low"), candle["close"])

    rng = max(high - low, 1e-9)
    body = abs(close - open_)
    body_ratio = min(1.0, body / rng)

    if side == "support":
        rejection = max(0.0, (min(open_, close) - low) / rng)
    else:
        rejection = max(0.0, (high - max(open_, close)) / rng)

    if avg_volume > 0:
        vol_ratio = volume / avg_volume
        vol_weight = min(3.0, vol_ratio)
    else:
        vol_weight = 0.5 if volume <= 0 else 1.0

    if average_range > 0:
        range_weight = min(2.0, rng / average_range)
    else:
        range_weight = 1.0

    recency_weight = 1.0 + (idx / max(1, total - 1)) * 0.75
    zero_volume_penalty = 0.35 if volume <= 0 else 1.0

    base = 1.0 + (rejection * 1.5) + (body_ratio * 0.5)
    return base * recency_weight * max(0.75, vol_weight) * max(0.75, range_weight) * zero_volume_penalty


def _cluster_levels(candidates: List[dict], tolerance: float) -> List[dict]:
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda x: x["price"])
    clusters: List[dict] = []

    for cand in ordered:
        if not clusters:
            clusters.append({
                "price_sum": cand["price"] * cand["score"],
                "weight_sum": cand["score"],
                "touches": 1,
                "score": cand["score"],
                "first_time": cand.get("time"),
                "last_time": cand.get("time"),
                "kind": cand["kind"],
                "prices": [cand["price"]],
                "volumes": [cand.get("volume", 0.0)],
            })
            continue

        center = clusters[-1]["price_sum"] / max(clusters[-1]["weight_sum"], 1e-9)
        if abs(cand["price"] - center) <= tolerance:
            cluster = clusters[-1]
            cluster["price_sum"] += cand["price"] * cand["score"]
            cluster["weight_sum"] += cand["score"]
            cluster["touches"] += 1
            cluster["score"] += cand["score"]
            cluster["last_time"] = cand.get("time") or cluster["last_time"]
            cluster["prices"].append(cand["price"])
            cluster["volumes"].append(cand.get("volume", 0.0))
        else:
            clusters.append({
                "price_sum": cand["price"] * cand["score"],
                "weight_sum": cand["score"],
                "touches": 1,
                "score": cand["score"],
                "first_time": cand.get("time"),
                "last_time": cand.get("time"),
                "kind": cand["kind"],
                "prices": [cand["price"]],
                "volumes": [cand.get("volume", 0.0)],
            })

    out: List[dict] = []
    for cluster in clusters:
        price = cluster["price_sum"] / max(cluster["weight_sum"], 1e-9)
        total_volume = sum(v for v in cluster["volumes"] if v is not None)
        avg_volume = total_volume / max(1, len(cluster["volumes"]))
        score = cluster["score"] + math.log1p(max(0.0, total_volume))
        out.append({
            "price": price,
            "touches": cluster["touches"],
            "score": round(score, 3),
            "kind": cluster["kind"],
            "first_time": cluster["first_time"],
            "last_time": cluster["last_time"],
            "volume": total_volume,
            "avg_volume": avg_volume,
            "validated": cluster["touches"] >= 2 or score >= 2.0,
            "width": max(cluster["prices"]) - min(cluster["prices"]),
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def _extract_context(
    price_history: List[dict],
    current_price: Optional[float] = None,
    lookback_days: int = 20,
    market_session: str = "Regular",
    is_crypto: bool = False,
    pivot_strength: int = 2,
) -> dict:
    candles = []
    if price_history:
        # O(K) optimization: Iterate backwards until we cross the cutoff date
        last_candle = _normalize_candle(price_history[-1])
        latest_dt = last_candle.get("dt_et") if last_candle else None
        
        if latest_dt and lookback_days > 0:
            from datetime import timedelta
            cutoff = latest_dt - timedelta(days=int(lookback_days))
        else:
            cutoff = None
            
        for raw_candle in reversed(price_history):
            normalized = _normalize_candle(raw_candle)
            if not normalized:
                continue
                
            if cutoff and normalized.get("dt_et") and normalized["dt_et"] < cutoff:
                break
                
            candles.append(normalized)
            
        candles.reverse()

    candles = _session_filter(candles, market_session=market_session, is_crypto=is_crypto)
    candles = [c for c in candles if c.get("high") is not None and c.get("low") is not None and c.get("close") is not None]

    if not candles:
        return {
            "candles": [],
            "support_levels": [],
            "resistance_levels": [],
            "nearest_support": None,
            "nearest_resistance": None,
            "price_tolerance": 0.0,
            "average_volume": 0.0,
            "average_range": 0.0,
            "lookback_days": lookback_days,
            "market_session": market_session,
            "is_crypto": is_crypto,
        }

    if current_price is None:
        current_price = candles[-1]["close"]
    current_price = _safe_float(current_price, candles[-1]["close"])

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]
    ranges = [max(0.0, c["high"] - c["low"]) for c in candles]

    average_volume = sum(volumes) / max(1, len(volumes))
    average_range = sum(ranges) / max(1, len(ranges))
    price_tolerance = max(current_price * 0.0025, average_range * 0.5, 0.01)

    support_candidates = []
    resistance_candidates = []
    strength = max(1, min(10, int(pivot_strength or 2)))
    window = strength if len(candles) >= (strength * 2 + 5) else 1

    for idx in range(window, len(candles) - window):
        candle = candles[idx]
        low_window = lows[idx - window: idx + window + 1]
        high_window = highs[idx - window: idx + window + 1]
        local_low = min(low_window)
        local_high = max(high_window)
        score = _candidate_score(candle, idx, len(candles), "support", average_volume, average_range)
        if candle["low"] <= local_low + (price_tolerance * 0.25):
            support_candidates.append({
                "price": candle["low"],
                "score": score,
                "kind": "support",
                "time": candle.get("dt_et"),
                "volume": candle.get("volume", 0.0),
            })
        score = _candidate_score(candle, idx, len(candles), "resistance", average_volume, average_range)
        if candle["high"] >= local_high - (price_tolerance * 0.25):
            resistance_candidates.append({
                "price": candle["high"],
                "score": score,
                "kind": "resistance",
                "time": candle.get("dt_et"),
                "volume": candle.get("volume", 0.0),
            })

    support_levels = _cluster_levels(support_candidates, price_tolerance)
    resistance_levels = _cluster_levels(resistance_candidates, price_tolerance)

    support_levels = [lvl for lvl in support_levels if lvl["validated"]] or support_levels
    resistance_levels = [lvl for lvl in resistance_levels if lvl["validated"]] or resistance_levels

    def _nearest(levels: List[dict], *, above: bool) -> Optional[dict]:
        eligible = [lvl for lvl in levels if (lvl["price"] >= current_price if above else lvl["price"] <= current_price)]
        if not eligible:
            return None
        eligible.sort(key=lambda lvl: (abs(lvl["price"] - current_price), -lvl["score"]))
        return eligible[0]

    nearest_support = _nearest(support_levels, above=False)
    nearest_resistance = _nearest(resistance_levels, above=True)

    return {
        "candles": candles,
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "current_price": current_price,
        "price_tolerance": price_tolerance,
        "average_volume": average_volume,
        "average_range": average_range,
        "lookback_days": lookback_days,
        "market_session": market_session,
        "is_crypto": is_crypto,
    }


def _touches_zone(candle: dict, zone_price: float, tolerance: float) -> bool:
    return candle["low"] <= zone_price + tolerance and candle["high"] >= zone_price - tolerance


def _bullish_confirmation(candle: dict, prev: Optional[dict] = None) -> bool:
    close = candle["close"]
    open_ = candle["open"]
    if close <= open_:
        return False
    if prev and close < prev["close"]:
        return False
    return True


def _bearish_confirmation(candle: dict, prev: Optional[dict] = None) -> bool:
    close = candle["close"]
    open_ = candle["open"]
    if close >= open_:
        return False
    if prev and close > prev["close"]:
        return False
    return True


def _zone_reason(zone: Optional[dict], default_label: str) -> str:
    if not zone:
        return default_label
    price = zone["price"]
    touches = zone["touches"]
    score = zone["score"]
    return f"{default_label} at ${price:.2f} ({touches} touches, score {score:.2f})"


def build_swing_context(
    price_history: List[dict],
    current_price: Optional[float] = None,
    lookback_days: int = 20,
    market_session: str = "Regular",
    is_crypto: bool = False,
) -> dict:
    return _extract_context(
        price_history=price_history,
        current_price=current_price,
        lookback_days=lookback_days,
        market_session=market_session,
        is_crypto=is_crypto,
    )


def detect_swing_entry(
    price_history: List[dict],
    current_price: Optional[float] = None,
    entry_mode: str = "bounce_retest",
    lookback_days: int = 20,
    market_session: str = "Regular",
    is_crypto: bool = False,
    pivot_strength: int = 2,
) -> dict:
    ctx = _extract_context(
        price_history=price_history,
        current_price=current_price,
        lookback_days=lookback_days,
        market_session=market_session,
        is_crypto=is_crypto,
        pivot_strength=pivot_strength,
    )
    candles = ctx["candles"]
    if len(candles) < 2:
        return {"action": "HOLD", "reason": "Swing Points: not enough candles", "context": ctx}

    latest = candles[-1]
    prev = candles[-2]
    tolerance = ctx["price_tolerance"]
    action = "HOLD"
    reason = "Swing Points: no valid setup"

    support = ctx["nearest_support"]
    resistance = ctx["nearest_resistance"]

    if entry_mode == "breakout_retest":
        if resistance and _touches_zone(latest, resistance["price"], tolerance):
            broke_up = prev["close"] <= resistance["price"] + tolerance and latest["close"] > resistance["price"] + tolerance
            if broke_up and _bullish_confirmation(latest, prev):
                action = "BUY"
                reason = f"Swing breakout retest confirmed: {_zone_reason(resistance, 'Resistance reclaim')}."
        if action == "HOLD" and support and _touches_zone(latest, support["price"], tolerance):
            broke_down = prev["close"] >= support["price"] - tolerance and latest["close"] < support["price"] - tolerance
            if broke_down and _bearish_confirmation(latest, prev):
                action = "SELL"
                reason = f"Swing breakdown retest confirmed: {_zone_reason(support, 'Support loss')}."
    else:
        if support and _touches_zone(latest, support["price"], tolerance):
            if latest["close"] > support["price"] + (tolerance * 0.1) and _bullish_confirmation(latest, prev):
                action = "BUY"
                reason = f"Swing bounce confirmed: {_zone_reason(support, 'Support bounce')}."
        if action == "HOLD" and resistance and _touches_zone(latest, resistance["price"], tolerance):
            if latest["close"] < resistance["price"] - (tolerance * 0.1) and _bearish_confirmation(latest, prev):
                action = "SELL"
                reason = f"Swing rejection confirmed: {_zone_reason(resistance, 'Resistance rejection')}."

    if action == "HOLD":
        if support and latest["low"] <= support["price"] + tolerance and latest["close"] > support["price"]:
            reason = f"Swing waiting for bounce confirmation at ${support['price']:.2f}"
        elif resistance and latest["high"] >= resistance["price"] - tolerance and latest["close"] < resistance["price"]:
            reason = f"Swing waiting for rejection confirmation at ${resistance['price']:.2f}"

    return {
        "action": action,
        "reason": reason,
        "context": ctx,
    }


def build_swing_exit(
    price_history: List[dict],
    current_price: Optional[float] = None,
    is_long: bool = True,
    lookback_days: int = 20,
    market_session: str = "Regular",
    is_crypto: bool = False,
) -> dict:
    ctx = _extract_context(
        price_history=price_history,
        current_price=current_price,
        lookback_days=lookback_days,
        market_session=market_session,
        is_crypto=is_crypto,
    )
    if not ctx["candles"]:
        return {
            "take_profit": None,
            "stop_loss": None,
            "tp_zone": None,
            "sl_zone": None,
            "context": ctx,
            "reason": "Swing Points: not enough candles",
        }

    tp_zone = ctx["nearest_resistance"] if is_long else ctx["nearest_support"]
    sl_zone = ctx["nearest_support"] if is_long else ctx["nearest_resistance"]

    if tp_zone is None and ctx["resistance_levels"]:
        tp_zone = ctx["resistance_levels"][0]
    if sl_zone is None and ctx["support_levels"]:
        sl_zone = ctx["support_levels"][0]

    tp = tp_zone["price"] if tp_zone else None
    sl = sl_zone["price"] if sl_zone else None

    return {
        "take_profit": tp,
        "stop_loss": sl,
        "tp_zone": tp_zone,
        "sl_zone": sl_zone,
        "context": ctx,
        "reason": (
            f"Swing TP {tp:.2f} / SL {sl:.2f}" if tp is not None and sl is not None
            else "Swing Points: levels unavailable"
        ),
    }


def format_swing_preview(
    symbol: str,
    swing_exit: dict,
    current_price: float,
    is_long: bool = True,
    is_option: bool = False,
) -> str:
    ctx = (swing_exit or {}).get("context") or {}
    lookback = ctx.get("lookback_days", 20)
    session = ctx.get("market_session", "Regular")
    tp_zone = (swing_exit or {}).get("tp_zone") or {}
    sl_zone = (swing_exit or {}).get("sl_zone") or {}
    label = "CALL" if is_long else "PUT"
    tp_label = "Resistance" if is_long else "Support"
    sl_label = "Support" if is_long else "Resistance"
    tp = swing_exit.get("take_profit")
    sl = swing_exit.get("stop_loss")

    price_str = f"${_safe_float(current_price):.2f}"
    tp_str = f"${tp:.2f}" if tp is not None else "N/A"
    sl_str = f"${sl:.2f}" if sl is not None else "N/A"

    lines = [
        f"🔮 Live Preview: {symbol} (Swing Points | {lookback}D {session}) | Price: {price_str}",
        f"Entry Side: {label}",
        f"Take Profit ({tp_label}): {tp_str}",
        f"Stop Loss ({sl_label}): {sl_str}",
    ]
    if tp_zone:
        lines.append(f"TP Zone Strength: {tp_zone.get('score', 0):.2f} / {tp_zone.get('touches', 0)} touches")
    if sl_zone:
        lines.append(f"SL Zone Strength: {sl_zone.get('score', 0):.2f} / {sl_zone.get('touches', 0)} touches")
    return " | ".join(lines)


def _prior_day_ohlc(candles: List[dict]) -> Optional[dict]:
    return prior_day_ohlc_from_history(candles)


def build_entry_signal(
    price_history: List[dict],
    current_price: Optional[float] = None,
    entry_mode: str = "indicator",
    lookback_days: int = 20,
    market_session: str = "Regular",
    is_crypto: bool = False,
    atr: Optional[float] = None,
    custom_buy_price: float = 0.0,
    entry_atr_mult: float = 1.0,
    entry_pct_move: float = 1.0,
    entry_atr_style: str = "pullback_reclaim",
    entry_pct_style: str = "dip_reclaim",
    swing_entry_mode: str = "bounce_retest",
    desired_direction: Optional[str] = None,
    high_low_buffer_pct: float = 0.0,
) -> dict:
    entry_mode = entry_mode or "indicator"
    lightweight_modes = {
        "custom_price", "atr", "hybrid_atr", "percentage", "hybrid_pct",
        "pivot_points", "hybrid_pivot", "high_low", "hybrid_high_low",
    }
    if entry_mode in lightweight_modes:
        candles = []
        for candle in price_history or []:
            normalized = _normalize_candle(candle)
            if normalized:
                candles.append(normalized)
        candles = _session_filter(candles, market_session=market_session, is_crypto=is_crypto)
        candles = _lookback_filter(candles, lookback_days=lookback_days)
        ranges = [max(0.0, c["high"] - c["low"]) for c in candles]
        average_range = sum(ranges) / max(1, len(ranges))
        if current_price is None and candles:
            current_price = candles[-1]["close"]
        current_price = _safe_float(current_price, candles[-1]["close"] if candles else 0.0)
        ctx = {
            "candles": candles,
            "support_levels": [],
            "resistance_levels": [],
            "nearest_support": None,
            "nearest_resistance": None,
            "current_price": current_price,
            "price_tolerance": max(current_price * 0.0025, average_range * 0.5, 0.01),
            "average_volume": 0.0,
            "average_range": average_range,
            "lookback_days": lookback_days,
            "market_session": market_session,
            "is_crypto": is_crypto,
        }
    else:
        ctx = _extract_context(
            price_history=price_history,
            current_price=current_price,
            lookback_days=lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
        )
    candles = ctx["candles"]
    if len(candles) < 2:
        return {
            "action": "HOLD",
            "reason": "Entry strategy: not enough candles",
            "context": ctx,
        }

    latest = candles[-1]
    prev = candles[-2]
    current_price = _safe_float(current_price, latest["close"])
    action = "HOLD"
    reason = "Entry strategy: waiting for confirmation"
    trigger_price = None
    label = entry_mode
    desired_direction = str(desired_direction or "").upper()
    structure_direction = "NONE"

    def _set_signal(next_action: str, next_reason: str, next_trigger=None):
        nonlocal action, reason, trigger_price, structure_direction
        next_action = str(next_action or "HOLD").upper()
        next_direction = "BULLISH" if next_action == "BUY" else "BEARISH" if next_action == "SELL" else "NONE"
        if desired_direction in {"BULLISH", "BEARISH"} and next_direction != desired_direction:
            action = "HOLD"
            structure_direction = next_direction
            reason = f"{next_direction.title()} structure ignored; waiting for {desired_direction.title()} setup"
            trigger_price = next_trigger
            return
        action = next_action
        structure_direction = next_direction
        reason = next_reason
        trigger_price = next_trigger

    if entry_mode == "custom_price":
        trigger_price = _safe_float(custom_buy_price, 0.0)
        if trigger_price > 0:
            if desired_direction == "BEARISH":
                if current_price >= trigger_price:
                    _set_signal("SELL", f"Custom PUT price entry confirmed at/above ${trigger_price:.2f}", trigger_price)
            elif current_price <= trigger_price:
                _set_signal("BUY", f"Custom price entry confirmed at ${trigger_price:.2f}", trigger_price)

    elif entry_mode in ("atr", "hybrid_atr"):
        atr_val = _safe_float(atr, 0.0)
        if atr_val <= 0:
            atr_val = max(ctx.get("average_range", 0.0), current_price * 0.01)
        offset = max(atr_val * max(entry_atr_mult, 0.1), current_price * 0.001)
        if entry_atr_style == "breakout_momentum":
            long_level = prev["close"] + offset
            short_level = prev["close"] - offset
            if latest["high"] >= long_level and latest["close"] >= long_level and latest["close"] >= latest["open"]:
                _set_signal("BUY", f"ATR breakout entry confirmed above ${long_level:.2f} (ATR x {entry_atr_mult:.2f})", long_level)
            elif latest["low"] <= short_level and latest["close"] <= short_level and latest["close"] <= latest["open"]:
                _set_signal("SELL", f"ATR breakdown entry confirmed below ${short_level:.2f} (ATR x {entry_atr_mult:.2f})", short_level)
        else:
            long_level = prev["close"] - offset
            short_level = prev["close"] + offset
            if latest["low"] <= long_level and latest["close"] >= long_level and latest["close"] >= latest["open"]:
                _set_signal("BUY", f"ATR pullback entry confirmed at ${long_level:.2f} (ATR x {entry_atr_mult:.2f})", long_level)
            elif latest["high"] >= short_level and latest["close"] <= short_level and latest["close"] <= latest["open"]:
                _set_signal("SELL", f"ATR extension entry confirmed at ${short_level:.2f} (ATR x {entry_atr_mult:.2f})", short_level)

    elif entry_mode in ("percentage", "hybrid_pct"):
        pct = max(entry_pct_move, 0.01)
        if entry_pct_style == "breakout_momentum":
            long_level = prev["close"] * (1.0 + (pct / 100.0))
            short_level = prev["close"] * (1.0 - (pct / 100.0))
            if latest["high"] >= long_level and latest["close"] >= long_level and latest["close"] >= latest["open"]:
                _set_signal("BUY", f"Percentage breakout entry confirmed above ${long_level:.2f} ({pct:.2f}%)", long_level)
            elif latest["low"] <= short_level and latest["close"] <= short_level and latest["close"] <= latest["open"]:
                _set_signal("SELL", f"Percentage breakdown entry confirmed below ${short_level:.2f} ({pct:.2f}%)", short_level)
        else:
            long_level = prev["close"] * (1.0 - (pct / 100.0))
            short_level = prev["close"] * (1.0 + (pct / 100.0))
            if latest["low"] <= long_level and latest["close"] >= long_level and latest["close"] >= latest["open"]:
                _set_signal("BUY", f"Percentage pullback entry confirmed at ${long_level:.2f} ({pct:.2f}%)", long_level)
            elif latest["high"] >= short_level and latest["close"] <= short_level and latest["close"] <= latest["open"]:
                _set_signal("SELL", f"Percentage extension entry confirmed at ${short_level:.2f} ({pct:.2f}%)", short_level)

    elif entry_mode in ("pivot_points", "hybrid_pivot"):
        prior = _prior_day_ohlc(candles)
        if prior:
            pivot = (prior["high"] + prior["low"] + prior["close"]) / 3.0
            r1 = (2 * pivot) - prior["low"]
            s1 = (2 * pivot) - prior["high"]
            tolerance = ctx.get("price_tolerance", 0.0)
            trigger_price = pivot
            if (latest["low"] <= s1 + tolerance and latest["close"] > s1) or (latest["close"] > pivot and prev["close"] <= pivot):
                _set_signal("BUY", f"Pivot reclaim confirmed above support at ${s1:.2f}", s1)
            elif (latest["high"] >= r1 - tolerance and latest["close"] < r1) or (latest["close"] < pivot and prev["close"] >= pivot):
                _set_signal("SELL", f"Pivot rejection confirmed below resistance at ${r1:.2f}", r1)

    elif entry_mode in ("high_low", "hybrid_high_low"):
        levels = prior_day_levels_from_history(candles, buffer_pct=high_low_buffer_pct)
        if levels:
            high_trigger = levels["high_trigger"]
            low_trigger = levels["low_trigger"]
            trigger_price = high_trigger
            buffer_note = f" with {levels['buffer_pct']:.2f}% buffer" if levels["buffer_pct"] else ""
            if latest["close"] > high_trigger and prev["close"] <= high_trigger:
                _set_signal(
                    "BUY",
                    f"Prior day high breakout confirmed above ${high_trigger:.2f}{buffer_note} (PDH ${levels['high']:.2f})",
                    high_trigger,
                )
            elif latest["close"] < low_trigger and prev["close"] >= low_trigger:
                _set_signal(
                    "SELL",
                    f"Prior day low breakdown confirmed below ${low_trigger:.2f}{buffer_note} (PDL ${levels['low']:.2f})",
                    low_trigger,
                )

    elif entry_mode in ("swing_points", "hybrid_swing"):
        swing_decision = detect_swing_entry(
            price_history,
            current_price=current_price,
            entry_mode=swing_entry_mode,
            lookback_days=lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
        )
        action = swing_decision.get("action", "HOLD")
        reason = swing_decision.get("reason", reason)
        ctx = swing_decision.get("context", ctx)
        structure_direction = "BULLISH" if action == "BUY" else "BEARISH" if action == "SELL" else "NONE"
        if desired_direction in {"BULLISH", "BEARISH"} and structure_direction != "NONE" and structure_direction != desired_direction:
            action = "HOLD"
            reason = f"{structure_direction.title()} swing setup ignored; waiting for {desired_direction.title()} setup"

    return {
        "action": action,
        "reason": reason,
        "trigger_price": trigger_price,
        "structure_direction": structure_direction,
        "mode": label,
        "context": ctx,
    }


def format_entry_preview(
    symbol: str,
    entry_signal: dict,
    current_price: float,
    entry_mode: str = "indicator",
    is_option: bool = False,
    high_low_buffer_pct: float = 0.0,
) -> str:
    ctx = (entry_signal or {}).get("context") or {}
    current_price = _safe_float(current_price)
    label = entry_mode.replace("_", " ").title()
    if entry_mode == "custom_price":
        label = "Custom Price"
    elif entry_mode in ("pivot_points", "hybrid_pivot"):
        label = "Pivot Points"
    elif entry_mode in ("high_low", "hybrid_high_low"):
        label = "Prior Day High/Low"
    elif entry_mode in ("atr", "hybrid_atr"):
        label = "ATR Entry"
    elif entry_mode in ("percentage", "hybrid_pct"):
        label = "Percentage Entry"
    elif entry_mode in ("swing_points", "hybrid_swing"):
        label = "Swing Points"

    lines = [f"🔮 Live Preview: {symbol} (Entry: {label}) | Price: ${current_price:.2f}"]

    trigger = (entry_signal or {}).get("trigger_price")
    if entry_mode == "custom_price":
        lines.append(f"Target Entry: ${_safe_float(trigger):.2f}" if trigger is not None else "Target Entry: N/A")
    elif entry_mode in ("atr", "hybrid_atr"):
        atr = ctx.get("average_range", 0.0) or 0.0
        lines.append((entry_signal or {}).get("reason", "ATR entry waiting"))
        lines.append(f"ATR Proxy: ${atr:.2f}")
        if trigger is not None:
            lines.append(f"Trigger Level: ${_safe_float(trigger):.2f}")
    elif entry_mode in ("percentage", "hybrid_pct"):
        lines.append((entry_signal or {}).get("reason", "Percentage entry waiting"))
        if trigger is not None:
            lines.append(f"Trigger Level: ${_safe_float(trigger):.2f}")
    elif entry_mode in ("pivot_points", "hybrid_pivot"):
        prior = _prior_day_ohlc(ctx.get("candles", []))
        if prior:
            pivot = (prior["high"] + prior["low"] + prior["close"]) / 3.0
            r1 = (2 * pivot) - prior["low"]
            s1 = (2 * pivot) - prior["high"]
            lines.append(f"Pivot: ${pivot:.2f} | S1: ${s1:.2f} | R1: ${r1:.2f}")
            lines.append((entry_signal or {}).get("reason", "Pivot entry waiting"))
        else:
            lines.append("Awaiting prior day data...")
    elif entry_mode in ("high_low", "hybrid_high_low"):
        levels = prior_day_levels_from_history(ctx.get("candles", []), buffer_pct=high_low_buffer_pct)
        if levels:
            lines.append(f"PDH: ${levels['high']:.2f} | PDL: ${levels['low']:.2f}")
            if levels["buffer_pct"]:
                lines.append(f"Triggers: Above ${levels['high_trigger']:.2f} | Below ${levels['low_trigger']:.2f} ({levels['buffer_pct']:.2f}% buffer)")
            lines.append((entry_signal or {}).get("reason", "High/Low entry waiting"))
        else:
            lines.append("Awaiting prior day data...")
    elif entry_mode in ("swing_points", "hybrid_swing"):
        swing_ctx = ctx or {}
        support = swing_ctx.get("nearest_support")
        resistance = swing_ctx.get("nearest_resistance")
        if support:
            lines.append(f"Support: ${support['price']:.2f} ({support.get('touches', 0)} touches)")
        if resistance:
            lines.append(f"Resistance: ${resistance['price']:.2f} ({resistance.get('touches', 0)} touches)")
        lines.append((entry_signal or {}).get("reason", "Swing entry waiting"))
    else:
        bull = ctx.get("bullish_count")
        bear = ctx.get("bearish_count")
        if bull is not None or bear is not None:
            lines.append(f"Signals: {bull or 0} Bullish / {bear or 0} Bearish")
        lines.append((entry_signal or {}).get("reason", "Indicator entry waiting"))

    return " | ".join(lines)
