"""
Official-source actual-value enrichment for the macro calendar.

The ForexFactory calendar feed used by the dashboard is good for schedule,
forecast, previous, and impact, but it often does not include actual values.
This module enriches the highest-value USD releases with official/public data.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import re
import time
from typing import Any
from urllib import request


_CACHE: dict[str, Any] = {
    "bls": None,
    "bls_at": 0.0,
    "census_retail": None,
    "census_retail_at": 0.0,
    "fred_claims": None,
    "fred_claims_at": 0.0,
}
_TTL_SECONDS = 30 * 60


BLS_SERIES = {
    "CPI_MOM": "CUSR0000SA0",
    "CPI_YY": "CUUR0000SA0",
    "CORE_CPI_MOM": "CUSR0000SA0L1E",
    "CORE_CPI_YY": "CUUR0000SA0L1E",
    "PPI_MOM": "WPSFD4",
    "PPI_YY": "WPSFD4",
    # Common calendar "Core PPI" proxy: final demand finished goods less foods and energy.
    "CORE_PPI_MOM": "WPSFD4131",
    "CORE_PPI_YY": "WPSFD4131",
}


def _urlopen_text(url: str, timeout: int = 12, data: bytes | None = None, headers: dict | None = None) -> str:
    req = request.Request(url, data=data, headers=headers or {"User-Agent": "BotBulls/2.0"})
    with request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current / previous) - 1.0) * 100.0


def _fmt_pct(value: float | None) -> str | None:
    if value is None:
        return None
    rounded = round(value, 1)
    if abs(rounded) < 0.05:
        rounded = 0.0
    return f"{rounded:.1f}%"


def _fmt_pp(value: float | None) -> str | None:
    if value is None:
        return None
    rounded = round(value, 1)
    if abs(rounded) < 0.05:
        rounded = 0.0
    sign = "+" if rounded > 0 else ""
    return f"{sign}{rounded:.1f} pp"


def _fmt_k(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{round(value / 1000):.0f}K"


def _parse_event_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.upper() == "N/A":
        return None
    multiplier = 1.0
    if text.endswith("%"):
        text = text[:-1]
    elif text.upper().endswith("K"):
        multiplier = 1000.0
        text = text[:-1]
    elif text.upper().endswith("M"):
        multiplier = 1_000_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _period_key(row: dict[str, Any]) -> tuple[int, int] | None:
    year = row.get("year")
    period = row.get("period", "")
    if not str(period).startswith("M"):
        return None
    try:
        return int(year), int(str(period)[1:])
    except (TypeError, ValueError):
        return None


def _clean_bls_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = []
    for row in rows:
        if _period_key(row) is None:
            continue
        if _safe_float(row.get("value")) is None:
            continue
        valid.append(row)
    return sorted(valid, key=lambda item: _period_key(item), reverse=True)


def _fetch_bls_actuals() -> dict[str, dict[str, Any]]:
    now = time.time()
    if _CACHE["bls"] and now - _CACHE["bls_at"] < _TTL_SECONDS:
        return _CACHE["bls"]

    current_year = datetime.now(timezone.utc).year
    payload = json.dumps({
        "seriesid": sorted(set(BLS_SERIES.values())),
        "startyear": str(current_year - 1),
        "endyear": str(current_year),
    }).encode("utf-8")
    raw = _urlopen_text(
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "BotBulls/2.0"},
        timeout=15,
    )
    response = json.loads(raw)
    by_series = {}
    for series in response.get("Results", {}).get("series", []):
        rows = _clean_bls_rows(series.get("data", []))
        if rows:
            by_series[series.get("seriesID")] = rows

    def build_metric(series_id: str) -> dict[str, Any] | None:
        rows = by_series.get(series_id)
        if not rows or len(rows) < 2:
            return None
        latest = rows[0]
        previous = rows[1]
        latest_value = _safe_float(latest.get("value"))
        previous_value = _safe_float(previous.get("value"))
        latest_key = _period_key(latest)
        prior_year_row = None
        if latest_key:
            prior_year_row = next(
                (
                    row for row in rows
                    if _period_key(row) == (latest_key[0] - 1, latest_key[1])
                ),
                None,
            )
        return {
            "series_id": series_id,
            "period": f"{latest.get('periodName')} {latest.get('year')}",
            "index_level": latest_value,
            "previous_index_level": previous_value,
            "mom": _pct_change(latest_value, previous_value),
            "yoy": _pct_change(latest_value, _safe_float(prior_year_row.get("value")) if prior_year_row else None),
            "preliminary": any(
                footnote.get("code") == "P"
                for footnote in latest.get("footnotes", [])
                if isinstance(footnote, dict)
            ),
            "source": "BLS Public Data API",
            "source_url": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        }

    metrics = {
        name: build_metric(series_id)
        for name, series_id in BLS_SERIES.items()
    }
    metrics = {key: value for key, value in metrics.items() if value}
    _CACHE["bls"] = metrics
    _CACHE["bls_at"] = now
    return metrics


def _fetch_census_retail_actual() -> dict[str, Any] | None:
    now = time.time()
    if _CACHE["census_retail"] and now - _CACHE["census_retail_at"] < _TTL_SECONDS:
        return _CACHE["census_retail"]

    try:
        html = _urlopen_text("https://www.census.gov/retail/sales.html", timeout=12)
    except Exception:
        return None

    text = re.sub(r"\s+", " ", html)
    period_match = re.search(r"sales for ([A-Z][a-z]+ \d{4})", text)
    values = re.findall(r"\b(up|down|increased|decreased|rose|fell)\s+(-?\d+(?:\.\d+)?)\s+percent", text, flags=re.I)
    revised_match = re.search(
        r"percent change was revised from (?:up|down|increased|decreased|rose|fell)\s+(-?\d+(?:\.\d+)?)\s+percent.*?to (?:up|down|increased|decreased|rose|fell)\s+(-?\d+(?:\.\d+)?)\s+percent",
        text,
        flags=re.I,
    )
    if not values:
        return None

    def signed_value(direction: str, raw: str) -> float:
        value = float(raw)
        return -abs(value) if direction.lower() in {"down", "decreased", "fell"} else value

    mom = signed_value(values[0][0], values[0][1])
    yoy = signed_value(values[1][0], values[1][1]) if len(values) > 1 else None
    data = {
        "period": period_match.group(1) if period_match else None,
        "mom": mom,
        "yoy": yoy,
        "previous_revised": signed_value(values[0][0], revised_match.group(2)) if revised_match else None,
        "previous_original": signed_value(values[0][0], revised_match.group(1)) if revised_match else None,
        "source": "U.S. Census Retail Sales Release",
        "source_url": "https://www.census.gov/retail/sales.html",
    }
    _CACHE["census_retail"] = data
    _CACHE["census_retail_at"] = now
    return data


def _fetch_fred_initial_claims() -> dict[str, Any] | None:
    now = time.time()
    if _CACHE["fred_claims"] and now - _CACHE["fred_claims_at"] < _TTL_SECONDS:
        return _CACHE["fred_claims"]

    try:
        csv_text = _urlopen_text("https://fred.stlouisfed.org/graph/fredgraph.csv?id=ICSA", timeout=12)
    except Exception:
        return None
    rows = [
        row for row in csv.DictReader(io.StringIO(csv_text))
        if row.get("ICSA") not in {"", "."}
    ]
    if len(rows) < 2:
        return None
    latest = rows[-1]
    previous = rows[-2]
    latest_value = _safe_float(latest.get("ICSA"))
    previous_value = _safe_float(previous.get("ICSA"))
    data = {
        "series_id": "ICSA",
        "period": latest.get("observation_date"),
        "actual": latest_value,
        "previous": previous_value,
        "wow_change": latest_value - previous_value if latest_value is not None and previous_value is not None else None,
        "source": "FRED ICSA (DOL/ETA Initial Claims)",
        "source_url": "https://fred.stlouisfed.org/series/ICSA",
    }
    _CACHE["fred_claims"] = data
    _CACHE["fred_claims_at"] = now
    return data


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(event.get("date", "")))
    except Exception:
        return None


def _is_past_event(event: dict[str, Any]) -> bool:
    event_dt = _event_datetime(event)
    if not event_dt:
        return False
    now = datetime.now(event_dt.tzinfo or timezone.utc)
    return event_dt <= now


def _macro_read(title: str, actual_value: float | None, forecast_value: float | None) -> str | None:
    if actual_value is None or forecast_value is None:
        return None
    normalized = title.lower()
    surprise = actual_value - forecast_value
    if abs(surprise) < 0.05:
        return "In line with forecast; limited macro surprise."
    if "cpi" in normalized or "ppi" in normalized or "inflation" in normalized:
        return (
            "Hotter inflation than expected; rates-up pressure and potential risk-asset headwind."
            if surprise > 0
            else "Cooler inflation than expected; rates-down pressure and potential risk-on support."
        )
    if "retail sales" in normalized:
        return (
            "Consumer demand stronger than expected; growth-supportive but can pressure rates."
            if surprise > 0
            else "Consumer demand softer than expected; growth concern, rates-down bias."
        )
    if "unemployment claims" in normalized:
        return (
            "Claims higher than expected; labor cooling, growth concern, rates-down bias."
            if surprise > 0
            else "Claims lower than expected; labor firmer, growth-supportive but fewer cut hopes."
        )
    return "Actual differed from forecast; check asset reaction and rates."


def _surprise_bias(title: str, surprise: float | None) -> str:
    if surprise is None or abs(surprise) < 0.05:
        return "neutral"
    normalized = title.lower()
    if "cpi" in normalized or "ppi" in normalized or "inflation" in normalized:
        return "positive" if surprise < 0 else "negative"
    if "retail sales" in normalized:
        return "positive" if surprise > 0 else "negative"
    if "unemployment claims" in normalized:
        return "positive" if surprise < 0 else "negative"
    return "neutral"


def _apply_actual(event: dict[str, Any], actual: str, source: dict[str, Any], actual_raw: float | None = None) -> None:
    event["actual"] = actual
    event["actual_status"] = "available"
    event["actual_source"] = source.get("source")
    event["actual_source_url"] = source.get("source_url")
    event["actual_period"] = source.get("period")
    event["actual_raw"] = actual_raw
    forecast_value = _parse_event_number(event.get("forecast"))
    if actual_raw is not None and forecast_value is not None:
        surprise = actual_raw - forecast_value
        event["surprise_raw"] = surprise
        event["surprise"] = _fmt_pp(surprise) if "%" in str(event.get("forecast", "")) else _fmt_k(surprise)
        event["surprise_bias"] = _surprise_bias(str(event.get("title", "")), surprise)
        event["macro_read"] = _macro_read(str(event.get("title", "")), actual_raw, forecast_value)
    elif event.get("title"):
        event["macro_read"] = source.get("macro_read")


def _mark_unavailable(event: dict[str, Any], reason: str, source: str | None = None) -> None:
    event.setdefault("actual", "")
    event["actual_status"] = "not_available"
    event["actual_unavailable_reason"] = reason
    if source:
        event["actual_source"] = source


def enrich_macro_calendar_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bls = None
    retail = None
    claims = None

    enriched = []
    for original in events:
        event = dict(original)
        title = str(event.get("title", ""))
        normalized = title.lower()
        if not _is_past_event(event):
            enriched.append(event)
            continue
        if event.get("actual"):
            event["actual_status"] = "available"
            enriched.append(event)
            continue

        try:
            if "cpi" in normalized or "ppi" in normalized:
                bls = bls if bls is not None else _fetch_bls_actuals()
                metric_key = None
                if "core cpi" in normalized and "y/y" in normalized:
                    metric_key = "CORE_CPI_YY"
                elif "core cpi" in normalized:
                    metric_key = "CORE_CPI_MOM"
                elif "cpi" in normalized and "y/y" in normalized:
                    metric_key = "CPI_YY"
                elif "cpi" in normalized:
                    metric_key = "CPI_MOM"
                elif "core ppi" in normalized and "y/y" in normalized:
                    metric_key = "CORE_PPI_YY"
                elif "core ppi" in normalized:
                    metric_key = "CORE_PPI_MOM"
                elif "ppi" in normalized and "y/y" in normalized:
                    metric_key = "PPI_YY"
                elif "ppi" in normalized:
                    metric_key = "PPI_MOM"

                metric = bls.get(metric_key) if metric_key else None
                if metric:
                    raw = metric.get("yoy") if "y/y" in normalized else metric.get("mom")
                    _apply_actual(event, _fmt_pct(raw) or "N/A", metric, raw)
                else:
                    _mark_unavailable(event, "Official BLS actual not found for this event.", "BLS")

            elif "retail sales" in normalized and "core" not in normalized:
                retail = retail if retail is not None else _fetch_census_retail_actual()
                if retail and retail.get("mom") is not None:
                    _apply_actual(event, _fmt_pct(retail.get("mom")) or "N/A", retail, retail.get("mom"))
                    if retail.get("previous_revised") is not None:
                        event["previous_revised"] = _fmt_pct(retail.get("previous_revised"))
                else:
                    _mark_unavailable(event, "Official Census retail sales actual not found.", "U.S. Census")

            elif "core retail sales" in normalized:
                _mark_unavailable(
                    event,
                    "Core retail actual needs Census table-level parsing/API key; headline retail is enriched.",
                    "U.S. Census"
                )

            elif "unemployment claims" in normalized:
                claims = claims if claims is not None else _fetch_fred_initial_claims()
                if claims and claims.get("actual") is not None:
                    _apply_actual(event, _fmt_k(claims.get("actual")) or "N/A", claims, claims.get("actual"))
                    event["previous_official"] = _fmt_k(claims.get("previous"))
                    event["week_over_week_change"] = _fmt_k(claims.get("wow_change"))
                else:
                    _mark_unavailable(event, "Official claims actual not found from fallback source.", "FRED ICSA")

            elif not event.get("forecast") and not event.get("previous"):
                _mark_unavailable(event, "Speech/event; no numeric actual is published.")
            else:
                _mark_unavailable(event, "Actual value is not provided by the calendar source.")
        except Exception as exc:
            _mark_unavailable(event, f"Actual enrichment failed: {exc}")

        enriched.append(event)
    return enriched
