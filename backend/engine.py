import asyncio
from datetime import datetime
import pytz
import time

from broker_factory import create_broker
from trader import evaluate_trade
from firebase_admin import firestore
import config as global_config
from user_config import UserConfig, set_user_config

def parse_timeframe_to_seconds(timeframe: str) -> int:
    tf = timeframe.upper().strip()
    if tf in ["1D", "1DAY", "DAY"]:
        return 86400
    
    import re
    match = re.search(r'\d+', tf)
    val = int(match.group()) if match else 5
    
    if "HOUR" in tf or "H" in tf:
        return val * 3600
    elif "MIN" in tf or "M" in tf:
        return val * 60
    elif "SEC" in tf or "S" in tf:
        return val
        
    return 300 # Default fallback (5 mins)

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
        self.signal_locked_direction = {}
        
        self.bot_running = False
        self.task = None
        self.force_scan_trigger = asyncio.Event()
        self.last_scan_time = None
        self.dashboard_primary_ticker = None
        self.loaded_from_cloud = False
        self.load_lock = asyncio.Lock()
        
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
                "parameters": self.config.parameters,
                "contexts": getattr(self.config, 'contexts', {})
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
                
                # Load isolated contexts if present
                if not hasattr(self.config, 'contexts') or not self.config.contexts:
                    self.config.contexts = {
                        "dashboard": { "toggles": {}, "parameters": {} },
                        "bots": { "toggles": {}, "parameters": {} },
                        "backtest": { "toggles": {}, "parameters": {} }
                    }
                cloud_contexts = ui.get("contexts", {})
                for ctx_name, ctx_data in cloud_contexts.items():
                    if ctx_name in self.config.contexts:
                        self.config.contexts[ctx_name]["toggles"].update(ctx_data.get("toggles", {}))
                        self.config.contexts[ctx_name]["parameters"].update(ctx_data.get("parameters", {}))
                
            # Load History
            doc_scans = self.db.collection("users").document(self.uid).collection("history").document("scans").get()
            if doc_scans.exists:
                raw_logs = doc_scans.to_dict().get("data", [])
                self.trade_log = []
                for log in raw_logs:
                    ticker = log.get('ticker')
                    action = log.get('action')
                    reason = log.get('reason', '')
                    if ticker not in self.config.TRADELIST:
                        # Skip ignored/skipped logs for deactivated bots
                        if "ignored" in reason.lower() or "skipped" in reason.lower() or action == 'HOLD':
                            continue
                    if ticker in self.config.TRADELIST or action in ['BUY', 'SELL']:
                        self.trade_log.append(log)

                
            doc_bot = self.db.collection("users").document(self.uid).collection("history").document("bot_scans").get()
            if doc_bot.exists:
                self.bot_scans.update(doc_bot.to_dict().get("data", {}))
                
            self.loaded_from_cloud = True
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
                    timeframe=timeframe,
                    use_bot_settings=False
                )
                self._record_scan(warmed)
            except Exception as exc:
                print(f"[warmup-{self.uid}] {ticker} {timeframe} failed: {exc}")


    async def trading_loop(self):
        self.bot_running = True
        print(f"[scheduler-{self.uid}] Trading loop started.")
        from user_config import active_evaluation_context
        active_evaluation_context.set("bots")
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
                # Watchlist rows are quote-only. Full indicator scans are reserved for active bots
                # and the ticker currently loaded by the dashboard chart.
                all_tickers_to_scan = list(dict.fromkeys(self.config.TRADELIST))
                if viewed and viewed in all_tickers_to_scan:
                    scan_order = [viewed] + [t for t in all_tickers_to_scan if t != viewed]
                else:
                    scan_order = all_tickers_to_scan
                
                for ticker in scan_order:
                    try:
                        ticker_norm = ticker.upper().replace("/", "")
                        t_settings = self.config.TICKER_SETTINGS.get(ticker, {})
                        is_active = ticker in self.config.TRADELIST
                        
                        if t_settings.get('paused', False):
                            continue

                        # --- Bot Loss Limit (Drawdown Limit on Allocated Bot Capital) ---
                        enable_risk = t_settings.get('enable_risk_management', True)
                        allocated_capital = float(t_settings.get('amount', 0.0))
                        loss_limit_pct = float(t_settings.get('max_daily_drawdown', 0.0))
                        
                        if is_active and enable_risk and allocated_capital > 0 and loss_limit_pct > 0:
                            realized_pl = float(t_settings.get('realized_pl', 0.0))
                            
                            # Find if there is an active position for this ticker
                            pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                            unrealized_pl = float(pos_info['unrealized_pl']) if pos_info else 0.0
                            
                            total_bot_pl = realized_pl + unrealized_pl
                            allowed_loss = allocated_capital * loss_limit_pct
                            
                            if total_bot_pl < 0 and abs(total_bot_pl) >= allowed_loss:
                                print(f"[engine-{self.uid}] Loss limit breached for {ticker}: -${abs(total_bot_pl):.2f} >= limit -${allowed_loss:.2f} ({loss_limit_pct*100:.1f}% of ${allocated_capital:.2f})")
                                
                                # Liquidate active position if any
                                if pos_info:
                                    print(f"[engine-{self.uid}] Liquidating active position in {ticker} due to loss limit breach...")
                                    close_res = self.broker.close_position(ticker)
                                    if close_res.get('success'):
                                        realized_pl += unrealized_pl
                                        total_bot_pl = realized_pl
                                
                                # Auto-pause the bot permanently
                                t_settings['paused'] = True
                                t_settings['pause_reason'] = f"Loss limit exceeded (-${abs(total_bot_pl):.2f} / -{(abs(total_bot_pl)/allocated_capital)*100:.1f}%)"
                                t_settings['realized_pl'] = 0.0  # reset for unpause
                                
                                # Cancel any pending orders
                                try:
                                    self.broker.cancel_orders_for_ticker(ticker)
                                except Exception as err:
                                    print(f"[engine-{self.uid}] Error cancelling orders for {ticker}: {err}")
                                
                                # Add log entry for the auto-pause
                                result = {
                                    "time": self.get_now().isoformat(),
                                    "action": "HALT",
                                    "ticker": ticker,
                                    "price": f"${pos_info['current_price']}" if pos_info else "$0.00",
                                    "price_raw": pos_info['current_price'] if pos_info else 0.0,
                                    "reason": f"🛑 BOT AUTO-PAUSED: Loss limit breached. Total Loss: -${abs(total_bot_pl):.2f} ({loss_limit_pct*100:.1f}% limit exceeded).",
                                    "signals": {},
                                    "bullish_count": 0,
                                    "bearish_count": 0,
                                    "total_signals": 0,
                                    "atr": 0.0,
                                    "rsi": 50.0,
                                    "position_sizing": {},
                                    "log_type": "Active Bot"
                                }
                                self.trade_log.insert(0, result)
                                await self.save_history()
                                await self.save_settings()
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
                        tf_to_use = t_settings.get('timeframe') or self.config.DEFAULT_TIMEFRAME
                        
                        result = None
                        if is_active:
                            if t_settings.get('ai_autopilot', False):
                                from ai_indicator import get_ai_decision_for_bar
                                
                                pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
                                ai_pos = None
                                if pos_info:
                                    ai_pos = {
                                        'entry_price': float(pos_info.get('avg_price', 0.0)),
                                        'qty': float(pos_info.get('qty', 0.0)),
                                        'stop_loss': float(pos_info.get('stop_loss', 0.0)) or self.last_trailing_stops.get(ticker, 0.0),
                                        'take_profit': float(pos_info.get('take_profit', 0.0)),
                                    }
                                
                                ai_decision = await asyncio.to_thread(
                                    get_ai_decision_for_bar,
                                    ticker=ticker,
                                    bar_data=None,
                                    account_equity=equity,
                                    available_cash=avail_cash,
                                    position=ai_pos,
                                    timeframe=tf_to_use,
                                    model=t_settings.get('ai_model')
                                )
                                
                                price_raw = ai_decision.get('price') or ai_decision.get('entry_price') or 0.0
                                action = ai_decision.get('signal', 'HOLD')
                                
                                stop_loss = ai_decision.get('stop_loss') or 0.0
                                take_profit = ai_decision.get('sell_target') or 0.0
                                pos_pct = ai_decision.get('position_size_pct', 0.10)
                                if pos_pct <= 0:
                                    pos_pct = 0.10
                                    
                                notional_size = min(equity * pos_pct, avail_cash)
                                
                                position_sizing = {
                                    "qty": notional_size / price_raw if price_raw > 0 else 0,
                                    "notional": notional_size,
                                    "stop_loss": stop_loss,
                                    "take_profit": take_profit,
                                    "risk_pct": pos_pct,
                                    "stop_distance": abs(price_raw - stop_loss) if stop_loss > 0 else 0.0,
                                }
                                
                                bot_result = {
                                    "time": self.get_now().isoformat(),
                                    "action": action,
                                    "ticker": ticker,
                                    "price": f"${price_raw:.4f}" if price_raw < 10 else f"${price_raw:.2f}",
                                    "price_raw": price_raw,
                                    "reason": ai_decision.get('summary', 'AI Autopilot Decision'),
                                    "sentiment_score": 1.0 if action == 'BUY' else -1.0 if action == 'SELL' else 0.0,
                                    "sentiment_confidence": ai_decision.get('confidence', 0.5),
                                    "sentiment_summary": ai_decision.get('summary', ''),
                                    "sentiment_key_factor": ai_decision.get('key_catalyst', ''),
                                    "signals": {
                                        "AI Autopilot": {
                                            "signal": "BULLISH" if action == 'BUY' else "BEARISH" if action == 'SELL' else "NEUTRAL",
                                            "weight": 5,
                                            "description": ai_decision.get('summary', '')
                                        }
                                    },
                                    "bullish_count": 5 if action == 'BUY' else 0,
                                    "bearish_count": 5 if action == 'SELL' else 0,
                                    "total_signals": 1,
                                    "atr": ai_decision.get('atr', price_raw * 0.02),
                                    "rsi": ai_decision.get('rsi', 50.0),
                                    "position_sizing": position_sizing,
                                    "price_history": [],
                                    "risk_status": {},
                                    "is_custom": True,
                                    "timeframe": tf_to_use
                                }
                                
                                self._record_bot_scan(bot_result)
                                result = bot_result
                            else:
                                bot_result = await asyncio.to_thread(
                                    evaluate_trade,
                                    ticker, 
                                    account_equity=equity, 
                                    available_cash=avail_cash,
                                    timeframe=tf_to_use,
                                    use_bot_settings=True
                                )
                                if bot_result:
                                    self._record_bot_scan(bot_result)
                                    result = bot_result
                                    
                        if result:
                            trade_executed = False

                            if is_active:
                                is_market_closed_skip = not is_crypto and self.config.get('MARKET_HOURS_ONLY', True) and not self.is_market_open()
                                if is_market_closed_skip:
                                    result['reason'] = "Market Closed. Trade execution skipped."
                                    if result['action'] in ['BUY', 'SELL']:
                                        result['action'] = 'HOLD'

                                tf_seconds = parse_timeframe_to_seconds(tf_to_use)
                                
                                # Check options trading toggle
                                is_options_enabled = t_settings.get('options_trading', getattr(self.config, 'ENABLE_OPTIONS_TRADING', False))
                                
                                # Resolve options configurations from ticker settings with config fallback
                                opt_stop_loss = t_settings.get('options_stop_loss_pct', getattr(self.config, 'OPTIONS_STOP_LOSS_PCT', 0.50))
                                opt_take_profit = t_settings.get('options_take_profit_pct', getattr(self.config, 'OPTIONS_TAKE_PROFIT_PCT', 1.00))
                                opt_exit_strategy = t_settings.get('options_exit_strategy', getattr(self.config, 'OPTIONS_EXIT_STRATEGY', 'hybrid'))
                                opt_expiry_days = t_settings.get('options_expiry_days', getattr(self.config, 'OPTIONS_EXPIRY_DAYS', 7))
                                opt_strike_type = t_settings.get('options_strike_type', getattr(self.config, 'OPTIONS_STRIKE_TYPE', 'ATM'))
                                opt_trade_amount = t_settings.get('options_trade_amount', getattr(self.config, 'OPTIONS_TRADE_AMOUNT', 100.0))
                                
                                pos_info = None
                                has_this_pos = False
                                has_open_order = False
                                
                                if is_options_enabled:
                                    from option_selector import parse_occ_symbol
                                    for p in all_positions:
                                        p_sym = p['symbol'].upper()
                                        parsed = parse_occ_symbol(p_sym)
                                        if parsed and parsed['underlying'] == ticker_norm:
                                            pos_info = p
                                            has_this_pos = True
                                            break
                                    # For option orders, symbol matches OCC structure starting with ticker_norm
                                    has_open_order = any(
                                        parse_occ_symbol(o['symbol']) and 
                                        parse_occ_symbol(o['symbol'])['underlying'] == ticker_norm and 
                                        o['side'].lower() == 'buy' 
                                        for o in all_orders
                                    )
                                else:
                                    has_this_pos = any(p['symbol'].replace("/", "").upper() == ticker_norm and float(p.get('qty', 0)) > 0 for p in all_positions)
                                    has_open_order = any(o['symbol'] == ticker_norm and o['side'].lower() == 'buy' for o in all_orders)
                                    pos_info = next((p for p in all_positions if p['symbol'].replace("/", "").upper() == ticker_norm), None)
 
                                enable_cooldown = t_settings.get("enable_cooldown", True)
                                if enable_cooldown and not has_this_pos:
                                    # Use custom cooldown if set, otherwise default to 4 candles
                                    custom_cooldown_min = t_settings.get("cooldown_minutes")
                                    if custom_cooldown_min is not None:
                                        cooldown_secs = custom_cooldown_min * 60
                                    else:
                                        cooldown_secs = tf_seconds * 4

                                    if ticker in self.last_trade_timestamps:
                                        elapsed = (self.get_now() - self.last_trade_timestamps[ticker]).total_seconds()
                                        if elapsed < cooldown_secs:
                                            # ONLY block entry orders during cooldown to prevent trapping the bot in a bad position
                                            blocked_entry = False
                                            if is_options_enabled and result['action'] in ['BUY', 'SELL']:
                                                blocked_entry = True
                                            elif not is_options_enabled and result['action'] == 'BUY':
                                                blocked_entry = True
                                                
                                            if blocked_entry:
                                                result['action'] = 'HOLD'
                                                rem = int(cooldown_secs - elapsed)
                                                if rem >= 3600:
                                                    result['reason'] = f"Trade Cooldown Active ({rem // 3600}h {(rem % 3600) // 60}m remaining)"
                                                elif rem >= 60:
                                                    result['reason'] = f"Trade Cooldown Active ({rem // 60}m {rem % 60}s remaining)"
                                                else:
                                                    result['reason'] = f"Trade Cooldown Active ({rem}s remaining)"

                                if not has_this_pos:
                                    self.last_trailing_stops.pop(ticker, None)

                                    # Get Smart Re-entry Settings
                                    enable_signal_reset = t_settings.get('enable_signal_reset', getattr(self.config, 'ENABLE_SIGNAL_RESET', False))
                                    enable_smart_reentry = t_settings.get('enable_smart_reentry', getattr(self.config, 'ENABLE_SMART_REENTRY', False))
                                    smart_reentry_type = t_settings.get('smart_reentry_type', getattr(self.config, 'SMART_REENTRY_TYPE', 'RSI'))
                                    smart_reentry_rsi_call_max = t_settings.get('smart_reentry_rsi_call_max', getattr(self.config, 'SMART_REENTRY_RSI_CALL_MAX', 70))
                                    smart_reentry_rsi_put_min = t_settings.get('smart_reentry_rsi_put_min', getattr(self.config, 'SMART_REENTRY_RSI_PUT_MIN', 30))
                                    smart_reentry_vwap_call_max_dist_pct = t_settings.get('smart_reentry_vwap_call_max_dist_pct', getattr(self.config, 'SMART_REENTRY_VWAP_CALL_MAX_DIST_PCT', 0.5))
                                    smart_reentry_vwap_put_max_dist_pct = t_settings.get('smart_reentry_vwap_put_max_dist_pct', getattr(self.config, 'SMART_REENTRY_VWAP_PUT_MAX_DIST_PCT', 0.5))

                                    # Apply Signal Reset Check/Clear
                                    if enable_signal_reset:
                                        action = result.get('action', 'HOLD')
                                        locked_dir = self.signal_locked_direction.get(ticker)
                                        if locked_dir:
                                            if action == locked_dir:
                                                result['action'] = 'HOLD'
                                                result['reason'] = f"Signal Reset Active: Blocked immediate re-entry on stale {locked_dir} signal."
                                            else:
                                                self.signal_locked_direction[ticker] = None
                                    else:
                                        self.signal_locked_direction[ticker] = None

                                    # Apply Smart Re-entry Pullback Filters
                                    if enable_smart_reentry:
                                        action = result.get('action', 'HOLD')
                                        if action in ['BUY', 'SELL']:
                                            if smart_reentry_type == 'RSI':
                                                rsi = result.get('rsi')
                                                if rsi is not None:
                                                    if action == 'BUY' and rsi > smart_reentry_rsi_call_max:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry RSI Filter: Blocked BUY since RSI ({rsi:.1f}) > {smart_reentry_rsi_call_max} (Overbought)."
                                                    elif action == 'SELL' and rsi < smart_reentry_rsi_put_min:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry RSI Filter: Blocked SELL since RSI ({rsi:.1f}) < {smart_reentry_rsi_put_min} (Oversold)."
                                            elif smart_reentry_type == 'VWAP':
                                                vwap = result.get('vwap')
                                                price = result.get('price_raw', 0.0)
                                                if vwap is not None and vwap > 0 and price > 0:
                                                    dist_pct = abs(price - vwap) / vwap * 100.0
                                                    if action == 'BUY' and dist_pct > smart_reentry_vwap_call_max_dist_pct:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry VWAP Filter: Blocked BUY since price is too far from VWAP ({dist_pct:.2f}% > {smart_reentry_vwap_call_max_dist_pct}%)."
                                                    elif action == 'SELL' and dist_pct > smart_reentry_vwap_put_max_dist_pct:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry VWAP Filter: Blocked SELL since price is too far from VWAP ({dist_pct:.2f}% > {smart_reentry_vwap_put_max_dist_pct}%)."
                                
                                # Sell Mode Logic
                                if has_this_pos and pos_info:
                                    if is_options_enabled:
                                        # Options Exits
                                        from option_selector import parse_occ_symbol
                                        parsed_occ = parse_occ_symbol(pos_info['symbol'])
                                        opt_type = parsed_occ['type'] if parsed_occ else 'CALL'
                                        
                                        exit_triggered = False
                                        exit_reason = ""
                                        
                                        # 1. Premium-based check
                                        opt_curr = float(pos_info['current_price'])
                                        opt_avg = float(pos_info['avg_price'])
                                        stop_loss_pct = opt_stop_loss
                                        take_profit_pct = opt_take_profit
                                        
                                        # Strategy A: Premium percent triggers
                                        if opt_exit_strategy in ['premium_pct', 'hybrid']:
                                            if opt_curr <= opt_avg * (1 - stop_loss_pct):
                                                exit_triggered = True
                                                exit_reason = f"Option Premium SL Hit (${opt_curr:.2f} <= ${opt_avg*(1-stop_loss_pct):.2f})"
                                            elif opt_curr >= opt_avg * (1 + take_profit_pct):
                                                exit_triggered = True
                                                exit_reason = f"Option Premium TP Hit (${opt_curr:.2f} >= ${opt_avg*(1+take_profit_pct):.2f})"
                                                
                                        # Fetch daily data for pivot & range exits
                                        if not exit_triggered and opt_exit_strategy in ['pivot_points', 'high_low', 'hybrid']:
                                            import yfinance as yf
                                            try:
                                                hist = yf.download(ticker, period="2d", interval="1d", progress=False)
                                                if len(hist) >= 2:
                                                    prev_h = float(hist.iloc[-2]['High'])
                                                    prev_l = float(hist.iloc[-2]['Low'])
                                                    prev_c = float(hist.iloc[-2]['Close'])
                                                else:
                                                    prev_h = prev_l = prev_c = result['price_raw']
                                            except Exception:
                                                prev_h = prev_l = prev_c = result['price_raw']
                                                
                                            current_price = result['price_raw']
                                            
                                            # Strategy B: Pivot points
                                            if opt_exit_strategy in ['pivot_points', 'hybrid']:
                                                P = (prev_h + prev_l + prev_c) / 3.0
                                                R1 = 2 * P - prev_l
                                                S1 = 2 * P - prev_h
                                                R2 = P + (prev_h - prev_l)
                                                S2 = P - (prev_h - prev_l)
                                                
                                                if opt_type == 'CALL':
                                                    if current_price >= R1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot TP Hit (Underlying {current_price:.2f} >= R1 {R1:.2f})"
                                                    elif current_price >= R2:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot TP Hit (Underlying {current_price:.2f} >= R2 {R2:.2f})"
                                                    elif current_price <= S1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot SL Hit (Underlying {current_price:.2f} <= S1 {S1:.2f})"
                                                else:  # PUT
                                                    if current_price <= S1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot TP Hit (Underlying {current_price:.2f} <= S1 {S1:.2f})"
                                                    elif current_price <= S2:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot TP Hit (Underlying {current_price:.2f} <= S2 {S2:.2f})"
                                                    elif current_price >= R1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot SL Hit (Underlying {current_price:.2f} >= R1 {R1:.2f})"
                                                        
                                            # Strategy C: Daily High/Low ranges
                                            if not exit_triggered and opt_exit_strategy in ['high_low', 'hybrid']:
                                                try:
                                                    today_hist = yf.download(ticker, period="1d", interval="5m", progress=False)
                                                    today_high = float(today_hist['High'].max())
                                                    today_low = float(today_hist['Low'].min())
                                                except Exception:
                                                    today_high = today_low = current_price
                                                    
                                                if opt_type == 'CALL':
                                                    if current_price < today_low:
                                                        exit_triggered = True
                                                        exit_reason = f"Intraday Low Breach (Underlying {current_price:.2f} < Today Low {today_low:.2f})"
                                                    elif current_price >= prev_h:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day High Breakout (Underlying {current_price:.2f} >= Prev High {prev_h:.2f})"
                                                    elif current_price <= prev_l:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day Low Breakdown (Underlying {current_price:.2f} <= Prev Low {prev_l:.2f})"
                                                else:  # PUT
                                                    if current_price > today_high:
                                                        exit_triggered = True
                                                        exit_reason = f"Intraday High Breach (Underlying {current_price:.2f} > Today High {today_high:.2f})"
                                                    elif current_price <= prev_l:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day Low Breakout (Underlying {current_price:.2f} <= Prev Low {prev_l:.2f})"
                                                    elif current_price >= prev_h:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day High Breakdown (Underlying {current_price:.2f} >= Prev High {prev_h:.2f})"
                                                        
                                        # Strategy D: Expiration Safeguard
                                        if not exit_triggered and parsed_occ:
                                            import datetime
                                            if parsed_occ['expiration'] <= datetime.date.today():
                                                # If it is expiration day, force exit 2 hours before market close
                                                now_dt = datetime.datetime.now()
                                                if now_dt.hour >= 13: # 13:00 Central (2 hours before 15:00 close)
                                                    exit_triggered = True
                                                    exit_reason = f"Expiration Safeguard (2 hours before close on expiration day)"
                                                    
                                        # Technical Indicator exit
                                        if not exit_triggered and opt_exit_strategy in ['indicator', 'hybrid']:
                                            if opt_type == 'CALL' and result['action'] == 'SELL':
                                                exit_triggered = True
                                                exit_reason = f"Technical Signal reversal to BEARISH"
                                            elif opt_type == 'PUT' and result['action'] == 'BUY':
                                                exit_triggered = True
                                                exit_reason = f"Technical Signal reversal to BULLISH"
                                                
                                        if exit_triggered:
                                            result['action'] = 'SELL'
                                            result['reason'] = exit_reason
                                        else:
                                            result['action'] = 'HOLD'
                                            result['reason'] = "Option position open. Checking exit criteria..."
                                            
                                    else:
                                        # Standard Stock/Crypto Sell Mode Logic
                                        entry_price = float(pos_info.get('avg_price', result['price_raw']))
                                        current_price = result['price_raw']
                                        
                                        # Read exit strategy parameters
                                        sl_pct    = t_settings.get('regular_stop_loss_pct', 0.05)
                                        tp_pct    = t_settings.get('regular_take_profit_pct', 0.10)
                                        trail_pct = t_settings.get('regular_trailing_pct', 0.03)
                                        precision = 6 if entry_price < 1.0 else 2  # Crypto precision fix
                                        
                                        if t_settings.get('ai_autopilot', False):
                                            stop_loss = result['position_sizing'].get('stop_loss') or self.last_trailing_stops.get(ticker)
                                            take_profit = result['position_sizing'].get('take_profit')
                                            
                                            if sell_mode == 'sltp' and result['action'] == 'SELL':
                                                result['action'] = 'HOLD'
                                                result['reason'] = 'AI Dynamic Sell Signal Ignored (Fixed SL/TP Mode)'
                                            
                                            if sell_mode in ['sltp', 'hybrid', 'ai_autopilot']:
                                                if stop_loss:
                                                    self.last_trailing_stops[ticker] = stop_loss
                                                    if current_price <= stop_loss:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'AI Stop Loss Hit (${current_price:.2f} <= ${stop_loss:.2f})'
                                                if take_profit:
                                                    if current_price >= take_profit:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'AI Take Profit Hit (${current_price:.2f} >= ${take_profit:.2f})'
                                        else:
                                            if sell_mode in ['percentage', 'hybrid_pct']:
                                                # ── NEW: Percentage-based exit ──────────────────────────────
                                                base_stop = round(entry_price * (1 - sl_pct), precision)
                                                take_profit = round(entry_price * (1 + tp_pct), precision)
                                                
                                                if ticker not in self.last_trailing_stops:
                                                    self.last_trailing_stops[ticker] = base_stop
                                                
                                                if t_settings.get('enable_pct_trail', True):
                                                    # Ratchet trailing stop upward (only ever tightens, never loosens)
                                                    candidate = round(current_price * (1 - trail_pct), precision)
                                                    if candidate > self.last_trailing_stops[ticker]:
                                                        self.last_trailing_stops[ticker] = candidate
                                                
                                                stop_loss = self.last_trailing_stops[ticker]
                                                
                                                # In percentage mode: silence indicator SELL signals, rely on % levels
                                                if sell_mode == 'percentage' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Percentage SL/TP Mode)'
                                                
                                                if current_price <= stop_loss:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Percentage Stop Loss Hit (${current_price:.{precision}f} <= ${stop_loss:.{precision}f}, Trail {trail_pct*100:.1f}%)'
                                                elif current_price >= take_profit:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Percentage Take Profit Hit (${current_price:.{precision}f} >= ${take_profit:.{precision}f}, TP {tp_pct*100:.1f}%)'
                                        
                                            elif sell_mode in ['pivot_points', 'hybrid_pivot']:
                                                # ── NEW: Pivot Point exit ───────────────────────────────────
                                                try:
                                                    import yfinance as yf
                                                    hist = await asyncio.to_thread(
                                                        lambda: yf.download(ticker, period="2d", interval="1d", progress=False)
                                                    )
                                                    if len(hist) >= 2:
                                                        prev_h = float(hist.iloc[-2]['High'])
                                                        prev_l = float(hist.iloc[-2]['Low'])
                                                        prev_c = float(hist.iloc[-2]['Close'])
                                                    else:
                                                        prev_h = prev_l = prev_c = current_price
                                                except Exception as piv_err:
                                                    print(f"[engine-{self.uid}] Pivot fetch failed for {ticker}: {piv_err}")
                                                    prev_h = prev_l = prev_c = current_price
                                                
                                                P  = (prev_h + prev_l + prev_c) / 3.0
                                                R1 = round(2 * P - prev_l, precision)
                                                R2 = round(P + (prev_h - prev_l), precision)
                                                S1 = round(2 * P - prev_h, precision)
                                                S2 = round(P - (prev_h - prev_l), precision)
                                                
                                                # In pivot mode: silence indicator SELL signals, rely on pivot levels
                                                if sell_mode == 'pivot_points' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Pivot Points Mode)'
                                                
                                                if current_price <= S1:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Stop Loss Hit (${current_price:.{precision}f} <= S1 ${S1:.{precision}f})'
                                                elif current_price <= S2:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Stop Loss Hit (${current_price:.{precision}f} <= S2 ${S2:.{precision}f})'
                                                elif current_price >= R1:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Take Profit Hit (${current_price:.{precision}f} >= R1 ${R1:.{precision}f})'
                                                elif current_price >= R2:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Take Profit Hit (${current_price:.{precision}f} >= R2 ${R2:.{precision}f})'
                                        
                                            else:
                                                # ── EXISTING: ATR-based logic (unchanged) ───────────────────
                                                stop_mult = t_settings.get('atr_stop_multiplier', 2.0)
                                                trail_mult = t_settings.get('atr_trail_multiplier', 3.0)
                                                tp_mult = t_settings.get('take_profit_multiplier', 4.0)
                                                
                                                base_stop_loss = entry_price - (result['atr'] * stop_mult)
                                                take_profit = entry_price + (result['atr'] * tp_mult)
                                                
                                                if ticker not in self.last_trailing_stops:
                                                    self.last_trailing_stops[ticker] = base_stop_loss
                                                
                                                if t_settings.get('enable_atr_trail', True):
                                                    trail_distance = result['atr'] * trail_mult
                                                    candidate_stop = current_price - trail_distance
                                                    if candidate_stop > self.last_trailing_stops[ticker]:
                                                        self.last_trailing_stops[ticker] = round(candidate_stop, precision)
                                                
                                                stop_loss = max(base_stop_loss, self.last_trailing_stops[ticker])
                                                
                                                if sell_mode == 'sltp' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Fixed SL/TP Mode)'
                                                    
                                                if sell_mode in ['sltp', 'hybrid']:
                                                    if current_price <= stop_loss:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Stop Loss Hit (${current_price:.{precision}f} <= ${stop_loss:.{precision}f})'
                                                    elif current_price >= take_profit:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Take Profit Hit (${current_price:.{precision}f} >= ${take_profit:.{precision}f})'

                                # Buy Execution
                                if result['action'] == 'BUY' and not is_market_closed_skip:
                                    if is_options_enabled:
                                        enable_risk = t_settings.get('enable_risk_management', True)
                                        max_pos = getattr(self.config, 'MAX_OPEN_POSITIONS', 5) if enable_risk else 999999
                                        if portfolio_count >= max_pos and not has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                        elif has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = "Options position already open."
                                        elif has_open_order:
                                            result['action'] = 'PENDING'
                                            result['reason'] = "Options buy order pending."
                                        elif t_settings.get("options_type", "both") == "puts_only":
                                            result['reason'] = "Strategy is configured for Puts Only."
                                        else:
                                            # Option contract selection
                                            from option_selector import get_option_chain, select_contract
                                            chain = get_option_chain(
                                                underlying=ticker,
                                                expiration_limit_days=opt_expiry_days,
                                                current_price=result['price_raw'],
                                                client=self.broker.client
                                            )
                                            contract = select_contract(
                                                contracts=chain,
                                                current_underlying_price=result['price_raw'],
                                                option_type="CALL",
                                                strike_type=opt_strike_type,
                                                target_expiry_days=opt_expiry_days
                                            )
                                            
                                            if contract:
                                                # Fetch estimate premium for sizing
                                                import math
                                                if self.broker.simulation_mode:
                                                    from options_pricing import black_scholes
                                                    import datetime
                                                    days = (contract['expiration'] - datetime.date.today()).days
                                                    t_years = max(0.0001, days / 365.0)
                                                    est_premium = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.20, option_type="CALL")
                                                    est_premium = max(0.05, est_premium)
                                                else:
                                                    try:
                                                        from alpaca.data.historical.option import OptionHistoricalDataClient
                                                        from alpaca.data.requests import OptionLatestQuoteRequest
                                                        data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                                                        req = OptionLatestQuoteRequest(symbol_or_symbols=[contract['symbol']])
                                                        quotes = data_client.get_option_latest_quote(req)
                                                        est_premium = float(quotes[contract['symbol']].ask_price)
                                                    except Exception as quote_err:
                                                        print(f"[options] Quote fetch failed (no OPRA?): {quote_err}. Falling back to theoretical pricing.")
                                                        # Fallback to our realistic theoretical pricing
                                                        from options_pricing import black_scholes
                                                        import datetime
                                                        days = (contract['expiration'] - datetime.date.today()).days
                                                        t_years = max(0.0001, days / 365.0)
                                                        # Use realistic parameters: 0.05 rate, 0.20 base vol + 0.04 markup, 0.013 div yield, plus $0.015 half-spread
                                                        est_premium_bs = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.24, option_type="CALL", q=0.013)
                                                        est_premium = max(0.01, est_premium_bs + 0.015)
                                                
                                                if est_premium > 0:
                                                    allocated = opt_trade_amount
                                                    qty = math.floor(allocated / (est_premium * 100.0))
                                                    
                                                    if qty > 0:
                                                        order_result = self.broker.place_option_order(
                                                            option_symbol=contract['symbol'],
                                                            qty=qty,
                                                            side='buy'
                                                        )
                                                        if order_result.get('success'):
                                                            result['order'] = order_result
                                                            result['order_id'] = order_result.get('order_id')
                                                            trade_executed = True
                                                            portfolio_count += 1
                                                            
                                                            result['qty'] = qty
                                                            result['total_cost'] = qty * est_premium * 100.0
                                                            result['fees'] = 0.0
                                                            result['reason'] = f"✅ BOUGHT {qty} CALL contracts ({contract['symbol']}) at premium ${est_premium:.2f}: {result['reason']}"
                                                            self.last_trade_timestamps[ticker] = self.get_now()
                                                            await self._increment_trade_count(ticker)
                                                        else:
                                                            result['reason'] = f"Option buy failed: {order_result.get('error')}"
                                                    else:
                                                        result['reason'] = f"Skipped: single contract cost (${est_premium * 100:.2f}) exceeds allocation (${allocated:.2f})"
                                                else:
                                                    result['reason'] = "Skipped: option premium estimate could not be retrieved."
                                            else:
                                                result['reason'] = "Skipped: no suitable CALL contract found."
                                    else:
                                        # Standard Stock/Crypto Buy execution
                                        sizing = result['position_sizing']
                                        enable_risk = t_settings.get('enable_risk_management', True)
                                        max_pos = getattr(self.config, 'MAX_OPEN_POSITIONS', 5) if enable_risk else 999999
                                        if portfolio_count >= max_pos and not has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                        elif has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = "Position already open. (Buy signal ignored)"
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
                                                
                                                # Reset trailing stop so new strategy starts fresh from entry price
                                                self.last_trailing_stops.pop(ticker, None)                                                
                                                order_qty = order_result.get('qty')
                                                if (order_qty is None or float(order_qty) == 0) and result['price_raw'] > 0:
                                                    order_qty = sizing['notional'] / result['price_raw']
                                                    
                                                result['qty'] = float(order_qty) if order_qty else 0
                                                result['total_cost'] = float(order_result.get('total_cost', sizing['notional']))
                                                result['fees'] = float(order_result.get('fees', 0))
                                                result['reason'] = f"✅ BOUGHT {result['qty']:.6f} shares at {result['price']}: {result['reason']}"
                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                await self._increment_trade_count(ticker)
                                                
                                # Sell Execution (or PUT option entry)
                                elif result['action'] == 'SELL' and not is_market_closed_skip:
                                    if is_options_enabled:
                                        if has_this_pos:
                                            # Close active option position
                                            order_result = self.broker.close_position(pos_info['symbol'])
                                            if order_result.get('success'):
                                                result['order'] = order_result
                                                result['order_id'] = order_result.get('order_id')
                                                trade_executed = True
                                                portfolio_count -= 1
                                                
                                                qty = float(pos_info['qty'])
                                                val = qty * float(pos_info['current_price']) * 100.0
                                                cost = qty * float(pos_info['avg_price']) * 100.0
                                                pl = val - cost
                                                pl_pct = (pl / cost * 100.0) if cost > 0 else 0.0
                                                
                                                result['qty'] = qty
                                                result['total_cost'] = val
                                                result['fees'] = 0.0
                                                result['pl'] = round(pl, 2)
                                                result['pl_pct'] = round(pl_pct, 2)
                                                
                                                result['reason'] = f"✅ CLOSED option position ({pos_info['symbol']}) at premium ${pos_info['current_price']:.2f} (P/L: ${pl:+.2f}): {result['reason']}"
                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                await self._increment_trade_count(ticker)
                                                
                                                # Accumulate realized P/L
                                                current_realized = t_settings.get('realized_pl', 0.0)
                                                t_settings['realized_pl'] = round(current_realized + pl, 2)
                                                await self.save_settings()

                                                enable_signal_reset = t_settings.get('enable_signal_reset', getattr(self.config, 'ENABLE_SIGNAL_RESET', False))
                                                if enable_signal_reset:
                                                    locked_dir = 'BUY' if opt_type == 'CALL' else 'SELL'
                                                    self.signal_locked_direction[ticker] = locked_dir
                                                    print(f"[engine-{self.uid}] Signal Reset Activated: Locked {locked_dir} re-entry for {ticker}")
                                            else:
                                                result['reason'] = f"Option sell failed: {order_result.get('error')}"
                                        else:
                                            # No option position, bearish signal -> BUY PUT option!
                                            enable_risk = t_settings.get('enable_risk_management', True)
                                            max_pos = getattr(self.config, 'MAX_OPEN_POSITIONS', 5) if enable_risk else 999999
                                            if portfolio_count >= max_pos:
                                                result['action'] = 'HOLD'
                                                result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                            elif has_open_order:
                                                result['action'] = 'PENDING'
                                                result['reason'] = "Options buy order pending."
                                            elif t_settings.get("options_type", "both") == "calls_only":
                                                result['reason'] = "Strategy is configured for Calls Only."
                                            else:
                                                # Option contract selection (PUT)
                                                from option_selector import get_option_chain, select_contract
                                                chain = get_option_chain(
                                                    underlying=ticker,
                                                    expiration_limit_days=opt_expiry_days,
                                                    current_price=result['price_raw'],
                                                    client=self.broker.client
                                                )
                                                contract = select_contract(
                                                    contracts=chain,
                                                    current_underlying_price=result['price_raw'],
                                                    option_type="PUT",
                                                    strike_type=opt_strike_type,
                                                    target_expiry_days=opt_expiry_days
                                                )
                                                
                                                if contract:
                                                    # Fetch estimate premium for sizing
                                                    import math
                                                    if self.broker.simulation_mode:
                                                        from options_pricing import black_scholes
                                                        import datetime
                                                        days = (contract['expiration'] - datetime.date.today()).days
                                                        t_years = max(0.0001, days / 365.0)
                                                        est_premium = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.20, option_type="PUT")
                                                        est_premium = max(0.05, est_premium)
                                                    else:
                                                        try:
                                                            from alpaca.data.historical.option import OptionHistoricalDataClient
                                                            from alpaca.data.requests import OptionLatestQuoteRequest
                                                            data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                                                            req = OptionLatestQuoteRequest(symbol_or_symbols=[contract['symbol']])
                                                            quotes = data_client.get_option_latest_quote(req)
                                                            est_premium = float(quotes[contract['symbol']].ask_price)
                                                        except Exception as quote_err:
                                                            print(f"[options] Quote fetch failed (no OPRA?): {quote_err}. Falling back to theoretical pricing.")
                                                            # Fallback to our realistic theoretical pricing
                                                            from options_pricing import black_scholes
                                                            import datetime
                                                            days = (contract['expiration'] - datetime.date.today()).days
                                                            t_years = max(0.0001, days / 365.0)
                                                            est_premium_bs = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.24, option_type="PUT", q=0.013)
                                                            est_premium = max(0.01, est_premium_bs + 0.015)
                                                    
                                                    if est_premium > 0:
                                                        allocated = opt_trade_amount
                                                        qty = math.floor(allocated / (est_premium * 100.0))
                                                        
                                                        if qty > 0:
                                                            order_result = self.broker.place_option_order(
                                                                option_symbol=contract['symbol'],
                                                                qty=qty,
                                                                side='buy'
                                                            )
                                                            if order_result.get('success'):
                                                                result['order'] = order_result
                                                                result['order_id'] = order_result.get('order_id')
                                                                trade_executed = True
                                                                portfolio_count += 1
                                                                
                                                                result['qty'] = qty
                                                                result['total_cost'] = qty * est_premium * 100.0
                                                                result['fees'] = 0.0
                                                                result['reason'] = f"✅ BOUGHT {qty} PUT contracts ({contract['symbol']}) at premium ${est_premium:.2f}: {result['reason']}"
                                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                                await self._increment_trade_count(ticker)
                                                            else:
                                                                result['reason'] = f"Option buy failed: {order_result.get('error')}"
                                                        else:
                                                            result['reason'] = f"Skipped: single contract cost (${est_premium * 100:.2f}) exceeds allocation (${allocated:.2f})"
                                                    else:
                                                        result['reason'] = "Skipped: option premium estimate could not be retrieved."
                                                else:
                                                    result['reason'] = "Skipped: no suitable PUT contract found."
                                    else:
                                        # Standard Stock/Crypto Sell execution
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
                                                    
                                                    # Accumulate realized P/L for the bot
                                                    current_realized = t_settings.get('realized_pl', 0.0)
                                                    t_settings['realized_pl'] = round(current_realized + pl, 2)
                                                    await self.save_settings()
                                                else:
                                                    result['reason'] = f"✅ SOLD at {result['price']}: {result['reason']}"
                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                await self._increment_trade_count(ticker)

                                                enable_signal_reset = t_settings.get('enable_signal_reset', getattr(self.config, 'ENABLE_SIGNAL_RESET', False))
                                                if enable_signal_reset:
                                                    self.signal_locked_direction[ticker] = 'BUY'
                                                    print(f"[engine-{self.uid}] Signal Reset Activated: Locked BUY re-entry for standard asset {ticker}")
                                            else:
                                                result['reason'] = f"Sell order failed: {order_result.get('error', 'Broker Error')}"
                                        else:
                                            result['reason'] = "Signal SELL ignored: No open position detected."
                                            result['action'] = "HOLD"
                            
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

    async def _increment_trade_count(self, ticker: str):
        t_settings = self.config.TICKER_SETTINGS.get(ticker, {})
        if not t_settings:
            return
            
        now = self.get_now()
        today_str = now.strftime("%Y-%m-%d")
        
        # Increment total trades
        t_settings['total_trades'] = t_settings.get('total_trades', 0) + 1
        t_settings['last_trade_at'] = now.isoformat()
        
        # Increment today trades
        last_trade_date = t_settings.get('last_trade_date', '')
        if last_trade_date == today_str:
            t_settings['today_trades'] = t_settings.get('today_trades', 0) + 1
        else:
            t_settings['last_trade_date'] = today_str
            t_settings['today_trades'] = 1
            
        await self.save_settings()


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
