import asyncio
from datetime import datetime
import pytz
import time

from broker_factory import create_broker
from trader import evaluate_trade
from swing_strategy import build_swing_exit
from options_direction import normalize_options_type, VALID_OPTION_DIRECTIONS, options_type_to_contract_type
from prior_day_high_low import (
    buffered_prior_day_levels,
    dynamic_high_low_exit_levels as _buffered_dynamic_high_low_exit_levels,
    price_history_days_central,
    prior_day_ohlc_from_history as _shared_prior_day_ohlc_from_history,
    resolve_high_low_buffer_pct,
)
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


def _safe_float(value, fallback: float = 0.0) -> float:
    try:
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        return float(value)
    except Exception:
        return fallback


def _price_history_days(price_history):
    return price_history_days_central(price_history)


def _ohlc_from_candles(candles):
    highs = [_safe_float(c.get("high"), None) for c in candles]
    lows = [_safe_float(c.get("low"), None) for c in candles]
    highs = [v for v in highs if v is not None]
    lows = [v for v in lows if v is not None]
    close = None
    for candle in reversed(candles):
        close = _safe_float(candle.get("close"), None)
        if close is not None:
            break
    if not highs or not lows or close is None:
        return None
    return {"high": max(highs), "low": min(lows), "close": close}


def _prior_day_ohlc_from_history(price_history):
    return _shared_prior_day_ohlc_from_history(price_history)


def _current_day_range_from_history(price_history):
    days = _price_history_days(price_history)
    if not days:
        return None
    ohlc = _ohlc_from_candles(days[-1][1])
    if not ohlc:
        return None
    return {"high": ohlc["high"], "low": ohlc["low"]}

FIXED_LEVEL_EXIT_MODES = {
    "custom_sltp",
    "sltp",
    "hybrid",
    "percentage",
    "hybrid_pct",
    "pivot_points",
    "hybrid_pivot",
    "high_low",
    "hybrid_high_low",
    "swing_points",
    "hybrid_swing",
}

HYBRID_EXIT_MODES = {
    "hybrid",
    "hybrid_pct",
    "hybrid_pivot",
    "hybrid_high_low",
    "hybrid_swing",
}


def _is_crypto_symbol(symbol: str) -> bool:
    clean = str(symbol or "").upper().replace("/", "")
    return clean.endswith(("USD", "USDT", "USDC")) or clean in {"BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"}


def _exit_signal_threshold(settings: dict, fallback: int = 1) -> int:
    return max(
        1,
        int(
            _safe_float(
                (settings or {}).get("min_sell_signals", (settings or {}).get("sell_threshold", fallback)),
                fallback,
            )
        ),
    )


def _mark_exit_signal_watch(result: dict, settings: dict, *, is_long: bool = True, prefix: str = "Position open") -> None:
    """Keep open-position scan logs focused on exit monitoring, not fresh entries."""
    threshold = _exit_signal_threshold(settings, getattr(global_config, "MIN_BEARISH_SIGNALS", 1))
    bullish_count = int(_safe_float(result.get("bullish_count"), 0))
    bearish_count = int(_safe_float(result.get("bearish_count"), 0))
    exit_count = bearish_count if is_long else bullish_count
    exit_direction = "BEARISH" if is_long else "BULLISH"
    exit_suffix = "S" if is_long else "B"
    result["action"] = "HOLD"
    result["exit_monitoring"] = True
    result["exit_signal_count"] = exit_count
    result["exit_signal_threshold"] = threshold
    result["exit_signal_direction"] = exit_direction
    result["exit_signal_label"] = f"Exit {exit_count}/{threshold}{exit_suffix}"
    result["reason"] = (
        f"{prefix}. Checking exit signals: {exit_count}/{threshold} {exit_direction} confirmed "
        f"({bullish_count}B / {bearish_count}S)."
    )


def _mark_exit_level_watch(result: dict, label: str, reason: str, details: dict = None) -> None:
    result["action"] = "HOLD"
    result["exit_monitoring"] = True
    result["exit_signal_label"] = label
    result["exit_watch_type"] = label
    result["exit_watch_details"] = details or {}
    result["reason"] = reason


def _next_above(levels, current_price, fallback_step):
    valid = sorted([float(v) for v in levels if v is not None and float(v) > current_price])
    if valid:
        return valid[0]
    step = abs(float(fallback_step or 0.0))
    return current_price + step if step > 0 else None


def _nearest_below(levels, current_price):
    valid = sorted([float(v) for v in levels if v is not None and float(v) < current_price], reverse=True)
    return valid[0] if valid else None


def _dynamic_pivot_levels(prev_h, prev_l, prev_c, current_price):
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
    tp = _next_above([r1, r2, r3, r3 + day_range], current_price, day_range)
    sl = _nearest_below([r3, r2, r1, p_val, s1, s2, s3], current_price)
    return {"tp": tp, "sl": sl, "p": p_val, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}


def _dynamic_high_low_levels(prev_h, prev_l, current_price, buffer_pct=0.0):
    return _buffered_dynamic_high_low_exit_levels(prev_h, prev_l, current_price, buffer_pct)

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
        self.last_bot_scan_time = {}
        self.last_history_save_ts = 0
        self.signal_locked_direction = {}
        self.exit_order_state = {}
        self.scan_log_state = {}
        
        self.bot_running = False
        self.task = None
        self.force_scan_trigger = asyncio.Event()
        self.last_scan_time = None
        self.dashboard_primary_ticker = None
        self.loaded_from_cloud = False
        self.load_lock = asyncio.Lock()
        self.settings_save_lock = asyncio.Lock()
        
    def get_now(self):
        tz = pytz.timezone(self.config.TIMEZONE if hasattr(self.config, 'TIMEZONE') else 'US/Central')
        return datetime.now(tz)

    def _normalize_signal_lock(self, value):
        direction = str(value or "").upper().strip()
        return direction if direction in {"BUY", "SELL"} else None

    def _hydrate_signal_reset_locks(self):
        hydrated = {}
        for ticker, settings in self.config.TICKER_SETTINGS.items():
            if not isinstance(settings, dict):
                continue
            locked_dir = self._normalize_signal_lock(settings.get("signal_locked_direction"))
            if locked_dir:
                ticker_key = str(ticker or "").upper().strip()
                hydrated[ticker_key] = locked_dir
                settings["signal_locked_direction"] = locked_dir
            else:
                settings.pop("signal_locked_direction", None)
        self.signal_locked_direction = hydrated

    def _set_signal_reset_lock(self, ticker, direction, t_settings=None):
        ticker_key = str(ticker or "").upper().strip()
        locked_dir = self._normalize_signal_lock(direction)
        if not ticker_key or not locked_dir:
            return None
        self.signal_locked_direction[ticker_key] = locked_dir
        settings = t_settings if isinstance(t_settings, dict) else self.config.TICKER_SETTINGS.setdefault(ticker_key, {})
        if isinstance(settings, dict):
            settings["signal_locked_direction"] = locked_dir
        return locked_dir

    def _clear_signal_reset_lock(self, ticker, t_settings=None):
        ticker_key = str(ticker or "").upper().strip()
        changed = False
        if ticker_key:
            changed = self.signal_locked_direction.pop(ticker_key, None) is not None
        settings = t_settings if isinstance(t_settings, dict) else self.config.TICKER_SETTINGS.get(ticker_key, {})
        if isinstance(settings, dict):
            changed = settings.pop("signal_locked_direction", None) is not None or changed
        return changed

    def _get_signal_reset_lock(self, ticker, t_settings=None):
        ticker_key = str(ticker or "").upper().strip()
        locked_dir = self._normalize_signal_lock(self.signal_locked_direction.get(ticker_key))
        if locked_dir:
            return locked_dir
        settings = t_settings if isinstance(t_settings, dict) else self.config.TICKER_SETTINGS.get(ticker_key, {})
        if isinstance(settings, dict):
            locked_dir = self._normalize_signal_lock(settings.get("signal_locked_direction"))
            if locked_dir and ticker_key:
                self.signal_locked_direction[ticker_key] = locked_dir
                settings["signal_locked_direction"] = locked_dir
                return locked_dir
        return None

    def _signal_reset_threshold(self, locked_dir, t_settings):
        if locked_dir == "SELL":
            keys = ("min_sell_signals", "sell_threshold")
            default_value = getattr(self.config, "MIN_BEARISH_SIGNALS", 1)
        else:
            keys = ("min_buy_signals", "threshold", "buy_threshold")
            default_value = getattr(self.config, "MIN_BULLISH_SIGNALS", 1)
        for key in keys:
            value = (t_settings or {}).get(key)
            if value is None or value == "":
                continue
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                continue
        try:
            return max(1, int(default_value))
        except (TypeError, ValueError):
            return 1

    def _signal_reset_count(self, result, locked_dir):
        key = "bearish_count" if locked_dir == "SELL" else "bullish_count"
        try:
            return float(result.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _signal_reset_is_reset(self, result, locked_dir, t_settings):
        return self._signal_reset_count(result, locked_dir) < self._signal_reset_threshold(locked_dir, t_settings)
        
    async def save_settings(self):
        async with self.settings_save_lock:
            try:
                import time, json, os
                data = {
                    "watchlist": self.config.WATCHLIST,
                    "tradelist": self.config.TRADELIST,
                    "strategy_timeframe": self.config.DEFAULT_TIMEFRAME,
                    "ticker_settings": self.config.TICKER_SETTINGS,
                    "toggles": self.config.toggles,
                    "parameters": self.config.parameters,
                    "contexts": getattr(self.config, 'contexts', {}),
                    "saved_at": time.time()
                }
                try:
                    os.makedirs("data", exist_ok=True)
                    with open(f"data/user_settings_{self.uid}.json", "w") as f:
                        json.dump(data, f, indent=2)
                except Exception as ex_loc:
                    print(f"[engine-{self.uid}] Failed writing local settings backup: {ex_loc}")

                if not self.db: return
                def _sync_save():
                    self.db.collection("users").document(self.uid).collection("settings").document("ui").set(data)
                await asyncio.wait_for(asyncio.to_thread(_sync_save), timeout=2.5)
            except asyncio.TimeoutError:
                print(f"[engine-{self.uid}] Firestore save_settings timed out (quota exceeded or network lag). Settings saved to local disk backup & active in memory.")
            except Exception as e:
                print(f"[engine-{self.uid}] Error saving settings to Firestore: {e} (Settings saved to local disk backup).")

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
            await asyncio.wait_for(asyncio.to_thread(_sync_save), timeout=2.5)
        except asyncio.TimeoutError:
            print(f"[engine-{self.uid}] Firestore save_history timed out. History active in memory.")
        except Exception as e:
            print(f"[engine-{self.uid}] Error saving history: {e}")

    async def save_history_throttled(self, force: bool = False, min_interval_seconds: int = 30):
        """Persist scan history without making fast option loops write every heartbeat."""
        now_ts = time.time()
        if not force and (now_ts - self.last_history_save_ts) < min_interval_seconds:
            return
        await self.save_history()
        self.last_history_save_ts = now_ts

    async def load_from_cloud(self):
        if not self.db: return
        try:
            # Load Alpaca Settings
            doc_alpaca = self.db.collection("users").document(self.uid).collection("settings").document("alpaca").get(timeout=2.5)
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
            ui = {}
            import json, os
            local_ui = {}
            local_path = f"data/user_settings_{self.uid}.json"
            if os.path.exists(local_path):
                try:
                    with open(local_path, "r") as f:
                        local_ui = json.load(f)
                except Exception as ex_loc:
                    print(f"[engine-{self.uid}] Failed reading local settings backup: {ex_loc}")

            try:
                doc_ui = self.db.collection("users").document(self.uid).collection("settings").document("ui").get(timeout=2.5)
                if doc_ui.exists:
                    ui = doc_ui.to_dict()
            except Exception as ex_fs:
                print(f"[engine-{self.uid}] Could not load UI settings from Firestore ({ex_fs}); checking local backup.")

            if local_ui and (not ui or local_ui.get("saved_at", 0) >= ui.get("saved_at", 0)):
                ui = local_ui
                print(f"[engine-{self.uid}] Loaded latest settings from local disk backup (saved_at: {ui.get('saved_at')}).")
            elif ui:
                print(f"[engine-{self.uid}] Loaded latest settings from Firestore.")

            if ui:
                self.config.WATCHLIST = ui.get("watchlist", self.config.WATCHLIST)
                self.config.TRADELIST = ui.get("tradelist", self.config.TRADELIST)
                self.config.DEFAULT_TIMEFRAME = ui.get("strategy_timeframe", self.config.DEFAULT_TIMEFRAME)
                self.config.TICKER_SETTINGS = ui.get("ticker_settings", {})
                self._hydrate_signal_reset_locks()
                
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
            doc_scans = self.db.collection("users").document(self.uid).collection("history").document("scans").get(timeout=2.5)
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

                
            doc_bot = self.db.collection("users").document(self.uid).collection("history").document("bot_scans").get(timeout=2.5)
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

    def get_options_market_status(self) -> dict:
        import pytz
        from datetime import datetime, timedelta
        et_now = datetime.now(pytz.timezone("US/Eastern"))
        
        # RTH for options: 09:30 to 16:00 ET, Monday to Friday
        # Scan window starts 2 minutes early at 09:28 ET
        is_weekday = et_now.weekday() < 5
        scan_start = et_now.replace(hour=9, minute=28, second=0, microsecond=0)
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
        
        is_active_window = is_weekday and (scan_start <= et_now <= market_close)
        is_open = is_weekday and (market_open <= et_now <= market_close)
        
        next_open = market_open
        next_scan = scan_start
        if not is_weekday or et_now > market_close:
            days_add = 1
            if et_now.weekday() == 4:  # Friday after close -> Monday
                days_add = 3
            elif et_now.weekday() == 5:  # Saturday -> Monday
                days_add = 2
            elif et_now.weekday() == 6 and et_now > market_close:  # Sunday -> Monday
                days_add = 1
            next_open = (et_now + timedelta(days=days_add)).replace(hour=9, minute=30, second=0, microsecond=0)
            next_scan = (et_now + timedelta(days=days_add)).replace(hour=9, minute=28, second=0, microsecond=0)
        elif et_now < scan_start:
            next_open = market_open
            next_scan = scan_start
            
        seconds_to_open = max(0, int((next_open - et_now).total_seconds()))
        seconds_to_pre_open = max(0, int((next_scan - et_now).total_seconds()))
        
        open_day_name = next_open.strftime("%A") if next_open.date() != et_now.date() else "Today"
        next_open_str = f"{open_day_name} at 9:30 AM ET"
        
        return {
            "is_active_window": is_active_window,
            "is_open": is_open,
            "seconds_to_open": seconds_to_open,
            "seconds_to_pre_open": seconds_to_pre_open,
            "next_open_str": next_open_str,
        }

    def get_stock_market_status(self, market_session: str = "Regular") -> dict:
        import pytz
        from datetime import datetime, timedelta
        et_now = datetime.now(pytz.timezone("US/Eastern"))
        
        session = str(market_session or "Regular").strip().lower()
        is_extended = session in {"extended", "24h", "crypto24", "all"}
        
        open_hour, open_minute = (4, 0) if is_extended else (9, 30)
        scan_hour, scan_minute = (3, 58) if is_extended else (9, 28)
        close_hour, close_minute = (20, 0) if is_extended else (16, 0)
        
        is_weekday = et_now.weekday() < 5
        scan_start = et_now.replace(hour=scan_hour, minute=scan_minute, second=0, microsecond=0)
        market_open = et_now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
        market_close = et_now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
        
        is_active_window = is_weekday and (scan_start <= et_now <= market_close)
        is_open = is_weekday and (market_open <= et_now <= market_close)
        
        next_open = market_open
        next_scan = scan_start
        if not is_weekday or et_now > market_close:
            days_add = 1
            if et_now.weekday() == 4:
                days_add = 3
            elif et_now.weekday() == 5:
                days_add = 2
            elif et_now.weekday() == 6 and et_now > market_close:
                days_add = 1
            next_open = (et_now + timedelta(days=days_add)).replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
            next_scan = (et_now + timedelta(days=days_add)).replace(hour=scan_hour, minute=scan_minute, second=0, microsecond=0)
        elif et_now < scan_start:
            if not is_weekday:
                days_add = 2 if et_now.weekday() == 5 else 1
                next_open = (et_now + timedelta(days=days_add)).replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
                next_scan = (et_now + timedelta(days=days_add)).replace(hour=scan_hour, minute=scan_minute, second=0, microsecond=0)
            else:
                next_open = market_open
                next_scan = scan_start
            
        seconds_to_open = max(0, int((next_open - et_now).total_seconds()))
        seconds_to_pre_open = max(0, int((next_scan - et_now).total_seconds()))
        
        open_day_name = next_open.strftime("%A") if next_open.date() != et_now.date() else "Today"
        time_label = "4:00 AM ET" if is_extended else "9:30 AM ET"
        next_open_str = f"{open_day_name} at {time_label}"
        
        return {
            "is_active_window": is_active_window,
            "is_open": is_open,
            "seconds_to_open": seconds_to_open,
            "seconds_to_pre_open": seconds_to_pre_open,
            "next_open_str": next_open_str,
            "market_session_label": "Extended" if is_extended else "Regular",
        }

    def _format_duration_compact(self, seconds: float) -> str:
        remaining = max(0, int(seconds))
        hours, rem = divmod(remaining, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes}m {secs}s"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def _find_position(self, positions, ticker_norm: str):
        ticker_norm = ticker_norm.upper().replace("/", "")
        for p in positions or []:
            if p.get('symbol', '').replace("/", "").upper() == ticker_norm:
                return p
        return None

    def _exit_level_change_threshold(self, price: float) -> float:
        try:
            return max(0.01, abs(float(price)) * 0.0005)
        except Exception:
            return 0.01

    def _allocation_amount(self, t_settings: dict, mode: str = 'regular') -> float:
        if mode == 'options':
            allocation_mode = str(t_settings.get('options_allocation_mode', t_settings.get('allocation_mode', 'fixed')) or 'fixed').lower()
            base_key = 'options_trade_amount'
            compound_key = 'options_compound_amount'
        else:
            allocation_mode = str(t_settings.get('allocation_mode', 'fixed') or 'fixed').lower()
            base_key = 'amount'
            compound_key = 'compound_amount'

        if allocation_mode == 'compound':
            amount = _safe_float(t_settings.get(compound_key), 0.0)
            if amount <= 0:
                amount = _safe_float(t_settings.get(base_key), 0.0)
            return max(0.0, amount)
        return max(0.0, _safe_float(t_settings.get(base_key), 0.0))

    def _apply_compound_pl(self, t_settings: dict, pl: float, mode: str = 'regular'):
        if mode == 'options':
            allocation_mode = str(t_settings.get('options_allocation_mode', t_settings.get('allocation_mode', 'fixed')) or 'fixed').lower()
            base_key = 'options_trade_amount'
            compound_key = 'options_compound_amount'
        else:
            allocation_mode = str(t_settings.get('allocation_mode', 'fixed') or 'fixed').lower()
            base_key = 'amount'
            compound_key = 'compound_amount'

        if allocation_mode != 'compound':
            return None
        current = _safe_float(t_settings.get(compound_key), 0.0)
        if current <= 0:
            current = _safe_float(t_settings.get(base_key), 0.0)
        next_amount = max(1.0, round(current + _safe_float(pl, 0.0), 2))
        t_settings[compound_key] = next_amount
        return next_amount

    def _normalize_pct_value(self, value, fallback: float) -> float:
        pct = _safe_float(value, fallback)
        # Accept both UI decimal values (0.10) and defensive whole-percent values (10).
        if pct > 1:
            pct = pct / 100.0
        return max(0.0, pct)

    def _option_pct_exit_settings(self, t_settings: dict):
        stop_loss_pct = self._normalize_pct_value(
            t_settings.get('options_stop_loss_pct', t_settings.get('regular_stop_loss_pct', 0.10)),
            0.10
        )
        take_profit_pct = self._normalize_pct_value(
            t_settings.get('options_take_profit_pct', t_settings.get('regular_take_profit_pct', 0.20)),
            0.20
        )
        trailing_pct = self._normalize_pct_value(
            t_settings.get('options_trailing_pct', t_settings.get('regular_trailing_pct', 0.10)),
            0.10
        )
        return stop_loss_pct, take_profit_pct, trailing_pct

    def _get_option_exit_price(self, option_symbol: str, fallback_price: float) -> dict:
        fallback_price = _safe_float(fallback_price, 0.0)
        quote = {
            'price': fallback_price,
            'source': 'alpaca_position_current_price',
            'bid': None,
            'ask': None,
            'mid': None,
        }
        if self.broker.simulation_mode:
            return quote
        if not getattr(self.config, 'ALPACA_API_KEY', None) or not getattr(self.config, 'ALPACA_SECRET_KEY', None):
            return quote
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.requests import OptionLatestQuoteRequest
            data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
            req = OptionLatestQuoteRequest(symbol_or_symbols=[option_symbol])
            quotes = data_client.get_option_latest_quote(req)
            latest = quotes.get(option_symbol) if hasattr(quotes, 'get') else quotes[option_symbol]
            bid = _safe_float(getattr(latest, 'bid_price', None), 0.0)
            ask = _safe_float(getattr(latest, 'ask_price', None), 0.0)
            mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else 0.0
            # Long option exits are sell orders, so bid is the most honest trigger price.
            exit_price = bid if bid > 0 else (mid if mid > 0 else fallback_price)
            quote.update({
                'price': exit_price,
                'source': 'alpaca_option_latest_quote_bid' if bid > 0 else 'alpaca_option_latest_quote_mid',
                'bid': bid if bid > 0 else None,
                'ask': ask if ask > 0 else None,
                'mid': mid if mid > 0 else None,
            })
        except Exception as exc:
            print(f"[options-exit] Latest quote unavailable for {option_symbol}: {exc}. Using position current_price={fallback_price:.2f}")
        return quote

    def _buy_option_contract(
        self,
        ticker: str,
        option_type: str,
        result: dict,
        t_settings: dict,
        opt_expiry_days,
        opt_strike_type,
        opt_trade_amount: float,
    ) -> tuple[bool, dict]:
        option_type = str(option_type or "").upper()
        if option_type not in {"CALL", "PUT"}:
            result["reason"] = "Skipped: invalid option contract direction."
            return False, result

        from option_selector import get_option_chain, select_contract
        chain = get_option_chain(
            underlying=ticker,
            expiration_limit_days=opt_expiry_days,
            current_price=result['price_raw'],
            client=self.broker.client
        )
        selection_data_client = None
        if not self.broker.simulation_mode and self.config.ALPACA_API_KEY and self.config.ALPACA_SECRET_KEY:
            try:
                from alpaca.data.historical.option import OptionHistoricalDataClient
                selection_data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
            except Exception as data_client_err:
                print(f"[options] Delta selection unavailable: {data_client_err}")
        contract = select_contract(
            contracts=chain,
            current_underlying_price=result['price_raw'],
            option_type=option_type,
            strike_type=opt_strike_type,
            target_expiry_days=opt_expiry_days,
            data_client=selection_data_client
        )
        if not contract:
            result['reason'] = f"Skipped: no suitable {option_type} contract found."
            return False, result

        import math
        quote_bid = quote_ask = quote_spread = quote_spread_pct = None
        if self.broker.simulation_mode:
            from options_pricing import black_scholes
            import datetime
            days = (contract['expiration'] - datetime.date.today()).days
            t_years = max(0.0001, days / 365.0)
            est_premium = black_scholes(
                result['price_raw'],
                contract['strike'],
                t_years,
                r=0.05,
                sigma=0.20,
                option_type=option_type,
            )
            est_premium = max(0.05, est_premium)
        else:
            try:
                from alpaca.data.historical.option import OptionHistoricalDataClient
                from alpaca.data.requests import OptionLatestQuoteRequest
                data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                req = OptionLatestQuoteRequest(symbol_or_symbols=[contract['symbol']])
                quotes = data_client.get_option_latest_quote(req)
                quote = quotes[contract['symbol']]
                quote_bid = _safe_float(getattr(quote, 'bid_price', None), 0.0)
                quote_ask = _safe_float(getattr(quote, 'ask_price', None), 0.0)
                est_premium = quote_ask
                if quote_bid > 0 and quote_ask > 0:
                    quote_spread = quote_ask - quote_bid
                    quote_mid = (quote_bid + quote_ask) / 2.0
                    quote_spread_pct = (quote_spread / quote_mid) * 100.0 if quote_mid > 0 else None
            except Exception as quote_err:
                print(f"[options] Quote fetch failed (no OPRA?): {quote_err}. Falling back to theoretical pricing.")
                from options_pricing import black_scholes
                import datetime
                days = (contract['expiration'] - datetime.date.today()).days
                t_years = max(0.0001, days / 365.0)
                est_premium_bs = black_scholes(
                    result['price_raw'],
                    contract['strike'],
                    t_years,
                    r=0.05,
                    sigma=0.24,
                    option_type=option_type,
                    q=0.013,
                )
                est_premium = max(0.01, est_premium_bs + 0.015)

        spread_limit_pct = _safe_float(
            t_settings.get('options_max_entry_spread_pct'),
            15.0 if str(opt_strike_type).upper() == 'OTM' else 12.0
        )
        if not self.broker.simulation_mode and (_safe_float(quote_bid, 0.0) <= 0 or _safe_float(quote_ask, 0.0) <= 0):
            result['reason'] = f"Skipped: live option bid/ask unavailable for {contract['symbol']}."
            return False, result
        if not self.broker.simulation_mode and quote_spread_pct is not None and quote_spread_pct > spread_limit_pct:
            result['reason'] = f"Skipped: option spread too wide for {contract['symbol']} ({quote_spread_pct:.1f}% > {spread_limit_pct:.1f}%)."
            return False, result
        if est_premium <= 0:
            result['reason'] = "Skipped: option premium estimate could not be retrieved."
            return False, result

        allocated = opt_trade_amount
        qty = math.floor(allocated / (est_premium * 100.0))
        if qty <= 0:
            result['reason'] = f"Skipped: single contract cost (${est_premium * 100:.2f}) exceeds allocation (${allocated:.2f})"
            return False, result

        order_result = self.broker.place_option_order(
            option_symbol=contract['symbol'],
            qty=qty,
            side='buy',
            limit_price=None
        )
        if not order_result.get('success'):
            result['reason'] = f"Option buy failed: {order_result.get('error')}"
            return False, result

        result['order'] = order_result
        result['order_id'] = order_result.get('order_id')
        result['qty'] = qty
        result['total_cost'] = qty * est_premium * 100.0
        result['fees'] = 0.0
        result['option_entry_quote'] = {
            'bid': quote_bid,
            'ask': quote_ask,
            'spread': quote_spread,
            'spread_pct': quote_spread_pct,
            'limit_price': est_premium,
        }
        spread_note = f" | bid/ask ${quote_bid:.2f}/${quote_ask:.2f}, spread {quote_spread_pct:.1f}%" if quote_spread_pct is not None else ""
        selection_note = f" | {contract.get('selection_reason')}" if contract.get('selection_reason') else ""
        result['reason'] = f"BOUGHT {qty} {option_type} contracts ({contract['symbol']}) with limit ${est_premium:.2f}{spread_note}{selection_note}: {result['reason']}"
        t_settings['options_underlying_entry_price'] = float(result.get('price_raw') or 0.0)
        t_settings['options_active_contract'] = contract['symbol']
        try:
            fresh_positions = self.broker.get_positions()
            fresh_option_pos = self._find_position(
                fresh_positions,
                contract['symbol'].replace("/", "").upper(),
            )
            if fresh_option_pos:
                from main import _ensure_exit_forecast_snapshot
                _ensure_exit_forecast_snapshot(
                    t_settings,
                    result,
                    fresh_option_pos,
                    ticker,
                )
        except Exception as snapshot_err:
            print(f"[forecast] {ticker} {option_type} snapshot deferred until position fill is visible: {snapshot_err}")
        return True, result

    def _build_exit_protection_targets(self, ticker: str, result: dict, pos_info: dict, t_settings: dict):
        sell_mode = t_settings.get('sell_mode', 'indicator')
        if sell_mode not in FIXED_LEVEL_EXIT_MODES:
            return None

        ticker_norm = ticker.upper().replace("/", "")
        is_crypto = _is_crypto_symbol(ticker_norm)
        entry_price = _safe_float(pos_info.get('avg_price'), _safe_float(result.get('price_raw'), 0.0))
        current_price = _safe_float(result.get('price_raw'), _safe_float(pos_info.get('current_price'), 0.0))
        qty = abs(_safe_float(pos_info.get('qty'), 0.0))
        precision = 6 if entry_price < 1.0 or is_crypto else 2
        tp = sl = None
        source = sell_mode

        if qty <= 0 or entry_price <= 0:
            return None

        if sell_mode == 'custom_sltp':
            sl = _safe_float(t_settings.get('custom_stop_loss_price'), 0.0)
            tp = _safe_float(t_settings.get('custom_take_profit_price'), 0.0)

        elif sell_mode in ('percentage', 'hybrid_pct'):
            sl_pct = float(t_settings.get('regular_stop_loss_pct', 0.05))
            tp_pct = float(t_settings.get('regular_take_profit_pct', 0.10))
            trail_pct = float(t_settings.get('regular_trailing_pct', 0.03))
            enable_trail = bool(t_settings.get('enable_pct_trail', False))
            base_stop = round(entry_price * (1 - sl_pct), precision)
            if enable_trail:
                if ticker not in self.last_trailing_stops:
                    self.last_trailing_stops[ticker] = base_stop
                if current_price > 0:
                    candidate = round(current_price * (1 - trail_pct), precision)
                    if candidate > self.last_trailing_stops[ticker]:
                        self.last_trailing_stops[ticker] = candidate
                sl = self.last_trailing_stops[ticker]
            else:
                sl = base_stop
            tp = round(entry_price * (1 + tp_pct), precision)

        elif sell_mode in ('sltp', 'hybrid'):
            atr = _safe_float(result.get('atr'), 0.0)
            if atr <= 0:
                return None
            stop_mult = float(t_settings.get('atr_stop_multiplier', 2.0))
            trail_mult = float(t_settings.get('atr_trail_multiplier', 3.0))
            tp_mult = float(t_settings.get('take_profit_multiplier', 4.0))
            enable_atr_trail = bool(t_settings.get('enable_atr_trail', False))
            base_stop = entry_price - (atr * stop_mult)
            if enable_atr_trail:
                if ticker not in self.last_trailing_stops:
                    self.last_trailing_stops[ticker] = base_stop
                if current_price > 0:
                    candidate = current_price - (atr * trail_mult)
                    if candidate > self.last_trailing_stops[ticker]:
                        self.last_trailing_stops[ticker] = round(candidate, precision)
                sl = round(max(base_stop, self.last_trailing_stops[ticker]), precision)
            else:
                sl = round(base_stop, precision)
            tp = round(entry_price + (atr * tp_mult), precision)

        elif sell_mode in ('pivot_points', 'hybrid_pivot'):
            prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
            if not prior_day:
                return None
            prev_h = prior_day['high']
            prev_l = prior_day['low']
            prev_c = prior_day['close']
            pivot_levels = _dynamic_pivot_levels(prev_h, prev_l, prev_c, current_price)
            if not pivot_levels:
                return None
            tp = round(pivot_levels['tp'], precision)
            sl = round(pivot_levels['sl'], precision)

        elif sell_mode in ('high_low', 'hybrid_high_low'):
            prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
            if not prior_day:
                return None
            hl_buffer = resolve_high_low_buffer_pct(t_settings)
            hl_levels = _dynamic_high_low_levels(prior_day['high'], prior_day['low'], current_price, hl_buffer)
            if not hl_levels:
                return None
            tp = round(hl_levels['tp'], precision)
            sl = round(hl_levels['sl'], precision)

        elif sell_mode in ('swing_points', 'hybrid_swing'):
            swing_exit = build_swing_exit(
                result.get('price_history', []),
                current_price=current_price,
                is_long=True,
                lookback_days=int(t_settings.get('swing_exit_lookback_days', t_settings.get('swing_lookback_days', 20)) or 20),
                market_session=t_settings.get('market_session', 'Regular'),
                is_crypto=is_crypto,
            )
            tp = swing_exit.get('take_profit')
            sl = swing_exit.get('stop_loss')

        if tp is None or sl is None:
            return None

        tp = round(float(tp), precision)
        sl = round(float(sl), precision)
        if tp <= 0 or sl <= 0 or tp <= sl:
            return None

        return {
            'symbol': ticker_norm,
            'qty': qty,
            'take_profit': tp,
            'stop_loss': sl,
            'strategy': source,
            'asset_class': 'crypto' if is_crypto else 'stock',
            'protection_type': 'watcher',
            'current_price': current_price,
        }

    def _sync_exit_protection(self, ticker: str, result: dict, pos_info: dict, t_settings: dict, force: bool = False):
        ticker_norm = ticker.upper().replace("/", "")
        sell_mode = t_settings.get('sell_mode', 'indicator')
        if not pos_info or _safe_float(pos_info.get('qty'), 0.0) <= 0:
            return None

        if sell_mode not in FIXED_LEVEL_EXIT_MODES:
            cancel_res = self.broker.cancel_exit_orders(ticker_norm)
            if cancel_res.get('cancelled', 0) > 0:
                print(f"[exit-protect] {ticker_norm} cancelled stale broker exit order because exit mode is {sell_mode}")
            return None

        targets = self._build_exit_protection_targets(ticker, result, pos_info, t_settings)
        if not targets:
            return None

        if t_settings.get('exit_protection_enabled', True) is False:
            return None

        cancel_res = self.broker.cancel_exit_orders(ticker_norm)
        if cancel_res.get('cancelled', 0) > 0:
            print(f"[exit-protect] {ticker_norm} cancelled stale broker exit order; watcher protection is active")

        precision = 6 if _is_crypto_symbol(ticker_norm) or min(targets['take_profit'], targets['stop_loss']) < 1 else 2
        status = f"Active | Watcher | TP ${targets['take_profit']:.{precision}f} | SL ${targets['stop_loss']:.{precision}f}"
        return {'status': status, 'active': True, **targets}

    def _cancel_exit_protection(self, ticker: str, reason: str = ""):
        ticker_norm = ticker.upper().replace("/", "")
        res = self.broker.cancel_exit_orders(ticker_norm)
        if res.get('cancelled', 0) > 0:
            detail = f" ({reason})" if reason else ""
            print(f"[exit-protect] {ticker_norm} cancelled broker exit order{detail}")
        return res

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

                # Watchlist rows and dashboard charts have their own data refresh.
                # The 60s trading loop is dedicated strictly to active bots in TRADELIST
                # and prioritized over dashboard data refresh.
                scan_order = list(dict.fromkeys(self.config.TRADELIST))
                force_triggered = self.force_scan_trigger.is_set()
                
                for ticker in scan_order:
                    try:
                        ticker_norm = ticker.upper().replace("/", "")
                        t_settings = self.config.TICKER_SETTINGS.get(ticker, {})
                        is_active = ticker in self.config.TRADELIST
                        
                        if t_settings.get('paused', False):
                            continue

                        is_opt_bot = t_settings.get('options_trading', t_settings.get('is_option', False))
                        required_interval = getattr(self.config, 'OPTIONS_SCAN_INTERVAL_SECONDS', 10) if is_opt_bot else getattr(self.config, 'SCAN_INTERVAL_SECONDS', 60)
                        now_ts = time.time()
                        last_ts = self.last_bot_scan_time.get(ticker, 0)
                        if not force_triggered and (now_ts - last_ts) < (required_interval - 1.0):
                            continue
                        self.last_bot_scan_time[ticker] = now_ts

                        if is_opt_bot:
                            opt_market_status = self.get_options_market_status()
                            if not opt_market_status["is_active_window"] and not force_triggered:
                                closed_log = {
                                    "time": self.get_now().isoformat(),
                                    "action": "HOLD",
                                    "ticker": ticker,
                                    "price": "$0.00",
                                    "price_raw": 0.0,
                                    "reason": f"🛑 Options Market Closed. Starts scanning 2m before market open ({opt_market_status['next_open_str']}).",
                                    "summary": f"🛑 Options Market Closed. Starts scanning 2m before open ({opt_market_status['next_open_str']}).",
                                    "detail": f"Starts scanning in {self._format_duration_compact(opt_market_status['seconds_to_pre_open'])} (2m prior to market opening).",
                                    "status": "MARKET_CLOSED",
                                    "is_options_market_closed": True,
                                    "seconds_to_open": opt_market_status["seconds_to_open"],
                                    "seconds_to_pre_open": opt_market_status["seconds_to_pre_open"],
                                    "next_open_str": opt_market_status["next_open_str"],
                                    "refresh_rate_sec": max(10, min(3600, opt_market_status["seconds_to_pre_open"])),
                                    "signals": {},
                                    "bullish_count": 0,
                                    "bearish_count": 0,
                                    "total_signals": 0,
                                    "is_option": True,
                                }
                                self.scan_log_state[ticker] = closed_log
                                self.bot_scans[ticker] = closed_log
                                tf_bucket = self.bot_scans_by_tf.setdefault(t_settings.get('timeframe', self.config.DEFAULT_TIMEFRAME), {})
                                tf_bucket[ticker] = closed_log
                                continue
                        elif not _is_crypto_symbol(ticker_norm):
                            market_session = str(t_settings.get("market_session", "Regular")).strip().lower()
                            stock_market_status = self.get_stock_market_status(market_session)
                            if not stock_market_status["is_active_window"] and not force_triggered:
                                session_label = stock_market_status["market_session_label"]
                                closed_log = {
                                    "time": self.get_now().isoformat(),
                                    "action": "HOLD",
                                    "ticker": ticker,
                                    "price": "$0.00",
                                    "price_raw": 0.0,
                                    "reason": f"🛑 {session_label} Market Closed. Starts scanning 2m before market open ({stock_market_status['next_open_str']}).",
                                    "summary": f"🛑 {session_label} Market Closed. Starts scanning 2m before open ({stock_market_status['next_open_str']}).",
                                    "detail": f"Starts scanning in {self._format_duration_compact(stock_market_status['seconds_to_pre_open'])} (2m prior to market opening).",
                                    "status": "MARKET_CLOSED",
                                    "is_stock_market_closed": True,
                                    "market_session": session_label,
                                    "seconds_to_open": stock_market_status["seconds_to_open"],
                                    "seconds_to_pre_open": stock_market_status["seconds_to_pre_open"],
                                    "next_open_str": stock_market_status["next_open_str"],
                                    "refresh_rate_sec": max(60, min(3600, stock_market_status["seconds_to_pre_open"])),
                                    "signals": {},
                                    "bullish_count": 0,
                                    "bearish_count": 0,
                                    "total_signals": 0,
                                    "is_option": False,
                                }
                                self.scan_log_state[ticker] = closed_log
                                self.bot_scans[ticker] = closed_log
                                tf_bucket = self.bot_scans_by_tf.setdefault(t_settings.get('timeframe', self.config.DEFAULT_TIMEFRAME), {})
                                tf_bucket[ticker] = closed_log
                                continue

                        # --- Bot Loss Limit (Drawdown Limit on Allocated Bot Capital) ---
                        enable_risk = t_settings.get('enable_risk_management', True)
                        allocated_capital = self._allocation_amount(t_settings, 'options' if t_settings.get('options_trading') else 'regular') or float(t_settings.get('amount', 0.0))
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
                                    close_res = self.broker.close_position(
                                        ticker,
                                        market_session=t_settings.get('market_session', 'Regular'),
                                        current_price=pos_info.get('current_price') if pos_info else None
                                    )
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
                            if (now - self.last_scan_timestamps[ticker]).total_seconds() < max(1, required_interval - 1):
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
                                    
                                configured_allocation = self._allocation_amount(t_settings, 'regular')
                                notional_size = configured_allocation if configured_allocation > 0 else equity * pos_pct
                                notional_size = min(notional_size, avail_cash)
                                
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
                                    use_bot_settings=True,
                                    ignore_cache=is_opt_bot or force_triggered
                                )
                                if bot_result:
                                    self._record_bot_scan(bot_result)
                                    result = bot_result
                                    
                        if result:
                            if float(result.get('price_raw', 0.0)) <= 0:
                                result['action'] = 'HOLD'
                                result['reason'] = 'Live quote unavailable ($0.00) - Trade evaluation skipped'
                            trade_executed = False

                            if is_active and float(result.get('price_raw', 0.0)) > 0:
                                market_session = str(t_settings.get('market_session', 'Regular')).strip().lower()
                                is_options_enabled = t_settings.get('options_trading', getattr(self.config, 'ENABLE_OPTIONS_TRADING', False)) or t_settings.get('is_option', False)
                                if is_crypto:
                                    is_market_closed_skip = False
                                elif is_options_enabled:
                                    is_market_closed_skip = self.config.get('MARKET_HOURS_ONLY', True) and not self.get_options_market_status()["is_open"]
                                else:
                                    is_market_closed_skip = self.config.get('MARKET_HOURS_ONLY', True) and not self.get_stock_market_status(market_session)["is_open"]
                                
                                if is_market_closed_skip:
                                    result['reason'] = "Market Closed. Trade execution skipped."
                                    if result['action'] in ['BUY', 'SELL']:
                                        result['action'] = 'HOLD'

                                tf_seconds = parse_timeframe_to_seconds(tf_to_use)
                                
                                # Check options trading toggle
                                is_options_enabled = t_settings.get('options_trading', getattr(self.config, 'ENABLE_OPTIONS_TRADING', False))
                                
                                # Resolve options configurations from ticker settings with config fallback
                                opt_stop_loss, opt_take_profit, opt_trailing_pct = self._option_pct_exit_settings(t_settings)
                                # sell_mode is the visible UI setting. Prefer it over legacy/stale options_exit_strategy.
                                opt_exit_strategy = t_settings.get('sell_mode') or t_settings.get('options_exit_strategy') or getattr(self.config, 'OPTIONS_EXIT_STRATEGY', 'hybrid')
                                opt_expiry_days = t_settings.get('options_expiry_days', getattr(self.config, 'OPTIONS_EXPIRY_DAYS', 7))
                                opt_strike_type = t_settings.get('options_strike_type', getattr(self.config, 'OPTIONS_STRIKE_TYPE', 'ATM'))
                                opt_trade_amount = self._allocation_amount(t_settings, 'options') or float(getattr(self.config, 'OPTIONS_TRADE_AMOUNT', 100.0))
                                
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
                                    smart_reentry_type = str(t_settings.get('smart_reentry_type', getattr(self.config, 'SMART_REENTRY_TYPE', 'RSI')) or 'RSI').upper()
                                    smart_reentry_rsi_call_max = _safe_float(t_settings.get('smart_reentry_rsi_call_max'), _safe_float(getattr(self.config, 'SMART_REENTRY_RSI_CALL_MAX', 70), 70.0))
                                    smart_reentry_rsi_put_min = _safe_float(t_settings.get('smart_reentry_rsi_put_min'), _safe_float(getattr(self.config, 'SMART_REENTRY_RSI_PUT_MIN', 30), 30.0))
                                    smart_reentry_vwap_call_max_dist_pct = _safe_float(t_settings.get('smart_reentry_vwap_call_max_dist_pct'), _safe_float(getattr(self.config, 'SMART_REENTRY_VWAP_CALL_MAX_DIST_PCT', 0.5), 0.5))
                                    smart_reentry_vwap_put_max_dist_pct = _safe_float(t_settings.get('smart_reentry_vwap_put_max_dist_pct'), _safe_float(getattr(self.config, 'SMART_REENTRY_VWAP_PUT_MAX_DIST_PCT', 0.5), 0.5))

                                    # Apply Signal Reset Check/Clear
                                    if enable_signal_reset:
                                        action = result.get('action', 'HOLD')
                                        locked_dir = self._get_signal_reset_lock(ticker, t_settings)
                                        if locked_dir:
                                            if action != locked_dir and self._signal_reset_is_reset(result, locked_dir, t_settings):
                                                if self._clear_signal_reset_lock(ticker, t_settings):
                                                    await self.save_settings()
                                            elif action == locked_dir:
                                                result['action'] = 'HOLD'
                                                reset_count = self._signal_reset_count(result, locked_dir)
                                                reset_threshold = self._signal_reset_threshold(locked_dir, t_settings)
                                                result['reason'] = f"Signal Reset Active: Blocked stale {locked_dir} re-entry until directional signal resets below {reset_threshold} (now {reset_count:g})."
                                            else:
                                                result['reason'] = f"Signal Reset Active: waiting for stale {locked_dir} signal to cool below threshold."
                                    else:
                                        if self._clear_signal_reset_lock(ticker, t_settings):
                                            await self.save_settings()

                                    # Apply Smart Re-entry Pullback Filters
                                    if enable_smart_reentry:
                                        action = result.get('action', 'HOLD')
                                        if action in ['BUY', 'SELL']:
                                            if smart_reentry_type == 'RSI':
                                                rsi = _safe_float(result.get('rsi'), None)
                                                if rsi is not None:
                                                    if action == 'BUY' and rsi > smart_reentry_rsi_call_max:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry RSI Filter: Blocked BUY since RSI ({rsi:.1f}) > {smart_reentry_rsi_call_max} (Overbought)."
                                                    elif action == 'SELL' and rsi < smart_reentry_rsi_put_min:
                                                        result['action'] = 'HOLD'
                                                        result['reason'] = f"Smart Re-entry RSI Filter: Blocked SELL since RSI ({rsi:.1f}) < {smart_reentry_rsi_put_min} (Oversold)."
                                            elif smart_reentry_type == 'VWAP':
                                                vwap = _safe_float(result.get('vwap'), 0.0)
                                                price = _safe_float(result.get('price_raw'), 0.0)
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
                                    # Ensure indicator exits trigger when holding open positions
                                    if sell_mode in ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_atr', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing']:
                                        min_sell_req = int(t_settings.get('min_sell_signals', t_settings.get('sell_threshold', t_settings.get('threshold', getattr(self.config, 'MIN_BEARISH_SIGNALS', 1)))) or 1)
                                        bearish_cnt = result.get('bearish_count', 0)
                                        bullish_cnt = result.get('bullish_count', 0)
                                        if bearish_cnt >= min_sell_req and bearish_cnt >= bullish_cnt and result.get('action') != 'SELL':
                                            result['action'] = 'SELL'
                                            result['reason'] = f"SELL Triggered (Indicator Exit): {bearish_cnt} bearish signals confirmed"
                                    if is_options_enabled:
                                        # Options Exits
                                        from option_selector import parse_occ_symbol
                                        parsed_occ = parse_occ_symbol(pos_info['symbol'])
                                        opt_type = parsed_occ['type'] if parsed_occ else 'CALL'
                                        
                                        exit_triggered = False
                                        exit_reason = ""
                                        swing_exit_lookback_days = int(t_settings.get('swing_exit_lookback_days', t_settings.get('swing_lookback_days', 20)) or 20)
                                        swing_session = t_settings.get('market_session', 'Regular')
                                        is_crypto_underlying = any(str(ticker_norm).endswith(base) for base in ["USD", "USDT", "USDC"]) or ticker.upper() in ["BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"]
                                        current_price = result['price_raw']
                                        
                                        # 1. Premium-based check
                                        quote_info = self._get_option_exit_price(pos_info['symbol'], pos_info.get('current_price'))
                                        opt_curr = float(quote_info['price'])
                                        result['option_exit_check'] = {
                                            'premium': opt_curr,
                                            'source': quote_info.get('source'),
                                            'bid': quote_info.get('bid'),
                                            'ask': quote_info.get('ask'),
                                            'mid': quote_info.get('mid'),
                                            'strategy': opt_exit_strategy,
                                        }
                                        opt_avg = float(pos_info['avg_price'])
                                        stop_loss_pct = opt_stop_loss
                                        take_profit_pct = opt_take_profit
                                        option_trail_pct = opt_trailing_pct
                                        
                                        # Strategy A: Premium percent triggers
                                        if opt_exit_strategy in ['premium_pct', 'percentage', 'hybrid_pct']:
                                            base_stop = opt_avg * (1 - stop_loss_pct)
                                            stop_loss_price = base_stop
                                            trail_key = pos_info.get('symbol', ticker)
                                            if bool(t_settings.get('enable_pct_trail', False)) and option_trail_pct > 0:
                                                if trail_key not in self.last_trailing_stops:
                                                    self.last_trailing_stops[trail_key] = base_stop
                                                candidate = opt_curr * (1 - option_trail_pct)
                                                if candidate > self.last_trailing_stops[trail_key]:
                                                    self.last_trailing_stops[trail_key] = candidate
                                                stop_loss_price = self.last_trailing_stops[trail_key]
                                            if opt_curr <= stop_loss_price:
                                                exit_triggered = True
                                                exit_reason = (
                                                    f"Option Premium SL Hit (${opt_curr:.2f} <= ${stop_loss_price:.2f}; "
                                                    f"entry ${opt_avg:.2f}, SL {stop_loss_pct*100:.1f}%, trail {option_trail_pct*100:.1f}%, "
                                                    f"price_source={quote_info['source']})"
                                                )
                                            elif opt_curr >= opt_avg * (1 + take_profit_pct):
                                                exit_triggered = True
                                                exit_reason = (
                                                    f"Option Premium TP Hit (${opt_curr:.2f} >= ${opt_avg*(1+take_profit_pct):.2f}; "
                                                    f"entry ${opt_avg:.2f}, TP {take_profit_pct*100:.1f}%, "
                                                    f"price_source={quote_info['source']})"
                                                )

                                        # Strategy A1: Underlying ATR trigger, anchored to the underlying entry when known.
                                        if not exit_triggered and opt_exit_strategy in ['sltp', 'hybrid']:
                                            atr_val = _safe_float(result.get('atr'), 0.0)
                                            entry_underlying = _safe_float(t_settings.get('options_underlying_entry_price'), current_price)
                                            stop_mult = _safe_float(t_settings.get('atr_stop_multiplier'), 2.0)
                                            trail_mult = _safe_float(t_settings.get('atr_trail_multiplier'), 3.0)
                                            tp_mult = _safe_float(t_settings.get('take_profit_multiplier'), 4.0)
                                            if atr_val > 0 and entry_underlying > 0:
                                                trail_key = pos_info.get('symbol', ticker)
                                                if opt_type == 'CALL':
                                                    atr_sl = entry_underlying - (atr_val * stop_mult)
                                                    atr_tp = entry_underlying + (atr_val * tp_mult)
                                                    effective_sl = atr_sl
                                                    if bool(t_settings.get('enable_atr_trail', False)) and trail_mult > 0:
                                                        candidate = current_price - (atr_val * trail_mult)
                                                        previous = self.last_trailing_stops.get(trail_key, atr_sl)
                                                        effective_sl = max(atr_sl, previous, candidate)
                                                        self.last_trailing_stops[trail_key] = effective_sl
                                                    if current_price <= effective_sl:
                                                        exit_triggered = True
                                                        exit_reason = f"Option ATR SL Hit (Underlying {current_price:.2f} <= {effective_sl:.2f})"
                                                    elif current_price >= atr_tp:
                                                        exit_triggered = True
                                                        exit_reason = f"Option ATR TP Hit (Underlying {current_price:.2f} >= {atr_tp:.2f})"
                                                else:
                                                    atr_sl = entry_underlying + (atr_val * stop_mult)
                                                    atr_tp = entry_underlying - (atr_val * tp_mult)
                                                    effective_sl = atr_sl
                                                    if bool(t_settings.get('enable_atr_trail', False)) and trail_mult > 0:
                                                        candidate = current_price + (atr_val * trail_mult)
                                                        previous = self.last_trailing_stops.get(trail_key, atr_sl)
                                                        effective_sl = min(atr_sl, previous, candidate)
                                                        self.last_trailing_stops[trail_key] = effective_sl
                                                    if current_price >= effective_sl:
                                                        exit_triggered = True
                                                        exit_reason = f"Option ATR SL Hit (Underlying {current_price:.2f} >= {effective_sl:.2f})"
                                                    elif current_price <= atr_tp:
                                                        exit_triggered = True
                                                        exit_reason = f"Option ATR TP Hit (Underlying {current_price:.2f} <= {atr_tp:.2f})"

                                        # Strategy A2: Custom premium SL/TP triggers
                                        if not exit_triggered and opt_exit_strategy == 'custom_sltp':
                                            custom_sl = _safe_float(t_settings.get('custom_stop_loss_price'), 0.0)
                                            custom_tp = _safe_float(t_settings.get('custom_take_profit_price'), 0.0)
                                            if custom_sl > 0 and opt_curr <= custom_sl:
                                                exit_triggered = True
                                                exit_reason = f"Option Custom SL Hit (${opt_curr:.2f} <= ${custom_sl:.2f})"
                                            elif custom_tp > 0 and opt_curr >= custom_tp:
                                                exit_triggered = True
                                                exit_reason = f"Option Custom TP Hit (${opt_curr:.2f} >= ${custom_tp:.2f})"

                                        # Strategy A3: Custom underlying-price SL/TP triggers
                                        if not exit_triggered and opt_exit_strategy == 'custom_underlying_sltp':
                                            custom_u_sl = _safe_float(t_settings.get('custom_underlying_stop_loss_price'), 0.0)
                                            custom_u_tp = _safe_float(t_settings.get('custom_underlying_take_profit_price'), 0.0)
                                            if opt_type == 'CALL':
                                                if custom_u_sl > 0 and current_price <= custom_u_sl:
                                                    exit_triggered = True
                                                    exit_reason = f"Option Custom Underlying SL Hit (Underlying ${current_price:.2f} <= ${custom_u_sl:.2f})"
                                                elif custom_u_tp > 0 and current_price >= custom_u_tp:
                                                    exit_triggered = True
                                                    exit_reason = f"Option Custom Underlying TP Hit (Underlying ${current_price:.2f} >= ${custom_u_tp:.2f})"
                                            else:
                                                if custom_u_sl > 0 and current_price >= custom_u_sl:
                                                    exit_triggered = True
                                                    exit_reason = f"Option Custom Underlying SL Hit (Underlying ${current_price:.2f} >= ${custom_u_sl:.2f})"
                                                elif custom_u_tp > 0 and current_price <= custom_u_tp:
                                                    exit_triggered = True
                                                    exit_reason = f"Option Custom Underlying TP Hit (Underlying ${current_price:.2f} <= ${custom_u_tp:.2f})"
                                                
                                        # Reuse the already-fetched OHLCV candles for pivot & range exits.
                                        if not exit_triggered and opt_exit_strategy in ['pivot_points', 'hybrid_pivot', 'high_low', 'hybrid_high_low']:
                                            prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
                                            if prior_day:
                                                prev_h = prior_day['high']
                                                prev_l = prior_day['low']
                                                prev_c = prior_day['close']
                                            else:
                                                prev_h = prev_l = prev_c = result['price_raw']

                                            # Strategy B: Pivot points
                                            if opt_exit_strategy in ['pivot_points', 'hybrid_pivot']:
                                                P = (prev_h + prev_l + prev_c) / 3.0
                                                R1 = 2 * P - prev_l
                                                S1 = 2 * P - prev_h

                                                if opt_type == 'CALL':
                                                    if current_price >= R1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot TP Hit (Underlying {current_price:.2f} >= R1 {R1:.2f})"
                                                    elif current_price <= S1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot SL Hit (Underlying {current_price:.2f} <= S1 {S1:.2f})"
                                                else:  # PUT
                                                    if current_price <= S1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot TP Hit (Underlying {current_price:.2f} <= S1 {S1:.2f})"
                                                    elif current_price >= R1:
                                                        exit_triggered = True
                                                        exit_reason = f"Pivot SL Hit (Underlying {current_price:.2f} >= R1 {R1:.2f})"
                                                        
                                            # Strategy C: Prior Day High/Low ranges
                                            if not exit_triggered and opt_exit_strategy in ['high_low', 'hybrid_high_low']:
                                                hl_levels = buffered_prior_day_levels(
                                                    {"high": prev_h, "low": prev_l, "close": prev_c},
                                                    resolve_high_low_buffer_pct(t_settings),
                                                )
                                                if opt_type == 'CALL' and hl_levels:
                                                    if current_price >= hl_levels['high_trigger']:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day High Breakout (Underlying {current_price:.2f} >= Trigger {hl_levels['high_trigger']:.2f}, PDH {hl_levels['high']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                                                    elif current_price <= hl_levels['low_trigger']:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day Low Breakdown (Underlying {current_price:.2f} <= Trigger {hl_levels['low_trigger']:.2f}, PDL {hl_levels['low']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                                                elif opt_type == 'PUT' and hl_levels:
                                                    if current_price <= hl_levels['low_trigger']:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day Low Breakout (Underlying {current_price:.2f} <= Trigger {hl_levels['low_trigger']:.2f}, PDL {hl_levels['low']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                                                    elif current_price >= hl_levels['high_trigger']:
                                                        exit_triggered = True
                                                        exit_reason = f"Prev Day High Breakdown (Underlying {current_price:.2f} >= Trigger {hl_levels['high_trigger']:.2f}, PDH {hl_levels['high']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"

                                        # Strategy C2: Swing points exits
                                        if not exit_triggered and opt_exit_strategy in ['swing_points', 'hybrid_swing']:
                                            swing_exit = build_swing_exit(
                                                result.get('price_history', []),
                                                current_price=result.get('price_raw', current_price),
                                                is_long=(opt_type == 'CALL'),
                                                lookback_days=swing_exit_lookback_days,
                                                market_session=swing_session,
                                                is_crypto=is_crypto_underlying,
                                            )
                                            tp = swing_exit.get('take_profit')
                                            sl = swing_exit.get('stop_loss')
                                            if tp is not None and sl is not None:
                                                if opt_type == 'CALL':
                                                    if current_price >= tp:
                                                        exit_triggered = True
                                                        exit_reason = f"Swing TP Hit (Underlying {current_price:.2f} >= Resistance {tp:.2f})"
                                                    elif current_price <= sl:
                                                        exit_triggered = True
                                                        exit_reason = f"Swing SL Hit (Underlying {current_price:.2f} <= Support {sl:.2f})"
                                                else:
                                                    if current_price <= tp:
                                                        exit_triggered = True
                                                        exit_reason = f"Swing TP Hit (Underlying {current_price:.2f} <= Support {tp:.2f})"
                                                    elif current_price >= sl:
                                                        exit_triggered = True
                                                        exit_reason = f"Swing SL Hit (Underlying {current_price:.2f} >= Resistance {sl:.2f})"
                                                        
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
                                        if not exit_triggered and opt_exit_strategy in ['indicator', 'hybrid', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing']:
                                            exit_signal_threshold = int(_safe_float(
                                                t_settings.get('min_sell_signals', t_settings.get('sell_threshold', 1)),
                                                1
                                            ))
                                            bullish_count = int(_safe_float(result.get('bullish_count'), 0))
                                            bearish_count = int(_safe_float(result.get('bearish_count'), 0))
                                            call_exit_signal = bearish_count >= exit_signal_threshold
                                            put_exit_signal = bullish_count >= exit_signal_threshold
                                            if opt_type == 'CALL' and call_exit_signal:
                                                exit_triggered = True
                                                exit_reason = f"Technical Signal reversal to BEARISH ({bearish_count}/{exit_signal_threshold})"
                                            elif opt_type == 'PUT' and put_exit_signal:
                                                exit_triggered = True
                                                exit_reason = f"Technical Signal reversal to BULLISH ({bullish_count}/{exit_signal_threshold})"
                                                
                                        if exit_triggered:
                                            result['action'] = 'SELL'
                                            result['option_intent'] = 'CLOSE_OPTION'
                                            result['reason'] = exit_reason
                                        else:
                                            result['option_intent'] = 'HOLD'
                                            _mark_exit_signal_watch(
                                                result,
                                                t_settings,
                                                is_long=(opt_type == 'CALL'),
                                                prefix="Option position open",
                                            )

                                    else:
                                        # Standard Stock/Crypto Sell Mode Logic
                                        entry_price = float(pos_info.get('avg_price', result['price_raw']))
                                        current_price = float(result.get('price_raw', 0.0))
                                        if current_price <= 0:
                                            result['action'] = 'HOLD'
                                            result['reason'] = 'Live quote unavailable ($0.00)'
                                            break
                                        
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
                                            if sell_mode == 'custom_sltp':
                                                custom_sl = _safe_float(t_settings.get('custom_stop_loss_price'), 0.0)
                                                custom_tp = _safe_float(t_settings.get('custom_take_profit_price'), 0.0)
                                                if result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Custom SL/TP Mode)'
                                                if custom_sl > 0 and current_price <= custom_sl:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Custom Stop Loss Hit (${current_price:.{precision}f} <= ${custom_sl:.{precision}f})'
                                                elif custom_tp > 0 and current_price >= custom_tp:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Custom Take Profit Hit (${current_price:.{precision}f} >= ${custom_tp:.{precision}f})'
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "Custom SL/TP",
                                                        f"Position open. Checking custom exits: Price ${current_price:.{precision}f}, SL ${custom_sl:.{precision}f}, TP ${custom_tp:.{precision}f}.",
                                                        {"price": current_price, "stop_loss": custom_sl, "take_profit": custom_tp},
                                                    )

                                            elif sell_mode in ['percentage', 'hybrid_pct']:
                                                # ── NEW: Percentage-based exit ──────────────────────────────
                                                base_stop = round(entry_price * (1 - sl_pct), precision)
                                                take_profit = round(entry_price * (1 + tp_pct), precision)
                                                
                                                enable_trail = bool(t_settings.get('enable_pct_trail', False))
                                                if enable_trail:
                                                    if ticker not in self.last_trailing_stops:
                                                        self.last_trailing_stops[ticker] = base_stop
                                                    # Ratchet trailing stop upward (only ever tightens, never loosens)
                                                    candidate = round(current_price * (1 - trail_pct), precision)
                                                    if candidate > self.last_trailing_stops[ticker]:
                                                        self.last_trailing_stops[ticker] = candidate
                                                    stop_loss = self.last_trailing_stops[ticker]
                                                    sl_desc = f"Trail {trail_pct*100:.1f}%"
                                                else:
                                                    stop_loss = base_stop
                                                    sl_desc = f"Fixed SL {sl_pct*100:.1f}%"
                                                
                                                # In percentage mode: silence indicator SELL signals, rely on % levels
                                                if sell_mode == 'percentage' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Percentage SL/TP Mode)'
                                                
                                                if current_price <= stop_loss:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Percentage Stop Loss Hit (${current_price:.{precision}f} <= ${stop_loss:.{precision}f}, {sl_desc})'
                                                elif current_price >= take_profit:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Percentage Take Profit Hit (${current_price:.{precision}f} >= ${take_profit:.{precision}f}, TP {tp_pct*100:.1f}%)'
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "PCT Exit",
                                                        f"Position open. Checking PCT exits: Price ${current_price:.{precision}f}, SL ${stop_loss:.{precision}f} ({sl_desc}), TP ${take_profit:.{precision}f} ({tp_pct*100:.1f}%).",
                                                        {
                                                            "price": current_price,
                                                            "stop_loss": stop_loss,
                                                            "take_profit": take_profit,
                                                            "trail_enabled": enable_trail,
                                                            "trail_pct": trail_pct if enable_trail else None,
                                                        },
                                                    )
                                        
                                            elif sell_mode in ['pivot_points', 'hybrid_pivot']:
                                                # Pivot point exit from the scan's OHLCV history.
                                                prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
                                                if prior_day:
                                                    prev_h = prior_day['high']
                                                    prev_l = prior_day['low']
                                                    prev_c = prior_day['close']
                                                else:
                                                    prev_h = prev_l = prev_c = current_price
                                                
                                                P  = (prev_h + prev_l + prev_c) / 3.0
                                                R1 = round(2 * P - prev_l, precision)
                                                R2 = round(P + (prev_h - prev_l), precision)
                                                S1 = round(2 * P - prev_h, precision)
                                                S2 = round(P - (prev_h - prev_l), precision)
                                                dynamic_pivots = _dynamic_pivot_levels(prev_h, prev_l, prev_c, current_price)
                                                pivot_tp = round(dynamic_pivots['tp'], precision) if dynamic_pivots else R1
                                                pivot_sl = round(dynamic_pivots['sl'], precision) if dynamic_pivots else S1
                                                
                                                # In pivot mode: silence indicator SELL signals, rely on pivot levels
                                                if sell_mode == 'pivot_points' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Pivot Points Mode)'
                                                
                                                if current_price <= S2:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Stop Loss Hit (${current_price:.{precision}f} <= S2 ${S2:.{precision}f})'
                                                elif current_price <= pivot_sl:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Stop Loss Hit (${current_price:.{precision}f} <= ${pivot_sl:.{precision}f})'
                                                elif current_price >= pivot_tp:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'Pivot Take Profit Hit (${current_price:.{precision}f} >= ${pivot_tp:.{precision}f})'
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "Pivot Exit",
                                                        f"Position open. Checking Pivot exits: Price ${current_price:.{precision}f}, SL ${pivot_sl:.{precision}f}, TP ${pivot_tp:.{precision}f}.",
                                                        {"price": current_price, "stop_loss": pivot_sl, "take_profit": pivot_tp, "pivot": P},
                                                    )

                                            elif sell_mode in ['high_low', 'hybrid_high_low']:
                                                prior_day = _prior_day_ohlc_from_history(result.get('price_history', []))
                                                if prior_day:
                                                    prev_h = round(prior_day['high'], precision)
                                                    prev_l = round(prior_day['low'], precision)
                                                else:
                                                    prev_h = prev_l = current_price

                                                if sell_mode == 'high_low' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (High/Low Mode)'

                                                hl_buffer = resolve_high_low_buffer_pct(t_settings)
                                                dynamic_hl = _dynamic_high_low_levels(prev_h, prev_l, current_price, hl_buffer)
                                                hl_tp = round(dynamic_hl['tp'], precision) if dynamic_hl else prev_h
                                                hl_sl = round(dynamic_hl['sl'], precision) if dynamic_hl else prev_l

                                                if current_price >= hl_tp:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'High/Low Take Profit Hit (${current_price:.{precision}f} >= ${hl_tp:.{precision}f}, Buffer {hl_buffer:.2f}%)'
                                                elif current_price <= hl_sl:
                                                    result['action'] = 'SELL'
                                                    result['reason'] = f'High/Low Stop Loss Hit (${current_price:.{precision}f} <= ${hl_sl:.{precision}f}, Buffer {hl_buffer:.2f}%)'
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_level_watch(
                                                        result,
                                                        "PDH/PDL Exit",
                                                        f"Position open. Checking PDH/PDL exits: Price ${current_price:.{precision}f}, SL ${hl_sl:.{precision}f}, TP ${hl_tp:.{precision}f}, Buffer {hl_buffer:.2f}%.",
                                                        {"price": current_price, "stop_loss": hl_sl, "take_profit": hl_tp, "buffer_pct": hl_buffer},
                                                    )

                                            elif sell_mode in ['swing_points', 'hybrid_swing']:
                                                swing_exit = build_swing_exit(
                                                    result.get('price_history', []),
                                                    current_price=current_price,
                                                    is_long=True,
                                                    lookback_days=int(t_settings.get('swing_exit_lookback_days', t_settings.get('swing_lookback_days', 20)) or 20),
                                                    market_session=t_settings.get('market_session', 'Regular'),
                                                    is_crypto=is_crypto,
                                                )
                                                tp = swing_exit.get('take_profit')
                                                sl = swing_exit.get('stop_loss')
                                                if sell_mode == 'swing_points' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Swing Points Mode)'

                                                if tp is not None and sl is not None:
                                                    if current_price >= tp:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Swing Take Profit Hit (${current_price:.{precision}f} >= ${tp:.{precision}f})'
                                                    elif current_price <= sl:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Swing Stop Loss Hit (${current_price:.{precision}f} <= ${sl:.{precision}f})'
                                                    elif result.get('action') != 'SELL':
                                                        _mark_exit_level_watch(
                                                            result,
                                                            "Swing Exit",
                                                            f"Position open. Checking Swing exits: Price ${current_price:.{precision}f}, SL ${sl:.{precision}f}, TP ${tp:.{precision}f}.",
                                                            {"price": current_price, "stop_loss": sl, "take_profit": tp},
                                                        )
                                                elif result.get('action') != 'SELL':
                                                    _mark_exit_signal_watch(result, t_settings, is_long=True, prefix="Position open. Swing exit levels unavailable")

                                            else:
                                                # ── EXISTING: ATR-based logic (unchanged) ───────────────────
                                                stop_mult = t_settings.get('atr_stop_multiplier', 2.0)
                                                trail_mult = t_settings.get('atr_trail_multiplier', 3.0)
                                                tp_mult = t_settings.get('take_profit_multiplier', 4.0)
                                                atr_value = _safe_float(result.get('atr'), 0.0)
                                                
                                                base_stop_loss = entry_price - (atr_value * stop_mult)
                                                take_profit = entry_price + (atr_value * tp_mult)
                                                
                                                enable_atr_trail = bool(t_settings.get('enable_atr_trail', False))
                                                if enable_atr_trail:
                                                    if ticker not in self.last_trailing_stops:
                                                        self.last_trailing_stops[ticker] = base_stop_loss
                                                    trail_distance = atr_value * trail_mult
                                                    candidate_stop = current_price - trail_distance
                                                    if candidate_stop > self.last_trailing_stops[ticker]:
                                                        self.last_trailing_stops[ticker] = round(candidate_stop, precision)
                                                    stop_loss = max(base_stop_loss, self.last_trailing_stops[ticker])
                                                else:
                                                    stop_loss = base_stop_loss
                                                
                                                if sell_mode == 'sltp' and result['action'] == 'SELL':
                                                    result['action'] = 'HOLD'
                                                    result['reason'] = 'Sell Signal Ignored (Fixed SL/TP Mode)'
                                                    
                                                if sell_mode in ['sltp', 'hybrid', 'atr', 'hybrid_atr']:
                                                    if current_price <= stop_loss:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Stop Loss Hit (${current_price:.{precision}f} <= ${stop_loss:.{precision}f})'
                                                    elif current_price >= take_profit:
                                                        result['action'] = 'SELL'
                                                        result['reason'] = f'Take Profit Hit (${current_price:.{precision}f} >= ${take_profit:.{precision}f})'
                                                    elif result.get('action') != 'SELL':
                                                        trail_desc = f", Trail {trail_mult:.2f}x ATR" if enable_atr_trail else ""
                                                        _mark_exit_level_watch(
                                                            result,
                                                            "ATR Exit",
                                                            f"Position open. Checking ATR exits: Price ${current_price:.{precision}f}, SL ${stop_loss:.{precision}f}, TP ${take_profit:.{precision}f}, ATR ${atr_value:.{precision}f}{trail_desc}.",
                                                            {
                                                                "price": current_price,
                                                                "stop_loss": stop_loss,
                                                                "take_profit": take_profit,
                                                                "atr": atr_value,
                                                                "trail_enabled": enable_atr_trail,
                                                                "trail_multiplier": trail_mult if enable_atr_trail else None,
                                                            },
                                                        )

                                        if result.get('action') != 'SELL' and not result.get('exit_monitoring'):
                                            _mark_exit_signal_watch(result, t_settings, is_long=True, prefix="Position open")

                                if has_this_pos and pos_info and not is_options_enabled and not is_market_closed_skip:
                                    if result['action'] == 'SELL':
                                        self._cancel_exit_protection(ticker, result.get('reason', 'sell signal'))
                                    elif result['action'] == 'HOLD':
                                        protection = self._sync_exit_protection(ticker, result, pos_info, t_settings)
                                        if protection:
                                            result['exit_protection'] = protection

                                options_execution_handled = False
                                if is_options_enabled and not is_market_closed_skip:
                                    option_side = normalize_options_type(t_settings.get("options_type", "calls_only"), default="calls_only")
                                    option_intent = result.get("option_intent", "HOLD")
                                    if has_this_pos and result.get('action') == 'SELL':
                                        option_intent = "CLOSE_OPTION"
                                        result["option_intent"] = option_intent
                                    if option_side not in VALID_OPTION_DIRECTIONS and not has_this_pos:
                                        result['action'] = 'HOLD'
                                        result['option_intent'] = 'DISABLED'
                                        result['reason'] = "HOLD: Both direction is disabled. Choose CALL or PUT."
                                        options_execution_handled = True

                                    if option_intent in {"OPEN_CALL", "OPEN_PUT"}:
                                        options_execution_handled = True
                                        expected_contract = "CALL" if option_intent == "OPEN_CALL" else "PUT"
                                        configured_contract = options_type_to_contract_type(option_side)
                                        enable_risk = t_settings.get('enable_risk_management', True)
                                        max_pos = getattr(self.config, 'MAX_OPEN_POSITIONS', 5) if enable_risk else 999999
                                        if configured_contract != expected_contract:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"HOLD: {expected_contract} signal ignored because bot direction is {configured_contract or 'disabled'}."
                                        elif portfolio_count >= max_pos and not has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = f"Max positions reached ({portfolio_count}/{max_pos})."
                                        elif has_this_pos:
                                            result['action'] = 'HOLD'
                                            result['reason'] = "Options position already open."
                                        elif has_open_order:
                                            result['action'] = 'PENDING'
                                            result['option_intent'] = 'PENDING'
                                            result['reason'] = "Options buy order pending."
                                        else:
                                            success, result = self._buy_option_contract(
                                                ticker=ticker,
                                                option_type=expected_contract,
                                                result=result,
                                                t_settings=t_settings,
                                                opt_expiry_days=opt_expiry_days,
                                                opt_strike_type=opt_strike_type,
                                                opt_trade_amount=opt_trade_amount,
                                            )
                                            if success:
                                                trade_executed = True
                                                portfolio_count += 1
                                                result['action'] = 'BUY'
                                                result['option_intent'] = option_intent
                                                result['reason'] = f"✅ {result['reason']}"
                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                await self._increment_trade_count(ticker)
                                                await self.save_settings()

                                    elif option_intent == "CLOSE_OPTION" and has_this_pos and pos_info:
                                        options_execution_handled = True
                                        order_result = self.broker.close_position(
                                            pos_info['symbol'],
                                            market_session=t_settings.get('market_session', 'Regular'),
                                            current_price=result.get('price_raw')
                                        )
                                        if order_result.get('success'):
                                            result['order'] = order_result
                                            result['order_id'] = order_result.get('order_id')
                                            trade_executed = True
                                            portfolio_count -= 1

                                            qty = float(pos_info['qty'])
                                            exit_check_price = _safe_float(result.get('option_exit_check', {}).get('premium'), pos_info.get('current_price'))
                                            val = qty * exit_check_price * 100.0
                                            cost = qty * float(pos_info['avg_price']) * 100.0
                                            pl = val - cost
                                            pl_pct = (pl / cost * 100.0) if cost > 0 else 0.0

                                            result['action'] = 'SELL'
                                            result['option_intent'] = 'CLOSE_OPTION'
                                            result['qty'] = qty
                                            result['total_cost'] = val
                                            result['fees'] = 0.0
                                            result['pl'] = round(pl, 2)
                                            result['pl_pct'] = round(pl_pct, 2)
                                            result['reason'] = f"✅ CLOSED option position ({pos_info['symbol']}) at checked premium ${exit_check_price:.2f} (est. P/L: ${pl:+.2f}): {result['reason']}"
                                            t_settings.pop('options_underlying_entry_price', None)
                                            t_settings.pop('options_active_contract', None)
                                            from main import _clear_forecast_exit_snapshots
                                            _clear_forecast_exit_snapshots(t_settings)
                                            self.last_trailing_stops.pop(pos_info['symbol'], None)
                                            self.last_trade_timestamps[ticker] = self.get_now()
                                            await self._increment_trade_count(ticker)

                                            current_realized = t_settings.get('realized_pl', 0.0)
                                            t_settings['realized_pl'] = round(current_realized + pl, 2)
                                            self._apply_compound_pl(t_settings, pl, 'options')
                                            await self.save_settings()

                                            enable_signal_reset = t_settings.get('enable_signal_reset', getattr(self.config, 'ENABLE_SIGNAL_RESET', False))
                                            if enable_signal_reset:
                                                from option_selector import parse_occ_symbol
                                                parsed_closed = parse_occ_symbol(pos_info['symbol'])
                                                closed_type = parsed_closed['type'] if parsed_closed else 'CALL'
                                                locked_dir = 'BUY' if closed_type == 'CALL' else 'SELL'
                                                self._set_signal_reset_lock(ticker, locked_dir, t_settings)
                                                await self.save_settings()
                                                print(f"[engine-{self.uid}] Signal Reset Activated: Locked {locked_dir} re-entry for {ticker}")
                                        else:
                                            result['reason'] = f"Option sell failed: {order_result.get('error')}"

                                # Buy Execution
                                if not options_execution_handled and result['action'] == 'BUY' and not is_market_closed_skip:
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
                                        elif t_settings.get("options_type", "calls_only") == "puts_only":
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
                                            selection_data_client = None
                                            if not self.broker.simulation_mode and self.config.ALPACA_API_KEY and self.config.ALPACA_SECRET_KEY:
                                                try:
                                                    from alpaca.data.historical.option import OptionHistoricalDataClient
                                                    selection_data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                                                except Exception as data_client_err:
                                                    print(f"[options] Delta selection unavailable: {data_client_err}")
                                            contract = select_contract(
                                                contracts=chain,
                                                current_underlying_price=result['price_raw'],
                                                option_type="CALL",
                                                strike_type=opt_strike_type,
                                                target_expiry_days=opt_expiry_days,
                                                data_client=selection_data_client
                                            )
                                            
                                            if contract:
                                                # Fetch estimate premium for sizing
                                                import math
                                                quote_bid = quote_ask = quote_spread = quote_spread_pct = None
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
                                                        quote = quotes[contract['symbol']]
                                                        quote_bid = _safe_float(getattr(quote, 'bid_price', None), 0.0)
                                                        quote_ask = _safe_float(getattr(quote, 'ask_price', None), 0.0)
                                                        est_premium = quote_ask
                                                        if quote_bid > 0 and quote_ask > 0:
                                                            quote_spread = quote_ask - quote_bid
                                                            quote_mid = (quote_bid + quote_ask) / 2.0
                                                            quote_spread_pct = (quote_spread / quote_mid) * 100.0 if quote_mid > 0 else None
                                                    except Exception as quote_err:
                                                        print(f"[options] Quote fetch failed (no OPRA?): {quote_err}. Falling back to theoretical pricing.")
                                                        from options_pricing import black_scholes
                                                        import datetime
                                                        days = (contract['expiration'] - datetime.date.today()).days
                                                        t_years = max(0.0001, days / 365.0)
                                                        est_premium_bs = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.24, option_type="CALL", q=0.013)
                                                        est_premium = max(0.01, est_premium_bs + 0.015)

                                                spread_limit_pct = _safe_float(
                                                    t_settings.get('options_max_entry_spread_pct'),
                                                    15.0 if str(opt_strike_type).upper() == 'OTM' else 12.0
                                                )
                                                if not self.broker.simulation_mode and (_safe_float(quote_bid, 0.0) <= 0 or _safe_float(quote_ask, 0.0) <= 0):
                                                    result['reason'] = f"Skipped: live option bid/ask unavailable for {contract['symbol']}."
                                                elif not self.broker.simulation_mode and quote_spread_pct is not None and quote_spread_pct > spread_limit_pct:
                                                    result['reason'] = f"Skipped: option spread too wide for {contract['symbol']} ({quote_spread_pct:.1f}% > {spread_limit_pct:.1f}%)."
                                                elif est_premium > 0:
                                                    allocated = opt_trade_amount
                                                    qty = math.floor(allocated / (est_premium * 100.0))
                                                    if qty > 0:
                                                        order_result = self.broker.place_option_order(
                                                            option_symbol=contract['symbol'],
                                                            qty=qty,
                                                            side='buy',
                                                            limit_price=None
                                                        )
                                                        if order_result.get('success'):
                                                            result['order'] = order_result
                                                            result['order_id'] = order_result.get('order_id')
                                                            trade_executed = True
                                                            portfolio_count += 1

                                                            result['qty'] = qty
                                                            result['total_cost'] = qty * est_premium * 100.0
                                                            result['fees'] = 0.0
                                                            result['option_entry_quote'] = {
                                                                'bid': quote_bid,
                                                                'ask': quote_ask,
                                                                'spread': quote_spread,
                                                                'spread_pct': quote_spread_pct,
                                                                'limit_price': est_premium,
                                                            }
                                                            spread_note = f" | bid/ask ${quote_bid:.2f}/${quote_ask:.2f}, spread {quote_spread_pct:.1f}%" if quote_spread_pct is not None else ""
                                                            selection_note = f" | {contract.get('selection_reason')}" if contract.get('selection_reason') else ""
                                                            result['reason'] = f"✅ BOUGHT {qty} CALL contracts ({contract['symbol']}) with limit ${est_premium:.2f}{spread_note}{selection_note}: {result['reason']}"
                                                            t_settings['options_underlying_entry_price'] = float(result.get('price_raw') or 0.0)
                                                            t_settings['options_active_contract'] = contract['symbol']
                                                            try:
                                                                fresh_positions = self.broker.get_positions()
                                                                fresh_option_pos = self._find_position(
                                                                    fresh_positions,
                                                                    contract['symbol'].replace("/", "").upper(),
                                                                )
                                                                if fresh_option_pos:
                                                                    from main import _ensure_exit_forecast_snapshot
                                                                    _ensure_exit_forecast_snapshot(
                                                                        t_settings,
                                                                        result,
                                                                        fresh_option_pos,
                                                                        ticker,
                                                                    )
                                                            except Exception as snapshot_err:
                                                                print(f"[forecast] {ticker} CALL snapshot deferred until position fill is visible: {snapshot_err}")
                                                            self.last_trade_timestamps[ticker] = self.get_now()
                                                            await self._increment_trade_count(ticker)
                                                            await self.save_settings()
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
                                                stop_loss=sizing['stop_loss'], take_profit=sizing['take_profit'],
                                                market_session=t_settings.get('market_session', 'Regular'),
                                                current_price=result.get('price_raw')
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
                                                try:
                                                    fresh_positions = self.broker.get_positions()
                                                    fresh_pos = self._find_position(fresh_positions, ticker_norm)
                                                    if fresh_pos:
                                                        from main import _ensure_exit_forecast_snapshot
                                                        _ensure_exit_forecast_snapshot(
                                                            t_settings,
                                                            result,
                                                            fresh_pos,
                                                            ticker,
                                                        )
                                                        protection = self._sync_exit_protection(ticker, result, fresh_pos, t_settings, force=True)
                                                        if protection:
                                                            result['exit_protection'] = protection
                                                    else:
                                                        print(f"[exit-protect] {ticker_norm} buy accepted; watcher and exit snapshot will be applied after position fill is visible")
                                                except Exception as protect_err:
                                                    print(f"[exit-protect] {ticker_norm} post-buy protection error: {protect_err}")
                                            else:
                                                result['action'] = 'HOLD'
                                                result['reason'] = f"Buy order failed: {order_result.get('error', 'Broker Error')}"
                                                print(f"[engine] Buy order failed for {ticker}: {result['reason']}")
                                                
                                # Sell Execution (or PUT option entry)
                                elif not options_execution_handled and result['action'] == 'SELL' and not is_market_closed_skip:
                                    if is_options_enabled:
                                        if has_this_pos:
                                            # Close active option position
                                            order_result = self.broker.close_position(
                                                pos_info['symbol'],
                                                market_session=t_settings.get('market_session', 'Regular'),
                                                current_price=result.get('price_raw')
                                            )
                                            if order_result.get('success'):
                                                result['order'] = order_result
                                                result['order_id'] = order_result.get('order_id')
                                                trade_executed = True
                                                portfolio_count -= 1
                                                
                                                qty = float(pos_info['qty'])
                                                exit_check_price = _safe_float(result.get('option_exit_check', {}).get('premium'), pos_info.get('current_price'))
                                                val = qty * exit_check_price * 100.0
                                                cost = qty * float(pos_info['avg_price']) * 100.0
                                                pl = val - cost
                                                pl_pct = (pl / cost * 100.0) if cost > 0 else 0.0
                                                
                                                result['qty'] = qty
                                                result['total_cost'] = val
                                                result['fees'] = 0.0
                                                result['pl'] = round(pl, 2)
                                                result['pl_pct'] = round(pl_pct, 2)
                                                
                                                result['reason'] = f"✅ CLOSED option position ({pos_info['symbol']}) at checked premium ${exit_check_price:.2f} (est. P/L: ${pl:+.2f}): {result['reason']}"
                                                t_settings.pop('options_underlying_entry_price', None)
                                                t_settings.pop('options_active_contract', None)
                                                from main import _clear_forecast_exit_snapshots
                                                _clear_forecast_exit_snapshots(t_settings)
                                                self.last_trailing_stops.pop(pos_info['symbol'], None)
                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                await self._increment_trade_count(ticker)
                                                
                                                # Accumulate realized P/L
                                                current_realized = t_settings.get('realized_pl', 0.0)
                                                t_settings['realized_pl'] = round(current_realized + pl, 2)
                                                self._apply_compound_pl(t_settings, pl, 'options')
                                                await self.save_settings()

                                                enable_signal_reset = t_settings.get('enable_signal_reset', getattr(self.config, 'ENABLE_SIGNAL_RESET', False))
                                                if enable_signal_reset:
                                                    locked_dir = 'BUY' if opt_type == 'CALL' else 'SELL'
                                                    self._set_signal_reset_lock(ticker, locked_dir, t_settings)
                                                    await self.save_settings()
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
                                            elif t_settings.get("options_type", "calls_only") == "calls_only":
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
                                                selection_data_client = None
                                                if not self.broker.simulation_mode and self.config.ALPACA_API_KEY and self.config.ALPACA_SECRET_KEY:
                                                    try:
                                                        from alpaca.data.historical.option import OptionHistoricalDataClient
                                                        selection_data_client = OptionHistoricalDataClient(self.config.ALPACA_API_KEY, self.config.ALPACA_SECRET_KEY)
                                                    except Exception as data_client_err:
                                                        print(f"[options] Delta selection unavailable: {data_client_err}")
                                                contract = select_contract(
                                                    contracts=chain,
                                                    current_underlying_price=result['price_raw'],
                                                    option_type="PUT",
                                                    strike_type=opt_strike_type,
                                                    target_expiry_days=opt_expiry_days,
                                                    data_client=selection_data_client
                                                )
                                                
                                                if contract:
                                                    # Fetch estimate premium for sizing
                                                    import math
                                                    quote_bid = quote_ask = quote_spread = quote_spread_pct = None
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
                                                            quote = quotes[contract['symbol']]
                                                            quote_bid = _safe_float(getattr(quote, 'bid_price', None), 0.0)
                                                            quote_ask = _safe_float(getattr(quote, 'ask_price', None), 0.0)
                                                            est_premium = quote_ask
                                                            if quote_bid > 0 and quote_ask > 0:
                                                                quote_spread = quote_ask - quote_bid
                                                                quote_mid = (quote_bid + quote_ask) / 2.0
                                                                quote_spread_pct = (quote_spread / quote_mid) * 100.0 if quote_mid > 0 else None
                                                        except Exception as quote_err:
                                                            print(f"[options] Quote fetch failed (no OPRA?): {quote_err}. Falling back to theoretical pricing.")
                                                            from options_pricing import black_scholes
                                                            import datetime
                                                            days = (contract['expiration'] - datetime.date.today()).days
                                                            t_years = max(0.0001, days / 365.0)
                                                            est_premium_bs = black_scholes(result['price_raw'], contract['strike'], t_years, r=0.05, sigma=0.24, option_type="PUT", q=0.013)
                                                            est_premium = max(0.01, est_premium_bs + 0.015)

                                                    spread_limit_pct = _safe_float(
                                                        t_settings.get('options_max_entry_spread_pct'),
                                                        15.0 if str(opt_strike_type).upper() == 'OTM' else 12.0
                                                    )
                                                    if not self.broker.simulation_mode and (_safe_float(quote_bid, 0.0) <= 0 or _safe_float(quote_ask, 0.0) <= 0):
                                                        result['reason'] = f"Skipped: live option bid/ask unavailable for {contract['symbol']}."
                                                    elif not self.broker.simulation_mode and quote_spread_pct is not None and quote_spread_pct > spread_limit_pct:
                                                        result['reason'] = f"Skipped: option spread too wide for {contract['symbol']} ({quote_spread_pct:.1f}% > {spread_limit_pct:.1f}%)."
                                                    elif est_premium > 0:
                                                        allocated = opt_trade_amount
                                                        qty = math.floor(allocated / (est_premium * 100.0))
                                                        if qty > 0:
                                                            order_result = self.broker.place_option_order(
                                                                option_symbol=contract['symbol'],
                                                                qty=qty,
                                                                side='buy',
                                                                limit_price=None
                                                            )
                                                            if order_result.get('success'):
                                                                result['order'] = order_result
                                                                result['order_id'] = order_result.get('order_id')
                                                                trade_executed = True
                                                                portfolio_count += 1

                                                                result['qty'] = qty
                                                                result['total_cost'] = qty * est_premium * 100.0
                                                                result['fees'] = 0.0
                                                                result['option_entry_quote'] = {
                                                                    'bid': quote_bid,
                                                                    'ask': quote_ask,
                                                                    'spread': quote_spread,
                                                                    'spread_pct': quote_spread_pct,
                                                                    'limit_price': est_premium,
                                                                }
                                                                spread_note = f" | bid/ask ${quote_bid:.2f}/${quote_ask:.2f}, spread {quote_spread_pct:.1f}%" if quote_spread_pct is not None else ""
                                                                selection_note = f" | {contract.get('selection_reason')}" if contract.get('selection_reason') else ""
                                                                result['reason'] = f"✅ BOUGHT {qty} PUT contracts ({contract['symbol']}) with limit ${est_premium:.2f}{spread_note}{selection_note}: {result['reason']}"
                                                                t_settings['options_underlying_entry_price'] = float(result.get('price_raw') or 0.0)
                                                                t_settings['options_active_contract'] = contract['symbol']
                                                                try:
                                                                    fresh_positions = self.broker.get_positions()
                                                                    fresh_option_pos = self._find_position(
                                                                        fresh_positions,
                                                                        contract['symbol'].replace("/", "").upper(),
                                                                    )
                                                                    if fresh_option_pos:
                                                                        from main import _ensure_exit_forecast_snapshot
                                                                        _ensure_exit_forecast_snapshot(
                                                                            t_settings,
                                                                            result,
                                                                            fresh_option_pos,
                                                                            ticker,
                                                                        )
                                                                except Exception as snapshot_err:
                                                                    print(f"[forecast] {ticker} PUT snapshot deferred until position fill is visible: {snapshot_err}")
                                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                                await self._increment_trade_count(ticker)
                                                                await self.save_settings()
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
                                            order_result = self.broker.close_position(
                                                ticker,
                                                market_session=t_settings.get('market_session', 'Regular'),
                                                current_price=result.get('price_raw')
                                            )
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
                                                    self._apply_compound_pl(t_settings, pl, 'regular')
                                                    from main import _clear_forecast_exit_snapshots
                                                    _clear_forecast_exit_snapshots(t_settings)
                                                    await self.save_settings()
                                                else:
                                                    result['reason'] = f"✅ SOLD at {result['price']}: {result['reason']}"
                                                self.last_trade_timestamps[ticker] = self.get_now()
                                                await self._increment_trade_count(ticker)

                                                enable_signal_reset = t_settings.get('enable_signal_reset', getattr(self.config, 'ENABLE_SIGNAL_RESET', False))
                                                if enable_signal_reset:
                                                    self._set_signal_reset_lock(ticker, 'BUY', t_settings)
                                                    await self.save_settings()
                                                    print(f"[engine-{self.uid}] Signal Reset Activated: Locked BUY re-entry for standard asset {ticker}")
                                            else:
                                                result['reason'] = f"Sell order failed: {order_result.get('error', 'Broker Error')}"
                                        else:
                                            result['reason'] = "Signal SELL ignored: No open position detected."
                                            result['action'] = "HOLD"
                            
                            result['log_type'] = "Active Bot" if is_active else "Watchlist"
                            log_bucket_format = "%Y-%m-%dT%H:%M:%S" if is_opt_bot else "%Y-%m-%dT%H:%M"
                            log_time_bucket = self.get_now().strftime(log_bucket_format)
                            log_key = f"{log_time_bucket}_{ticker}_{result['action']}"
                            
                            def _get_log_key(log_entry):
                                try:
                                    t_str = datetime.fromisoformat(log_entry.get('time', '')).strftime(log_bucket_format)
                                    return f"{t_str}_{log_entry.get('ticker','')}_{log_entry.get('action','')}"
                                except: return ""
                                
                            if not any(_get_log_key(log) == log_key for log in self.trade_log):
                                if is_active:
                                    self.trade_log.insert(0, result)
                                if trade_executed and is_active:
                                    await self.save_history_throttled(force=True)

                    except Exception as e:
                        print(f"[scheduler-{self.uid}] Error scanning {ticker}: {e}")

                if len(self.trade_log) > 200:
                    self.trade_log = self.trade_log[:200]

                self.last_scan_time = self.get_now().isoformat()
                await self.save_history_throttled()

            except Exception as e:
                print(f"[scheduler-{self.uid}] Loop error: {e}")

            try:
                min_sleep = getattr(self.config, 'OPTIONS_SCAN_INTERVAL_SECONDS', 10) if any(
                    self.config.TICKER_SETTINGS.get(t, {}).get('options_trading', self.config.TICKER_SETTINGS.get(t, {}).get('is_option', False))
                    for t in self.config.TRADELIST
                ) else getattr(self.config, 'SCAN_INTERVAL_SECONDS', 60)
                await asyncio.wait_for(self.force_scan_trigger.wait(), timeout=min_sleep)
                self.force_scan_trigger.clear()
            except asyncio.TimeoutError:
                self.force_scan_trigger.clear()

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
        try:
            users_ref = self.db.collection("users").stream(timeout=3.0)
            for doc in users_ref:
                uid = doc.id
                await self.start_user(uid)
        except Exception as e:
            print(f"[UserManager] Error streaming users from Firestore: {e}")
        print(f"[UserManager] Started {len(self.engines)} user engines.")
        
    async def stop_all(self):
        for uid, engine in self.engines.items():
            if engine.bot_running:
                engine.bot_running = False
                if engine.task: engine.task.cancel()
