"""Pure strategy gates for the analysis-only market Screener.

The module deliberately contains no broker, persistence, or network code. It
turns the existing completed-candle technical result and the independent news
watch result into auditable strategy qualifications.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any


SCREENING_STRATEGY_MODES = {"technical", "event", "combined", "discovery"}
DEFAULT_SCREENING_STRATEGY_MODE = "combined"
SCREENING_STRATEGY_LABELS = {
    "technical": "Indicator Only",
    "event": "Event-Driven Only",
    "combined": "Combined · Require Both Indicator + Event",
    "discovery": "Discovery · Either Qualifies",
}


def normalize_screening_strategy_mode(value: Any, default: str = DEFAULT_SCREENING_STRATEGY_MODE) -> str:
    """Normalize persisted/UI strategy names without silently enabling execution."""
    aliases = {
        "technical_only": "technical",
        "indicator": "technical",
        "event_driven": "event",
        "news": "event",
        "hybrid": "combined",
        "both": "combined",
        "any": "discovery",
        "either": "discovery",
    }
    candidate = str(value or "").strip().lower().replace("-", "_")
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in SCREENING_STRATEGY_MODES else default


def screening_strategy_label(value: Any) -> str:
    """Return one canonical label for scan logs and delivered alerts."""
    mode = normalize_screening_strategy_mode(value)
    return SCREENING_STRATEGY_LABELS.get(mode, SCREENING_STRATEGY_LABELS[DEFAULT_SCREENING_STRATEGY_MODE])


def screening_strategy_logic(value: Any) -> str:
    """Describe the exact entry qualification rule selected by the user."""
    mode = normalize_screening_strategy_mode(value)
    return {
        "technical": "Indicator-only entry rule: indicator qualification is required; the event leg is not used.",
        "event": "Event-driven entry rule: a current qualifying catalyst is required; the indicator leg is not required.",
        "combined": "Combined entry rule: both indicator and event qualifications must be true in the same direction.",
        "discovery": "Discovery entry rule: either leg may qualify, but an explicit opposing qualified event blocks the setup.",
    }.get(mode, "Combined entry rule: both indicator and event qualifications must be true in the same direction.")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _published_ts(article: dict, now_ts: float) -> float:
    raw = _safe_float(article.get("published_ts"), 0.0)
    if raw > 0:
        return raw
    published_at = str(article.get("published_at") or "").strip()
    if published_at:
        try:
            return datetime.fromisoformat(published_at.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    age_hours = _safe_float(article.get("age_hours"), 0.0)
    return max(0.0, now_ts - age_hours * 3600.0) if age_hours > 0 else 0.0


def assess_event_catalyst(
    news_watch: dict | None,
    *,
    now_ts: float | None = None,
    max_age_days: int = 10,
    min_bullish_pct: float = 65.0,
    max_bearish_pct: float = 35.0,
    min_confidence: float = 0.55,
) -> dict:
    """Return a conservative, explainable event qualification.

    A positive/negative tone by itself is not enough. The latest directly
    relevant article must be recent, the feed must be initialized/current, and
    the aggregate classification must clear a confidence and sentiment gate.
    """
    now = float(now_ts or time.time())
    watch = news_watch if isinstance(news_watch, dict) else {}
    latest = watch.get("latest") if isinstance(watch.get("latest"), dict) else {}
    status = str(watch.get("status") or "waiting").lower()
    try:
        age_limit_days = max(1, min(30, int(max_age_days)))
    except (TypeError, ValueError):
        age_limit_days = 10
    try:
        article_count = max(0, int(_safe_float(watch.get("article_count"), 0.0)))
    except (TypeError, ValueError):
        article_count = 0
    latest_summary = str(
        watch.get("latest_summary")
        or latest.get("summary")
        or latest.get("excerpt")
        or ""
    ).strip()
    collected_news_summary = str(
        watch.get("ai_analysis")
        or watch.get("previous_summary")
        or "No collected news summary is available."
    ).strip()
    result = {
        "eligible": False,
        "direction": "NEUTRAL",
        "status": "WAITING" if not watch else status.upper(),
        "reason": "Waiting for a current, directly relevant catalyst.",
        "catalyst_id": "",
        "headline": str(latest.get("title") or ""),
        "source": str(latest.get("source") or ""),
        "published_at": latest.get("published_at") or "",
        "latest_summary": latest_summary,
        "collected_news_summary": collected_news_summary,
        "previous_news_summary": str(watch.get("previous_summary") or "").strip(),
        "ai_analysis": str(watch.get("ai_analysis") or "").strip(),
        "analysis_direction": str(watch.get("sentiment_direction") or "NEUTRAL").upper(),
        "article_count": article_count,
        "collection_days": age_limit_days,
        "checked_at": watch.get("checked_at") or "",
        "age_hours": None,
        "sentiment_pct": _safe_float(watch.get("sentiment_pct"), 50.0),
        "confidence": _safe_float(watch.get("sentiment_confidence"), 0.0),
        "impact": str(latest.get("impact") or "UNKNOWN").upper(),
        "new_count": int(_safe_float(watch.get("new_count"), 0.0)),
    }
    if status not in {"current", "degraded"} or not watch.get("initialized"):
        if status in {"stale", "unavailable"}:
            result["reason"] = "News data is stale or unavailable; event qualification is blocked."
        return result

    relevance = str(latest.get("relevance") or "DIRECT").upper()
    direction = str(watch.get("sentiment_direction") or "NEUTRAL").upper()
    published_ts = _published_ts(latest, now)
    age_hours = max(0.0, (now - published_ts) / 3600.0) if published_ts > 0 else None
    result["age_hours"] = round(age_hours, 1) if age_hours is not None else None
    result["direction"] = direction if direction in {"BULLISH", "BEARISH"} else "NEUTRAL"
    if latest.get("id"):
        result["catalyst_id"] = str(latest["id"])
    else:
        identity = "|".join((result["headline"], result["source"], str(published_ts)))
        result["catalyst_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    if not latest or not result["headline"]:
        result["reason"] = "No directly relevant recent headline is available."
        return result
    if relevance == "IRRELEVANT":
        result["reason"] = "The latest headline was classified as irrelevant to the ticker."
        return result
    if age_hours is None or age_hours > age_limit_days * 24:
        result["reason"] = f"The latest catalyst is older than {age_limit_days} days."
        return result
    if status == "degraded":
        result["reason"] = "News feed is degraded; waiting for a clean current refresh."
        return result
    if result["impact"] not in {"HIGH", "MEDIUM", "UNKNOWN"}:
        result["reason"] = "The latest headline does not meet the minimum catalyst-impact threshold."
        return result

    confidence = result["confidence"]
    sentiment_pct = result["sentiment_pct"]
    if direction == "BULLISH" and sentiment_pct >= min_bullish_pct and confidence >= min_confidence:
        result["eligible"] = True
        result["reason"] = f"Recent bullish catalyst ({sentiment_pct:.0f}% sentiment, {confidence:.0%} confidence)."
    elif direction == "BEARISH" and sentiment_pct <= max_bearish_pct and confidence >= min_confidence:
        result["eligible"] = True
        result["reason"] = f"Recent bearish catalyst ({sentiment_pct:.0f}% sentiment, {confidence:.0%} confidence)."
    else:
        result["reason"] = "News tone or confidence did not clear the event qualification threshold."
    return result


def assess_swing_technical(scan: dict | None, settings: dict | None = None) -> dict:
    """Assess the existing technical direction for a slower swing profile.

    The selected indicator pool remains authoritative. This adds a lightweight
    price-structure guard using completed candles: bullish setups should hold
    above the 20-bar mean and bearish setups below it. It is intentionally
    conservative when history is missing and never treats missing data as a
    neutral pass.
    """
    scan = scan if isinstance(scan, dict) else {}
    settings = settings if isinstance(settings, dict) else {}
    direction = str(scan.get("direction") or "NEUTRAL").upper()
    signals = scan.get("signals") if isinstance(scan.get("signals"), dict) else {}
    selected = settings.get("indicators")
    selected_names = [str(name).strip() for name in selected if str(name).strip()] if isinstance(selected, list) else []
    # Keep the event leg independent. If a user previously selected the AI
    # news indicator in the technical pool, it must not count as technical
    # confirmation a second time in Combined mode.
    def is_news_indicator(name: str) -> bool:
        normalized = str(name or "").strip().lower()
        return "news" in normalized or normalized in {"botbulls4", "botbulls4 news ai"}

    technical_names = [
        name for name in (selected_names or list(signals.keys()))
        if not is_news_indicator(name)
    ]
    technical_signal_values = [
        signals.get(name) for name in technical_names
        if isinstance(signals.get(name), dict) and signals.get(name, {}).get("enabled", True) is not False
    ]
    if selected_names and not technical_names:
        direction = "NEUTRAL"
    elif technical_signal_values:
        bullish_weight = sum(max(1, int(item.get("weight", 1) or 1)) for item in technical_signal_values if str(item.get("signal") or "").upper() == "BULLISH")
        bearish_weight = sum(max(1, int(item.get("weight", 1) or 1)) for item in technical_signal_values if str(item.get("signal") or "").upper() == "BEARISH")
        try:
            buy_threshold = max(1, int(settings.get("min_buy_signals", 1) or 1))
        except (TypeError, ValueError):
            buy_threshold = 1
        try:
            sell_threshold = max(1, int(settings.get("min_sell_signals", 1) or 1))
        except (TypeError, ValueError):
            sell_threshold = 1
        if bullish_weight >= buy_threshold and bearish_weight < sell_threshold:
            direction = "BULLISH"
        elif bearish_weight >= sell_threshold and bullish_weight < buy_threshold:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"
    elif selected_names:
        direction = "NEUTRAL"
    result = {
        "eligible": False,
        "direction": direction if direction in {"BULLISH", "BEARISH"} else "NEUTRAL",
        "status": "WAITING",
        "reason": "Waiting for a valid completed-candle indicator setup.",
        "indicator_reason": str(scan.get("reason") or ""),
        "price": _safe_float(scan.get("price_raw"), 0.0),
        "sma20": None,
        "sma50": None,
        "volume_ratio": None,
        "timeframe": str(scan.get("timeframe") or settings.get("timeframe") or ""),
        "technical_indicators": technical_names,
    }
    if str(scan.get("evaluation_status") or "").lower() != "valid":
        return result
    if direction not in {"BULLISH", "BEARISH"}:
        result["status"] = "NEUTRAL"
        result["reason"] = "Indicators are not directionally aligned."
        return result

    history = scan.get("price_history")
    rows = history if isinstance(history, list) else []
    closes = [_safe_float(row.get("close"), 0.0) for row in rows if isinstance(row, dict)]
    closes = [value for value in closes if value > 0]
    if len(closes) < 20 or result["price"] <= 0:
        result["reason"] = "Insufficient completed-candle history for the swing trend guard."
        return result

    sma20 = sum(closes[-20:]) / 20.0
    sma50 = sum(closes[-50:]) / 50.0 if len(closes) >= 50 else None
    result["sma20"] = round(sma20, 8)
    result["sma50"] = round(sma50, 8) if sma50 is not None else None
    volumes = [_safe_float(row.get("volume"), 0.0) for row in rows if isinstance(row, dict)]
    volumes = [value for value in volumes if value > 0]
    if len(volumes) >= 21:
        average_volume = sum(volumes[-21:-1]) / 20.0
        if average_volume > 0:
            result["volume_ratio"] = round(volumes[-1] / average_volume, 3)

    price = result["price"]
    if direction == "BULLISH":
        trend_pass = price >= sma20 and (sma50 is None or sma20 >= sma50)
        trend_text = "price is above the 20-bar mean and the trend stack is positive"
    else:
        trend_pass = price <= sma20 and (sma50 is None or sma20 <= sma50)
        trend_text = "price is below the 20-bar mean and the trend stack is negative"
    result["eligible"] = bool(trend_pass)
    result["status"] = "QUALIFIED" if trend_pass else "BLOCKED"
    result["reason"] = (
        f"Indicator setup qualified: {trend_text}."
        if trend_pass
        else f"Indicator setup blocked: {trend_text} is not true on the latest completed candle."
    )
    return result


def combine_strategy_qualification(
    mode: Any,
    technical: dict,
    event: dict,
) -> dict:
    """Apply the user-selected technical/event policy without side effects."""
    selected_mode = normalize_screening_strategy_mode(mode)
    technical_ok = bool(technical.get("eligible"))
    event_ok = bool(event.get("eligible"))
    technical_direction = str(technical.get("direction") or "NEUTRAL").upper()
    event_direction = str(event.get("direction") or "NEUTRAL").upper()
    same_direction = technical_direction == event_direction and technical_direction in {"BULLISH", "BEARISH"}

    if selected_mode == "technical":
        qualified = technical_ok
        direction = technical_direction if qualified else "NEUTRAL"
        reason = technical.get("reason") or "Indicator qualification is not ready."
    elif selected_mode == "event":
        qualified = event_ok
        direction = event_direction if qualified else "NEUTRAL"
        reason = event.get("reason") or "Event qualification is not ready."
    elif selected_mode == "discovery":
        # Discovery is OR, but an explicit opposing high-confidence event is a
        # risk veto for a technical entry rather than a second opinion to ignore.
        risk_veto = technical_ok and event_ok and not same_direction
        qualified = (technical_ok or event_ok) and not risk_veto
        direction = technical_direction if technical_ok else event_direction if event_ok else "NEUTRAL"
        reason = (
            "Discovery blocked: indicator and event directions conflict."
            if risk_veto
            else technical.get("reason") if technical_ok else event.get("reason") or "No discovery qualification is ready."
        )
    else:
        qualified = technical_ok and event_ok and same_direction
        direction = technical_direction if qualified else "NEUTRAL"
        if technical_ok and event_ok and not same_direction:
            reason = "Combined setup blocked: indicator and event directions conflict."
        elif not technical_ok:
            reason = f"Combined setup waiting on indicator confirmation: {technical.get('reason') or 'not ready.'}"
        elif not event_ok:
            reason = f"Combined setup waiting on a qualifying catalyst: {event.get('reason') or 'not ready.'}"
        else:
            reason = "Combined indicator and event setup qualified."

    return {
        "mode": selected_mode,
        "qualified": bool(qualified),
        "direction": direction,
        "technical_qualified": technical_ok,
        "event_qualified": event_ok,
        "same_direction": same_direction,
        "reason": reason,
    }
