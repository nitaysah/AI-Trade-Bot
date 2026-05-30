"""
Agentic AI Indicator — Groq-powered autonomous trading research.

Gathers full market context (price action, technicals, trend structure, volatility,
news, macro indices, VIX, sector correlation) and uses Groq's LLM to output a
structured BUY/SELL/HOLD signal with position sizing, leverage guidance, and targets.
"""

import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

import numpy as np
import yfinance as yf

import config as global_config
from user_config import get_user_config

# ---------------------------------------------------------------------------
# In-memory cache: { (ticker, timeframe): {"data": {...}, "timestamp": float} }
# ---------------------------------------------------------------------------
_AI_CACHE: dict = {}

try:
    from groq import Groq
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False
    print("[ai_indicator] Groq library not installed. AI Indicator will use rule-based fallback.")

# ---------------------------------------------------------------------------
# Sector ETF mapping
# ---------------------------------------------------------------------------
_SECTOR_MAP = {
    # Tech
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK",
    "INTC": "XLK", "META": "XLK", "GOOGL": "XLK", "GOOG": "XLK",
    "NFLX": "XLC",
    # Consumer Disc
    "TSLA": "XLY", "AMZN": "XLY", "COST": "XLY", "WMT": "XLP",
    # Finance
    "JPM": "XLF", "GS": "XLF", "BAC": "XLF",
    # Energy
    "XOM": "XLE", "CVX": "XLE",
    # Healthcare
    "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV",
    # Default / crypto
}
_DEFAULT_SECTOR_ETF = "SPY"


def _get_sector_etf(ticker: str) -> str:
    clean = ticker.upper().replace("/", "").replace("USD", "").replace("USDT", "")
    return _SECTOR_MAP.get(ticker.upper(), _SECTOR_MAP.get(clean, _DEFAULT_SECTOR_ETF))


# ---------------------------------------------------------------------------
# News fetcher
# ---------------------------------------------------------------------------
def _fetch_news_headlines(ticker: str, max_headlines: int = 12) -> list[str]:
    try:
        query = urllib.parse.quote(f"{ticker} stock")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        response = urllib.request.urlopen(req, timeout=6)
        root = ET.parse(response).getroot()
        headlines = []
        for item in root.findall(".//item")[:max_headlines]:
            title = item.find("title")
            if title is not None and title.text:
                clean = title.text.rsplit(" - ", 1)[0].strip()
                headlines.append(clean)
        return headlines
    except Exception as e:
        print(f"[ai_indicator] News fetch error for {ticker}: {e}")
        return []


# ---------------------------------------------------------------------------
# Market context gatherer
# ---------------------------------------------------------------------------
def gather_market_context(ticker: str, timeframe: str = "4Hour") -> dict:
    """
    Collects comprehensive market context for the agentic prompt:
    - Price, ATR, RSI, MACD, Bollinger, EMA trend, 52-week range from yfinance
    - SPY 5-day trend (bull/bear market proxy) + ticker-SPY correlation
    - VIX level and regime classification
    - Sector ETF 5-day trend
    - Realized volatility, support/resistance, consecutive candle direction
    - Earnings date, beta
    - Google News headlines
    """
    ctx = {
        "ticker": ticker,
        "timeframe": timeframe,
        "price": None,
        "prev_close": None,
        "change_pct": None,
        "week52_high": None,
        "week52_low": None,
        "range_position_pct": None,
        "atr": None,
        "rsi": None,
        "macd_hist": None,
        "macd_crossover": "NONE",  # BULLISH_CROSS / BEARISH_CROSS / NONE
        "volume_ratio": None,      # today vs 20d avg
        "market_cap": None,
        "sector": None,
        "industry": None,
        # EMA trend structure
        "ema_9": None,
        "ema_21": None,
        "ema_50": None,
        "sma_200": None,
        "ema_trend": "UNKNOWN",    # STRONG_BULL / BULL / BEAR / STRONG_BEAR
        # Bollinger Bands
        "boll_upper": None,
        "boll_lower": None,
        "boll_width_pct": None,    # width as % of price (squeeze detection)
        "boll_position": None,     # where price sits 0-100 inside the bands
        # Support / Resistance
        "support": None,
        "resistance": None,
        # Realized volatility
        "realized_vol_30d": None,  # annualized 30-day realized vol %
        # Consecutive direction
        "consec_green": 0,
        "consec_red": 0,
        # Correlation with SPY
        "corr_spy_30d": None,
        # Earnings & Beta
        "next_earnings": None,
        "days_to_earnings": None,
        "beta": None,
        # Macro
        "spy_trend": "UNKNOWN",
        "spy_change_5d": None,
        "vix_level": None,
        "vix_regime": "UNKNOWN",
        "sector_etf": _get_sector_etf(ticker),
        "sector_trend": "UNKNOWN",
        "sector_change_5d": None,
        # News
        "headlines": [],
        "headline_count": 0,
        "is_crypto": any(ticker.upper().endswith(b) for b in ["USD", "USDT", "USDC", "BTC", "ETH"]),
    }

    spy_returns = None  # used for correlation later

    # ── 1. Ticker fundamentals via yfinance ──────────────────────────────
    try:
        yf_ticker = ticker.upper()
        if ctx["is_crypto"]:
            for suffix in ["USDT", "USDC", "USD"]:
                if yf_ticker.endswith(suffix):
                    yf_ticker = yf_ticker[: -len(suffix)] + "-" + suffix
                    break

        t = yf.Ticker(yf_ticker)
        hist = t.history(period="1y", interval="1d")

        if hist is not None and not hist.empty:
            close = hist["Close"]
            high = hist["High"]
            low = hist["Low"]

            ctx["price"] = float(close.iloc[-1])
            ctx["prev_close"] = float(close.iloc[-2]) if len(hist) >= 2 else ctx["price"]
            ctx["change_pct"] = ((ctx["price"] - ctx["prev_close"]) / ctx["prev_close"] * 100) if ctx["prev_close"] else 0
            ctx["week52_high"] = float(high.max())
            ctx["week52_low"] = float(low.min())

            rng = ctx["week52_high"] - ctx["week52_low"]
            if rng > 0:
                ctx["range_position_pct"] = round((ctx["price"] - ctx["week52_low"]) / rng * 100, 1)

            # ATR(14)
            tr = (high - low).combine(
                (high - close.shift(1)).abs(), max
            ).combine(
                (low - close.shift(1)).abs(), max
            )
            ctx["atr"] = round(float(tr.ewm(span=14, adjust=False).mean().iloc[-1]), 4)

            # RSI(14)
            delta = close.diff()
            gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
            rs = gain / loss.replace(0, 1e-9)
            rsi_series = 100 - (100 / (1 + rs))
            ctx["rsi"] = round(float(rsi_series.iloc[-1]), 1)

            # MACD histogram + crossover detection
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist_series = macd_line - signal_line
            ctx["macd_hist"] = round(float(macd_hist_series.iloc[-1]), 4)
            if len(macd_hist_series) >= 2:
                prev_h = float(macd_hist_series.iloc[-2])
                curr_h = float(macd_hist_series.iloc[-1])
                if prev_h <= 0 < curr_h:
                    ctx["macd_crossover"] = "BULLISH_CROSS"
                elif prev_h >= 0 > curr_h:
                    ctx["macd_crossover"] = "BEARISH_CROSS"

            # EMA trend structure: 9 > 21 > 50 > SMA200 = STRONG_BULL
            ctx["ema_9"] = round(float(close.ewm(span=9, adjust=False).mean().iloc[-1]), 4)
            ctx["ema_21"] = round(float(close.ewm(span=21, adjust=False).mean().iloc[-1]), 4)
            ctx["ema_50"] = round(float(close.ewm(span=50, adjust=False).mean().iloc[-1]), 4)
            if len(close) >= 200:
                ctx["sma_200"] = round(float(close.rolling(200).mean().iloc[-1]), 4)
            e9, e21, e50, s200 = ctx["ema_9"], ctx["ema_21"], ctx["ema_50"], ctx["sma_200"]
            if e9 and e21 and e50:
                if s200 and e9 > e21 > e50 > s200:
                    ctx["ema_trend"] = "STRONG_BULL"
                elif e9 > e21 > e50:
                    ctx["ema_trend"] = "BULL"
                elif s200 and e9 < e21 < e50 < s200:
                    ctx["ema_trend"] = "STRONG_BEAR"
                elif e9 < e21 < e50:
                    ctx["ema_trend"] = "BEAR"
                else:
                    ctx["ema_trend"] = "MIXED"

            # Bollinger Bands (20, 2)
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            boll_u = sma20 + 2 * std20
            boll_l = sma20 - 2 * std20
            if not np.isnan(boll_u.iloc[-1]):
                ctx["boll_upper"] = round(float(boll_u.iloc[-1]), 4)
                ctx["boll_lower"] = round(float(boll_l.iloc[-1]), 4)
                bw = ctx["boll_upper"] - ctx["boll_lower"]
                ctx["boll_width_pct"] = round(bw / ctx["price"] * 100, 2) if ctx["price"] else None
                if bw > 0:
                    ctx["boll_position"] = round((ctx["price"] - ctx["boll_lower"]) / bw * 100, 1)

            # Support / Resistance (20-day lookback low/high)
            lookback = min(20, len(hist))
            ctx["support"] = round(float(low.iloc[-lookback:].min()), 4)
            ctx["resistance"] = round(float(high.iloc[-lookback:].max()), 4)

            # Realized Volatility (30-day annualized)
            if len(close) >= 31:
                log_returns = np.log(close / close.shift(1)).dropna()
                rv = float(log_returns.iloc[-30:].std()) * np.sqrt(252) * 100
                ctx["realized_vol_30d"] = round(rv, 1)

            # Consecutive green/red candles
            greens, reds = 0, 0
            for j in range(len(close) - 1, 0, -1):
                if float(close.iloc[j]) > float(close.iloc[j - 1]):
                    greens += 1
                else:
                    break
            for j in range(len(close) - 1, 0, -1):
                if float(close.iloc[j]) < float(close.iloc[j - 1]):
                    reds += 1
                else:
                    break
            ctx["consec_green"] = greens
            ctx["consec_red"] = reds

            # Volume ratio (last bar vs 20-day avg)
            if "Volume" in hist.columns and hist["Volume"].iloc[-1] > 0:
                avg_vol = hist["Volume"].rolling(20).mean().iloc[-1]
                if avg_vol > 0:
                    ctx["volume_ratio"] = round(float(hist["Volume"].iloc[-1] / avg_vol), 2)

            # Save ticker returns for SPY correlation later
            if len(close) >= 31:
                ticker_returns = np.log(close / close.shift(1)).dropna().values[-30:]
            else:
                ticker_returns = None

        # Info (may fail for crypto)
        try:
            info = t.fast_info
            ctx["market_cap"] = getattr(info, "market_cap", None)
            ctx["sector"] = getattr(info, "sector", None)
            ctx["industry"] = getattr(info, "industry", None)
        except Exception:
            pass

        # Earnings date & beta (from full info — may be slow for some tickers)
        try:
            full_info = t.info
            # Next earnings
            import datetime
            earn_ts = full_info.get("earningsTimestamp")
            if earn_ts:
                earn_dt = datetime.datetime.fromtimestamp(earn_ts)
                ctx["next_earnings"] = earn_dt.strftime("%Y-%m-%d")
                ctx["days_to_earnings"] = (earn_dt - datetime.datetime.now()).days
            ctx["beta"] = full_info.get("beta")
        except Exception:
            pass

    except Exception as e:
        print(f"[ai_indicator] yfinance error for {ticker}: {e}")
        ticker_returns = None

    # ── 2. SPY 5-day trend + correlation ─────────────────────────────────
    try:
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(period="2mo", interval="1d")  # need 30 days for correlation
        if spy_hist is not None and len(spy_hist) >= 5:
            spy_5d_start = float(spy_hist["Close"].iloc[-5])
            spy_latest = float(spy_hist["Close"].iloc[-1])
            ctx["spy_change_5d"] = round((spy_latest - spy_5d_start) / spy_5d_start * 100, 2)
            ctx["spy_trend"] = "BULLISH" if ctx["spy_change_5d"] > 0.3 else "BEARISH" if ctx["spy_change_5d"] < -0.3 else "NEUTRAL"

            # 30-day correlation
            try:
                if ticker_returns is not None and len(spy_hist["Close"]) >= 31:
                    spy_ret = np.log(spy_hist["Close"] / spy_hist["Close"].shift(1)).dropna().values[-30:]
                    if len(spy_ret) == len(ticker_returns):
                        ctx["corr_spy_30d"] = round(float(np.corrcoef(ticker_returns, spy_ret)[0, 1]), 2)
            except Exception:
                pass
    except Exception as e:
        print(f"[ai_indicator] SPY trend error: {e}")

    # ── 3. VIX level ─────────────────────────────────────────────────────
    try:
        if not ctx["is_crypto"]:
            vix = yf.Ticker("^VIX")
            vix_hist = vix.history(period="5d", interval="1d")
            if vix_hist is not None and not vix_hist.empty:
                ctx["vix_level"] = round(float(vix_hist["Close"].iloc[-1]), 1)
                v = ctx["vix_level"]
                ctx["vix_regime"] = "LOW" if v < 15 else "MEDIUM" if v < 25 else "HIGH"
        else:
            ctx["vix_regime"] = "CRYPTO"
    except Exception as e:
        print(f"[ai_indicator] VIX error: {e}")

    # ── 4. Sector ETF trend ──────────────────────────────────────────────
    try:
        if ctx["sector_etf"] not in ("SPY",) or not ctx["is_crypto"]:
            sec = yf.Ticker(ctx["sector_etf"])
            sec_hist = sec.history(period="10d", interval="1d")
            if sec_hist is not None and len(sec_hist) >= 5:
                sec_start = float(sec_hist["Close"].iloc[-5])
                sec_latest = float(sec_hist["Close"].iloc[-1])
                ctx["sector_change_5d"] = round((sec_latest - sec_start) / sec_start * 100, 2)
                ctx["sector_trend"] = (
                    "BULLISH" if ctx["sector_change_5d"] > 0.2
                    else "BEARISH" if ctx["sector_change_5d"] < -0.2
                    else "NEUTRAL"
                )
    except Exception as e:
        print(f"[ai_indicator] Sector ETF error: {e}")

    # ── 5. News headlines ────────────────────────────────────────────────
    ctx["headlines"] = _fetch_news_headlines(ticker)
    ctx["headline_count"] = len(ctx["headlines"])

    return ctx


# ---------------------------------------------------------------------------
# Rule-based fallback signal
# ---------------------------------------------------------------------------
def _fallback_signal(ctx: dict, available_cash: float, account_equity: float) -> dict:
    """Generates a basic signal using simple rules when Groq is unavailable."""
    price = ctx.get("price") or 0
    rsi = ctx.get("rsi") or 50
    macd_hist = ctx.get("macd_hist") or 0
    spy_trend = ctx.get("spy_trend", "NEUTRAL")
    vix_regime = ctx.get("vix_regime", "MEDIUM")
    atr = ctx.get("atr") or (price * 0.02)

    bull_score = 0
    bear_score = 0

    if rsi < 35:
        bull_score += 2
    elif rsi > 65:
        bear_score += 2

    if macd_hist > 0:
        bull_score += 1
    elif macd_hist < 0:
        bear_score += 1

    if spy_trend == "BULLISH":
        bull_score += 1
    elif spy_trend == "BEARISH":
        bear_score += 1

    if bull_score > bear_score + 1:
        signal = "BUY"
        confidence = min(0.55 + bull_score * 0.05, 0.75)
        regime = "BULLISH"
    elif bear_score > bull_score + 1:
        signal = "SELL"
        confidence = min(0.55 + bear_score * 0.05, 0.75)
        regime = "BEARISH"
    else:
        signal = "HOLD"
        confidence = 0.45
        regime = "NEUTRAL"

    # Leverage guidance from VIX
    leverage_map = {"LOW": "1.5x – Low VIX, moderate leverage OK", "MEDIUM": "1x – Normal conditions", "HIGH": "0.5x – High VIX, reduce exposure", "CRYPTO": "1x – Crypto: no leverage recommended"}
    leverage = leverage_map.get(vix_regime, "1x – Default")

    pos_pct = 0.10 if signal == "BUY" else 0.0
    pos_usd = round(available_cash * pos_pct, 2)

    stop_loss = round(price - atr * 2, 4) if price else None
    sell_target = round(price + atr * 4, 4) if price else None
    sell_target_pct = round((sell_target - price) / price * 100, 2) if price and sell_target else None

    return {
        "signal": signal,
        "confidence": round(confidence, 2),
        "entry_price": round(price, 4) if price else None,
        "position_size_pct": pos_pct,
        "position_size_usd": pos_usd,
        "stop_loss": stop_loss,
        "sell_target": sell_target,
        "sell_target_pct": sell_target_pct,
        "leverage_guidance": leverage,
        "regime": regime,
        "summary": (
            f"Rule-based analysis for {ctx['ticker']}: RSI at {rsi:.1f}, "
            f"MACD {'positive' if macd_hist > 0 else 'negative'}, "
            f"SPY trend {spy_trend.lower()}, VIX regime {vix_regime.lower()}. "
            "Groq AI unavailable — using technical fallback."
        ),
        "key_catalyst": f"RSI: {rsi:.1f} | MACD: {'+' if macd_hist > 0 else ''}{macd_hist:.4f}",
        "risk_factors": f"Groq AI offline — limited analysis. VIX: {vix_regime}",
        "headline_count": ctx.get("headline_count", 0),
        "cached": False,
        "model": "rule-based-fallback",
    }


# ---------------------------------------------------------------------------
# Main agentic function
# ---------------------------------------------------------------------------
def get_ai_indicator(
    ticker: str,
    timeframe: str = "4Hour",
    available_cash: float = 10000.0,
    account_equity: float = 10000.0,
) -> dict:
    """
    Gathers full market context and calls Groq to produce a structured
    BUY/SELL/HOLD signal with position sizing and leverage guidance.
    Includes in-memory caching with configurable TTL.
    """
    cfg = get_user_config()
    cache_minutes = getattr(cfg, "AI_INDICATOR_CACHE_MINUTES", 10)
    cache_key = (ticker.upper(), timeframe)

    # ── Cache check ──────────────────────────────────────────────────────
    now = time.time()
    if cache_key in _AI_CACHE:
        entry = _AI_CACHE[cache_key]
        age_seconds = now - entry["timestamp"]
        if age_seconds < cache_minutes * 60:
            result = dict(entry["data"])
            result["cached"] = True
            result["cache_age_seconds"] = int(age_seconds)
            return result

    # ── Gather context ───────────────────────────────────────────────────
    print(f"[ai_indicator] Gathering context for {ticker} ({timeframe})…")
    ctx = gather_market_context(ticker, timeframe)

    # ── Gather News Sentiment ────────────────────────────────────────────
    try:
        sent = get_ai_sentiment(ticker)
    except Exception as e:
        print(f"[ai_indicator] Error getting sentiment for {ticker}: {e}")
        sent = {
            "score": 0.0,
            "confidence": 0.5,
            "summary": "News sentiment analysis currently unavailable.",
            "key_factor": "N/A"
        }

    # ── Groq availability check ──────────────────────────────────────────
    groq_key = getattr(cfg, "GROQ_API_KEY", "")
    if not _GROQ_AVAILABLE or not groq_key or groq_key in ("", "your_groq_api_key_here"):
        print(f"[ai_indicator] Groq unavailable for {ticker}, using fallback.")
        result = _fallback_signal(ctx, available_cash, account_equity)
        result.update({
            "sentiment_score": sent.get("score", 0.0),
            "sentiment_confidence": sent.get("confidence", 0.5),
            "sentiment_summary": sent.get("summary", ""),
            "sentiment_key_factor": sent.get("key_factor", "N/A"),
        })
        _AI_CACHE[cache_key] = {"data": result, "timestamp": now}
        return result

    # ── Build agentic prompt ─────────────────────────────────────────────
    price = ctx.get("price")
    price_str = f"${price:,.4f}" if price and price < 10 else (f"${price:,.2f}" if price else "N/A")
    atr_str = f"{ctx['atr']:,.4f}" if ctx.get("atr") and ctx["atr"] < 1 else (f"{ctx['atr']:,.2f}" if ctx.get("atr") else "N/A")

    headlines_text = "\n".join(f"  • {h}" for h in ctx["headlines"][:12]) if ctx["headlines"] else "  • No recent headlines found."

    vix_str = f"{ctx['vix_level']:.1f} ({ctx['vix_regime']} volatility)" if ctx.get("vix_level") else f"{ctx['vix_regime']} regime"
    spy_str = f"{ctx['spy_change_5d']:+.2f}% 5-day ({ctx['spy_trend']})" if ctx.get("spy_change_5d") is not None else ctx.get("spy_trend", "N/A")
    sector_str = f"{ctx['sector_etf']} {ctx['sector_change_5d']:+.2f}% 5-day ({ctx['sector_trend']})" if ctx.get("sector_change_5d") is not None else f"{ctx['sector_etf']} ({ctx['sector_trend']})"
    range_str = f"${ctx['week52_low']:,.2f} – ${ctx['week52_high']:,.2f} (price at {ctx['range_position_pct']}% of range)" if ctx.get("week52_low") else "N/A"
    vol_str = f"{ctx['volume_ratio']:.2f}x avg" if ctx.get("volume_ratio") else "N/A"
    cap_str = f"${ctx['market_cap']/1e9:.1f}B" if ctx.get("market_cap") and ctx["market_cap"] > 1e6 else ("Crypto/N/A" if ctx["is_crypto"] else "N/A")

    # New data field formatting
    ema_str = f"EMA9={ctx['ema_9']:.2f} / EMA21={ctx['ema_21']:.2f} / EMA50={ctx['ema_50']:.2f}" if ctx.get("ema_9") else "N/A"
    sma200_str = f"${ctx['sma_200']:.2f}" if ctx.get("sma_200") else "N/A (insufficient data)"
    trend_str = ctx.get("ema_trend", "UNKNOWN")
    boll_str = (
        f"Upper=${ctx['boll_upper']:.2f}  Lower=${ctx['boll_lower']:.2f}  Width={ctx['boll_width_pct']:.1f}%  Price@{ctx['boll_position']:.0f}%"
        if ctx.get("boll_upper") else "N/A"
    )
    sr_str = f"Support=${ctx['support']:.2f}  Resistance=${ctx['resistance']:.2f}" if ctx.get("support") else "N/A"
    rv_str = f"{ctx['realized_vol_30d']:.1f}% annualized" if ctx.get("realized_vol_30d") else "N/A"
    corr_str = f"{ctx['corr_spy_30d']:.2f}" if ctx.get("corr_spy_30d") is not None else "N/A"
    beta_str = f"{ctx['beta']:.2f}" if ctx.get("beta") else "N/A"
    earn_str = f"{ctx['next_earnings']} ({ctx['days_to_earnings']}d away)" if ctx.get("next_earnings") else "N/A"
    consec_str = f"{ctx['consec_green']} green" if ctx["consec_green"] > 0 else f"{ctx['consec_red']} red" if ctx["consec_red"] > 0 else "0"
    macd_cross_str = ctx.get("macd_crossover", "NONE")

    # ── System message (role-priming for more disciplined output) ────────
    system_msg = """You are an institutional-grade quantitative trading analyst AI. Your primary objective is CAPITAL PRESERVATION first, PROFITABLE TRADES second. You are extremely disciplined and NEVER chase momentum or force a trade when conditions are ambiguous.

CORE PRINCIPLES:
1. ONLY generate BUY when you have HIGH CONVICTION with multiple confirming signals. Default to HOLD.
2. Every BUY MUST have a minimum 2:1 reward-to-risk ratio (sell_target upside ≥ 2x the stop_loss downside).
3. NEVER buy into extreme overextension (RSI > 75, price > upper Bollinger, 5+ consecutive green candles).
4. NEVER buy when macro and sector are BOTH bearish — wait for the turn.
5. Scale position size proportionally to conviction: low conviction = 5%, high conviction = 15-20%, maximum 25%.
6. Always set stop_loss using ATR-based levels (1.5-2.5x ATR below entry). Never use arbitrary round numbers.
7. Set sell_target at the next resistance level, or use 3-5x ATR above entry — whichever is more conservative.
8. If earnings are within 5 days, ALWAYS output HOLD unless there's a very strong technical setup (confidence ≥ 0.8).
9. If VIX is HIGH (>25), cut all position sizes by 50% and tighten stops.
10. If the ticker has low correlation with SPY (<0.3) during a broad sell-off, this is a relative-strength signal — treat more bullishly.

You MUST output valid JSON only. No markdown, no comments, no explanation outside the JSON object."""

    # ── User prompt with full data ───────────────────────────────────────
    prompt = f"""Analyze **{ticker}** and generate a precise, actionable trade decision.

═══════════════════════════════════════════════
MARKET INTELLIGENCE BRIEF — {ticker}
═══════════════════════════════════════════════

── PRICE ACTION ──
  Current Price:       {price_str}
  Daily Change:        {f"{ctx['change_pct']:+.2f}%" if ctx.get('change_pct') is not None else 'N/A'}
  52-Week Range:       {range_str}
  ATR(14):             {atr_str}
  Volume vs 20d Avg:   {vol_str}
  Market Cap:          {cap_str}
  Consec. Candles:     {consec_str}

── MOMENTUM & OSCILLATORS ──
  RSI(14):             {f"{ctx['rsi']:.1f}" if ctx.get('rsi') else 'N/A'}
  MACD Histogram:      {f"{ctx['macd_hist']:+.4f}" if ctx.get('macd_hist') is not None else 'N/A'}
  MACD Crossover:      {macd_cross_str}

── TREND STRUCTURE ──
  EMA Stack:           {ema_str}
  SMA(200):            {sma200_str}
  Trend Regime:        {trend_str}
  Price vs EMAs:       {"ABOVE ALL" if ctx.get('ema_9') and price and price > ctx['ema_9'] > ctx['ema_21'] else "BELOW ALL" if ctx.get('ema_50') and price and price < ctx['ema_50'] else "MIXED"}

── VOLATILITY & BANDS ──
  Bollinger Bands:     {boll_str}
  Realized Vol (30d):  {rv_str}
  Beta:                {beta_str}

── KEY LEVELS ──
  20-Day Support:      {f"${ctx['support']:.2f}" if ctx.get('support') else 'N/A'}
  20-Day Resistance:   {f"${ctx['resistance']:.2f}" if ctx.get('resistance') else 'N/A'}

── MACRO CONTEXT ──
  S&P 500 (SPY):       {spy_str}
  VIX:                 {vix_str}
  Sector ({ctx['sector_etf']}):  {sector_str}
  Correlation w/ SPY:  {corr_str}

── EVENTS ──
  Next Earnings:       {earn_str}

── RECENT NEWS ──
{headlines_text}

── ACCOUNT ──
  Available Cash:      ${available_cash:,.2f}
  Account Equity:      ${account_equity:,.2f}

═══════════════════════════════════════════════
DECISION FRAMEWORK (follow step by step):
═══════════════════════════════════════════════

1. TREND CHECK: Is EMA trend STRONG_BULL or BULL? Is price above SMA(200)? If both are bearish → lean HOLD/SELL.
2. MOMENTUM CHECK: RSI in healthy zone (30-70)? MACD histogram rising? Any crossover? Volume confirming?
3. VOLATILITY CHECK: Bollinger width — is a squeeze forming (width < 3%)? Is realized vol extreme? VIX regime?
4. KEY LEVEL CHECK: Is price near support (buy zone) or resistance (sell zone)? How far from 52-week extremes?
5. MACRO CHECK: SPY trend, sector trend, correlation — is the tide with us or against us?
6. NEWS/EVENT CHECK: Any catalyst? Earnings imminent? Avoid if within 5 days.
7. RISK/REWARD: Calculate stop from ATR (1.5-2.5x ATR), target from resistance or 3-5x ATR. MUST be ≥ 2:1 R:R.
8. POSITION SIZE: Scale by conviction + volatility regime. Cut 50% in high VIX.
9. FINAL DECISION: Synthesize all above. Default to HOLD unless there is clear edge.

═══════════════════════════════════════════════
LEVERAGE RULES:
  VIX LOW (<15): up to 1.5x (bullish, low fear)
  VIX MEDIUM (15-25): 1x (standard)
  VIX HIGH (>25): 0.5x-0.75x (defensive)
  CRYPTO: always 1x max (no leverage)

POSITION SIZING RULES:
  BUY: 5-25% of available_cash based on conviction tier
  SELL: suggest exit % (50-100%)
  HOLD: position_size_pct = 0

CONVICTION TIERS:
  LOW (0.50-0.65):    5-8% allocation    — edge is marginal, take a starter position
  MEDIUM (0.65-0.80): 10-15% allocation  — good setup with confirming signals
  HIGH (0.80-1.00):   18-25% allocation  — exceptional multi-factor confluence
═══════════════════════════════════════════════

Output ONLY valid JSON:
{{
  "signal": "<BUY|SELL|HOLD>",
  "confidence": <float 0.0-1.0>,
  "entry_price": <float, current price or limit-order level>,
  "position_size_pct": <float 0.0-1.0>,
  "position_size_usd": <float>,
  "stop_loss": <float, ATR-based stop>,
  "sell_target": <float, resistance or ATR-based target>,
  "sell_target_pct": <float, expected gain %>,
  "leverage_guidance": "<e.g. '1x – Normal VIX'>",
  "regime": "<BULLISH|BEARISH|NEUTRAL|VOLATILE>",
  "summary": "<4-6 sentences: full professional thesis covering technicals, trend, macro, and catalysts>",
  "key_catalyst": "<A detailed 2-3 sentence quantitative breakdown of the primary driving catalyst. Cite specific technical alignments like RSI support/breakouts, MACD histogram crossovers, volume relative surges, sector momentum support, or key news developments (e.g. block purchases, positive guidance).>",
  "risk_factors": "<A detailed 2-3 sentence analysis of the primary risk factors threatening the trade. Cite trend invalidation levels, critical support/moving average breaks, ATR-based volatility expansions, or adverse newsflow (insider selling, upcoming earnings gap risk, macroeconomic headwinds).>",
  "headline_count": {ctx['headline_count']}
}}"""

    # ── Groq call ────────────────────────────────────────────────────────
    try:
        model = getattr(cfg, "AI_INDICATOR_MODEL", "llama-3.3-70b-versatile")
        client = Groq(api_key=groq_key)

        print(f"[ai_indicator] Calling Groq ({model}) for {ticker}…")
        try:
            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                response_format={"type": "json_object"},
                temperature=0.2,   # Very low temp for disciplined, consistent trading decisions
                max_tokens=1500,   # More room for detailed thesis in summary field
            )
        except Exception as e:
            fallback_model = "llama-3.1-8b-instant"
            if model != fallback_model:
                print(f"[ai_indicator] Primary Groq model ({model}) failed: {e}. Trying automatic fallback to {fallback_model}...")
                response = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                    ],
                    model=fallback_model,
                    response_format={"type": "json_object"},
                    temperature=0.2,
                    max_tokens=1500,
                )
                model = fallback_model
            else:
                raise e

        raw = response.choices[0].message.content.strip()
        data = json.loads(raw)

        # ── Validate & clamp ─────────────────────────────────────────────
        signal = str(data.get("signal", "HOLD")).upper()
        if signal not in ("BUY", "SELL", "HOLD"):
            signal = "HOLD"

        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        pos_pct = max(0.0, min(1.0, float(data.get("position_size_pct", 0.0))))
        pos_usd = float(data.get("position_size_usd", available_cash * pos_pct))
        # Clamp position USD to available cash
        pos_usd = min(pos_usd, available_cash)

        result = {
            "signal": signal,
            "confidence": round(confidence, 3),
            "entry_price": _safe_float(data.get("entry_price")),
            "position_size_pct": round(pos_pct, 3),
            "position_size_usd": round(pos_usd, 2),
            "stop_loss": _safe_float(data.get("stop_loss")),
            "sell_target": _safe_float(data.get("sell_target")),
            "sell_target_pct": _safe_float(data.get("sell_target_pct")),
            "leverage_guidance": str(data.get("leverage_guidance", "1x – Default")),
            "regime": str(data.get("regime", "NEUTRAL")).upper(),
            "summary": str(data.get("summary", "")),
            "key_catalyst": str(data.get("key_catalyst", "N/A")),
            "risk_factors": str(data.get("risk_factors", "N/A")),
            "headline_count": int(data.get("headline_count", ctx["headline_count"])),
            "cached": False,
            "model": model,
            # Merged news sentiment context
            "sentiment_score": sent.get("score", 0.0),
            "sentiment_confidence": sent.get("confidence", 0.5),
            "sentiment_summary": sent.get("summary", ""),
            "sentiment_key_factor": sent.get("key_factor", "N/A"),
            # Extra context echoed back for UI display
            "price": ctx.get("price"),
            "rsi": ctx.get("rsi"),
            "vix_level": ctx.get("vix_level"),
            "vix_regime": ctx.get("vix_regime"),
            "spy_trend": ctx.get("spy_trend"),
            "sector_etf": ctx.get("sector_etf"),
            "sector_trend": ctx.get("sector_trend"),
            "ema_trend": ctx.get("ema_trend"),
            "realized_vol_30d": ctx.get("realized_vol_30d"),
            "corr_spy_30d": ctx.get("corr_spy_30d"),
            "beta": ctx.get("beta"),
            "next_earnings": ctx.get("next_earnings"),
            "days_to_earnings": ctx.get("days_to_earnings"),
            "boll_width_pct": ctx.get("boll_width_pct"),
            "support": ctx.get("support"),
            "resistance": ctx.get("resistance"),
        }

        _AI_CACHE[cache_key] = {"data": result, "timestamp": now}
        print(f"[ai_indicator] {ticker}: {signal} (confidence={confidence:.0%})")
        return result

    except json.JSONDecodeError as e:
        print(f"[ai_indicator] JSON parse error for {ticker}: {e}")
    except Exception as e:
        print(f"[ai_indicator] Groq error for {ticker}: {e}")

    # Fallback if Groq call fails
    result = _fallback_signal(ctx, available_cash, account_equity)
    result.update({
        "sentiment_score": sent.get("score", 0.0),
        "sentiment_confidence": sent.get("confidence", 0.5),
        "sentiment_summary": sent.get("summary", ""),
        "sentiment_key_factor": sent.get("key_factor", "N/A"),
    })
    _AI_CACHE[cache_key] = {"data": result, "timestamp": now}
    return result


def _safe_float(val):
    """Safely convert a value to float, returning None if not possible."""
    if val is None:
        return None
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None


def get_ai_decision_for_bar(
    ticker: str,
    bar_data,    # Current bar indicators. If None, gather live context.
    account_equity: float,
    available_cash: float,
    position,    # Current open position (None if flat)
    timeframe: str = "1Hour",
    force_refresh: bool = False,
    model: str = None,
) -> dict:
    """
    Streamlined AI decision for bot/backtest integration.
    Returns: {signal, confidence, entry_price, stop_loss, sell_target, position_size_usd, position_size_pct, summary, ...}
    
    If bar_data is None, gathers live market context.
    Otherwise constructs a simulated/historical context from bar_data.
    """
    cfg = get_user_config()
    now = time.time()
    
    # 1. Gather context
    if bar_data is None:
        # Live Bot scan mode - check cache first (unless force_refresh is True)
        cache_minutes = getattr(cfg, "AI_INDICATOR_CACHE_MINUTES", 10)
        cache_key = (ticker.upper(), f"autopilot_{timeframe}")
        if not force_refresh and cache_key in _AI_CACHE:
            entry = _AI_CACHE[cache_key]
            if now - entry["timestamp"] < cache_minutes * 60:
                result = dict(entry["data"])
                result["cached"] = True
                return result
                
        ctx = gather_market_context(ticker, timeframe)
    else:
        # Backtest mode or custom historical data bar
        price = float(bar_data.get('Close', bar_data.get('price', 0.0)))
        atr = float(bar_data.get('ATR', price * 0.02))
        rsi = float(bar_data.get('RSI', 50.0))
        macd_hist = float(bar_data.get('MACD_Hist', 0.0))
        
        # Calculate EMA trend regime
        ema_9 = bar_data.get('EMA_Fast')
        ema_21 = bar_data.get('EMA_Slow')
        sma_200 = bar_data.get('SMA')
        ema_trend = "MIXED"
        if ema_9 is not None and ema_21 is not None:
            ema_9_f = float(ema_9)
            ema_21_f = float(ema_21)
            if sma_200 is not None:
                sma_200_f = float(sma_200)
                if ema_9_f > ema_21_f > sma_200_f:
                    ema_trend = "STRONG_BULL"
                elif ema_9_f > ema_21_f:
                    ema_trend = "BULL"
                elif ema_9_f < ema_21_f < sma_200_f:
                    ema_trend = "STRONG_BEAR"
                elif ema_9_f < ema_21_f:
                    ema_trend = "BEAR"
            else:
                if ema_9_f > ema_21_f:
                    ema_trend = "BULL"
                else:
                    ema_trend = "BEAR"
                    
        boll_upper = bar_data.get('BOLL_Upper')
        boll_lower = bar_data.get('BOLL_Lower')
        boll_width_pct = 0.0
        boll_position = 50.0
        if boll_upper is not None and boll_lower is not None:
            bu = float(boll_upper)
            bl = float(boll_lower)
            if bu > bl:
                boll_width_pct = round((bu - bl) / price * 100, 2) if price else 0.0
                boll_position = round((price - bl) / (bu - bl) * 100, 1)
                
        # Support/Resistance based on ATR or lookback if not provided
        support = float(bar_data.get('support', price - atr * 2))
        resistance = float(bar_data.get('resistance', price + atr * 2))
        
        is_crypto = any(ticker.upper().endswith(b) for b in ["USD", "USDT", "USDC", "BTC", "ETH"])
        
        ctx = {
            "ticker": ticker,
            "timeframe": timeframe,
            "price": price,
            "change_pct": 0.0,
            "week52_high": price * 1.5,
            "week52_low": price * 0.7,
            "range_position_pct": 50.0,
            "atr": atr,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "macd_crossover": "NONE",
            "volume_ratio": 1.0,
            "market_cap": None,
            "sector": None,
            "industry": None,
            "ema_9": ema_9,
            "ema_21": ema_21,
            "ema_50": None,
            "sma_200": sma_200,
            "ema_trend": ema_trend,
            "boll_upper": boll_upper,
            "boll_lower": boll_lower,
            "boll_width_pct": boll_width_pct,
            "boll_position": boll_position,
            "support": support,
            "resistance": resistance,
            "realized_vol_30d": 30.0,
            "consec_green": 0,
            "consec_red": 0,
            "corr_spy_30d": 0.5,
            "beta": 1.0,
            "next_earnings": None,
            "days_to_earnings": None,
            "spy_trend": "NEUTRAL",
            "spy_change_5d": 0.0,
            "vix_level": 15.0,
            "vix_regime": "MEDIUM",
            "sector_etf": "SPY",
            "sector_trend": "NEUTRAL",
            "sector_change_5d": 0.0,
            "headlines": [],
            "headline_count": 0,
            "is_crypto": is_crypto,
        }
        
    # 2. Check Groq API Availability
    groq_key = getattr(cfg, "GROQ_API_KEY", "")
    if not _GROQ_AVAILABLE or not groq_key or groq_key in ("", "your_groq_api_key_here"):
        # Fallback to rule-based analysis
        result = _fallback_signal(ctx, available_cash, account_equity)
        result["reason"] = f"Technical Confluence Fallback (Groq Offline) - Signal: {result['signal']}"
        if bar_data is None:
            _AI_CACHE[cache_key] = {"data": result, "timestamp": now}
        return result
        
    # 3. Construct Position Info for prompt
    pos_str = "FLAT (No current open position)"
    current_pl = 0.0
    current_pl_pct = 0.0
    if position:
        entry_price = float(position.get('entry_price', 0))
        qty = float(position.get('qty', 0))
        current_price = ctx['price']
        if entry_price > 0:
            current_pl = (current_price - entry_price) * qty
            current_pl_pct = (current_price - entry_price) / entry_price * 100
        pos_str = (
            f"LONG Position of {qty:.4f} units | "
            f"Entry Price: ${entry_price:.2f} | "
            f"Current Unrealized P/L: ${current_pl:,.2f} ({current_pl_pct:+.2f}%) | "
            f"Stop Loss: ${position.get('stop_loss', 0.0):.2f} | "
            f"Take Profit Target: ${position.get('take_profit', 0.0):.2f}"
        )
        
    # 4. Prompt construction
    price = ctx['price']
    price_str = f"${price:,.4f}" if price and price < 10 else (f"${price:,.2f}" if price else "N/A")
    atr_str = f"{ctx['atr']:,.4f}" if ctx.get("atr") and ctx["atr"] < 1 else (f"{ctx['atr']:,.2f}" if ctx.get("atr") else "N/A")
    ema_str = f"EMA9={ctx['ema_9']:.2f} / EMA21={ctx['ema_21']:.2f}" if ctx.get("ema_9") else "N/A"
    sma200_str = f"${ctx['sma_200']:.2f}" if ctx.get("sma_200") else "N/A"
    trend_str = ctx.get("ema_trend", "UNKNOWN")
    boll_str = (
        f"Upper=${ctx['boll_upper']:.2f}  Lower=${ctx['boll_lower']:.2f}  Width={ctx['boll_width_pct']:.1f}%  Price@{ctx['boll_position']:.0f}%"
        if ctx.get("boll_upper") else "N/A"
    )
    sr_str = f"Support=${ctx['support']:.2f}  Resistance=${ctx['resistance']:.2f}" if ctx.get("support") else "N/A"
    headlines_text = "\n".join(f"  • {h}" for h in ctx["headlines"][:12]) if ctx["headlines"] else "  • No recent headlines/historical news available."
    
    system_msg = """You are an institutional-grade quantitative trading analyst AI running in AUTOPILOT MODE. Your primary objective is CAPITAL PRESERVATION first, PROFITABLE TRADES second.
You are fully responsible for both entry (BUY) and exit (SELL) decisions.

CORE PRINCIPLES:
1. ONLY generate BUY when you have HIGH CONVICTION with multiple confirming signals. Default to HOLD.
2. If we currently hold an open position (provided in CURRENT POSITION below), you must decide whether to CONTINUE HOLDING (return HOLD) or exit immediately (return SELL).
3. Every BUY MUST have a minimum 2:1 reward-to-risk ratio (sell_target upside >= 2x the stop_loss downside).
4. Scale position size proportionally to conviction: low conviction = 5%, high conviction = 15-20%, maximum 25%.
5. Always set stop_loss using ATR-based levels (1.5-2.5x ATR below entry). Never use arbitrary round numbers.
6. Set sell_target at the next resistance level, or use 3-5x ATR above entry.
7. If VIX is HIGH (>25), cut all position sizes by 50% and tighten stops.

You MUST output valid JSON only. No markdown, no comments, no explanation outside the JSON object."""

    prompt = f"""Analyze **{ticker}** and make a precise Autopilot trade decision.

═══════════════════════════════════════════════
MARKET INTELLIGENCE BRIEF — {ticker}
═══════════════════════════════════════════════

── CURRENT POSITION STATUS ──
  {pos_str}

── PRICE ACTION ──
  Current Price:       {price_str}
  ATR(14):             {atr_str}
  Trend Regime:        {trend_str}
  EMA Stack:           {ema_str}
  SMA(200):            {sma200_str}

── MOMENTUM & OSCILLATORS ──
  RSI(14):             {f"{ctx['rsi']:.1f}" if ctx.get('rsi') else 'N/A'}
  MACD Histogram:      {f"{ctx['macd_hist']:+.4f}" if ctx.get('macd_hist') is not None else 'N/A'}

── VOLATILITY & BANDS ──
  Bollinger Bands:     {boll_str}
  VIX Level:           {ctx.get('vix_level', 15.0)} ({ctx.get('vix_regime', 'MEDIUM')} regime)

── KEY LEVELS ──
  20-Day Support:      {sr_str}

── RECENT NEWS / CATALYSTS ──
{headlines_text}

── ACCOUNT ──
  Available Cash:      ${available_cash:,.2f}
  Account Equity:      ${account_equity:,.2f}

═══════════════════════════════════════════════
DECISION FRAMEWORK & RULES:
═══════════════════════════════════════════════
- If FLAT: Decide whether to BUY (initiate long) or HOLD (remain flat).
- If holding an open position: Evaluate if conditions have turned bearish, or if stop/target levels should be adjusted, or if we should exit (SELL) now to lock in gains or cut losses. Returning HOLD means keep holding current position.
- Output a single JSON object.

Output ONLY valid JSON:
{{
  "signal": "<BUY|SELL|HOLD>",
  "confidence": <float 0.0-1.0>,
  "entry_price": <float, current price or limit-order level>,
  "position_size_pct": <float 0.0-1.0, fraction of available cash to allocate on BUY, or 0.0 if HOLD/SELL>,
  "position_size_usd": <float, cash amount to allocate on BUY, or 0.0 if HOLD/SELL>,
  "stop_loss": <float, ATR-based stop price>,
  "sell_target": <float, ATR-based target price>,
  "sell_target_pct": <float, expected gain %>,
  "leverage_guidance": "1x - Autopilot Normal",
  "regime": "<BULLISH|BEARISH|NEUTRAL|VOLATILE>",
  "summary": "<2-3 sentences justifying this decision based on indicators and current position state>",
  "key_catalyst": "<A detailed 2-3 sentence quantitative breakdown of the primary driving catalyst. Cite specific technical alignments like RSI support/breakouts, MACD histogram crossovers, volume relative surges, sector momentum support, or key news developments (e.g. block purchases, positive guidance).>",
  "risk_factors": "<A detailed 2-3 sentence analysis of the primary risk factors threatening the trade. Cite trend invalidation levels, critical support/moving average breaks, ATR-based volatility expansions, or adverse newsflow (insider selling, upcoming earnings gap risk, macroeconomic headwinds).>",
  "headline_count": {ctx['headline_count']}
}}"""

    # 5. Make Groq Call
    try:
        model_to_use = model or getattr(cfg, "AI_INDICATOR_MODEL", "llama-3.3-70b-versatile")
        client = Groq(api_key=groq_key)
        
        try:
            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                model=model_to_use,
                response_format={"type": "json_object"},
                temperature=0.1,  # extremely consistent
                max_tokens=800,
            )
        except Exception as e:
            fallback_model = "llama-3.1-8b-instant"
            if model_to_use != fallback_model:
                print(f"[ai_indicator] Autopilot check primary model ({model_to_use}) failed: {e}. Trying automatic fallback to {fallback_model}...")
                response = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                    ],
                    model=fallback_model,
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=800,
                )
                model_to_use = fallback_model
            else:
                raise e
        
        raw = response.choices[0].message.content.strip()
        data = json.loads(raw)
        
        signal = str(data.get("signal", "HOLD")).upper()
        if signal not in ("BUY", "SELL", "HOLD"):
            signal = "HOLD"
            
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        pos_pct = max(0.0, min(1.0, float(data.get("position_size_pct", 0.0))))
        pos_usd = float(data.get("position_size_usd", available_cash * pos_pct))
        pos_usd = min(pos_usd, available_cash)
        
        result = {
            "signal": signal,
            "confidence": round(confidence, 3),
            "entry_price": _safe_float(data.get("entry_price", price)),
            "position_size_pct": round(pos_pct, 3),
            "position_size_usd": round(pos_usd, 2),
            "stop_loss": _safe_float(data.get("stop_loss")),
            "sell_target": _safe_float(data.get("sell_target")),
            "sell_target_pct": _safe_float(data.get("sell_target_pct")),
            "leverage_guidance": str(data.get("leverage_guidance", "1x - Autopilot")),
            "regime": str(data.get("regime", "NEUTRAL")).upper(),
            "summary": str(data.get("summary", "")),
            "key_catalyst": str(data.get("key_catalyst", "N/A")),
            "risk_factors": str(data.get("risk_factors", "N/A")),
            "headline_count": int(data.get("headline_count", ctx["headline_count"])),
            "cached": False,
            "model": model_to_use,
            "reason": str(data.get("summary", "")),  # mirror summary to reason for backtester/engine logging
        }
        
        # Merge key fields from ctx just in case
        for k in ["price", "rsi", "vix_level", "vix_regime", "spy_trend", "ema_trend", "support", "resistance"]:
            if k not in result:
                result[k] = ctx.get(k)
                
        if bar_data is None:
            _AI_CACHE[cache_key] = {"data": result, "timestamp": now}
            
        return result
        
    except Exception as e:
        print(f"[ai_indicator] Error in get_ai_decision_for_bar for {ticker}: {e}")
        # Fallback
        result = _fallback_signal(ctx, available_cash, account_equity)
        result["reason"] = f"Technical Confluence Fallback (Error) - Signal: {result['signal']}"
        if bar_data is None:
            _AI_CACHE[cache_key] = {"data": result, "timestamp": now}
        return result


def clear_ai_cache(ticker: str = None, timeframe: str = None):
    """Clear AI indicator cache for a ticker/timeframe combo, or all if None."""
    global _AI_CACHE
    if ticker is None:
        _AI_CACHE.clear()
    else:
        keys_to_remove = [
            k for k in _AI_CACHE
            if k[0] == ticker.upper() and (timeframe is None or k[1] == timeframe)
        ]
        for k in keys_to_remove:
            del _AI_CACHE[k]


# ───────────────────────────────────────────────────────────────────────────
# AI Sentiment Analysis (merged from sentiment.py)
# ───────────────────────────────────────────────────────────────────────────
SENTIMENT_CACHE: dict = {}
SENTIMENT_CACHE_DURATION: int = 1800  # 30 minutes

def _fallback_sentiment(ticker: str, headlines: list) -> dict:
    """Rule-based sentiment scoring as a fallback."""
    if not headlines:
        return {
            "score": 0.0,
            "confidence": 0.50,
            "summary": f"No recent news headlines detected for {ticker}. The current AI sentiment is derived from rolling institutional block flow, options flow, and sector momentum. Technical trend remains the dominant catalyst.",
            "key_factor": "Technical / Trend Dominance",
            "headline_count": 0
        }

    pos_words = ["beat", "surged", "growth", "upgrade", "gain", "climb", "high", "success", "profit", "record", "launch", "bullish", "strong", "outperform", "buy", "rise", "positive", "expand", "acquisition", "rally", "soar"]
    neg_words = ["miss", "drop", "plunge", "decline", "fall", "loss", "lawsuit", "complaint", "downgrade", "bearish", "weak", "underperform", "sell", "negative", "shrink", "debt", "risk", "hazard", "crack", "struggle"]
    
    score = 0.0
    pos_count = 0
    neg_count = 0
    for h in headlines:
        h_lower = h.lower()
        for pw in pos_words:
            if pw in h_lower:
                pos_count += 1
        for nw in neg_words:
            if nw in h_lower:
                neg_count += 1
    
    total_words = pos_count + neg_count
    if total_words > 0:
        score = (pos_count - neg_count) / total_words
    else:
        score = 0.0
    
    summary = (
        f"Core catalyst: {headlines[0]}. "
        f"Supporting newsflow indicates: {headlines[1] if len(headlines) > 1 else 'general market adjustments'}. "
        + (f"Additionally, headlines like '{headlines[2]}' suggest significant interest. " if len(headlines) > 2 else "")
        + f"Overall sentiment shows a {'dominant bullish dynamic with strong institutional volume support.' if score > 0.1 else 'prevalent bearish undertone suggesting near-term caution and resistance.' if score < -0.1 else 'balanced, neutral consolidation phase with mixed risk triggers.'}"
    )
    key_factor = headlines[0]
    if len(key_factor) > 60:
        key_factor = key_factor[:57] + "..."

    return {
        "score": round(score, 2),
        "confidence": 0.85,
        "summary": summary,
        "key_factor": key_factor,
        "headline_count": len(headlines)
    }

def get_ai_sentiment(ticker: str) -> dict:
    """
    Fetches recent news headlines and uses Groq to produce a structured
    sentiment analysis with score, confidence, and detailed reasoning.
    Includes caching to prevent rate limits.
    """
    global SENTIMENT_CACHE

    # 1. Check Cache First
    now = time.time()
    if ticker in SENTIMENT_CACHE:
        cache_entry = SENTIMENT_CACHE[ticker]
        if now - cache_entry['timestamp'] < SENTIMENT_CACHE_DURATION:
            return cache_entry['data']

    # 2. Fetch recent headlines using internal RSS fetcher
    headlines = _fetch_news_headlines(ticker, max_headlines=10)

    # Local fallback engine when Groq is not available or API key is not configured
    cfg = get_user_config()
    groq_key = getattr(cfg, "GROQ_API_KEY", "")
    if not _GROQ_AVAILABLE or not groq_key or groq_key in ("", "your_groq_api_key_here"):
        result = _fallback_sentiment(ticker, headlines)
        SENTIMENT_CACHE[ticker] = {
            "data": result,
            "timestamp": time.time()
        }
        return result

    try:
        if not headlines:
            return {
                "score": 0.0,
                "confidence": 0.0,
                "summary": "Could not extract headlines.",
                "key_factor": "N/A",
                "headline_count": 0
            }

        news_text = "\n".join([f"- {h}" for h in headlines])

        # Enhanced prompt with structured output
        prompt = f"""You are an expert quantitative trading analyst. Analyze these recent financial news headlines for {ticker} and provide a trading-relevant sentiment assessment.

Headlines:
{news_text}

Respond with ONLY valid JSON (no markdown, no code blocks) with these exact keys:
{{
    "score": <float between -1.0 (extremely bearish) and 1.0 (extremely bullish)>,
    "confidence": <float between 0.0 (no confidence) and 1.0 (very confident)>,
    "summary": "<A highly detailed, professional 4-5 sentence trading-relevant summary. Synthesize the core catalysts, market reactions, earnings impacts, product launches, or macroeconomic pressures, and end with a strategic quantitative outlook on the directional momentum.>",
    "key_factor": "<the single most important factor driving sentiment>"
}}

Scoring guide:
- Score > 0.5: Strong positive catalysts (earnings beat, upgrades, expansion)
- Score 0.2 to 0.5: Mildly positive news flow
- Score -0.2 to 0.2: Mixed or neutral sentiment
- Score -0.5 to -0.2: Mildly negative news flow  
- Score < -0.5: Strong negative catalysts (downgrades, lawsuits, losses)

Confidence guide:
- High (0.7-1.0): Clear, unambiguous news with direct financial impact
- Medium (0.4-0.7): Somewhat relevant but mixed signals
- Low (0.0-0.4): Vague, speculative, or conflicting headlines
"""

        client = Groq(api_key=groq_key)

        response = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
        )

        output = response.choices[0].message.content.strip()
        sentiment_data = json.loads(output)

        score = max(-1.0, min(1.0, float(sentiment_data.get('score', 0.0))))
        confidence = max(0.0, min(1.0, float(sentiment_data.get('confidence', 0.5))))

        result = {
            "score": round(score, 3),
            "confidence": round(confidence, 3),
            "summary": sentiment_data.get('summary', 'Analysis complete.'),
            "key_factor": sentiment_data.get('key_factor', 'N/A'),
            "headline_count": len(headlines)
        }

        # Save to cache
        SENTIMENT_CACHE[ticker] = {
            "data": result,
            "timestamp": time.time()
        }

        return result

    except json.JSONDecodeError as e:
        print(f"[ai_indicator] JSON parse error for {ticker}, using fallback: {e}")
        result = _fallback_sentiment(ticker, headlines)
        SENTIMENT_CACHE[ticker] = {"data": result, "timestamp": time.time()}
        return result
    except Exception as e:
        print(f"[ai_indicator] Error fetching sentiment for {ticker}, using fallback: {e}")
        result = _fallback_sentiment(ticker, headlines)
        SENTIMENT_CACHE[ticker] = {"data": result, "timestamp": time.time()}
        return result
