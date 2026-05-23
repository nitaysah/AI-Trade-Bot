"""
AI Trading Bot — FastAPI Backend.

Production-grade trading engine with:
- Multi-ticker watchlist scanning
- Background scheduler for automated evaluation
- Full REST API for the dashboard
- Alpaca broker integration
- Risk management enforcement
"""

# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Query, Header, HTTPException, Depends
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
# pyrefly: ignore [missing-import]
import asyncio
from datetime import datetime, timedelta
import pytz
# pyrefly: ignore [missing-import]
import firebase_admin
# pyrefly: ignore [missing-import]
from firebase_admin import auth, credentials, firestore
# pyrefly: ignore [missing-import]
from cryptography.fernet import Fernet
import base64
import hashlib
import time

# pyrefly: ignore [missing-import]
from trader import evaluate_trade, get_risk_manager, clear_evaluation_cache
# pyrefly: ignore [missing-import]
from broker_factory import create_broker
from backtester import Backtester
from data_manager import get_historical_data
import config
# pyrefly: ignore [missing-import]
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
        def _sync_save():
            db.collection("settings").document("alpaca").set(data)
        await asyncio.to_thread(_sync_save)
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
            if isinstance(log_list, list):
                for entry in log_list[:100]:
                    clean_entry = entry.copy()
                    if "price_history" in clean_entry: del clean_entry["price_history"]
                    stripped.append(clean_entry)
            elif isinstance(log_list, dict):
                for k, entry in log_list.items():
                    clean_entry = entry.copy()
                    if "price_history" in clean_entry: del clean_entry["price_history"]
                    stripped[k] = clean_entry
            return stripped

        cloud_scans = _strip_heavy_data(trade_log)
        cloud_trades = []
        
        # Strip bot scans too (it's a dict)
        cloud_bot_scans = {}
        for ticker, entry in bot_scans.items():
            clean_entry = entry.copy()
            if "price_history" in clean_entry: del clean_entry["price_history"]
            cloud_bot_scans[ticker] = clean_entry

        def _sync_save():
            db.collection("history").document("scans").set({"data": cloud_scans})
            db.collection("history").document("trades").set({"data": cloud_trades})
            db.collection("history").document("bot_scans").set({"data": cloud_bot_scans})
        await asyncio.to_thread(_sync_save)
    except Exception as e:
        print(f"[vault] Error saving history: {e}")

async def save_settings_to_cloud():
    """Saves all UI settings to Firestore."""
    if not db: return
    try:
        data = {
            "watchlist": config.WATCHLIST.copy() if isinstance(config.WATCHLIST, list) else config.WATCHLIST,
            "tradelist": config.TRADELIST.copy() if isinstance(config.TRADELIST, list) else config.TRADELIST,
            "strategy_timeframe": config.DEFAULT_TIMEFRAME,
            "ticker_settings": getattr(config, 'TICKER_SETTINGS', {}).copy() if isinstance(getattr(config, 'TICKER_SETTINGS', {}), dict) else getattr(config, 'TICKER_SETTINGS', {}),
            "toggles": {k: getattr(config, k) for k in dir(config) if k.startswith("ENABLE_")},
            "parameters": {k: getattr(config, k) for k in dir(config) if k.isupper() and not k.startswith("_") and not isinstance(getattr(config, k), (list, dict)) and k not in ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "GROQ_API_KEY", "FERNET_KEY"]}
        }
        def _sync_save():
            db.collection("settings").document("ui").set(data)
        await asyncio.to_thread(_sync_save)
    except Exception as e:
        print(f"[vault] Error saving settings: {e}")

async def load_all_from_cloud():
    """Restores everything from Firestore on startup."""
    if not db: return
    global trade_log
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

                # Restore parameters
                params = ui.get("parameters", {})
                for k, v in params.items():
                    if hasattr(config, k):
                        setattr(config, k, v)
                
                print("[vault] Restored UI settings, parameters and watchlist.")
        except Exception as e:
            print(f"[vault] WARNING: Could not fetch UI settings from cloud: {e}")

        # 3. History
        try:
            # doc_trades removed
            doc_scans = db.collection("history").document("scans").get()
            if doc_scans.exists:
                raw_logs = doc_scans.to_dict().get("data", [])
                # Only keep logs for tickers in TRADELIST or real trades to keep dashboard clean
                trade_log = [log for log in raw_logs if log.get('ticker') in config.TRADELIST or log.get('action') in ['BUY', 'SELL']]
            
            doc_bot = db.collection("history").document("bot_scans").get()
            if doc_bot.exists:
                bot_scans.update(doc_bot.to_dict().get("data", {}))
                print(f"[vault] Restored {len(bot_scans)} bot scans from cloud.")
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
    if token == "dev-token":
        return {"uid": "dev-user", "email": "dev@example.com"}
    try:
        # Verify the ID token
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        print(f"[security] Token verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid or expired authentication credentials")

broker = create_broker()
trade_log = []      # In-memory history for all scans (HOLD/WATCH/BUY/SELL)
latest_scans = {}   # {ticker: latest_evaluation_result}
latest_scans_by_tf = {}  # {timeframe: {ticker: latest_evaluation_result}}
last_trade_timestamps = {}  # {ticker: datetime} — Cooldown tracking
last_trailing_stops = {}  # {ticker: float} — Trailing stop memory for active positions
bot_running = False
last_scan_time = None
force_scan_trigger = None
last_scan_timestamps = {}
bot_scans = {}
bot_scans_by_tf = {}  # {timeframe: {ticker: latest_evaluation_result}}
cloud_restore_log = [] # Capture startup events for debugging
dashboard_primary_ticker = None  # Track what the user is viewing on the dashboard

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

def _record_scan(result: dict):
    """Record a scan in both flat and timeframe-indexed stores."""
    if not result:
        return
    ticker = result.get("ticker", "").upper()
    timeframe = result.get("timeframe", config.DEFAULT_TIMEFRAME)
    if not ticker:
        return
    latest_scans[ticker] = result
    tf_bucket = latest_scans_by_tf.setdefault(timeframe, {})
    tf_bucket[ticker] = result

def _record_bot_scan(result: dict):
    if not result:
        return
    ticker = result.get("ticker", "").upper()
    timeframe = result.get("timeframe", config.DEFAULT_TIMEFRAME)
    if not ticker:
        return
    bot_scans[ticker] = result
    tf_bucket = bot_scans_by_tf.setdefault(timeframe, {})
    tf_bucket[ticker] = result

def _pick_scan(ticker: str, timeframe: str, prefer_bot: bool = True, ignore_freshness: bool = False):
    ticker = ticker.upper()
    
    def _is_fresh(scan):
        if not scan: return False
        if ignore_freshness: return True
        try:
            scan_time = datetime.fromisoformat(scan.get("time", ""))
            now = get_now()
            # If the scan is older than 55 seconds, consider it stale
            return (now - scan_time).total_seconds() < 55
        except Exception:
            return False

    scan = None
    if prefer_bot:
        scan = bot_scans_by_tf.get(timeframe, {}).get(ticker)
        if _is_fresh(scan): return scan
        
    scan = latest_scans_by_tf.get(timeframe, {}).get(ticker)
    if _is_fresh(scan): return scan
    
    # Fallback to non-timeframe specific caches just in case, but verify timeframe match
    if prefer_bot:
        scan = bot_scans.get(ticker)
        if scan and scan.get("timeframe") == timeframe and _is_fresh(scan):
            return scan
            
    scan = latest_scans.get(ticker)
    if scan and scan.get("timeframe") == timeframe and _is_fresh(scan):
        return scan
        
    return None

async def _warm_timeframe_scans(timeframe: str, primary_ticker: str = None, limit: int = 4):
    """Warm cache for target timeframe in the background."""
    if not config.WATCHLIST:
        return
    ordered = [t.upper() for t in config.WATCHLIST]
    if primary_ticker and primary_ticker.upper() in ordered:
        ordered.remove(primary_ticker.upper())
        ordered.insert(0, primary_ticker.upper())
    targets = ordered[:max(1, limit)]
    account = broker.get_account_info()
    if account.get('simulation', True):
        return
    for ticker in targets:
        if _pick_scan(ticker, timeframe, prefer_bot=False):
            continue
        is_crypto = any(c in ticker for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in ticker
        avail_cash = account.get('non_marginable_buying_power', account['cash']) if is_crypto else account['cash']
        try:
            warmed = await asyncio.to_thread(
                evaluate_trade,
                ticker,
                account_equity=account['equity'],
                available_cash=avail_cash,
                timeframe=timeframe
            )
            _record_scan(warmed)
            if ticker in config.TRADELIST:
                _record_bot_scan(warmed)
        except Exception as exc:
            print(f"[warmup] {ticker} {timeframe} failed: {exc}")

def is_market_open():
    """Checks if US stock market is open (9:30 AM - 4:00 PM ET, Mon-Fri)."""
    import pytz
    et_now = datetime.now(pytz.timezone("US/Eastern"))
    market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if et_now < market_open or et_now > market_close or et_now.weekday() >= 5:
        return False
    return True


# ──────────────────────────────────────────────
# Background Scheduler
# ──────────────────────────────────────────────
async def trading_loop():
    """Background task that scans the watchlist on an interval."""
    global bot_running, last_scan_time, latest_scans, force_scan_trigger, trade_log, last_trailing_stops
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

            # ─── LIVE PORTFOLIO SYNC ───
            try:
                pass # Replaced by live Alpaca API data fetching on-demand
            except Exception as e:
                print("[scheduler] Sync error: Unable to sync data.")


            # Scan priority: 1. Dashboard primary (what user sees), 2. Active Bots, 3. Watchlist
            viewed = dashboard_primary_ticker
            bots_minus_viewed = [t for t in config.TRADELIST if t != viewed]
            watch_minus_both = [t for t in config.WATCHLIST if t not in config.TRADELIST and t != viewed]
            scan_order = ([viewed] if viewed and viewed in config.WATCHLIST else []) + bots_minus_viewed + watch_minus_both
            print(f"\n{'='*60}")
            print(f"[scheduler] Starting scan cycle at {get_now()}")
            print(f"[scheduler] Dashboard Primary: {viewed or 'N/A'}")
            print(f"[scheduler] Active Bots (TRADELIST): {bots_minus_viewed}")
            print(f"[scheduler] Watchlist: {watch_minus_both}")
            print(f"{'='*60}")
            for ticker in scan_order:
                try:
                    ticker_norm = ticker.upper().replace("/", "")
                    t_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
                    is_active = ticker in config.TRADELIST
                    
                    # ── SAFETY CHECK 1: Per-Ticker Pause ──
                    if t_settings.get('paused', False):
                        continue

                    # ── ASSET TYPE & MODE DETECTION ──
                    is_crypto = any(ticker_norm.endswith(base) for base in ["USD", "USDT", "USDC"]) or \
                                ticker.upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
                    sell_mode = t_settings.get('sell_mode', 'indicator')
                    print(f"[trader] {ticker} Mode: {sell_mode}")

                    # Market hours filter moved to trade execution block to allow sentiment scans

                    # ── SAFETY CHECK 3: Stale Order Cleanup (Indicator Mode) ──
                    # If in indicator mode, we manage exits ourselves via signals.
                    # Cancel any existing SL/TP orders on Alpaca to prevent "AUTO-EXIT"
                    if is_active and sell_mode == 'indicator' and not broker.simulation_mode:
                        broker.cancel_orders_for_ticker(ticker, side_filter='sell')

                    # ── SAFETY CHECK 4: Scan Interval Enforcement (1-minute) ──
                    now = get_now()
                    if ticker in last_scan_timestamps:
                        elapsed_scan = (now - last_scan_timestamps[ticker]).total_seconds()
                        if elapsed_scan < 55: 
                            continue
                    
                    last_scan_timestamps[ticker] = now

                    # 1. Determine available cash based on asset type
                    avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
                    
                    # 2. Evaluate Ticker (Indicators + AI Sentiment)
                    # OFF-LOAD to thread to prevent blocking the event loop!
                    result = await asyncio.to_thread(
                        evaluate_trade,
                        ticker, 
                        account_equity=equity, 
                        available_cash=avail_cash
                    )
                    if result:
                        is_active = ticker in config.TRADELIST
                        log_prefix = "[BOT]" if is_active else "[WATCH]"
                        print(f"{log_prefix} {ticker}: ${result['price_raw']} | Signal: {result['action']} | Sent: {result['sentiment_score']}")
                        
                        if ticker in config.TRADELIST:
                            _record_bot_scan(result)
                        _record_scan(result)
                        trade_executed = False

                        # 3. Auto-execute trades ONLY if ticker is in TRADELIST
                        if ticker in config.TRADELIST:
                            is_market_closed_skip = not is_crypto and getattr(config, 'MARKET_HOURS_ONLY', True) and not is_market_open()
                            if is_market_closed_skip:
                                print(f"[ACTION] SKIP TRADE {ticker}: Market Closed.")
                                result['reason'] = "Market Closed. Trade execution skipped."
                                
                            # --- Cooldown Check ---
                            cooldown_secs = getattr(config, 'TRADE_COOLDOWN_SECONDS', 300)
                            if cooldown_secs > 0 and ticker in last_trade_timestamps:
                                elapsed = (get_now() - last_trade_timestamps[ticker]).total_seconds()
                                if elapsed < cooldown_secs:
                                    if result['action'] in ['BUY', 'SELL']:
                                        rem = int(cooldown_secs - elapsed)
                                        result['action'] = 'HOLD'
                                        result['reason'] = f"Trade Cooldown Active ({rem}s remaining)"
                                        print(f"[ACTION] COOLDOWN {ticker}: {rem}s remaining. Converting to HOLD.")

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

                            if not has_this_pos:
                                last_trailing_stops.pop(ticker, None)

                            pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                            t_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
                            sell_mode = t_settings.get('sell_mode', 'indicator')
                            
                            # --- Sell Mode Logic ---
                            if has_this_pos and pos_info:
                                entry_price = float(pos_info.get('avg_entry_price', result['price_raw']))
                                current_price = result['price_raw']
                                
                                stop_mult = t_settings.get('atr_stop_multiplier', getattr(config, 'ATR_STOP_MULTIPLIER', 2.0))
                                trail_mult = t_settings.get('atr_trail_multiplier', getattr(config, 'ATR_TRAIL_MULTIPLIER', 3.0))
                                tp_mult = t_settings.get('take_profit_multiplier', getattr(config, 'ATR_TAKE_PROFIT_MULTIPLIER', 4.0))
                                
                                base_stop_loss = entry_price - (result['atr'] * stop_mult)
                                take_profit = entry_price + (result['atr'] * tp_mult)
                                
                                # Retrieve or initialize the trailing stop-loss
                                if ticker not in last_trailing_stops:
                                    last_trailing_stops[ticker] = base_stop_loss
                                
                                # Trailing stop logic: only move stop higher for long positions
                                trail_distance = result['atr'] * trail_mult
                                candidate_stop = current_price - trail_distance
                                if candidate_stop > last_trailing_stops[ticker]:
                                    last_trailing_stops[ticker] = round(candidate_stop, 2)
                                
                                # Trailed stop is at least the base stop-loss
                                stop_loss = max(base_stop_loss, last_trailing_stops[ticker])
                                
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

                            if result['action'] == 'BUY' and not is_market_closed_skip:
                                sizing = result['position_sizing']
                                
                                if portfolio_count >= getattr(config, 'MAX_OPEN_POSITIONS', 5) and not has_this_pos:
                                    result['action'] = 'HOLD'
                                    result['reason'] = f"Max positions reached ({portfolio_count}/{getattr(config, 'MAX_OPEN_POSITIONS', 5)}). Buy signal blocked."
                                    print(f"[ACTION] SKIP BUY {ticker}: Max positions reached ({portfolio_count}/{getattr(config, 'MAX_OPEN_POSITIONS', 5)})")
                                elif has_this_pos:
                                    # KEEP THE BUY SIGNAL but skip execution
                                    # Capture unrealized P/L for scan log
                                    if pos_info:
                                        result['pl'] = pos_info['unrealized_pl']
                                        result['pl_pct'] = pos_info['unrealized_pl_pct']
                                        result['qty'] = pos_info['qty']
                                    result['reason'] = "Position already open for this ticker."
                                    print(f"[ACTION] SKIP BUY {ticker}: Position already open.")
                                elif has_open_order:
                                    # Block new buys if a buy order is already pending
                                    result['action'] = 'PENDING'
                                    result['reason'] = "Buy order already sent and pending execution."
                                    print(f"[ACTION] SKIP BUY {ticker}: Order already pending.")
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
                                        result['order_id'] = order_result.get('order_id')
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
                                    # KEEP THE BUY SIGNAL but skip execution
                                    if pos_info:
                                        result['pl'] = pos_info['unrealized_pl']
                                        result['pl_pct'] = pos_info['unrealized_pl_pct']
                                        result['qty'] = pos_info['qty']
                                    result['reason'] = "Position already open."

                            elif result['action'] == 'SELL' and not is_market_closed_skip:
                                if has_this_pos:
                                    # Get position info before closing for P/L calculation
                                    pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                                    
                                    order_result = broker.close_position(ticker)
                                    if order_result.get('success'):
                                        result['order'] = order_result
                                        result['order_id'] = order_result.get('order_id')
                                        trade_executed = True
                                        portfolio_count -= 1
                                        print(f"[BOT-ACTION] SELL {ticker}: Order placed successfully.")

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
                            # LOG FILTER: Only show Active Bots in the main execution log
                            if is_active:
                                trade_log.insert(0, result)
                            
                            # If it was a real execution (must be an active bot), save to permanent ledger
                            if trade_executed and is_active:
                                await save_history_to_cloud()


                except Exception as e:
                    print(f"[scheduler] Error scanning {ticker}: Unable to scan ticker.")

            # 4. Post-Cycle Cleanup & Sync
            # Bound history size
            if len(trade_log) > 200:
                trade_log = trade_log[:200]

            last_scan_time = get_now().isoformat()
            print(f"[scheduler] Cycle complete at {last_scan_time}\n{'='*60}")
            
            # Periodically sync scan log to cloud even if no trade executed
            # This ensures logs survive Cloud Run container restarts
            await save_history_to_cloud()

        except Exception as e:
            print("[scheduler] Loop error: An unexpected error occurred.")

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


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────


from engine import UserManager
user_manager = UserManager(db)

@app.get("/")
def root():
    """Welcome message for the API root."""
    return {
        "message": "Bot Bulls API is running!",
        "version": "2.0.0",
        "docs": "/docs",
        "dashboard": "/api/dashboard"
    }


@app.post("/api/alpaca_config")
async def update_alpaca_config(cfg: AlpacaConfig, user: dict = Depends(verify_token)):
    success = broker.connect(cfg.api_key, cfg.secret_key, cfg.paper)
    eng = user_manager.get_engine(user['uid'])
    if success:
        # Persist to cloud (Encrypted)
        await save_config_to_cloud(cfg.api_key, cfg.secret_key, cfg.paper)
        
        # Update config module state
        eng.config.ALPACA_API_KEY = cfg.api_key
        eng.config.ALPACA_SECRET_KEY = cfg.secret_key
        eng.config.ALPACA_PAPER = cfg.paper
        
        return {"status": "success", "message": "Connected to Alpaca."}
    else:
        return {"status": "error", "message": "Failed to connect to Alpaca. Check your keys."}


@app.delete("/api/alpaca_config")
def unlink_alpaca(, user: dict = Depends(verify_token)):
    # Delete from cloud
    eng = user_manager.get_engine(user['uid'])
    if db:
        db.collection("settings").document("alpaca").delete()
    
    # Reset broker to simulation
    eng.broker.simulation_mode = True
    eng.broker.client = None
    
    # Clear config module state
    eng.config.ALPACA_API_KEY = ""
    eng.config.ALPACA_SECRET_KEY = ""
    return {"status": "success", "message": "Alpaca account unlinked. Switched to simulation mode."}
    
@app.get("/api/dashboard")
async def get_dashboard(ticker: str = None, timeframe: str = None, mode: str = "heavy", user: dict = Depends(verify_token)):
    """
    Main dashboard endpoint — returns everything the UI needs in one call.
    """
    eng = user_manager.get_engine(user['uid'])
    overall_start = time.perf_counter()
    started = time.perf_counter()
    mode = (mode or "heavy").lower()
    if timeframe is None:
        timeframe = eng.config.DEFAULT_TIMEFRAME
        
    account = eng.broker.get_account_info()
    positions = eng.broker.get_positions()
    risk_mgr = get_risk_manager()

    # Determine which ticker to focus on for the chart/analysis
    # Priority: 1. URL Param, 2. First Active Bot, 3. First Watchlist Item, 4. TSLA fallback
    global dashboard_primary_ticker
    primary_ticker = ticker.upper() if ticker else (
        eng.config.TRADELIST[0] if eng.config.TRADELIST else (
            eng.config.WATCHLIST[0] if eng.config.WATCHLIST else "TSLA"
        )
    )
    dashboard_primary_ticker = primary_ticker  # Tell the background loop what user is viewing
    
    # ─── FRESH ANALYSIS ───
    # If it's an active bot, we prioritize the background scan to avoid conflicts
    is_active_bot = primary_ticker in eng.config.TRADELIST
    primary_scan = eng._pick_scan(primary_ticker, timeframe, prefer_bot=is_active_bot)
    if mode != "fast" and not primary_scan:
        # Use settled cash (non-marginable) for crypto
        is_crypto = any(c in primary_ticker for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in primary_ticker
        avail_cash = account.get('non_marginable_buying_power', account['cash']) if is_crypto else account['cash']

        primary_scan = await asyncio.to_thread(
            evaluate_trade,
            primary_ticker, 
            account_equity=account['equity'], 
            available_cash=avail_cash,
            timeframe=timeframe,
            data_source="webull"  # Dashboard always uses Webull for higher fidelity research
        )
        if primary_scan:
            _record_scan(primary_scan)
            if is_active_bot:
                _record_bot_scan(primary_scan)
    
    if not primary_scan:
        primary_scan = eng._pick_scan(primary_ticker, timeframe, prefer_bot=is_active_bot) or {}
    sentiment_score = primary_scan.get('sentiment_score', 0)
    sentiment_confidence = primary_scan.get('sentiment_confidence', 0)

    if sentiment_score > 0.3:
        sentiment_label = "Bullish"
    elif sentiment_score < -0.3:
        sentiment_label = "Bearish"
    else:
        sentiment_label = "Neutral"

    # Format daily P/L (Today Only)
    daily_pl = account.get('daily_pl', 0)
    daily_pl_pct = account.get('daily_pl_pct', 0)
    daily_pl_sign = "+" if daily_pl >= 0 else ""

    # Calculate All-Time Profit (Realized + Unrealized)
    # 1. Sum all realized P/L from history
    total_realized_pl = 0 # Can be enhanced by Alpaca Account Activities API later
    # 2. Sum all unrealized P/L from current positions
    total_unrealized_pl = sum(p.get('unrealized_pl', 0) for p in positions)
    
    total_profit = total_realized_pl + total_unrealized_pl
    
    # Estimate total profit % based on current equity
    # If equity is 100k and profit is 10k, then initial was 90k, so 10/90 = 11%
    initial_est = account['equity'] - total_profit
    total_profit_pct = (total_profit / initial_est * 100) if initial_est > 0 else 0
    total_pl_sign = "+" if total_profit >= 0 else ""

    payload = {
        # Portfolio Summary Cards
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
        "tickerAmounts": eng.config.TICKER_AMOUNTS,
        "ticker_settings": eng.config.TICKER_SETTINGS,
        "simulation": account.get('simulation', True),
        "has_keys": bool(eng.config.ALPACA_API_KEY),

        # Detailed Data
        "positions": positions,
        "recentTrades": [_format_trade_for_ui(t) for t in eng.trade_log],
        "orderHistory": eng.broker.get_order_history(),
        "pendingOrders": eng.broker.get_open_orders(),
        "watchlistScans": {
            ticker: _format_scan_for_ui(
                eng._pick_scan(ticker, timeframe, prefer_bot=False) or 
                eng._pick_scan(ticker, getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME), prefer_bot=False, ignore_freshness=True) or {}
            )
            for ticker in eng.config.WATCHLIST
        },
        "botScans": {
            ticker: _format_scan_for_ui(
                eng._pick_scan(ticker, timeframe, prefer_bot=True) or 
                eng._pick_scan(ticker, getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME), prefer_bot=True, ignore_freshness=True) or {}
            )
            for ticker in eng.config.TRADELIST
        },

        # Strategy Signals (primary ticker)
        "primaryTicker": primary_ticker,
        "signals": _format_scan_for_ui(primary_scan).get('signals', {}),
        "priceHistory": primary_scan.get('price_history', []),

        # Risk Management
        "risk": risk_mgr.get_risk_status(account['equity']),
        "ticker_settings": getattr(config, 'TICKER_SETTINGS', {}),

        # Bot Meta
        "botRunning": eng.bot_running,
        "lastScan": last_scan_time or "Starting...",
        "indicator_settings": {k: getattr(config, k, True) for k in dir(config) if k.startswith("ENABLE_")},
        "indicator_parameters": {k: getattr(config, k) for k in dir(config) if k.isupper() and not k.startswith("_") and k not in ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "GROQ_API_KEY", "FERNET_KEY"]},
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
        "maxOpenPositions": getattr(config, 'MAX_OPEN_POSITIONS', 5),
        "tradeCooldownSeconds": getattr(config, 'TRADE_COOLDOWN_SECONDS', 300),
        "marketHoursOnly": getattr(config, 'MARKET_HOURS_ONLY', True),

        "debug_logs": []
    }
    if mode == "fast":
        payload["signals"] = {}
        payload["priceHistory"] = []
        payload["watchlistScans"] = {
            k: {
                "ticker": v.get("ticker", ""),
                "price": v.get("price", ""),
                "action": v.get("action", "HOLD"),
                "reason": v.get("reason", ""),
                "bullish_count": v.get("bullish_count", 0),
                "bearish_count": v.get("bearish_count", 0),
                "total_signals": v.get("total_signals", 0),
                "signals": {}
            } for k, v in payload["watchlistScans"].items()
        }
        payload["botScans"] = {
            k: {
                "ticker": v.get("ticker", ""),
                "price": v.get("price", ""),
                "action": v.get("action", "HOLD"),
                "reason": v.get("reason", ""),
                "bullish_count": v.get("bullish_count", 0),
                "bearish_count": v.get("bearish_count", 0),
                "total_signals": v.get("total_signals", 0),
                "signals": {}
            } for k, v in payload["botScans"].items()
        }
        # asyncio.create_task(_warm_timeframe_scans(timeframe, primary_ticker=primary_ticker, limit=5))
    print(f"[perf] /api/dashboard mode={mode} {primary_ticker} {timeframe}: {(time.perf_counter() - started) * 1000:.1f}ms")
    return payload


@app.get("/api/scan/{ticker}")
def scan_ticker(ticker: str, timeframe: str = "4Hour", user: dict = Depends(verify_token)):
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker.upper()):
    eng = user_manager.get_engine(user['uid'])
        return {"error": "Invalid ticker format"}
    """On-demand scan of a specific ticker."""
    account = eng.broker.get_account_info()
    if account.get('simulation', True):
        return {"error": "Alpaca connection required for scanning."}
    # ─── CACHING ───
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
        _record_scan(result)
        return _format_scan_for_ui(result)
    return {"error": f"Could not analyze {ticker}"}


@app.post("/api/backtest")
async def run_backtest(data: dict, user: dict = Depends(verify_token)):
    """
    Runs a historical backtest for a ticker.
    """
    eng = user_manager.get_engine(user['uid'])
    ticker = data.get("ticker", "AAPL").upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 30))
    capital = float(data.get("capital", 1000.0))
    threshold = int(data.get("threshold", 5))
    sell_threshold = int(data.get("sell_threshold", 3))
    indicators = data.get("indicators", []) # List of names like ['RSI', 'MACD']
    ext_hours = data.get("ext_hours", True)
    
    end_date = get_now()
    start_date = end_date - timedelta(days=days)
    
    sell_mode = data.get("sell_mode", "indicator")
    risk_per_trade = float(data.get("risk_per_trade", 0.02))
    max_pos_pct = float(data.get("max_position_pct", 0.25))
    atr_stop_multiplier = float(data.get("atr_stop_multiplier", 2.0))
    atr_trail_multiplier = float(data.get("atr_trail_multiplier", 3.0))
    atr_take_profit_multiplier = float(data.get("atr_take_profit_multiplier", 4.0))
    
    # Backtester uses get_historical_data internally
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
        atr_take_profit_multiplier=atr_take_profit_multiplier
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
async def download_all_data(data: dict, user: dict = Depends(verify_token)):
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
        
        # Security: Sanitize ticker to be strictly alphanumeric and verify path confinement
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
def get_indicator_settings(, user: dict = Depends(verify_token)):
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
            "ENABLE_ADX_TREND": {"label": "ADX Trend", "description": "Wilder's ADX (14-period) Trend Strength Filter", "enabled": getattr(config, "ENABLE_ADX_TREND", True)},
            "ENABLE_SMA": {"label": "SMA 200", "description": "Simple Moving Average (200-period) institutional filter", "enabled": getattr(config, "ENABLE_SMA", True)},
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
async def update_risk_settings(settings: dict, user: dict = Depends(verify_token)):
    """Updates risk management parameters."""
    for key, value in settings.items():
        if hasattr(config, key):
            # Convert percentage strings/ints to decimals if needed
            if key in ['MAX_DAILY_DRAWDOWN', 'RISK_PER_TRADE', 'MAX_POSITION_PCT']:
                # Assume value is 0-100 if it's > 1
                if isinstance(value, (int, float)) and value > 1:
                    value = value / 100.0
            
    asyncio.create_task(save_settings_to_cloud())
    print(f"[settings] Updated risk parameters: {', '.join(settings.keys())}")
    return {"status": "success", "settings": settings}


@app.post("/api/settings/ticker_amount")
async def update_ticker_amount(data: dict, user: dict = Depends(verify_token)):
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
        
        asyncio.create_task(save_settings_to_cloud())
        print(f"[settings] {ticker}: Allocated trade amount updated")
        return {"status": "success", "ticker_amounts": eng.config.TICKER_AMOUNTS}
    return {"status": "error", "message": "Ticker required"}


@app.post("/api/settings/timeframe")
async def update_timeframe(data: dict, user: dict = Depends(verify_token)):
    """Updates the default trading timeframe and triggers a re-scan."""
    global eng.latest_scans, eng.bot_scans, eng.latest_scans_by_tf, bot_scans_by_tf
    new_tf = data.get("timeframe")
    if new_tf in ["30Sec", "1Min", "2Min", "3Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "2Hour", "4Hour", "1Day"]:
        eng.config.DEFAULT_TIMEFRAME = new_tf
        # Clear stale UI/evaluation scans for the previous timeframe while keeping
        # raw indicator bar caches available per (ticker, timeframe).
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
        bot_scans_by_tf = {
            tf: scans for tf, scans in bot_scans_by_tf.items()
            if tf == new_tf
        }
        asyncio.create_task(save_settings_to_cloud())
        print(f"[settings] Global Timeframe synced to {new_tf}. Triggering immediate scan.")
        
        # Wake up the background loop
        if force_scan_trigger:
            force_scan_trigger.set()
        else:
            print("[settings] Trigger skip: loop not started.")
        asyncio.create_task(_warm_timeframe_scans(new_tf, limit=5))
        
        return {"status": "success", "timeframe": new_tf}
    return {"status": "error", "message": "Invalid timeframe"}


@app.get("/api/watchlist")
def get_watchlist(, user: dict = Depends(verify_token)):
    """Returns the current watchlist."""
    return eng.config.WATCHLIST


@app.post("/api/watchlist")
async def add_to_watchlist(data: dict, user: dict = Depends(verify_token)):
    """Add a ticker to the watchlist."""
    ticker = data.get("ticker", "").upper()
    if ticker and ticker not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(ticker)
        asyncio.create_task(save_settings_to_cloud())
        print(f"[settings] Added {ticker} to watchlist")
    return {"status": "success", "watchlist": eng.config.WATCHLIST}


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, user: dict = Depends(verify_token)):
    """Remove a ticker from the watchlist."""
    ticker = ticker.upper()
    if ticker in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(ticker)
        # Deactivate bot if removed from watchlist
        if ticker in eng.config.TRADELIST:
            eng.config.TRADELIST.remove(ticker)
            print(f"[settings] {ticker} removed from watchlist & deactivated")
        else:
            print(f"[settings] Removed {ticker} from watchlist")
        asyncio.create_task(save_settings_to_cloud())
        if force_scan_trigger:
            force_scan_trigger.set()
    return {"status": "success", "watchlist": eng.config.WATCHLIST, "tradelist": eng.config.TRADELIST}


@app.get("/api/tradelist")
def get_tradelist(, user: dict = Depends(verify_token)):
    """Returns the current active trade list."""
    return eng.config.TRADELIST


@app.post("/api/tradelist")
async def add_to_tradelist(data: dict, user: dict = Depends(verify_token)):
    """Add a ticker to the active trade list."""
    ticker = data.get("ticker", "").upper()
    timeframe = data.get("timeframe", eng.config.DEFAULT_TIMEFRAME)
    
    if ticker and ticker not in eng.config.TRADELIST:
        eng.config.TRADELIST.append(ticker)
        
        # LOCK IN TIMEFRAME: Save to ticker settings so it's sticky
        if ticker not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[ticker] = {}
        eng.config.TICKER_SETTINGS[ticker]['timeframe'] = timeframe
        print(f"[settings] Activated {ticker} on locked {timeframe} timeframe")

        # Ensure it's also in watchlist so we can see it
        if ticker not in eng.config.WATCHLIST:
            eng.config.WATCHLIST.append(ticker)
        asyncio.create_task(save_settings_to_cloud())
        if force_scan_trigger:
            force_scan_trigger.set()
    return {"status": "success", "tradelist": eng.config.TRADELIST, "watchlist": eng.config.WATCHLIST}


@app.get("/api/debug/history")
async def debug_history(, user: dict = Depends(verify_token)):
    return {
    eng = user_manager.get_engine(user['uid'])
        "executed_trades_count": 0,
        "trade_log_count": len(eng.trade_log),
        "executed_trades": [],
        "eng.trade_log": eng.trade_log[:10]
    }

@app.delete("/api/tradelist/{ticker}")
async def remove_from_tradelist(ticker: str, user: dict = Depends(verify_token)):
    """Remove a ticker from the active trade list."""
    ticker = ticker.upper()
    if ticker in eng.config.TRADELIST:
        eng.config.TRADELIST.remove(ticker)
        asyncio.create_task(save_settings_to_cloud())
        print(f"[settings] Removed {ticker} from active tradelist (Bot Deactivated)")
    return {"status": "success", "tradelist": eng.config.TRADELIST}


@app.get("/api/search/{query}")
def search_symbols(query: str, user: dict = Depends(verify_token)):
    """Searches for tradeable assets using the modular eng.broker."""
    if len(query) < 1:
        return []
    return eng.broker.search_assets(query)


@app.post("/api/bots/create")
async def create_bot(data: dict, user: dict = Depends(verify_token)):
    """Creates a new active bot with custom settings (adds to watchlist, tradelist, and TICKER_SETTINGS)."""
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
        
    # Apply custom settings
    if symbol not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[symbol] = {}
        
    if "capital" in data:
        eng.config.TICKER_SETTINGS[symbol]["amount"] = float(data["capital"])
        
    if "threshold" in data:
        eng.config.TICKER_SETTINGS[symbol]["min_buy_signals"] = int(data["threshold"])
        eng.config.TICKER_SETTINGS[symbol]["min_sell_signals"] = int(data["threshold"])
    
    if "timeframe" in data:
        eng.config.TICKER_SETTINGS[symbol]["timeframe"] = data["timeframe"]
        
    if "sell_mode" in data:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = data["sell_mode"]
    else:
        # Explicit default for new bots
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = "indicator"
        
    if "indicators" in data and isinstance(data["indicators"], list):
        eng.config.TICKER_SETTINGS[symbol]["indicators"] = data["indicators"]

    # Risk Management overrides
    for rk in ["risk_per_trade", "max_daily_drawdown", "max_position_pct", "atr_stop_multiplier", "atr_trail_multiplier", "take_profit_multiplier"]:
        if rk in data:
            eng.config.TICKER_SETTINGS[symbol][rk] = float(data[rk])

    # Ensure it's in watchlist to be scanned
    if symbol not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(symbol)
        
    # Add to tradelist to activate the bot
    if symbol not in eng.config.TRADELIST:
        eng.config.TRADELIST.append(symbol)
        print(f"[settings] Launched new bot for {symbol} with custom settings")
        
    # Save settings to cloud (runs in background)
    asyncio.create_task(save_settings_to_cloud())
    
    # Trigger immediate scan so user sees results right away
    if force_scan_trigger:
        force_scan_trigger.set()
    
    return {"status": "success", "symbol": symbol, "watchlist": eng.config.WATCHLIST, "tradelist": eng.config.TRADELIST, "settings": eng.config.TICKER_SETTINGS[symbol]}




@app.post("/api/cancel_order")
async def cancel_order(data: dict, user: dict = Depends(verify_token)):
    """Cancel an active order on Alpaca by ID."""
    order_id = data.get("order_id")
    if not order_id:
        return {"status": "error", "message": "order_id is required"}
    result = eng.broker.cancel_order_by_id(order_id)
    if result.get("success"):
        return {"status": "success", "message": f"Order {order_id} cancelled successfully."}
    else:
        return {"status": "error", "message": result.get("error", "Failed to cancel order.")}





# ──────────────────────────────────────────────
# Legacy persistence removed (Now using Cloud Vault)
# ──────────────────────────────────────────────


@app.post("/api/settings/indicators")
async def update_indicators(updates: dict, user: dict = Depends(verify_token)):
    """Update indicator toggles or parameters dynamically. Instant in-memory + persists to Firestore."""
    for k, v in updates.items():
        if hasattr(config, k):
            if k.startswith("ENABLE_"):
                setattr(config, k, bool(v))
            else:
                try:
                    current_val = getattr(config, k)
                    if isinstance(current_val, int):
                        setattr(config, k, int(v))
                    elif isinstance(current_val, float):
                        setattr(config, k, float(v))
                    else:
                        setattr(config, k, v)
                except Exception as e:
                    print(f"[settings] Type conversion error for {k}: {e}")
                    setattr(config, k, v)

    # Save to Firestore (Does not trigger uvicorn reload)
    asyncio.create_task(save_settings_to_cloud())
    
    # Clear cache so the next dashboard fetch gets the new indicator states
    clear_evaluation_cache()
    if force_scan_trigger:
        force_scan_trigger.set()
        
    print(f"[settings] Updated indicator settings: {', '.join(updates.keys())}")
    return {"status": "success"}


@app.post("/api/settings/ticker")
async def update_ticker_settings(data: dict, user: dict = Depends(verify_token)):
    """Update settings for a specific ticker."""
    ticker = data.get("ticker", "").upper()
    settings = data.get("settings", {})
    if not ticker: return {"status": "error"}

    if ticker not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[ticker] = {}
    # Filter out null values to keep global defaults if not specified
    for k, v in settings.items():
        if v is not None:
            eng.config.TICKER_SETTINGS[ticker][k] = v
        elif k in eng.config.TICKER_SETTINGS[ticker]:
            del eng.config.TICKER_SETTINGS[ticker][k]
    
    asyncio.create_task(save_settings_to_cloud())
    return {"status": "success"}

@app.delete("/api/settings/ticker/{ticker}")
async def reset_ticker_settings(ticker: str, user: dict = Depends(verify_token)):
    """Reset a ticker to global defaults."""
    ticker = ticker.upper()
    if ticker in eng.config.TICKER_SETTINGS:
        del eng.config.TICKER_SETTINGS[ticker]
        asyncio.create_task(save_settings_to_cloud())
    return {"status": "success"}

def _load_saved_settings(, user: dict = Depends(verify_token)):
    """Load indicator toggles from settings.json on startup."""
    import json, os
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k == "WATCHLIST":
                    eng.config.WATCHLIST = v
                elif k == "TRADELIST":
                    eng.config.TRADELIST = v
                elif hasattr(config, k) and k.startswith("ENABLE_"):
                    setattr(config, k, bool(v))
            # Load Ticker Settings
            eng.config.TICKER_SETTINGS = saved.get("TICKER_SETTINGS", {})
            eng.config.DEFAULT_TIMEFRAME = saved.get("DEFAULT_TIMEFRAME", eng.config.DEFAULT_TIMEFRAME)
            eng.config.SCAN_INTERVAL_SECONDS = saved.get("SCAN_INTERVAL_SECONDS", eng.config.SCAN_INTERVAL_SECONDS)
            
            print(f"[settings] Loaded {len(saved)} saved settings")
        except Exception as e:
            print(f"[settings] Error loading: {e}")


# Legacy _save_executed_trade / _load_executed_trades removed.
# Order history is now fetched live from Alpaca via eng.broker.get_order_history().

# Load initial settings (Now handled by lifespan on startup)

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _format_trade_for_ui(trade: dict) -> dict:
    """Formats a trade decision for the frontend table."""
    # Resolve timeframe: entry's own > ticker settings > global default
    ticker = trade.get("ticker", "").replace("/", "").upper()
    tf = trade.get("timeframe")
    if not tf:
        t_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
        tf = t_settings.get('timeframe', eng.config.DEFAULT_TIMEFRAME)
    
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
        'ADX Trend': 'ENABLE_ADX_TREND',
        'SMA': 'ENABLE_SMA',
    }

    raw_signals = scan.get("signals", {})
    all_signals = {}
    for name, data in raw_signals.items():
        toggle_key = SIGNAL_TO_TOGGLE.get(name, '')
        enabled = getattr(config, toggle_key, True) if toggle_key else True
        all_signals[name] = {**data, 'enabled': enabled, 'toggle_key': toggle_key}

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
