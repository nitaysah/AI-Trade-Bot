"""Context for the Macro Data dialog: how to read each reading, what each release means, and a one-line backdrop.

Pure functions (no network). The numbers come from macro_service / the calendar feed; this module only classifies and
explains them, using stated thresholds, so the dialog can say more than a bare value.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── Reading the levels ────────────────────────────────────────────────────────────────────────────────────────────

VIX_BANDS = ((15.0, "Calm", "good"), (20.0, "Normal", "neutral"), (30.0, "Elevated", "warn"), (float("inf"), "Stress", "bad"))


def vix_level(value: Optional[float]) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    for limit, label, tone in VIX_BANDS:
        if value < limit:
            return {"label": label, "tone": tone, "note": "Fear gauge: under 15 is calm, over 30 is market stress."}
    return None


def curve_level(spread: Optional[float]) -> Optional[Dict[str, str]]:
    """10-year minus 3-month yield (the NY Fed's recession-signal curve)."""
    if spread is None:
        return None
    if spread < 0:
        return {"label": "Inverted", "tone": "bad", "note": "Short rates above long rates has preceded most US recessions, with a long lag."}
    if spread < 0.5:
        return {"label": "Flat", "tone": "warn", "note": "Little extra yield for lending long: markets expect slower growth or lower rates."}
    return {"label": "Normal", "tone": "good", "note": "Long yields above short yields, the usual shape in an expanding economy."}


def yield_level(value: Optional[float]) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    if value >= 5.0:
        return {"label": "High", "tone": "warn", "note": "Yields at or above 5% compete with stocks for money and raise borrowing costs."}
    if value >= 3.5:
        return {"label": "Moderate", "tone": "neutral", "note": "Yields in the 3.5-5% range: meaningful, but not restrictive on their own."}
    return {"label": "Low", "tone": "good", "note": "Low yields are supportive for growth stocks and borrowing."}


def range_label(position_pct: Optional[float]) -> Optional[str]:
    if position_pct is None:
        return None
    if position_pct >= 90:
        return "near its 1-year high"
    if position_pct <= 10:
        return "near its 1-year low"
    return f"{position_pct:.0f}% of the way up its 1-year range"


# ── Explaining the calendar ───────────────────────────────────────────────────────────────────────────────────────

# (keywords in the lower-cased title, text). The first entry that matches wins.
WHY_IT_MATTERS = (
    (("core cpi", "cpi"), "Inflation. A hotter reading raises the odds the Fed keeps rates high (usually negative for stocks and bonds, positive for the dollar); a cooler one does the opposite."),
    (("core ppi", "ppi"), "Wholesale prices, a lead on consumer inflation. Large surprises move rate expectations."),
    (("non-farm", "nonfarm", "employment change"), "Jobs. Strong hiring supports growth but can delay rate cuts; a weak report raises recession worries."),
    (("unemployment rate",), "Slack in the labour market. A rising rate is read as cooling demand."),
    (("unemployment claims", "jobless claims"), "Weekly layoffs. A sustained rise is an early sign the labour market is weakening."),
    (("retail sales",), "Consumer spending is about two-thirds of US growth. Core retail sales (ex autos) is the cleaner read."),
    (("fomc", "federal funds", "interest rate decision", "rate statement"), "The Fed's rate decision and guidance. Often the largest scheduled mover of rates, the dollar and equities."),
    (("fed chair", "fed chairman", "fomc member", "speaks"), "Central-bank commentary. Markets trade the tone on rates and inflation, and it can move prices within minutes."),
    (("gdp",), "The broadest growth measure. Revisions and the price-deflator part matter as much as the headline."),
    (("pce",), "The Fed's preferred inflation gauge. Core PCE is the number policymakers quote."),
    (("ism", "pmi", "philly fed", "empire state", "manufacturing index"), "Survey of business activity: above 50 (or above 0 for regional Fed indexes) signals expansion. Forward-looking, so it moves markets early."),
    (("consumer sentiment", "consumer confidence"), "How households feel about the economy and inflation; weak readings can foreshadow slower spending."),
    (("housing", "building permits", "home sales"), "Housing is rate sensitive, so it shows how higher borrowing costs are biting."),
    (("treasury", "bond auction", "10-y", "30-y"), "Demand for government debt. A weak auction pushes yields up."),
    (("boe", "bank of england"), "UK policy and the pound. Moves GBP pairs and can spill into global yields."),
    (("ecb",), "Euro-area policy and the euro. Moves EUR pairs and the dollar index."),
    (("boj", "bank of japan"), "Japanese policy and the yen. A surprise can unwind yen carry trades and hit global risk assets."),
)


def why_it_matters(title: str) -> str:
    text = str(title or "").lower()
    for keywords, explanation in WHY_IT_MATTERS:
        if any(keyword in text for keyword in keywords):
            return explanation
    return ""


# ── The one-line backdrop ─────────────────────────────────────────────────────────────────────────────────────────

def backdrop(indicators: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """A short, rule-based read of the indicators: volatility regime, rates trend, dollar direction, curve shape.

    Each point names the number it comes from, so nothing here is a hidden judgement."""
    points: List[str] = []
    tone = "neutral"
    vix = (indicators.get("^VIX") or {})
    if vix.get("current_value") is not None:
        level = vix.get("level") or {}
        points.append(f"Volatility is {str(level.get('label', 'unknown')).lower()} (VIX {vix['current_value']:.1f}).")
        tone = {"Calm": "good", "Normal": "neutral", "Elevated": "warn", "Stress": "bad"}.get(level.get("label"), "neutral")
    tnx = (indicators.get("^TNX") or {})
    if tnx.get("current_value") is not None and tnx.get("week_change_bp") is not None:
        week = tnx["week_change_bp"]
        direction = "rising" if week >= 5 else "falling" if week <= -5 else "little changed"
        points.append(f"10-year yield {tnx['current_value']:.2f}% and {direction} this week ({week:+.0f} bp).")
    dxy = (indicators.get("DX-Y.NYB") or {})
    if dxy.get("week_change_pct") is not None:
        week = dxy["week_change_pct"]
        points.append(f"Dollar {'firmer' if week >= 0.5 else 'softer' if week <= -0.5 else 'steady'} this week ({week:+.1f}%).")
    curve = (indicators.get("CURVE") or {})
    if curve.get("current_value") is not None:
        points.append(f"Yield curve {str((curve.get('level') or {}).get('label', '')).lower()} (10Y-3M {curve['current_value']:+.2f} pts).")
        if (curve.get("level") or {}).get("label") == "Inverted" and tone != "bad":
            tone = "warn"
    return {"tone": tone, "points": points}
