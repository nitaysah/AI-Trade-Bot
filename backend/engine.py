import asyncio
from datetime import datetime
import pytz
import time

from broker_factory import create_broker
from trader import evaluate_trade
from firebase_admin import firestore
import config as global_config
from user_config import UserConfig, set_user_config

class UserEngine:
    def __init__(self, uid: str, db):
        self.uid = uid
        self.db = db
        self.config = UserConfig(uid)
        self.broker = create_broker()
        
        self.trade_log = []
        self.latest_scans = {}
        self.latest_scans_by_tf = {}
        self.last_trade_timestamps = {}
        self.last_trailing_stops = {}
        self.bot_scans = {}
        self.bot_scans_by_tf = {}
        self.last_scan_timestamps = {}
        
        self.bot_running = False
        self.task = None
        self.force_scan_trigger = asyncio.Event()
        self.last_scan_time = None
        self.dashboard_primary_ticker = None
        
    def get_now(self):
        tz = pytz.timezone(self.config.TIMEZONE if hasattr(self.config, 'TIMEZONE') else 'US/Central')
        return datetime.now(tz)
        
    async def save_settings(self):
        if not self.db: return
        try:
            data = {
                "watchlist": self.config.WATCHLIST,
                "tradelist": self.config.TRADELIST,
                "strategy_timeframe": self.config.DEFAULT_TIMEFRAME,
                "ticker_settings": self.config.TICKER_SETTINGS,
                "toggles": self.config.toggles,
                "parameters": self.config.parameters
            }
            def _sync_save():
                self.db.collection("users").document(self.uid).collection("settings").document("ui").set(data)
            await asyncio.to_thread(_sync_save)
        except Exception as e:
            print(f"[engine-{self.uid}] Error saving settings: {e}")

    async def save_history(self):
        if not self.db: return
        try:
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

            cloud_scans = _strip_heavy_data(self.trade_log)
            
            cloud_bot_scans = {}
            for ticker, entry in self.bot_scans.items():
                clean_entry = entry.copy()
                if "price_history" in clean_entry: del clean_entry["price_history"]
                cloud_bot_scans[ticker] = clean_entry

            def _sync_save():
                ref = self.db.collection("users").document(self.uid).collection("history")
                ref.document("scans").set({"data": cloud_scans})
                ref.document("bot_scans").set({"data": cloud_bot_scans})
            await asyncio.to_thread(_sync_save)
        except Exception as e:
            print(f"[engine-{self.uid}] Error saving history: {e}")

    async def load_from_cloud(self):
        if not self.db: return
        try:
            # Load Alpaca Settings
            doc_alpaca = self.db.collection("users").document(self.uid).collection("settings").document("alpaca").get()
            if doc_alpaca.exists:
                data = doc_alpaca.to_dict()
                from main import vault
                api_cipher = data.get("api_key", "")
                sec_cipher = data.get("secret_key", "")
                
                decrypted_key = vault.decrypt(api_cipher)
                decrypted_secret = vault.decrypt(sec_cipher)
                
                if decrypted_key and decrypted_secret:
                    self.config.ALPACA_API_KEY = decrypted_key
                    self.config.ALPACA_SECRET_KEY = decrypted_secret
                    self.config.ALPACA_PAPER = data.get("paper", True)
                    self.broker.connect(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY, self.config.ALPACA_PAPER)

            # Load UI Settings
            doc_ui = self.db.collection("users").document(self.uid).collection("settings").document("ui").get()
            if doc_ui.exists:
                ui = doc_ui.to_dict()
                self.config.WATCHLIST = ui.get("watchlist", self.config.WATCHLIST)
                self.config.TRADELIST = ui.get("tradelist", self.config.TRADELIST)
                self.config.DEFAULT_TIMEFRAME = ui.get("strategy_timeframe", self.config.DEFAULT_TIMEFRAME)
                self.config.TICKER_SETTINGS = ui.get("ticker_settings", {})
                self.config.toggles.update(ui.get("toggles", {}))
                self.config.parameters.update(ui.get("parameters", {}))
                
            # Load History
            doc_scans = self.db.collection("users").document(self.uid).collection("history").document("scans").get()
            if doc_scans.exists:
                raw_logs = doc_scans.to_dict().get("data", [])
                self.trade_log = [log for log in raw_logs if log.get('ticker') in self.config.TRADELIST or log.get('action') in ['BUY', 'SELL']]
                
            doc_bot = self.db.collection("users").document(self.uid).collection("history").document("bot_scans").get()
            if doc_bot.exists:
                self.bot_scans.update(doc_bot.to_dict().get("data", {}))
                
        except Exception as e:
            print(f"[engine-{self.uid}] Load error: {e}")

    def _record_scan(self, result: dict):
        if not result: return
        ticker = result.get("ticker", "").upper()
        timeframe = result.get("timeframe", self.config.DEFAULT_TIMEFRAME)
        if not ticker: return
        self.latest_scans[ticker] = result
        tf_bucket = self.latest_scans_by_tf.setdefault(timeframe, {})
        tf_bucket[ticker] = result

    def _record_bot_scan(self, result: dict):
        if not result: return
        ticker = result.get("ticker", "").upper()
        timeframe = result.get("timeframe", self.config.DEFAULT_TIMEFRAME)
        if not ticker: return
        self.bot_scans[ticker] = result
        tf_bucket = self.bot_scans_by_tf.setdefault(timeframe, {})
        tf_bucket[ticker] = result

    def _pick_scan(self, ticker: str, timeframe: str, prefer_bot: bool = True, ignore_freshness: bool = False):
        ticker = ticker.upper()
        
        def _is_fresh(scan):
            if not scan: return False
            if ignore_freshness: return True
            try:
                scan_time = datetime.fromisoformat(scan.get("time", ""))
                now = self.get_now()
                return (now - scan_time).total_seconds() < 55
            except Exception:
                return False

        scan = None
        if prefer_bot:
            scan = self.bot_scans_by_tf.get(timeframe, {}).get(ticker)
            if _is_fresh(scan): return scan
            
        scan = self.latest_scans_by_tf.get(timeframe, {}).get(ticker)
        if _is_fresh(scan): return scan
        
        # Fallback to non-timeframe specific caches just in case, but verify timeframe match
        if prefer_bot:
            scan = self.bot_scans.get(ticker)
            if scan and scan.get("timeframe") == timeframe and _is_fresh(scan):
                return scan
                
        scan = self.latest_scans.get(ticker)
        if scan and scan.get("timeframe") == timeframe and _is_fresh(scan):
            return scan
            
        return None

    def is_market_open(self):
        import pytz
        et_now = datetime.now(pytz.timezone("US/Eastern"))
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
        if et_now < market_open or et_now > market_close or et_now.weekday() >= 5:
            return False
        return True

    async def _warm_timeframe_scans(self, timeframe: str, primary_ticker: str = None, limit: int = 4):
        set_user_config(self.config)
        if not self.config.WATCHLIST: return
        ordered = [t.upper() for t in self.config.WATCHLIST]
        if primary_ticker and primary_ticker.upper() in ordered:
            ordered.remove(primary_ticker.upper())
            ordered.insert(0, primary_ticker.upper())
        targets = ordered[:max(1, limit)]
        account = self.broker.get_account_info()
        if account.get('simulation', True): return
        
        for ticker in targets:
            try:
                is_crypto = any(c in ticker for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in ticker
                avail_cash = account.get('non_marginable_buying_power', account['cash']) if is_crypto else account['cash']
                warmed = await asyncio.to_thread(
                    evaluate_trade,
                    ticker,
                    account_equity=account['equity'],
                    available_cash=avail_cash,
                    timeframe=timeframe
                )
                self._record_scan(warmed)
                if ticker in self.config.TRADELIST:
                    self._record_bot_scan(warmed)
            except Exception as exc:
                print(f"[warmup-{self.uid}] {ticker} {timeframe} failed: {exc}")


    async def trading_loop(self):
        self.bot_running = True
        print(f"[scheduler-{self.uid}] Trading loop started.")
        while self.bot_running:
            set_user_config(self.config)
            try:
                account = self.broker.get_account_info()
                if account.get('simulation', True):
                    self.last_scan_time = "Disconnected (Alpaca Link Required)"
                    await asyncio.sleep(10)
                    continue
                equity = account['equity']
                
                from trader import get_risk_manager
                risk_mgr = get_risk_manager()
                
                # Reset equity at the start of a new day, or if it's completely missing
                now_date = self.get_now().date()
                if not hasattr(self, 'last_equity_date') or self.last_equity_date != now_date or risk_mgr.daily_starting_equity is None or risk_mgr.daily_starting_equity <= 0:
                    risk_mgr.set_daily_equity(equity)
                    self.last_equity_date = now_date
                
                # Detect massive equity jumps (e.g., switching from $100k Paper to $10k Live)
                elif abs(risk_mgr.daily_starting_equity - equity) / risk_mgr.daily_starting_equity > 0.50:
                    print(f"[risk-{self.uid}] Massive equity change detected (${risk_mgr.daily_starting_equity} -> ${equity}). Resetting daily baseline to prevent false halt.")
                    risk_mgr.set_daily_equity(equity)

                all_positions = self.broker.get_positions()
                all_orders = self.broker.get_open_orders()
                portfolio_count = len(all_positions)

                viewed = self.dashboard_primary_ticker
                bots_minus_viewed = [t for t in self.config.TRADELIST if t != viewed]
                watch_minus_both = [t for t in self.config.WATCHLIST if t not in self.config.TRADELIST and t != viewed]
                scan_order = ([viewed] if viewed and viewed in self.config.WATCHLIST else []) + bots_minus_viewed + watch_minus_both
                
                for ticker in scan_order:
                    try:
                        ticker_norm = ticker.upper().replace("/", "")
                        t_settings = self.config.TICKER_SETTINGS.get(ticker, {})
                        is_active = ticker in self.config.TRADELIST
                        
                        if t_settings.get('paused', False):
                            continue

                        is_crypto = any(ticker_norm.endswith(base) for base in ["USD", "USDT", "USDC"]) or ticker.upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
                        sell_mode = t_settings.get('sell_mode', 'indicator')

                        if is_active and sell_mode == 'indicator' and not self.broker.simulation_mode:
                            self.broker.cancel_orders_for_ticker(ticker, side_filter='sell')

                        now = self.get_now()
                        if ticker in self.last_scan_timestamps:
                            if (now - self.last_scan_timestamps[ticker]).total_seconds() < 55:
                                continue
                        self.last_scan_timestamps[ticker] = now

                        avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']
                        
                        result = await asyncio.to_thread(
                            evaluate_trade,
                            ticker, 
                            account_equity=equity, 
                            available_cash=avail_cash
                        )
                        if result:
                            if is_active: self._record_bot_scan(result)
                            self._record_scan(result)
                            trade_executed = False

                            if is_active:
                                is_market_closed_skip = not is_crypto and self.config.get('MARKET_HOURS_ONLY', True) and not self.is_market_open()
                                if is_market_closed_skip:
                                    result['reason'] = "Market Closed. Trade execution skipped."

                                cooldown_secs = self.config.get('TRADE_COOLDOWN_SECONDS', 300)
                                if cooldown_secs > 0 and ticker in self.last_trade_timestamps:
                                    elapsed = (self.get_now() - self.last_trade_timestamps[ticker]).total_seconds()
                                    if elapsed < cooldown_secs:
                                        if result['action'] in ['BUY', 'SELL']:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"Trade Cooldown Active ({int(cooldown_secs - elapsed)}s remaining)"

                                has_this_pos = any(p['symbol'].replace("/", "").upper() == ticker_norm and float(p.get('qty', 0)) > 0 for p in all_positions)
                                has_open_order = any(o['symbol'] == ticker_norm and o['side'].lower() == 'buy' for o in all_orders)

                                if not has_this_pos:
                                    self.last_trailing_stops.pop(ticker, None)

                                pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                                
                                # Sell Mode Logic
                                if has_this_pos and pos_info:
                                    entry_price = float(pos_info.get('avg_entry_price', result['price_raw']))
                                    current_price = result['price_raw']
                                    
                                    stop_mult = t_settings.get('atr_stop_multiplier', self.config.get('ATR_STOP_MULTIPLIER', 2.0))
                                    trail_mult = t_settings.get('atr_trail_multiplier', self.config.get('ATR_TRAIL_MULTIPLIER', 3.0))
                                    tp_mult = t_settings.get('take_profit_multiplier', self.config.get('ATR_TAKE_PROFIT_MULTIPLIER', 4.0))
                                    
                                    base_stop_loss = entry_price - (result['atr'] * stop_mult)
                                    take_profit = entry_price + (result['atr'] * tp_mult)
                                    
                                    if ticker not in self.last_trailing_stops:
                                        self.last_trailing_stops[ticker] = base_stop_loss
                                    
                                    trail_distance = result['atr'] * trail_mult
                                    candidate_stop = current_price - trail_distance
                                    if candidate_stop > self.last_trailing_stops[ticker]:
                                        self.last_trailing_stops[ticker] = round(candidate_stop, 2)
                                    
                                    stop_loss = max(base_stop_loss, self.last_trailing_stops[ticker])
                                    
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

                                # Buy Execution
                                if result['action'] == 'BUY' and not is_market_closed_skip:
                                    sizing = result['position_sizing']
                                    max_pos = self.config.get('MAX_OPEN_POSITIONS', 5)
                                    if portfolio_count >= max_pos and not has_this_pos:
                                        result['action'] = 'HOLD'
                                        result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                    elif has_this_pos:
                                        if pos_info:
                                            result['pl'] = pos_info['unrealized_pl']
                                            result['pl_pct'] = pos_info['unrealized_pl_pct']
                                            result['qty'] = pos_info['qty']
                                        result['reason'] = "Position already open."
                                    elif has_open_order:
                                        result['action'] = 'PENDING'
                                        result['reason'] = "Buy order pending."
                                    elif sizing['notional'] > 0:
                                        order_result = self.broker.place_order(
                                            symbol=ticker, notional=sizing['notional'], side='buy',
                                            stop_loss=sizing['stop_loss'], take_profit=sizing['take_profit']
                                        )
                                        if order_result.get('success'):
                                            result['order'] = order_result
                                            result['order_id'] = order_result.get('order_id')
                                            trade_executed = True
                                            portfolio_count += 1
                                            
                                            order_qty = order_result.get('qty')
                                            if (order_qty is None or float(order_qty) == 0) and result['price_raw'] > 0:
                                                order_qty = sizing['notional'] / result['price_raw']
                                                
                                            result['qty'] = float(order_qty) if order_qty else 0
                                            result['total_cost'] = float(order_result.get('total_cost', sizing['notional']))
                                            result['fees'] = float(order_result.get('fees', 0))
                                            result['reason'] = f"✅ BOUGHT {result['qty']:.6f} shares at {result['price']}: {result['reason']}"
                                            self.last_trade_timestamps[ticker] = self.get_now()
                                            
                                # Sell Execution
                                elif result['action'] == 'SELL' and not is_market_closed_skip:
                                    if has_this_pos:
                                        pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                                        order_result = self.broker.close_position(ticker)
                                        if order_result.get('success'):
                                            result['order'] = order_result
                                            result['order_id'] = order_result.get('order_id')
                                            trade_executed = True
                                            portfolio_count -= 1
                                            
                                            order_qty = order_result.get('qty')
                                            if order_qty and float(order_qty) > 0:
                                                result['qty'] = float(order_qty)
                                            elif pos_info:
                                                result['qty'] = float(pos_info['qty'])
                                            else:
                                                result['qty'] = 0

                                            result['total_cost'] = float(pos_info['market_value']) if pos_info else order_result.get('proceeds', 0)
                                            result['fees'] = float(order_result.get('fees', 0))

                                            if pos_info:
                                                entry_price = float(pos_info['avg_price'])
                                                exit_price = result['price_raw']
                                                qty = float(order_result.get('qty')) if order_result.get('qty') else float(pos_info['qty'])
                                                pl = (exit_price - entry_price) * qty
                                                pl_pct = ((exit_price / entry_price) - 1) * 100
                                                result['pl'] = round(pl, 2)
                                                result['pl_pct'] = round(pl_pct, 2)
                                                result['reason'] = f"✅ SOLD at {result['price']} (Entry: ${entry_price:.4f}): {result['reason']}"
                                            else:
                                                result['reason'] = f"✅ SOLD at {result['price']}: {result['reason']}"
                                            self.last_trade_timestamps[ticker] = self.get_now()
                                        else:
                                            result['reason'] = f"Sell order failed: {order_result.get('error', 'Broker Error')}"
                                    else:
                                        result['reason'] = "Signal SELL ignored: No open position detected."
                            
                            result['log_type'] = "Active Bot" if is_active else "Watchlist"
                            log_time_min = self.get_now().strftime("%Y-%m-%dT%H:%M")
                            log_key = f"{log_time_min}_{ticker}_{result['action']}"
                            
                            def _get_log_key(log_entry):
                                try:
                                    t_str = datetime.fromisoformat(log_entry.get('time', '')).strftime('%Y-%m-%dT%H:%M')
                                    return f"{t_str}_{log_entry.get('ticker','')}_{log_entry.get('action','')}"
                                except: return ""
                                
                            if not any(_get_log_key(log) == log_key for log in self.trade_log):
                                if is_active:
                                    self.trade_log.insert(0, result)
                                if trade_executed and is_active:
                                    await self.save_history()

                    except Exception as e:
                        print(f"[scheduler-{self.uid}] Error scanning {ticker}: {e}")

                if len(self.trade_log) > 200:
                    self.trade_log = self.trade_log[:200]

                self.last_scan_time = self.get_now().isoformat()
                await self.save_history()

            except Exception as e:
                print(f"[scheduler-{self.uid}] Loop error: {e}")

            try:
                await asyncio.wait_for(self.force_scan_trigger.wait(), timeout=self.config.get('SCAN_INTERVAL_SECONDS', 60))
                self.force_scan_trigger.clear()
            except asyncio.TimeoutError:
                pass


class UserManager:
    def __init__(self, db):
        self.db = db
        self.engines = {}
        
    def get_engine(self, uid: str) -> UserEngine:
        if uid not in self.engines:
            print(f"[UserManager] Spawning engine for {uid}")
            engine = UserEngine(uid, self.db)
            self.engines[uid] = engine
        return self.engines[uid]

    async def start_user(self, uid: str):
        engine = self.get_engine(uid)
        if not engine.bot_running:
            await engine.load_from_cloud()
            engine.task = asyncio.create_task(engine.trading_loop())
            
    async def start_all(self):
        print("[UserManager] Loading all user configurations...")
        if not self.db: return
        users_ref = self.db.collection("users").stream()
        for doc in users_ref:
            uid = doc.id
            await self.start_user(uid)
        print(f"[UserManager] Started {len(self.engines)} user engines.")
        
    async def stop_all(self):
        for uid, engine in self.engines.items():
            if engine.bot_running:
                engine.bot_running = False
                if engine.task: engine.task.cancel()

