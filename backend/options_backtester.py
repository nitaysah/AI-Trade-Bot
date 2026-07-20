import pandas as pd
import numpy as np
import math
import datetime
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_signals
from swing_strategy import build_entry_signal, build_swing_exit
from options_direction import validate_options_type
from options_pricing import black_scholes, calculate_historical_volatility
from option_selector import add_us_market_trading_days, _strike_profile, _target_strike_from_profile
from prior_day_high_low import buffered_prior_day_levels, resolve_high_low_buffer_pct

def _safe_chart_level(row, key, digits=4):
    try:
        value = row.get(key)
        if value is None or pd.isna(value):
            return None
        return round(float(value), digits)
    except Exception:
        return None

def _safe_chart_bool(row, key):
    try:
        value = row.get(key)
        if value is None or pd.isna(value):
            return False
        return bool(value)
    except Exception:
        return False

def _chart_indicator_fields(row, digits=4, enabled_indicators=None, buy_mode="", sell_mode=""):
    fields = {
        "ema_fast": _safe_chart_level(row, "EMA_Fast", digits),
        "ema_slow": _safe_chart_level(row, "EMA_Slow", digits),
        "sma": _safe_chart_level(row, "SMA", digits),
        "vwap": _safe_chart_level(row, "VWAP", digits),
        "supertrend": _safe_chart_level(row, "Supertrend", digits),
        "supertrend_up": _safe_chart_bool(row, "Supertrend_Trend"),
        "boll_upper": _safe_chart_level(row, "BOLL_Upper", digits),
        "boll_middle": _safe_chart_level(row, "BOLL_Middle", digits),
        "boll_lower": _safe_chart_level(row, "BOLL_Lower", digits),
        "daily_high_low_high": _safe_chart_level(row, "Daily_HighLow_High", digits),
        "daily_high_low_low": _safe_chart_level(row, "Daily_HighLow_Low", digits),
        "daily_high_low_high_trigger": _safe_chart_level(row, "Daily_HighLow_High_Trigger", digits),
        "daily_high_low_low_trigger": _safe_chart_level(row, "Daily_HighLow_Low_Trigger", digits),
        "daily_high_low_bullish": _safe_chart_bool(row, "Daily_HighLow_Bullish"),
        "daily_high_low_bearish": _safe_chart_bool(row, "Daily_HighLow_Bearish"),
        "pivot_p": _safe_chart_level(row, "Pivot_P", digits),
        "pivot_r1": _safe_chart_level(row, "Pivot_R1", digits),
        "pivot_s1": _safe_chart_level(row, "Pivot_S1", digits),
        "pivot_r2": _safe_chart_level(row, "Pivot_R2", digits),
        "pivot_s2": _safe_chart_level(row, "Pivot_S2", digits),
        "pivot_r3": _safe_chart_level(row, "Pivot_R3", digits),
        "pivot_s3": _safe_chart_level(row, "Pivot_S3", digits),
        "pivot_r4": _safe_chart_level(row, "Pivot_R4", digits),
        "pivot_s4": _safe_chart_level(row, "Pivot_S4", digits),
        "pivot_r5": _safe_chart_level(row, "Pivot_R5", digits),
        "pivot_s5": _safe_chart_level(row, "Pivot_S5", digits),
        "pivot_bullish": _safe_chart_bool(row, "Pivot_Bullish"),
        "pivot_bearish": _safe_chart_bool(row, "Pivot_Bearish"),
        "volume_profile_poc": _safe_chart_level(row, "Volume_Profile_POC", digits),
        "volume_profile_vah": _safe_chart_level(row, "Volume_Profile_VAH", digits),
        "volume_profile_val": _safe_chart_level(row, "Volume_Profile_VAL", digits),
        "volume_profile_bullish": _safe_chart_bool(row, "Volume_Profile_Bullish"),
        "volume_profile_bearish": _safe_chart_bool(row, "Volume_Profile_Bearish"),
        "swing_support_1": _safe_chart_level(row, "Swing_Support_1", digits),
        "swing_support_2": _safe_chart_level(row, "Swing_Support_2", digits),
        "swing_support_3": _safe_chart_level(row, "Swing_Support_3", digits),
        "swing_support_4": _safe_chart_level(row, "Swing_Support_4", digits),
        "swing_support_5": _safe_chart_level(row, "Swing_Support_5", digits),
        "swing_resistance_1": _safe_chart_level(row, "Swing_Resistance_1", digits),
        "swing_resistance_2": _safe_chart_level(row, "Swing_Resistance_2", digits),
        "swing_resistance_3": _safe_chart_level(row, "Swing_Resistance_3", digits),
        "swing_resistance_4": _safe_chart_level(row, "Swing_Resistance_4", digits),
        "swing_resistance_5": _safe_chart_level(row, "Swing_Resistance_5", digits),
        "swing_bullish": _safe_chart_bool(row, "Swing_Bullish"),
        "swing_bearish": _safe_chart_bool(row, "Swing_Bearish"),
        "rsi": _safe_chart_level(row, "RSI", 4),
        "macd_line": _safe_chart_level(row, "MACD_Line", 6),
        "macd_signal": _safe_chart_level(row, "MACD_Signal", 6),
        "macd_hist": _safe_chart_level(row, "MACD_Hist", 6),
        "adx": _safe_chart_level(row, "ADX", 4),
        "di_plus": _safe_chart_level(row, "DI_Plus", 4),
        "di_minus": _safe_chart_level(row, "DI_Minus", 4),
        "wt1": _safe_chart_level(row, "WT1", 6),
        "wt2": _safe_chart_level(row, "WT2", 6),
        "mfi": _safe_chart_level(row, "MFI_14", 6),
        "lx_smart_trail": _safe_chart_level(row, "LX_SmartTrail", digits),
        "lx_trend_tracer": _safe_chart_level(row, "LX_TrendTracer", digits),
        "lx_rz_upper": _safe_chart_level(row, "LX_RZ_Upper", digits),
        "lx_rz_lower": _safe_chart_level(row, "LX_RZ_Lower", digits),
        "ha_close": _safe_chart_level(row, "HA_Close", digits),
        "ut_trail": _safe_chart_level(row, "UT_Trail", digits),
        "ba1_buy": _safe_chart_bool(row, "BA1_Buy"),
        "ba1_sell": _safe_chart_bool(row, "BA1_Sell"),
        "ba1_bullish": _safe_chart_bool(row, "BA1_Bullish"),
        "ba1_bearish": _safe_chart_bool(row, "BA1_Bearish"),
        "ba2_buy": _safe_chart_bool(row, "BA2_Buy"),
        "ba2_sell": _safe_chart_bool(row, "BA2_Sell"),
        "ba3_buy": _safe_chart_bool(row, "BA3_Buy"),
        "ba3_sell": _safe_chart_bool(row, "BA3_Sell"),
        "cdl_name": str(row.get("cdl_name", "Neutral")),
        "cdl_bullish": _safe_chart_bool(row, "cdl_bullish"),
        "cdl_bearish": _safe_chart_bool(row, "cdl_bearish"),
    }
    if enabled_indicators is None:
        return fields

    enabled = set(enabled_indicators)
    keys = set()
    groups = {
        "EMA Cross": {"ema_fast", "ema_slow"},
        "SMA": {"sma"},
        "VWAP": {"vwap"},
        "Supertrend": {"supertrend", "supertrend_up"},
        "Bollinger": {"boll_upper", "boll_middle", "boll_lower"},
        "RSI": {"rsi"},
        "MACD": {"macd_line", "macd_signal", "macd_hist"},
        "ADX Trend": {"adx", "di_plus", "di_minus"},
        "Volume Profile": {"volume_profile_poc", "volume_profile_vah", "volume_profile_val", "volume_profile_bullish", "volume_profile_bearish"},
        "BotBulls1": {"wt1", "wt2", "mfi", "ba1_buy", "ba1_sell", "ba1_bullish", "ba1_bearish"},
        "BotBulls2": {"lx_smart_trail", "lx_trend_tracer", "lx_rz_upper", "lx_rz_lower", "ba2_buy", "ba2_sell"},
        "BotBulls3": {"ha_close", "ut_trail", "ba3_buy", "ba3_sell"},
        "Candle Patterns": {"cdl_name", "cdl_bullish", "cdl_bearish"},
    }
    for indicator in enabled:
        keys.update(groups.get(indicator, set()))

    modes = {str(buy_mode or ""), str(sell_mode or "")}
    if "Prior Day High/Low" in enabled or modes & {"high_low", "hybrid_high_low"}:
        keys.update(key for key in fields if key.startswith("daily_high_low_"))
    if "Pivot Points (S/R)" in enabled or modes & {"pivot_points", "hybrid_pivot"}:
        keys.update(key for key in fields if key.startswith("pivot_"))
    if "Swing Points" in enabled or modes & {"swing", "hybrid_swing"}:
        keys.update(key for key in fields if key.startswith("swing_"))
    return {key: fields[key] for key in keys}

class OptionsBacktester:
    def __init__(
        self,
        ticker: str,
        timeframe: str,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        df=None,
        initial_capital: float = 1000.0,
        trade_amount: float = 100.0,
        expiry_days: int = 7,
        strike_type: str = "ATM",
        options_type: str = "calls_only",
        stop_loss_pct: float = 0.50,
        take_profit_pct: float = 1.00,
        exit_strategy: str = "hybrid",
        sell_mode: str = "indicator",
        close_before_expiry_hours: float = 2.0,
        threshold: int = 5,
        sell_threshold: int = 3,
        enabled_indicators: list = None,
        ext_hours: bool = True,
        r: float = 0.05,
        cancel_event=None,
        intraday_only: bool = False,
        enable_signal_reset: bool = False,
        enable_smart_reentry: bool = False,
        smart_reentry_type: str = "RSI",
        smart_reentry_rsi_call_max: int = 70,
        smart_reentry_rsi_put_min: int = 30,
        smart_reentry_vwap_call_max_dist_pct: float = 0.5,
        smart_reentry_vwap_put_max_dist_pct: float = 0.5,
        enable_cooldown: bool = True,
        cooldown_minutes: int = None,
        buy_mode: str = "indicator",
        custom_buy_price: float = 0.0,
        custom_stop_loss_price: float = 0.0,
        custom_take_profit_price: float = 0.0,
        custom_underlying_stop_loss_price: float = 0.0,
        custom_underlying_take_profit_price: float = 0.0,
        atr_stop_multiplier: float = 2.0,
        atr_trail_multiplier: float = 3.0,
        atr_take_profit_multiplier: float = 4.0,
        enable_atr_trail: bool = True,
        regular_stop_loss_pct: float = 0.05,
        regular_take_profit_pct: float = 0.10,
        regular_trailing_pct: float = 0.03,
        enable_pct_trail: bool = True,
        risk_per_trade: float = 1.0,
        max_pos_pct: float = 1.0,
        entry_atr_multiplier: float = None,
        entry_pct_move: float = None,
        entry_atr_style: str = "pullback_reclaim",
        entry_pct_style: str = "dip_reclaim",
        swing_entry_mode: str = "bounce_retest",
        swing_entry_lookback_days: int = 20,
        swing_exit_lookback_days: int = 20,
        market_session: str = "Regular",
        enable_risk: bool = True,
        daily_high_low_buffer_pct: float = None
    ):
        self.ticker = ticker
        self.timeframe = timeframe
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.trade_amount = trade_amount
        self.expiry_days = expiry_days
        self.strike_type = strike_type
        self.options_type = validate_options_type(options_type)
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        # sell_mode is the unified exit strategy key (mirrors regular backtester).
        # exit_strategy is kept for backward-compat with old API callers.
        self.sell_mode = sell_mode if sell_mode != 'indicator' else exit_strategy
        self.exit_strategy = exit_strategy
        self.close_before_expiry_hours = close_before_expiry_hours
        self.threshold = int(threshold or 0)
        self.sell_threshold = int(sell_threshold or 0)
        self.enabled_indicators = enabled_indicators or []
        if self.enabled_indicators:
            self.threshold = max(1, self.threshold)
            self.sell_threshold = max(1, self.sell_threshold)
        self.ext_hours = ext_hours
        self.r = r
        self.cancel_event = cancel_event
        self.intraday_only = intraday_only
        self.enable_signal_reset = bool(enable_signal_reset)
        self.enable_smart_reentry = bool(enable_smart_reentry)
        self.daily_high_low_buffer_pct = resolve_high_low_buffer_pct(fallback=daily_high_low_buffer_pct)
        self.indicator_settings = {"DAILY_HIGH_LOW_BUFFER_PCT": self.daily_high_low_buffer_pct}
        self.smart_reentry_type = str(smart_reentry_type or "RSI").upper()
        self.smart_reentry_rsi_call_max = float(smart_reentry_rsi_call_max or 70.0)
        self.smart_reentry_rsi_put_min = float(smart_reentry_rsi_put_min or 30.0)
        self.smart_reentry_vwap_call_max_dist_pct = float(smart_reentry_vwap_call_max_dist_pct or 0.5)
        self.smart_reentry_vwap_put_max_dist_pct = float(smart_reentry_vwap_put_max_dist_pct or 0.5)
        self.enable_cooldown = enable_cooldown
        self.cooldown_minutes = cooldown_minutes
        # Entry mode
        self.buy_mode = buy_mode
        self.custom_buy_price = custom_buy_price
        self.custom_stop_loss_price = custom_stop_loss_price
        self.custom_take_profit_price = custom_take_profit_price
        self.custom_underlying_stop_loss_price = custom_underlying_stop_loss_price
        self.custom_underlying_take_profit_price = custom_underlying_take_profit_price
        # ATR exit params
        self.atr_stop_multiplier = atr_stop_multiplier
        self.atr_trail_multiplier = atr_trail_multiplier
        self.atr_take_profit_multiplier = atr_take_profit_multiplier
        self.enable_atr_trail = bool(enable_atr_trail)
        # Percentage exit params
        self.regular_stop_loss_pct = regular_stop_loss_pct or stop_loss_pct
        self.regular_take_profit_pct = regular_take_profit_pct or take_profit_pct
        self.regular_trailing_pct = regular_trailing_pct
        self.enable_pct_trail = bool(enable_pct_trail)
        self.entry_atr_multiplier = entry_atr_multiplier or atr_stop_multiplier
        self.entry_pct_move = entry_pct_move or max(1.0, (regular_stop_loss_pct or stop_loss_pct) * 100.0)
        self.entry_atr_style = entry_atr_style or "pullback_reclaim"
        self.entry_pct_style = entry_pct_style or "dip_reclaim"
        self.swing_entry_mode = swing_entry_mode or "bounce_retest"
        self.swing_entry_lookback_days = swing_entry_lookback_days or 20
        self.swing_exit_lookback_days = swing_exit_lookback_days or 20
        self.indicator_settings.update({
            "SWING_POINTS_LOOKBACK_DAYS": self.swing_entry_lookback_days,
            "SWING_POINTS_ENTRY_MODE": self.swing_entry_mode,
            "market_session": market_session or "Regular",
        })
        self.market_session = market_session or "Regular"
        # Risk sizing
        self.risk_per_trade = risk_per_trade
        self.max_pos_pct = max_pos_pct
        self.enable_risk = enable_risk
        
        # Real-market pricing proxy parameters
        self.q = 0.013 # SPY/broad market approx dividend yield
        self.iv_markup = 0.04 # 4% markup for Implied Volatility over Historical
        self.spread_width = 0.03 # Typical $0.03 spread on liquid ETF options

        self.equity = initial_capital
        self.position = None  # None or Dict representing option position
        self.trades = []
        self.equity_history = []
        self.skipped_entries = []
        self.signal_locked_direction = None
        self.last_exit_time = None
        self.raw_df = df
        self.df = None
        self.options_csv_cache = {}
        self._load_options_csv_cache()
        self.data_source = "yahoo"

    def _load_options_csv_cache(self):
        import os
        for candidate in ["data/spy_options_chain_5m.csv", "backend/data/spy_options_chain_5m.csv"]:
            if os.path.exists(candidate) and self.ticker == "SPY":
                try:
                    df_opt = pd.read_csv(candidate)
                    for _, row in df_opt.iterrows():
                        key = (str(row["timestamp"])[:16], round(float(row["strike_price"]), 1), str(row["option_type"]).upper(), int(row["expiration_dte"]))
                        self.options_csv_cache[key] = float(row["option_price"])
                    print(f"[options-csv] Loaded {len(self.options_csv_cache)} option prices from {candidate}")
                except Exception as e:
                    print(f"[options-csv] Warning loading CSV: {e}")
                break

    def _lookup_option_price(self, timestamp, strike, option_type, expiration_dte, fallback_price: float) -> float:
        if not self.options_csv_cache:
            return fallback_price
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        key = (ts_str[:16], round(float(strike), 1), str(option_type).upper(), int(expiration_dte))
        if key in self.options_csv_cache:
            return self.options_csv_cache[key]
        return fallback_price

    def _as_datetime(self, ts) -> datetime.datetime:
        if hasattr(ts, "to_pydatetime"):
            return ts.to_pydatetime()
        return ts

    def _expiration_close_for_entry(self, entry_time) -> datetime.datetime:
        entry_dt = self._as_datetime(entry_time)
        expiration_date = add_us_market_trading_days(entry_dt.date(), int(self.expiry_days or 0))
        close_time = datetime.time(16, 0)
        expiration_dt = datetime.datetime.combine(expiration_date, close_time)
        if entry_dt.tzinfo is not None:
            expiration_dt = expiration_dt.replace(tzinfo=entry_dt.tzinfo)
        return expiration_dt

    def _time_to_expiry_years(self, timestamp, expiration_close) -> float:
        ts = self._as_datetime(timestamp)
        remaining_seconds = (expiration_close - ts).total_seconds()
        return max(0.00001, remaining_seconds / (365.0 * 24.0 * 3600.0))

    def _hours_to_expiry(self, timestamp, expiration_close) -> float:
        ts = self._as_datetime(timestamp)
        return max(0.0, (expiration_close - ts).total_seconds() / 3600.0)

    def _localize_dt(self, ts):
        import pytz
        is_crypto = False
        clean_ticker = self.ticker.upper().replace("/", "")
        for base in ["USD", "USDT", "USDC"]:
            if clean_ticker.endswith(base):
                is_crypto = True
                break
        
        if is_crypto:
            tz = pytz.utc
        else:
            tz = pytz.timezone("America/New_York")
        
        localized_dt = tz.localize(ts)
        return localized_dt.astimezone(pytz.utc)

    def _to_utc_iso(self, ts):
        utc_dt = self._localize_dt(ts)
        return utc_dt.strftime("%Y-%m-%d %H:%M:%SZ")

    def _to_unix(self, ts):
        utc_dt = self._localize_dt(ts)
        return int(utc_dt.timestamp())

    def _to_central_str(self, ts):
        import pytz
        utc_dt = self._localize_dt(ts)
        central_tz = pytz.timezone("America/Chicago")
        central_dt = utc_dt.astimezone(central_tz)
        return central_dt.strftime("%Y-%m-%d %H:%M")

    def run(self) -> dict:
        # 1. Clean date formats
        if hasattr(self.start_date, "tzinfo") and self.start_date.tzinfo is not None:
            self.start_date = self.start_date.replace(tzinfo=None)
        if hasattr(self.end_date, "tzinfo") and self.end_date.tzinfo is not None:
            self.end_date = self.end_date.replace(tzinfo=None)

        if self.raw_df is not None:
            raw_df = self.raw_df
        else:
            # Fetch with padding where the provider can actually satisfy it.
            yahoo_intraday_limits = {
                "1Min": 7,
                "2Min": 60,
                "3Min": 60,
                "5Min": 60,
                "10Min": 60,
                "15Min": 60,
                "30Min": 60,
                "1Hour": 730,
                "2Hour": 730,
                "4Hour": 730,
            }
            requested_days = max(1, (self.end_date - self.start_date).days)
            max_days = yahoo_intraday_limits.get(self.timeframe)
            warmup_days = 100 if max_days is None else max(0, min(100, max_days - requested_days))
            padded_start = self.start_date - datetime.timedelta(days=warmup_days)
            raw_df = get_historical_data(self.ticker, self.timeframe, padded_start, self.end_date, force_yahoo=True)

        if raw_df is None or raw_df.empty:
            return {"error": "No underlying historical data found."}

        # Normalize column names to Title Case to prevent KeyErrors
        raw_df = raw_df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume',
            'OPEN': 'Open', 'HIGH': 'High', 'LOW': 'Low', 'CLOSE': 'Close', 'VOLUME': 'Volume'
        })
        raw_df = raw_df.loc[:, ~raw_df.columns.duplicated()]

        # Filter RTH: Options are only traded during regular market hours. Enforce strictly for intraday.
        clean_ticker = self.ticker.upper().replace("/", "")
        is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        if is_crypto:
            return {"error": "Options trading is not supported for cryptocurrency tickers."}
        if not is_crypto and self.timeframe != "1Day":
            try:
                time_idx = raw_df.index.time
                start_time = pd.to_datetime("09:30:00").time()
                # Options on index ETFs (SPY, QQQ, DIA, IWM) trade until 4:15 PM ET. Others trade until 4:00 PM ET.
                end_str = "16:15:00" if clean_ticker in ["SPY", "QQQ", "DIA", "IWM"] else "16:00:00"
                end_time = pd.to_datetime(end_str).time()
                rth_mask = (time_idx >= start_time) & (time_idx <= end_time)
                raw_df = raw_df[rth_mask]
                print(f"[options-backtest] Strictly enforced Regular Trading Hours (RTH: 09:30 - {end_str[:5]}) for {clean_ticker}")
            except Exception as e:
                print(f"[options-backtest] RTH filtering failed: {e}")

        # 2. Add technical indicators (skip if already present like BotBulls3)
        if "UT_Above" not in raw_df.columns:
            calc_settings = {
                **(self.indicator_settings or {}),
                "enabled_indicators": self.enabled_indicators,
                "buy_mode": self.buy_mode,
                "sell_mode": self.sell_mode,
            }
            res = calculate_indicators_for_df(raw_df, self.timeframe, self.ticker, settings=calc_settings)
            self.df = res["df"] if isinstance(res, dict) and "df" in res else res
        else:
            self.df = raw_df
            
        if self.df is None or (hasattr(self.df, 'empty') and self.df.empty):
            return {"error": "Failed to calculate technical indicators."}

        full_df = self.df
        
        # 3. Pre-calculate historical volatility rolling vector
        closes = full_df['Close'].values
        if closes.ndim > 1:
            closes = closes[:, 0]
        returns = np.diff(closes) / closes[:-1]
        returns = np.insert(returns, 0, 0)
        
        # Map timeframe to annualized multiplier
        tf_secs = 24 * 3600  # Default daily
        if self.timeframe == "1Min":
            tf_secs = 60
        elif self.timeframe == "5Min":
            tf_secs = 5 * 60
        elif self.timeframe == "15Min":
            tf_secs = 15 * 60
        elif self.timeframe == "30Min":
            tf_secs = 30 * 60
        elif self.timeframe == "1Hour":
            tf_secs = 3600
        
        # Approximate bars per trading day
        bars_per_day = (390 * 60) / tf_secs if tf_secs < 24*3600 else 1.0
        annual_bars = 252 * bars_per_day

        rolling_vol = pd.Series(returns).rolling(window=50).std() * math.sqrt(annual_bars)
        rolling_vol = np.clip(rolling_vol.fillna(0.16).values, 0.10, 0.35)
        full_df['Historical_Vol'] = rolling_vol

        # 4. Map daily high/low/close for previous-day pivot and range exits
        if getattr(full_df.index, "tz", None) is not None:
            df_dates = pd.Series(full_df.index.tz_convert("America/Chicago").date, index=full_df.index)
        else:
            df_dates = pd.Series(full_df.index.date, index=full_df.index)
        daily_groups = full_df.groupby(df_dates)
        daily_highs = daily_groups['High'].max()
        daily_lows = daily_groups['Low'].min()
        daily_closes = daily_groups['Close'].last()

        unique_dates = sorted(list(daily_highs.index))
        prev_day_data = {}
        for idx, dt in enumerate(unique_dates):
            if idx == 0:
                prev_day_data[dt] = None
            else:
                prev_day_data[dt] = {
                    "High": daily_highs.iloc[idx-1],
                    "Low": daily_lows.iloc[idx-1],
                    "Close": daily_closes.iloc[idx-1]
                }

        # Slice back to window
        self.df = full_df.loc[self.start_date:self.end_date]
        if self.df.empty:
            return {"error": "No data available in requested range after filtering."}

        price_history_records = []
        for ts, row in self.df.iterrows():
            try:
                price_history_records.append({
                    "time": int(ts.timestamp()) if hasattr(ts, "timestamp") else ts,
                    "open": float(row.get("Open", row.get("open", row.get("Close", 0.0)))),
                    "high": float(row.get("High", row.get("high", row.get("Close", 0.0)))),
                    "low": float(row.get("Low", row.get("low", row.get("Close", 0.0)))),
                    "close": float(row.get("Close", row.get("close", 0.0))),
                    "volume": float(row.get("Volume", row.get("volume", 0.0))),
                })
            except Exception:
                continue

        # 5. Simulate bar-by-bar options trading
        self._today_high = None
        self._today_low = None
        self._last_date = None
        self.signal_locked_direction = None
        self.last_exit_time = None

        for i in range(len(self.df)):
            if self.cancel_event and self.cancel_event.is_set():
                print(f"[options-backtest] Cancelled by user halfway through execution ({i}/{len(self.df)} bars)")
                return {"error": "Backtest cancelled by user"}
                
            current_bar = self.df.iloc[i]
            timestamp = self.df.index[i]
            current_price = current_bar['Close']
            current_vol = current_bar['Historical_Vol']

            if i < 30:
                continue

            prev_bar = self.df.iloc[i-1]

            # Dynamic tracking of intraday high/low
            current_date = timestamp.tz_convert("America/Chicago").date() if getattr(timestamp, "tzinfo", None) is not None else timestamp.date()
            if self._last_date != current_date:
                self._today_high = current_bar['High']
                self._today_low = current_bar['Low']
                self._last_date = current_date
            else:
                self._today_high = max(self._today_high, current_bar['High'])
                self._today_low = min(self._today_low, current_bar['Low'])

            # Pre-fetch previous day boundaries
            p_data = prev_day_data.get(current_date)
            prev_h = p_data["High"] if p_data else None
            prev_l = p_data["Low"] if p_data else None
            prev_c = p_data["Close"] if p_data else None

            # Check exits if position is open
            if self.position:
                opt_type = self.position['option_type']
                strike = self.position['strike_price']
                entry_date = self.position['entry_time']

                # Compute current option premium with IV markup and dividend yield
                expiration_close = self.position.get('expiration_close') or self._expiration_close_for_entry(entry_date)
                t_remaining = self._time_to_expiry_years(timestamp, expiration_close)
                opt_price_bs = black_scholes(current_price, strike, t_remaining, self.r, current_vol + self.iv_markup, opt_type, q=self.q)
                opt_price_bs = self._lookup_option_price(timestamp, strike, opt_type, self.expiry_days, opt_price_bs)
                opt_price = max(0.01, opt_price_bs - (self.spread_width / 2.0)) # Sell at the bid
                
                # Check exit triggers
                exit_triggered = False
                exit_reason = ""
                configured_exit_modes = {self.sell_mode or self.exit_strategy}
                
                # Intraday Check
                if self.intraday_only and timestamp.time() >= datetime.time(15, 55):
                    exit_triggered = True
                    exit_reason = "Intraday End of Day (15:55 PM ET)"

                # Expiration safeguard
                hours_remaining = self._hours_to_expiry(timestamp, expiration_close)
                if not exit_triggered and hours_remaining <= self.close_before_expiry_hours:
                    exit_triggered = True
                    exit_reason = f"Expiration Safeguard ({self.close_before_expiry_hours}h remaining)"

                # Strategy A: Custom premium SL/TP
                if not exit_triggered and configured_exit_modes.intersection({"custom_sltp"}):
                    if self.custom_stop_loss_price > 0 and opt_price <= self.custom_stop_loss_price:
                        exit_triggered = True
                        exit_reason = f"Custom Premium Stop Loss Hit (${opt_price:.2f} <= ${self.custom_stop_loss_price:.2f})"
                    elif self.custom_take_profit_price > 0 and opt_price >= self.custom_take_profit_price:
                        exit_triggered = True
                        exit_reason = f"Custom Premium Take Profit Hit (${opt_price:.2f} >= ${self.custom_take_profit_price:.2f})"

                # Strategy A2: Custom underlying-price SL/TP
                if not exit_triggered and configured_exit_modes.intersection({"custom_underlying_sltp"}):
                    if opt_type == "CALL":
                        if self.custom_underlying_stop_loss_price > 0 and current_price <= self.custom_underlying_stop_loss_price:
                            exit_triggered = True
                            exit_reason = f"Custom Underlying Stop Loss Hit (Underlying ${current_price:.2f} <= ${self.custom_underlying_stop_loss_price:.2f})"
                        elif self.custom_underlying_take_profit_price > 0 and current_price >= self.custom_underlying_take_profit_price:
                            exit_triggered = True
                            exit_reason = f"Custom Underlying Take Profit Hit (Underlying ${current_price:.2f} >= ${self.custom_underlying_take_profit_price:.2f})"
                    else:
                        if self.custom_underlying_stop_loss_price > 0 and current_price >= self.custom_underlying_stop_loss_price:
                            exit_triggered = True
                            exit_reason = f"Custom Underlying Stop Loss Hit (Underlying ${current_price:.2f} >= ${self.custom_underlying_stop_loss_price:.2f})"
                        elif self.custom_underlying_take_profit_price > 0 and current_price <= self.custom_underlying_take_profit_price:
                            exit_triggered = True
                            exit_reason = f"Custom Underlying Take Profit Hit (Underlying ${current_price:.2f} <= ${self.custom_underlying_take_profit_price:.2f})"

                # Strategy B: Dynamic Pivot Point Exit
                if not exit_triggered and prev_h is not None and prev_l is not None and prev_c is not None and configured_exit_modes.intersection({"pivot_points", "hybrid_pivot"}):
                    P = (prev_h + prev_l + prev_c) / 3.0
                    R1 = 2 * P - prev_l
                    S1 = 2 * P - prev_h

                    if opt_type == "CALL":
                        if current_bar['High'] >= R1:
                            exit_triggered = True
                            exit_reason = f"Pivot Take Profit Hit (Underlying High >= R1: {R1:.2f})"
                        elif current_bar['Low'] <= S1:
                            exit_triggered = True
                            exit_reason = f"Pivot Stop Loss Hit (Underlying Low <= S1: {S1:.2f})"
                    else: # PUT
                        if current_bar['Low'] <= S1:
                            exit_triggered = True
                            exit_reason = f"Pivot Take Profit Hit (Underlying Low <= S1: {S1:.2f})"
                        elif current_bar['High'] >= R1:
                            exit_triggered = True
                            exit_reason = f"Pivot Stop Loss Hit (Underlying High >= R1: {R1:.2f})"

                # Strategy C: Prior Day High/Low ranges
                if not exit_triggered and prev_h is not None and prev_l is not None and prev_c is not None and configured_exit_modes.intersection({"high_low", "hybrid_high_low"}):
                    hl_levels = buffered_prior_day_levels(
                        {"high": prev_h, "low": prev_l, "close": prev_c},
                        self.daily_high_low_buffer_pct,
                    )
                    if opt_type == "CALL" and hl_levels:
                        if current_bar['High'] >= hl_levels["high_trigger"]:
                            exit_triggered = True
                            exit_reason = f"Prev Day High Breakout (Underlying High >= Trigger {hl_levels['high_trigger']:.2f}, PDH {hl_levels['high']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                        elif current_bar['Low'] <= hl_levels["low_trigger"]:
                            exit_triggered = True
                            exit_reason = f"Prev Day Low Breakdown (Underlying Low <= Trigger {hl_levels['low_trigger']:.2f}, PDL {hl_levels['low']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                    elif opt_type == "PUT" and hl_levels:
                        if current_bar['Low'] <= hl_levels["low_trigger"]:
                            exit_triggered = True
                            exit_reason = f"Prev Day Low Breakout (Underlying Low <= Trigger {hl_levels['low_trigger']:.2f}, PDL {hl_levels['low']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                        elif current_bar['High'] >= hl_levels["high_trigger"]:
                            exit_triggered = True
                            exit_reason = f"Prev Day High Breakdown (Underlying High >= Trigger {hl_levels['high_trigger']:.2f}, PDH {hl_levels['high']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"

                # Strategy C2: Swing support/resistance exits
                if not exit_triggered and configured_exit_modes.intersection({"swing_points", "hybrid_swing"}):
                    swing_exit = build_swing_exit(
                        price_history_records[:i + 1],
                        current_price=current_price,
                        is_long=(opt_type == "CALL"),
                        lookback_days=int(self.swing_exit_lookback_days or 20),
                        market_session=self.market_session,
                        is_crypto=False,
                    )
                    tp = swing_exit.get("take_profit")
                    sl = swing_exit.get("stop_loss")
                    if tp is not None and sl is not None:
                        if opt_type == "CALL":
                            if current_price >= tp:
                                exit_triggered = True
                                exit_reason = f"Swing Take Profit Hit (Underlying {current_price:.2f} >= {tp:.2f})"
                            elif current_price <= sl:
                                exit_triggered = True
                                exit_reason = f"Swing Stop Loss Hit (Underlying {current_price:.2f} <= {sl:.2f})"
                        else:
                            if current_price <= tp:
                                exit_triggered = True
                                exit_reason = f"Swing Take Profit Hit (Underlying {current_price:.2f} <= {tp:.2f})"
                            elif current_price >= sl:
                                exit_triggered = True
                                exit_reason = f"Swing Stop Loss Hit (Underlying {current_price:.2f} >= {sl:.2f})"

                # Strategy D: Indicator Reversal (Indicator/Hybrid modes)
                if not exit_triggered and configured_exit_modes.intersection({"indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}):
                    signals = _generate_signals(
                        current_bar,
                        prev_bar,
                        settings={"enabled_indicators": self.enabled_indicators},
                        recent_bars=self.df.iloc[max(0, i - 4) : i + 1],
                    )
                    filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
                    bearish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BEARISH')
                    bullish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BULLISH')
                    
                    if opt_type == "CALL" and bearish_count >= self.sell_threshold:
                        exit_triggered = True
                        exit_reason = f"Indicator Reversal Signal ({bearish_count} Bearish)"
                    elif opt_type == "PUT" and bullish_count >= self.sell_threshold:
                        exit_triggered = True
                        exit_reason = f"Indicator Reversal Signal ({bullish_count} Bullish)"

                if exit_triggered:
                    self._exit_option_trade(opt_price, current_price, timestamp, exit_reason)
                    continue

                # ── Resolve effective exit strategy ─────────────────────────
                # sell_mode maps the UI dropdown to a strategy key.
                # If sell_mode is set (non-indicator), it overrides exit_strategy.
                eff_mode = self.sell_mode or self.exit_strategy

                # ── ATR-based Stop-Loss / Take-Profit ───────────────────────
                current_atr = current_bar.get('ATR', None)
                if current_atr is None or current_atr == 0:
                    current_atr = current_price * 0.01  # 1% fallback

                if not exit_triggered and eff_mode in ['sltp', 'hybrid', 'atr']:
                    if opt_type == 'CALL':
                        atr_sl = self.position['entry_underlying'] - self.atr_stop_multiplier * current_atr
                        if self.enable_atr_trail and getattr(self, 'atr_trail_multiplier', 0) > 0:
                            trail_sl = current_price - self.atr_trail_multiplier * current_atr
                            cur_trail = self.position.get('trailing_sl', atr_sl)
                            if trail_sl > cur_trail:
                                self.position['trailing_sl'] = trail_sl
                            atr_sl = max(atr_sl, self.position.get('trailing_sl', atr_sl))
                        atr_tp = self.position['entry_underlying'] + self.atr_take_profit_multiplier * current_atr
                        if current_price <= atr_sl:
                            exit_triggered = True
                            self.position['initial_stop_price'] = self.position.get('initial_stop_price') or atr_sl
                            self.position['exit_stop_price'] = atr_sl
                            exit_reason = f"ATR Stop Loss Hit (Underlying {current_price:.2f} <= SL {atr_sl:.2f})"
                        elif current_price >= atr_tp:
                            exit_triggered = True
                            exit_reason = f"ATR Take Profit Hit (Underlying {current_price:.2f} >= TP {atr_tp:.2f})"
                    else:  # PUT profits when underlying falls
                        atr_sl = self.position['entry_underlying'] + self.atr_stop_multiplier * current_atr
                        if self.enable_atr_trail and getattr(self, 'atr_trail_multiplier', 0) > 0:
                            trail_sl = current_price + self.atr_trail_multiplier * current_atr
                            cur_trail = self.position.get('trailing_sl', atr_sl)
                            if trail_sl < cur_trail:
                                self.position['trailing_sl'] = trail_sl
                            atr_sl = min(atr_sl, self.position.get('trailing_sl', atr_sl))
                        atr_tp = self.position['entry_underlying'] - self.atr_take_profit_multiplier * current_atr
                        if current_price >= atr_sl:
                            exit_triggered = True
                            self.position['initial_stop_price'] = self.position.get('initial_stop_price') or atr_sl
                            self.position['exit_stop_price'] = atr_sl
                            exit_reason = f"ATR Stop Loss Hit (Underlying {current_price:.2f} >= SL {atr_sl:.2f})"
                        elif current_price <= atr_tp:
                            exit_triggered = True
                            exit_reason = f"ATR Take Profit Hit (Underlying {current_price:.2f} <= TP {atr_tp:.2f})"

                # ── Percentage Stop-Loss / Take-Profit on Premium ───────────
                if not exit_triggered and eff_mode in ['percentage', 'hybrid_pct', 'premium_pct']:
                    pct_sl = self.regular_stop_loss_pct
                    pct_tp = self.regular_take_profit_pct
                    trail_pct = self.regular_trailing_pct
                    entry_prem = self.position['entry_premium']
                    # Track trailing high-water mark
                    if 'trail_high' not in self.position:
                        self.position['trail_high'] = opt_price
                    else:
                        self.position['trail_high'] = max(self.position['trail_high'], opt_price)
                    trail_sl = self.position['trail_high'] * (1 - trail_pct)
                    if opt_price <= entry_prem * (1 - pct_sl):
                        exit_triggered = True
                        self.position['initial_stop_price'] = self.position.get('initial_stop_price') or (entry_prem * (1 - pct_sl))
                        self.position['exit_stop_price'] = entry_prem * (1 - pct_sl)
                        exit_reason = f"Premium Stop Loss Hit (-{pct_sl*100:.0f}%)"
                    elif opt_price >= entry_prem * (1 + pct_tp):
                        exit_triggered = True
                        exit_reason = f"Premium Take Profit Hit (+{pct_tp*100:.0f}%)"
                    elif self.enable_pct_trail and trail_pct > 0 and opt_price < trail_sl and self.position['trail_high'] > entry_prem:
                        exit_triggered = True
                        self.position['initial_stop_price'] = self.position.get('initial_stop_price') or (entry_prem * (1 - pct_sl))
                        self.position['exit_stop_price'] = trail_sl
                        exit_reason = f"Trailing Stop Hit (Premium fell {trail_pct*100:.0f}% from high)"

                if exit_triggered:
                    self._exit_option_trade(opt_price, current_price, timestamp, exit_reason)
                    continue

            # Evaluate entries if no position is held
            else:
                signals = _generate_signals(
                    current_bar,
                    prev_bar,
                    settings={"enabled_indicators": self.enabled_indicators},
                    recent_bars=self.df.iloc[max(0, i - 4) : i + 1],
                )
                filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
                
                bullish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BULLISH')
                bearish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BEARISH')
                buy_threshold = max(1, int(self.threshold or 1))
                sell_threshold = max(1, int(self.sell_threshold or buy_threshold))

                desired_direction = "BULLISH" if self.options_type == "calls_only" else "BEARISH"
                option_type = "CALL" if self.options_type == "calls_only" else "PUT"
                expected_action = "BUY" if desired_direction == "BULLISH" else "SELL"
                indicator_action = "HOLD"
                if filtered_signals:
                    if desired_direction == "BULLISH" and bullish_count >= buy_threshold:
                        indicator_action = "BUY"
                    elif desired_direction == "BEARISH" and bearish_count >= sell_threshold:
                        indicator_action = "SELL"

                entry_mode = self.buy_mode
                price_action_modes = ['custom_price', 'atr', 'hybrid_atr', 'percentage', 'hybrid_pct', 'pivot_points', 'hybrid_pivot', 'high_low', 'hybrid_high_low', 'swing_points', 'hybrid_swing']
                hybrid_modes = {'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'}
                entry_action = "HOLD"
                entry_reason = ""
                if entry_mode in price_action_modes:
                    entry_signal = build_entry_signal(
                        price_history_records[: i + 1],
                        current_price=current_price,
                        entry_mode=entry_mode,
                        lookback_days=self.swing_entry_lookback_days,
                        market_session=self.market_session,
                        is_crypto=False,
                        atr=current_bar.get('ATR', current_price * 0.01),
                        custom_buy_price=self.custom_buy_price,
                        entry_atr_mult=self.entry_atr_multiplier,
                        entry_pct_move=self.entry_pct_move,
                        entry_atr_style=self.entry_atr_style,
                        entry_pct_style=self.entry_pct_style,
                        swing_entry_mode=self.swing_entry_mode,
                        desired_direction=desired_direction,
                        high_low_buffer_pct=self.daily_high_low_buffer_pct,
                    )
                    structure_action = entry_signal.get("action", "HOLD")
                    structure_ok = structure_action == expected_action and entry_signal.get("structure_direction") == desired_direction
                    indicator_ok = indicator_action == expected_action

                    if entry_mode in hybrid_modes:
                        if indicator_ok and structure_ok:
                            entry_action = expected_action
                            entry_reason = f"Hybrid {option_type}: {bullish_count if option_type=='CALL' else bearish_count} indicator signals + {entry_signal.get('reason', 'structure confirmed')}"
                    elif structure_ok:
                        entry_action = expected_action
                        entry_reason = entry_signal.get("reason", f"Entry trigger: {entry_mode}")

                elif indicator_action == expected_action:
                    entry_action = expected_action
                    entry_reason = (
                        f"Bullish signals crossover ({bullish_count} Bullish)"
                        if option_type == "CALL"
                        else f"Bearish signals crossover ({bearish_count} Bearish)"
                    )

                if self.enable_signal_reset and self.signal_locked_direction:
                    locked_dir = self.signal_locked_direction
                    reset_count = bullish_count if locked_dir == "BUY" else bearish_count
                    reset_threshold = buy_threshold if locked_dir == "BUY" else sell_threshold
                    if entry_action != locked_dir and reset_count < reset_threshold:
                        self.signal_locked_direction = None

                allowed, block_reason = self._entry_guard_allows(entry_action, current_bar, current_price, timestamp)
                if entry_action == expected_action and allowed:
                    self._enter_option_trade(
                        option_type=option_type,
                        underlying_price=current_price,
                        vol=current_vol,
                        timestamp=timestamp,
                        reason=entry_reason or f"{option_type} entry triggered"
                    )

            # Record equity curve
            self.equity_history.append({
                'time': self._to_utc_iso(timestamp),
                'equity': round(self.equity + (self._get_unrealized_pl(current_price, current_vol, timestamp) if self.position else 0.0), 2)
            })

        # Force close open option at the very end
        if self.position:
            last_bar = self.df.iloc[-1]
            last_ts = self.df.index[-1]
            expiration_close = self.position.get('expiration_close') or self._expiration_close_for_entry(self.position['entry_time'])
            t_remaining = self._time_to_expiry_years(last_ts, expiration_close)
            opt_price_bs = black_scholes(last_bar['Close'], self.position['strike_price'], t_remaining, self.r, last_bar['Historical_Vol'] + self.iv_markup, self.position['option_type'], q=self.q)
            opt_price_bs = self._lookup_option_price(last_ts, self.position['strike_price'], self.position['option_type'], self.expiry_days, opt_price_bs)
            opt_price = max(0.01, opt_price_bs - (self.spread_width / 2.0))
            self._exit_option_trade(opt_price, last_bar['Close'], last_ts, "End of Backtest")

        return self._calculate_metrics()

    def _cooldown_block_reason(self, timestamp):
        if not self.enable_cooldown or self.last_exit_time is None:
            return None
        from engine import parse_timeframe_to_seconds
        tf_seconds = parse_timeframe_to_seconds(self.timeframe)
        cooldown_secs = self.cooldown_minutes * 60 if self.cooldown_minutes is not None else tf_seconds * 4
        if (timestamp - self.last_exit_time).total_seconds() < cooldown_secs:
            return "Trade Cooldown Active"
        return None

    def _smart_reentry_allows(self, action, bar, current_price):
        if action not in ["BUY", "SELL"] or not self.enable_smart_reentry:
            return True, None

        if self.smart_reentry_type == "RSI":
            try:
                current_rsi = float(bar.get("RSI"))
            except (TypeError, ValueError):
                return True, None
            if action == "BUY" and current_rsi > self.smart_reentry_rsi_call_max:
                return False, f"Smart Re-entry RSI Filter: RSI {current_rsi:.1f} > max {self.smart_reentry_rsi_call_max:.1f}"
            if action == "SELL" and current_rsi < self.smart_reentry_rsi_put_min:
                return False, f"Smart Re-entry RSI Filter: RSI {current_rsi:.1f} < min {self.smart_reentry_rsi_put_min:.1f}"
            return True, None

        if self.smart_reentry_type == "VWAP":
            try:
                current_vwap = float(bar.get("VWAP"))
                current_price = float(current_price)
            except (TypeError, ValueError):
                return True, None
            if current_vwap <= 0 or current_price <= 0:
                return True, None
            dist_pct = abs(current_price - current_vwap) / current_vwap * 100.0
            if action == "BUY" and dist_pct > self.smart_reentry_vwap_call_max_dist_pct:
                return False, f"Smart Re-entry VWAP Filter: distance {dist_pct:.2f}% > max {self.smart_reentry_vwap_call_max_dist_pct:.2f}%"
            if action == "SELL" and dist_pct > self.smart_reentry_vwap_put_max_dist_pct:
                return False, f"Smart Re-entry VWAP Filter: distance {dist_pct:.2f}% > max {self.smart_reentry_vwap_put_max_dist_pct:.2f}%"
        return True, None

    def _entry_guard_allows(self, action, bar, current_price, timestamp):
        if action not in ["BUY", "SELL"]:
            return True, None

        if self.enable_signal_reset and self.signal_locked_direction == action:
            return False, f"Signal Reset Active: waiting for stale {action} signal to reset"

        cooldown_reason = self._cooldown_block_reason(timestamp)
        if cooldown_reason:
            return False, cooldown_reason

        return self._smart_reentry_allows(action, bar, current_price)

    def _enter_option_trade(self, option_type: str, underlying_price: float, vol: float, timestamp: datetime.datetime, reason: str):
        # Determine Strike Price
        strike_mode = str(self.strike_type or "ATM").upper()
        profile = _strike_profile(strike_mode)
        strike = _target_strike_from_profile(
            underlying_price,
            option_type,
            strike_mode,
            profile.get("target_depth_pct", 0.0),
        )
        
        # Round strike price to nearest integer or 0.50
        strike = round(strike * 2) / 2
        
        # Calculate theoretical option price (premium) at entry with IV markup and dividends
        expiration_close = self._expiration_close_for_entry(timestamp)
        t_years = self._time_to_expiry_years(timestamp, expiration_close)
        entry_premium_bs = black_scholes(underlying_price, strike, t_years, self.r, vol + self.iv_markup, option_type, q=self.q)
        entry_premium_bs = self._lookup_option_price(timestamp, strike, option_type, self.expiry_days, entry_premium_bs)
        entry_premium = max(0.01, entry_premium_bs + (self.spread_width / 2.0)) # Pay the ask

        if entry_premium <= 0:
            return

        contract_multiplier = 100.0
        single_contract_cost = entry_premium * contract_multiplier

        # Risk-based sizing
        risk_amount = self.equity * self.risk_per_trade
        risk_per_contract = single_contract_cost
        max_notional = self.equity * self.max_pos_pct

        if self.enable_risk:
            # Estimate risk distance per contract
            if self.sell_mode in ['percentage', 'hybrid_pct']:
                risk_per_contract = single_contract_cost * self.regular_stop_loss_pct
            else:
                # Conservative fallback: assume total loss of premium
                risk_per_contract = single_contract_cost

            if risk_per_contract <= 0:
                risk_per_contract = single_contract_cost

            risk_based_qty = risk_amount / risk_per_contract
            notional_qty = max_notional / single_contract_cost
        else:
            risk_based_qty = self.equity / single_contract_cost
            notional_qty = self.equity / single_contract_cost
        
        # Trade amount budget cap (Allocation limit)
        budget_qty = self.trade_amount / single_contract_cost
        
        # Final quantity is the minimum of all constraints (and can't exceed cash on hand)
        qty = math.floor(min(risk_based_qty, notional_qty, budget_qty, self.equity / single_contract_cost))
        if qty <= 0 and self.equity >= single_contract_cost:
            qty = 1

        if qty <= 0:
            limiting_reasons = []
            if self.enable_risk and risk_based_qty < 1:
                limiting_reasons.append(
                    f"risk cap ${risk_amount:.2f} is below estimated risk ${risk_per_contract:.2f}"
                )
            if budget_qty < 1:
                limiting_reasons.append(
                    f"allocation ${self.trade_amount:.2f} is below contract cost ${single_contract_cost:.2f}"
                )
            if self.enable_risk and notional_qty < 1:
                limiting_reasons.append(
                    f"max position cap ${max_notional:.2f} is below contract cost ${single_contract_cost:.2f}"
                )
            if (self.equity / single_contract_cost) < 1:
                limiting_reasons.append(
                    f"equity ${self.equity:.2f} is below contract cost ${single_contract_cost:.2f}"
                )
            limit_text = "; ".join(limiting_reasons) or "sizing rules allow less than 1 contract"
            if len(self.skipped_entries) < 25:
                self.skipped_entries.append({
                    "time": self._to_utc_iso(timestamp),
                    "reason": (
                        f"Skipped {option_type}: {limit_text}. Estimated premium ${entry_premium:.2f} "
                        f"(contract cost ${single_contract_cost:.2f})."
                    ),
                    "option_type": option_type,
                    "estimated_premium": round(float(entry_premium), 4),
                    "estimated_contract_cost": round(float(single_contract_cost), 2),
                    "trade_amount": round(float(self.trade_amount), 2),
                    "underlying_price": round(float(underlying_price), 2),
                    "strike": round(float(strike), 2),
                })
            return

        total_cost = qty * single_contract_cost

        # Record position
        self.position = {
            "option_type": option_type,
            "strike_price": strike,
            "entry_time": timestamp,
            "expiration_date": expiration_close.date(),
            "expiration_close": expiration_close,
            "entry_underlying": underlying_price,
            "entry_premium": entry_premium,
            "entry_price": entry_premium,
            "qty": qty,
            "contract_cost": single_contract_cost,
            "total_cost": total_cost,
            "initial_stop_price": None,
            "exit_stop_price": None,
            "reason": reason
        }

    def _exit_option_trade(self, option_premium: float, underlying_price: float, timestamp: datetime.datetime, reason: str):
        contract_multiplier = 100.0
        exit_value = self.position['qty'] * option_premium * contract_multiplier
        pl = exit_value - self.position['total_cost']
        pl_pct = (pl / self.position['total_cost']) * 100.0

        # Equity is tracked as account value, so realize only P/L on close.
        self.equity += pl

        exit_stop_price = self.position.get('exit_stop_price')
        initial_stop_price = self.position.get('initial_stop_price')
        stop_note = ""
        if exit_stop_price is not None:
            if initial_stop_price is not None and abs(float(exit_stop_price) - float(initial_stop_price)) > 1e-9:
                stop_note = f" | Stop Used: ${float(exit_stop_price):.4f} (initial ${float(initial_stop_price):.4f})"
            else:
                stop_note = f" | Stop Used: ${float(exit_stop_price):.4f}"

        self.trades.append({
            'ticker': self.ticker,
            'entry_time': self._to_utc_iso(self.position['entry_time']),
            'exit_time': self._to_utc_iso(timestamp),
            'qty': int(self.position['qty']),
            'option_type': self.position['option_type'],
            'expiration_date': self.position.get('expiration_date').isoformat() if self.position.get('expiration_date') else None,
            'strike': self.position['strike_price'],
            'entry_price': round(self.position['entry_premium'], 4),
            'exit_price': round(option_premium, 4),
            'entry_underlying': round(self.position['entry_underlying'], 2),
            'exit_underlying': round(underlying_price, 2),
            'entry_cost': round(self.position['total_cost'], 2),
            'exit_value': round(exit_value, 2),
            'fees': 0.0,
            'pl': round(pl, 2),
            'pl_pct': round(pl_pct, 2),
            'reason': reason,
            'initial_stop_price': round(initial_stop_price, 4) if initial_stop_price is not None else None,
            'exit_stop_price': round(exit_stop_price, 4) if exit_stop_price is not None else None,
        })
        print(f"[{self._to_central_str(timestamp)}] SELL {self.ticker} {self.position['option_type']} @ ${option_premium:.4f} | P/L: ${pl:.2f} ({pl_pct:+.2f}%) | Reason: {reason}{stop_note}")
        if self.enable_signal_reset:
            self.signal_locked_direction = 'BUY' if self.position['option_type'] == 'CALL' else 'SELL'
        self.last_exit_time = timestamp
        self.position = None

    def _get_unrealized_pl(self, current_price: float, vol: float, timestamp: datetime.datetime) -> float:
        opt_type = self.position['option_type']
        strike = self.position['strike_price']
        entry_date = self.position['entry_time']
        expiration_close = self.position.get('expiration_close') or self._expiration_close_for_entry(entry_date)
        t_remaining = self._time_to_expiry_years(timestamp, expiration_close)
        opt_price_bs = black_scholes(current_price, strike, t_remaining, self.r, vol + self.iv_markup, opt_type, q=self.q)
        opt_price_bs = self._lookup_option_price(timestamp, strike, opt_type, self.expiry_days, opt_price_bs)
        opt_price = max(0.01, opt_price_bs - (self.spread_width / 2.0))
        
        contract_multiplier = 100.0
        current_value = self.position['qty'] * opt_price * contract_multiplier
        return current_value - self.position['total_cost']

    def _calculate_metrics(self) -> dict:
        if not self.trades:
            start_price = float(self.df.iloc[0].get('Open', self.df.iloc[0]['Close'])) if self.df is not None and not self.df.empty else 0.0
            end_price = float(self.df.iloc[-1]['Close']) if self.df is not None and not self.df.empty else start_price
            buy_hold_equity = (end_price / start_price) * self.initial_capital if start_price > 0 else self.initial_capital
            buy_hold_roi = ((end_price - start_price) / start_price) * 100.0 if start_price > 0 else 0.0
            total_bars = len(self.df) if self.df is not None else 0
            candles = []
            if self.df is not None:
                for i in range(total_bars):
                    row = self.df.iloc[i]
                    ts = self.df.index[i]
                    try:
                        candles.append({
                            "time": self._to_unix(ts),
                            "open": round(float(row.get('Open', row.get('open', 0))), 4),
                            "high": round(float(row.get('High', row.get('high', 0))), 4),
                            "low": round(float(row.get('Low', row.get('low', 0))), 4),
                            "close": round(float(row.get('Close', row.get('close', 0))), 4),
                            "volume": int(row.get('Volume', row.get('volume', 0))),
                            **_chart_indicator_fields(row, 4, self.enabled_indicators, self.buy_mode, self.sell_mode),
                        })
                    except Exception:
                        continue
            # Mask out candlestick patterns for all candles except the single last candle to eliminate chart noise
            if "Candle Patterns" in self.enabled_indicators and len(candles) > 1:
                for c in candles[:-1]:
                    c["cdl_name"] = "Neutral"
                    c["cdl_bullish"] = False
                    c["cdl_bearish"] = False
            no_trade_reason = (
                self.skipped_entries[0]["reason"]
                if self.skipped_entries
                else "No option entries confirmed during the period. Try lowering thresholds, enabling more indicators, or choosing a looser entry strategy."
            )
            return {
                "summary": {
                    "ticker": self.ticker,
                    "timeframe": self.timeframe,
                    "period_days": max(0, (self.end_date - self.start_date).days),
                    "start_date_utc": self.start_date.isoformat(),
                    "end_date_utc": self.end_date.isoformat(),
                    "data_source": self.data_source,
                    "initial_capital": self.initial_capital,
                    "final_equity": round(self.equity, 2),
                    "total_pl": 0.0,
                    "roi_pct": 0.0,
                    "buy_hold_equity": round(buy_hold_equity, 2),
                    "buy_hold_roi_pct": round(buy_hold_roi, 2),
                    "win_rate_pct": 0.0,
                    "total_trades": 0,
                    "profitable_trades": 0,
                    "losing_trades": 0,
                    "avg_trade_pl": 0.0,
                    "best_trade": 0.0,
                    "worst_trade": 0.0,
                    "max_drawdown_pct": 0.0,
                    "sharpe_ratio": 0.0,
                    "sortino_ratio": 0.0,
                    "calmar_ratio": 0.0,
                    "profit_factor": 0.0,
                    "win_loss_ratio": 0.0,
                    "avg_hold_hours": 0.0,
                    "expectancy": 0.0,
                    "max_win_streak": 0,
                    "max_loss_streak": 0,
                    "no_trade_reason": no_trade_reason,
                    "skipped_entry_count": len(self.skipped_entries),
                },
                "equity_curve": self.equity_history[::max(1, len(self.equity_history)//200)] if self.equity_history else [],
                "trades": [],
                "candles": candles,
                "diagnostics": {
                    "no_trade_reason": no_trade_reason,
                    "skipped_entries": self.skipped_entries,
                },
            }

        df_trades = pd.DataFrame(self.trades)
        win_rate = (df_trades['pl'] > 0).mean() * 100.0
        total_pl = self.equity - self.initial_capital
        roi = (total_pl / self.initial_capital) * 100.0

        max_dd = 0.0
        if self.equity_history:
            equity_vals = [e['equity'] for e in self.equity_history]
            peak = equity_vals[0]
            for v in equity_vals:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd

        pl_series = df_trades['pl'].values
        entry_costs = df_trades['entry_cost'].values
        returns = np.where(entry_costs > 0, pl_series / entry_costs, 0.0)

        winning_pl = df_trades[df_trades['pl'] > 0]['pl']
        losing_pl = df_trades[df_trades['pl'] <= 0]['pl'].abs()
        gross_profit = winning_pl.sum()
        gross_loss = losing_pl.sum()
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if gross_profit > 0 else 0.0)
        avg_win = round(winning_pl.mean(), 2) if len(winning_pl) > 0 else 0.0
        avg_loss = round(losing_pl.mean(), 2) if len(losing_pl) > 0 else 0.0
        win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (None if avg_win > 0 else 0.0)
        avg_win_pct = round(df_trades[df_trades['pl'] > 0]['pl_pct'].mean(), 2) if len(winning_pl) > 0 else 0.0
        avg_loss_pct = round(df_trades[df_trades['pl'] <= 0]['pl_pct'].mean(), 2) if len(losing_pl) > 0 else 0.0
        wr = win_rate / 100.0
        expectancy = round((wr * avg_win) - ((1 - wr) * avg_loss), 2)

        sharpe = 0.0
        if len(returns) > 1 and returns.std() > 0:
            sharpe = round((returns.mean() / returns.std()) * np.sqrt(252), 2)

        sortino = 0.0
        downside = returns[returns < 0]
        if len(downside) > 1 and downside.std() > 0:
            sortino = round((returns.mean() / downside.std()) * np.sqrt(252), 2)

        calmar = 0.0
        period_days = max((self.end_date - self.start_date).days, 1)
        annualised_roi = roi * (365 / period_days)
        if max_dd > 0:
            calmar = round(annualised_roi / max_dd, 2)

        avg_hold_hours = 0.0
        try:
            entry_times = pd.to_datetime(df_trades['entry_time'])
            exit_times = pd.to_datetime(df_trades['exit_time'])
            hold_durations = (exit_times - entry_times).dt.total_seconds() / 3600
            avg_hold_hours = round(hold_durations.mean(), 1)
        except Exception:
            pass

        max_win_streak = max_loss_streak = cur_win = cur_loss = 0
        for pl_val in df_trades['pl']:
            if pl_val > 0:
                cur_win += 1
                cur_loss = 0
            else:
                cur_loss += 1
                cur_win = 0
            max_win_streak = max(max_win_streak, cur_win)
            max_loss_streak = max(max_loss_streak, cur_loss)

        # Calculate Buy & Hold from the first available bar (Open to final Close)
        start_price = float(self.df.iloc[0].get('Open', self.df.iloc[0]['Close']))
        end_price = float(self.df.iloc[-1]['Close'])
        buy_hold_equity = (end_price / start_price) * self.initial_capital
        buy_hold_roi = ((end_price - start_price) / start_price) * 100.0

        total_bars = len(self.df)
        candles = []
        for i in range(total_bars):
            row = self.df.iloc[i]
            ts = self.df.index[i]
            try:
                unix_time = self._to_unix(ts)
                candles.append({
                    "time": unix_time,
                    "open": round(float(row.get('Open', row.get('open', 0))), 4),
                    "high": round(float(row.get('High', row.get('high', 0))), 4),
                    "low": round(float(row.get('Low', row.get('low', 0))), 4),
                    "close": round(float(row.get('Close', row.get('close', 0))), 4),
                    "volume": int(row.get('Volume', row.get('volume', 0))),
                    **_chart_indicator_fields(row, 4, self.enabled_indicators, self.buy_mode, self.sell_mode),
                })
            except Exception:
                continue

        # Mask out candlestick patterns for all candles except the single last candle to eliminate chart noise
        if "Candle Patterns" in self.enabled_indicators and len(candles) > 1:
            for c in candles[:-1]:
                c["cdl_name"] = "Neutral"
                c["cdl_bullish"] = False
                c["cdl_bearish"] = False

        return {
            "summary": {
                "ticker": self.ticker,
                "timeframe": self.timeframe,
                "period_days": max(0, (self.end_date - self.start_date).days),
                "start_date_utc": self.start_date.isoformat(),
                "end_date_utc": self.end_date.isoformat(),
                "data_source": self.data_source,
                "options_type": self.options_type,
                "expiry_days": self.expiry_days,
                "strike_type": self.strike_type,
                "buy_mode": self.buy_mode,
                "sell_mode": self.sell_mode,
                "enable_risk": self.enable_risk,
                "initial_capital": self.initial_capital,
                "final_equity": round(self.equity, 2),
                "total_pl": round(total_pl, 2),
                "roi_pct": round(roi, 2),
                "buy_hold_equity": round(buy_hold_equity, 2),
                "buy_hold_roi_pct": round(buy_hold_roi, 2),
                "win_rate_pct": round(win_rate, 2),
                "total_trades": len(self.trades),
                "profitable_trades": int(len(df_trades[df_trades['pl'] > 0])),
                "losing_trades": int(len(df_trades[df_trades['pl'] <= 0])),
                "avg_trade_pl": round(df_trades['pl'].mean(), 2),
                "best_trade": round(df_trades['pl'].max(), 2),
                "worst_trade": round(df_trades['pl'].min(), 2),
                "max_drawdown_pct": round(max_dd, 2),
                "sharpe_ratio": sharpe,
                "sortino_ratio": sortino,
                "calmar_ratio": calmar,
                "profit_factor": profit_factor,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "avg_win_pct": avg_win_pct,
                "avg_loss_pct": avg_loss_pct,
                "win_loss_ratio": win_loss_ratio,
                "avg_hold_hours": avg_hold_hours,
                "expectancy": expectancy,
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "skipped_entry_count": len(self.skipped_entries),
            },
            "equity_curve": self.equity_history[::max(1, len(self.equity_history)//200)],
            "trades": self.trades[::-1],
            "candles": candles,
        }
