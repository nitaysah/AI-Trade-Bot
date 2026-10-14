"""Pure calculations behind the fundamentals report: growth rates, a quality scorecard and the Piotroski F-score.

Everything here works on plain "series" lists, newest first: ``[{"period": "2025-09-30", "value": 123.0}, ...]``
(the shape the fundamentals service builds from the annual statements). No network, no side effects, so every number can
be checked by hand and unit-tested.

The scorecard is an evidence summary, not advice: each category is rated from stated thresholds and the reasons are
returned next to the rating so the user can see exactly what drove it.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

Series = Sequence[Dict[str, Any]]

STRONG, OK, WEAK, UNKNOWN = "strong", "ok", "weak", "unknown"


def num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def at(series: Series, index: int) -> Optional[float]:
    if not series or index >= len(series):
        return None
    return num(series[index].get("value"))


def pct_change(series: Series, back: int = 1) -> Optional[float]:
    """Percent change from ``back`` periods ago to the latest; None unless both are positive-base comparable."""
    current, prior = at(series, 0), at(series, back)
    if current is None or prior is None or prior == 0:
        return None
    if prior < 0:  # growth from a negative base is not meaningful as a percentage
        return None
    return (current / prior - 1.0) * 100.0


def cagr(series: Series, years: int) -> Optional[float]:
    """Compound annual growth over ``years`` periods; needs two positive values."""
    current, prior = at(series, 0), at(series, years)
    if current is None or prior is None or current <= 0 or prior <= 0 or years <= 0:
        return None
    return ((current / prior) ** (1.0 / years) - 1.0) * 100.0


def ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def margin_series(numerator: Series, revenue: Series) -> List[Optional[float]]:
    """Margin (percent) for each period present in both series, newest first (None where unavailable)."""
    by_period = {row.get("period"): num(row.get("value")) for row in revenue or []}
    out = []
    for row in numerator or []:
        revenue_value = by_period.get(row.get("period"))
        value = num(row.get("value"))
        out.append(value / revenue_value * 100.0 if value is not None and revenue_value else None)
    return out


def margin_change_points(numerator: Series, revenue: Series) -> Optional[float]:
    """Latest margin minus the prior year's margin, in percentage points."""
    margins = margin_series(numerator, revenue)
    if len(margins) < 2 or margins[0] is None or margins[1] is None:
        return None
    return margins[0] - margins[1]


# ── Piotroski F-score ─────────────────────────────────────────────────────────────────────────────────────────────

def piotroski(*, net_income: Series, operating_cash: Series, assets: Series, debt: Series, current_assets: Series,
              current_liabilities: Series, shares: Series, gross_profit: Series, revenue: Series) -> Dict[str, Any]:
    """The nine Piotroski (2000) tests on two consecutive annual periods. Tests whose inputs are missing are reported as
    unavailable and left out of the denominator, so a thin data set never looks like a weak company."""
    tests: List[Dict[str, Any]] = []

    def add(name: str, passed: Optional[bool], detail: str) -> None:
        tests.append({"test": name, "result": None if passed is None else bool(passed), "detail": detail})

    ni0, ni1 = at(net_income, 0), at(net_income, 1)
    ocf0 = at(operating_cash, 0)
    a0, a1 = at(assets, 0), at(assets, 1)
    roa0, roa1 = ratio(ni0, a0), ratio(ni1, a1)
    add("Profitable", None if ni0 is None else ni0 > 0, "Net income is positive")
    add("Operating cash flow positive", None if ocf0 is None else ocf0 > 0, "The business generates cash from operations")
    add("Return on assets improving", None if roa0 is None or roa1 is None else roa0 > roa1, "Net income / assets rose versus last year")
    add("Cash earnings above reported earnings", None if ocf0 is None or ni0 is None else ocf0 > ni0,
        "Operating cash flow exceeds net income (low accrual risk)")
    lev0, lev1 = ratio(at(debt, 0), a0), ratio(at(debt, 1), a1)
    add("Leverage falling", None if lev0 is None or lev1 is None else lev0 < lev1 or (lev0 == 0 and lev1 == 0), "Debt / assets is lower than last year")
    cr0, cr1 = ratio(at(current_assets, 0), at(current_liabilities, 0)), ratio(at(current_assets, 1), at(current_liabilities, 1))
    add("Liquidity improving", None if cr0 is None or cr1 is None else cr0 > cr1, "Current ratio is higher than last year")
    s0, s1 = at(shares, 0), at(shares, 1)
    add("No dilution", None if s0 is None or s1 is None else s0 <= s1 * 1.005, "Share count did not grow (buybacks count as a pass)")
    gm0, gm1 = ratio(at(gross_profit, 0), at(revenue, 0)), ratio(at(gross_profit, 1), at(revenue, 1))
    add("Gross margin improving", None if gm0 is None or gm1 is None else gm0 > gm1, "Gross margin is higher than last year")
    at0, at1 = ratio(at(revenue, 0), a0), ratio(at(revenue, 1), a1)
    add("Asset turnover improving", None if at0 is None or at1 is None else at0 > at1, "Revenue / assets is higher than last year")

    available = [t for t in tests if t["result"] is not None]
    score = sum(1 for t in available if t["result"])
    return {"score": score, "available": len(available), "tests": tests, "reliable": len(available) >= 7}


# ── Scorecard ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _category(key: str, label: str, rating: str, reasons: List[str]) -> Dict[str, Any]:
    return {"key": key, "label": label, "rating": rating, "reasons": reasons}


def rate_growth(revenue_growth: Optional[float], revenue_cagr: Optional[float], earnings_growth: Optional[float]) -> Dict[str, Any]:
    reasons: List[str] = []
    if revenue_growth is None and revenue_cagr is None:
        return _category("growth", "Growth", UNKNOWN, ["Not enough annual history to measure growth."])
    primary = revenue_growth if revenue_growth is not None else revenue_cagr
    if revenue_growth is not None:
        reasons.append(f"Revenue {revenue_growth:+.1f}% last year")
    if revenue_cagr is not None:
        reasons.append(f"{revenue_cagr:+.1f}% a year over 3 years")
    if earnings_growth is not None:
        reasons.append(f"net income {earnings_growth:+.1f}% last year")
    rating = STRONG if primary >= 10 else OK if primary >= 0 else WEAK
    if rating == STRONG and earnings_growth is not None and earnings_growth < 0:
        rating = OK
        reasons.append("profit is not keeping up with sales")
    return _category("growth", "Growth", rating, reasons)


def rate_profitability(net_margin: Optional[float], operating_margin: Optional[float], op_margin_change: Optional[float],
                       roe: Optional[float]) -> Dict[str, Any]:
    if net_margin is None and operating_margin is None:
        return _category("profitability", "Profitability", UNKNOWN, ["Margins are not available."])
    reasons: List[str] = []
    if net_margin is not None:
        reasons.append(f"net margin {net_margin:.1f}%")
    if operating_margin is not None:
        reasons.append(f"operating margin {operating_margin:.1f}%")
    if op_margin_change is not None:
        reasons.append(f"operating margin {op_margin_change:+.1f} pts vs last year")
    if roe is not None:
        reasons.append(f"return on equity {roe:.0f}%")
    margin = net_margin if net_margin is not None else operating_margin
    if margin < 0:
        rating = WEAK
    elif margin >= 15 and (op_margin_change is None or op_margin_change >= -1.0):
        rating = STRONG
    elif margin >= 5:
        rating = OK
    else:
        rating = WEAK if (op_margin_change is not None and op_margin_change < 0) else OK
    return _category("profitability", "Profitability", rating, reasons)


def rate_cash_quality(fcf_margin: Optional[float], ocf_to_net_income: Optional[float], free_cash: Optional[float]) -> Dict[str, Any]:
    if free_cash is None and fcf_margin is None:
        return _category("cash", "Cash generation", UNKNOWN, ["Cash-flow data is not available."])
    reasons: List[str] = []
    if fcf_margin is not None:
        reasons.append(f"free-cash-flow margin {fcf_margin:.1f}%")
    if ocf_to_net_income is not None:
        reasons.append(f"operating cash is {ocf_to_net_income:.1f}x net income")
    if free_cash is not None and free_cash <= 0:
        return _category("cash", "Cash generation", WEAK, reasons + ["free cash flow is negative"])
    strong = (fcf_margin is not None and fcf_margin >= 10) and (ocf_to_net_income is None or ocf_to_net_income >= 0.9)
    if ocf_to_net_income is not None and ocf_to_net_income < 0.6:
        reasons.append("profits are not converting into cash")
        # Weak conversion is a warning, but a business that still keeps a healthy free-cash-flow margin is not "weak".
        return _category("cash", "Cash generation", OK if (fcf_margin is not None and fcf_margin >= 5) else WEAK, reasons)
    return _category("cash", "Cash generation", STRONG if strong else OK, reasons)


def rate_strength(debt_to_equity: Optional[float], negative_equity: bool, net_cash: Optional[float], interest_coverage: Optional[float],
                  current_ratio: Optional[float]) -> Dict[str, Any]:
    reasons: List[str] = []
    if negative_equity:
        reasons.append("shareholder equity is negative")
    if debt_to_equity is not None and not negative_equity:
        reasons.append(f"debt/equity {debt_to_equity:.2f}")
    if net_cash is not None:
        reasons.append("net cash" if net_cash >= 0 else "net debt")
    if interest_coverage is not None:
        reasons.append(f"interest covered {interest_coverage:.1f}x by operating profit" if interest_coverage >= 0
                       else "operating loss, so interest is not covered by operating profit")
    if current_ratio is not None:
        reasons.append(f"current ratio {current_ratio:.2f}")
    known = [debt_to_equity, interest_coverage, current_ratio]
    if not negative_equity and all(value is None for value in known) and net_cash is None:
        return _category("strength", "Financial strength", UNKNOWN, ["Balance-sheet data is not available."])
    if negative_equity or (interest_coverage is not None and interest_coverage < 3) or (debt_to_equity is not None and debt_to_equity > 1.5):
        return _category("strength", "Financial strength", WEAK, reasons)
    strong = (
        ((net_cash is not None and net_cash >= 0) or (debt_to_equity is not None and debt_to_equity <= 0.5))
        and (interest_coverage is None or interest_coverage >= 8)
        and (current_ratio is None or current_ratio >= 1.0)
    )
    return _category("strength", "Financial strength", STRONG if strong else OK, reasons)


def rate_valuation(forward_pe: Optional[float], trailing_pe: Optional[float], peg: Optional[float], fcf_yield: Optional[float],
                   growth: Optional[float]) -> Dict[str, Any]:
    reasons: List[str] = []
    if forward_pe is not None:
        reasons.append(f"forward P/E {forward_pe:.1f}")
    elif trailing_pe is not None:
        reasons.append(f"trailing P/E {trailing_pe:.1f}")
    if peg is not None:
        reasons.append(f"PEG {peg:.2f}")
    if fcf_yield is not None:
        reasons.append(f"free-cash-flow yield {fcf_yield:.1f}%")
    pe = forward_pe if forward_pe is not None else trailing_pe
    if pe is None and peg is None and fcf_yield is None:
        return _category("valuation", "Valuation", UNKNOWN, ["Valuation inputs are not available (for example no earnings)."])
    stretched = (peg is not None and peg > 2.0) or (pe is not None and pe > 40 and (growth is None or growth < 15))
    cheap = (peg is not None and peg <= 1.2) or (pe is not None and 0 < pe <= 15) or (fcf_yield is not None and fcf_yield >= 6)
    if stretched:
        reasons.append("price is high relative to growth")
        return _category("valuation", "Valuation", WEAK, reasons)
    if cheap:
        return _category("valuation", "Valuation", STRONG, reasons)
    return _category("valuation", "Valuation", OK, reasons)


ASSESSMENTS = {
    "insufficient": "Insufficient evidence",
    "valuation": "Wait for a better valuation",
    "consider": "Worth considering",
    "improve": "Wait for improvement",
    "mixed": "Mixed evidence",
}


def overall(categories: List[Dict[str, Any]], piotroski_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Combine the category ratings (and a reliable F-score) into a 0-100 score and a plain assessment."""
    known = [c for c in categories if c["rating"] != UNKNOWN]
    if len(known) < 3:
        return {"score": None, "assessment": ASSESSMENTS["insufficient"], "known_categories": len(known)}
    points = {STRONG: 2, OK: 1, WEAK: 0}
    score = sum(points[c["rating"]] for c in known) / (2 * len(known)) * 100.0
    by_key = {c["key"]: c["rating"] for c in categories}
    quality_weak = [k for k in ("profitability", "cash", "strength") if by_key.get(k) == WEAK]
    if piotroski_result and piotroski_result.get("reliable"):
        f_score = piotroski_result["score"] / max(1, piotroski_result["available"])
        score = 0.8 * score + 0.2 * (f_score * 100.0)
    if by_key.get("valuation") == WEAK and not quality_weak:
        label = ASSESSMENTS["valuation"]
    elif score >= 72 and not quality_weak and by_key.get("valuation") != WEAK:
        label = ASSESSMENTS["consider"]
    elif quality_weak and score < 55:
        label = ASSESSMENTS["improve"]
    else:
        label = ASSESSMENTS["mixed"]
    return {"score": round(score), "assessment": label, "known_categories": len(known)}


def takeaways(categories: List[Dict[str, Any]], assessment: str) -> List[str]:
    """Up to four plain-English points: the strongest and weakest areas first."""
    order = {STRONG: 0, WEAK: 1, OK: 2, UNKNOWN: 3}
    ranked = sorted((c for c in categories if c["rating"] != UNKNOWN), key=lambda c: order[c["rating"]])
    verbs = {STRONG: "is a strength", WEAK: "is a concern", OK: "looks acceptable"}
    lines = []
    for category in ranked[:4]:
        lines.append(f"{category['label']} {verbs[category['rating']]}: {', '.join(category['reasons'][:3])}.")
    if not lines:
        lines.append("There is not enough structured data to form a view.")
    return lines
