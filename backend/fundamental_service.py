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

import research_metrics as rm

from instrument_types import is_known_etf


CENTRAL_TZ = ZoneInfo("America/Chicago")
REPORT_VERSION = 2  # 2: scorecard, Piotroski F-score, quality/valuation metrics; dividend yield scale fixed
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


def _norm_label(label: Any) -> str:
    """'Total Revenue', 'TotalRevenue' and 'total_revenue' all become 'totalrevenue'."""
    return "".join(ch for ch in str(label).lower() if ch.isalnum())


def _row(frame: Any, aliases: tuple[str, ...]) -> list[dict[str, Any]]:
    """Newest-first annual values for the first alias found in a statement frame.

    yfinance changed its statement row names from spaced labels ("Total Revenue") to CamelCase ("TotalRevenue"); matching on
    the normalised label keeps both working. Before this, every statement lookup silently came back empty and the report
    fell back to "Insufficient evidence"."""
    if frame is None or not hasattr(frame, "loc"):
        return []
    try:
        labels = {_norm_label(name): name for name in frame.index} if hasattr(frame, "index") else {}
    except TypeError:
        return []
    for alias in aliases:
        # Without an index to match against, fall back to asking the frame for the alias directly.
        original = labels.get(_norm_label(alias)) if labels else alias
        if original is None:
            continue
        try:
            series = frame.loc[original]
            values = series.to_dict() if hasattr(series, "to_dict") else {}
            output = []
            for period, value in sorted(values.items(), key=lambda item: str(item[0]), reverse=True):
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
    ebit = _row(income, ("EBIT", "Operating Income"))
    interest_expense = _row(income, ("Interest Expense", "Interest Expense Non Operating"))
    shares_series = _row(income, ("Diluted Average Shares", "Basic Average Shares"))

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

    # ── Derived quality measures (annual statements; every one is None when its inputs are missing) ──
    revenue_cagr = rm.cagr(revenue, min(3, len(revenue) - 1)) if len(revenue) >= 3 else None
    net_income_growth = rm.pct_change(net_income)
    eps_growth = rm.pct_change(diluted_eps)
    op_margin_change = rm.margin_change_points(operating_income, revenue)
    gross_margin_change = rm.margin_change_points(gross_profit, revenue)
    negative_equity = latest_equity is not None and latest_equity <= 0
    if negative_equity:
        debt_equity = None  # a ratio over negative equity is meaningless; it is reported as a concern instead
    equity_now, equity_prior = _latest(equity), (_number(equity[1].get("value")) if len(equity) > 1 else None)
    average_equity = (equity_now + equity_prior) / 2 if equity_now is not None and equity_prior is not None else equity_now
    roe = latest_net / average_equity * 100 if latest_net is not None and average_equity and average_equity > 0 else None
    if roe is None:
        roe_info = _number(_info_value(info, "returnOnEquity"))
        roe = roe_info * 100 if roe_info is not None else None
    net_cash = latest_cash - latest_debt if latest_cash is not None and latest_debt is not None else None
    latest_ebit = _latest(ebit)
    latest_interest = _latest(interest_expense)
    interest_coverage = latest_ebit / abs(latest_interest) if latest_ebit is not None and latest_interest not in (None, 0) else None
    latest_ebitda = _latest(ebitda)
    net_debt_to_ebitda = (-net_cash) / latest_ebitda if net_cash is not None and latest_ebitda and latest_ebitda > 0 else None
    ocf_to_net_income = latest_ocf / latest_net if latest_ocf is not None and latest_net and latest_net > 0 else None
    share_change_pct = ((_latest(shares_series) / _number(shares_series[1].get("value")) - 1) * 100
                        if len(shares_series) > 1 and _number(shares_series[1].get("value")) and _latest(shares_series) is not None else None)

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
    # Yahoo reports dividendYield already in percent (0.32 means 0.32%), but payoutRatio as a fraction (0.12 means 12%).
    # Scaling both with the same "<= 2" rule showed a 0.3% yield as 32%.
    valuation["payout_ratio"] = valuation["payout_ratio"] * 100 if valuation["payout_ratio"] is not None else None
    market_cap_now = valuation["market_cap"]
    fcf_yield = latest_fcf / market_cap_now * 100 if latest_fcf is not None and market_cap_now else None
    price_now = _number(_info_value(info, "currentPrice", "regularMarketPrice"))
    high_52w = _number(_info_value(info, "fiftyTwoWeekHigh"))
    low_52w = _number(_info_value(info, "fiftyTwoWeekLow"))
    target_upside = (valuation["target_mean"] / price_now - 1) * 100 if valuation["target_mean"] and price_now else None
    below_high = (price_now / high_52w - 1) * 100 if price_now and high_52w else None
    range_position = ((price_now - low_52w) / (high_52w - low_52w) * 100
                      if price_now and high_52w and low_52w is not None and high_52w > low_52w else None)

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

    metrics["growth"] += [
        _metric("Revenue growth (3-yr annual)", _fmt_pct(revenue_cagr), "Compound annual revenue growth over up to three years.", "A steady multi-year rate is more reliable than one strong or weak year.", "Positive" if revenue_cagr and revenue_cagr > 0 else "", "Up to 3 years"),
        _metric("Net income growth", _fmt_pct(net_income_growth), "Year-over-year change in reported net income.", "Profit growing faster than revenue suggests improving efficiency; slower suggests rising costs.", "Positive" if net_income_growth and net_income_growth > 0 else ""),
        _metric("EPS growth", _fmt_pct(eps_growth), "Year-over-year change in diluted earnings per share.", "EPS growth also reflects buybacks and dilution, not only profit.", "Positive" if eps_growth and eps_growth > 0 else ""),
    ]
    metrics["profitability"] += [
        _metric("Return on equity", f"{roe:.1f}%" if roe is not None else "Not available", "Net income relative to average shareholder equity.", "High ROE is good only when it is not produced mainly by heavy debt or buybacks shrinking equity."),
        _metric("Operating margin change", f"{op_margin_change:+.1f} pts" if op_margin_change is not None else "Not available", "Operating margin now minus the prior year.", "Rising margins show pricing power or cost control; falling margins deserve a reason."),
    ]
    metrics["cash_generation"] += [
        _metric("Cash conversion (OCF / net income)", f"{ocf_to_net_income:.2f}x" if ocf_to_net_income is not None else "Not available", "Operating cash flow per dollar of reported profit.", "Near or above 1.0 means earnings are backed by cash; well below 1.0 can signal aggressive accounting or working-capital drag."),
        _metric("Free cash flow yield", _fmt_pct(fcf_yield), "Free cash flow as a percentage of market value.", "A higher yield means more cash generated per dollar paid for the company."),
    ]
    metrics["financial_strength"] += [
        _metric("Net cash (debt)", net_cash if net_cash is not None else "Not available", "Cash minus total debt. Positive means net cash.", "Net cash gives room to invest, buy back stock or ride out a downturn."),
        _metric("Interest coverage", f"{interest_coverage:.1f}x" if interest_coverage is not None else "Not available", "Operating profit divided by interest expense.", "Below about 3x leaves little cushion if profits fall."),
        _metric("Net debt / EBITDA", f"{net_debt_to_ebitda:.1f}x" if net_debt_to_ebitda is not None else "Not available", "Years of operating earnings needed to repay net debt.", "Above roughly 3x is considered leveraged for most industries."),
    ]
    metrics["valuation"] += [
        _metric("Price vs 52-week high", f"{below_high:.1f}%" if below_high is not None else "Not available", "How far the price is from its 52-week high.", "A large discount can mean opportunity or a deteriorating business; check the scorecard before assuming either."),
        _metric("Analyst target upside", f"{target_upside:+.1f}%" if target_upside is not None else "Not available", "Mean analyst price target versus the current price.", "Targets lag price and are often optimistic; treat as sentiment, not a forecast."),
    ]
    metrics["shareholder_returns"] = [
        _metric("Dividend yield", valuation["dividend_yield"] if valuation["dividend_yield"] is not None else "None paid", "Annual dividend as a percentage of the share price.", "Only meaningful if the payout is covered by free cash flow."),
        _metric("Payout ratio", valuation["payout_ratio"] if valuation["payout_ratio"] is not None else "Not available", "Share of profit paid out as dividends.", "Above 80-100% leaves little room to grow or protect the dividend."),
        _metric("Share count change", f"{share_change_pct:+.1f}%" if share_change_pct is not None else "Not available", "Change in diluted shares versus last year. Negative means buybacks.", "Steady dilution transfers value away from existing holders; buybacks do the opposite."),
    ]

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

    if negative_equity: concerns.append("Shareholder equity is negative, so debt ratios are not meaningful; check why (buybacks versus losses).")
    if interest_coverage is not None and interest_coverage < 0: concerns.append("The company has an operating loss, so its interest costs are not covered by operating profit.")
    elif interest_coverage is not None and interest_coverage < 3: concerns.append(f"Operating profit covers interest only {interest_coverage:.1f}x; a profit dip would strain debt payments.")
    if ocf_to_net_income is not None and ocf_to_net_income < 0.6: concerns.append("Operating cash flow is well below reported profit; look at receivables, inventory and one-off gains.")
    if share_change_pct is not None and share_change_pct > 3: concerns.append(f"The share count grew {share_change_pct:.1f}%, diluting existing holders.")
    if share_change_pct is not None and share_change_pct < -2: strengths.append(f"The share count fell {abs(share_change_pct):.1f}% (buybacks).")
    if revenue_cagr is not None and revenue_cagr >= 10: strengths.append(f"Revenue compounded {revenue_cagr:.1f}% a year over the last {min(3, len(revenue) - 1)} years.")
    if net_cash is not None and net_cash > 0: strengths.append("The company holds more cash than debt.")
    if op_margin_change is not None and op_margin_change <= -2: concerns.append(f"Operating margin fell {abs(op_margin_change):.1f} points versus last year.")
    if below_high is not None and below_high <= -25: monitor.append(f"The price is {abs(below_high):.0f}% below its 52-week high; find out whether the business or the market caused it.")
    f_score = rm.piotroski(net_income=net_income, operating_cash=operating_cash, assets=assets, debt=debt, current_assets=current_assets,
                           current_liabilities=current_liabilities, shares=shares_series, gross_profit=gross_profit, revenue=revenue)
    if f_score["reliable"] and f_score["score"] >= 7: strengths.append(f"Piotroski F-score {f_score['score']}/{f_score['available']}: financial health is improving on most tests.")
    if f_score["reliable"] and f_score["score"] <= 3: concerns.append(f"Piotroski F-score {f_score['score']}/{f_score['available']}: most financial-health tests are failing.")

    fundamental_conditions = [
        "Revisit when the next report confirms revenue, margin, and free-cash-flow direction." if revenue_growth is not None else "Revisit after a complete, current financial statement set is available.",
        "Use the technical Entry Watch separately for timing; this report describes business and valuation evidence.",
    ]
    if valuation["peg_ratio"] is not None and valuation["peg_ratio"] > 2:
        fundamental_conditions.insert(0, "A more attractive entry would require valuation to moderate or growth estimates to improve.")

    scorecard = [
        rm.rate_growth(revenue_growth, revenue_cagr, net_income_growth),
        rm.rate_profitability(net_margin, operating_margin, op_margin_change, roe),
        rm.rate_cash_quality(fcf_margin, ocf_to_net_income, latest_fcf),
        rm.rate_strength(debt_equity, negative_equity, net_cash, interest_coverage, current_ratio),
        rm.rate_valuation(valuation["forward_pe"], valuation["trailing_pe"], valuation["peg_ratio"], fcf_yield, revenue_growth),
    ]
    verdict = rm.overall(scorecard, f_score)
    assessment = verdict["assessment"]
    takeaways = rm.takeaways(scorecard, assessment)

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
        "score": verdict["score"],
        "scorecard": scorecard,
        "takeaways": takeaways,
        "piotroski": f_score,
        "price_context": {"price": price_now, "high_52w": high_52w, "low_52w": low_52w, "below_high_pct": below_high,
                          "range_position_pct": range_position, "target_upside_pct": target_upside},
        "summary": (f"Fundamental profile: {assessment}"
                    + (f" ({verdict['score']}/100 across {verdict['known_categories']} areas)" if verdict["score"] is not None else "")
                    + ". This is an evidence summary from public financial data, not investment advice; review the scorecard, concerns and valuation before deciding."),
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
