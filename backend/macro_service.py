"""
macro_service.py
Keyless US macro data via yfinance.
Fetches live values + recent news for key macroeconomic indicators and caches
results in memory for 15 minutes to avoid hammering Yahoo Finance.
"""

import yfinance as yf

import macro_context
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor
import time

# ── Core US macro tickers ──────────────────────────────────────────────────────────────────────────────────────────
# kind "yield": Yahoo quotes yields in percent (5.2 = 5.2%), so moves are shown in basis points, not as a percent of a percent.
MACRO_TICKERS = {
    "^VIX":     {"kind": "index", "name": "Market Volatility (VIX)",       "unit": "pts", "description": "Fear gauge: spikes during market stress. A high VIX means bots should size down and expect wider swings."},
    "^TNX":     {"kind": "yield", "name": "US 10-Year Treasury Yield",      "unit": "%",   "description": "Rising yields tighten financial conditions and compete with stocks. Watch for sudden spikes above recent highs."},
    "^IRX":     {"kind": "yield", "name": "3-Month T-Bill Yield",           "unit": "%",   "description": "A close proxy for the Fed's policy rate (it is the market yield on 13-week bills, not the Fed funds rate itself)."},
    "DX-Y.NYB": {"kind": "index", "name": "US Dollar Index (DXY)",          "unit": "pts", "description": "A strong dollar weighs on commodities, crypto and multinational earnings; a weak dollar often lifts risk assets."},
    "^GSPC":    {"kind": "index", "name": "S&P 500",                        "unit": "pts", "description": "The broad US equity market: the backdrop every stock bot trades against."},
    "CL=F":     {"kind": "index", "name": "WTI Crude Oil",                  "unit": "$",   "description": "Energy prices feed straight into inflation and into energy-sector earnings."},
    "GC=F":     {"kind": "index", "name": "Gold",                           "unit": "$",   "description": "Often rises when real yields fall or investors want safety; a drop alongside rising yields is typical of tighter money."},
}

# ── In-memory cache (shared across all requests) ───────────────────────────────
_cache = {
    "data": None,
    "last_updated": None,
}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 900   # 15 minutes


def _round(value, digits=3):
    return float(round(float(value), digits))


def _level_for(symbol, kind, value):
    if symbol == "^VIX":
        return macro_context.vix_level(value)
    if kind == "yield" and symbol == "^TNX":
        return macro_context.yield_level(value)
    return None


def _summarise(symbol, meta, closes):
    """Everything the dialog shows for one series of daily closes (oldest first)."""
    current = _round(closes.iloc[-1])
    previous = _round(closes.iloc[-2]) if len(closes) > 1 else None
    week_ago = _round(closes.iloc[-6]) if len(closes) > 5 else None
    kind = meta.get("kind", "index")
    change_pct = round((current - previous) / previous * 100, 2) if previous else 0.0
    out = {
        **{k: v for k, v in meta.items() if k != "kind"},
        "kind": kind,
        "ticker": symbol,
        "current_value": current,
        "previous_value": previous,
        "change_pct": change_pct,
        "direction": "up" if (current - (previous or current)) >= 0 else "down",
        "as_of": closes.index[-1].strftime("%Y-%m-%d"),
    }
    window = closes.iloc[-252:]
    low, high = float(window.min()), float(window.max())
    out["low_52w"], out["high_52w"] = _round(low), _round(high)
    out["range_position_pct"] = round((current - low) / (high - low) * 100, 1) if high > low else None
    out["sparkline"] = [_round(v) for v in closes.iloc[-30:]]
    if kind == "yield":
        change_bp = round((current - previous) * 100, 1) if previous is not None else 0.0
        out["change_bp"] = change_bp
        out["change_display"] = f"{change_bp:+.1f} bp"
        out["week_change_bp"] = round((current - week_ago) * 100, 1) if week_ago is not None else None
    else:
        out["change_display"] = f"{change_pct:+.2f}%"
        out["week_change_pct"] = round((current - week_ago) / week_ago * 100, 2) if week_ago else None
    out["level"] = _level_for(symbol, kind, current)
    out["range_note"] = macro_context.range_label(out["range_position_pct"])
    return out


def _fetch_one(symbol, meta):
    """One ticker: its summary and recent news, or an error entry. Runs in a worker thread."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y", interval="1d")
        if hist.empty:
            return symbol, {"error": f"No data for {symbol}", **meta, "ticker": symbol}, None
        closes = hist["Close"].dropna()
        if closes.empty:
            return symbol, {"error": f"No closing prices for {symbol}", **meta, "ticker": symbol}, None
        summary = _summarise(symbol, meta, closes)

        # Pull up to 5 recent news headlines
        raw_news = []
        try:
            news_list = ticker.news or []
            for item in news_list[:5]:
                content = item.get("content", {})
                title = content.get("title") or item.get("title", "")
                provider = (content.get("provider") or {}).get("displayName", item.get("publisher", ""))
                link = (content.get("canonicalUrl") or {}).get("url", item.get("link", ""))
                pub_dt = content.get("pubDate") or item.get("providerPublishTime", "")
                raw_news.append({"title": title, "publisher": provider, "link": link, "published": str(pub_dt)})
        except Exception:
            pass
        summary["recent_news"] = raw_news
        return symbol, summary, closes
    except Exception as e:
        return symbol, {"error": str(e), **meta, "ticker": symbol}, None


def _fetch_all_macro() -> dict:
    """
    Fetches one year of daily closes (for the 52-week range, week change and sparkline) and recent news for every
    ticker in MACRO_TICKERS, plus the derived 10Y-3M yield curve. The tickers are fetched in parallel, so adding
    instruments does not make the dashboard card wait longer (it answers within a few seconds on a cold cache).
    Results are keyed by ticker symbol, in MACRO_TICKERS order.
    """
    with ThreadPoolExecutor(max_workers=len(MACRO_TICKERS)) as pool:
        fetched = list(pool.map(lambda item: _fetch_one(*item), MACRO_TICKERS.items()))
    results, series = {}, {}
    for symbol, summary, closes in fetched:
        results[symbol] = summary
        if closes is not None:
            series[symbol] = closes
    curve = _yield_curve(series)
    if curve:
        results["CURVE"] = curve
    return results


def _yield_curve(series):
    """10-year minus 3-month yield on the dates both have a close (the NY Fed recession-signal curve)."""
    if "^TNX" not in series or "^IRX" not in series:
        return None
    joined = (series["^TNX"] - series["^IRX"]).dropna()
    if len(joined) < 2:
        return None
    meta = {"kind": "spread", "name": "Yield Curve (10Y − 3M)", "unit": "pts",
            "description": "10-year yield minus 3-month yield. Below zero (inverted) has preceded most US recessions, with a long lag."}
    current, previous = _round(joined.iloc[-1], 3), _round(joined.iloc[-2], 3)
    window = joined.iloc[-252:]
    week_ago = _round(joined.iloc[-6], 3) if len(joined) > 5 else None
    return {
        **{k: v for k, v in meta.items() if k != "kind"}, "kind": "spread", "ticker": "10Y-3M",
        "current_value": current, "previous_value": previous,
        "change_pct": 0.0, "direction": "up" if current >= previous else "down",
        "change_bp": round((current - previous) * 100, 1), "change_display": f"{(current - previous) * 100:+.1f} bp",
        "week_change_bp": round((current - week_ago) * 100, 1) if week_ago is not None else None,
        "as_of": joined.index[-1].strftime("%Y-%m-%d"),
        "low_52w": _round(window.min()), "high_52w": _round(window.max()),
        "range_position_pct": round((current - float(window.min())) / (float(window.max()) - float(window.min())) * 100, 1) if float(window.max()) > float(window.min()) else None,
        "sparkline": [_round(v) for v in joined.iloc[-30:]],
        "level": macro_context.curve_level(current), "range_note": None, "recent_news": [],
    }


def get_macro_data(force_refresh: bool = False) -> dict:
    """
    Public entry point.  Returns cached data if fresh, otherwise re-fetches.
    """
    with _cache_lock:
        now = time.time()
        if (
            not force_refresh
            and _cache["data"] is not None
            and _cache["last_updated"] is not None
            and (now - _cache["last_updated"]) < CACHE_TTL_SECONDS
        ):
            return _cache["data"]

        fresh = _fetch_all_macro()
        _cache["data"] = fresh
        _cache["last_updated"] = now
        return fresh
