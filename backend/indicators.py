"""
Multi-Indicator Technical Analysis Engine.

Calculates a full suite of professional-grade indicators.
Exposes calculate_indicators_for_df and _generate_signals for backtesting.
"""

from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import ta as ta_lib
import config as global_config
from user_config import get_user_config

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from broker_factory import get_alpaca_clients
from webull_broker import WebullBroker

# Shared Webull instance for data only
wb_data_provider = WebullBroker()

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Mystic Pulse V2.0 Utilities
# ---------------------------------------------------------------------------
def _wilders_smooth(series, length):
    """Replicates Pine Script smoothed logic."""
    smooth = np.zeros(len(series))
    for i in range(len(series)):
        if i == 0:
            smooth[i] = series[i]
        else:
            if length == 0:
                smooth[i] = series[i]
            else:
                smooth[i] = smooth[i-1] - (smooth[i-1] / length) + series[i]
    return smooth

def _get_mystic_v2_presets(timeframe):
    """Maps Alpaca timeframes to V2.0 adaptive presets."""
    defaults = {
        '1m':  {'adx': 21, 'smth': 3},
        '5m':  {'adx': 18, 'smth': 2},
        '15m': {'adx': 14, 'smth': 1},
        '1h':  {'adx': 9,  'smth': 1},
        '4h':  {'adx': 7,  'smth': 1},
        '1d':  {'adx': 5,  'smth': 1}
    }
    key = '1h'
    if timeframe == '1Min': key = '1m'
    elif timeframe == '2Min': key = '5m'
    elif timeframe == '3Min': key = '5m'
    elif timeframe == '5Min': key = '5m'
    elif timeframe == '10Min': key = '15m'
    elif timeframe == '15Min': key = '15m'
    elif timeframe == '30Min': key = '30m'
    elif timeframe == '1Hour': key = '1h'
    elif timeframe == '2Hour': key = '1h'
    elif timeframe == '4Hour': key = '4h'
    elif timeframe == '1Day': key = '1d'
    return defaults.get(key, defaults['1h'])

# Alpaca clients (Dynamic access)
# ---------------------------------------------------------------------------
_stock_client = None
_crypto_client = None

def get_alpaca_clients():
    """Returns initialized Alpaca clients using the latest config keys (cached)."""
    global _stock_client, _crypto_client
    
    if not get_user_config().ALPACA_API_KEY or get_user_config().ALPACA_API_KEY == "your_alpaca_api_key_here":
        return None, None
    
    if _stock_client is None:
        print("[indicators] Initializing Alpaca Stock client...")
        _stock_client = StockHistoricalDataClient(get_user_config().ALPACA_API_KEY, get_user_config().ALPACA_SECRET_KEY)
    
    if _crypto_client is None:
        print("[indicators] Initializing Alpaca Crypto client...")
        _crypto_client = CryptoHistoricalDataClient(get_user_config().ALPACA_API_KEY, get_user_config().ALPACA_SECRET_KEY)
        
    return _stock_client, _crypto_client

_data_cache = {}

def get_full_analysis(ticker, timeframe="5Min", data_source="alpaca"):
    """
    Fetch live data from Alpaca or Webull, compute indicators, and return a result dict.
    Supports both Stocks and Crypto.
    """
    try:
        df = None

        # --- Mode 1: Webull Data (High Fidelity for Dashboard) ---
        if data_source == "webull":
            tf_map = {
                "30Sec": "s30", "1Min": "m1", "2Min": "m2", "3Min": "m3",
                "5Min": "m5", "10Min": "m10", "15Min": "m15", "30Min": "m30",
                "1Hour": "m60", "2Hour": "m120", "4Hour": "m240", "1Day": "D"
            }
            wb_tf = tf_map.get(timeframe, "m5")
            bars = wb_data_provider.get_bars(ticker, timespan=wb_tf, count=1200)
            if bars:
                if isinstance(bars, list) and len(bars) > 0:
                    # Check for dummy bar with None time
                    if isinstance(bars[0], dict) and bars[0].get('time') is None:
                        print(f"[indicators] Webull returned dummy bar for {ticker}. Falling back to Alpaca.")
                        bars = None
                
            if bars: # Check again after potential fallback
                # Handle crypto response structure: [{'symbol': '...', 'result': [...]}]
                if isinstance(bars, list) and len(bars) > 0 and isinstance(bars[0], dict) and 'result' in bars[0]:
                    bars = bars[0]['result']
                    print(f"[indicators] Extracted result list. New len: {len(bars)}")
                
                df = pd.DataFrame(bars)
                
                # Handle both string and numeric timestamps
                if pd.api.types.is_numeric_dtype(df['time']):
                    df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)
                else:
                    df['timestamp'] = pd.to_datetime(df['time'], utc=True)
                    
                df.set_index('timestamp', inplace=True)
                df.columns = [c.capitalize() for c in df.columns]
                df = df.sort_index() # Ensure chronological order for indicators
                print(f"[indicators] Webull bars fetched for {ticker}: {len(df)} rows. First time: {df.index[0]}, Last time: {df.index[-1]}")
            else:
                print(f"[indicators] Webull data failed for {ticker}. Falling back to Alpaca.")
                data_source = "alpaca"

        # --- Mode 2: Alpaca Data (Execution Feed) ---
        if data_source == "alpaca":
            # Initialize clients
            stock_client, crypto_client = get_alpaca_clients()
            
            clean_ticker = ticker.upper().replace("/", "")
            is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
            
            request_ticker = ticker
            if is_crypto:
                for base in ["USDT", "USDC", "USD"]:
                    if clean_ticker.endswith(base):
                        request_ticker = clean_ticker.replace(base, f"/{base}")
                        break

            if is_crypto:
                client = crypto_client
                request_class = CryptoBarsRequest
            else:
                client = stock_client
                request_class = StockBarsRequest

            if not client:
                print(f"[indicators] ERROR: Alpaca client not initialized.")
                return None

            end_date = datetime.now()
            cache_key = (ticker.upper(), timeframe.upper())

            # Timeframe -> Alpaca object + lookback
            tf_str = timeframe.upper()
            if tf_str in ["30SEC", "1MIN"]:
                tf_obj = TimeFrame.Minute
                lookback_days = 2
            elif tf_str == "2MIN":
                tf_obj = TimeFrame(2, TimeFrameUnit.Minute)
                lookback_days = 3
            elif tf_str == "3MIN":
                tf_obj = TimeFrame(3, TimeFrameUnit.Minute)
                lookback_days = 3
            elif tf_str == "5MIN":
                tf_obj = TimeFrame(5, TimeFrameUnit.Minute)
                lookback_days = 4
            elif tf_str == "10MIN":
                tf_obj = TimeFrame(10, TimeFrameUnit.Minute)
                lookback_days = 5
            elif tf_str == "15MIN":
                tf_obj = TimeFrame(15, TimeFrameUnit.Minute)
                lookback_days = 7
            elif tf_str == "30MIN":
                tf_obj = TimeFrame(30, TimeFrameUnit.Minute)
                lookback_days = 14
            elif tf_str in ["1HOUR", "60MIN"]:
                tf_obj = TimeFrame.Hour
                lookback_days = 30
            elif tf_str == "2HOUR":
                tf_obj = TimeFrame(2, TimeFrameUnit.Hour)
                lookback_days = 45
            elif tf_str == "4HOUR":
                tf_obj = TimeFrame(4, TimeFrameUnit.Hour)
                lookback_days = 60
            elif tf_str in ["1D", "1DAY"]:
                tf_obj = TimeFrame.Day
                lookback_days = 365
            else:
                tf_obj = TimeFrame(5, TimeFrameUnit.Minute)
                lookback_days = 5

            # Incremental cache
            if cache_key in _data_cache:
                last_bar_time, cached_df = _data_cache[cache_key]
                start_date = last_bar_time
            else:
                start_date = end_date - timedelta(days=lookback_days)
                cached_df = None

            request_params = request_class(
                symbol_or_symbols=request_ticker,
                timeframe=tf_obj,
                start=start_date,
                end=end_date,
            )
            
            if not is_crypto:
                request_params.feed = "iex"

            bars = client.get_stock_bars(request_params) if not is_crypto else client.get_crypto_bars(request_params)
            df = bars.df
            if df is not None and not df.empty:
                df = df[~df.index.duplicated(keep='last')] # Remove duplicates immediately
                if isinstance(df.index, pd.MultiIndex):
                    df = df.xs(request_ticker, level=0)
                
                if cached_df is not None:
                    df = pd.concat([cached_df, df])
                
                df = df[~df.index.duplicated(keep='last')]
                df = df.sort_index()
                _data_cache[cache_key] = (df.index[-1], df)

        if df is None or df.empty:
            return None

        # --- Indicator Calculations ---
        # Standardise column names (Webull/Alpaca fallback)
        df.columns = [c.capitalize() for c in df.columns]
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        open_ = df["Open"]
        volume = df["Volume"]

        if len(df) < 30:
            return None

        return calculate_indicators_for_df(df, timeframe, ticker)

    except Exception as exc:
        print(f"[indicators] Fetch error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Core engine: calculate_indicators_for_df
# ---------------------------------------------------------------------------
def calculate_indicators_for_df(df, timeframe="5Min", ticker="UNKNOWN"):
    """
    Compute every indicator on *df* and return a result dict.
    Designed to work identically for live analysis and backtesting.
    """
    try:
        # --- Column normalisation ------------------------------------------
        for col in ("Open", "High", "Low", "Close", "Volume"):
            lc = col.lower()
            if lc in df.columns and col not in df.columns:
                df[col] = df[lc]

        # Ensure numeric (yfinance sometimes returns object columns)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # --- RSI -----------------------------------------------------------
        df["RSI"] = ta_lib.momentum.rsi(close, window=get_user_config().RSI_PERIOD)

        # --- EMA -----------------------------------------------------------
        df["EMA_Fast"] = ta_lib.trend.ema_indicator(close, window=get_user_config().EMA_FAST)
        df["EMA_Slow"] = ta_lib.trend.ema_indicator(close, window=get_user_config().EMA_SLOW)

        # --- SMA -----------------------------------------------------------
        df["SMA"] = ta_lib.trend.sma_indicator(close, window=getattr(get_user_config(), "SMA_PERIOD", 200))

        # --- MACD ----------------------------------------------------------
        macd_ind = ta_lib.trend.MACD(
            close,
            window_fast=get_user_config().MACD_FAST,
            window_slow=get_user_config().MACD_SLOW,
            window_sign=get_user_config().MACD_SIGNAL,
        )
        df["MACD_Line"] = macd_ind.macd()
        df["MACD_Signal"] = macd_ind.macd_signal()
        df["MACD_Hist"] = macd_ind.macd_diff()

        # --- Bollinger Bands -----------------------------------------------
        boll_ind = ta_lib.volatility.BollingerBands(
            close, window=get_user_config().BOLL_PERIOD, window_dev=get_user_config().BOLL_STD_DEV
        )
        df["BOLL_Upper"] = boll_ind.bollinger_hband()
        df["BOLL_Lower"] = boll_ind.bollinger_lband()
        df["BOLL_Middle"] = boll_ind.bollinger_mavg()

        # --- Supertrend (Webull/TradingView Compatible Wilder's Smoothing) ---
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Wilder's Smoothing (Exponential Moving Average with alpha = 1/N)
        st_atr = tr.ewm(alpha=1/get_user_config().SUPERTREND_PERIOD, min_periods=get_user_config().SUPERTREND_PERIOD, adjust=False).mean()
        
        hl2 = (high + low) / 2.0
        st_mult = get_user_config().SUPERTREND_MULTIPLIER
        basic_ub = hl2 + st_mult * st_atr
        basic_lb = hl2 - st_mult * st_atr

        n = len(df)
        final_ub = np.empty(n)
        final_lb = np.empty(n)
        trend = np.empty(n, dtype=bool)
        st_vals = np.empty(n)

        # Convert to numpy for speed
        close_arr = close.to_numpy()
        bub = basic_ub.to_numpy()
        blb = basic_lb.to_numpy()

        # Find first non-NaN index to start calculation
        start_idx = 0
        for idx, val in enumerate(bub):
            if not np.isnan(val):
                start_idx = idx
                break

        final_ub[:] = np.nan
        final_lb[:] = np.nan
        trend[:] = True
        st_vals[:] = np.nan

        if start_idx < n:
            final_ub[start_idx] = bub[start_idx]
            final_lb[start_idx] = blb[start_idx]
            trend[start_idx] = True
            st_vals[start_idx] = blb[start_idx]

        for i in range(start_idx + 1, n):
            # Upper band
            if bub[i] < final_ub[i - 1] or close_arr[i - 1] > final_ub[i - 1]:
                final_ub[i] = bub[i]
            else:
                final_ub[i] = final_ub[i - 1]

            # Lower band
            if blb[i] > final_lb[i - 1] or close_arr[i - 1] < final_lb[i - 1]:
                final_lb[i] = blb[i]
            else:
                final_lb[i] = final_lb[i - 1]

            # Trend flip
            if trend[i - 1]:
                trend[i] = not (close_arr[i] < final_lb[i])
            else:
                trend[i] = close_arr[i] > final_ub[i]

            st_vals[i] = final_lb[i] if trend[i] else final_ub[i]

        df["Supertrend"] = st_vals
        df["Supertrend_Trend"] = trend

        # ---------------------------------------------------------
        # 4. AUTO-ANCHORED VWAP (Institutional Smart Line - Rolling Model)
        # ---------------------------------------------------------
        try:
            # Calculate typical price and PV across the whole DataFrame
            typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
            pv = typical_price * df["Volume"]
            
            # Pre-calculate Cumulative sums for O(1) interval summation
            cpv = pv.cumsum()
            cvol = df["Volume"].cumsum()
            
            # Find the Volume Spike indices (institutions entering)
            vol_ma = df["Volume"].rolling(window=getattr(get_user_config(), 'VOL_MA_PERIOD', 20), min_periods=1).mean()
            spike_condition = df["Volume"] > (vol_ma * getattr(get_user_config(), 'VOL_SPIKE_MULTIPLIER', 1.5))
            
            # Create a forward-filled Series of the most recent spike's integer index
            int_indices = pd.Series(range(len(df)), index=df.index)
            spike_int_indices = int_indices.where(spike_condition)
            current_anchor_idx = spike_int_indices.ffill()
            
            # Calculate rolling AVWAP for all rows
            avwap_vals = []
            for t_idx in range(len(df)):
                a_idx = current_anchor_idx.iloc[t_idx]
                if pd.isna(a_idx):
                    # Before the first anchor, fall back to standard daily VWAP
                    date_val = df.index[t_idx].date()
                    day_mask = df.index.date == date_val
                    day_df = df[day_mask]
                    day_pv = ( (day_df["High"] + day_df["Low"] + day_df["Close"]) / 3 * day_df["Volume"] ).cumsum()
                    day_vol = day_df["Volume"].cumsum()
                    
                    # Align index
                    current_time = df.index[t_idx]
                    day_val_pv = day_pv.loc[current_time]
                    day_val_vol = day_vol.loc[current_time]
                    avwap_vals.append(day_val_pv / day_val_vol if day_val_vol > 0 else close.iloc[t_idx])
                else:
                    a_idx = int(a_idx)
                    sum_pv = cpv.iloc[t_idx] - (cpv.iloc[a_idx - 1] if a_idx > 0 else 0)
                    sum_vol = cvol.iloc[t_idx] - (cvol.iloc[a_idx - 1] if a_idx > 0 else 0)
                    avwap_vals.append(sum_pv / sum_vol if sum_vol > 0 else close.iloc[t_idx])
            
            df["VWAP"] = avwap_vals
            df["Anchor_Date"] = current_anchor_idx.map(lambda idx: df.index[int(idx)] if not pd.isna(idx) else pd.NaT)

        except Exception as e:
            print(f"[indicators] VWAP Calc Error: {e}")
            df["VWAP"] = close.copy()
            df["Anchor_Date"] = pd.NaT

        # --- ATR (Wilder's Smoothing) --------------------------------------
        df["ATR"] = tr.ewm(alpha=1/get_user_config().ATR_PERIOD, min_periods=get_user_config().ATR_PERIOD, adjust=False).mean()

        # --- ADX (Standard Welles Wilder ADX / DI+ / DI-) -------------------
        adx_ind = ta_lib.trend.ADXIndicator(
            high=high, low=low, close=close, window=getattr(get_user_config(), "ADX_PERIOD", 14)
        )
        df["ADX"] = adx_ind.adx()
        df["DI_Plus"] = adx_ind.adx_pos()
        df["DI_Minus"] = adx_ind.adx_neg()

        # --- Mystic Pulse V2.0 (Adaptive DMI + Persistence Engine) ----------
        presets = _get_mystic_v2_presets(timeframe)
        adx_len = presets['adx']
        smth = presets['smth']

        # 1. Pre-Smoothing (Adaptive SMA Filter)
        if smth > 1:
            work_close = df["Close"].rolling(window=smth).mean()
            work_high = df["High"].rolling(window=smth).mean()
            work_low = df["Low"].rolling(window=smth).mean()
        else:
            work_close = df["Close"]
            work_high = df["High"]
            work_low = df["Low"]

        # 2. Components (TR, +DM, -DM)
        tr = pd.concat([
            work_high - work_low,
            (work_high - work_close.shift(1)).abs(),
            (work_low - work_close.shift(1)).abs()
        ], axis=1).max(axis=1).fillna(0)

        up_move = work_high - work_high.shift(1)
        dn_move = work_low.shift(1) - work_low
        plus_dm = np.where((up_move > dn_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0)

        # 3. Wilder's Smoothing
        smoothed_tr = _wilders_smooth(tr.values, adx_len)
        smoothed_pdm = _wilders_smooth(plus_dm, adx_len)
        smoothed_mdm = _wilders_smooth(minus_dm, adx_len)

        # 4. DI Plus/Minus
        denom = np.where(smoothed_tr == 0, 1.0, smoothed_tr)
        df["plus_di"] = (smoothed_pdm / denom) * 100
        df["minus_di"] = (smoothed_mdm / denom) * 100

        # 5. Persistence Counter (V2 Logic)
        pos_count = np.zeros(len(df))
        neg_count = np.zeros(len(df))
        c_pos, c_neg = 0, 0
        di_plus = df["plus_di"].values
        di_minus = df["minus_di"].values

        for i in range(1, len(df)):
            if not np.isnan(di_plus[i]) and not np.isnan(di_plus[i-1]) and di_plus[i] > di_plus[i-1] and di_plus[i] > di_minus[i]:
                c_pos += 1
                c_neg = 0
            elif not np.isnan(di_minus[i]) and not np.isnan(di_minus[i-1]) and di_minus[i] > di_minus[i-1] and di_minus[i] > di_plus[i]:
                c_neg += 1
                c_pos = 0
            pos_count[i] = c_pos
            neg_count[i] = c_neg

        df["Bull_Pulse"] = pos_count
        df["Bear_Pulse"] = neg_count

        # 6. Normalization for Strategy Entry (Matching the "Neon" visuals)
        collect_length = 100
        df['max_p'] = df['Bull_Pulse'].rolling(collect_length).max()
        df['max_n'] = df['Bear_Pulse'].rolling(collect_length).max()

        # 7. Signal Logic (Threshold of 5 bars)
        df["mp_buy_trigger"] = (df["Bull_Pulse"] >= 5) & (df["Bull_Pulse"].shift(1) < 5)
        df["mp_sell_trigger"] = (df["Bear_Pulse"] >= 5) & (df["Bear_Pulse"].shift(1) < 5)

        # --- Candlestick Patterns ------------------------------------------
        # Bullish Engulfing
        bull_eng = (df["Close"].shift(1) < df["Open"].shift(1)) & \
                   (df["Close"] > df["Open"]) & \
                   (df["Close"] >= df["Open"].shift(1)) & \
                   (df["Open"] <= df["Close"].shift(1))
        
        # Bearish Engulfing
        bear_eng = (df["Close"].shift(1) > df["Open"].shift(1)) & \
                   (df["Close"] < df["Open"]) & \
                   (df["Close"] <= df["Open"].shift(1)) & \
                   (df["Open"] >= df["Close"].shift(1))
        
        # Hammer (Simplified: long lower wick, small body at top)
        body = (df["Close"] - df["Open"]).abs()
        range_ = df["High"] - df["Low"]
        lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]
        upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
        hammer = (lower_wick > body * 2) & (upper_wick < body) & (range_ > 0)
        
        # Shooting Star (Simplified: long upper wick, small body at bottom)
        shooting_star = (upper_wick > body * 2) & (lower_wick < body) & (range_ > 0)

        # Doji (Indecision: very small body relative to range)
        doji = (body <= (range_ * 0.1)) & (range_ > 0)

        # Marubozu (Strong Momentum: very small wicks relative to body)
        bull_marubozu = (body > (range_ * 0.9)) & (df["Close"] > df["Open"])
        bear_marubozu = (body > (range_ * 0.9)) & (df["Close"] < df["Open"])

        # Morning Star (3-candle bullish reversal)
        morning_star = (df["Close"].shift(2) < df["Open"].shift(2)) & \
                       ((df["Open"].shift(1) - df["Close"].shift(1)).abs() < (df["Open"].shift(2) - df["Close"].shift(2)).abs() * 0.3) & \
                       (df["Close"] > df["Open"]) & \
                       (df["Close"] > (df["Open"].shift(2) + df["Close"].shift(2)) / 2)

        # Evening Star (3-candle bearish reversal)
        evening_star = (df["Close"].shift(2) > df["Open"].shift(2)) & \
                       ((df["Open"].shift(1) - df["Close"].shift(1)).abs() < (df["Close"].shift(2) - df["Open"].shift(2)).abs() * 0.3) & \
                       (df["Close"] < df["Open"]) & \
                       (df["Close"] < (df["Open"].shift(2) + df["Close"].shift(2)) / 2)

        # Hanging Man & Inverted Hammer
        # Note: Hanging Man is a hammer at a swing high, Inverted Hammer is a shooting star at a swing low
        uptrend = df["Close"] > df["Close"].shift(5)
        downtrend = df["Close"] < df["Close"].shift(5)
        
        hanging_man = hammer & uptrend
        inverted_hammer = shooting_star & downtrend
        
        # Override basic hammer/shooting star if trend context applies
        hammer = hammer & downtrend
        shooting_star = shooting_star & uptrend

        # Piercing Line (Bullish Reversal)
        piercing_line = (df["Close"].shift(1) < df["Open"].shift(1)) & \
                        (df["Open"] < df["Low"].shift(1)) & \
                        (df["Close"] > (df["Open"].shift(1) + df["Close"].shift(1)) / 2) & \
                        (df["Close"] < df["Open"].shift(1))

        # Dark Cloud Cover (Bearish Reversal)
        dark_cloud = (df["Close"].shift(1) > df["Open"].shift(1)) & \
                     (df["Open"] > df["High"].shift(1)) & \
                     (df["Close"] < (df["Open"].shift(1) + df["Close"].shift(1)) / 2) & \
                     (df["Close"] > df["Open"].shift(1))

        # Three White Soldiers (Strong Bullish)
        three_white_soldiers = (df["Close"] > df["Open"]) & (df["Close"].shift(1) > df["Open"].shift(1)) & (df["Close"].shift(2) > df["Open"].shift(2)) & \
                               (df["Close"] > df["Close"].shift(1)) & (df["Close"].shift(1) > df["Close"].shift(2)) & \
                               (df["Open"] > df["Open"].shift(1)) & (df["Open"] < df["Close"].shift(1)) & \
                               (upper_wick < body * 0.2) & (df["High"].shift(1) - df[["Open", "Close"]].shift(1).max(axis=1) < (df["Close"].shift(1) - df["Open"].shift(1)) * 0.2)

        # Three Black Crows (Strong Bearish)
        three_black_crows = (df["Close"] < df["Open"]) & (df["Close"].shift(1) < df["Open"].shift(1)) & (df["Close"].shift(2) < df["Open"].shift(2)) & \
                            (df["Close"] < df["Close"].shift(1)) & (df["Close"].shift(1) < df["Close"].shift(2)) & \
                            (df["Open"] < df["Open"].shift(1)) & (df["Open"] > df["Close"].shift(1)) & \
                            (lower_wick < body * 0.2) & (df[["Open", "Close"]].shift(1).min(axis=1) - df["Low"].shift(1) < (df["Open"].shift(1) - df["Close"].shift(1)) * 0.2)

        # Specialized Dojis
        dragonfly_doji = doji & (lower_wick > body * 3) & (upper_wick < body)
        gravestone_doji = doji & (upper_wick > body * 3) & (lower_wick < body)

        df["cdl_bullish"] = bull_eng | hammer | inverted_hammer | bull_marubozu | morning_star | piercing_line | three_white_soldiers | dragonfly_doji
        df["cdl_bearish"] = bear_eng | shooting_star | hanging_man | bear_marubozu | evening_star | dark_cloud | three_black_crows | gravestone_doji
        df["cdl_name"] = "Neutral"
        
        # Apply labels (order matters for precedence if multiple match, lower = higher precedence)
        df.loc[doji, "cdl_name"] = "Doji"
        df.loc[bull_marubozu, "cdl_name"] = "Bull Marubozu"
        df.loc[bear_marubozu, "cdl_name"] = "Bear Marubozu"
        df.loc[hammer, "cdl_name"] = "Hammer"
        df.loc[inverted_hammer, "cdl_name"] = "Inverted Hammer"
        df.loc[shooting_star, "cdl_name"] = "Shooting Star"
        df.loc[hanging_man, "cdl_name"] = "Hanging Man"
        df.loc[dragonfly_doji, "cdl_name"] = "Dragonfly Doji"
        df.loc[gravestone_doji, "cdl_name"] = "Gravestone Doji"
        df.loc[bull_eng, "cdl_name"] = "Bull Engulfing"
        df.loc[bear_eng, "cdl_name"] = "Bear Engulfing"
        df.loc[piercing_line, "cdl_name"] = "Piercing Line"
        df.loc[dark_cloud, "cdl_name"] = "Dark Cloud Cover"
        df.loc[morning_star, "cdl_name"] = "Morning Star"
        df.loc[evening_star, "cdl_name"] = "Evening Star"
        df.loc[three_white_soldiers, "cdl_name"] = "3 White Soldiers"
        df.loc[three_black_crows, "cdl_name"] = "3 Black Crows"

        # --- Build result --------------------------------------------------
        # Forward-fill then back-fill any remaining NaN from warm-up period
        indicator_cols = [
            "RSI", "EMA_Fast", "EMA_Slow", "SMA",
            "MACD_Line", "MACD_Signal", "MACD_Hist",
            "BOLL_Upper", "BOLL_Lower", "BOLL_Middle",
            "Supertrend", "ATR", "VWAP",
            "ADX", "DI_Plus", "DI_Minus",
        ]
        for col in indicator_cols:
            if col in df.columns:
                df[col] = df[col].ffill().bfill()

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        signals = _generate_signals(latest, prev)

        def _safe(val):
            """Return float or None, never NaN/Inf."""
            try:
                v = float(val)
                return None if (v != v or v == float('inf') or v == float('-inf')) else v
            except Exception:
                return None

        return {
            "ticker": ticker,
            "price": float(latest["Close"]),
            "atr": round(float(latest["ATR"]), 4),
            "rsi": round(float(latest["RSI"]), 2),
            "bullish_count": sum(
                s.get("weight", 1) for s in signals.values() if s["signal"] == "BULLISH"
            ),
            "bearish_count": sum(
                s.get("weight", 1) for s in signals.values() if s["signal"] == "BEARISH"
            ),
            "signals": signals,
            "price_history": [
                {
                    "time": int(t.timestamp()),
                    "open": _safe(row["Open"]),
                    "high": _safe(row["High"]),
                    "low": _safe(row["Low"]),
                    "close": _safe(row["Close"]),
                    "volume": _safe(row["Volume"]),
                    # Overlay indicators (drawn on price chart)
                    "ema_fast":      _safe(row.get("EMA_Fast")),
                    "ema_slow":      _safe(row.get("EMA_Slow")),
                    "sma":           _safe(row.get("SMA")),
                    "vwap":          _safe(row.get("VWAP")),
                    "supertrend":    _safe(row.get("Supertrend")),
                    "supertrend_up": bool(row.get("Supertrend_Trend", True)),
                    "boll_upper":    _safe(row.get("BOLL_Upper")),
                    "boll_middle":   _safe(row.get("BOLL_Middle")),
                    "boll_lower":    _safe(row.get("BOLL_Lower")),
                    # Oscillators (drawn in sub-pane)
                    "rsi":           _safe(row.get("RSI")),
                    "macd_line":     _safe(row.get("MACD_Line")),
                    "macd_signal":   _safe(row.get("MACD_Signal")),
                    "macd_hist":     _safe(row.get("MACD_Hist")),
                    "mystic_bull":   _safe(row.get("Bull_Pulse")),
                    "mystic_bear":   _safe(row.get("Bear_Pulse")),
                } for t, row in df.iterrows()
            ],
            "df": df,
        }

    except Exception as exc:
        print(f"[indicators] Calc error for {ticker}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Signal generation (used by live engine AND backtester)
# ---------------------------------------------------------------------------
def _generate_signals(latest, prev):
    """
    Read indicator values from a single bar and return signal verdicts.
    """
    try:
        rsi_val = float(latest["RSI"])
        macd_h = float(latest["MACD_Hist"])
        ema_f = float(latest["EMA_Fast"])
        ema_s = float(latest["EMA_Slow"])
        st_bull = bool(latest["Supertrend_Trend"])
        price = float(latest["Close"])
        boll_lo = float(latest["BOLL_Lower"])
        boll_hi = float(latest["BOLL_Upper"])
        bull_p = int(latest["Bull_Pulse"])
        bear_p = int(latest["Bear_Pulse"])

        # Smart AVWAP Dynamics
        vwap = float(latest["VWAP"])
        prev_vwap = float(prev.get("VWAP", vwap))
        is_near_vwap = abs(price - vwap) / vwap < 0.015  # Price is within 1.5% of the Anchor Line
        bull_candle = bool(latest.get("cdl_bullish", False))
        prev_close = float(prev.get("Close", price))

        sma_val = float(latest.get("SMA", price))

        adx_val = float(latest.get("ADX", 0))
        di_plus = float(latest.get("DI_Plus", 0))
        di_minus = float(latest.get("DI_Minus", 0))

        # Check if trend strength exists (> 25)
        is_trending = adx_val > getattr(get_user_config(), "ADX_TRENDING_THRESHOLD", 25)

        # ADX Trend signal generator
        if is_trending:
            if di_plus > di_minus:
                adx_signal = "BULLISH"
                adx_reason = f"DI+ ({di_plus:.1f}) > DI- ({di_minus:.1f}) | Trend Strong ({adx_val:.1f})"
            elif di_minus > di_plus:
                adx_signal = "BEARISH"
                adx_reason = f"DI- ({di_minus:.1f}) > DI+ ({di_plus:.1f}) | Trend Strong ({adx_val:.1f})"
            else:
                adx_signal = "NEUTRAL"
                adx_reason = f"DI Neutral | Trend Strong ({adx_val:.1f})"
        else:
            adx_signal = "NEUTRAL"
            adx_reason = f"Flat/Choppy Market (ADX: {adx_val:.1f})"

        return {
            "RSI": {
                "signal": (
                    "BULLISH" if rsi_val < get_user_config().RSI_OVERSOLD
                    else "BEARISH" if rsi_val > get_user_config().RSI_OVERBOUGHT
                    else "NEUTRAL"
                ),
                "reason": (
                    f"Oversold {rsi_val:.1f}" if rsi_val < get_user_config().RSI_OVERSOLD
                    else f"Overbought {rsi_val:.1f}" if rsi_val > get_user_config().RSI_OVERBOUGHT
                    else f"Neutral {rsi_val:.1f}"
                ),
                "weight": 1,
            },
            "MACD": {
                "signal": (
                    "BULLISH" if macd_h > 0 
                    else "BEARISH" if macd_h < 0
                    else "NEUTRAL"
                ),
                "reason": (
                    f"Bullish Cross" if (macd_h > 0 and float(prev.get("MACD_Hist", 0)) <= 0)
                    else f"Bearish Cross" if (macd_h < 0 and float(prev.get("MACD_Hist", 0)) >= 0)
                    else f"Rising {macd_h:.2f}" if macd_h > 0
                    else f"Falling {macd_h:.2f}"
                ),
                "weight": 1,
            },
            "EMA Cross": {
                "signal": ("BULLISH" if ema_f > ema_s else "BEARISH") if is_trending else "NEUTRAL",
                "reason": f"{ema_f:.1f} vs {ema_s:.1f} (ADX: {adx_val:.1f})" if is_trending else f"Ignored: Choppy Market (ADX {adx_val:.1f})",
                "weight": 1,
            },
            "ADX Trend": {
                "signal": adx_signal,
                "reason": adx_reason,
                "weight": 1,
            },
            "Supertrend": {
                "signal": "BULLISH" if st_bull else "BEARISH",
                "reason": f"{'Above' if st_bull else 'Below'} {float(latest['Supertrend']):.1f}",
                "weight": 1,
            },
            "Bollinger": {
                "signal": (
                    "BULLISH" if price <= (boll_lo + (boll_hi - boll_lo) * 0.2)
                    else "BEARISH" if price >= (boll_hi - (boll_hi - boll_lo) * 0.2)
                    else "NEUTRAL"
                ),
                "reason": (
                    f"Near Low {boll_lo:.1f}" if price <= (boll_lo + (boll_hi - boll_lo) * 0.2)
                    else f"Near High {boll_hi:.1f}" if price >= (boll_hi - (boll_hi - boll_lo) * 0.2)
                    else "In Channel"
                ),
                "weight": 1,
            },
            "VWAP": {
                "signal": (
                    # BUY: Price breaks above AVWAP or holds support with a bullish candle
                    "BULLISH" if (price >= vwap and prev_close < prev_vwap) or (price >= vwap and is_near_vwap and bull_candle)
                    # SELL: Price collapses below AVWAP (distribution)
                    else "BEARISH" if price < vwap
                    else "NEUTRAL"
                ),
                "reason": (
                    f"AVWAP Breakout ({vwap:.1f})" if (price >= vwap and prev_close < prev_vwap)
                    else f"Bounce off AVWAP ({vwap:.1f})" if (price >= vwap and is_near_vwap and bull_candle)
                    else f"Below AVWAP ({vwap:.1f})" if price < vwap
                    else f"Holding above AVWAP ({vwap:.1f})"
                ),
                "weight": 1
            },
            "SMA": {
                "signal": "BULLISH" if price >= sma_val else "BEARISH",
                "reason": f"Above SMA ({sma_val:.1f})" if price >= sma_val else f"Below SMA ({sma_val:.1f})",
                "weight": 1,
            },
            "Mystic Pulse": {
                "signal": (
                    "BULLISH" if bull_p >= 5
                    else "BEARISH" if bear_p >= 5
                    else "NEUTRAL"
                ),
                "reason": f"Strength {int(bull_p if bull_p > bear_p else bear_p)}/5",
                "weight": 1,
            },
            "Candle Patterns": {
                "signal": (
                    "BULLISH" if bool(latest.get("cdl_bullish", False))
                    else "BEARISH" if bool(latest.get("cdl_bearish", False))
                    else "NEUTRAL"
                ),
                "reason": f"Pattern: {latest.get('cdl_name', 'None')}",
                "weight": 1,
            },
        }

    except Exception:
        return {}
