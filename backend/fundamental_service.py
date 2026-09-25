"""Cached, explainable fundamental research reports for Screener profiles.

This module is deliberately separate from technical signal evaluation. It uses
structured public financial data, produces plain-language interpretations, and
never creates orders or an investment recommendation.
"""

from __future__ import annotations

import copy
import math
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

from instrument_types import is_known_etf


CENTRAL_TZ = ZoneInfo("America/Chicago")
REPORT_VERSION = 1
REFRESH_SECONDS = 24 * 60 * 60
RETRY_SECONDS = 30 * 60


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if hasattr(value, "item"):
            value = value.item()
        number = float(value)
        return round(number, 6) if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _is_crypto(symbol: str) -> bool:
    value = str(symbol or "").upper().replace("/", "")
    return any(value.endswith(suffix) for suffix in ("USD", "USDT", "USDC", "BTC", "ETH"))


def _info_value(info: dict, *keys: str) -> Any:
    for key in keys:
        value = info.get(key)
        if value is not None and value != "":
            return value
    return None


def _period_label(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return _text(value, 32)


def _statement_frame(ticker: Any, method: str) -> Any:
    try:
        source = getattr(ticker, method, None)
        if callable(source):
            return source(freq="yearly")
        return source
    except Exception:
        return None


def _row(frame: Any, aliases: tuple[str, ...]) -> list[dict[str, Any]]:
    if frame is None or not hasattr(frame, "loc"):
        return []
    for alias in aliases:
        try:
            series = frame.loc[alias]
            values = series.to_dict() if hasattr(series, "to_dict") else {}
            output = []
            for period, value in values.items():
                number = _number(value)
                if number is not None:
                    output.append({"period": _period_label(period), "value": number})
            return output[:5]
        except (KeyError, TypeError, AttributeError, IndexError):
            continue
    return []


def _latest(series: list[dict[str, Any]]) -> float | None:
    return _number(series[0].get("value")) if series else None


def _growth(series: list[dict[str, Any]]) -> float | None:
    if len(series) < 2:
        return None
    current = _number(series[0].get("value"))
    prior = _number(series[1].get("value"))
    if current is None or prior in (None, 0):
        return None
    return (current / prior - 1) * 100


def _as_percent(value: Any) -> float | None:
    number = _number(value)
    if number is not None and abs(number) <= 2:
        return round(number * 100, 4)
    return number


def _metric(label: str, value: Any, meaning: str, interpretation: str = "", trend: str = "", period: str = "Latest reported") -> dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "meaning": meaning,
        "interpretation": interpretation,
        "trend": trend,
        "period": period,
        "source": "Yahoo Finance structured data",
    }


def _fmt_pct(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "Not available"


def _build_report(ticker: str, options_profile: bool = False) -> dict[str, Any]:
    symbol = str(ticker or "").upper().strip()
    if not symbol:
        raise ValueError("Ticker is required")
    if _is_crypto(symbol):
        return {"report_version": REPORT_VERSION, "ticker": symbol, "status": "not_applicable", "assessment": "Insufficient evidence", "summary": "Company financial statements are not applicable to this crypto profile."}
    if is_known_etf(symbol):
        return {
            "report_version": REPORT_VERSION,
            "ticker": symbol,
            "status": "not_applicable",
            "assessment": "Insufficient evidence",
            "summary": "Company financial statements are not applicable to an exchange-traded fund; evaluate its holdings, index methodology, fees, liquidity, and tracking instead.",
        }
    if options_profile:
        return {"report_version": REPORT_VERSION, "ticker": symbol, "status": "not_applicable", "assessment": "Insufficient evidence", "summary": "Fundamental research is shown for the underlying equity; an option contract itself has no operating statements or P/E."}

    yf_ticker = yf.Ticker(symbol)
    info = yf_ticker.get_info() if hasattr(yf_ticker, "get_info") else (yf_ticker.info or {})
    if not isinstance(info, dict):
        info = {}
    income = _statement_frame(yf_ticker, "get_income_stmt")
    balance = _statement_frame(yf_ticker, "get_balance_sheet")
    cashflow = _statement_frame(yf_ticker, "get_cash_flow")

    revenue = _row(income, ("Total Revenue", "Operating Revenue"))
    gross_profit = _row(income, ("Gross Profit",))
    operating_income = _row(income, ("Operating Income",))
    net_income = _row(income, ("Net Income", "Net Income Common Stockholders"))
    diluted_eps = _row(income, ("Diluted EPS", "Basic EPS"))
    ebitda = _row(income, ("EBITDA", "Normalized EBITDA"))
    operating_cash = _row(cashflow, ("Operating Cash Flow", "Total Cash From Operating Activities"))
    capex = _row(cashflow, ("Capital Expenditure", "Capital Expenditures"))
    free_cash = _row(cashflow, ("Free Cash Flow",))
    cash = _row(balance, ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"))
    debt = _row(balance, ("Total Debt", "Long Term Debt And Capital Lease Obligation"))
    assets = _row(balance, ("Total Assets",))
    liabilities = _row(balance, ("Total Liabilities Net Minority Interest", "Total Liabilities"))
    equity = _row(balance, ("Stockholders Equity", "Total Equity Gross Minority Interest"))
    current_assets = _row(balance, ("Current Assets",))
    current_liabilities = _row(balance, ("Current Liabilities",))

    latest_revenue = _latest(revenue)
    latest_gross = _latest(gross_profit)
    latest_operating = _latest(operating_income)
    latest_net = _latest(net_income)
    latest_ocf = _latest(operating_cash)
    latest_fcf = _latest(free_cash)
    latest_cash = _latest(cash)
    latest_debt = _latest(debt)
    latest_equity = _latest(equity)
    latest_current_assets = _latest(current_assets)
    latest_current_liabilities = _latest(current_liabilities)

    revenue_growth = _growth(revenue)
    net_margin = latest_net / latest_revenue * 100 if latest_net is not None and latest_revenue else None
    gross_margin = latest_gross / latest_revenue * 100 if latest_gross is not None and latest_revenue else None
    operating_margin = latest_operating / latest_revenue * 100 if latest_operating is not None and latest_revenue else None
    fcf_margin = latest_fcf / latest_revenue * 100 if latest_fcf is not None and latest_revenue else None
    debt_equity = latest_debt / latest_equity if latest_debt is not None and latest_equity else None
    current_ratio = latest_current_assets / latest_current_liabilities if latest_current_assets is not None and latest_current_liabilities else None

    valuation = {
        "market_cap": _number(_info_value(info, "marketCap")),
        "enterprise_value": _number(_info_value(info, "enterpriseValue")),
        "trailing_pe": _number(_info_value(info, "trailingPE")),
        "forward_pe": _number(_info_value(info, "forwardPE")),
        "peg_ratio": _number(_info_value(info, "pegRatio")),
        "price_to_sales": _number(_info_value(info, "priceToSalesTrailing12Months")),
        "price_to_book": _number(_info_value(info, "priceToBook")),
        "ev_to_ebitda": _number(_info_value(info, "enterpriseToEbitda")),
        "dividend_yield": _number(_info_value(info, "dividendYield")),
        "payout_ratio": _number(_info_value(info, "payoutRatio")),
        "target_low": _number(_info_value(info, "targetLowPrice")),
        "target_mean": _number(_info_value(info, "targetMeanPrice")),
        "target_high": _number(_info_value(info, "targetHighPrice")),
    }
    valuation["dividend_yield"] = valuation["dividend_yield"] * 100 if valuation["dividend_yield"] is not None and abs(valuation["dividend_yield"]) <= 2 else valuation["dividend_yield"]
    valuation["payout_ratio"] = valuation["payout_ratio"] * 100 if valuation["payout_ratio"] is not None and abs(valuation["payout_ratio"]) <= 2 else valuation["payout_ratio"]

    metrics = {
        "growth": [_metric("Revenue growth", _fmt_pct(revenue_growth), "Year-over-year change in reported revenue.", "Positive growth means the business is producing more sales; verify whether margins and cash flow support it.", "Improving" if revenue_growth is not None and revenue_growth > 0 else "Weakening" if revenue_growth is not None else "")],
        "profitability": [
            _metric("Gross margin", _fmt_pct(gross_margin), "Revenue remaining after direct costs.", "Higher or improving gross margin indicates stronger product economics.", "Positive" if gross_margin is not None and gross_margin > 0 else ""),
            _metric("Operating margin", _fmt_pct(operating_margin), "Operating profit retained from each dollar of revenue.", "Improvement suggests better operating efficiency; investigate the company’s disclosed drivers.", "Positive" if operating_margin is not None and operating_margin > 0 else ""),
            _metric("Net margin", _fmt_pct(net_margin), "Profit remaining after operating costs, interest, and taxes.", "Positive net margin indicates reported profitability; compare its trend with cash generation.", "Positive" if net_margin is not None and net_margin > 0 else ""),
        ],
        "cash_generation": [
            _metric("Operating cash flow", latest_ocf, "Cash generated by the company’s operations.", "Operating cash flow should generally support reported earnings over time.", "Positive" if latest_ocf is not None and latest_ocf > 0 else "Weak"),
            _metric("Free cash flow", latest_fcf, "Cash remaining after capital investment.", "Positive free cash flow can fund debt reduction, dividends, or reinvestment.", "Positive" if latest_fcf is not None and latest_fcf > 0 else "Weak"),
            _metric("Free cash flow margin", _fmt_pct(fcf_margin), "Free cash flow as a percentage of revenue.", "Higher conversion means more sales become discretionary cash.", "Positive" if fcf_margin is not None and fcf_margin > 0 else ""),
        ],
        "financial_strength": [
            _metric("Cash", latest_cash, "Cash and short-term investments available to the company.", "More cash can improve resilience, subject to debt and operating needs."),
            _metric("Total debt", latest_debt, "Borrowings and debt-like obligations reported on the balance sheet.", "Debt should be considered alongside cash flow and interest obligations."),
            _metric("Debt / equity", round(debt_equity, 2) if debt_equity is not None else "Not available", "Debt relative to shareholder equity.", "Lower leverage generally leaves more flexibility, but norms vary by industry."),
            _metric("Current ratio", round(current_ratio, 2) if current_ratio is not None else "Not available", "Current assets relative to current liabilities.", "A higher ratio generally indicates more short-term liquidity; industry context matters."),
        ],
        "valuation": [
            _metric("Market cap", valuation["market_cap"], "Market value of the company’s outstanding equity."),
            _metric("Trailing P/E", valuation["trailing_pe"] or "Not meaningful", "Price paid for each dollar of past earnings.", "Compare with the company’s history, peers, and growth rather than using a universal cutoff."),
            _metric("Forward P/E", valuation["forward_pe"] or "Not meaningful", "Price paid for each dollar of forecast earnings.", "Forecast-based valuation depends on estimates that can change."),
            _metric("PEG ratio", valuation["peg_ratio"] or "Not available", "P/E considered alongside expected growth.", "Useful only when the growth estimate and earnings quality are comparable."),
            _metric("Enterprise value / EBITDA", valuation["ev_to_ebitda"] or "Not available", "Enterprise value relative to operating earnings before interest, taxes, depreciation, and amortization.", "Compare businesses with similar capital structures and accounting characteristics."),
        ],
    }

    strengths, concerns, monitor = [], [], []
    if revenue_growth is not None and revenue_growth > 0: strengths.append(f"Revenue grew {_fmt_pct(revenue_growth)} year over year.")
    elif revenue_growth is not None: concerns.append(f"Revenue declined {_fmt_pct(abs(revenue_growth))} year over year.")
    if net_margin is not None and net_margin > 0: strengths.append(f"Net margin is {_fmt_pct(net_margin)}.")
    elif net_margin is not None: concerns.append("The latest reported net margin is negative.")
    if latest_fcf is not None and latest_fcf > 0: strengths.append("The latest reported period generated positive free cash flow.")
    elif latest_fcf is not None: concerns.append("The latest reported period generated negative free cash flow.")
    if valuation["forward_pe"] is not None and valuation["trailing_pe"] is not None and valuation["forward_pe"] < valuation["trailing_pe"]: strengths.append("Forward earnings expectations are below the trailing valuation multiple.")
    if valuation["peg_ratio"] is not None and valuation["peg_ratio"] > 2: concerns.append("The PEG ratio is elevated relative to the reported growth estimate.")
    if debt_equity is not None and debt_equity > 1.5: concerns.append("Debt is high relative to reported equity; review cash flow and maturities.")
    if revenue_growth is not None: monitor.append("Check whether the next reported period maintains the current revenue trend.")
    monitor.append("Compare the next earnings release with current estimates and cash-flow conversion.")
    monitor.append("Review the latest 10-K or 10-Q risk factors for changes in business conditions.")

    fundamental_conditions = [
        "Revisit when the next report confirms revenue, margin, and free-cash-flow direction." if revenue_growth is not None else "Revisit after a complete, current financial statement set is available.",
        "Use the technical Entry Watch separately for timing; this report describes business and valuation evidence.",
    ]
    if valuation["peg_ratio"] is not None and valuation["peg_ratio"] > 2:
        fundamental_conditions.insert(0, "A more attractive entry would require valuation to moderate or growth estimates to improve.")

    evidence_count = len(strengths) + len(concerns)
    if evidence_count < 2:
        assessment = "Insufficient evidence"
    elif concerns and (valuation["peg_ratio"] is not None and valuation["peg_ratio"] > 2):
        assessment = "Wait for a better valuation"
    elif len(strengths) >= len(concerns) + 2:
        assessment = "Worth considering"
    elif concerns:
        assessment = "Wait for improvement"
    else:
        assessment = "Mixed evidence"

    filings = []
    try:
        raw_filings = yf_ticker.get_sec_filings()
        if hasattr(raw_filings, "head"):
            raw_filings = raw_filings.head(8).to_dict(orient="records")
        if isinstance(raw_filings, list):
            for row in raw_filings[:8]:
                if isinstance(row, dict):
                    filings.append({"date": _text(row.get("date") or row.get("filingDate"), 32), "type": _text(row.get("type") or row.get("form"), 24), "title": _text(row.get("title") or row.get("description"), 160), "url": _text(row.get("edgarUrl") or row.get("url"), 500)})
    except Exception:
        pass

    return {
        "report_version": REPORT_VERSION,
        "ticker": symbol,
        "status": "current",
        "company": {
            "name": _text(_info_value(info, "longName", "shortName") or symbol, 160),
            "description": _text(_info_value(info, "longBusinessSummary"), 1600),
            "sector": _text(_info_value(info, "sector"), 80),
            "industry": _text(_info_value(info, "industry"), 120),
            "country": _text(_info_value(info, "country"), 80),
            "employees": _number(_info_value(info, "fullTimeEmployees")),
            "website": _text(_info_value(info, "website"), 300),
            "currency": _text(_info_value(info, "currency", "financialCurrency"), 16),
        },
        "valuation": valuation,
        "metrics": metrics,
        "financial_trends": {"revenue": revenue, "net_income": net_income, "free_cash_flow": free_cash, "operating_cash_flow": operating_cash, "eps": diluted_eps, "ebitda": ebitda, "cash": cash, "debt": debt, "assets": assets, "liabilities": liabilities, "equity": equity},
        "estimates": {"analyst_count": _number(_info_value(info, "numberOfAnalystOpinions")), "recommendation": _text(_info_value(info, "recommendationKey"), 40), "target_low": valuation["target_low"], "target_mean": valuation["target_mean"], "target_high": valuation["target_high"]},
        "ownership": {"shares_outstanding": _number(_info_value(info, "sharesOutstanding")), "insider_pct": _as_percent(_info_value(info, "heldPercentInsiders")), "institutional_pct": _as_percent(_info_value(info, "heldPercentInstitutions")), "short_pct_float": _as_percent(_info_value(info, "shortPercentOfFloat"))},
        "filings": filings,
        "strengths": strengths,
        "concerns": concerns,
        "monitor": monitor,
        "fundamental_conditions": fundamental_conditions,
        "assessment": assessment,
        "summary": f"Fundamental profile: {assessment}. Review the strengths, concerns, valuation context, and monitoring items before making an investment decision.",
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "financial_period": revenue[0].get("period") if revenue else "Not available",
        "source": "Yahoo Finance structured data; SEC filing links when available",
        "options_profile": bool(options_profile),
    }


def build_fundamental_report(ticker: str, previous: dict | None = None, options_profile: bool = False, force: bool = False) -> dict:
    previous = copy.deepcopy(previous or {})
    try:
        report = _build_report(ticker, options_profile=options_profile)
        report["next_check_at"] = time.time() + REFRESH_SECONDS
        report["error"] = ""
        return report
    except Exception as exc:
        stale = previous if previous else {"report_version": REPORT_VERSION, "ticker": str(ticker or "").upper()}
        stale.update({"status": "stale" if previous.get("status") == "current" else "unavailable", "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "error": f"{type(exc).__name__}: {_text(exc, 240)}", "next_check_at": time.time() + RETRY_SECONDS})
        return stale


def get_fundamental_report_detail(ticker: str, cached: dict | None = None, options_profile: bool = False) -> dict:
    try:
        cache_due = not cached or float(cached.get("next_check_at", 0) or 0) <= time.time()
    except (TypeError, ValueError, OverflowError):
        cache_due = True
    if cached and cached.get("report_version") == REPORT_VERSION and cached.get("status") == "current" and not cache_due:
        return copy.deepcopy(cached)
    return build_fundamental_report(ticker, cached, options_profile=options_profile, force=True)
