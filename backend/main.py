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
import pytz
import firebase_admin
from firebase_admin import auth, credentials, firestore
from cryptography.fernet import Fernet
import base64
import hashlib

from trader import evaluate_trade, get_risk_manager
from broker_factory import create_broker
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
# Initialize Firebase Admin with service account and singleton check
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
print("[main] Firestore client connected to database: trading-bot")

# ──────────────────────────────────────────────
# Secret Encryption Vault
# ──────────────────────────────────────────────
class Vault:
    """Handles bank-grade encryption for sensitive API keys."""
    def __init__(self):
        # Derive a stable master key from the project ID (Cloud Run provides GOOGLE_CLOUD_PROJECT)
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

async def save_config_to_cloud(api_key, secret_key, paper):
    """Saves encrypted keys to Firestore."""
    if not db: return
    try:
        data = {
            "api_key": vault.encrypt(api_key),
            "secret_key": vault.encrypt(secret_key),
            "paper": paper,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        db.collection("settings").document("alpaca").set(data)
        print("[vault] Config securely saved to Firestore.")
    except Exception as e:
        print(f"[vault] Error saving to cloud: {e}")

async def save_history_to_cloud():
    """Saves executed trades and scan history to Firestore."""
    if not db: return
    try:
        # Optimization: Remove heavy price_history from logs before saving to cloud
        # to prevent Firestore 1MB document limit issues
        def _strip_heavy_data(log_list):
            stripped = []
            for entry in log_list[:100]:
                clean_entry = entry.copy()
                if "price_history" in clean_entry:
                    del clean_entry["price_history"]
                stripped.append(clean_entry)
            return stripped

        cloud_scans = _strip_heavy_data(trade_log)
        cloud_trades = _strip_heavy_data(executed_trades)

        db.collection("history").document("scans").set({"data": cloud_scans})
        db.collection("history").document("trades").set({"data": cloud_trades})
    except Exception as e:
        print(f"[vault] Error saving history: {e}")

async def save_settings_to_cloud():
    """Saves all UI settings to Firestore."""
    if not db: return
    try:
        data = {
            "watchlist": config.WATCHLIST,
            "tradelist": config.TRADELIST,
            "strategy_timeframe": config.DEFAULT_TIMEFRAME,
            "ticker_settings": getattr(config, 'TICKER_SETTINGS', {}),
            "toggles": {k: getattr(config, k) for k in dir(config) if k.startswith("ENABLE_")}
        }
        db.collection("settings").document("ui").set(data)
    except Exception as e:
        print(f"[vault] Error saving settings: {e}")

async def load_all_from_cloud():
    """Restores everything from Firestore on startup."""
    if not db: return
    global executed_trades, trade_log
    try:
        try:
            print("[vault] Fetching Alpaca config from Firestore...")
            doc_alpaca = db.collection("settings").document("alpaca").get()
            if doc_alpaca.exists:
                data = doc_alpaca.to_dict()
                api_cipher = data.get("api_key", "")
                sec_cipher = data.get("secret_key", "")
                
                print(f"[vault] Decrypting keys (cipher lengths: {len(api_cipher)}, {len(sec_cipher)})...")
                decrypted_key = vault.decrypt(api_cipher)
                decrypted_secret = vault.decrypt(sec_cipher)
                
                if decrypted_key and decrypted_secret:
                    config.ALPACA_API_KEY = decrypted_key
                    config.ALPACA_SECRET_KEY = decrypted_secret
                    config.ALPACA_PAPER = data.get("paper", True)
                    print(f"[vault] Keys decrypted (ID starts with: {decrypted_key[:4]}...). Attempting broker connection...")
                    success = broker.connect(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, config.ALPACA_PAPER)
                    if success:
                        msg = "SUCCESS: Broker connected using cloud keys."
                    else:
                        msg = "FAILED: Broker could not connect using cloud keys."
                    print(f"[vault] {msg}")
                    cloud_restore_log.append(msg)
                else:
                    msg = "WARNING: Decryption produced empty results."
                    print(f"[vault] {msg}")
                    cloud_restore_log.append(msg)
            else:
                msg = "No Alpaca config found in Firestore."
                print(f"[vault] {msg}")
                cloud_restore_log.append(msg)
        except Exception as e:
            msg = f"ERROR: Could not fetch Alpaca config: {e}"
            print(f"[vault] {msg}")
            cloud_restore_log.append(msg)

        # 2. UI Settings
        try:
            doc_ui = db.collection("settings").document("ui").get()
            if doc_ui.exists:
                ui = doc_ui.to_dict()
                config.WATCHLIST = ui.get("watchlist", config.WATCHLIST)
                config.TRADELIST = ui.get("tradelist", config.TRADELIST)
                config.DEFAULT_TIMEFRAME = ui.get("strategy_timeframe", config.DEFAULT_TIMEFRAME)
                config.TICKER_SETTINGS = ui.get("ticker_settings", {})
                toggles = ui.get("toggles", {})
                for k, v in toggles.items():
                    if hasattr(config, k): setattr(config, k, v)
                print("[vault] Restored UI settings and watchlist.")
        except Exception as e:
            print(f"[vault] WARNING: Could not fetch UI settings from cloud: {e}")

        # 3. History
        try:
            doc_trades = db.collection("history").document("trades").get()
            if doc_trades.exists:
                executed_trades = doc_trades.to_dict().get("data", [])
            
            doc_scans = db.collection("history").document("scans").get()
            if doc_scans.exists:
                trade_log = doc_scans.to_dict().get("data", [])
        except Exception as e:
            print(f"[vault] WARNING: Could not fetch trade history from cloud: {e}")
            
        print(f"[vault] Cloud restore complete. Alpaca Linked: {bool(config.ALPACA_API_KEY)}")
        cloud_restore_log.append(f"Cloud restore complete at {get_now()}. Linked: {bool(config.ALPACA_API_KEY)}")
    except Exception as e:
        msg = f"FATAL: Cloud restore failed: {e}"
        print(f"[vault] {msg}")
        cloud_restore_log.append(msg)
        import traceback
        traceback.print_exc()

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

broker = create_broker()
trade_log = []      # In-memory history for all scans (HOLD/WATCH/BUY/SELL)
executed_trades = [] # Persistent history for successful BUY/SELL orders only
latest_scans = {}   # {ticker: latest_evaluation_result}
last_trade_timestamps = {}  # {ticker: datetime} — Cooldown tracking
bot_running = False
last_scan_time = None
force_scan_trigger = None
cloud_restore_log = [] # Capture startup events for debugging

class AlpacaConfig(BaseModel):
    api_key: str
    secret_key: str
    paper: bool = True

# ──────────────────────────────────────────────
# Global Bot State
# ──────────────────────────────────────────────

def get_now():
    """Returns current time in the configured timezone (Central Time by default)."""
    tz = pytz.timezone(config.TIMEZONE)
    return datetime.now(tz)


# ──────────────────────────────────────────────
# Background Scheduler
# ──────────────────────────────────────────────
async def trading_loop():
    """Background task that scans the watchlist on an interval."""
    global bot_running, last_scan_time, latest_scans, force_scan_trigger, trade_log, executed_trades
    if force_scan_trigger is None:
        force_scan_trigger = asyncio.Event()

    bot_running = True
    print(f"[scheduler] Trading loop started. Scanning every {config.SCAN_INTERVAL_SECONDS}s")

    while bot_running:
        try:
            account = broker.get_account_info()
            # BLOCKED: Only scan if Alpaca is linked
            if account.get('simulation', True):
                last_scan_time = "Disconnected (Alpaca Link Required)"
                await asyncio.sleep(10)
                continue
            equity = account['equity']

            # Set daily baseline if not set or suspiciously low (e.g., default 100.0)
            risk_mgr = get_risk_manager()
            if risk_mgr.daily_starting_equity is None or risk_mgr.daily_starting_equity < 1000.0:
                print(f"[scheduler] Setting baseline equity: ${equity:.2f}")
                risk_mgr.set_daily_equity(equity)
            
            # Get all open positions and pending orders once per cycle
            all_positions = broker.get_positions()
            all_orders = broker.get_open_orders()
            portfolio_count = len(all_positions)

            # ─── RECONCILIATION: Capture Stop Loss / Take Profit ───
            try:
                recent_fills = broker.get_recent_trades(limit=10)
                for fill in recent_fills:
                    # Check if this trade is already in our executed_trades log
                    already_logged = any(
                        (t.get('order', {}).get('order_id') == fill['id']) or 
                        (t.get('order_id') == fill['id']) 
                        for t in executed_trades
                    )
                    
                    if not already_logged and fill['side'] == 'SELL':
                        print(f"[scheduler] Reconciled automatic SELL for {fill['symbol']} at ${fill['price']}")
                        recon_entry = {
                            "time": fill['time'],
                            "action": "SELL",
                            "ticker": fill['symbol'],
                            "price": f"${fill['price']:.2f}",
                            "price_raw": fill['price'],
                            "qty": fill['qty'],
                            "reason": "✅ AUTO-EXIT (Stop Loss or Take Profit)",
                            "order": {"success": True, "order_id": fill['id'], "status": "filled"}
                        }
                        executed_trades.insert(0, recon_entry)
                        trade_log.insert(0, recon_entry)
                        await save_history_to_cloud()
            except Exception as e:
                print(f"[scheduler] Reconciliation error: {e}")

            print(f"\n{'='*60}")
            print(f"[scheduler] Starting scan cycle at {get_now()}")
            print(f"[scheduler] Active Bots (TRADELIST): {config.TRADELIST}")
            print(f"[scheduler] Watchlist: {config.WATCHLIST}")
            print(f"{'='*60}")
            for ticker in config.WATCHLIST:
                try:
                    # ── SAFETY CHECK 1: Per-Ticker Pause ──
                    t_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
                    if t_settings.get('paused', False):
                        # Bot is paused — skip evaluation entirely but keep in list
                        continue

                    # ── SAFETY CHECK 2: Market Hours Filter (Stocks only) ──
                    clean_ticker = ticker.upper().replace("/", "")
                    is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"]) or \
                                ticker.upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
                    
                    if not is_crypto and getattr(config, 'MARKET_HOURS_ONLY', True):
                        import pytz
                        et_now = datetime.now(pytz.timezone("US/Eastern"))
                        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
                        market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
                        if et_now < market_open or et_now > market_close or et_now.weekday() >= 5:
                            is_active = ticker in config.TRADELIST
                            log_prefix = "[BOT-SKIP]" if is_active else "[WATCH-SKIP]"
                            print(f"{log_prefix} {ticker}: Market Closed (9:30 AM - 4:00 PM ET)")
                            continue

                    # ── SAFETY CHECK 3: Cooldown Timer ──
                    cooldown_secs = getattr(config, 'TRADE_COOLDOWN_SECONDS', 300)
                    if cooldown_secs > 0 and ticker in last_trade_timestamps:
                        elapsed = (datetime.now(pytz.timezone(config.TIMEZONE)) - last_trade_timestamps[ticker]).total_seconds()
                        if elapsed < cooldown_secs:
                            continue

                    # 1. Determine available cash based on asset type
                    avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
                    
                    # 2. Evaluate Ticker (Indicators + AI Sentiment)
                    result = evaluate_trade(
                        ticker, 
                        account_equity=equity, 
                        available_cash=avail_cash
                    )
                    if result:
                        is_active = ticker in config.TRADELIST
                        log_prefix = "[BOT]" if is_active else "[WATCH]"
                        print(f"{log_prefix} {ticker}: ${result['price_raw']} | Signal: {result['action']} | Sent: {result['sentiment_score']}")
                        
                        latest_scans[ticker] = result
                        trade_executed = False

                        # 3. Auto-execute trades ONLY if ticker is in TRADELIST
                        if ticker in config.TRADELIST:
                            # Normalize symbol for matching
                            ticker_norm = ticker.replace("/", "").upper()
                            has_this_pos = any(
                                p['symbol'].replace("/", "").upper() == ticker_norm and float(p.get('qty', 0)) > 0 
                                for p in all_positions
                            )
                            has_open_order = any(
                                o['symbol'] == ticker_norm and o['side'].lower() == 'buy'
                                for o in all_orders
                            )

                            pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                            t_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
                            sell_mode = t_settings.get('sell_mode', 'indicator')
                            
                            # --- Sell Mode Logic ---
                            if has_this_pos and pos_info:
                                entry_price = float(pos_info.get('avg_entry_price', result['price_raw']))
                                current_price = result['price_raw']
                                
                                stop_mult = t_settings.get('atr_stop_multiplier', getattr(config, 'ATR_STOP_MULTIPLIER', 2.0))
                                tp_mult = t_settings.get('take_profit_multiplier', getattr(config, 'ATR_TAKE_PROFIT_MULTIPLIER', 4.0))
                                
                                stop_loss = entry_price - (result['atr'] * stop_mult)
                                take_profit = entry_price + (result['atr'] * tp_mult)
                                
                                if sell_mode == 'sltp' and result['action'] == 'SELL':
                                    result['action'] = 'HOLD'
                                    result['reason'] = 'Sell Signal Ignored (Fixed SL/TP Mode)'
                                    
                                if sell_mode in ['sltp', 'hybrid']:
                                    if current_price <= stop_loss:
                                        result['action'] = 'SELL'
                                        result['reason'] = f'Stop Loss Hit (${current_price:.2f} <= ${stop_loss:.2f})'
                                    elif current_price >= take_profit:
                                        result['action'] = 'SELL'
                                        result['reason'] = f'Take Profit Hit (${current_price:.2f} >= ${take_profit:.2f})'

                            if result['action'] == 'BUY':
                                sizing = result['position_sizing']
                                
                                # ── SAFETY CHECK 4: Max Open Positions ──
                                max_positions = getattr(config, 'MAX_OPEN_POSITIONS', 5)
                                if portfolio_count >= max_positions and not has_this_pos:
                                    result['action'] = 'HOLD'
                                    result['reason'] = f"Max positions reached ({portfolio_count}/{max_positions}). Buy signal blocked."
                                    print(f"[risk] {ticker}: Blocked BUY — max positions ({portfolio_count}/{max_positions})")
                                elif has_this_pos:
                                    # Block new buys if we already have a position for THIS stock
                                    result['action'] = 'HOLD'
                                    # Capture unrealized P/L for scan log
                                    if pos_info:
                                        result['pl'] = pos_info['unrealized_pl']
                                        result['pl_pct'] = pos_info['unrealized_pl_pct']
                                        result['qty'] = pos_info['qty']
                                    result['reason'] = "Position already open for this ticker."
                                elif has_open_order:
                                    # Block new buys if a buy order is already pending
                                    result['action'] = 'PENDING'
                                    result['reason'] = "Buy order already sent and pending execution."
                                elif sizing['notional'] > 0:
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
                                        # For notional orders, qty might be None or 0 initially, so estimate it
                                        order_qty = order_result.get('qty')
                                        if (order_qty is None or float(order_qty) == 0) and result['price_raw'] > 0:
                                            order_qty = sizing['notional'] / result['price_raw']
                                            
                                        result['qty'] = float(order_qty) if order_qty else 0
                                        result['total_cost'] = float(order_result.get('total_cost', sizing['notional']))
                                        result['fees'] = float(order_result.get('fees', 0))

                                        # Enhance reason with execution info — using 6 decimal places
                                        result['reason'] = f"✅ BOUGHT {result['qty']:.6f} shares at {result['price']}: {result['reason']}"
                                        print(f"[BOT-ACTION] BUY {ticker}: Executing trade for ${sizing['notional']:.2f}")
                                        last_trade_timestamps[ticker] = datetime.now(pytz.timezone(config.TIMEZONE))
                                elif has_this_pos:
                                    result['action'] = 'HOLD'
                                    # Capture unrealized P/L for scan log
                                    pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                                    if pos_info:
                                        result['pl'] = pos_info['unrealized_pl']
                                        result['pl_pct'] = pos_info['unrealized_pl_pct']
                                        result['qty'] = pos_info['qty']
                                    result['reason'] = "Position already open."

                            elif result['action'] == 'SELL':
                                if has_this_pos:
                                    # Get position info before closing for P/L calculation
                                    pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                                    
                                    order_result = broker.close_position(ticker)
                                    if order_result.get('success'):
                                        result['order'] = order_result
                                        trade_executed = True
                                        portfolio_count -= 1

                                        # Capture Detailed Receipt (Close position proceeds)
                                        # PRIORITIZE the quantity returned from the broker order
                                        order_qty = order_result.get('qty')
                                        if order_qty and float(order_qty) > 0:
                                            result['qty'] = float(order_qty)
                                        elif pos_info:
                                            result['qty'] = float(pos_info['qty'])
                                        else:
                                            result['qty'] = 0

                                        if pos_info:
                                            result['total_cost'] = float(pos_info['market_value'])
                                        else:
                                            result['total_cost'] = order_result.get('proceeds', 0)
                                        
                                        result['fees'] = float(order_result.get('fees', 0))

                                        # Calculate Realized P/L
                                        if pos_info:
                                            entry_price = float(pos_info['avg_price'])
                                            exit_price = result['price_raw']
                                            qty = float(order_result.get('qty')) if order_result.get('qty') else float(pos_info['qty'])
                                            pl = (exit_price - entry_price) * qty
                                            pl_pct = ((exit_price / entry_price) - 1) * 100
                                            result['pl'] = round(pl, 2)
                                            result['pl_pct'] = round(pl_pct, 2)

                                        # Enhance reason with execution info
                                        if pos_info:
                                            result['reason'] = f"✅ SOLD at {result['price']} (Entry: ${entry_price:.4f}): {result['reason']}"
                                        else:
                                            result['reason'] = f"✅ SOLD at {result['price']}: {result['reason']}"
                                        print(f"[BOT-ACTION] SELL {ticker}: Closed position, P/L: {result.get('pl', 0)}")
                                        last_trade_timestamps[ticker] = datetime.now(pytz.timezone(config.TIMEZONE))
                                    else:
                                        print(f"[trader] FAILED SELL {ticker}: {order_result.get('error', 'Unknown Error')}")
                                        result['reason'] = f"Sell order failed: {order_result.get('error', 'Broker Error')}"
                                else:
                                    print(f"[ACTION] SKIP SELL {ticker}: No position found in broker scan.")
                                    # Keep the action as SELL for the logs, but mark it as skipped
                                    result['reason'] = "Signal SELL ignored: No open position detected."
                        
                        # 4. Dashboard / Log Management
                        is_active = ticker in config.TRADELIST
                        result['log_type'] = "Active Bot" if is_active else "Watchlist"

                        # Use 1-minute resolution for the duplicate check key
                        # This prevents multiple logs for the same stock in the same minute
                        log_time_min = get_now().strftime("%Y-%m-%dT%H:%M")
                        log_key = f"{log_time_min}_{ticker}_{result['action']}"
                        
                        def _get_log_key(log_entry):
                            try:
                                t_str = datetime.fromisoformat(log_entry.get('time', '')).strftime('%Y-%m-%dT%H:%M')
                                return f"{t_str}_{log_entry.get('ticker','')}_{log_entry.get('action','')}"
                            except: return ""
                            
                        if not any(_get_log_key(log) == log_key for log in trade_log):
                            trade_log.insert(0, result)
                            
                            # If it was a real execution (must be an active bot), save to permanent ledger
                            if trade_executed and is_active:
                                executed_trades.insert(0, result)
                                await save_history_to_cloud()


                except Exception as e:
                    print(f"[scheduler] Error scanning {ticker}: {e}")

            # 4. Post-Cycle Cleanup & Sync
            # Bound history size
            if len(trade_log) > 200:
                trade_log = trade_log[:200]
            if len(executed_trades) > 200:
                executed_trades = executed_trades[:200]

            last_scan_time = get_now().isoformat()
            print(f"[scheduler] Cycle complete at {last_scan_time}\n{'='*60}")
            
            # Periodically sync scan log to cloud even if no trade executed
            # This ensures logs survive Cloud Run container restarts
            await save_history_to_cloud()

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
    await load_all_from_cloud()
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
async def update_alpaca_config(cfg: AlpacaConfig):
    success = broker.connect(cfg.api_key, cfg.secret_key, cfg.paper)
    if success:
        # Persist to cloud (Encrypted)
        await save_config_to_cloud(cfg.api_key, cfg.secret_key, cfg.paper)
        
        # Update config module state
        config.ALPACA_API_KEY = cfg.api_key
        config.ALPACA_SECRET_KEY = cfg.secret_key
        config.ALPACA_PAPER = cfg.paper
        
        return {"status": "success", "message": "Connected to Alpaca."}
    else:
        return {"status": "error", "message": "Failed to connect to Alpaca. Check your keys."}


@app.delete("/api/alpaca_config")
def unlink_alpaca():
    # Delete from cloud
    if db:
        db.collection("settings").document("alpaca").delete()
    
    # Reset broker to simulation
    broker.simulation_mode = True
    broker.client = None
    
    # Clear config module state
    config.ALPACA_API_KEY = ""
    config.ALPACA_SECRET_KEY = ""
    
    return {"status": "success", "message": "Alpaca account unlinked. Switched to simulation mode."}


@app.get("/api/dashboard")
def get_dashboard(ticker: str = None, timeframe: str = None):
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
    
    # ─── FRESH ANALYSIS ───
    # We no longer cache scans to ensure user always sees the absolute latest data.
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
    
    if not primary_scan:
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
        "positionsList": ", ".join(p['symbol'] for p in positions) if positions else "No positions",
        "dailyPL": f"{pl_sign}${pl:.2f} ({pl_sign}{pl_pct:.1f}%)",
        "totalProfit": f"{pl_sign}${pl:.2f} ({pl_sign}{pl_pct:.1f}%)",
        "aiSentiment": f"{sentiment_label} ({sentiment_score})",
        "sentiment_confidence": sentiment_confidence,
        "sentiment_summary": primary_scan.get("sentiment_summary", ""),
        "sentiment_key_factor": primary_scan.get("sentiment_key_factor", "N/A"),
        "tickerAmounts": config.TICKER_AMOUNTS,
        "ticker_settings": config.TICKER_SETTINGS,
        "simulation": account.get('simulation', True),
        "has_keys": bool(config.ALPACA_API_KEY),

        # Detailed Data
        "positions": positions,
        "recentTrades": [_format_trade_for_ui(t) for t in trade_log],
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
        "watchlist": config.WATCHLIST,
        "tradelist": config.TRADELIST,
        "scanInterval": config.SCAN_INTERVAL_SECONDS,

        # Safety Controls
        "maxOpenPositions": getattr(config, 'MAX_OPEN_POSITIONS', 5),
        "tradeCooldownSeconds": getattr(config, 'TRADE_COOLDOWN_SECONDS', 300),
        "marketHoursOnly": getattr(config, 'MARKET_HOURS_ONLY', True),

        "debug_logs": cloud_restore_log
    }


@app.get("/api/scan/{ticker}")
def scan_ticker(ticker: str, timeframe: str = "5Min"):
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker.upper()):
        return {"error": "Invalid ticker format"}
    """On-demand scan of a specific ticker."""
    account = broker.get_account_info()
    if account.get('simulation', True):
        return {"error": "Alpaca connection required for scanning."}
    # ─── CACHING ───
    now = datetime.now()
    cached_scan = latest_scans.get(ticker.upper())
    is_recent = False
    if cached_scan and cached_scan.get('timeframe') == timeframe:
        try:
            last_time = datetime.fromisoformat(cached_scan.get('time', ''))
            if (now - last_time).total_seconds() < 30:
                is_recent = True
        except: pass

    if is_recent:
        return _format_scan_for_ui(cached_scan)

    # Use settled cash (non-marginable) for crypto
    is_crypto = any(c in ticker.upper() for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in ticker.upper()
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
async def run_backtest(data: dict):
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
    
    end_date = get_now()
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
async def download_all_data(data: dict):
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
            "ENABLE_AI_SENTIMENT": {"label": "News Sentiment", "description": "Groq-powered News Sentiment Analysis", "enabled": getattr(config, "ENABLE_AI_SENTIMENT", True)},
            "ENABLE_CANDLE_PATTERNS": {"label": "Candle Patterns", "description": "Engulfing, Hammer, Shooting Star patterns", "enabled": getattr(config, "ENABLE_CANDLE_PATTERNS", True)},
        },
    }


@app.post("/api/settings/risk")
async def update_risk_settings(settings: dict):
    """Updates risk management parameters."""
    for key, value in settings.items():
        if hasattr(config, key):
            # Convert percentage strings/ints to decimals if needed
            if key in ['MAX_DAILY_DRAWDOWN', 'RISK_PER_TRADE', 'MAX_POSITION_PCT']:
                # Assume value is 0-100 if it's > 1
                if isinstance(value, (int, float)) and value > 1:
                    value = value / 100.0
            
            setattr(config, key, value)
    
    await save_settings_to_cloud()
    print(f"[settings] Updated risk parameters: {', '.join(settings.keys())}")
    return {"status": "success", "settings": settings}


@app.post("/api/settings/ticker_amount")
async def update_ticker_amount(data: dict):
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
        
        await save_settings_to_cloud()
        print(f"[settings] {ticker}: Allocated trade amount updated")
        return {"status": "success", "ticker_amounts": config.TICKER_AMOUNTS}
    return {"status": "error", "message": "Ticker required"}


@app.post("/api/settings/timeframe")
async def update_timeframe(data: dict):
    """Updates the default trading timeframe and triggers a re-scan."""
    global latest_scans
    new_tf = data.get("timeframe")
    if new_tf in ["1Min", "5Min", "15Min", "30Min", "1Hour", "4Hour", "1Day"]:
        config.DEFAULT_TIMEFRAME = new_tf
        # Clear stale scans and indicator cache
        latest_scans = {}
        from indicators import _data_cache
        _data_cache.clear()
        
        await save_settings_to_cloud()
        print(f"[settings] Global Timeframe synced to {new_tf}. Triggering immediate scan.")
        
        # Wake up the background loop
        if force_scan_trigger:
            force_scan_trigger.set()
        else:
            print("[settings] Trigger skip: loop not started.")
        
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
        await save_settings_to_cloud()
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
            
        await save_settings_to_cloud()
        if force_scan_trigger:
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
            
        await save_settings_to_cloud()
        if force_scan_trigger:
            force_scan_trigger.set()
    return {"status": "success", "tradelist": config.TRADELIST, "watchlist": config.WATCHLIST}


@app.get("/api/debug/history")
async def debug_history():
    return {
        "executed_trades_count": len(executed_trades),
        "trade_log_count": len(trade_log),
        "executed_trades": executed_trades[:10],
        "trade_log": trade_log[:10]
    }

@app.delete("/api/tradelist/{ticker}")
async def remove_from_tradelist(ticker: str):
    """Remove a ticker from the active trade list."""
    ticker = ticker.upper()
    if ticker in config.TRADELIST:
        config.TRADELIST.remove(ticker)
        await save_settings_to_cloud()
        print(f"[settings] Removed {ticker} from active tradelist (Bot Deactivated)")
    return {"status": "success", "tradelist": config.TRADELIST}


@app.get("/api/search/{query}")
def search_symbols(query: str):
    """Searches for tradeable assets using the modular broker."""
    if len(query) < 1:
        return []
    return broker.search_assets(query)


@app.post("/api/bots/create")
async def create_bot(data: dict):
    """Creates a new active bot with custom settings (adds to watchlist, tradelist, and TICKER_SETTINGS)."""
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
        
    # Apply custom settings
    if symbol not in config.TICKER_SETTINGS:
        config.TICKER_SETTINGS[symbol] = {}
        
    if "capital" in data:
        config.TICKER_SETTINGS[symbol]["amount"] = float(data["capital"])
        
    if "threshold" in data:
        config.TICKER_SETTINGS[symbol]["min_buy_signals"] = int(data["threshold"])
        config.TICKER_SETTINGS[symbol]["min_sell_signals"] = int(data["threshold"])
        
    if "sell_mode" in data:
        config.TICKER_SETTINGS[symbol]["sell_mode"] = data["sell_mode"]
        
    if "indicators" in data and isinstance(data["indicators"], list):
        config.TICKER_SETTINGS[symbol]["indicators"] = data["indicators"]

    # Ensure it's in watchlist to be scanned
    if symbol not in config.WATCHLIST:
        config.WATCHLIST.append(symbol)
        
    # Add to tradelist to activate the bot
    if symbol not in config.TRADELIST:
        config.TRADELIST.append(symbol)
        print(f"[settings] Launched new bot for {symbol} with custom settings")
        
    # Save settings to cloud (runs in background)
    asyncio.create_task(save_settings_to_cloud())
    
    # Trigger immediate scan so user sees results right away
    if force_scan_trigger:
        force_scan_trigger.set()
    
    return {"status": "success", "symbol": symbol, "watchlist": config.WATCHLIST, "tradelist": config.TRADELIST, "settings": config.TICKER_SETTINGS[symbol]}




# ──────────────────────────────────────────────
# Legacy persistence removed (Now using Cloud Vault)
# ──────────────────────────────────────────────


@app.post("/api/settings/indicators")
async def update_indicators(updates: dict):
    """Toggle one or more indicators on or off. Instant in-memory + persists to Firestore."""
    # Update in memory immediately (instant)
    for k, v in updates.items():
        if hasattr(config, k) and k.startswith("ENABLE_"):
            setattr(config, k, bool(v))

    # Save to Firestore (Does not trigger uvicorn reload)
    await save_settings_to_cloud()
    print(f"[settings] Updated indicator toggles: {', '.join(updates.keys())}")
    return {"status": "success"}


@app.post("/api/settings/ticker")
async def update_ticker_settings(data: dict):
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
    
    await save_settings_to_cloud()
    return {"status": "success"}

@app.delete("/api/settings/ticker/{ticker}")
async def reset_ticker_settings(ticker: str):
    """Reset a ticker to global defaults."""
    ticker = ticker.upper()
    if ticker in config.TICKER_SETTINGS:
        del config.TICKER_SETTINGS[ticker]
        await save_settings_to_cloud()
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


# Load initial settings (Now handled by lifespan on startup)

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
        "pl": trade.get("pl"),
        "pl_pct": trade.get("pl_pct"),
        "bullish_count": trade.get("bullish_count", 0),
        "bearish_count": trade.get("bearish_count", 0),
        "total_signals": trade.get("total_signals", 0),
        "timeframe": trade.get("timeframe", config.DEFAULT_TIMEFRAME),
        "log_type": trade.get("log_type", "Active Bot"),
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
        'News Sentiment': 'ENABLE_AI_SENTIMENT',
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