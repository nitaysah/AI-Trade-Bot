"""
AI Trading Bot — FastAPI Backend.

Production-grade trading engine with:
- Multi-ticker watchlist scanning
- Background scheduler for automated evaluation
- Full REST API for the dashboard
- Alpaca broker integration
- Risk management enforcement
"""

from fastapi import FastAPI, Query, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
import asyncio
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import auth, credentials

from trader import evaluate_trade, get_risk_manager
from broker import AlpacaBroker
from backtester import Backtester
from data_manager import get_historical_data
import config
import yfinance as yf
import json
import os
import re


# ──────────────────────────────────────────────
# Initialization
# ──────────────────────────────────────────────
# Initialize Firebase Admin
try:
    # Explicitly provide project ID for robustness
    firebase_admin.initialize_app(options={'projectId': 'trading-bot-engine-df3de'})
    print("[main] Firebase Admin initialized (trading-bot-engine-df3de).")
except Exception as e:
    print(f"[main] Firebase Admin init: {e}")

async def verify_token(authorization: str = Header(None)):
    """Security dependency to verify Firebase ID Token."""
    if not authorization:
        print("[security] Missing Authorization header")
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    if not authorization.startswith("Bearer "):
        print("[security] Invalid Authorization format")
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    
    token = authorization.split("Bearer ")[1]
    try:
        # Verify the ID token
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        print(f"[security] Token verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token verification failed: {str(e)}")

broker = AlpacaBroker()
trade_log = []      # In-memory history for all scans (HOLD/WATCH/BUY/SELL)
executed_trades = [] # Persistent history for successful BUY/SELL orders only
latest_scans = {}   # {ticker: latest_evaluation_result}
bot_running = False
last_scan_time = None

# Persistence path
ALPACA_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "alpaca_settings.json")

class AlpacaConfig(BaseModel):
    api_key: str
    secret_key: str
    paper: bool = True

def load_persisted_alpaca():
    if os.path.exists(ALPACA_CONFIG_PATH):
        try:
            with open(ALPACA_CONFIG_PATH, "r") as f:
                data = json.load(f)
                success = broker.connect(data['api_key'], data['secret_key'], data['paper'])
                if success:
                    # Update global config for other components to see
                    config.ALPACA_API_KEY = data['api_key']
                    config.ALPACA_SECRET_KEY = data['secret_key']
                    config.ALPACA_PAPER = data['paper']
                    print("[main] Loaded persisted Alpaca credentials.")
        except Exception as e:
            print(f"[main] Error loading persisted Alpaca config: {e}")

load_persisted_alpaca()


# ──────────────────────────────────────────────
# Background Scheduler
# ──────────────────────────────────────────────
async def trading_loop():
    """Background task that scans the watchlist on an interval."""
    global bot_running, last_scan_time, latest_scans, force_scan_trigger
    force_scan_trigger = asyncio.Event()

    bot_running = True
    print(f"[scheduler] Trading loop started. Scanning every {config.SCAN_INTERVAL_SECONDS}s")

    while bot_running:
        try:
            account = broker.get_account_info()
            equity = account['equity']

            # Set daily baseline if not set
            risk_mgr = get_risk_manager()
            if risk_mgr.daily_starting_equity is None:
                risk_mgr.set_daily_equity(equity)
            
            all_positions = broker.get_positions()
            portfolio_count = len(all_positions)

            for ticker in config.WATCHLIST:
                try:
                    # 1. Determine available cash based on asset type
                    # Robust crypto detection
                    clean_ticker = ticker.upper().replace("/", "")
                    is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
                    avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
                    
                    # 2. Evaluate Ticker (Indicators + AI Sentiment)
                    result = evaluate_trade(
                        ticker, 
                        account_equity=equity, 
                        available_cash=avail_cash,
                        timeframe=config.DEFAULT_TIMEFRAME
                    )
                    if result:
                        is_active = ticker in config.TRADELIST
                        log_prefix = "[trader]" if is_active else "[scan]"
                        print(f"{log_prefix} {ticker}: ${result['price_raw']} | Signal: {result['action']} | Sent: {result['sentiment_score']}")
                        
                        latest_scans[ticker] = result
                        trade_executed = False

                        # 3. Auto-execute trades ONLY if ticker is in TRADELIST
                        if ticker in config.TRADELIST:
                            # Normalize symbol for position matching
                            ticker_norm = ticker.replace("/", "").upper()
                            has_this_pos = any(
                                p['symbol'].replace("/", "").upper() == ticker_norm and float(p.get('qty', 0)) > 0 
                                for p in all_positions
                            )

                            if result['action'] == 'BUY':
                                sizing = result['position_sizing']
                                
                                if portfolio_count > 0 and not has_this_pos:
                                    # Block new buys if we already have a position elsewhere
                                    result['action'] = 'WATCH'
                                    result['reason'] = "Signal BUY ignored: Already holding another position."
                                elif not has_this_pos and sizing['notional'] > 0:
                                    order_result = broker.place_order(
                                        symbol=ticker,
                                        notional=sizing['notional'],
                                        side='buy',
                                        stop_loss=sizing['stop_loss'],
                                        take_profit=sizing['take_profit']
                                    )
                                    if order_result.get('success'):
                                        result['order'] = order_result
                                        trade_executed = True
                                        portfolio_count += 1 # Lock further buys in this loop
                                        
                                        # Capture Detailed Receipt
                                        # Force float to ensure decimal precision
                                        result['qty'] = float(order_result.get('qty', 0))
                                        result['total_cost'] = order_result.get('total_cost', sizing['notional'])
                                        result['fees'] = order_result.get('fees', 0)

                                        # Enhance reason with execution info — using 6 decimal places
                                        result['reason'] = f"✅ BOUGHT {result['qty']:.6f} shares at {result['price']}: {result['reason']}"
                                        print(f"[trader] BUY {ticker}: ${sizing['notional']:.2f}")
                                elif has_this_pos:
                                    result['action'] = 'HOLD'
                                    result['reason'] = "Position already open."

                            elif result['action'] == 'SELL':
                                if has_this_pos:
                                    order_result = broker.close_position(ticker)
                                    if order_result.get('success'):
                                        result['order'] = order_result
                                        trade_executed = True
                                        portfolio_count -= 1

                                        # Capture Detailed Receipt (Close position proceeds)
                                        result['qty'] = order_result.get('qty', 'ALL')
                                        result['total_cost'] = order_result.get('proceeds', 0)
                                        result['fees'] = order_result.get('fees', 0)

                                        # Enhance reason with execution info
                                        result['reason'] = f"✅ SOLD at {result['price']}: {result['reason']}"
                                        print(f"[trader] SELL {ticker}: closed position")
                                    else:
                                        print(f"[trader] FAILED SELL {ticker}: {order_result.get('error', 'Unknown Error')}")
                                        result['reason'] = f"Sell order failed: {order_result.get('error', 'Broker Error')}"
                                else:
                                    print(f"[trader] SKIP SELL {ticker}: No position found in broker scan.")
                                    # Keep the action as SELL for the logs, but mark it as skipped
                                    result['reason'] = "Signal SELL ignored: No open position detected."
                        
                        # 4. Dashboard / Log Management
                        should_log = trade_executed or (ticker in config.TRADELIST)
                        if should_log:
                            # Use a more specific key to prevent duplicate logs for the same minute
                            log_key = f"{result['time']}_{ticker}_{result['action']}"
                            if not any(f"{log['time']}_{log['ticker']}_{log['action']}" == log_key for log in trade_log):
                                trade_log.insert(0, result)
                                
                                # If it was a real execution, save to permanent ledger
                                if trade_executed:
                                    executed_trades.insert(0, result)
                                    _save_executed_trade(result)

                        # Cap log size to prevent memory bloat
                        if len(trade_log) > 100:
                            trade_log.pop()

                except Exception as e:
                    print(f"[scheduler] Error scanning {ticker}: {e}")

            last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[scheduler] Scan complete at {last_scan_time}")

        except Exception as e:
            print(f"[scheduler] Loop error: {e}")

        try:
            # Wait for interval OR force trigger
            await asyncio.wait_for(force_scan_trigger.wait(), timeout=config.SCAN_INTERVAL_SECONDS)
            force_scan_trigger.clear()
        except asyncio.TimeoutError:
            pass


# ──────────────────────────────────────────────
# App Lifecycle
# ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background trading loop on startup."""
    task = asyncio.create_task(trading_loop())
    yield
    global bot_running
    bot_running = False
    task.cancel()


app = FastAPI(
    title="AI Trader BOT",
    description="Automated AI-driven quantitative trading platform",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────

@app.get("/")
def root():
    """Welcome message for the API root."""
    return {
        "message": "AI Trading Bot API is running!",
        "version": "2.0.0",
        "docs": "/docs",
        "dashboard": "/api/dashboard"
    }


@app.post("/api/alpaca_config")
async def update_alpaca_config(cfg: AlpacaConfig, user = Depends(verify_token)):
    success = broker.connect(cfg.api_key, cfg.secret_key, cfg.paper)
    if success:
        # Persist
        with open(ALPACA_CONFIG_PATH, "w") as f:
            json.dump({
                "api_key": cfg.api_key,
                "secret_key": cfg.secret_key,
                "paper": cfg.paper
            }, f)
        
        # Update config module state
        config.ALPACA_API_KEY = cfg.api_key
        config.ALPACA_SECRET_KEY = cfg.secret_key
        config.ALPACA_PAPER = cfg.paper
        
        return {"status": "success", "message": "Connected to Alpaca."}
    else:
        return {"status": "error", "message": "Failed to connect to Alpaca. Check your keys."}


@app.delete("/api/alpaca_config")
async def unlink_alpaca(user = Depends(verify_token)):
    if os.path.exists(ALPACA_CONFIG_PATH):
        os.remove(ALPACA_CONFIG_PATH)
    
    # Reset broker to simulation
    broker.simulation_mode = True
    broker.client = None
    
    # Clear config module state
    config.ALPACA_API_KEY = ""
    config.ALPACA_SECRET_KEY = ""
    
    return {"status": "success", "message": "Alpaca account unlinked. Switched to simulation mode."}


@app.get("/api/dashboard")
def get_dashboard(ticker: str = None, timeframe: str = None, user = Depends(verify_token)):
    """
    Main dashboard endpoint — returns everything the UI needs in one call.
    """
    if timeframe is None:
        timeframe = config.DEFAULT_TIMEFRAME
        
    account = broker.get_account_info()
    positions = broker.get_positions()
    risk_mgr = get_risk_manager()

    # Determine which ticker to focus on for the chart/analysis
    # Priority: 1. URL Param, 2. First Active Bot, 3. First Watchlist Item, 4. TSLA fallback
    primary_ticker = ticker.upper() if ticker else (
        config.TRADELIST[0] if config.TRADELIST else (
            config.WATCHLIST[0] if config.WATCHLIST else "TSLA"
        )
    )
    
    # If a specific ticker/timeframe is requested, we can optionally re-evaluate here
    # or just pull from the latest_scans. Let's re-evaluate to ensure fresh data.
    # Use settled cash (non-marginable) for crypto
    is_crypto = any(c in primary_ticker for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in primary_ticker
    avail_cash = account.get('non_marginable_buying_power', account['cash']) if is_crypto else account['cash']

    primary_scan = evaluate_trade(
        primary_ticker, 
        account_equity=account['equity'], 
        available_cash=avail_cash,
        timeframe=timeframe
    )
    if primary_scan:
        latest_scans[primary_ticker] = primary_scan
    else:
        primary_scan = latest_scans.get(primary_ticker, {})
    sentiment_score = primary_scan.get('sentiment_score', 0)
    sentiment_confidence = primary_scan.get('sentiment_confidence', 0)

    if sentiment_score > 0.3:
        sentiment_label = "Bullish"
    elif sentiment_score < -0.3:
        sentiment_label = "Bearish"
    else:
        sentiment_label = "Neutral"

    # Format daily P/L
    pl = account.get('daily_pl', 0)
    pl_pct = account.get('daily_pl_pct', 0)
    pl_sign = "+" if pl >= 0 else ""

    return {
        # Portfolio Summary Cards
        "capital": f"${account['equity']:.2f}",
        "cash": f"${account['cash']:.2f}",
        "openPositions": str(len(positions)),
        "dailyPL": f"{pl_sign}${pl:.2f} ({pl_sign}{pl_pct:.1f}%)",
        "aiSentiment": f"{sentiment_label} ({sentiment_score})",
        "sentimentConfidence": sentiment_confidence,
        "sentimentSummary": primary_scan.get("sentiment_summary", ""),
        "tickerAmounts": config.TICKER_AMOUNTS,
        "simulation": account.get('simulation', True),

        # Detailed Data
        "positions": positions,
        "recentTrades": [_format_trade_for_ui(t) for t in trade_log[:20]],
        "executedTrades": [_format_trade_for_ui(t) for t in executed_trades],
        "watchlistScans": {
            ticker: _format_scan_for_ui(scan)
            for ticker, scan in latest_scans.items()
        },

        # Strategy Signals (primary ticker)
        "primaryTicker": primary_ticker,
        "signals": _format_scan_for_ui(primary_scan).get('signals', {}),
        "priceHistory": primary_scan.get('price_history', []),

        # Risk Management
        "risk": risk_mgr.get_risk_status(account['equity']),
        "ticker_settings": getattr(config, 'TICKER_SETTINGS', {}),

        # Bot Meta
        "botRunning": bot_running,
        "lastScan": last_scan_time or "Starting...",
        "indicator_settings": {k: getattr(config, k, True) for k in dir(config) if k.startswith("ENABLE_")},
        "strategyTimeframe": config.DEFAULT_TIMEFRAME,
        "simulation": account.get('simulation', True),
        "watchlist": config.WATCHLIST,
        "tradelist": config.TRADELIST,
        "scanInterval": config.SCAN_INTERVAL_SECONDS,
    }


@app.get("/api/scan/{ticker}")
def scan_ticker(ticker: str, timeframe: str = "5Min", user = Depends(verify_token)):
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker.upper()):
        return {"error": "Invalid ticker format"}
    """On-demand scan of a specific ticker."""
    account = broker.get_account_info()
    # Use settled cash (non-marginable) for crypto
    is_crypto = any(c in ticker.upper() for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in ticker.upper()
    # Use non_marginable_buying_power, but fallback to cash if it's 0
    avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']

    result = evaluate_trade(
        ticker.upper(), 
        account_equity=account['equity'], 
        available_cash=avail_cash,
        timeframe=timeframe
    )
    if result:
        latest_scans[ticker.upper()] = result
        return _format_scan_for_ui(result)
    return {"error": f"Could not analyze {ticker}"}


@app.post("/api/backtest")
async def run_backtest(data: dict, user = Depends(verify_token)):
    """
    Runs a historical backtest for a ticker.
    """
    ticker = data.get("ticker", "AAPL").upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 30))
    capital = float(data.get("capital", 1000.0))
    threshold = int(data.get("threshold", 5))
    sell_threshold = int(data.get("sell_threshold", 3))
    indicators = data.get("indicators", []) # List of names like ['RSI', 'MACD']
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # Backtester uses yfinance internally
    bt = Backtester(
        ticker=ticker,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        threshold=threshold,
        sell_threshold=sell_threshold,
        enabled_indicators=indicators
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
async def download_all_data(data: dict, user = Depends(verify_token)):
    """
    Downloads and caches all available history for a ticker across all timeframes.
    Also saves stock metadata (sector, market cap, etc.)
    """
    raw_ticker = str(data.get("ticker", "")).strip().upper()
    if not raw_ticker:
        return {"error": "Ticker required"}
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", raw_ticker):
        return {"error": "Invalid ticker format"}
    ticker = raw_ticker
    
    timeframes = [
        ("1Min", 7),
        ("5Min", 60),
        ("15Min", 60),
        ("30Min", 60),
        ("1Hour", 365),
        ("4Hour", 730),
        ("1Day", 1825)
    ]
    
    log = []
    end_date = datetime.now()
    
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
        
        # Security: Canonicalize path and verify it stays inside data/
        data_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "data"))
        if not os.path.exists(data_dir): os.makedirs(data_dir)
        
        info_path = os.path.realpath(os.path.join(data_dir, f"{ticker}_info.json"))
        if os.path.commonpath([data_dir, info_path]) != data_dir:
            return {"error": "Invalid ticker path"}
            
        with open(info_path, "w") as f:
            json.dump(info, f, indent=4)
        log.append("Metadata: Saved")
    except Exception as e:
        log.append(f"Metadata Error: {e}")
        
    return {"ticker": ticker, "status": log}


@app.get("/api/settings/indicators")
def get_indicator_settings():
    """Returns all indicator toggles grouped by category."""
    return {
        "Momentum": {
            "ENABLE_RSI": {"label": "RSI", "description": "Relative Strength Index — Overbought / Oversold", "enabled": getattr(config, "ENABLE_RSI", True)},
            "ENABLE_MACD": {"label": "MACD", "description": "Moving Average Convergence Divergence", "enabled": getattr(config, "ENABLE_MACD", True)},
        },
        "Trend": {
            "ENABLE_EMA": {"label": "EMA Cross", "description": "Exponential Moving Average Crossover (9/21)", "enabled": getattr(config, "ENABLE_EMA", True)},
            "ENABLE_SUPERTREND": {"label": "Supertrend", "description": "Supertrend Indicator (10, 3)", "enabled": getattr(config, "ENABLE_SUPERTREND", True)},
            "ENABLE_BOLLINGER": {"label": "Bollinger", "description": "Bollinger Bands (20, 2σ)", "enabled": getattr(config, "ENABLE_BOLLINGER", True)},
        },
        "Volume": {
            "ENABLE_VWAP": {"label": "VWAP", "description": "Volume Weighted Average Price", "enabled": getattr(config, "ENABLE_VWAP", True)},
        },
        "Custom": {
            "ENABLE_MYSTIC_PULSE": {"label": "Mystic Pulse", "description": "DMI-based Consecutive Trend Strength", "enabled": getattr(config, "ENABLE_MYSTIC_PULSE", True)},
            "ENABLE_AI_SENTIMENT": {"label": "AI Sentiment", "description": "Groq-powered News Sentiment Analysis", "enabled": getattr(config, "ENABLE_AI_SENTIMENT", True)},
            "ENABLE_CANDLE_PATTERNS": {"label": "Candle Patterns", "description": "Engulfing, Hammer, Shooting Star patterns", "enabled": getattr(config, "ENABLE_CANDLE_PATTERNS", True)},
        },
    }


@app.post("/api/settings/risk")
def update_risk_settings(settings: dict):
    """Updates risk management parameters."""
    for key, value in settings.items():
        if hasattr(config, key):
            # Convert percentage strings/ints to decimals if needed
            if key in ['MAX_DAILY_DRAWDOWN', 'RISK_PER_TRADE', 'MAX_POSITION_PCT']:
                # Assume value is 0-100 if it's > 1
                if isinstance(value, (int, float)) and value > 1:
                    value = value / 100.0
            
            setattr(config, key, value)
    
    _save_settings()
    print(f"[settings] Updated risk parameters: {', '.join(settings.keys())}")
    return {"status": "success", "settings": settings}


@app.post("/api/settings/ticker_amount")
def update_ticker_amount(data: dict, user = Depends(verify_token)):
    """Updates the allocated trade amount for a specific ticker."""
    ticker = data.get("ticker", "").upper()
    amount = data.get("amount")
    
    if ticker:
        if amount is None or amount == "":
            if ticker in config.TICKER_AMOUNTS:
                del config.TICKER_AMOUNTS[ticker]
        else:
            try:
                config.TICKER_AMOUNTS[ticker] = float(amount)
            except:
                return {"status": "error", "message": "Invalid amount"}
        
        _save_settings()
        print(f"[settings] {ticker}: Allocated trade amount updated")
        return {"status": "success", "ticker_amounts": config.TICKER_AMOUNTS}
    return {"status": "error", "message": "Ticker required"}


@app.post("/api/settings/timeframe")
def update_timeframe(data: dict):
    """Updates the default trading timeframe and triggers a re-scan."""
    global latest_scans
    new_tf = data.get("timeframe")
    if new_tf in ["1Min", "5Min", "15Min", "30Min", "1Hour", "4Hour", "1Day"]:
        config.DEFAULT_TIMEFRAME = new_tf
        # Clear stale scans and indicator cache
        latest_scans = {}
        from indicators import _data_cache
        _data_cache.clear()
        
        _save_settings()
        print(f"[settings] Global Timeframe synced to {new_tf}. Triggering immediate scan.")
        
        # Wake up the background loop
        if 'force_scan_trigger' in globals():
            force_scan_trigger.set()
        # or we can force one loop here:
        print(f"[settings] Timeframe changed to {new_tf}. Clearing cache.")
        
        return {"status": "success", "timeframe": new_tf}
    return {"status": "error", "message": "Invalid timeframe"}


@app.get("/api/watchlist")
def get_watchlist():
    """Returns the current watchlist."""
    return config.WATCHLIST


@app.post("/api/watchlist")
async def add_to_watchlist(data: dict):
    """Add a ticker to the watchlist."""
    ticker = data.get("ticker", "").upper()
    if ticker and ticker not in config.WATCHLIST:
        config.WATCHLIST.append(ticker)
        _save_settings()
        print(f"[settings] Added {ticker} to watchlist")
    return {"status": "success", "watchlist": config.WATCHLIST}


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str):
    """Remove a ticker from the watchlist."""
    ticker = ticker.upper()
    if ticker in config.WATCHLIST:
        config.WATCHLIST.remove(ticker)
        # Deactivate bot if removed from watchlist
        if ticker in config.TRADELIST:
            config.TRADELIST.remove(ticker)
            print(f"[settings] {ticker} removed from watchlist & deactivated")
        else:
            print(f"[settings] Removed {ticker} from watchlist")
            
        _save_settings()
        if 'force_scan_trigger' in globals():
            force_scan_trigger.set()
    return {"status": "success", "watchlist": config.WATCHLIST, "tradelist": config.TRADELIST}


@app.get("/api/tradelist")
def get_tradelist():
    """Returns the current active trade list."""
    return config.TRADELIST


@app.post("/api/tradelist")
async def add_to_tradelist(data: dict):
    """Add a ticker to the active trade list."""
    ticker = data.get("ticker", "").upper()
    if ticker and ticker not in config.TRADELIST:
        config.TRADELIST.append(ticker)
        # Ensure it's also in watchlist so we can see it
        if ticker not in config.WATCHLIST:
            config.WATCHLIST.append(ticker)
            print(f"[settings] Added {ticker} to watchlist & activated bot")
        else:
            print(f"[settings] Added {ticker} to active tradelist (Bot Activated)")
            
        _save_settings()
        if 'force_scan_trigger' in globals():
            force_scan_trigger.set()
    return {"status": "success", "tradelist": config.TRADELIST, "watchlist": config.WATCHLIST}


@app.delete("/api/tradelist/{ticker}")
async def remove_from_tradelist(ticker: str):
    """Remove a ticker from the active trade list."""
    ticker = ticker.upper()
    if ticker in config.TRADELIST:
        config.TRADELIST.remove(ticker)
        _save_settings()
        print(f"[settings] Removed {ticker} from active tradelist (Bot Deactivated)")
    return {"status": "success", "tradelist": config.TRADELIST}


def _save_settings():
    """Persist watchlist and tradelist to settings.json."""
    import json, os
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
    try:
        existing = {}
        if os.path.exists(settings_path):
            with open(settings_path, "r") as f:
                existing = json.load(f)

        existing["WATCHLIST"] = config.WATCHLIST
        existing["TRADELIST"] = config.TRADELIST
        existing["TICKER_SETTINGS"] = config.TICKER_SETTINGS
        existing["DEFAULT_TIMEFRAME"] = config.DEFAULT_TIMEFRAME
        existing["SCAN_INTERVAL_SECONDS"] = config.SCAN_INTERVAL_SECONDS

        with open(settings_path, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"[settings] Error saving settings: {e}")


@app.post("/api/settings/indicators")
async def update_indicators(updates: dict):
    """Toggle one or more indicators on or off. Instant in-memory + persists to settings.json."""
    import json, os

    # Update in memory immediately (instant)
    for k, v in updates.items():
        if hasattr(config, k) and k.startswith("ENABLE_"):
            setattr(config, k, bool(v))

    # Save to settings.json (does NOT trigger uvicorn reload)
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
    try:
        existing = {}
        if os.path.exists(settings_path):
            with open(settings_path, "r") as f:
                existing = json.load(f)

        existing.update({k: bool(v) for k, v in updates.items() if k.startswith("ENABLE_")})

        with open(settings_path, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"[settings] Error saving: {e}")

    print(f"[settings] Updated indicator toggles: {', '.join(updates.keys())}")
    return {"status": "success"}


@app.post("/api/settings/ticker")
async def update_ticker_settings(data: dict, user = Depends(verify_token)):
    """Update settings for a specific ticker."""
    ticker = data.get("ticker", "").upper()
    settings = data.get("settings", {})
    if not ticker: return {"status": "error"}

    if ticker not in config.TICKER_SETTINGS:
        config.TICKER_SETTINGS[ticker] = {}
    
    # Filter out null values to keep global defaults if not specified
    for k, v in settings.items():
        if v is not None:
            config.TICKER_SETTINGS[ticker][k] = v
        elif k in config.TICKER_SETTINGS[ticker]:
            del config.TICKER_SETTINGS[ticker][k]

    _save_settings()
    return {"status": "success"}

@app.delete("/api/settings/ticker/{ticker}")
async def reset_ticker_settings(ticker: str, user = Depends(verify_token)):
    """Reset a ticker to global defaults."""
    ticker = ticker.upper()
    if ticker in config.TICKER_SETTINGS:
        del config.TICKER_SETTINGS[ticker]
        _save_settings()
    return {"status": "success"}

def _load_saved_settings():
    """Load indicator toggles from settings.json on startup."""
    import json, os
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k == "WATCHLIST":
                    config.WATCHLIST = v
                elif k == "TRADELIST":
                    config.TRADELIST = v
                elif hasattr(config, k) and k.startswith("ENABLE_"):
                    setattr(config, k, bool(v))
            # Load Ticker Settings
            config.TICKER_SETTINGS = saved.get("TICKER_SETTINGS", {})
            config.DEFAULT_TIMEFRAME = saved.get("DEFAULT_TIMEFRAME", config.DEFAULT_TIMEFRAME)
            config.SCAN_INTERVAL_SECONDS = saved.get("SCAN_INTERVAL_SECONDS", config.SCAN_INTERVAL_SECONDS)
            
            print(f"[settings] Loaded {len(saved)} saved settings")
        except Exception as e:
            print(f"[settings] Error loading: {e}")


def _save_executed_trade(trade: dict):
    """Append a successful trade to trades.json."""
    import json, os
    trades_path = os.path.join(os.path.dirname(__file__), "trades.json")
    try:
        existing = []
        if os.path.exists(trades_path):
            with open(trades_path, "r") as f:
                existing = json.load(f)
        
        existing.insert(0, trade)
        # Keep last 1000 trades
        existing = existing[:1000]

        with open(trades_path, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"[trades] Error saving executed trade: {e}")


def _load_executed_trades():
    """Load persistent trade history on startup."""
    global executed_trades
    import json, os
    trades_path = os.path.join(os.path.dirname(__file__), "trades.json")
    if os.path.exists(trades_path):
        try:
            with open(trades_path, "r") as f:
                executed_trades = json.load(f)
            print(f"[trades] Loaded {len(executed_trades)} executed trades")
        except Exception as e:
            print(f"[trades] Error loading history: {e}")


# Load saved settings on import
_load_saved_settings()
_load_executed_trades()

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _format_trade_for_ui(trade: dict) -> dict:
    """Formats a trade decision for the frontend table."""
    return {
        "time": trade.get("time", ""),
        "action": trade.get("action", ""),
        "ticker": trade.get("ticker", ""),
        "price": trade.get("price", ""),
        "qty": trade.get("qty", "N/A"),
        "total_cost": trade.get("total_cost", 0),
        "fees": trade.get("fees", 0),
        "reason": trade.get("reason", ""),
        "sentiment_score": trade.get("sentiment_score", 0),
        "bullish_count": trade.get("bullish_count", 0),
        "bearish_count": trade.get("bearish_count", 0),
        "total_signals": trade.get("total_signals", 0),
    }


def _format_scan_for_ui(scan: dict) -> dict:
    """Formats a full scan result for the watchlist panel.
    Sends ALL signals with an 'enabled' flag so the UI can
    show disabled signals as greyed-out clickable cards."""

    SIGNAL_TO_TOGGLE = {
        'RSI': 'ENABLE_RSI',
        'MACD': 'ENABLE_MACD',
        'EMA Cross': 'ENABLE_EMA',
        'Supertrend': 'ENABLE_SUPERTREND',
        'Bollinger': 'ENABLE_BOLLINGER',
        'VWAP': 'ENABLE_VWAP',
        'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
        'AI Sentiment': 'ENABLE_AI_SENTIMENT',
        'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
    }

    raw_signals = scan.get("signals", {})
    all_signals = {}
    for name, data in raw_signals.items():
        toggle_key = SIGNAL_TO_TOGGLE.get(name, '')
        enabled = getattr(config, toggle_key, True) if toggle_key else True
        all_signals[name] = {**data, 'enabled': enabled, 'toggle_key': toggle_key}

    bullish = sum(1 for s in all_signals.values() if s.get('signal') == 'BULLISH' and s.get('enabled'))
    bearish = sum(1 for s in all_signals.values() if s.get('signal') == 'BEARISH' and s.get('enabled'))
    active_count = sum(1 for s in all_signals.values() if s.get('enabled'))

    return {
        "ticker": scan.get("ticker", ""),
        "price": scan.get("price", ""),
        "action": scan.get("action", "HOLD"),
        "reason": scan.get("reason", ""),
        "sentiment_score": scan.get("sentiment_score", 0),
        "sentiment_confidence": scan.get("sentiment_confidence", 0),
        "sentiment_summary": scan.get("sentiment_summary", ""),
        "bullish_count": bullish,
        "bearish_count": bearish,
        "total_signals": active_count,
        "signals": all_signals,
        "rsi": scan.get("rsi", 0),
        "atr": scan.get("atr", 0),
        "position_sizing": scan.get("position_sizing", {}),
        "price_history": scan.get("price_history", []),
    }