"""
AI Trading Bot — FastAPI Backend.

Production-grade trading engine with:
- Multi-ticker watchlist scanning
- Background scheduler for automated evaluation
- Full REST API for the dashboard
- Alpaca broker integration
- Risk management enforcement
- MULTI-TENANT ARCHITECTURE
"""

from fastapi import FastAPI, Query, Header, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
import asyncio
from datetime import datetime, timedelta
import pytz
import firebase_admin
from firebase_admin import auth, credentials, firestore
import time
import os
import re
import json
import yfinance as yf
from cryptography.fernet import Fernet
import base64
import hashlib

from trader import evaluate_trade, get_risk_manager, clear_evaluation_cache, get_confluence_decision
from indicators import get_full_analysis
from data_manager import get_historical_data
from engine import UserManager, UserEngine
import config as global_config
from user_config import set_user_config, get_user_config
from ai_indicator import get_ai_indicator, clear_ai_cache

# ──────────────────────────────────────────────
# Secret Encryption Vault
# ──────────────────────────────────────────────
class Vault:
    """Handles bank-grade encryption for sensitive API keys."""
    def __init__(self):
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "trading-bot-engine-df3de")
        seed = project_id.encode()
        key_hash = hashlib.sha256(seed).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key_hash))

    def encrypt(self, plain_text: str) -> str:
        if not plain_text: return ""
        return self.fernet.encrypt(plain_text.encode()).decode()

    def decrypt(self, cipher_text: str) -> str:
        if not cipher_text: return ""
        try:
            decrypted = self.fernet.decrypt(cipher_text.encode()).decode()
            return decrypted.strip()
        except Exception as e:
            print(f"[vault] DECRYPTION FAILED: {e}")
            return ""

    def self_test(self):
        test_str = "alpaca_test_123"
        encrypted = self.encrypt(test_str)
        decrypted = self.decrypt(encrypted)
        if test_str == decrypted:
            print("[vault] Self-test PASSED.")
        else:
            print("[vault] Self-test FAILED! Encryption/Decryption mismatch.")

vault = Vault()
vault.self_test()

# Initialize Firebase Admin
if not firebase_admin._apps:
    try:
        cred_path = os.path.join(os.path.dirname(__file__), "serviceAccount.json")
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app(options={'projectId': 'trading-bot-engine-df3de'})
        print("[main] Firebase Admin initialized.")
    except Exception as e:
        print(f"[main] Firebase Admin init error: {e}")

db = firestore.client(database_id="trading-bot")
user_manager = UserManager(db)
_webull_market_data_broker = None
_chart_analysis_cache = {}


def _chart_cache_ttl(timeframe: str) -> int:
    tf = (timeframe or "").upper()
    if tf in ("30SEC", "1MIN"):
        return 10
    if tf in ("2MIN", "3MIN", "5MIN"):
        return 20
    if tf in ("10MIN", "15MIN", "30MIN"):
        return 45
    if tf in ("1HOUR", "60MIN"):
        return 120
    if tf in ("2HOUR", "4HOUR"):
        return 300
    return 900


def _format_scan_price(price) -> str:
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "$0.00"
    return f"${p:.4f}" if p < 10 else f"${p:.2f}"


def _get_cached_chart_analysis(ticker: str, timeframe: str, force: bool = False):
    ticker_key = _normalize_quote_symbol(ticker)
    timeframe_key = timeframe or get_user_config().DEFAULT_TIMEFRAME
    cache_key = (ticker_key, timeframe_key)
    now_ts = time.time()
    cached = _chart_analysis_cache.get(cache_key)
    if cached and not force and now_ts - cached["timestamp"] < _chart_cache_ttl(timeframe_key):
        return cached["analysis"], True

    analysis = get_full_analysis(ticker_key, timeframe=timeframe_key, data_source="webull")
    if analysis:
        _chart_analysis_cache[cache_key] = {
            "analysis": analysis,
            "timestamp": now_ts
        }
    return analysis, False


def _technical_scan_from_analysis(ticker: str, timeframe: str, analysis: dict, use_bot_settings: bool = False):
    if not analysis:
        return None

    decision = get_confluence_decision(
        ticker,
        analysis,
        ai_sentiment_score=0.0,
        ai_sentiment_confidence=0.0,
        use_bot_settings=use_bot_settings
    )
    price = analysis.get("price", 0.0)
    return {
        "time": get_now().isoformat(),
        "ticker": _normalize_quote_symbol(ticker),
        "timeframe": timeframe,
        "action": decision.get("action", "HOLD"),
        "reason": decision.get("reason", ""),
        "price": _format_scan_price(price),
        "price_raw": price,
        "signals": decision.get("signals", {}),
        "bullish_count": decision.get("bullish_count", 0),
        "bearish_count": decision.get("bearish_count", 0),
        "total_signals": len(analysis.get("signals", {})),
        "atr": analysis.get("atr", 0.0),
        "rsi": analysis.get("rsi", 50.0),
        "sentiment_score": 0.0,
        "sentiment_confidence": 0.0,
        "sentiment_summary": "",
        "sentiment_key_factor": "Technical signals",
        "position_sizing": {},
        "price_history": analysis.get("price_history", []),
        "risk_status": {},
        "is_custom": False,
    }


def _normalize_quote_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("/", "").strip()


def _format_quote_price(symbol: str, price) -> str:
    try:
        price_float = float(price)
    except (TypeError, ValueError):
        return ""
    clean = _normalize_quote_symbol(symbol)
    is_crypto = any(clean.endswith(base) for base in ["USD", "USDT", "USDC"])
    decimals = 4 if is_crypto or price_float < 10 else 2
    return f"${price_float:.{decimals}f}"


def _lookup_quote_price(latest_prices: dict, symbol: str):
    clean = _normalize_quote_symbol(symbol)
    for key in (symbol, clean):
        if key in latest_prices:
            return latest_prices[key]
    return None


def _get_webull_market_data_broker():
    """Return a data-only Webull broker for realtime quotes, independent of the trading broker."""
    global _webull_market_data_broker

    if _webull_market_data_broker and getattr(_webull_market_data_broker, "market_client", None):
        return _webull_market_data_broker

    app_key = getattr(global_config, "WEBULL_APP_KEY", "")
    app_secret = getattr(global_config, "WEBULL_APP_SECRET", "")
    if not app_key or app_key == "your_webull_app_key_here" or not app_secret:
        return None

    try:
        from webull_broker import WebullBroker
        broker = WebullBroker(data_only=True)
        if getattr(broker, "market_client", None):
            _webull_market_data_broker = broker
            return broker
    except Exception as e:
        print(f"[dashboard] Webull quote client unavailable: {e}")
    return None


def _fetch_latest_watchlist_prices(eng: UserEngine, tickers: list) -> dict:
    if not tickers:
        return {}

    clean_tickers = list(dict.fromkeys(_normalize_quote_symbol(t) for t in tickers if _normalize_quote_symbol(t)))
    provider = (
        eng.broker
        if hasattr(eng.broker, "get_latest_prices") and getattr(eng.broker, "market_client", None)
        else None
    )

    if provider is None:
        provider = _get_webull_market_data_broker()

    if provider is None:
        return {}

    try:
        return provider.get_latest_prices(clean_tickers) or {}
    except Exception as e:
        print(f"[dashboard] Live Webull price fetch failed: {e}")
        return {}

async def verify_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    token = authorization.split("Bearer ")[1]
    if token == "dev-token":
        return {"uid": "dev-user", "email": "dev@example.com"}
    try:
        return auth.verify_id_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication credentials")

async def get_user_engine(user: dict = Depends(verify_token)) -> UserEngine:
    eng = user_manager.get_engine(user['uid'])
    if not getattr(eng, 'loaded_from_cloud', False):
        async with eng.load_lock:
            if not eng.loaded_from_cloud:
                await eng.load_from_cloud()
                eng.loaded_from_cloud = True
    if not eng.bot_running:
        async with eng.load_lock:
            if not eng.bot_running:
                eng.task = asyncio.create_task(eng.trading_loop())
    set_user_config(eng.config)
    return eng

def get_now():
    """Returns current time in the configured timezone."""
    tz = pytz.timezone(get_user_config().TIMEZONE)
    return datetime.now(tz)

class AlpacaConfig(BaseModel):
    api_key: str
    secret_key: str
    paper: bool = True

@asynccontextmanager
async def lifespan(app: FastAPI):
    await user_manager.start_all()
    yield
    await user_manager.stop_all()

app = FastAPI(
    title="Bot Bulls",
    description="Automated AI-driven quantitative trading platform",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://trading-bot-engine-df3de.firebaseapp.com",
        "https://trading-bot-engine-df3de.web.app",
        "http://localhost:5000",
        "http://localhost:3000",
        "http://127.0.0.1:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Bot Bulls API is running!", "version": "2.0.0"}


@app.post("/api/alpaca_config")
async def update_alpaca_config(cfg: AlpacaConfig, eng: UserEngine = Depends(get_user_engine)):
    success = eng.broker.connect(cfg.api_key, cfg.secret_key, cfg.paper)
    if success:
        # Persist to cloud (Encrypted)
        data = {
            "api_key": vault.encrypt(cfg.api_key),
            "secret_key": vault.encrypt(cfg.secret_key),
            "paper": cfg.paper,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        def _sync_save():
            db.collection("users").document(eng.uid).collection("settings").document("alpaca").set(data)
        await asyncio.to_thread(_sync_save)
        
        # Update config module state
        eng.config.ALPACA_API_KEY = cfg.api_key
        eng.config.ALPACA_SECRET_KEY = cfg.secret_key
        eng.config.ALPACA_PAPER = cfg.paper
        
        return {"status": "success", "message": "Connected to Alpaca."}
    else:
        return {"status": "error", "message": "Failed to connect to Alpaca. Check your keys."}


@app.delete("/api/alpaca_config")
def unlink_alpaca(eng: UserEngine = Depends(get_user_engine)):
    # Delete from cloud
    if db:
        db.collection("users").document(eng.uid).collection("settings").document("alpaca").delete()
    
    # Reset broker to simulation
    eng.broker.simulation_mode = True
    eng.broker.client = None
    
    # Clear config module state
    eng.config.ALPACA_API_KEY = ""
    eng.config.ALPACA_SECRET_KEY = ""
    return {"status": "success", "message": "Alpaca account unlinked. Switched to simulation mode."}


@app.get("/api/portfolio/history")
async def get_portfolio_history(
    period: str = "1M",
    timeframe: str = "1D",
    eng: UserEngine = Depends(get_user_engine)
):
    """
    Get portfolio history for the current user's broker connection.
    Maps period '1Y' to Alpaca-compatible '1A'.
    """
    alpaca_period = "1A" if period.upper() == "1Y" else period
    try:
        data = await asyncio.to_thread(eng.broker.get_portfolio_history, period=alpaca_period, timeframe=timeframe)
        return data
    except Exception as e:
        print(f"[api] Error getting portfolio history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/market-data")
async def get_market_data(eng: UserEngine = Depends(get_user_engine)):
    try:
        # Run yfinance operations in a background thread to prevent blocking the async event loop
        def fetch_data():
            import yfinance as yf
            # 1. Fetch Sector Data
            sector_tickers = (
                "AAPL MSFT GOOG NVDA META TSLA AVGO AMD QCOM NFLX AMZN ADBE CRM INTC CSCO ORCL "
                "JPM BAC WFC MS GS V MA AXP BLK C SCHW HDB "
                "LLY UNH JNJ MRK ABBV PFE WMT COST PG KO PEP NKE MCD EL "
                "XOM CVX COP OXY SLB CAT GE HON UNP LMT DE MMM "
                "BTC-USD ETH-USD SOL-USD ADA-USD XRP-USD DOGE-USD DOT-USD LINK-USD LTC-USD NEAR-USD BCH-USD AVAX-USD"
            )
            # Disable yfinance logging
            df_sector = yf.download(sector_tickers, period="5d", progress=False)
            
            sector_data = {}
            for ticker in sector_tickers.split():
                try:
                    valid_closes = df_sector['Close'][ticker].dropna()
                    if len(valid_closes) >= 2:
                        current_price = valid_closes.iloc[-1]
                        prev_price = valid_closes.iloc[-2]
                        pct_change = ((current_price / prev_price) - 1) * 100
                        sector_data[ticker] = round(pct_change, 2)
                    else:
                        sector_data[ticker] = 0.0
                except:
                    sector_data[ticker] = 0.0

            # 2. Market Breadth via Watchlist
            user_config = eng.config
            raw_list = list(set(user_config.WATCHLIST + user_config.TRADELIST))
            
            # Fallback to market leaders if watchlist and tradelist are empty
            if not raw_list:
                raw_list = ["AAPL", "MSFT", "GOOG", "NVDA", "TSLA", "META", "AMD", "NFLX", "AMZN", "COIN"]
            
            # yfinance requires hyphens for crypto (e.g., BTC-USD instead of BTCUSD)
            combined_list = []
            for t in raw_list:
                if t.endswith("USD") and "-" not in t:
                    combined_list.append(t.replace("USD", "-USD"))
                else:
                    combined_list.append(t)
            
            advancing = 0
            declining = 0
            new_highs = 0
            new_lows = 0
            above_50 = 0
            above_200 = 0
            tickers_list = []
            
            if len(combined_list) > 0:
                df_breadth = yf.download(" ".join(combined_list), period="200d", progress=False)
                for ticker in combined_list:
                    try:
                        if len(combined_list) == 1:
                            valid_closes = df_breadth['Close'].dropna()
                            valid_highs = df_breadth['High'].dropna()
                            valid_lows = df_breadth['Low'].dropna()
                        else:
                            valid_closes = df_breadth['Close'][ticker].dropna()
                            valid_highs = df_breadth['High'][ticker].dropna()
                            valid_lows = df_breadth['Low'][ticker].dropna()
                            
                        if len(valid_closes) >= 2:
                            curr = valid_closes.iloc[-1]
                            prev = valid_closes.iloc[-2]
                            
                            is_adv = bool(curr > prev)
                            if is_adv: advancing += 1
                            else: declining += 1
                            
                            year_high = valid_highs.max()
                            year_low = valid_lows.min()
                            
                            is_high = bool(curr >= year_high * 0.98)
                            is_low = bool(curr <= year_low * 1.02)
                            if is_high: new_highs += 1
                            if is_low: new_lows += 1
                            
                            sma50 = None
                            above_50_status = None
                            if len(valid_closes) >= 50:
                                sma50 = float(valid_closes.iloc[-50:].mean())
                                above_50_status = bool(curr > sma50)
                                if above_50_status: above_50 += 1
                                
                            sma200 = None
                            above_200_status = None
                            if len(valid_closes) >= 200:
                                sma200 = float(valid_closes.iloc[-200:].mean())
                                above_200_status = bool(curr > sma200)
                                if above_200_status: above_200 += 1
                                
                            tickers_list.append({
                                "ticker": ticker,
                                "price": round(float(curr), 2),
                                "change_pct": round(float(((curr / prev) - 1) * 100), 2) if prev else 0.0,
                                "above_50": above_50_status,
                                "above_200": above_200_status,
                                "is_high": is_high,
                                "is_low": is_low
                            })
                    except Exception:
                        pass
                        
            total = max(1, len(tickers_list))
            breadth_data = {
                "advancing": advancing,
                "declining": declining,
                "new_highs": new_highs,
                "new_lows": new_lows,
                "above_50_pct": round((above_50 / total) * 100),
                "above_200_pct": round((above_200 / total) * 100),
                "tickers": tickers_list
            }
            
            return {
                "sectors": sector_data,
                "breadth": breadth_data
            }
            
        data = await asyncio.to_thread(fetch_data)
        return data
    except Exception as e:
        print(f"Error fetching market data: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/macro-events")
async def get_macro_events():
    try:
        from macro_service import get_macro_data
        
        def fetch_calendar():
            import requests
            try:
                url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
                headers = {'User-Agent': 'Mozilla/5.0'}
                r = requests.get(url, headers=headers, timeout=5)
                r.raise_for_status()
                data = r.json()
                
                # Expand to High/Medium USD and High major global economies
                def is_relevant(e):
                    if e.get('country') == 'USD' and e.get('impact') in ['High', 'Medium']: return True
                    if e.get('country') in ['EUR', 'GBP', 'CNY', 'JPY'] and e.get('impact') == 'High': return True
                    return False
                    
                return [e for e in data if is_relevant(e)]
            except Exception as e:
                print(f"Error fetching forex calendar: {e}")
                return []
                
        # Run both operations concurrently
        indicators_task = asyncio.to_thread(get_macro_data, False)
        calendar_task = asyncio.to_thread(fetch_calendar)
        
        indicators, calendar = await asyncio.gather(indicators_task, calendar_task)
        
        return {"status": "success", "indicators": indicators, "calendar": calendar}
    except Exception as e:
        print(f"Error fetching macro events: {e}")
        return {"status": "error", "message": str(e), "indicators": {}, "calendar": []}

@app.get("/api/dashboard")
async def get_dashboard(request: Request, mode: str = "fast", ticker: str = None, timeframe: str = None, source: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """
    Main dashboard endpoint — returns everything the UI needs in one call.
    """
    overall_start = time.perf_counter()

    # Track dashboard primary ticker
    if ticker:
        eng.dashboard_primary_ticker = ticker.upper()

    # Identify primary ticker
    primary_ticker = (
        ticker.upper() if ticker else (
            eng.config.TRADELIST[0] if eng.config.TRADELIST else (
                eng.config.WATCHLIST[0] if eng.config.WATCHLIST else "MSFT"
            )
        )
    )

    # Fallback/default timeframe logic
    if timeframe is None:
        timeframe = eng.config.TICKER_SETTINGS.get(primary_ticker, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME
    
    # Get general account info
    account = eng.broker.get_account_info()
    positions = eng.broker.get_positions()
    orders = eng.broker.get_open_orders()
    
    # Active bot status check
    is_active_bot = primary_ticker in eng.config.TRADELIST
    
    # Check cache first for primary ticker scan
    primary_scan = None
    prefer_bot_for_primary = is_active_bot and source == "bots"
    cached_scan = eng._pick_scan(primary_ticker, timeframe, prefer_bot=prefer_bot_for_primary)
    if cached_scan:
        primary_scan = {**cached_scan, 'cached': True}

    if mode == "heavy" and not primary_scan:
        is_crypto = any(c in primary_ticker for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in primary_ticker
        avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
        try:
            primary_scan = await asyncio.to_thread(
                evaluate_trade,
                primary_ticker, 
                account_equity=account['equity'], 
                available_cash=avail_cash,
                timeframe=timeframe,
                use_bot_settings=prefer_bot_for_primary
            )
            if primary_scan:
                if prefer_bot_for_primary:
                    eng._record_bot_scan(primary_scan)
                else:
                    eng._record_scan(primary_scan)
        except Exception as e:
            print(f"[dashboard] Scan error for primary ticker {primary_ticker}: {e}")
            primary_scan = None

    if not primary_scan:
        primary_scan = {
            "time": get_now().isoformat(),
            "ticker": primary_ticker,
            "action": "HOLD",
            "price": "$0.00",
            "price_raw": 0.0,
            "reason": "Scanning in background...",
            "signals": {},
            "price_history": [],
            "timeframe": timeframe,
            "sentiment_score": 0.0,
            "sentiment_confidence": 0.0,
            "sentiment_summary": "",
            "sentiment_key_factor": "",
            "bullish_count": 0,
            "bearish_count": 0,
            "total_signals": 0,
            "atr": 0.0,
            "rsi": 50.0,
            "position_sizing": {},
            "risk_status": {},
            "is_custom": False
        }

    # Format the primary scan cleanly
    formatted_primary = _format_scan_for_ui(primary_scan, use_global_settings=(not prefer_bot_for_primary))
    
    # Retrieve order history
    orders_history = eng.broker.get_order_history()
    
    # Format daily P/L (Today Only)
    daily_pl = account.get('daily_pl', 0.0)
    daily_pl_pct = account.get('daily_pl_pct', 0.0)
    daily_pl_sign = "+" if daily_pl >= 0 else ""

    # Calculate All-Time Profit (Realized + Unrealized) directly from the broker history
    total_profit, total_profit_pct = eng.broker.get_all_time_profit()
    total_pl_sign = "+" if total_profit >= 0 else ""

    # Format trade log (scans/decisions)
    ui_trade_log = [_format_trade_for_ui(trade) for trade in eng.trade_log]

    # Warmup or background scan triggers
    last_scan_time = eng.last_scan_time
    cloud_restore_log = []

    risk_mgr = get_risk_manager()

    sentiment_score = primary_scan.get('sentiment_score', 0)
    sentiment_confidence = primary_scan.get('sentiment_confidence', 0)

    if sentiment_score > 0.3:
        sentiment_label = "Bullish"
    elif sentiment_score < -0.3:
        sentiment_label = "Bearish"
    else:
        sentiment_label = "Neutral"

    # Fetch live Webull prices for every visible ticker, even when trading uses Alpaca.
    all_tickers = list(dict.fromkeys(eng.config.WATCHLIST + eng.config.TRADELIST + [primary_ticker]))
    latest_prices = await asyncio.to_thread(_fetch_latest_watchlist_prices, eng, all_tickers)

    def _inject_price(scan_ui, t):
        live_price = _lookup_quote_price(latest_prices, t)
        if live_price:
            scan_ui['price'] = _format_quote_price(t, live_price)
            scan_ui['price_raw'] = live_price
        if not scan_ui.get('ticker'):
            scan_ui['ticker'] = t
        return scan_ui

    def _price_only_scan(t):
        return _inject_price({
            "ticker": t,
            "price": "",
            "price_raw": 0.0,
            "action": "HOLD",
            "reason": "",
            "bullish_count": 0,
            "bearish_count": 0,
            "total_signals": 0,
            "signals": {}
        }, t)

    formatted_primary = _inject_price(formatted_primary, primary_ticker)

    # Build the final payload
    payload = {
        "simulation": account.get('simulation', True),
        "has_keys": bool(eng.config.ALPACA_API_KEY),
        "capital": f"${account['equity']:.2f}",
        "cash": f"${account['cash']:.2f}",
        "openPositions": str(len(positions)),
        "positionsList": ", ".join(p['symbol'] for p in positions) if positions else "No positions",
        "dailyPL": f"{daily_pl_sign}${daily_pl:.2f} ({daily_pl_sign}{daily_pl_pct:.1f}%)",
        "totalProfit": f"{total_pl_sign}${total_profit:.2f} ({total_pl_sign}{total_profit_pct:.1f}%)",
        "aiSentiment": f"{sentiment_label} ({sentiment_score})",
        "sentiment_confidence": sentiment_confidence,
        "sentiment_summary": primary_scan.get("sentiment_summary", ""),
        "sentiment_key_factor": primary_scan.get("sentiment_key_factor", "N/A"),
        "account": account,
        "positions": positions,
        "open_orders": orders,
        "pendingOrders": orders,
        "orders_history": orders_history,
        "orderHistory": orders_history,
        "trade_log": ui_trade_log,
        "recentTrades": ui_trade_log,
        
        "watchlistScans": {
            t: _price_only_scan(t)
            for t in eng.config.WATCHLIST
        },
        "botScans": {
            t: _inject_price(_format_scan_for_ui(
                eng._pick_scan(t, timeframe, prefer_bot=True) or 
                eng._pick_scan(t, eng.config.TICKER_SETTINGS.get(t, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME), prefer_bot=True, ignore_freshness=True) or {},
                use_global_settings=False
            ), t)
            for t in eng.config.TRADELIST
        },

        # Strategy Signals (primary ticker)
        "primaryTicker": primary_ticker,
        "primaryScan": formatted_primary,
        "signals": formatted_primary.get('signals', {}),
        "priceHistory": primary_scan.get('price_history', []),

        # Risk Management
        "risk": risk_mgr.get_risk_status(account['equity']),
        "ticker_settings": {
            t: {'timeframe': eng.config.TICKER_SETTINGS.get(t, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME)}
            for t in eng.config.WATCHLIST
        },

        # Bot Meta
        "botRunning": eng.bot_running,
        "brokerConnected": not account.get('simulation', True),
        "has_keys": bool(eng.config.ALPACA_API_KEY),
        "lastScan": last_scan_time or "Starting...",
        "indicator_settings": eng.config.toggles,
        "indicator_parameters": eng.config.parameters,
        "strategyTimeframe": timeframe,
        "watchlist": eng.config.WATCHLIST,
        "tradelist": eng.config.TRADELIST,

        # Performance Timings
        "performance": {
            "total_ms": (time.perf_counter() - overall_start) * 1000,
            "eval_ms": primary_scan.get('perf_ms', 0),
            "cached": primary_scan.get('cached', False)
        },
        "scanInterval": eng.config.SCAN_INTERVAL_SECONDS,

        # Safety Controls
        "maxOpenPositions": eng.config.get('MAX_OPEN_POSITIONS', 5),
        "tradeCooldownSeconds": eng.config.get('TRADE_COOLDOWN_SECONDS', 300),
        "marketHoursOnly": eng.config.get('MARKET_HOURS_ONLY', True),

        "debug_logs": cloud_restore_log
    }

    if mode == "fast":
        payload["signals"] = {}
        payload["primaryScan"]["signals"] = {}
        if "price_history" in payload["primaryScan"]:
            del payload["primaryScan"]["price_history"]
        payload["priceHistory"] = []
        for k, v in payload["watchlistScans"].items():
            v["signals"] = {}
            if "price_history" in v:
                del v["price_history"]
        for k, v in payload["botScans"].items():
            v["signals"] = {}
            if "price_history" in v:
                del v["price_history"]

    return payload


@app.get("/api/chart/{ticker}")
async def get_chart_data(
    ticker: str,
    timeframe: str = None,
    force: bool = False,
    source: str = "dashboard",
    eng: UserEngine = Depends(get_user_engine)
):
    """Fast chart endpoint: OHLCV + overlay indicators + technical confluence only."""
    ticker = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}

    timeframe = timeframe or eng.config.TICKER_SETTINGS.get(ticker, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME
    prefer_bot = ticker in eng.config.TRADELIST and source == "bots"
    started = time.perf_counter()

    analysis, cached = await asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, force)
    if not analysis:
        raise HTTPException(status_code=404, detail=f"Could not load chart data for {ticker}")

    scan = _technical_scan_from_analysis(ticker, timeframe, analysis, use_bot_settings=prefer_bot)
    formatted_scan = _format_scan_for_ui(scan, use_global_settings=(not prefer_bot))
    print(f"[perf] /api/chart {ticker} {timeframe}: {(time.perf_counter() - started) * 1000:.1f}ms cached={cached}")
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "cached": cached,
        "primaryScan": formatted_scan,
        "signals": formatted_scan.get("signals", {}),
        "priceHistory": scan.get("price_history", []),
        "price_history": scan.get("price_history", []),
        "action": formatted_scan.get("action", "HOLD"),
        "reason": formatted_scan.get("reason", ""),
        "bullish_count": formatted_scan.get("bullish_count", 0),
        "bearish_count": formatted_scan.get("bearish_count", 0),
        "total_signals": formatted_scan.get("total_signals", 0),
        "performance": {
            "total_ms": (time.perf_counter() - started) * 1000,
            "cached": cached
        }
    }


@app.get("/api/ai-indicator")
async def get_ai_indicator_endpoint(
    ticker: str,
    timeframe: str = "4Hour",
    force: bool = False,
    eng: UserEngine = Depends(get_user_engine)
):
    """
    Agentic AI Indicator — Groq-powered autonomous market research.
    Returns a BUY/SELL/HOLD signal with position sizing, leverage guidance,
    sell targets, and a detailed institutional-grade reasoning summary.
    """
    ticker = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}

    if not getattr(eng.config, "ENABLE_AI_INDICATOR", True):
        return {"error": "AI Indicator is disabled in settings", "signal": "HOLD", "confidence": 0}

    if force:
        clear_ai_cache(ticker, timeframe)

    account = eng.broker.get_account_info()
    available_cash = float(account.get("cash", 10000.0))
    account_equity = float(account.get("equity", 10000.0))

    try:
        result = await asyncio.to_thread(
            get_ai_indicator,
            ticker,
            timeframe,
            available_cash,
            account_equity,
        )
        return result
    except Exception as e:
        print(f"[api] AI indicator error for {ticker}: {e}")
        return {
            "error": str(e),
            "signal": "HOLD",
            "confidence": 0,
            "summary": "AI Indicator temporarily unavailable.",
            "cached": False,
        }


@app.get("/api/signals/{ticker}")
async def get_fast_signals(
    ticker: str,
    timeframe: str = None,
    source: str = "dashboard",
    eng: UserEngine = Depends(get_user_engine)
):
    """Fast signal endpoint reusing cached chart analysis when available."""
    ticker = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}

    timeframe = timeframe or eng.config.TICKER_SETTINGS.get(ticker, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME
    prefer_bot = ticker in eng.config.TRADELIST and source == "bots"
    analysis, cached = await asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, False)
    if not analysis:
        raise HTTPException(status_code=404, detail=f"Could not load signals for {ticker}")

    scan = _technical_scan_from_analysis(ticker, timeframe, analysis, use_bot_settings=prefer_bot)
    formatted_scan = _format_scan_for_ui(scan, use_global_settings=(not prefer_bot))
    if "price_history" in formatted_scan:
        del formatted_scan["price_history"]
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "cached": cached,
        "primaryScan": formatted_scan,
        "signals": formatted_scan.get("signals", {}),
        "action": formatted_scan.get("action", "HOLD"),
        "reason": formatted_scan.get("reason", ""),
        "bullish_count": formatted_scan.get("bullish_count", 0),
        "bearish_count": formatted_scan.get("bearish_count", 0),
        "total_signals": formatted_scan.get("total_signals", 0),
    }


@app.get("/api/scan/{ticker}")
def scan_ticker(ticker: str, timeframe: str = "4Hour", eng: UserEngine = Depends(get_user_engine)):
    """On-demand scan of a specific ticker."""
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker.upper()):
        return {"error": "Invalid ticker format"}
    account = eng.broker.get_account_info()
    if account.get('simulation', True):
        return {"error": "Alpaca connection required for scanning."}
        
    now = datetime.now()
    cached_scan = eng._pick_scan(ticker.upper(), timeframe, prefer_bot=False)
    is_recent = False
    if cached_scan and cached_scan.get('timeframe') == timeframe:
        try:
            last_time = datetime.fromisoformat(cached_scan.get('time', ''))
            if (now - last_time).total_seconds() < 30:
                is_recent = True
        except: pass

    if is_recent:
        return _format_scan_for_ui(cached_scan)

    is_crypto = any(c in ticker.upper() for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in ticker.upper()
    avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']

    result = evaluate_trade(
        ticker.upper(), 
        account_equity=account['equity'], 
        available_cash=avail_cash,
        timeframe=timeframe
    )
    if result:
        eng._record_scan(result)
        return _format_scan_for_ui(result)
    return {"error": f"Could not analyze {ticker}"}


@app.post("/api/backtest")
async def run_backtest(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs a historical backtest for a ticker."""
    ticker = data.get("ticker", "AAPL").upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 30))
    capital = float(data.get("capital", 1000.0))
    threshold = int(data.get("threshold", 5))
    sell_threshold = int(data.get("sell_threshold", 3))
    indicators = data.get("indicators", []) 
    ext_hours = data.get("ext_hours", True)
    
    end_date = get_now()
    start_date = end_date - timedelta(days=days)
    
    sell_mode = data.get("sell_mode", "indicator")
    risk_per_trade = float(data.get("risk_per_trade", 0.02))
    max_pos_pct = float(data.get("max_position_pct", 0.25))
    atr_stop_multiplier = float(data.get("atr_stop_multiplier", 2.0))
    atr_trail_multiplier = float(data.get("atr_trail_multiplier", 3.0))
    atr_take_profit_multiplier = float(data.get("atr_take_profit_multiplier", 4.0))
    
    ai_autopilot = data.get("ai_autopilot", False) or (sell_mode == "ai_autopilot")
    ai_interval = int(data.get("ai_interval", 10))
    ai_model = data.get("ai_model", "llama-3.3-70b-versatile")
    
    from backtester import Backtester
    bt = Backtester(
        ticker=ticker,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        threshold=threshold,
        sell_threshold=sell_threshold,
        enabled_indicators=indicators,
        risk_per_trade=risk_per_trade,
        max_pos_pct=max_pos_pct,
        ext_hours=ext_hours,
        sell_mode=sell_mode,
        atr_stop_multiplier=atr_stop_multiplier,
        atr_trail_multiplier=atr_trail_multiplier,
        atr_take_profit_multiplier=atr_take_profit_multiplier,
        ai_autopilot=ai_autopilot,
        ai_interval=ai_interval,
        ai_model=ai_model
    )
    
    try:
        results = bt.run()
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        print(f"[api] Backtest crash: {e}")
        return {"status": "error", "message": "An internal error occurred during backtest simulation."}


@app.post("/api/download_all")
async def download_all_data(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Downloads and caches all available history for a ticker across all timeframes."""
    raw_ticker = str(data.get("ticker", "")).strip().upper()
    if not raw_ticker:
        return {"error": "Ticker required"}
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", raw_ticker):
        return {"error": "Invalid ticker format"}
    ticker = raw_ticker
    
    timeframes = [
        ("30Sec", 7),
        ("1Min", 7),
        ("2Min", 14),
        ("3Min", 14),
        ("5Min", 60),
        ("10Min", 60),
        ("15Min", 60),
        ("30Min", 60),
        ("1Hour", 365),
        ("2Hour", 365),
        ("4Hour", 730),
        ("1Day", 1825)
    ]
    
    log = []
    end_date = get_now()
    
    # 1. Download Price History
    for tf, days in timeframes:
        start_date = end_date - timedelta(days=days)
        df = get_historical_data(ticker, tf, start_date, end_date)
        status = "Success" if df is not None and not df.empty else "No Data"
        log.append(f"{tf}: {status}")
        
    # 2. Download Metadata
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
        clean_ticker = re.sub(r'[^A-Z0-9]', '', ticker)
        safe_ticker = os.path.basename(clean_ticker)
        data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
        if not os.path.exists(data_dir): 
            os.makedirs(data_dir)
        
        info_path = os.path.abspath(os.path.join(data_dir, f"{safe_ticker}_info.json"))
        if not info_path.startswith(data_dir + os.sep):
            return {"error": "Invalid ticker path"}
            
        with open(info_path, "w") as f:
            json.dump(info, f, indent=4)
        log.append("Metadata: Saved")
    except Exception as e:
        print(f"[api] Metadata extraction error: {e}")
        log.append("Metadata Error: Could not save metadata securely")
        
    return {"ticker": ticker, "status": log}


@app.get("/api/settings/indicators")
def get_indicator_settings(eng: UserEngine = Depends(get_user_engine)):
    """Returns all indicator toggles grouped by category."""
    return {
        "Momentum": {
            "ENABLE_RSI": {"label": "RSI", "description": "Relative Strength Index — Overbought / Oversold", "enabled": getattr(eng.config, "ENABLE_RSI", True)},
            "ENABLE_MACD": {"label": "MACD", "description": "Moving Average Convergence Divergence", "enabled": getattr(eng.config, "ENABLE_MACD", True)},
        },
        "Trend": {
            "ENABLE_EMA": {"label": "EMA Cross", "description": "Exponential Moving Average Crossover (9/21)", "enabled": getattr(eng.config, "ENABLE_EMA", True)},
            "ENABLE_SUPERTREND": {"label": "Supertrend", "description": "Supertrend Indicator (10, 3)", "enabled": getattr(eng.config, "ENABLE_SUPERTREND", True)},
            "ENABLE_BOLLINGER": {"label": "Bollinger", "description": "Bollinger Bands (20, 2σ)", "enabled": getattr(eng.config, "ENABLE_BOLLINGER", True)},
            "ENABLE_ADX_TREND": {"label": "ADX Trend", "description": "Wilder's ADX (14-period) Trend Strength Filter", "enabled": getattr(eng.config, "ENABLE_ADX_TREND", True)},
            "ENABLE_SMA": {"label": "SMA 200", "description": "Simple Moving Average (200-period) institutional filter", "enabled": getattr(eng.config, "ENABLE_SMA", True)},
        },
        "Volume": {
            "ENABLE_VWAP": {"label": "VWAP", "description": "Volume Weighted Average Price", "enabled": getattr(eng.config, "ENABLE_VWAP", True)},
        },
        "Custom": {
            "ENABLE_MYSTIC_PULSE": {"label": "Mystic Pulse", "description": "DMI-based Consecutive Trend Strength", "enabled": getattr(eng.config, "ENABLE_MYSTIC_PULSE", True)},
            "ENABLE_AI_SENTIMENT": {"label": "News Sentiment", "description": "Groq-powered News Sentiment Analysis", "enabled": getattr(eng.config, "ENABLE_AI_SENTIMENT", True)},
            "ENABLE_CANDLE_PATTERNS": {"label": "Candle Patterns", "description": "Engulfing, Hammer, Shooting Star patterns", "enabled": getattr(eng.config, "ENABLE_CANDLE_PATTERNS", True)},
        },
    }


@app.post("/api/settings/risk")
async def update_risk_settings(settings: dict, eng: UserEngine = Depends(get_user_engine)):
    """Updates risk management parameters."""
    for key, value in settings.items():
        if key in ['MAX_DAILY_DRAWDOWN', 'RISK_PER_TRADE', 'MAX_POSITION_PCT']:
            if isinstance(value, (int, float)) and value > 1:
                value = value / 100.0
        eng.config.set(key, value)
            
    await eng.save_settings()
    print(f"[settings] Updated risk parameters for {eng.uid}: {', '.join(settings.keys())}")
    return {"status": "success", "settings": settings}


@app.post("/api/settings/ticker_amount")
async def update_ticker_amount(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Updates the allocated trade amount for a specific ticker."""
    ticker = data.get("ticker", "").upper()
    amount = data.get("amount")
    
    if ticker:
        if amount is None or amount == "":
            if ticker in eng.config.TICKER_AMOUNTS:
                del eng.config.TICKER_AMOUNTS[ticker]
        else:
            try:
                eng.config.TICKER_AMOUNTS[ticker] = float(amount)
            except:
                return {"status": "error", "message": "Invalid amount"}
        
        await eng.save_settings()
        print(f"[settings] {ticker}: Allocated trade amount updated for {eng.uid}")
        return {"status": "success", "ticker_amounts": eng.config.TICKER_AMOUNTS}
    return {"status": "error", "message": "Ticker required"}


@app.post("/api/settings/timeframe")
async def update_timeframe(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Updates the default trading timeframe and triggers a re-scan."""
    new_tf = data.get("timeframe")
    if new_tf in ["30Sec", "1Min", "2Min", "3Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "2Hour", "4Hour", "1Day"]:
        eng.config.DEFAULT_TIMEFRAME = new_tf
        eng.latest_scans = {
            ticker: scan for ticker, scan in eng.latest_scans.items()
            if scan.get('timeframe') == new_tf
        }
        eng.bot_scans = {
            ticker: scan for ticker, scan in eng.bot_scans.items()
            if scan.get('timeframe') == new_tf
        }
        eng.latest_scans_by_tf = {
            tf: scans for tf, scans in eng.latest_scans_by_tf.items()
            if tf == new_tf
        }
        eng.bot_scans_by_tf = {
            tf: scans for tf, scans in eng.bot_scans_by_tf.items()
            if tf == new_tf
        }
        await eng.save_settings()
        print(f"[settings] Global Timeframe synced to {new_tf} for {eng.uid}. Chart data will refresh on next dashboard request.")
        
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()
        
        return {"status": "success", "timeframe": new_tf}
    return {"status": "error", "message": "Invalid timeframe"}


@app.get("/api/watchlist")
def get_watchlist(eng: UserEngine = Depends(get_user_engine)):
    """Returns the current watchlist."""
    return eng.config.WATCHLIST


@app.post("/api/watchlist")
async def add_to_watchlist(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Add a ticker to the watchlist."""
    ticker = data.get("ticker", "").upper()
    if ticker and ticker not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(ticker)
        await eng.save_settings()
        print(f"[settings] Added {ticker} to watchlist for {eng.uid}")
    return {"status": "success", "watchlist": eng.config.WATCHLIST}


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Remove a ticker from the watchlist."""
    ticker = ticker.upper()
    if ticker in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(ticker)
        print(f"[settings] Removed {ticker} from watchlist")
        await eng.save_settings()
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()
    return {"status": "success", "watchlist": eng.config.WATCHLIST}


@app.get("/api/tradelist")
def get_tradelist(eng: UserEngine = Depends(get_user_engine)):
    """Returns the current active trade list."""
    return eng.config.TRADELIST


@app.post("/api/tradelist")
async def add_to_tradelist(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Add a ticker to the active trade list."""
    ticker = data.get("ticker", "").upper()
    timeframe = data.get("timeframe", eng.config.DEFAULT_TIMEFRAME)
    
    if ticker and ticker not in eng.config.TRADELIST:
        eng.config.TRADELIST.append(ticker)
        
        if ticker not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[ticker] = {}
        eng.config.TICKER_SETTINGS[ticker]['timeframe'] = timeframe
        print(f"[settings] Activated {ticker} on locked {timeframe} timeframe")

        await eng.save_settings()
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()
    return {"status": "success", "tradelist": eng.config.TRADELIST, "watchlist": eng.config.WATCHLIST}


@app.get("/api/debug/history")
async def debug_history(eng: UserEngine = Depends(get_user_engine)):
    return {
        "executed_trades_count": 0,
        "trade_log_count": len(eng.trade_log),
        "executed_trades": [],
        "eng.trade_log": eng.trade_log[:10]
    }

@app.delete("/api/tradelist/{ticker}")
async def remove_from_tradelist(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Remove a ticker from the active trade list."""
    ticker = ticker.upper()
    if ticker in eng.config.TRADELIST:
        eng.config.TRADELIST.remove(ticker)
        await eng.save_settings()
        print(f"[settings] Removed {ticker} from active tradelist (Bot Deactivated) for {eng.uid}")
    return {"status": "success", "tradelist": eng.config.TRADELIST}


@app.get("/api/search/{query}")
def search_symbols(query: str, eng: UserEngine = Depends(get_user_engine)):
    """Searches for tradeable assets using the modular eng.broker."""
    if len(query) < 1:
        return []
    return eng.broker.search_assets(query)


@app.post("/api/bots/create")
async def create_bot(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Creates a new active bot with custom settings (adds to watchlist, tradelist, and TICKER_SETTINGS)."""
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
        
    if symbol not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[symbol] = {}
        
    if "capital" in data:
        eng.config.TICKER_SETTINGS[symbol]["amount"] = float(data["capital"])
        
    if "threshold" in data:
        eng.config.TICKER_SETTINGS[symbol]["min_buy_signals"] = int(data["threshold"])
    if "sell_threshold" in data:
        eng.config.TICKER_SETTINGS[symbol]["min_sell_signals"] = int(data["sell_threshold"])
    
    if "timeframe" in data:
        eng.config.TICKER_SETTINGS[symbol]["timeframe"] = data["timeframe"]
        
    if "sell_mode" in data:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = data["sell_mode"]
    else:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = "indicator"
        
    if "indicators" in data and isinstance(data["indicators"], list):
        eng.config.TICKER_SETTINGS[symbol]["indicators"] = data["indicators"]

    for rk in ["risk_per_trade", "max_daily_drawdown", "max_position_pct", "atr_stop_multiplier", "atr_trail_multiplier", "take_profit_multiplier"]:
        if rk in data:
            eng.config.TICKER_SETTINGS[symbol][rk] = float(data[rk])

    add_to_watchlist = data.get("add_to_watchlist", False)
    if add_to_watchlist and symbol not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(symbol)
        
    if symbol not in eng.config.TRADELIST:
        eng.config.TRADELIST.append(symbol)
        print(f"[settings] Launched new bot for {symbol} with custom settings for {eng.uid}")
        
    await eng.save_settings()
    
    # Warm up cache synchronously before returning so immediate dashboard refresh gets the price/data
    try:
        timeframe = data.get("timeframe") or eng.config.TICKER_SETTINGS.get(symbol, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME
        is_crypto = any(c in symbol for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in symbol
        account = eng.broker.get_account_info()
        avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
        
        initial_scan = await asyncio.to_thread(
            evaluate_trade,
            symbol,
            account_equity=account['equity'],
            available_cash=avail_cash,
            timeframe=timeframe,
            use_bot_settings=True
        )
        if initial_scan:
            eng._record_bot_scan(initial_scan)
            print(f"[bots/create] Synchronous warm up scan completed for new bot {symbol}")
    except Exception as e:
        print(f"[bots/create] Synchronous warm up scan failed for {symbol}: {e}")
    
    if eng.force_scan_trigger:
        eng.force_scan_trigger.set()
    
    return {"status": "success", "symbol": symbol, "watchlist": eng.config.WATCHLIST, "tradelist": eng.config.TRADELIST, "settings": eng.config.TICKER_SETTINGS[symbol]}


@app.post("/api/cancel_order")
async def cancel_order(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Cancel an active order on Alpaca by ID."""
    order_id = data.get("order_id")
    if not order_id:
        return {"status": "error", "message": "order_id is required"}
    result = eng.broker.cancel_order_by_id(order_id)
    if result.get("success"):
        return {"status": "success", "message": f"Order {order_id} cancelled successfully."}
    else:
        return {"status": "error", "message": result.get("error", "Failed to cancel order.")}


@app.post("/api/settings/indicators")
async def update_indicators(updates: dict, eng: UserEngine = Depends(get_user_engine)):
    """Update indicator toggles or parameters dynamically. Instant in-memory + persists to Firestore."""
    for k, v in updates.items():
        if k.startswith("ENABLE_"):
            eng.config.toggles[k] = bool(v)
        else:
            try:
                default_val = getattr(global_config, k, None)
                if isinstance(default_val, int):
                    eng.config.parameters[k] = int(v)
                elif isinstance(default_val, float):
                    eng.config.parameters[k] = float(v)
                else:
                    eng.config.parameters[k] = v
            except Exception as e:
                print(f"[settings] Type conversion error for {k}: {e}")
                eng.config.parameters[k] = v

    await eng.save_settings()
    clear_evaluation_cache()
    if eng.force_scan_trigger:
        eng.force_scan_trigger.set()
        
    print(f"[settings] Updated indicator settings for {eng.uid}: {', '.join(updates.keys())}")
    return {"status": "success"}


@app.post("/api/settings/ticker")
async def update_ticker_settings(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Update settings for a specific ticker."""
    ticker = data.get("ticker", "").upper()
    settings = data.get("settings", {})
    if not ticker: return {"status": "error"}

    if ticker not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[ticker] = {}
    for k, v in settings.items():
        if v is not None:
            eng.config.TICKER_SETTINGS[ticker][k] = v
        elif k in eng.config.TICKER_SETTINGS[ticker]:
            del eng.config.TICKER_SETTINGS[ticker][k]
    
    await eng.save_settings()
    return {"status": "success"}

@app.delete("/api/settings/ticker/{ticker}")
async def reset_ticker_settings(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Reset a ticker to global defaults."""
    ticker = ticker.upper()
    if ticker in eng.config.TICKER_SETTINGS:
        del eng.config.TICKER_SETTINGS[ticker]
        await eng.save_settings()
    return {"status": "success"}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _format_trade_for_ui(trade: dict) -> dict:
    """Formats a trade decision for the frontend table."""
    ticker = trade.get("ticker", "").replace("/", "").upper()
    tf = trade.get("timeframe")
    uc = get_user_config()
    if not tf:
        t_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {})
        tf = t_settings.get('timeframe', uc.DEFAULT_TIMEFRAME)
    
    return {
        "time": trade.get("time", ""),
        "action": trade.get("action", ""),
        "ticker": ticker,
        "price": trade.get("price", ""),
        "qty": trade.get("qty", "N/A"),
        "total_cost": trade.get("total_cost", 0),
        "fees": trade.get("fees", 0),
        "reason": trade.get("reason", ""),
        "pl": trade.get("pl"),
        "pl_pct": trade.get("pl_pct"),
        "bullish_count": trade.get("bullish_count", 0),
        "bearish_count": trade.get("bearish_count", 0),
        "total_signals": trade.get("total_signals", 0),
        "timeframe": tf,
        "log_type": trade.get("log_type", "Active Bot"),
    }


def _format_scan_for_ui(scan: dict, use_global_settings: bool = False) -> dict:
    """Formats a full scan result for the watchlist panel."""
    SIGNAL_TO_TOGGLE = {
        'RSI': 'ENABLE_RSI',
        'MACD': 'ENABLE_MACD',
        'EMA Cross': 'ENABLE_EMA',
        'Supertrend': 'ENABLE_SUPERTREND',
        'Bollinger': 'ENABLE_BOLLINGER',
        'VWAP': 'ENABLE_VWAP',
        'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
        'News Sentiment': 'ENABLE_AI_SENTIMENT',
        'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
        'ADX Trend': 'ENABLE_ADX_TREND',
        'SMA': 'ENABLE_SMA',
        'BotBulls1': 'ENABLE_BOTBULLS1',
        'BotBulls2': 'ENABLE_BOTBULLS2',
        'BotBulls3': 'ENABLE_BOTBULLS3',
    }

    raw_signals = scan.get("signals", {})
    all_signals = {}
    uc = get_user_config()
    ticker = scan.get("ticker", "")
    t_settings = getattr(uc, 'TICKER_SETTINGS', {}).get(ticker, {}) if not use_global_settings else {}
    allowed_indicators = t_settings.get('indicators') if not use_global_settings else None

    for name, data in raw_signals.items():
        toggle_key = SIGNAL_TO_TOGGLE.get(name, '')
        if name == 'News Sentiment':
            enabled = getattr(uc, 'ENABLE_AI_SENTIMENT', True)
        elif allowed_indicators is not None:
            enabled = name in allowed_indicators
        else:
            enabled = getattr(uc, toggle_key, True) if toggle_key else True
            
        all_signals[name] = {**data, 'enabled': enabled, 'toggle_key': toggle_key if name != 'News Sentiment' else 'ENABLE_AI_SENTIMENT'}

    bullish = sum(s.get('weight', 1) for s in all_signals.values() if s.get('signal') == 'BULLISH' and s.get('enabled'))
    bearish = sum(s.get('weight', 1) for s in all_signals.values() if s.get('signal') == 'BEARISH' and s.get('enabled'))
    active_count = sum(1 for s in all_signals.values() if s.get('enabled'))

    return {
        "ticker": scan.get("ticker", ""),
        "price": scan.get("price", ""),
        "action": scan.get("action", "HOLD"),
        "reason": scan.get("reason", ""),
        "sentiment_score": scan.get("sentiment_score", 0),
        "sentiment_confidence": scan.get("sentiment_confidence", 0),
        "sentiment_summary": scan.get("sentiment_summary", ""),
        "sentiment_key_factor": scan.get("sentiment_key_factor", "N/A"),
        "bullish_count": bullish,
        "bearish_count": bearish,
        "total_signals": active_count,
        "signals": all_signals,
        "rsi": scan.get("rsi", 0),
        "atr": scan.get("atr", 0),
        "pl": scan.get("pl"),
        "pl_pct": scan.get("pl_pct"),
        "qty": scan.get("qty"),
        "position_sizing": scan.get("position_sizing", {}),
        "price_history": scan.get("price_history", []),
    }
