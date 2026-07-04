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

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*utcnow.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=".*webull.*")

from fastapi import FastAPI, Query, Header, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager, contextmanager
from pydantic import BaseModel
import asyncio
import threading
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
from swing_strategy import build_swing_exit, detect_swing_entry, format_swing_preview, build_entry_signal, format_entry_preview
import config as global_config
from user_config import set_user_config, get_user_config
from ai_indicator import get_ai_indicator, clear_ai_cache
from cache_manager import TTLCacheStore, stable_cache_key

# Global threading event for cancelling backtests on page reload
global_backtest_cancel = threading.Event()

# ──────────────────────────────────────────────
# Secret Encryption Vault
# ──────────────────────────────────────────────
class Vault:
    """Handles bank-grade encryption for sensitive API keys."""
    def __init__(self):
        secret_key = os.getenv("VAULT_SECRET_KEY")
        if secret_key:
            try:
                self.fernet = Fernet(secret_key.encode())
                print("[vault] Running in HIGH-SECURITY mode (VAULT_SECRET_KEY loaded)")
            except Exception as e:
                print(f"[vault] ERROR: Invalid VAULT_SECRET_KEY format ({e}). Falling back to project ID.")
                self._load_fallback()
        else:
            print("[vault] WARNING: Running in fallback mode. Set VAULT_SECRET_KEY for production.")
            self._load_fallback()

    def _load_fallback(self):
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
            firebase_admin.initialize_app(options={'projectId': 'bot-bulls'})
        print("[main] Firebase Admin initialized.")
    except Exception as e:
        print(f"[main] Firebase Admin init error: {e}")

db = firestore.client()
user_manager = UserManager(db)
_webull_market_data_broker = None
_chart_analysis_cache = TTLCacheStore(maxsize=256)
_quote_cache = TTLCacheStore(maxsize=512)


def _cache_settings_hash(eng: UserEngine, ticker: str | None = None, context: str = "dashboard") -> str:
    ticker_key = (ticker or "").upper()
    ctx_data = eng.config.contexts.get(context, {}) if getattr(eng.config, "contexts", None) else {}
    payload = {
        "ctx": context,
        "active_toggles": eng.config.active_toggles,
        "active_parameters": eng.config.active_parameters,
        "ticker_settings": eng.config.TICKER_SETTINGS.get(ticker_key, {}) if ticker_key else {},
        "ctx_toggles": ctx_data.get("toggles", {}),
        "ctx_params": ctx_data.get("parameters", {}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


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


def _normalize_analysis_context(source: str = "dashboard") -> str:
    return source if source in {"dashboard", "bots", "backtest"} else "dashboard"


@contextmanager
def _analysis_request_context(source: str = "dashboard", ticker: str = None, use_bot_settings: bool = False):
    from user_config import active_evaluation_context, active_ticker_context

    eval_token = active_evaluation_context.set(_normalize_analysis_context(source))
    ticker_token = active_ticker_context.set(_normalize_quote_symbol(ticker)) if use_bot_settings and ticker else None
    try:
        yield
    finally:
        if ticker_token is not None:
            active_ticker_context.reset(ticker_token)
        active_evaluation_context.reset(eval_token)


def _format_scan_price(price) -> str:
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "$0.00"
    return f"${p:.4f}" if p < 10 else f"${p:.2f}"


def _clear_chart_analysis_cache(ticker: str = None, timeframe: str = None):
    ticker_key = _normalize_quote_symbol(ticker) if ticker else None
    timeframe_key = timeframe.upper() if timeframe else None
    _chart_analysis_cache.delete_matching(
        lambda k: (
            (not ticker_key or k[2] == ticker_key)
            and (not timeframe_key or k[3] == timeframe_key)
        )
    )


def _get_cached_chart_analysis(ticker: str, timeframe: str, eng: UserEngine, force: bool = False, source: str = "dashboard"):
    ticker_key = _normalize_quote_symbol(ticker)
    timeframe_key = timeframe or get_user_config().DEFAULT_TIMEFRAME
    settings_hash = _cache_settings_hash(eng, ticker_key, source)
    cache_key = stable_cache_key(eng.uid, source, ticker_key, timeframe_key, "webull", settings_hash, 1)
    cached, age = _chart_analysis_cache.get_with_age(cache_key, _chart_cache_ttl(timeframe_key))
    if cached and not force:
        return cached["analysis"], True, age

    analysis = get_full_analysis(ticker_key, timeframe=timeframe_key, data_source="webull")
    if analysis:
        _chart_analysis_cache.set(cache_key, {"analysis": analysis})
    return analysis, False, 0.0



def _get_prior_day_ohlc(price_history):
    from datetime import datetime
    import pytz
    if not price_history: return None
    ny_tz = pytz.timezone('America/New_York')
    days = {}
    for candle in price_history:
        dt = datetime.fromtimestamp(candle['time'], ny_tz)
        date_str = dt.strftime('%Y-%m-%d')
        if date_str not in days: days[date_str] = []
        days[date_str].append(candle)
    sorted_days = sorted(days.keys())
    if len(sorted_days) < 2: return None
    prior_date = sorted_days[-2]
    prior_candles = days[prior_date]
    high = max((c['high'] for c in prior_candles if c.get('high') is not None), default=None)
    low = min((c['low'] for c in prior_candles if c.get('low') is not None), default=None)
    close = None
    for c in reversed(prior_candles):
        if c.get('close') is not None:
            close = c['close']
            break
    if high is None or low is None or close is None: return None
    return {"high": high, "low": low, "close": close, "date": prior_date}


def _next_level_above(levels, current_price, fallback_step):
    valid = sorted([float(v) for v in levels if v is not None and float(v) > current_price])
    if valid:
        return valid[0]
    step = abs(float(fallback_step or 0.0))
    return current_price + step if step > 0 else None


def _nearest_level_below(levels, current_price):
    valid = sorted([float(v) for v in levels if v is not None and float(v) < current_price], reverse=True)
    return valid[0] if valid else None


def _dynamic_pivot_exit_levels(prev_h, prev_l, prev_c, current_price):
    day_range = max(float(prev_h) - float(prev_l), 0.0)
    if day_range <= 0:
        return None
    p_val = (float(prev_h) + float(prev_l) + float(prev_c)) / 3.0
    r1 = (2 * p_val) - float(prev_l)
    s1 = (2 * p_val) - float(prev_h)
    r2 = p_val + day_range
    s2 = p_val - day_range
    r3 = float(prev_h) + 2 * (p_val - float(prev_l))
    s3 = float(prev_l) - 2 * (float(prev_h) - p_val)
    tp = _next_level_above([r1, r2, r3, r3 + day_range], current_price, day_range)
    sl = _nearest_level_below([r3, r2, r1, p_val, s1, s2, s3], current_price)
    return {"tp": tp, "sl": sl}


def _dynamic_high_low_exit_levels(prev_h, prev_l, current_price):
    day_range = max(float(prev_h) - float(prev_l), 0.0)
    if day_range <= 0:
        return None
    if current_price >= float(prev_h):
        tp = _next_level_above([float(prev_h) + day_range, float(prev_h) + (2 * day_range)], current_price, day_range)
        sl = float(prev_h)
    else:
        tp = float(prev_h)
        sl = float(prev_l)
    return {"tp": tp, "sl": sl}


def _format_exit_protection_status(eng: UserEngine, symbol: str, is_option: bool, is_crypto: bool):
    if is_option:
        return {"label": "Exit Protection", "status": "Watching underlying", "active": False}
    if is_crypto:
        return {"label": "Exit Protection", "status": "Watching TP/SL", "active": False}

    try:
        levels = eng.broker.get_exit_order_levels(symbol)
    except Exception as exc:
        print(f"[dashboard] Exit protection status unavailable for {symbol}: {exc}")
        return {"label": "Exit Protection", "status": "Unknown", "active": False}

    tp = levels.get("take_profit")
    sl = levels.get("stop_loss")
    if tp is not None and sl is not None and levels.get("orders"):
        return {
            "label": "Exit Protection",
            "status": f"OCO active | TP ${float(tp):.2f} | SL ${float(sl):.2f}",
            "active": True,
            "take_profit": float(tp),
            "stop_loss": float(sl),
        }
    return {"label": "Exit Protection", "status": "No OCO order yet", "active": False}


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
    prior_day = _get_prior_day_ohlc(analysis.get("price_history", []))
    return {
        "prior_day_ohlc": prior_day,
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


def _is_crypto_symbol(symbol: str) -> bool:
    clean = _normalize_quote_symbol(symbol)
    return clean.endswith(("USD", "USDT", "USDC")) or clean in {"BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"}


def _normalize_market_session_for_symbol(symbol: str, session: str = None) -> str:
    session_text = str(session or "").strip()
    if _is_crypto_symbol(symbol):
        return "24h"
    if session_text.lower() in {"", "default"}:
        return "Regular"
    if session_text.lower() == "crypto24":
        return "24h"
    return session_text


def _validate_market_session_for_symbol(symbol: str, market_session: str, eng: UserEngine) -> dict:
    session = _normalize_market_session_for_symbol(symbol, market_session)
    check = eng.broker.check_market_session_allowed(symbol, session)
    if not check.get("success") or not check.get("allowed"):
        message = check.get("message") or check.get("error") or f"{symbol} is not eligible for {session} trading."
        raise HTTPException(status_code=400, detail=message)
    return {**check, "market_session": session}


def _format_quote_price(symbol: str, price) -> str:
    try:
        price_float = float(price)
    except (TypeError, ValueError):
        return ""
    clean = _normalize_quote_symbol(symbol)
    is_crypto = any(clean.endswith(base) for base in ["USD", "USDT", "USDC"])
    
    if is_crypto:
        decimals = 8
    elif price_float < 1.0:
        decimals = 4
    else:
        decimals = 2
        
    res = f"${price_float:.{decimals}f}"
    if is_crypto and '.' in res:
        res = res.rstrip('0').rstrip('.')
        if not res[1:]: return "$0" # if it was $0.00000000 -> $
    return res


def _get_stale_latest_prices(eng: UserEngine, tickers: list, max_age: int = 300) -> dict:
    clean_tickers = list(dict.fromkeys(_normalize_quote_symbol(t) for t in tickers if _normalize_quote_symbol(t)))
    result = {}
    for ticker in clean_tickers:
        cache_key = stable_cache_key(eng.uid, ticker, "quote", 1)
        cached, age = _quote_cache.peek_with_age(cache_key)
        if cached is not None and age is not None and age < max_age:
            result[ticker] = cached
    return result


def _get_cached_latest_prices(eng: UserEngine, tickers: list, max_age: int = 3, stale_max_age: int = 300) -> dict:
    clean_tickers = list(dict.fromkeys(_normalize_quote_symbol(t) for t in tickers if _normalize_quote_symbol(t)))
    if not clean_tickers:
        return {}

    result = {}
    misses = []

    for ticker in clean_tickers:
        cache_key = stable_cache_key(eng.uid, ticker, "quote", 1)
        cached, age = _quote_cache.peek_with_age(cache_key)
        if cached is not None and age is not None and age < max_age:
            result[ticker] = cached
        else:
            misses.append(ticker)

    if misses:
        provider = (
            eng.broker
            if hasattr(eng.broker, "get_latest_prices") and getattr(eng.broker, "market_client", None)
            else None
        )

        if provider is None:
            provider = _get_webull_market_data_broker()

        if provider is not None:
            try:
                fresh = provider.get_latest_prices(misses) or {}
                for ticker, price in fresh.items():
                    cache_key = stable_cache_key(eng.uid, _normalize_quote_symbol(ticker), "quote", 1)
                    _quote_cache.set(cache_key, price)
                    result[_normalize_quote_symbol(ticker)] = price
            except Exception as e:
                print(f"[dashboard] Live quote fetch failed for {misses}: {type(e).__name__}: {e!r}")

        for ticker in misses:
            if ticker not in result:
                cache_key = stable_cache_key(eng.uid, ticker, "quote", 1)
                cached, age = _quote_cache.peek_with_age(cache_key)
                if cached is not None and age is not None and age < stale_max_age:
                    result[ticker] = cached

    return result


def _lookup_quote_price(latest_prices: dict, symbol: str):
    clean = _normalize_quote_symbol(symbol)
    for key in (symbol, clean):
        if key in latest_prices:
            return latest_prices[key]
    return None


def _get_cached_quote_detail(eng: UserEngine, symbol: str, max_age: int = 10, stale_max_age: int = 300) -> dict:
    clean = _normalize_quote_symbol(symbol)
    cache_key = stable_cache_key(eng.uid, clean, "quote", 1)
    cached, age = _quote_cache.peek_with_age(cache_key)
    if cached is not None and age is not None and age < max_age:
        return {"price": cached, "cache_age": age, "source": "cache", "stale": False}

    providers = []
    webull_provider = _get_webull_market_data_broker()
    if webull_provider is not None:
        providers.append(("webull", webull_provider))

    broker_provider = (
        eng.broker
        if hasattr(eng.broker, "get_latest_prices") and getattr(eng.broker, "market_client", None)
        else None
    )
    if broker_provider is not None and broker_provider is not webull_provider:
        providers.append(("broker", broker_provider))

    for source, provider in providers:
        try:
            fresh = provider.get_latest_prices([clean]) or {}
            price = _lookup_quote_price(fresh, clean)
            if price is not None:
                _quote_cache.set(cache_key, price)
                return {"price": price, "cache_age": 0.0, "source": source, "stale": False}
        except Exception as e:
            print(f"[dashboard] Live quote fetch failed for {clean} via {source}: {type(e).__name__}: {e!r}")

    cached, age = _quote_cache.peek_with_age(cache_key)
    if cached is not None and age is not None and age < stale_max_age:
        return {"price": cached, "cache_age": age, "source": "stale_cache", "stale": True}

    return {"price": None, "cache_age": None, "source": "unavailable", "stale": True}


def _get_webull_market_data_broker():
    """Return a data-only Webull broker for realtime quotes, independent of the trading broker."""
    global _webull_market_data_broker

    if _webull_market_data_broker and getattr(_webull_market_data_broker, "market_client", None):
        return _webull_market_data_broker

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
        if os.getenv("ENV") == "development":
            return {"uid": "dev-user", "email": "dev@example.com"}
        else:
            raise HTTPException(status_code=401, detail="Development backdoor is disabled in production")
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

allowed_origins = [
    "https://bot-bulls.web.app",
    "https://bot-bulls.firebaseapp.com",
    "https://trading-bot-engine-df3de.firebaseapp.com",
    "https://trading-bot-engine-df3de.web.app",
    "https://207-211-184-22.sslip.io",
]

if os.getenv("ENV") != "production":
    allowed_origins.extend([
        "http://localhost:5000",
        "http://localhost:3000",
        "http://127.0.0.1:5500"
    ])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def reset_analysis_context_middleware(request: Request, call_next):
    from user_config import active_evaluation_context, active_ticker_context

    eval_token = active_evaluation_context.set("dashboard")
    ticker_token = active_ticker_context.set(None)
    try:
        return await call_next(request)
    finally:
        active_ticker_context.reset(ticker_token)
        active_evaluation_context.reset(eval_token)

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
    alpaca_period = "1A" if period.upper() == "1Y" else ("all" if period.upper() == "ALL" else period)
    try:
        data = await asyncio.to_thread(eng.broker.get_portfolio_history, period=alpaca_period, timeframe=timeframe)
        return data
    except Exception as e:
        print(f"[api] Error getting portfolio history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
import time as time_module
_MARKET_DATA_CACHE = None
_MARKET_DATA_LAST_FETCH = 0

@app.get("/api/market-data")
async def get_market_data(eng: UserEngine = Depends(get_user_engine)):
    global _MARKET_DATA_CACHE, _MARKET_DATA_LAST_FETCH
    # Cache for 60 seconds
    if _MARKET_DATA_CACHE and time_module.time() - _MARKET_DATA_LAST_FETCH < 60:
        return _MARKET_DATA_CACHE
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


@app.get("/api/quote/{ticker}")
async def get_live_quote(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Lightweight quote-only endpoint for visual active-chart candle updates."""
    clean = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", clean):
        raise HTTPException(status_code=400, detail="Invalid ticker format")

    quote = await asyncio.to_thread(_get_cached_quote_detail, eng, clean, 10, 300)
    price = quote.get("price")
    if price is None:
        raise HTTPException(status_code=404, detail=f"Quote unavailable for {clean}")

    return {
        "ticker": clean,
        "price": float(price),
        "price_display": _format_quote_price(clean, price),
        "server_time": get_now().isoformat(),
        "cache_age_seconds": round(float(quote.get("cache_age") or 0.0), 1),
        "stale": bool(quote.get("stale")),
        "source": quote.get("source") or "unavailable",
    }


_macro_calendar_cache = {"data": [], "timestamp": 0}

@app.get("/api/macro-events")
async def get_macro_events():
    try:
        from macro_service import get_macro_data
        
        def fetch_calendar():
            global _macro_calendar_cache
            import time
            now = time.time()
            # Cache for 1 hour
            if now - _macro_calendar_cache["timestamp"] < 3600 and _macro_calendar_cache["data"]:
                return _macro_calendar_cache["data"]

            import requests
            try:
                url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                r = requests.get(url, headers=headers, timeout=5)
                r.raise_for_status()
                data = r.json()
                
                # Expand to High/Medium USD and High major global economies
                def is_relevant(e):
                    if e.get('country') == 'USD' and e.get('impact') in ['High', 'Medium']: return True
                    if e.get('country') in ['EUR', 'GBP', 'CNY', 'JPY'] and e.get('impact') == 'High': return True
                    return False
                    
                result = [e for e in data if is_relevant(e)]
                _macro_calendar_cache["data"] = result
                _macro_calendar_cache["timestamp"] = now
                return result
            except Exception as e:
                print(f"Error fetching forex calendar: {e}")
                return _macro_calendar_cache["data"]
                
        # Run both operations concurrently
        indicators_task = asyncio.to_thread(get_macro_data, False)
        calendar_task = asyncio.to_thread(fetch_calendar)
        
        indicators, calendar = await asyncio.gather(indicators_task, calendar_task)
        
        return {"status": "success", "indicators": indicators, "calendar": calendar}
    except Exception as e:
        print(f"Error fetching macro events: {e}")
        return {"status": "error", "message": str(e), "indicators": {}, "calendar": []}


def _get_threshold_setting(settings, keys, default_value):
    for key in keys:
        value = (settings or {}).get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    try:
        return int(default_value)
    except (TypeError, ValueError):
        return 1


def _normalize_signal_threshold(value, selected_count, default_value):
    if selected_count <= 0:
        return 0

    try:
        threshold = int(value)
    except (TypeError, ValueError):
        threshold = 0

    try:
        default_threshold = int(default_value)
    except (TypeError, ValueError):
        default_threshold = 1

    if threshold <= 0:
        threshold = min(default_threshold, selected_count)

    return max(1, min(threshold, selected_count))


def _normalize_bot_signal_thresholds(settings, eng):
    indicators = settings.get("indicators")
    if not isinstance(indicators, list):
        return False

    before = (settings.get("min_buy_signals"), settings.get("min_sell_signals"))
    selected_count = len(indicators)
    buy_default = getattr(eng.config, "MIN_BULLISH_SIGNALS", getattr(get_user_config(), "MIN_BULLISH_SIGNALS", 1))
    sell_default = getattr(eng.config, "MIN_BEARISH_SIGNALS", getattr(get_user_config(), "MIN_BEARISH_SIGNALS", 1))
    settings["min_buy_signals"] = _normalize_signal_threshold(settings.get("min_buy_signals"), selected_count, buy_default)
    settings["min_sell_signals"] = _normalize_signal_threshold(settings.get("min_sell_signals"), selected_count, sell_default)
    return before != (settings.get("min_buy_signals"), settings.get("min_sell_signals"))


async def _repair_bot_signal_thresholds_if_needed(eng):
    repaired = False
    for settings in eng.config.TICKER_SETTINGS.values():
        if _normalize_bot_signal_thresholds(settings, eng):
            repaired = True
    if repaired:
        await eng.save_settings()
        print("[settings] Repaired zero/invalid bot signal thresholds")


def _build_indicator_forecast(settings, scan_data, mode="entry", is_long=True, is_option=False, options_type="both", eng=None):
    if not scan_data: return ""
    signals = scan_data.get('signals', {})
    
    # active_signals filter only those with enabled=True (set by trader.py)
    active_signals = {k: v for k, v in signals.items() if v.get('enabled', False)}
    
    if not active_signals:
        return ""
        
    parts = []
    
    cfg = getattr(eng, "config", None)
    user_cfg = get_user_config()
    buy_default = getattr(cfg, 'MIN_BULLISH_SIGNALS', getattr(user_cfg, 'MIN_BULLISH_SIGNALS', 1))
    sell_default = getattr(cfg, 'MIN_BEARISH_SIGNALS', getattr(user_cfg, 'MIN_BEARISH_SIGNALS', 1))
    buy_threshold = _get_threshold_setting(settings, ('min_buy_signals', 'threshold', 'buy_threshold'), buy_default)
    sell_threshold = _get_threshold_setting(settings, ('min_sell_signals', 'sell_threshold'), sell_default)
    
    if mode == "entry":
        target = "BULLISH"
        if is_option and options_type == "puts_only":
            target = "BEARISH"
        target_label = target
        if is_option and options_type == "both":
            target_label = "OPTIONS"
            
        current_count = 0
        for name, data in active_signals.items():
            sig = data.get('signal', 'NEUTRAL')
            if sig == target or (target == "BULLISH" and sig == "BEARISH" and is_option and options_type == "both"):
                current_count += data.get('weight', 1)
                
        rem = max(0, buy_threshold - current_count)
        parts.append(f"[{target_label} TARGET: {buy_threshold} | REMAINING: {rem}]")
        
        for name, data in active_signals.items():
            sig = data.get('signal', 'NEUTRAL')
            reason = data.get('reason', '')
            if sig == target:
                icon = "🔴" if sig == "BEARISH" else "✅"
                parts.append(f"{icon} {name} {sig} ({reason})")
            elif target == "BULLISH" and sig == "BEARISH" and is_option and options_type == "both":
                parts.append(f"🔴 {name} BEARISH ({reason})")
            else:
                parts.append(f"⏳ {name} {sig} ({reason})")
    else:
        target = "BEARISH" if is_long else "BULLISH"
        current_count = 0
        for name, data in active_signals.items():
            if data.get('signal') == target:
                current_count += data.get('weight', 1)
                
        rem = max(0, sell_threshold - current_count)
        parts.append(f"[{target} EXIT TARGET: {sell_threshold} | REMAINING: {rem}]")
        
        for name, data in active_signals.items():
            sig = data.get('signal', 'NEUTRAL')
            reason = data.get('reason', '')
            if sig == target:
                icon = "🔴" if sig == "BEARISH" else "✅"
                parts.append(f"{icon} {name} {sig} ({reason})")
            else:
                parts.append(f"⏳ {name} {sig} ({reason})")
                
    return " | ".join(parts)


def _build_swing_forecast(settings, scan_data, mode="entry", is_long=True, is_option=False, options_type="both", eng=None):
    if not scan_data:
        return ""

    price_history = scan_data.get("price_history", [])
    current_price = scan_data.get("price_raw") or scan_data.get("price") or 0.0
    try:
        current_price = float(str(current_price).replace("$", "").replace(",", ""))
    except Exception:
        current_price = 0.0

    market_session = settings.get("market_session", "Regular")
    is_crypto = any(str(scan_data.get("ticker", "")).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or str(scan_data.get("ticker", "")).upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]

    if mode == "entry":
        lookback_days = int(
            settings.get(
                "swing_entry_lookback_days",
                settings.get("swing_lookback_days", 20),
            ) or 20
        )
        entry_mode = settings.get("swing_entry_mode", "bounce_retest")
        swing_decision = detect_swing_entry(
            price_history,
            current_price=current_price,
            entry_mode=entry_mode,
            lookback_days=lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
        )
        ctx = swing_decision.get("context", {})
        support = ctx.get("nearest_support")
        resistance = ctx.get("nearest_resistance")
        parts = [
            f"🔮 Live Preview: {scan_data.get('ticker', 'Example')} (Entry Swing) | Price: ${current_price:.2f}",
            f"[SWING ENTRY: {entry_mode.upper()} | LOOKBACK: {lookback_days}D]",
            f"Support: ${support['price']:.2f}" if support else "Support: N/A",
            f"Resistance: ${resistance['price']:.2f}" if resistance else "Resistance: N/A",
            swing_decision.get("reason", "Swing Points: waiting"),
        ]
        if is_option:
            parts.insert(1, f"Options: {options_type.upper()}")
        return " | ".join(parts)

    lookback_days = int(
        settings.get(
            "swing_exit_lookback_days",
            settings.get("swing_lookback_days", 20),
        ) or 20
    )
    swing_exit = build_swing_exit(
        price_history,
        current_price=current_price,
        is_long=is_long,
        lookback_days=lookback_days,
        market_session=market_session,
        is_crypto=is_crypto,
    )
    symbol = scan_data.get("ticker", "")
    return format_swing_preview(
        symbol=symbol,
        swing_exit=swing_exit,
        current_price=current_price,
        is_long=is_long,
        is_option=is_option,
    )


def _entry_forecast_float(value, default=0.0):
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _normalize_pct_setting(value, default=0.0):
    pct = _entry_forecast_float(value, default)
    if pct is None:
        pct = default
    if pct > 1:
        pct = pct / 100.0
    return max(0.0, pct)


def _option_pct_exit_settings(settings):
    return (
        _normalize_pct_setting(settings.get('options_stop_loss_pct', settings.get('regular_stop_loss_pct', 0.10)), 0.10),
        _normalize_pct_setting(settings.get('options_take_profit_pct', settings.get('regular_take_profit_pct', 0.20)), 0.20),
        _normalize_pct_setting(settings.get('options_trailing_pct', settings.get('regular_trailing_pct', 0.10)), 0.10),
    )


def _entry_forecast_money(value):
    value = _entry_forecast_float(value, None)
    return "N/A" if value is None else f"${value:.2f}"


def _entry_card_prior_day(scan_data, entry_signal):
    prior = (scan_data or {}).get("prior_day_ohlc")
    if prior:
        return prior

    ctx = (entry_signal or {}).get("context") or {}
    candles = ctx.get("candles") or []
    return _get_prior_day_ohlc(candles)


def _entry_strategy_label(buy_mode):
    labels = {
        "indicator": "Indicator",
        "custom_price": "Custom Price",
        "atr": "ATR",
        "hybrid_atr": "Hybrid ATR",
        "percentage": "Percentage",
        "hybrid_pct": "Hybrid Percentage",
        "pivot_points": "Pivot Points",
        "hybrid_pivot": "Hybrid Pivot",
        "high_low": "Prior Day High/Low",
        "hybrid_high_low": "Hybrid High/Low",
        "swing_points": "Swing Points",
        "hybrid_swing": "Hybrid (Indicator + Swing)",
    }
    return labels.get(buy_mode or "indicator", str(buy_mode or "indicator").replace("_", " ").title())


def _exit_strategy_label(sell_mode):
    labels = {
        "indicator": "Indicator",
        "custom_sltp": "Custom SL/TP",
        "custom_underlying_sltp": "Custom Underlying SL/TP",
        "sltp": "ATR Stop / Take Profit",
        "hybrid": "Hybrid (Indicator + ATR)",
        "percentage": "Percentage",
        "hybrid_pct": "Hybrid (Indicator + Percentage)",
        "pivot_points": "Pivot Points",
        "hybrid_pivot": "Hybrid (Indicator + Pivot)",
        "high_low": "Prior Day High/Low",
        "hybrid_high_low": "Hybrid (Indicator + High/Low)",
        "swing_points": "Swing Points",
        "hybrid_swing": "Hybrid (Indicator + Swing)",
        "premium_pct": "Premium Percentage",
    }
    return labels.get(sell_mode or "indicator", str(sell_mode or "indicator").replace("_", " ").title())


def _format_entry_card_forecast(settings, scan_data, entry_signal, current_price, buy_mode, is_option=False, options_type="both"):
    ctx = (entry_signal or {}).get("context") or {}
    reason = (entry_signal or {}).get("reason") or "Entry strategy: waiting for confirmation"
    action = (entry_signal or {}).get("action", "HOLD")
    status = "Ready" if action in ("BUY", "SELL") else "Waiting"
    mode = buy_mode or "indicator"
    parts = [f"[Entry Strategy] {_entry_strategy_label(mode)}"]

    if is_option:
        parts.append(f"[Options] {str(options_type or 'both').upper()}")

    if mode == "custom_price":
        target = settings.get("custom_buy_price", (entry_signal or {}).get("trigger_price"))
        label = "CALL Target Entry" if is_option else "Custom Entry"
        parts.extend([
            f"[{label}] {_entry_forecast_money(target)}",
            f"[Status] {status}",
            reason,
        ])
        return " | ".join(parts)

    if mode in ("atr", "hybrid_atr"):
        atr_val = _entry_forecast_float(scan_data.get("atr"), 0.0)
        if atr_val <= 0:
            atr_val = _entry_forecast_float(ctx.get("average_range"), 0.0)
        mult = _entry_forecast_float(settings.get("entry_atr_multiplier", settings.get("atr_stop_multiplier", 2.0)), 2.0)
        style = settings.get("entry_atr_style", "pullback_reclaim")
        prev_close = current_price
        candles = ctx.get("candles") or []
        if len(candles) >= 2:
            prev_close = _entry_forecast_float(candles[-2].get("close"), current_price)
        offset = max(atr_val * max(mult, 0.1), current_price * 0.001)
        long_level = prev_close + offset if style == "breakout_momentum" else prev_close - offset
        short_level = prev_close - offset if style == "breakout_momentum" else prev_close + offset
        label = "ATR Buy Above" if style == "breakout_momentum" else "ATR Buy Below"
        style_label = "Breakout / Momentum" if style == "breakout_momentum" else "Pullback / Reclaim"
        if mode == "hybrid_atr":
            parts.append("[Hybrid] ATR setup or indicator buy threshold")
        if is_option:
            option_side = str(options_type or "both").lower()
            call_label = "CALL Above" if style == "breakout_momentum" else "CALL Reclaim From"
            put_label = "PUT Below" if style == "breakout_momentum" else "PUT Rejection From"
            if option_side != "puts_only":
                parts.append(f"[{call_label}] {_entry_forecast_money(long_level)}")
            if option_side != "calls_only":
                parts.append(f"[{put_label}] {_entry_forecast_money(short_level)}")
            parts.extend([
                f"[Style] {style_label}",
                f"[ATR] x{mult:.1f}",
                reason,
            ])
        else:
            parts.extend([
                f"[{label}] {_entry_forecast_money(long_level)}",
                f"[Style] {style_label}",
                f"[ATR] x{mult:.1f}",
                reason,
            ])
        return " | ".join(parts)

    if mode in ("percentage", "hybrid_pct"):
        pct = _entry_forecast_float(settings.get("entry_pct_move", max(1.0, _entry_forecast_float(settings.get("regular_stop_loss_pct"), 0.05) * 100.0)), 1.0)
        style = settings.get("entry_pct_style", "dip_reclaim")
        prev_close = current_price
        candles = ctx.get("candles") or []
        if len(candles) >= 2:
            prev_close = _entry_forecast_float(candles[-2].get("close"), current_price)
        long_level = prev_close * (1.0 + (pct / 100.0)) if style == "breakout_momentum" else prev_close * (1.0 - (pct / 100.0))
        short_level = prev_close * (1.0 - (pct / 100.0)) if style == "breakout_momentum" else prev_close * (1.0 + (pct / 100.0))
        label = "PCT Buy Above" if style == "breakout_momentum" else "PCT Buy Below"
        style_label = "Breakout / Momentum" if style == "breakout_momentum" else ("Reversal / Reclaim" if is_option else "Dip / Reclaim")
        if mode == "hybrid_pct":
            parts.append("[Hybrid] Percentage setup or indicator buy threshold")
        if is_option:
            option_side = str(options_type or "both").lower()
            call_label = "CALL Above" if style == "breakout_momentum" else "CALL Reclaim From"
            put_label = "PUT Below" if style == "breakout_momentum" else "PUT Rejection From"
            if option_side != "puts_only":
                parts.append(f"[{call_label}] {_entry_forecast_money(long_level)}")
            if option_side != "calls_only":
                parts.append(f"[{put_label}] {_entry_forecast_money(short_level)}")
            parts.extend([
                f"[Style] {style_label}",
                f"[Move] {pct:.1f}%",
                reason,
            ])
        else:
            parts.extend([
                f"[{label}] {_entry_forecast_money(long_level)}",
                f"[Style] {style_label}",
                f"[Move] {pct:.1f}%",
                reason,
            ])
        return " | ".join(parts)

    if mode in ("pivot_points", "hybrid_pivot"):
        prior = _entry_card_prior_day(scan_data, entry_signal)
        if mode == "hybrid_pivot":
            parts.append("[Hybrid] Pivot setup or indicator buy threshold")
        if prior:
            high = _entry_forecast_float(prior.get("high"))
            low = _entry_forecast_float(prior.get("low"))
            close = _entry_forecast_float(prior.get("close"))
            pivot = (high + low + close) / 3.0
            r1 = (2 * pivot) - low
            s1 = (2 * pivot) - high
            parts.extend([
                f"[Pivot] {_entry_forecast_money(pivot)}",
                f"[Buy Support S1] {_entry_forecast_money(s1)}",
                f"[Breakout R1] {_entry_forecast_money(r1)}",
                reason,
            ])
        else:
            parts.extend(["[Pivot Entry] Awaiting prior day data", reason])
        return " | ".join(parts)

    if mode in ("high_low", "hybrid_high_low"):
        prior = _entry_card_prior_day(scan_data, entry_signal)
        if mode == "hybrid_high_low":
            parts.append("[Hybrid] High/Low setup or indicator buy threshold")
        if prior:
            parts.extend([
                f"[Buy Breakout] Prior High {_entry_forecast_money(prior.get('high'))}",
                f"[Buy Breakdown Guard] Prior Low {_entry_forecast_money(prior.get('low'))}",
                reason,
            ])
        else:
            parts.extend(["[High/Low Entry] Awaiting prior day data", reason])
        return " | ".join(parts)

    if mode in ("swing_points", "hybrid_swing"):
        support = ctx.get("nearest_support")
        resistance = ctx.get("nearest_resistance")
        entry_mode = settings.get("swing_entry_mode", "bounce_retest").replace("_", " ").title()
        lookback = settings.get("swing_entry_lookback_days", settings.get("swing_lookback_days", 20))
        if mode == "hybrid_swing":
            parts.append("[Hybrid] Swing setup or indicator buy threshold")
        parts.append(f"[Swing Entry] {entry_mode} | {lookback}D")
        parts.append(f"[Support] {_entry_forecast_money(support.get('price'))}" if support else "[Support] N/A")
        parts.append(f"[Resistance] {_entry_forecast_money(resistance.get('price'))}" if resistance else "[Resistance] N/A")
        parts.append(reason)
        return " | ".join(parts)

    return reason


def _build_entry_forecast(settings, scan_data, is_option=False, options_type="both", eng=None, for_card=False):
    if not scan_data:
        return ""

    price_history = scan_data.get("price_history", [])
    current_price = scan_data.get("price_raw") or scan_data.get("price") or 0.0
    try:
        current_price = float(str(current_price).replace("$", "").replace(",", ""))
    except Exception:
        current_price = 0.0

    buy_mode = settings.get("buy_mode", "indicator")
    if buy_mode == "indicator":
        indicator_forecast = _build_indicator_forecast(
            settings,
            scan_data,
            mode="entry",
            is_long=True,
            is_option=is_option,
            options_type=options_type,
            eng=eng,
        )
        if for_card:
            parts = [f"[Entry Strategy] {_entry_strategy_label(buy_mode)}"]
            if is_option:
                parts.append(f"[Options] {str(options_type or 'both').upper()}")
            if indicator_forecast:
                parts.append(indicator_forecast)
            return " | ".join(parts)
        return indicator_forecast

    lookback_days = int(
        settings.get(
            "swing_entry_lookback_days",
            settings.get("swing_lookback_days", 20),
        ) or 20
    )
    market_session = settings.get("market_session", "Regular")
    is_crypto = any(str(scan_data.get("ticker", "")).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or str(scan_data.get("ticker", "")).upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
    entry_signal = build_entry_signal(
        price_history,
        current_price=current_price,
        entry_mode=buy_mode,
        lookback_days=lookback_days,
        market_session=market_session,
        is_crypto=is_crypto,
        atr=scan_data.get("atr", 0.0),
        custom_buy_price=settings.get("custom_buy_price", 0.0),
        entry_atr_mult=float(settings.get("entry_atr_multiplier", settings.get("atr_stop_multiplier", 2.0)) or 2.0),
        entry_pct_move=float(settings.get("entry_pct_move", max(1.0, float(settings.get("regular_stop_loss_pct", 0.05)) * 100.0)) or 1.0),
        entry_atr_style=settings.get("entry_atr_style", "pullback_reclaim"),
        entry_pct_style=settings.get("entry_pct_style", "dip_reclaim"),
        swing_entry_mode=settings.get("swing_entry_mode", "bounce_retest"),
    )
    if for_card:
        return _format_entry_card_forecast(
            settings,
            scan_data,
            entry_signal,
            current_price,
            buy_mode,
            is_option=is_option,
            options_type=options_type,
        )

    return format_entry_preview(
        symbol=scan_data.get("ticker", "Example"),
        entry_signal=entry_signal,
        current_price=current_price,
        entry_mode=buy_mode,
        is_option=is_option,
    )

@app.get("/api/dashboard")
async def get_dashboard(request: Request, mode: str = "fast", ticker: str = None, timeframe: str = None, source: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """
    Main dashboard endpoint — returns everything the UI needs in one call.
    """
    from user_config import active_evaluation_context
    ctx = source if source in ["dashboard", "bots", "backtest"] else "dashboard"
    active_evaluation_context.set(ctx)
    overall_start = time.perf_counter()
    account_start = time.perf_counter()

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
    
    # Get general account info and broker data concurrently
    import asyncio
    try:
        account, positions, orders, profit_data, orders_history = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(eng.broker.get_account_info),
                asyncio.to_thread(eng.broker.get_positions),
                asyncio.to_thread(eng.broker.get_open_orders),
                asyncio.to_thread(eng.broker.get_all_time_profit),
                asyncio.to_thread(eng.broker.get_order_history),
                return_exceptions=False
            ),
            timeout=3.0
        )
        total_profit, total_profit_pct = profit_data
    except asyncio.TimeoutError:
        print("[dashboard] Broker API timeout; using cached/fallback account data")
        account = {'equity': 0, 'cash': 0, 'buying_power': 0, 'simulation': True, 'daily_pl': 0, 'daily_pl_pct': 0}
        positions, orders, orders_history = [], [], []
        total_profit, total_profit_pct = 0.0, 0.0
    except Exception as e:
        print(f"[dashboard] Broker API unavailable: {type(e).__name__}: {e or repr(e)}")
        account = {'equity': 0, 'cash': 0, 'buying_power': 0, 'simulation': True, 'daily_pl': 0, 'daily_pl_pct': 0}
        positions, orders, orders_history = [], [], []
        total_profit, total_profit_pct = 0.0, 0.0
    
    account_ms = (time.perf_counter() - account_start) * 1000
    
    # Active bot status check
    is_active_bot = primary_ticker in eng.config.TRADELIST
    
    # Check cache first for primary ticker scan
    primary_scan = None
    prefer_bot_for_primary = is_active_bot and ctx == "bots"
    if prefer_bot_for_primary:
        bot_tf = eng.config.TICKER_SETTINGS.get(primary_ticker, {}).get('timeframe')
        if bot_tf:
            timeframe = bot_tf
    
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

    await _repair_bot_signal_thresholds_if_needed(eng)

    # Format the primary scan cleanly
    formatted_primary = _format_scan_for_ui(primary_scan, use_global_settings=(not prefer_bot_for_primary))
    
    # Format daily P/L (Today Only)
    daily_pl = account.get('daily_pl', 0.0)
    daily_pl_pct = account.get('daily_pl_pct', 0.0)
    daily_pl_sign = "+" if daily_pl >= 0 else ""

    # Calculate All-Time Profit (Realized + Unrealized) directly from the broker history
    total_pl_sign = "+" if total_profit >= 0 else ""

    # Format trade log (scans/decisions)
    ui_trade_log = [_format_trade_for_ui(trade) for trade in eng.trade_log]
    order_history_ms = 0  # fetched concurrently above

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

    # Fetch live Webull prices, caching inactive watchlist tickers for 60 seconds to save API calls
    fast_tickers = list(dict.fromkeys(eng.config.TRADELIST + [primary_ticker]))
    slow_tickers = [t for t in eng.config.WATCHLIST if t not in fast_tickers]
    
    quote_start = time.perf_counter()
    stale_fast_prices = _get_stale_latest_prices(eng, fast_tickers, 300)
    stale_slow_prices = _get_stale_latest_prices(eng, slow_tickers, 900)
    quote_timeout = 1.25 if mode == "fast" else 3.0
    try:
        fast_prices, slow_prices = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_get_cached_latest_prices, eng, fast_tickers, 3, 300),
                asyncio.to_thread(_get_cached_latest_prices, eng, slow_tickers, 60, 900)
            ),
            timeout=quote_timeout
        )
    except asyncio.TimeoutError:
        print(f"[dashboard] Quote API timeout after {quote_timeout:.2f}s; using cached prices")
        fast_prices, slow_prices = stale_fast_prices, stale_slow_prices
    except Exception as e:
        print(f"[dashboard] Quote API error: {type(e).__name__}: {e!r}")
        fast_prices, slow_prices = stale_fast_prices, stale_slow_prices
    latest_prices = {**slow_prices, **fast_prices}
    quote_ms = (time.perf_counter() - quote_start) * 1000

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

    # --- Inject exit_forecast into positions ---
    from option_selector import parse_occ_symbol
    import re
    for p in positions:
        try:
            symbol = p.get('symbol', '')
            asset_class = p.get('asset_class', '')
            qty = float(p.get('qty', 0))
            if qty == 0:
                p['exit_forecast'] = ""
                continue
                
            # Parse OCC options:
            parsed_occ = parse_occ_symbol(symbol)
            is_option = (parsed_occ is not None) or p.get('is_option', False) or (asset_class == 'option') or (len(symbol) >= 15)
            is_crypto_position = any(str(symbol).upper().replace("/", "").endswith(base) for base in ["USD", "USDT", "USDC"])
            p['exit_protection'] = _format_exit_protection_status(eng, symbol, is_option, is_crypto_position)
            
            if parsed_occ:
                underlying = parsed_occ['underlying']
            else:
                m = re.match(r"^([A-Z]+)", symbol)
                underlying = m.group(1) if m else symbol
            
            active_ctx = eng.config.contexts.get(active_evaluation_context.get(), {})
            strategy_overrides = active_ctx.get("parameters", {})
            toggle_overrides = active_ctx.get("toggles", {})
            
            settings = eng.config.TICKER_SETTINGS.get(underlying, {})
            if is_option:
                strat = settings.get('sell_mode') or settings.get('options_exit_strategy') or toggle_overrides.get('options_exit_strategy') or eng.config.active_toggles.get('options_exit_strategy', 'hybrid')
            else:
                strat = settings.get('sell_mode') or toggle_overrides.get('exit_strategy') or eng.config.active_toggles.get('exit_strategy', 'hybrid')
                
            underlying_tf = settings.get('timeframe') or eng.config.DEFAULT_TIMEFRAME
            underlying_scan = eng._pick_scan(underlying, underlying_tf, prefer_bot=True, ignore_freshness=True)
            if not underlying_scan:
                underlying_scan = eng.bot_scans.get(underlying) or eng.latest_scans.get(underlying, {})
                
            avg_price = float(p.get('avg_price') or p.get('avg_entry_price') or 0)
            current_price = float(p.get('current_price', 0))
            
            targets = []
            exit_strategy_prefix = f"[Exit Strategy] {_exit_strategy_label(strat)}"
            
            # --- STOCK / CRYPTO ---
            if not is_option:
                is_long = qty > 0
                atr = underlying_scan.get('atr', 0)
                
                if strat in ('indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'):
                    buy_default = getattr(eng.config, 'MIN_BULLISH_SIGNALS', getattr(get_user_config(), 'MIN_BULLISH_SIGNALS', 1))
                    sell_default = getattr(eng.config, 'MIN_BEARISH_SIGNALS', getattr(get_user_config(), 'MIN_BEARISH_SIGNALS', 1))
                    sell_threshold = _get_threshold_setting(settings, ('min_sell_signals', 'sell_threshold'), sell_default)
                    buy_threshold = _get_threshold_setting(settings, ('min_buy_signals', 'threshold', 'buy_threshold'), buy_default)
                    desc_dynamic = _build_indicator_forecast(settings, underlying_scan, mode="exit", is_long=is_long, is_option=is_option, options_type=settings.get("options_type", "both"), eng=eng)
                    if not desc_dynamic:
                        desc_dynamic = f"≥ {sell_threshold} BEARISH Signals" if is_long else f"≥ {buy_threshold} BULLISH Signals"
                    targets.append({"label": "Indicator", "val": float('-inf') if is_long else float('inf'), "type": "cond", "desc": desc_dynamic})

                if strat in ('swing_points', 'hybrid_swing'):
                    swing_desc = _build_swing_forecast(settings, underlying_scan, mode="exit", is_long=(is_long if not is_option else is_call), is_option=is_option, options_type=settings.get("options_type", "both"), eng=eng)
                    swing_exit = build_swing_exit(
                        underlying_scan.get('price_history', []),
                        current_price=underlying_scan.get('price_raw', 0.0),
                        is_long=(is_long if not is_option else is_call),
                        lookback_days=int(settings.get('swing_exit_lookback_days', settings.get('swing_lookback_days', 20)) or 20),
                        market_session=settings.get('market_session', 'Regular'),
                        is_crypto=any(str(underlying).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or str(underlying).upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"],
                    )
                    if swing_exit.get("take_profit") is not None and swing_exit.get("stop_loss") is not None:
                        targets.append({"label": "Swing TP", "val": swing_exit["take_profit"], "type": "price", "desc": swing_desc})
                        targets.append({"label": "Swing SL", "val": swing_exit["stop_loss"], "type": "price", "desc": swing_desc})

                if strat == 'custom_sltp':
                    custom_sl = _entry_forecast_float(settings.get('custom_stop_loss_price'), 0.0)
                    custom_tp = _entry_forecast_float(settings.get('custom_take_profit_price'), 0.0)
                    if custom_sl > 0:
                        targets.append({"label": "Custom SL", "val": custom_sl, "type": "price"})
                    if custom_tp > 0:
                        targets.append({"label": "Custom TP", "val": custom_tp, "type": "price"})

                if strat in ('sltp', 'hybrid') and atr > 0 and avg_price > 0:
                    sl_mult = settings.get('atr_stop_multiplier', getattr(eng.config, 'ATR_STOP_MULTIPLIER', 2.0))
                    tp_mult = settings.get('take_profit_multiplier', getattr(eng.config, 'ATR_TAKE_PROFIT_MULTIPLIER', 4.0))
                    
                    sl_price = avg_price - (atr * sl_mult) if is_long else avg_price + (atr * sl_mult)
                    trailing = eng.last_trailing_stops.get(symbol)
                    if isinstance(trailing, dict):
                        trailing = trailing.get("trailing_stop_price")
                    if settings.get('enable_atr_trail', True) and isinstance(trailing, (int, float)):
                        sl_price = trailing
                        
                    tp_price = avg_price + (atr * tp_mult) if is_long else avg_price - (atr * tp_mult)
                    
                    targets.append({"label": "ATR SL", "val": sl_price, "type": "price"})
                    targets.append({"label": "ATR TP", "val": tp_price, "type": "price"})
                    
                if strat in ('percentage', 'hybrid_pct') and avg_price > 0:
                    sl_pct = settings.get('regular_stop_loss_pct', 0.05)
                    tp_pct = settings.get('regular_take_profit_pct', 0.10)
                    sl_price = avg_price * (1 - sl_pct) if is_long else avg_price * (1 + sl_pct)
                    
                    trailing = eng.last_trailing_stops.get(symbol)
                    if isinstance(trailing, dict):
                        trailing = trailing.get("trailing_stop_price")
                    if settings.get('enable_pct_trail', True) and isinstance(trailing, (int, float)):
                        sl_price = trailing
                        
                    tp_price = avg_price * (1 + tp_pct) if is_long else avg_price * (1 - tp_pct)
                    
                    targets.append({"label": "Pct SL", "val": sl_price, "type": "price"})
                    targets.append({"label": "Pct TP", "val": tp_price, "type": "price"})
                    
                if strat in ('pivot_points', 'hybrid_pivot'):
                    last_day = underlying_scan.get('prior_day_ohlc')
                    if not last_day:
                        hist = underlying_scan.get('price_history', [])
                        last_day = hist[-1] if len(hist) > 0 else {}
                    if last_day:
                        h = last_day.get('high')
                        l = last_day.get('low')
                        c = last_day.get('close')
                        if h is not None and l is not None and c is not None:
                            pivot_levels = _dynamic_pivot_exit_levels(h, l, c, current_price)
                            if pivot_levels:
                                targets.append({"label": "Pivot TP", "val": pivot_levels["tp"], "type": "price"})
                                targets.append({"label": "Pivot SL", "val": pivot_levels["sl"], "type": "price"})

                if strat in ('high_low', 'hybrid_high_low'):
                    last_day = underlying_scan.get('prior_day_ohlc')
                    if not last_day:
                        hist = underlying_scan.get('price_history', [])
                        last_day = hist[-1] if len(hist) > 0 else {}
                    if last_day:
                        dh = last_day.get('high')
                        dl = last_day.get('low')
                        if dh is not None and dl is not None:
                            hl_levels = _dynamic_high_low_exit_levels(dh, dl, current_price)
                            if hl_levels:
                                targets.append({"label": "H/L TP", "val": hl_levels["tp"], "type": "price"})
                                targets.append({"label": "H/L SL", "val": hl_levels["sl"], "type": "price"})
            
            # --- OPTIONS ---
            else:
                op_type = parsed_occ.get('type', 'CALL') if parsed_occ else 'CALL'
                is_call = op_type == 'CALL'
                
                if strat in ('premium_pct', 'percentage', 'hybrid_pct') and avg_price > 0:
                    sl_pct, tp_pct, trail_pct = _option_pct_exit_settings(settings)
                    sl_price = avg_price * (1.0 - sl_pct)
                    trailing = eng.last_trailing_stops.get(symbol)
                    if settings.get('enable_pct_trail', True) and isinstance(trailing, (int, float)):
                        sl_price = trailing
                    tp_price = avg_price * (1.0 + tp_pct)
                    targets.append({"label": f"Option Pct SL ({sl_pct*100:.1f}% / Trail {trail_pct*100:.1f}%)", "val": sl_price, "type": "price"})
                    targets.append({"label": f"Option Pct TP ({tp_pct*100:.1f}%)", "val": tp_price, "type": "price"})

                if strat in ('sltp', 'hybrid'):
                    atr = _entry_forecast_float(underlying_scan.get('atr'), 0.0)
                    entry_underlying = _entry_forecast_float(settings.get('options_underlying_entry_price'), underlying_scan.get('price_raw', 0.0))
                    sl_mult = _entry_forecast_float(settings.get('atr_stop_multiplier', getattr(eng.config, 'ATR_STOP_MULTIPLIER', 2.0)), 2.0)
                    tp_mult = _entry_forecast_float(settings.get('take_profit_multiplier', getattr(eng.config, 'ATR_TAKE_PROFIT_MULTIPLIER', 4.0)), 4.0)
                    if atr > 0 and entry_underlying > 0:
                        if is_call:
                            targets.append({"label": f"ATR TP ({tp_mult:.1f}x)", "val": entry_underlying + (atr * tp_mult), "type": "underlying_price"})
                            targets.append({"label": f"ATR SL ({sl_mult:.1f}x)", "val": entry_underlying - (atr * sl_mult), "type": "underlying_price"})
                        else:
                            targets.append({"label": f"ATR TP ({tp_mult:.1f}x)", "val": entry_underlying - (atr * tp_mult), "type": "underlying_price"})
                            targets.append({"label": f"ATR SL ({sl_mult:.1f}x)", "val": entry_underlying + (atr * sl_mult), "type": "underlying_price"})

                if strat == 'custom_sltp':
                    custom_sl = _entry_forecast_float(settings.get('custom_stop_loss_price'), 0.0)
                    custom_tp = _entry_forecast_float(settings.get('custom_take_profit_price'), 0.0)
                    if custom_sl > 0:
                        targets.append({"label": "Custom SL", "val": custom_sl, "type": "price"})
                    if custom_tp > 0:
                        targets.append({"label": "Custom TP", "val": custom_tp, "type": "price"})

                if strat == 'custom_underlying_sltp':
                    custom_u_sl = _entry_forecast_float(settings.get('custom_underlying_stop_loss_price'), 0.0)
                    custom_u_tp = _entry_forecast_float(settings.get('custom_underlying_take_profit_price'), 0.0)
                    if custom_u_sl > 0:
                        targets.append({"label": "Custom Underlying SL", "val": custom_u_sl, "type": "underlying_price"})
                    if custom_u_tp > 0:
                        targets.append({"label": "Custom Underlying TP", "val": custom_u_tp, "type": "underlying_price"})
                    
                if strat in ('pivot_points', 'hybrid_pivot'):
                    last_day = underlying_scan.get('prior_day_ohlc')
                    if not last_day:
                        hist = underlying_scan.get('price_history', [])
                        last_day = hist[-1] if len(hist) > 0 else {}
                    if last_day:
                        h = last_day.get('high')
                        l = last_day.get('low')
                        c = last_day.get('close')
                        if h is not None and l is not None and c is not None:
                            p_val = (h + l + c) / 3
                            r1 = (2 * p_val) - l
                            s1 = (2 * p_val) - h
                            
                            target_price = r1 if is_call else s1
                            sl_target = s1 if is_call else r1
                            targets.append({"label": "Pivot TP", "val": target_price, "type": "underlying_price"})
                            targets.append({"label": "Pivot SL", "val": sl_target, "type": "underlying_price"})
                            
                if strat in ('high_low', 'hybrid_high_low'):
                    last_day = underlying_scan.get('prior_day_ohlc')
                    if not last_day:
                        hist = underlying_scan.get('price_history', [])
                        last_day = hist[-1] if len(hist) > 0 else {}
                    if last_day:
                        dh = last_day.get('high')
                        dl = last_day.get('low')
                        if dh is not None and dl is not None:
                            target_price = dh if is_call else dl
                            sl_target = dl if is_call else dh
                            targets.append({"label": "H/L TP", "val": target_price, "type": "underlying_price"})
                            targets.append({"label": "H/L SL", "val": sl_target, "type": "underlying_price"})

                if strat in ('indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'):
                    buy_default = getattr(eng.config, 'MIN_BULLISH_SIGNALS', getattr(get_user_config(), 'MIN_BULLISH_SIGNALS', 1))
                    sell_default = getattr(eng.config, 'MIN_BEARISH_SIGNALS', getattr(get_user_config(), 'MIN_BEARISH_SIGNALS', 1))
                    sell_threshold = _get_threshold_setting(settings, ('min_sell_signals', 'sell_threshold'), sell_default)
                    buy_threshold = _get_threshold_setting(settings, ('min_buy_signals', 'threshold', 'buy_threshold'), buy_default)
                    desc = f"≥ {sell_threshold} BEARISH Signals" if is_call else f"≥ {buy_threshold} BULLISH Signals"
                    targets.append({"label": "Indicator", "val": float('-inf') if is_call else float('inf'), "type": "cond", "desc": desc})

                if strat in ('swing_points', 'hybrid_swing'):
                    swing_desc = _build_swing_forecast(settings, underlying_scan, mode="exit", is_long=is_call, is_option=is_option, options_type=settings.get("options_type", "both"), eng=eng)
                    swing_exit = build_swing_exit(
                        underlying_scan.get('price_history', []),
                        current_price=underlying_scan.get('price_raw', 0.0),
                        is_long=is_call,
                        lookback_days=int(settings.get('swing_exit_lookback_days', settings.get('swing_lookback_days', 20)) or 20),
                        market_session=settings.get('market_session', 'Regular'),
                        is_crypto=any(str(underlying).upper().endswith(base) for base in ["USD", "USDT", "USDC"]) or str(underlying).upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"],
                    )
                    if swing_exit.get("take_profit") is not None and swing_exit.get("stop_loss") is not None:
                        targets.append({"label": "Swing TP", "val": swing_exit["take_profit"], "type": "underlying_price", "desc": swing_desc})
                        targets.append({"label": "Swing SL", "val": swing_exit["stop_loss"], "type": "underlying_price", "desc": swing_desc})
            
            if not targets:
                p['exit_forecast'] = f"{exit_strategy_prefix} | No targets found"
                continue
                
            min_pct_dist = float('inf')
            closest_target = None
            
            for t in targets:
                t['dist_pct'] = float('inf')
                if t['type'] == 'price' and current_price > 0:
                    pct = abs(current_price - t['val']) / current_price
                    t['dist_pct'] = pct
                    if pct < min_pct_dist:
                        min_pct_dist = pct
                        closest_target = t
                elif t['type'] == 'underlying_price':
                    live_u = _lookup_quote_price(latest_prices, underlying) or underlying_scan.get('price_raw', 0)
                    if live_u > 0:
                        pct = abs(live_u - t['val']) / live_u
                        t['dist_pct'] = pct
                        if pct < min_pct_dist:
                            min_pct_dist = pct
                            closest_target = t

            # Sort targets: closest first, conditions/tech at the end
            targets.sort(key=lambda x: x.get('dist_pct', float('inf')))

            def fmt_val(v):
                if not ("USD" in str(symbol).upper() or "BTC" in str(symbol).upper()):
                    return f"{v:.2f}"
                s = f"{v:.8f}".rstrip('0')
                if s.endswith('.'): s += '00'
                elif len(s.split('.')[1]) == 1: s += '0'
                return s

            final_strs = [exit_strategy_prefix]
            for t in targets:
                is_closest = (t == closest_target and strat == 'hybrid' and closest_target is not None)
                prefix = "★ " if is_closest else ""
                suffix = " ★" if is_closest else ""
                
                if t['type'] == 'cond':
                    final_strs.append(f"{prefix}[{t['label']}] {t['desc']}{suffix}")
                elif t['type'] == 'price':
                    final_strs.append(f"{prefix}[{t['label']}] ${fmt_val(t['val'])}{suffix}")
                elif t['type'] == 'underlying_price':
                    final_strs.append(f"{prefix}[U-{t['label']}] ${fmt_val(t['val'])}{suffix}")

            p['exit_forecast'] = " | ".join(final_strs)

            if is_crypto_position:
                tp_target = next((t for t in targets if t.get('type') == 'price' and 'TP' in str(t.get('label', '')).upper()), None)
                sl_target = next((t for t in targets if t.get('type') == 'price' and 'SL' in str(t.get('label', '')).upper()), None)
                if tp_target and sl_target:
                    p['exit_protection'] = {
                        "label": "Exit Protection",
                        "status": f"Watcher active | TP ${fmt_val(tp_target['val'])} | SL ${fmt_val(sl_target['val'])}",
                        "active": True,
                        "take_profit": float(tp_target['val']),
                        "stop_loss": float(sl_target['val']),
                        "protection_type": "watcher",
                    }
            
        except Exception as e:
            import traceback
            print(f"[dashboard] Error forecasting exit for {p.get('symbol')}: {e}")
            traceback.print_exc()
            p['exit_forecast'] = "Forecast Error"

    # Build the final payload
    bot_scans_dict = {}
    for t in eng.config.TRADELIST:
        scan = eng._pick_scan(t, timeframe, prefer_bot=True) or eng._pick_scan(t, eng.config.TICKER_SETTINGS.get(t, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME), prefer_bot=True, ignore_freshness=True) or {}
        ui_scan = _inject_price(_format_scan_for_ui(scan, use_global_settings=False), t)
        t_settings = eng.config.TICKER_SETTINGS.get(t, {})
        is_opt = t_settings.get('options_trading', t_settings.get('is_option', False))
        ui_scan['entry_forecast'] = _build_entry_forecast(
            t_settings,
            scan,
            is_option=is_opt,
            options_type=t_settings.get("options_type", "both"),
            eng=eng,
            for_card=True,
        )
        bot_scans_dict[t] = ui_scan
        
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
        "botScans": bot_scans_dict,

        # Strategy Signals (primary ticker)
        "primaryTicker": primary_ticker,
        "primaryScan": formatted_primary,
        "signals": formatted_primary.get('signals', {}),
        "priceHistory": [],

        # Risk Management
        "risk": risk_mgr.get_risk_status(account['equity']),
        "ticker_settings": eng.config.TICKER_SETTINGS,

        # Bot Meta
        "botRunning": eng.bot_running,
        "brokerConnected": not account.get('simulation', True),
        "has_keys": bool(eng.config.ALPACA_API_KEY),
        "lastScan": last_scan_time or "Starting...",
        "indicator_settings": eng.config.active_toggles,
        "indicator_parameters": eng.config.active_parameters,
        "indicator_overrides": eng.config.contexts.get(ctx, {}).get("parameters", {}),
        "indicator_toggle_overrides": eng.config.contexts.get(ctx, {}).get("toggles", {}),
        "strategyTimeframe": timeframe,
        "watchlist": eng.config.WATCHLIST,
        "tradelist": eng.config.TRADELIST,

        # Performance Timings
        "performance": {
            "total_ms": (time.perf_counter() - overall_start) * 1000,
            "account_ms": account_ms,
            "quote_ms": quote_ms,
            "order_history_ms": order_history_ms,
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

    # Dashboard heartbeats should not carry chart candles. The active chart owns
    # candles through /api/chart so heavy refreshes can update signals without
    # forcing a full chart redraw or shipping large payloads.
    if "price_history" in payload["primaryScan"]:
        del payload["primaryScan"]["price_history"]
    for scan_map in (payload["watchlistScans"], payload["botScans"]):
        for v in scan_map.values():
            if "price_history" in v:
                del v["price_history"]

    if mode == "fast":
        payload["signals"] = {}
        payload["primaryScan"]["signals"] = {}
        for k, v in payload["watchlistScans"].items():
            v["signals"] = {}
        for k, v in payload["botScans"].items():
            v["signals"] = {}

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

    with _analysis_request_context(source, ticker, prefer_bot):
        analysis, cached, cache_age = await asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, eng, force, source)
        if not analysis:
            raise HTTPException(status_code=404, detail=f"Could not load chart data for {ticker}")

        scan = _technical_scan_from_analysis(ticker, timeframe, analysis, use_bot_settings=prefer_bot)
        formatted_scan = _format_scan_for_ui(scan, use_global_settings=(not prefer_bot))
        settings = eng.config.TICKER_SETTINGS.get(ticker, {}) if prefer_bot else {}
        is_opt = settings.get('options_trading', False)
        formatted_scan["entry_forecast"] = _build_entry_forecast(
            settings,
            scan,
            is_option=is_opt,
            options_type=settings.get("options_type", "both"),
            eng=eng,
        ) if settings else ""
        formatted_scan["swing_entry_forecast"] = _build_swing_forecast(
            settings,
            scan,
            mode="entry",
            is_long=True,
            is_option=is_opt,
            options_type=settings.get("options_type", "both"),
            eng=eng,
        ) if settings else ""
        formatted_scan["swing_exit_forecast"] = _build_swing_forecast(
            settings,
            scan,
            mode="exit",
            is_long=True,
            is_option=is_opt,
            options_type=settings.get("options_type", "both"),
            eng=eng,
        ) if settings else ""
    
    # --- TEMPORARY LOGGING FOR DEBUGGING ---
    print(f"[perf] /api/chart {ticker} {timeframe}: {(time.perf_counter() - started) * 1000:.1f}ms cached={cached}")
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "cached": cached,
        "cache_age_seconds": round(cache_age or 0.0, 1),
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
            "cache_age_seconds": round(cache_age or 0.0, 1),
            "cached": cached
        }
    }


@app.get("/api/ollama/models")
async def get_ollama_models(eng: UserEngine = Depends(get_user_engine)):
    """
    Queries the local Ollama instance for pulled models.
    Returns a list of model names or an empty list if offline.
    """
    import requests
    host = getattr(eng.config, "OLLAMA_HOST", "http://localhost:11434")
    try:
        response = await asyncio.to_thread(
            requests.get,
            f"{host.rstrip('/')}/api/tags",
            timeout=1.5
        )
        if response.status_code == 200:
            models_data = response.json().get("models", [])
            return {
                "status": "online",
                "models": [m["name"] for m in models_data]
            }
    except Exception as e:
        print(f"[api] Ollama not reachable at {host}: {e}")
    return {"status": "offline", "models": []}


@app.get("/api/ai-indicator")
async def get_ai_indicator_endpoint(
    ticker: str,
    timeframe: str = "1Hour",
    model: str = None,
    force: bool = False,
    eng: UserEngine = Depends(get_user_engine)
):
    """
    Agentic AI Indicator — Groq/Ollama-powered autonomous market research.
    Returns a BUY/SELL/HOLD signal with position sizing, leverage guidance,
    sell targets, and a detailed reasoning summary.
    """
    ticker = _normalize_quote_symbol(ticker)
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"error": "Invalid ticker format"}

    if not getattr(eng.config, "ENABLE_AI_INDICATOR", True):
        return {"error": "AI Indicator is disabled in settings", "signal": "HOLD", "confidence": 0}

    started = time.perf_counter()

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
            model,
        )
        result.setdefault("performance", {})
        result["performance"].update({
            "total_ms": (time.perf_counter() - started) * 1000,
        })
        return result
    except Exception as e:
        print(f"[api] AI indicator error for {ticker}: {e}")
        return {
            "error": str(e),
            "signal": "HOLD",
            "confidence": 0,
            "summary": "AI Indicator temporarily unavailable.",
            "cached": False,
            "performance": {
                "total_ms": (time.perf_counter() - started) * 1000,
            },
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
    with _analysis_request_context(source, ticker, prefer_bot):
        analysis, cached, cache_age = await asyncio.to_thread(_get_cached_chart_analysis, ticker, timeframe, eng, False, source)
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
        "cache_age_seconds": round(cache_age or 0.0, 1),
        "primaryScan": formatted_scan,
        "signals": formatted_scan.get("signals", {}),
        "action": formatted_scan.get("action", "HOLD"),
        "reason": formatted_scan.get("reason", ""),
        "bullish_count": formatted_scan.get("bullish_count", 0),
        "bearish_count": formatted_scan.get("bearish_count", 0),
        "total_signals": formatted_scan.get("total_signals", 0),
    }


@app.get("/api/scan/{ticker}")
def scan_ticker(ticker: str, timeframe: str = "1Hour", eng: UserEngine = Depends(get_user_engine)):
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


@app.post("/api/backtest/cancel")
async def cancel_backtest():
    """Signals the global cancellation event to abort running backtests."""
    global_backtest_cancel.set()
    return {"status": "cancelled", "message": "Backtest cancelled successfully"}

@app.post("/api/backtest")
async def run_backtest(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs a historical backtest for a ticker."""
    global_backtest_cancel.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    # Apply indicator overrides for this backtest run
    eng.config.contexts.get("backtest", {}).get("parameters", {}).clear()
    eng.config.contexts.get("backtest", {}).get("toggles", {}).clear()
    for k, v in data.get("indicator_overrides", {}).items():
        eng.config.set(k, v)

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)
    
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
    
    regular_stop_loss_pct = float(data.get("regular_stop_loss_pct", 0.05))
    regular_take_profit_pct = float(data.get("regular_take_profit_pct", 0.10))
    regular_trailing_pct = float(data.get("regular_trailing_pct", 0.03))
    entry_atr_multiplier = data.get("entry_atr_multiplier")
    if entry_atr_multiplier is None:
        entry_atr_multiplier = data.get("atr_stop_multiplier", 2.0)
    entry_pct_move = data.get("entry_pct_move")
    if entry_pct_move is None:
        entry_pct_move = max(1.0, regular_stop_loss_pct * 100.0)
    
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
        regular_stop_loss_pct=regular_stop_loss_pct,
        regular_take_profit_pct=regular_take_profit_pct,
        regular_trailing_pct=regular_trailing_pct,
        buy_mode=data.get("buy_mode", "indicator"),
        custom_buy_price=float(data.get("custom_buy_price", 0.0)),
        entry_atr_multiplier=float(entry_atr_multiplier),
        entry_pct_move=float(entry_pct_move),
        entry_atr_style=data.get("entry_atr_style", "pullback_reclaim"),
        entry_pct_style=data.get("entry_pct_style", "dip_reclaim"),
        swing_entry_mode=data.get("swing_entry_mode", "bounce_retest"),
        swing_entry_lookback_days=int(data.get("swing_entry_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        market_session=data.get("market_session", "Regular"),
        ai_autopilot=ai_autopilot,
        ai_interval=ai_interval,
        ai_model=ai_model,
        enable_cooldown=bool(data.get("enable_cooldown", True)),
        cooldown_minutes=int(data["cooldown_minutes"]) if data.get("cooldown_minutes") is not None else None,
        cancel_event=global_backtest_cancel
    )
    
    try:
        results = await asyncio.to_thread(bt.run)
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        print(f"[api] Backtest crash: {e}")
        return {"status": "error", "message": "An internal error occurred during backtest simulation."}



@app.post("/api/backtest/optimize")
async def run_optimization_endpoint(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs an Auto-Optimization grid search with In-Sample/Out-of-Sample splitting."""
    global_backtest_cancel.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    # Apply indicator overrides for this backtest run
    eng.config.contexts.get("backtest", {}).get("parameters", {}).clear()
    eng.config.contexts.get("backtest", {}).get("toggles", {}).clear()
    for k, v in data.get("indicator_overrides", {}).items():
        eng.config.set(k, v)

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)
    
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
        
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 90))
    capital = float(data.get("capital", 1000.0))
    split_pct = float(data.get("oos_split_pct", 30.0))
    
    end_date = get_now()
    start_date = end_date - timedelta(days=days)
    
    # Base kwargs for the backtester
    base_kwargs = {
        "ticker": ticker,
        "timeframe": timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": capital,
        "enabled_indicators": data.get("indicators", []),
        "risk_per_trade": float(data.get("risk_per_trade", 0.02)),
        "max_pos_pct": float(data.get("max_position_pct", 0.25)),
        "ext_hours": data.get("ext_hours", True),
        "sell_mode": data.get("sell_mode", "indicator"),
        "ai_autopilot": False, # Disable AI autopilot during massive grid search to save Groq limits
        "enable_cooldown": bool(data.get("enable_cooldown", True)),
        "cooldown_minutes": int(data["cooldown_minutes"]) if data.get("cooldown_minutes") is not None else None,
        "buy_mode": data.get("buy_mode", "indicator"),
        "custom_buy_price": float(data.get("custom_buy_price", 0.0)),
        "entry_atr_multiplier": float(data.get("entry_atr_multiplier", data.get("atr_stop_multiplier", 2.0))),
        "entry_pct_move": float(data.get("entry_pct_move", max(1.0, float(data.get("regular_stop_loss_pct", 0.05)) * 100.0))),
        "entry_atr_style": data.get("entry_atr_style", "pullback_reclaim"),
        "entry_pct_style": data.get("entry_pct_style", "dip_reclaim"),
        "swing_entry_mode": data.get("swing_entry_mode", "bounce_retest"),
        "swing_entry_lookback_days": int(data.get("swing_entry_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        "market_session": data.get("market_session", "Regular"),
        "regular_stop_loss_pct": float(data.get("regular_stop_loss_pct", 0.05)),
        "regular_take_profit_pct": float(data.get("regular_take_profit_pct", 0.10)),
        "regular_trailing_pct": float(data.get("regular_trailing_pct", 0.03)),
    }
    
    from optimizer import run_optimization
    try:
        results = await run_optimization(base_kwargs, split_pct)
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        import traceback
        print(f"[api] Auto-Optimize crash: {e}\n{traceback.format_exc()}")
        return {"status": "error", "message": "Optimization engine crashed."}

@app.post("/api/options/backtest")
async def run_options_backtest(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Runs a historical options backtest."""
    global_backtest_cancel.clear()
    from user_config import active_evaluation_context, active_ticker_context
    active_evaluation_context.set("backtest")
    # Apply indicator overrides for this backtest run
    eng.config.contexts.get("backtest", {}).get("parameters", {}).clear()
    eng.config.contexts.get("backtest", {}).get("toggles", {}).clear()
    for k, v in data.get("indicator_overrides", {}).items():
        eng.config.set(k, v)

    ticker = data.get("ticker", "AAPL").upper()
    active_ticker_context.set(ticker)
    
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
        
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 30))
    capital = float(data.get("capital", 1000.0))
    trade_amount = float(data.get("trade_amount", 100.0))
    expiry_days = int(data.get("expiry_days", 7))
    strike_type = data.get("strike_type", "ATM")
    options_type = data.get("options_type", "both")
    stop_loss_pct = float(data.get("stop_loss_pct", 0.50))
    take_profit_pct = float(data.get("take_profit_pct", 1.00))
    exit_strategy = data.get("exit_strategy", "hybrid")
    close_before_expiry_hours = float(data.get("close_before_expiry_hours", 2.0))
    threshold = int(data.get("threshold", 5))
    sell_threshold = int(data.get("sell_threshold", 3))
    indicators = data.get("indicators", [])
    ext_hours = data.get("ext_hours", True)
    r = float(data.get("r", 0.05))

    enable_signal_reset = bool(data.get("enable_signal_reset", False))
    enable_smart_reentry = bool(data.get("enable_smart_reentry", False))
    smart_reentry_type = data.get("smart_reentry_type", "RSI")
    smart_reentry_rsi_call_max = int(data.get("smart_reentry_rsi_call_max", 70))
    smart_reentry_rsi_put_min = int(data.get("smart_reentry_rsi_put_min", 30))
    smart_reentry_vwap_call_max_dist_pct = float(data.get("smart_reentry_vwap_call_max_dist_pct", 0.5))
    smart_reentry_vwap_put_max_dist_pct = float(data.get("smart_reentry_vwap_put_max_dist_pct", 0.5))
    entry_atr_multiplier = data.get("entry_atr_multiplier")
    if entry_atr_multiplier is None:
        entry_atr_multiplier = data.get("atr_stop_multiplier", 2.0)
    entry_pct_move = data.get("entry_pct_move")
    if entry_pct_move is None:
        entry_pct_move = max(1.0, float(data.get("stop_loss_pct", stop_loss_pct)) * 100.0)
    
    end_date = get_now()
    start_date = end_date - timedelta(days=days)
    
    from options_backtester import OptionsBacktester
    opt_bt = OptionsBacktester(
        ticker=ticker,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        trade_amount=trade_amount,
        expiry_days=expiry_days,
        strike_type=strike_type,
        options_type=options_type,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        exit_strategy=exit_strategy,
        close_before_expiry_hours=close_before_expiry_hours,
        threshold=threshold,
        sell_threshold=sell_threshold,
        enabled_indicators=indicators,
        ext_hours=ext_hours,
        r=r,
        cancel_event=global_backtest_cancel,
        enable_signal_reset=enable_signal_reset,
        enable_smart_reentry=enable_smart_reentry,
        smart_reentry_type=smart_reentry_type,
        smart_reentry_rsi_call_max=smart_reentry_rsi_call_max,
        smart_reentry_rsi_put_min=smart_reentry_rsi_put_min,
        smart_reentry_vwap_call_max_dist_pct=smart_reentry_vwap_call_max_dist_pct,
        smart_reentry_vwap_put_max_dist_pct=smart_reentry_vwap_put_max_dist_pct,
        enable_cooldown=bool(data.get("enable_cooldown", True)),
        cooldown_minutes=int(data["cooldown_minutes"]) if data.get("cooldown_minutes") is not None else 0,
        buy_mode=data.get("buy_mode", "indicator"),
        custom_buy_price=float(data.get("custom_buy_price", 0.0)),
        custom_stop_loss_price=float(data.get("custom_stop_loss_price", 0.0)),
        custom_take_profit_price=float(data.get("custom_take_profit_price", 0.0)),
        custom_underlying_stop_loss_price=float(data.get("custom_underlying_stop_loss_price", 0.0)),
        custom_underlying_take_profit_price=float(data.get("custom_underlying_take_profit_price", 0.0)),
        entry_atr_multiplier=float(entry_atr_multiplier),
        entry_pct_move=float(entry_pct_move),
        entry_atr_style=data.get("entry_atr_style", "pullback_reclaim"),
        entry_pct_style=data.get("entry_pct_style", "dip_reclaim"),
        swing_entry_mode=data.get("swing_entry_mode", "bounce_retest"),
        swing_entry_lookback_days=int(data.get("swing_entry_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        swing_exit_lookback_days=int(data.get("swing_exit_lookback_days", data.get("swing_lookback_days", 20)) or 20),
        market_session=data.get("market_session", "Regular"),
        sell_mode=data.get("sell_mode", data.get("exit_strategy", "indicator")),
        atr_stop_multiplier=float(data.get("atr_stop_multiplier", 2.0)),
        atr_trail_multiplier=float(data.get("atr_trail_multiplier", 3.0)),
        atr_take_profit_multiplier=float(data.get("atr_take_profit_multiplier", 4.0)),
        regular_stop_loss_pct=float(data.get("stop_loss_pct", stop_loss_pct)),
        regular_take_profit_pct=float(data.get("take_profit_pct", take_profit_pct)),
        regular_trailing_pct=float(data.get("regular_trailing_pct", 0.03)),
        risk_per_trade=float(data.get("risk_per_trade", 1.0)),
        max_pos_pct=float(data.get("max_pos_pct", 1.0))
    )

    
    try:
        results = await asyncio.to_thread(opt_bt.run)
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        print(f"[api] Options Backtest crash: {e}")
        return {"status": "error", "message": "An internal error occurred during options backtest simulation."}


@app.post("/api/backtest/monte_carlo")
async def run_monte_carlo_endpoint(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """
    Monte Carlo simulation on a completed trade list.
    Accepts the raw trades array from a previous backtest result — no re-running needed.
    Returns overfitting risk classification, Sharpe distribution, and fan chart data.
    """
    trades = data.get("trades", [])
    capital = float(data.get("capital", 1000.0))
    n_simulations = int(data.get("n_simulations", 1000))

    if not trades:
        return {"status": "error", "message": "No trades provided for Monte Carlo simulation."}
    if len(trades) < 3:
        return {"status": "error", "message": "Need at least 3 trades to run Monte Carlo simulation."}

    # Cap simulations for safety
    n_simulations = min(n_simulations, 5000)

    from monte_carlo import run_monte_carlo
    try:
        result = await asyncio.to_thread(run_monte_carlo, trades, capital, n_simulations)
        if "error" in result:
            return {"status": "error", "message": result["error"]}
        return {"status": "success", "result": result}
    except Exception as e:
        print(f"[api] Monte Carlo error: {e}")
        return {"status": "error", "message": "An error occurred during Monte Carlo simulation."}


@app.post("/api/backtest/deploy")
async def deploy_backtest_as_bot(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """
    One-click deploy: Converts a completed backtest's settings into a live bot.
    deploy_mode: "override" (keep existing position) or "liquidate" (close & redeploy)
    """
    symbol = data.get("ticker", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
    deploy_mode = data.get("deploy_mode", "override")  # "override" | "liquidate"
    is_options = data.get("is_options", False)

    # Handle liquidation if requested and position exists
    if deploy_mode == "liquidate" and symbol in eng.config.TRADELIST:
        try:
            eng.broker.cancel_orders_for_ticker(symbol)
            eng.broker.close_position(symbol)
        except Exception as e:
            print(f"[api] Error during liquidation for {symbol}: {e}")
        if symbol in eng.config.TRADELIST:
            eng.config.TRADELIST.remove(symbol)
        await eng.save_settings()

    # Build create_bot payload
    deploy_payload = {
        "symbol": symbol,
        "timeframe": data.get("timeframe"),
        "capital": data.get("capital"),
        "threshold": data.get("threshold"),
        "sell_threshold": data.get("sell_threshold"),
        "indicators": data.get("indicators", []),
        "add_to_watchlist": True,
        "buy_mode": data.get("buy_mode"),
        "custom_buy_price": data.get("custom_buy_price"),
        "market_session": data.get("market_session"),
        "swing_entry_mode": data.get("swing_entry_mode"),
        "swing_entry_lookback_days": data.get("swing_entry_lookback_days"),
        "swing_entry_lookback_mode": data.get("swing_entry_lookback_mode"),
        "entry_atr_multiplier": data.get("entry_atr_multiplier"),
        "entry_pct_move": data.get("entry_pct_move"),
        "entry_atr_style": data.get("entry_atr_style"),
        "entry_pct_style": data.get("entry_pct_style"),
        "swing_exit_lookback_days": data.get("swing_exit_lookback_days"),
        "swing_exit_lookback_mode": data.get("swing_exit_lookback_mode"),
    }

    if is_options:
        options_exit_strategy = data.get("sell_mode", data.get("options_exit_strategy", data.get("exit_strategy")))
        deploy_payload.update({
            "options_trading": True,
            "options_type": data.get("options_type", "both"),
            "options_trade_amount": data.get("trade_amount"),
            "options_expiry_days": data.get("expiry_days"),
            "options_strike_type": data.get("strike_type"),
            "sell_mode": options_exit_strategy,
            "options_exit_strategy": options_exit_strategy,
            "options_stop_loss_pct": data.get("stop_loss_pct"),
            "options_take_profit_pct": data.get("take_profit_pct"),
            "options_trailing_pct": data.get("options_trailing_pct", data.get("trailing_pct")),
        })
    else:
        deploy_payload.update({
            "sell_mode": data.get("sell_mode"),
            "risk_per_trade": data.get("risk_per_trade"),
            "max_position_pct": data.get("max_position_pct"),
            "atr_stop_multiplier": data.get("atr_stop_multiplier"),
            "atr_trail_multiplier": data.get("atr_trail_multiplier"),
            "take_profit_multiplier": data.get("atr_take_profit_multiplier"),
        })
        if "ai_autopilot" in data:
            deploy_payload["ai_autopilot"] = data.get("ai_autopilot")
            deploy_payload["ai_interval"] = data.get("ai_interval")
            deploy_payload["ai_model"] = data.get("ai_model")
            
    if "enable_cooldown" in data:
        deploy_payload["enable_cooldown"] = data.get("enable_cooldown")
    if "cooldown_minutes" in data:
        deploy_payload["cooldown_minutes"] = data.get("cooldown_minutes")

    # Call create_bot to initialize/update ticker
    res = await create_bot(deploy_payload, eng)
    if res.get("status") == "success":
        # Store indicator overrides and source badge (per-bot snapshot)
        if symbol not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[symbol] = {}
        
        # Tag as backtest source
        eng.config.TICKER_SETTINGS[symbol]["source"] = "backtest"
        
        indicator_overrides = data.get("indicator_overrides", {})
        if indicator_overrides:
            eng.config.TICKER_SETTINGS[symbol]["indicator_overrides"] = indicator_overrides
            
        # Robust sync fix: Ensure symbol is strictly in the tradelist memory
        if symbol not in eng.config.TRADELIST:
            eng.config.TRADELIST.insert(0, symbol)
            
        await eng.save_settings()
        res["settings"] = eng.config.TICKER_SETTINGS[symbol]
        
    return res


@app.get("/api/options/chain")
async def get_options_chain_api(ticker: str, expiry_days: int = 7, current_price: float = None, eng: UserEngine = Depends(get_user_engine)):
    """Fetches the options contract chain for a ticker from Alpaca."""
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker.upper()):
        return {"error": "Invalid ticker format"}
        
    from option_selector import get_option_chain
    chain = get_option_chain(
        underlying=ticker,
        expiration_limit_days=expiry_days,
        current_price=current_price,
        client=eng.broker.client
    )
    return {"status": "success", "chain": chain}


@app.get("/api/options/review")
async def get_options_review(review_date: str = None, eng: UserEngine = Depends(get_user_engine)):
    """Returns today's option fills and current Alpaca quotes/snapshots for trade review."""
    if review_date:
        try:
            review_day = datetime.strptime(review_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="review_date must be YYYY-MM-DD")
    else:
        review_day = get_now().date()

    try:
        order_history, positions = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(eng.broker.get_order_history, eng.trade_log, 250),
                asyncio.to_thread(eng.broker.get_positions),
            ),
            timeout=8.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timed out loading option trade history")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load option trade history: {e}")

    today_option_orders = []
    for order in order_history or []:
        symbol = _normalize_quote_symbol(order.get("symbol", ""))
        if not _is_option_occ_symbol(symbol):
            continue

        ts = _parse_iso_datetime(order.get("filled_at") or order.get("created_at"))
        if ts and ts.date() == review_day:
            today_option_orders.append(order)

    open_option_positions = []
    for position in positions or []:
        symbol = _normalize_quote_symbol(position.get("symbol", ""))
        if _is_option_occ_symbol(symbol) or position.get("is_option"):
            open_option_positions.append(position)

    quote_symbols = list(dict.fromkeys(
        [
            _normalize_quote_symbol(order.get("symbol", ""))
            for order in today_option_orders
            if _is_option_occ_symbol(order.get("symbol", ""))
        ] + [
            _normalize_quote_symbol(position.get("symbol", ""))
            for position in open_option_positions
            if _is_option_occ_symbol(position.get("symbol", ""))
        ]
    ))

    snapshots, quote_feed, quote_error = await asyncio.to_thread(_fetch_alpaca_option_snapshots, eng, quote_symbols)
    serialized_snapshots = {}
    for sym, snapshot in (snapshots or {}).items():
        serialized = _serialize_option_snapshot(snapshot)
        serialized["quote_feed"] = quote_feed if quote_feed != "unavailable" else None
        serialized_snapshots[_normalize_quote_symbol(sym)] = serialized

    today_rows = []
    realized_today = 0.0
    for order in today_option_orders:
        symbol = _normalize_quote_symbol(order.get("symbol", ""))
        row = _build_option_review_row(order, serialized_snapshots.get(symbol, {}), review_day)
        today_rows.append(row)
        if row.get("realized_pl") is not None:
            try:
                realized_today += float(row["realized_pl"])
            except (TypeError, ValueError):
                pass

    open_rows = []
    for position in open_option_positions:
        symbol = _normalize_quote_symbol(position.get("symbol", ""))
        parsed = None
        try:
            from option_selector import parse_occ_symbol
            parsed = parse_occ_symbol(symbol)
        except Exception:
            parsed = None

        snap = serialized_snapshots.get(symbol, {})
        quote = snap.get("quote", {})
        mid = quote.get("mid")
        avg_price = _float_or_none(position.get("avg_price")) or 0.0
        qty = _float_or_none(position.get("qty")) or 0.0
        cost_basis = _float_or_none(position.get("cost_basis"))
        if cost_basis is None and avg_price and qty:
            cost_basis = avg_price * qty * 100.0

        current_value = None
        if mid is not None and qty > 0:
            current_value = round(mid * qty * 100.0, 2)
        elif _float_or_none(position.get("market_value")) is not None:
            current_value = round(float(position.get("market_value")), 2)

        unrealized_pl = None
        unrealized_pl_pct = None
        if current_value is not None and cost_basis is not None:
            unrealized_pl = round(current_value - cost_basis, 2)
            unrealized_pl_pct = round(((current_value - cost_basis) / cost_basis) * 100.0, 2) if cost_basis > 0 else None

        open_rows.append({
            "symbol": symbol,
            "underlying": parsed["underlying"] if parsed else None,
            "expiration": parsed["expiration"].isoformat() if parsed else None,
            "option_type": parsed["type"] if parsed else None,
            "strike": parsed["strike"] if parsed else None,
            "qty": qty,
            "avg_price": avg_price,
            "current_quote": quote,
            "latest_trade": snap.get("latest_trade", {}),
            "greeks": snap.get("greeks", {}),
            "quote_feed": snap.get("quote_feed"),
            "market_value": current_value,
            "cost_basis": cost_basis,
            "unrealized_pl": unrealized_pl,
            "unrealized_pl_pct": unrealized_pl_pct,
            "days_to_expiry": (parsed["expiration"] - review_day).days if parsed else None,
        })

    today_rows.sort(key=lambda row: row.get("filled_at") or row.get("created_at") or "", reverse=True)
    open_rows.sort(key=lambda row: row.get("symbol", ""))

    summary = {
        "review_date": review_day.isoformat(),
        "today_option_orders": len(today_rows),
        "open_option_positions": len(open_rows),
        "tracked_contracts": len(serialized_snapshots),
        "quote_feed": quote_feed,
        "quote_error": quote_error or None,
        "today_realized_pl": round(realized_today, 2),
    }

    return {
        "status": "success",
        "summary": summary,
        "today_orders": today_rows,
        "open_positions": open_rows,
    }


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
def get_indicator_settings(context: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """Returns all indicator toggles grouped by category."""
    from user_config import active_evaluation_context
    token = active_evaluation_context.set(context)
    try:
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
            "ENABLE_AI_SENTIMENT": {"label": "Botbulls4 News AI", "description": "Groq-powered News Sentiment Analysis", "enabled": getattr(eng.config, "ENABLE_AI_SENTIMENT", True)},
            "ENABLE_CANDLE_PATTERNS": {"label": "Candle Patterns", "description": "Engulfing, Hammer, Shooting Star patterns", "enabled": getattr(eng.config, "ENABLE_CANDLE_PATTERNS", True)},
        },
    }
    finally:
        active_evaluation_context.reset(token)


@app.post("/api/settings/risk")
async def update_risk_settings(settings: dict, context: str = "dashboard", eng: UserEngine = Depends(get_user_engine)):
    """Updates risk management parameters."""
    from user_config import active_evaluation_context
    token = active_evaluation_context.set(context)
    try:
        for key, value in settings.items():
            if key in ['MAX_DAILY_DRAWDOWN', 'RISK_PER_TRADE', 'MAX_POSITION_PCT', 'GLOBAL_HARD_STOP_PCT']:
                if isinstance(value, (int, float)) and value > 1:
                    value = value / 100.0
            elif value is None:
                value = None # Explicit pass for null values
            eng.config.set(key, value)
    finally:
        active_evaluation_context.reset(token)
            
    await eng.save_settings()
    print(f"[settings] Updated risk parameters ({context}) for {eng.uid}: {', '.join(settings.keys())}")
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
        clear_evaluation_cache()
        clear_ai_cache()
        _clear_chart_analysis_cache()
        
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
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
    return {"status": "success", "watchlist": eng.config.WATCHLIST}


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Remove a ticker from the watchlist."""
    ticker = ticker.upper()
    if ticker in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(ticker)
        print(f"[settings] Removed {ticker} from watchlist")
        await eng.save_settings()
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
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
        eng.config.TRADELIST.insert(0, ticker)
        
        if ticker not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[ticker] = {}
        eng.config.TICKER_SETTINGS[ticker]['timeframe'] = timeframe
        eng.config.TICKER_SETTINGS[ticker]['market_session'] = _normalize_market_session_for_symbol(
            ticker,
            eng.config.TICKER_SETTINGS[ticker].get('market_session')
        )
        print(f"[settings] Activated {ticker} on locked {timeframe} timeframe")

        await eng.save_settings()
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
        if eng.force_scan_trigger:
            eng.force_scan_trigger.set()
    return {"status": "success", "tradelist": eng.config.TRADELIST, "watchlist": eng.config.WATCHLIST}


@app.get("/api/assets/session_eligibility/{symbol}")
def get_asset_session_eligibility(symbol: str, market_session: str = Query("Regular"), eng: UserEngine = Depends(get_user_engine)):
    """Return whether Alpaca allows the selected market session for this asset."""
    symbol = symbol.upper().strip()
    if not symbol:
        return {"status": "error", "allowed": False, "message": "Symbol is required"}
    session = _normalize_market_session_for_symbol(symbol, market_session)
    check = eng.broker.check_market_session_allowed(symbol, session)
    return {
        "status": "success" if check.get("success") else "error",
        "allowed": bool(check.get("allowed")),
        "symbol": check.get("symbol", symbol),
        "market_session": session,
        "overnight_tradable": bool(check.get("overnight_tradable", False)),
        "message": check.get("message") or check.get("error") or "",
    }


@app.get("/api/debug/history")
async def debug_history(eng: UserEngine = Depends(get_user_engine)):
    return {
        "executed_trades_count": 0,
        "trade_log_count": len(eng.trade_log),
        "executed_trades": [],
        "eng.trade_log": eng.trade_log[:10]
    }

@app.delete("/api/tradelist/{ticker}")
async def remove_from_tradelist(ticker: str, liquidate: bool = False, eng: UserEngine = Depends(get_user_engine)):
    """Remove a ticker from the active trade list."""
    ticker = ticker.upper()
    if ticker in eng.config.TRADELIST:
        eng.config.TRADELIST.remove(ticker)
        await eng.save_settings()
        print(f"[settings] Removed {ticker} from active tradelist (Bot Deactivated) for {eng.uid}")
        clear_evaluation_cache(ticker, None)
        _clear_chart_analysis_cache(ticker, None)
        
    res_cancel = eng.broker.cancel_orders_for_ticker(ticker)
    print(f"[tradelist] Cancelled pending orders for {ticker}: {res_cancel}")

    if liquidate:
        res = eng.broker.close_position(ticker)
        print(f"[tradelist] Liquidating {ticker}: {res}")

    return {"status": "success", "tradelist": eng.config.TRADELIST}


@app.get("/api/search/{query}")
def search_symbols(query: str, eng: UserEngine = Depends(get_user_engine)):
    """Searches for tradeable assets using the modular eng.broker."""
    if len(query) < 1:
        return []
    return eng.broker.search_assets(query)


@app.post("/api/bots/reorder")
async def reorder_bots(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Reorder the priority of active bots."""
    new_order = data.get("tradelist", [])
    if not new_order or not isinstance(new_order, list):
        return {"status": "error", "message": "Invalid tradelist format"}
    
    # Verify that all provided tickers are currently active bots, preserving order and removing duplicates
    valid_tickers = []
    for t in new_order:
        if t in eng.config.TRADELIST and t not in valid_tickers:
            valid_tickers.append(t)
    
    # Ensure any tickers currently active that weren't in the new order are appended to the end
    for t in eng.config.TRADELIST:
        if t not in valid_tickers:
            valid_tickers.append(t)
            
    eng.config.TRADELIST = valid_tickers
    await eng.save_settings()
    
    return {"status": "success", "tradelist": eng.config.TRADELIST}


@app.post("/api/bots/create")
async def create_bot(data: dict, eng: UserEngine = Depends(get_user_engine)):
    """Creates a new active bot with custom settings (adds to watchlist, tradelist, and TICKER_SETTINGS)."""
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
        
    if symbol in eng.config.TRADELIST and not data.get("force_override", False):
        return {
            "status": "error",
            "message": f"An active bot for '{symbol}' is already deployed. Please delete the existing bot first to deploy a new one."
        }
        
    # Check if the asset is tradable in Alpaca
    tradable_check = eng.broker.check_tradable(symbol)
    if not tradable_check.get("success") or not tradable_check.get("tradable"):
        return {
            "status": "error", 
            "message": tradable_check.get("error", f"Ticker '{symbol}' doesn't allow trading in Alpaca or does not exist.")
        }
    market_session_check = _validate_market_session_for_symbol(symbol, data.get("market_session", "Regular"), eng)
        
    if symbol not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[symbol] = {}
        
    if "capital" in data and data["capital"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["amount"] = float(data["capital"])
        
    if "threshold" in data and data["threshold"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["min_buy_signals"] = int(data["threshold"])
    if "sell_threshold" in data and data["sell_threshold"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["min_sell_signals"] = int(data["sell_threshold"])
    
    if "timeframe" in data:
        eng.config.TICKER_SETTINGS[symbol]["timeframe"] = data["timeframe"]
        
    if "sell_mode" in data:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = data["sell_mode"]
    else:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = "indicator"

    passthrough_settings = [
        "buy_mode",
        "custom_buy_price",
        "custom_stop_loss_price",
        "custom_take_profit_price",
        "custom_underlying_stop_loss_price",
        "custom_underlying_take_profit_price",
        "allocation_mode",
        "compound_amount",
        "options_allocation_mode",
        "options_compound_amount",
        "market_session",
        "entry_atr_multiplier",
        "entry_pct_move",
        "entry_atr_style",
        "entry_pct_style",
        "swing_entry_mode",
        "swing_entry_lookback_days",
        "swing_entry_lookback_mode",
        "swing_exit_lookback_days",
        "swing_exit_lookback_mode",
        "enable_risk_management",
        "enable_atr_trail",
        "enable_pct_trail",
        "regular_stop_loss_pct",
        "regular_take_profit_pct",
        "regular_trailing_pct",
        "enable_cooldown",
        "enable_signal_reset",
        "enable_smart_reentry",
        "smart_reentry_type",
        "smart_reentry_rsi_call_max",
        "smart_reentry_rsi_put_min",
        "smart_reentry_vwap_call_max_dist_pct",
        "smart_reentry_vwap_put_max_dist_pct",
    ]
    for key in passthrough_settings:
        if key in data:
            value = data[key]
            if value is None or value == "default":
                eng.config.TICKER_SETTINGS[symbol].pop(key, None)
            else:
                eng.config.TICKER_SETTINGS[symbol][key] = value
    eng.config.TICKER_SETTINGS[symbol]["market_session"] = _normalize_market_session_for_symbol(
        symbol,
        eng.config.TICKER_SETTINGS[symbol].get("market_session")
    )
    eng.config.TICKER_SETTINGS[symbol]["market_session"] = market_session_check["market_session"]

    allocation_mode = str(eng.config.TICKER_SETTINGS[symbol].get("allocation_mode", "fixed") or "fixed").lower()
    if allocation_mode == "compound" and not eng.config.TICKER_SETTINGS[symbol].get("compound_amount"):
        eng.config.TICKER_SETTINGS[symbol]["compound_amount"] = float(eng.config.TICKER_SETTINGS[symbol].get("amount", data.get("capital", 0)) or 0)
    if allocation_mode != "compound":
        eng.config.TICKER_SETTINGS[symbol].pop("compound_amount", None)
        
    if "indicators" in data and isinstance(data["indicators"], list):
        eng.config.TICKER_SETTINGS[symbol]["indicators"] = data["indicators"]
    _normalize_bot_signal_thresholds(eng.config.TICKER_SETTINGS[symbol], eng)

    for rk in ["risk_per_trade", "max_daily_drawdown", "max_position_pct", "atr_stop_multiplier", "atr_trail_multiplier", "take_profit_multiplier"]:
        if rk in data:
            if data[rk] is not None:
                eng.config.TICKER_SETTINGS[symbol][rk] = float(data[rk])
            else:
                if rk in eng.config.TICKER_SETTINGS[symbol]:
                    del eng.config.TICKER_SETTINGS[symbol][rk]

    if "cooldown_minutes" in data:
        if data["cooldown_minutes"] is None or data["cooldown_minutes"] == "default":
            if "cooldown_minutes" in eng.config.TICKER_SETTINGS[symbol]:
                del eng.config.TICKER_SETTINGS[symbol]["cooldown_minutes"]
        else:
            eng.config.TICKER_SETTINGS[symbol]["cooldown_minutes"] = int(data["cooldown_minutes"])

    # Options Trading parameters
    if "options_trading" in data:
        eng.config.TICKER_SETTINGS[symbol]["options_trading"] = bool(data["options_trading"])
    if "options_type" in data:
        eng.config.TICKER_SETTINGS[symbol]["options_type"] = data["options_type"]
    if "options_trade_amount" in data:
        eng.config.TICKER_SETTINGS[symbol]["options_trade_amount"] = float(data["options_trade_amount"])
    options_allocation_mode = str(eng.config.TICKER_SETTINGS[symbol].get("options_allocation_mode", eng.config.TICKER_SETTINGS[symbol].get("allocation_mode", "fixed")) or "fixed").lower()
    if options_allocation_mode == "compound" and not eng.config.TICKER_SETTINGS[symbol].get("options_compound_amount"):
        eng.config.TICKER_SETTINGS[symbol]["options_compound_amount"] = float(eng.config.TICKER_SETTINGS[symbol].get("options_trade_amount", data.get("options_trade_amount", 0)) or 0)
    if options_allocation_mode != "compound":
        eng.config.TICKER_SETTINGS[symbol].pop("options_compound_amount", None)
    if "options_expiry_days" in data and data["options_expiry_days"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["options_expiry_days"] = int(data["options_expiry_days"])
    if "options_strike_type" in data:
        eng.config.TICKER_SETTINGS[symbol]["options_strike_type"] = data["options_strike_type"]
    if "options_exit_strategy" in data:
        eng.config.TICKER_SETTINGS[symbol]["options_exit_strategy"] = data["options_exit_strategy"]
    if eng.config.TICKER_SETTINGS[symbol].get("options_trading") and eng.config.TICKER_SETTINGS[symbol].get("sell_mode"):
        eng.config.TICKER_SETTINGS[symbol]["options_exit_strategy"] = eng.config.TICKER_SETTINGS[symbol]["sell_mode"]
    if "options_stop_loss_pct" in data and data["options_stop_loss_pct"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["options_stop_loss_pct"] = float(data["options_stop_loss_pct"])
    if "options_take_profit_pct" in data and data["options_take_profit_pct"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["options_take_profit_pct"] = float(data["options_take_profit_pct"])
    if "options_trailing_pct" in data and data["options_trailing_pct"] is not None:
        eng.config.TICKER_SETTINGS[symbol]["options_trailing_pct"] = float(data["options_trailing_pct"])

    if "ai_autopilot" in data:
        eng.config.TICKER_SETTINGS[symbol]["ai_autopilot"] = bool(data["ai_autopilot"])
    if "ai_model" in data:
        eng.config.TICKER_SETTINGS[symbol]["ai_model"] = data["ai_model"]

    if "paused" in data:
        eng.config.TICKER_SETTINGS[symbol]["paused"] = bool(data["paused"])
        if not data["paused"]:
            eng.config.TICKER_SETTINGS[symbol]["pause_reason"] = ""
            eng.config.TICKER_SETTINGS[symbol]["realized_pl"] = 0.0
            eng.config.TICKER_SETTINGS[symbol]["started_at"] = get_now().isoformat()
    else:
        if not eng.config.TICKER_SETTINGS[symbol].get("paused", False):
            if not eng.config.TICKER_SETTINGS[symbol].get("started_at"):
                eng.config.TICKER_SETTINGS[symbol]["started_at"] = get_now().isoformat()

    add_to_watchlist = data.get("add_to_watchlist", False)
    if add_to_watchlist and symbol not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(symbol)
    elif not add_to_watchlist and symbol in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(symbol)
        
    if symbol not in eng.config.TRADELIST:
        eng.config.TRADELIST.insert(0, symbol)
        print(f"[settings] Launched new bot for {symbol} with custom settings for {eng.uid}")
        
    await eng.save_settings()
    clear_evaluation_cache(symbol, None)
    clear_ai_cache(symbol, None)
    _clear_chart_analysis_cache(symbol, None)
    
    # Warm up cache synchronously before returning so immediate dashboard refresh gets the price/data
    try:
        timeframe = data.get("timeframe") or eng.config.TICKER_SETTINGS.get(symbol, {}).get('timeframe') or eng.config.DEFAULT_TIMEFRAME
        is_crypto = _is_crypto_symbol(symbol)
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
async def update_indicators(updates: dict, context: str = "dashboard", ticker: str = None, eng: UserEngine = Depends(get_user_engine)):
    """Update indicator toggles or parameters dynamically. Instant in-memory + persists to Firestore."""
    
    if ticker:
        ticker = ticker.upper()
        if ticker not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[ticker] = {}
        if 'indicator_overrides' not in eng.config.TICKER_SETTINGS[ticker]:
            eng.config.TICKER_SETTINGS[ticker]['indicator_overrides'] = {}
            
        for k, v in updates.items():
            if v == "" or v is None:
                if k in eng.config.TICKER_SETTINGS[ticker]['indicator_overrides']:
                    del eng.config.TICKER_SETTINGS[ticker]['indicator_overrides'][k]
            else:
                try:
                    default_val = getattr(global_config, k, None)
                    if isinstance(default_val, int): v = int(v)
                    elif isinstance(default_val, float): v = float(v)
                except Exception:
                    pass
                eng.config.TICKER_SETTINGS[ticker]['indicator_overrides'][k] = v
    else:
        from user_config import active_evaluation_context
        token = active_evaluation_context.set(context)
        try:
            for k, v in updates.items():
                if v == "" or v is None:
                    eng.config.set(k, None)
                elif k.startswith("ENABLE_"):
                    eng.config.set(k, bool(v))
                else:
                    try:
                        default_val = getattr(global_config, k, None)
                        if isinstance(default_val, int):
                            eng.config.set(k, int(v))
                        elif isinstance(default_val, float):
                            eng.config.set(k, float(v))
                        else:
                            eng.config.set(k, v)
                    except Exception as e:
                        print(f"[settings] Type conversion error for {k}: {e}")
                        eng.config.set(k, v)
        finally:
            active_evaluation_context.reset(token)

    await eng.save_settings()
    clear_evaluation_cache()
    clear_ai_cache(ticker, None) if ticker else clear_ai_cache()
    _clear_chart_analysis_cache(ticker, None)
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
    previously_paused = eng.config.TICKER_SETTINGS.get(ticker, {}).get('paused', False)
    requested_session = settings.get("market_session", eng.config.TICKER_SETTINGS[ticker].get("market_session", "Regular"))
    market_session_check = _validate_market_session_for_symbol(ticker, requested_session, eng)
    for k, v in settings.items():
        if v is not None and v != "default":
            eng.config.TICKER_SETTINGS[ticker][k] = v
        elif k in eng.config.TICKER_SETTINGS[ticker]:
            del eng.config.TICKER_SETTINGS[ticker][k]
    eng.config.TICKER_SETTINGS[ticker]["market_session"] = market_session_check["market_session"]

    current_settings = eng.config.TICKER_SETTINGS[ticker]
    if current_settings.get("options_trading") and current_settings.get("sell_mode"):
        current_settings["options_exit_strategy"] = current_settings["sell_mode"]
    allocation_mode = str(current_settings.get("allocation_mode", "fixed") or "fixed").lower()
    if allocation_mode == "compound" and not current_settings.get("compound_amount"):
        current_settings["compound_amount"] = float(current_settings.get("amount", 0) or 0)
    if allocation_mode != "compound":
        current_settings.pop("compound_amount", None)

    options_allocation_mode = str(current_settings.get("options_allocation_mode", current_settings.get("allocation_mode", "fixed")) or "fixed").lower()
    if options_allocation_mode == "compound" and not current_settings.get("options_compound_amount"):
        current_settings["options_compound_amount"] = float(current_settings.get("options_trade_amount", current_settings.get("amount", 0)) or 0)
    if options_allocation_mode != "compound":
        current_settings.pop("options_compound_amount", None)
    _normalize_bot_signal_thresholds(eng.config.TICKER_SETTINGS[ticker], eng)

    if settings.get('paused') is False:
        eng.config.TICKER_SETTINGS[ticker]['pause_reason'] = ""
        eng.config.TICKER_SETTINGS[ticker]['realized_pl'] = 0.0
        if previously_paused or not eng.config.TICKER_SETTINGS[ticker].get('started_at'):
            eng.config.TICKER_SETTINGS[ticker]['started_at'] = get_now().isoformat()
            
    add_to_watchlist = data.get("add_to_watchlist")
    if add_to_watchlist is True and ticker not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(ticker)
    elif add_to_watchlist is False and ticker in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(ticker)
    
    await eng.save_settings()
    clear_evaluation_cache(ticker, None)
    clear_ai_cache(ticker, None)
    _clear_chart_analysis_cache(ticker, None)
    return {"status": "success"}

@app.delete("/api/settings/ticker/{ticker}")
async def reset_ticker_settings(ticker: str, eng: UserEngine = Depends(get_user_engine)):
    """Reset a ticker to global defaults."""
    ticker = ticker.upper()
    if ticker in eng.config.TICKER_SETTINGS:
        del eng.config.TICKER_SETTINGS[ticker]
        await eng.save_settings()
    clear_evaluation_cache(ticker, None)
    clear_ai_cache(ticker, None)
    _clear_chart_analysis_cache(ticker, None)
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


def _parse_iso_datetime(value):
    if not value:
        return None

    dt = value if isinstance(value, datetime) else None
    if dt is None:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(pytz.timezone(get_user_config().TIMEZONE))


def _is_option_occ_symbol(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,6}\d{6}[CP]\d{8}", _normalize_quote_symbol(symbol)))


def _model_value(obj, *names, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _float_or_none(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mapping_from_response(resp) -> dict:
    if resp is None:
        return {}
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "items"):
        try:
            return dict(resp.items())
        except Exception:
            pass
    if hasattr(resp, "model_dump"):
        try:
            return resp.model_dump()
        except Exception:
            pass
    if hasattr(resp, "dict"):
        try:
            return resp.dict()
        except Exception:
            pass
    return {}


def _serialize_option_snapshot(snapshot: dict) -> dict:
    latest_quote = _model_value(snapshot, "latest_quote")
    latest_trade = _model_value(snapshot, "latest_trade")
    greeks = _model_value(snapshot, "greeks")

    bid = _float_or_none(_model_value(latest_quote, "bid_price", "bid"))
    ask = _float_or_none(_model_value(latest_quote, "ask_price", "ask"))
    mid = ((bid + ask) / 2.0) if bid is not None and ask is not None else None
    spread = (ask - bid) if bid is not None and ask is not None else None
    spread_pct = ((spread / mid) * 100.0) if spread is not None and mid else None

    return {
        "quote": {
            "bid": bid,
            "ask": ask,
            "bid_size": _float_or_none(_model_value(latest_quote, "bid_size")),
            "ask_size": _float_or_none(_model_value(latest_quote, "ask_size")),
            "timestamp": str(_model_value(latest_quote, "timestamp", "t", default="")) or None,
            "exchange": _model_value(latest_quote, "exchange", "bid_exchange", "ask_exchange"),
            "mid": mid,
            "spread": spread,
            "spread_pct": spread_pct,
        },
        "latest_trade": {
            "price": _float_or_none(_model_value(latest_trade, "price", "trade_price")),
            "size": _float_or_none(_model_value(latest_trade, "size", "trade_size")),
            "timestamp": str(_model_value(latest_trade, "timestamp", "t", default="")) or None,
            "exchange": _model_value(latest_trade, "exchange"),
        },
        "greeks": {
            "delta": _float_or_none(_model_value(greeks, "delta")),
            "gamma": _float_or_none(_model_value(greeks, "gamma")),
            "theta": _float_or_none(_model_value(greeks, "theta")),
            "vega": _float_or_none(_model_value(greeks, "vega")),
            "rho": _float_or_none(_model_value(greeks, "rho")),
            "iv": _float_or_none(_model_value(greeks, "implied_volatility", "iv")),
        },
        "implied_volatility": _float_or_none(_model_value(snapshot, "implied_volatility", "iv")),
        "open_interest": _float_or_none(_model_value(snapshot, "open_interest")),
        "updated_at": str(_model_value(snapshot, "updated_at", "timestamp", default="")) or None,
    }


def _fetch_alpaca_option_snapshots(eng: UserEngine, symbols: list[str]) -> tuple[dict, str, str]:
    clean_symbols = list(dict.fromkeys(_normalize_quote_symbol(s) for s in symbols if _is_option_occ_symbol(s)))
    if not clean_symbols:
        return {}, "unavailable", ""

    api_key = getattr(eng.config, "ALPACA_API_KEY", "") or config.ALPACA_API_KEY
    api_secret = getattr(eng.config, "ALPACA_SECRET_KEY", "") or config.ALPACA_SECRET_KEY
    if not api_key or not api_secret:
        return {}, "unavailable", "Missing Alpaca API credentials."

    try:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionSnapshotRequest
    except Exception as exc:
        return {}, "unavailable", f"Alpaca option data client unavailable: {exc}"

    data_client = OptionHistoricalDataClient(api_key, api_secret)
    last_error = ""

    for feed in (OptionsFeed.OPRA, OptionsFeed.INDICATIVE):
        try:
            merged = {}
            for i in range(0, len(clean_symbols), 100):
                batch = clean_symbols[i:i + 100]
                req = OptionSnapshotRequest(symbol_or_symbols=batch, feed=feed)
                resp = data_client.get_option_snapshot(req)
                merged.update(_mapping_from_response(resp))

            if merged:
                return merged, getattr(feed, "value", str(feed)), ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

    return {}, "unavailable", last_error


def _build_option_review_row(order: dict, snapshot: dict, review_day) -> dict:
    symbol = _normalize_quote_symbol(order.get("symbol", ""))
    parsed = None
    try:
        from option_selector import parse_occ_symbol
        parsed = parse_occ_symbol(symbol)
    except Exception:
        parsed = None

    quote = snapshot.get("quote", {}) if snapshot else {}
    greeks = snapshot.get("greeks", {}) if snapshot else {}
    latest_trade = snapshot.get("latest_trade", {}) if snapshot else {}

    fill_price = _float_or_none(order.get("filled_avg_price")) or 0.0
    qty = _float_or_none(order.get("filled_qty") or order.get("qty")) or 0.0
    side = str(order.get("side", "")).upper()
    current_mid = quote.get("mid")
    contract_value = (fill_price * qty * 100.0) if fill_price and qty else 0.0

    mtm_pl = None
    mtm_pl_pct = None
    if side == "BUY" and current_mid is not None and fill_price > 0 and qty > 0:
        mtm_pl = round((current_mid - fill_price) * qty * 100.0, 2)
        mtm_pl_pct = round(((current_mid - fill_price) / fill_price) * 100.0, 2)

    return {
        "symbol": symbol,
        "underlying": parsed["underlying"] if parsed else None,
        "expiration": parsed["expiration"].isoformat() if parsed else None,
        "option_type": parsed["type"] if parsed else None,
        "strike": parsed["strike"] if parsed else None,
        "side": side,
        "status": order.get("status"),
        "qty": qty,
        "filled_avg_price": fill_price,
        "filled_at": order.get("filled_at") or order.get("created_at"),
        "created_at": order.get("created_at"),
        "total_cost": order.get("total_cost", contract_value),
        "realized_pl": order.get("pl"),
        "realized_pl_pct": order.get("pl_pct"),
        "current_quote": quote,
        "latest_trade": latest_trade,
        "greeks": greeks,
        "quote_feed": snapshot.get("quote_feed") if snapshot else None,
        "mark_to_market_pl": mtm_pl,
        "mark_to_market_pl_pct": mtm_pl_pct,
        "days_to_expiry": (parsed["expiration"] - review_day).days if parsed else None,
        "review_date": review_day.isoformat(),
        "reason": order.get("reason", ""),
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
        'Botbulls4 News AI': 'ENABLE_AI_SENTIMENT',
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
        if 'enabled' in data:
            enabled = data['enabled']
        elif name == 'Botbulls4 News AI':
            enabled = getattr(uc, 'ENABLE_AI_SENTIMENT', True)
        elif allowed_indicators is not None:
            enabled = name in allowed_indicators
        else:
            enabled = getattr(uc, toggle_key, True) if toggle_key else True
            
        all_signals[name] = {**data, 'enabled': enabled, 'toggle_key': toggle_key if name != 'Botbulls4 News AI' else 'ENABLE_AI_SENTIMENT'}

    bullish = scan.get('bullish_count')
    if bullish is None:
        bullish = sum(s.get('weight', 1) for s in all_signals.values() if s.get('signal') == 'BULLISH' and s.get('enabled'))

    bearish = scan.get('bearish_count')
    if bearish is None:
        bearish = sum(s.get('weight', 1) for s in all_signals.values() if s.get('signal') == 'BEARISH' and s.get('enabled'))

    active_count = scan.get('total_signals')
    if active_count is None:
        active_count = sum(1 for s in all_signals.values() if s.get('enabled'))

    return {
        "ticker": scan.get("ticker", ""),
        "price": scan.get("price", ""),
        "price_raw": scan.get("price_raw", 0.0),
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
        "entry_price": scan.get("entry_price"),
        "stop_loss": scan.get("stop_loss"),
        "sell_target": scan.get("sell_target"),
        "sell_target_pct": scan.get("sell_target_pct"),
        "leverage_guidance": scan.get("leverage_guidance"),
        "key_catalyst": scan.get("key_catalyst"),
        "risk_factors": scan.get("risk_factors"),
        "summary": scan.get("summary"),
        "regime": scan.get("regime"),
        "position_size_usd": scan.get("position_size_usd"),
        "position_size_pct": scan.get("position_size_pct"),
        "prior_day_ohlc": scan.get("prior_day_ohlc"),
    }

@app.get("/api/debug-doge")
async def get_debug_doge():
    if not user_manager.engines: return {"error": "no engines"}
    eng = list(user_manager.engines.values())[0]
    
    from main import _format_trade_for_ui
    ui_trade_log = [_format_trade_for_ui(trade) for trade in eng.trade_log]
    
    # Find DOGEUSD trade
    doge_trades = [t for t in ui_trade_log if t.get('ticker') == 'DOGEUSD' and t.get('action') == 'BUY']
    if not doge_trades:
        return {"error": "No open DOGEUSD trades found", "trades": ui_trade_log}
    
    p = doge_trades[0]
    
    from main import _lookup_quote_price, _format_quote_price, fmt_val
    scan = eng._pick_scan('DOGEUSD', '5Min', prefer_bot=True)
    return {"trade": p, "scan_targets": {"sl": scan.get("stop_loss"), "tp": scan.get("sell_target")}}
