"""
macro_service.py
Keyless US macro data via yfinance.
Fetches live values + recent news for key macroeconomic indicators and caches
results in memory for 15 minutes to avoid hammering Yahoo Finance.
"""

import yfinance as yf
from datetime import datetime, timedelta
import threading
import time

# ── Core US macro tickers ──────────────────────────────────────────────────────
MACRO_TICKERS = {
    "^VIX":     {"name": "Market Volatility (VIX)",           "unit": "pts",  "description": "Fear gauge – spikes during market stress. High VIX = bots should reduce position sizes."},
    "^TNX":     {"name": "US 10-Year Treasury Yield",          "unit": "%",    "description": "Rising yields = tighter financial conditions. Watch for sudden spikes above 4.5%."},
    "^IRX":     {"name": "Fed Rate (13-Week T-Bill)",          "unit": "%",    "description": "Tracks short-term Fed policy rate. Steady = stable; sharp moves = policy shift."},
    "DX-Y.NYB": {"name": "US Dollar Index (DXY)",              "unit": "pts",  "description": "Strong USD hurts commodities & crypto. Weak USD often lifts risk assets."},
}

# ── In-memory cache (shared across all requests) ───────────────────────────────
_cache = {
    "data": None,
    "last_updated": None,
}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 900   # 15 minutes


def _fetch_all_macro() -> dict:
    """
    Fetches current + previous values and recent news for all MACRO_TICKERS.
    Returns a dict of results keyed by ticker symbol.
    """
    results = {}

    for symbol, meta in MACRO_TICKERS.items():
        try:
            ticker = yf.Ticker(symbol)

            # Grab 30 days of history so we always have a "previous" value
            end_dt   = datetime.today()
            start_dt = end_dt - timedelta(days=30)
            hist = ticker.history(start=start_dt, end=end_dt)

            if hist.empty:
                results[symbol] = {"error": f"No data for {symbol}", **meta}
                continue

            closes = hist["Close"].dropna()
            current_val  = float(round(closes.iloc[-1], 3))
            previous_val = float(round(closes.iloc[-2], 3)) if len(closes) > 1 else None
            change_pct   = round(((current_val - previous_val) / previous_val) * 100, 2) if previous_val else 0.0

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

            results[symbol] = {
                **meta,
                "ticker":         symbol,
                "current_value":  current_val,
                "previous_value": previous_val,
                "change_pct":     change_pct,
                "direction":      "up" if change_pct >= 0 else "down",
                "recent_news":    raw_news,
                "as_of":          closes.index[-1].strftime("%Y-%m-%d"),
            }

        except Exception as e:
            results[symbol] = {"error": str(e), **meta, "ticker": symbol}

    return results


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
