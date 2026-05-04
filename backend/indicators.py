"""
Multi-Indicator Technical Analysis Engine.

Calculates a full suite of professional-grade indicators.
Exposes calculate_indicators_for_df and _generate_signals for backtesting.
"""

from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import ta as ta_lib
import pandas_ta as pta
import config

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
import config


# ---------------------------------------------------------------------------
# Alpaca clients (initialised once at import time)
# ---------------------------------------------------------------------------
stock_client = None
crypto_client = None

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
    elif timeframe == '5Min': key = '5m'
    elif timeframe == '15Min': key = '15m'
    elif timeframe == '1Hour': key = '1h'
    elif timeframe == '1Day': key = '1d'
    return defaults.get(key, defaults['1h'])

if config.ALPACA_API_KEY and config.ALPACA_API_KEY != "your_alpaca_api_key_here":
    stock_client = StockHistoricalDataClient(
        config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY
    )
    crypto_client = CryptoHistoricalDataClient(
        config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY
    )

_data_cache = {}


# ---------------------------------------------------------------------------
# Public API: get_full_analysis
# ---------------------------------------------------------------------------
def get_full_analysis(ticker, timeframe="5Min"):
    """
    Fetch live data from Alpaca, compute indicators, and return a result dict.
    Supports both Stocks and Crypto.
    """
    try:
        # Detect if it's crypto (standard Alpaca crypto pairs)
        clean_ticker = ticker.upper().replace("/", "")
        is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        
        request_ticker = ticker
        if is_crypto:
            # Format as BASE/QUOTE (e.g., BTC/USD)
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
            return None

        end_date = datetime.now()
        cache_key = (ticker, timeframe)

        # Timeframe -> Alpaca object + lookback
        tf_str = timeframe.upper()
        if tf_str == "1MIN":
            tf_obj = TimeFrame.Minute
            lookback_days = 1
        elif tf_str == "5MIN":
            tf_obj = TimeFrame(5, TimeFrameUnit.Minute)
            lookback_days = 3
        elif tf_str == "15MIN":
            tf_obj = TimeFrame(15, TimeFrameUnit.Minute)
            lookback_days = 7
        elif tf_str == "30MIN":
            tf_obj = TimeFrame(30, TimeFrameUnit.Minute)
            lookback_days = 14
        elif tf_str in ["1HOUR", "60MIN"]:
            tf_obj = TimeFrame.Hour
            lookback_days = 30
        elif tf_str == "4HOUR":
            tf_obj = TimeFrame(4, TimeFrameUnit.Hour)
            lookback_days = 60
        elif tf_str in ["1D", "1DAY"]:
            tf_obj = TimeFrame.Day
            lookback_days = 365
        else:
            tf_obj = TimeFrame(5, TimeFrameUnit.Minute)
            lookback_days = 7

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
        
        # Stocks need a feed, Crypto does not
        if not is_crypto:
            request_params.feed = "iex"

        try:
            if is_crypto:
                print(f"[indicators] Fetching Crypto Bars for {request_ticker} ({timeframe})")
                bars = client.get_crypto_bars(request_params)
            else:
                print(f"[indicators] Fetching Stock Bars for {request_ticker} ({timeframe})")
                bars = client.get_stock_bars(request_params)
                
            if bars.df.empty:
                print(f"[indicators] WARNING: Bars DF is EMPTY for {ticker}")
                new_df = pd.DataFrame()
            else:
                # Handle MultiIndex if necessary, or just extract the ticker
                if isinstance(bars.df.index, pd.MultiIndex):
                    if request_ticker in bars.df.index.levels[0]:
                        new_df = bars.df.loc[request_ticker].copy()
                    else:
                        print(f"[indicators] Ticker {request_ticker} not found in MultiIndex")
                        new_df = pd.DataFrame()
                else:
                    new_df = bars.df.copy()
                
                print(f"[indicators] Processed {len(new_df)} bars for {ticker}")
        except Exception as e:
            print(f"[indicators] Request error for {request_ticker}: {e}")
            new_df = pd.DataFrame()

        # Merge with cache
        if cached_df is not None:
            if not new_df.empty:
                df = pd.concat([cached_df, new_df])
                df = df[~df.index.duplicated(keep="last")].sort_index()
                max_bars = 1300 if timeframe == "1Day" else 1000
                df = df.tail(max_bars)
            else:
                df = cached_df.copy()
        else:
            if new_df.empty:
                return None
            df = new_df.copy().sort_index()

        _data_cache[cache_key] = (df.index[-1], df.copy())

        if len(df) < 30:
            return None

        # Alpaca columns are lowercase; standardise to Title-case
        df = df.rename(
            columns={
                "close": "Close",
                "high": "High",
                "low": "Low",
                "open": "Open",
                "volume": "Volume",
            }
        )

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

        # --- RSI -----------------------------------------------------------
        df["RSI"] = ta_lib.momentum.rsi(close, window=config.RSI_PERIOD)

        # --- EMA -----------------------------------------------------------
        df["EMA_Fast"] = ta_lib.trend.ema_indicator(close, window=config.EMA_FAST)
        df["EMA_Slow"] = ta_lib.trend.ema_indicator(close, window=config.EMA_SLOW)

        # --- MACD ----------------------------------------------------------
        macd_ind = ta_lib.trend.MACD(
            close,
            window_fast=config.MACD_FAST,
            window_slow=config.MACD_SLOW,
            window_sign=config.MACD_SIGNAL,
        )
        df["MACD_Line"] = macd_ind.macd()
        df["MACD_Signal"] = macd_ind.macd_signal()
        df["MACD_Hist"] = macd_ind.macd_diff()

        # --- Bollinger Bands -----------------------------------------------
        boll_ind = ta_lib.volatility.BollingerBands(
            close, window=config.BOLL_PERIOD, window_dev=config.BOLL_STD_DEV
        )
        df["BOLL_Upper"] = boll_ind.bollinger_hband()
        df["BOLL_Lower"] = boll_ind.bollinger_lband()
        df["BOLL_Middle"] = boll_ind.bollinger_mavg()

        # --- Supertrend ----------------------------------------------------
        st_atr = ta_lib.volatility.average_true_range(
            high, low, close, window=config.SUPERTREND_PERIOD
        )
        hl2 = (high + low) / 2.0
        st_mult = config.SUPERTREND_MULTIPLIER
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

        final_ub[0] = bub[0]
        final_lb[0] = blb[0]
        trend[0] = True
        st_vals[0] = blb[0]

        for i in range(1, n):
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

        # --- VWAP (daily session reset) ------------------------------------
        try:
            pv = df["Close"] * df["Volume"]
            cum_pv = pv.groupby(df.index.date).cumsum()
            cum_vol = df["Volume"].groupby(df.index.date).cumsum()
            # Avoid division by zero
            df["VWAP"] = np.where(cum_vol > 0, cum_pv / cum_vol, close)
        except Exception:
            df["VWAP"] = close.copy()

        # --- ATR -----------------------------------------------------------
        df["ATR"] = ta_lib.volatility.average_true_range(
            high, low, close, window=config.ATR_PERIOD
        )

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

        df["cdl_bullish"] = bull_eng | hammer | bull_marubozu | morning_star
        df["cdl_bearish"] = bear_eng | shooting_star | bear_marubozu | evening_star
        df["cdl_name"] = "Neutral"
        df.loc[bull_eng, "cdl_name"] = "Bullish Engulfing"
        df.loc[hammer, "cdl_name"] = "Hammer"
        df.loc[bear_eng, "cdl_name"] = "Bearish Engulfing"
        df.loc[shooting_star, "cdl_name"] = "Shooting Star"
        df.loc[doji, "cdl_name"] = "Doji (Indecision)"
        df.loc[bull_marubozu, "cdl_name"] = "Bull Marubozu"
        df.loc[bear_marubozu, "cdl_name"] = "Bear Marubozu"
        df.loc[morning_star, "cdl_name"] = "Morning Star"
        df.loc[evening_star, "cdl_name"] = "Evening Star"

        # --- Build result --------------------------------------------------
        # Forward-fill then back-fill any remaining NaN from warm-up period
        indicator_cols = [
            "RSI", "EMA_Fast", "EMA_Slow",
            "MACD_Line", "MACD_Signal", "MACD_Hist",
            "BOLL_Upper", "BOLL_Lower", "BOLL_Middle",
            "Supertrend", "ATR", "VWAP",
        ]
        for col in indicator_cols:
            if col in df.columns:
                df[col] = df[col].ffill().bfill()

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        signals = _generate_signals(latest, prev)

        return {
            "ticker": ticker,
            "price": float(latest["Close"]),
            "atr": round(float(latest["ATR"]), 4),
            "rsi": round(float(latest["RSI"]), 2),
            "bullish_count": sum(
                1 for s in signals.values() if s["signal"] == "BULLISH"
            ),
            "bearish_count": sum(
                1 for s in signals.values() if s["signal"] == "BEARISH"
            ),
            "signals": signals,
            "price_history": [],
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
        vwap = float(latest["VWAP"])
        bull_p = int(latest["Bull_Pulse"])
        bear_p = int(latest["Bear_Pulse"])

        # Pulse threshold (from config or default 5)
        pulse_thresh = getattr(config, "MYSTIC_PULSE_THRESHOLD", 5)

        return {
            "RSI": {
                "signal": (
                    "BULLISH" if rsi_val < config.RSI_OVERSOLD
                    else "BEARISH" if rsi_val > config.RSI_OVERBOUGHT
                    else "NEUTRAL"
                ),
                "reason": (
                    f"Oversold {rsi_val:.1f}" if rsi_val < config.RSI_OVERSOLD
                    else f"Overbought {rsi_val:.1f}" if rsi_val > config.RSI_OVERBOUGHT
                    else f"Neutral {rsi_val:.1f}"
                ),
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
            },
            "EMA Cross": {
                "signal": "BULLISH" if ema_f > ema_s else "BEARISH",
                "reason": f"{ema_f:.1f} vs {ema_s:.1f}",
            },
            "Supertrend": {
                "signal": "BULLISH" if st_bull else "BEARISH",
                "reason": f"{'Above' if st_bull else 'Below'} {float(latest['Supertrend']):.1f}",
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
            },
            "VWAP": {
                "signal": "BULLISH" if price > vwap else "BEARISH",
                "reason": f"Price > {vwap:.1f}" if price > vwap else f"Price < {vwap:.1f}",
            },
            "Mystic Pulse": {
                "signal": (
                    "BULLISH" if bull_p >= 5
                    else "BEARISH" if bear_p >= 5
                    else "NEUTRAL"
                ),
                "reason": f"Strength {int(bull_p if bull_p > bear_p else bear_p)}/5",
            },
            "Candle Patterns": {
                "signal": (
                    "BULLISH" if bool(latest.get("cdl_bullish", False))
                    else "BEARISH" if bool(latest.get("cdl_bearish", False))
                    else "NEUTRAL"
                ),
                "reason": f"Pattern: {latest.get('cdl_name', 'None')}",
            },
        }

    except Exception:
        return {}
