"""
Multi-Indicator Technical Analysis Engine.

Calculates a full suite of professional-grade indicators.
Exposes calculate_indicators_for_df and _generate_signals for backtesting.
"""

from datetime import datetime, timedelta
import hashlib
import pandas as pd
import numpy as np
import ta as ta_lib
import config as global_config
from user_config import active_evaluation_context, get_user_config
from swing_strategy import detect_swing_entry

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from broker_factory import get_alpaca_clients

wb_data_provider = None


def _get_wb_data_provider():
    global wb_data_provider
    if wb_data_provider is None:
        from webull_broker import WebullBroker
        wb_data_provider = WebullBroker()
    return wb_data_provider

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

def _timeframe_seconds(timeframe):
    tf = str(timeframe or "").upper().strip()
    if tf in ("1D", "1DAY", "DAY", "D"):
        return 86400
    import re
    match = re.search(r"\d+", tf)
    value = int(match.group()) if match else 5
    if "HOUR" in tf or tf.endswith("H"):
        return value * 3600
    if "SEC" in tf or tf.endswith("S"):
        return value
    if "MIN" in tf or tf.endswith("M"):
        return value * 60
    return 300

def _completed_candles_for_live_chart(df, timeframe, safety_delay_seconds=5):
    """Return only candles that are fully closed for live dashboard/bot views."""
    if df is None or df.empty or len(df) <= 1:
        return df

    seconds = _timeframe_seconds(timeframe)
    if seconds <= 0:
        return df.iloc[:-1]

    index = df.index
    if getattr(index, "tz", None) is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")

    now_utc = pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=safety_delay_seconds)
    completed_mask = (index + pd.Timedelta(seconds=seconds)) <= now_utc
    completed = df.loc[completed_mask]

    # Keep the previous behavior as a fallback if clocks/provider timestamps are unusual.
    if completed.empty:
        return df.iloc[:-1]
    return completed

def _volume_profile_levels(session_df, rows=48, value_area_pct=70.0):
    """Approximate volume-at-price from OHLCV bars for one completed session."""
    if session_df is None or session_df.empty:
        return (np.nan, np.nan, np.nan)

    try:
        rows = int(float(rows))
    except Exception:
        rows = 48
    rows = min(120, max(12, rows))

    try:
        value_area_pct = float(value_area_pct)
    except Exception:
        value_area_pct = 70.0
    value_area_pct = min(95.0, max(50.0, value_area_pct)) / 100.0

    low_price = float(pd.to_numeric(session_df["Low"], errors="coerce").min())
    high_price = float(pd.to_numeric(session_df["High"], errors="coerce").max())
    if not np.isfinite(low_price) or not np.isfinite(high_price) or high_price <= low_price:
        close_vals = pd.to_numeric(session_df["Close"], errors="coerce").dropna()
        if close_vals.empty:
            return (np.nan, np.nan, np.nan)
        price = float(close_vals.iloc[-1])
        return (price, price, price)

    edges = np.linspace(low_price, high_price, rows + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    profile = np.zeros(rows, dtype=float)

    for _, row in session_df.iterrows():
        lo = float(row.get("Low", np.nan))
        hi = float(row.get("High", np.nan))
        close = float(row.get("Close", np.nan))
        vol = float(row.get("Volume", 0.0) or 0.0)
        if not np.isfinite(vol) or vol <= 0:
            continue
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            price = close if np.isfinite(close) else (lo if np.isfinite(lo) else np.nan)
            if not np.isfinite(price):
                continue
            idx = int(np.clip(np.searchsorted(edges, price, side="right") - 1, 0, rows - 1))
            profile[idx] += vol
            continue

        candle_range = hi - lo
        overlaps = np.maximum(0.0, np.minimum(edges[1:], hi) - np.maximum(edges[:-1], lo))
        overlap_sum = overlaps.sum()
        if overlap_sum > 0:
            profile += vol * (overlaps / overlap_sum)
        else:
            idx = int(np.clip(np.searchsorted(edges, close, side="right") - 1, 0, rows - 1))
            profile[idx] += vol

    total_volume = profile.sum()
    if total_volume <= 0:
        return (np.nan, np.nan, np.nan)

    poc_idx = int(np.argmax(profile))
    target_volume = total_volume * value_area_pct
    included_volume = profile[poc_idx]
    low_idx = high_idx = poc_idx

    while included_volume < target_volume and (low_idx > 0 or high_idx < rows - 1):
        left_volume = profile[low_idx - 1] if low_idx > 0 else -1
        right_volume = profile[high_idx + 1] if high_idx < rows - 1 else -1
        if right_volume >= left_volume and high_idx < rows - 1:
            high_idx += 1
            included_volume += profile[high_idx]
        elif low_idx > 0:
            low_idx -= 1
            included_volume += profile[low_idx]
        else:
            break

    poc = float(centers[poc_idx])
    val = float(edges[low_idx])
    vah = float(edges[high_idx + 1])
    return (poc, vah, val)

def _is_crypto_symbol(symbol):
    clean = str(symbol or "").upper().replace("/", "").replace("-", "")
    return clean.endswith(("USD", "USDT", "USDC")) or clean in {"BTC", "ETH", "XRP", "DOGE", "LTC", "ADA", "SOL"}

def _df_row_to_swing_candle(index_value, row):
    try:
        ts = int(index_value.timestamp())
    except Exception:
        ts = index_value
    return {
        "time": ts,
        "open": float(row.get("Open", np.nan)),
        "high": float(row.get("High", np.nan)),
        "low": float(row.get("Low", np.nan)),
        "close": float(row.get("Close", np.nan)),
        "volume": float(row.get("Volume", 0.0) or 0.0),
    }

def _add_swing_point_columns(df, ticker, settings, param_getter, timeframe=None):
    """Populate rolling swing support/resistance columns without lookahead."""
    for col in (
        "Swing_Support_1", "Swing_Support_2", "Swing_Support_3", "Swing_Support_4", "Swing_Support_5",
        "Swing_Resistance_1", "Swing_Resistance_2", "Swing_Resistance_3", "Swing_Resistance_4", "Swing_Resistance_5",
        "Swing_Bullish", "Swing_Bearish",
    ):
        df[col] = False if col in {"Swing_Bullish", "Swing_Bearish"} else np.nan

    try:
        lookback_days = max(1, min(365, int(float(param_getter("SWING_POINTS_LOOKBACK_DAYS", 20)))))
    except Exception:
        lookback_days = 20
    try:
        level_count = max(1, min(5, int(float(param_getter("SWING_POINTS_LEVEL_COUNT", 1)))))
    except Exception:
        level_count = 1

    entry_mode = str(param_getter("SWING_POINTS_ENTRY_MODE", "bounce_retest") or "bounce_retest")
    if entry_mode not in {"bounce_retest", "breakout_retest"}:
        entry_mode = "bounce_retest"

    market_session = "Regular"
    if isinstance(settings, dict):
        market_session = settings.get("market_session") or settings.get("MARKET_SESSION") or market_session
    is_crypto = _is_crypto_symbol(ticker)
    if is_crypto:
        market_session = "Crypto24"

    history = []
    support_cols = [f"Swing_Support_{i}" for i in range(1, 6)]
    resistance_cols = [f"Swing_Resistance_{i}" for i in range(1, 6)]

    eval_context = active_evaluation_context.get()
    settings_source = str(settings.get("source", "") if isinstance(settings, dict) else "").lower()
    is_backtest_context = eval_context == "backtest" or settings_source == "backtest"
    calc_df = df

    if not is_backtest_context and len(df) > 600:
        seconds = max(60, _timeframe_seconds(timeframe))
        minutes = max(1.0, seconds / 60.0)
        rth_bars_per_day = max(1, int(round(390 / minutes)))
        live_window_bars = max(240, min(600, lookback_days * rth_bars_per_day))
        calc_df = df.iloc[-live_window_bars:]

    for idx, row in calc_df.iterrows():
        candle = _df_row_to_swing_candle(idx, row)
        if not all(np.isfinite(candle.get(k, np.nan)) for k in ("open", "high", "low", "close")):
            continue
        history.append(candle)
        decision = detect_swing_entry(
            history,
            current_price=candle["close"],
            entry_mode=entry_mode,
            lookback_days=lookback_days,
            market_session=market_session,
            is_crypto=is_crypto,
        )
        ctx = decision.get("context") or {}
        current_price = candle["close"]

        def _level_price(level):
            try:
                return float(level.get("price", np.nan))
            except Exception:
                return np.nan

        supports = sorted(
            [
                lvl for lvl in (ctx.get("support_levels") or [])
                if np.isfinite(_level_price(lvl)) and _level_price(lvl) <= current_price
            ],
            key=lambda lvl: (abs(_level_price(lvl) - current_price), -float(lvl.get("score", 0.0))),
        )
        resistances = sorted(
            [
                lvl for lvl in (ctx.get("resistance_levels") or [])
                if np.isfinite(_level_price(lvl)) and _level_price(lvl) >= current_price
            ],
            key=lambda lvl: (abs(_level_price(lvl) - current_price), -float(lvl.get("score", 0.0))),
        )
        for pos, level in enumerate(supports[:level_count]):
            try:
                df.at[idx, support_cols[pos]] = float(level.get("price"))
            except Exception:
                pass
        for pos, level in enumerate(resistances[:level_count]):
            try:
                df.at[idx, resistance_cols[pos]] = float(level.get("price"))
            except Exception:
                pass
        action = decision.get("action", "HOLD")
        if action == "BUY":
            df.at[idx, "Swing_Bullish"] = True
        elif action == "SELL":
            df.at[idx, "Swing_Bearish"] = True

# Alpaca clients (Dynamic access)
# ---------------------------------------------------------------------------
_client_cache = {}

def get_alpaca_clients():
    """Returns initialized Alpaca clients for the active user's credentials (cached per key pair)."""
    global _client_cache
    usr_cfg = get_user_config()
    api_key = getattr(usr_cfg, 'ALPACA_API_KEY', '').strip()
    secret_key = getattr(usr_cfg, 'ALPACA_SECRET_KEY', '').strip()
    
    if not api_key or api_key.startswith("your_alpaca"):
        return None, None
        
    cache_key = (api_key, secret_key)
    if cache_key not in _client_cache:
        print(f"[indicators] Initializing Alpaca clients for user key {api_key[:4]}***...")
        s_client = StockHistoricalDataClient(api_key, secret_key)
        c_client = CryptoHistoricalDataClient(api_key, secret_key)
        _client_cache[cache_key] = (s_client, c_client)
        
    return _client_cache[cache_key]

_data_cache = {}

def _coerce_param_value(value, fallback=None):
    try:
        if fallback is not None:
            return type(fallback)(value)
        return value
    except Exception:
        return value


def _bot_swing_param_alias(key, settings=None):
    """Map bot Swing entry/exit strategy settings onto the visual Swing indicator."""
    if not isinstance(settings, dict):
        return None

    swing_modes = {"swing_points", "hybrid_swing"}
    buy_mode = str(settings.get("buy_mode") or "").strip().lower()
    sell_mode = str(settings.get("sell_mode") or settings.get("options_exit_strategy") or "").strip().lower()

    if key == "SWING_POINTS_ENTRY_MODE" and buy_mode in swing_modes:
        return settings.get("swing_entry_mode") or settings.get("SWING_POINTS_ENTRY_MODE")

    if key == "SWING_POINTS_LOOKBACK_DAYS":
        if buy_mode in swing_modes:
            return (
                settings.get("swing_entry_lookback_days")
                or settings.get("swing_lookback_days")
                or settings.get("SWING_POINTS_LOOKBACK_DAYS")
            )
        if sell_mode in swing_modes:
            return (
                settings.get("swing_exit_lookback_days")
                or settings.get("swing_lookback_days")
                or settings.get("SWING_POINTS_LOOKBACK_DAYS")
            )

    return None


def _bot_swing_settings_source(settings=None):
    if not isinstance(settings, dict):
        return "indicator_settings"
    swing_modes = {"swing_points", "hybrid_swing"}
    buy_mode = str(settings.get("buy_mode") or "").strip().lower()
    sell_mode = str(settings.get("sell_mode") or settings.get("options_exit_strategy") or "").strip().lower()
    if buy_mode in swing_modes:
        return "bot_entry_strategy"
    if sell_mode in swing_modes:
        return "bot_exit_strategy"
    return "indicator_settings"


def _resolve_param(key, fallback=None, settings=None):
    if settings and isinstance(settings, dict):
        alias_val = _bot_swing_param_alias(key, settings)
        if alias_val is not None and alias_val != "":
            return _coerce_param_value(alias_val, fallback)

        candidates = [
            settings,
            settings.get("indicator_overrides") if isinstance(settings.get("indicator_overrides"), dict) else {},
            settings.get("indicator_parameters") if isinstance(settings.get("indicator_parameters"), dict) else {},
        ]
        for source in candidates:
            val = source.get(key)
            if val is not None and val != "":
                return _coerce_param_value(val, fallback)
    return getattr(get_user_config(), key, fallback)


def _add_fast_candlestick_columns(df):
    """Add the same candle-pattern columns used by the full indicator path."""
    body = (df["Close"] - df["Open"]).abs()
    range_ = df["High"] - df["Low"]
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)

    bull_eng = (df["Close"].shift(1) < df["Open"].shift(1)) & (df["Close"] > df["Open"]) & (df["Close"] >= df["Open"].shift(1)) & (df["Open"] <= df["Close"].shift(1))
    bear_eng = (df["Close"].shift(1) > df["Open"].shift(1)) & (df["Close"] < df["Open"]) & (df["Close"] <= df["Open"].shift(1)) & (df["Open"] >= df["Close"].shift(1))
    hammer_raw = (lower_wick > body * 2) & (upper_wick < body) & (range_ > 0)
    shooting_raw = (upper_wick > body * 2) & (lower_wick < body) & (range_ > 0)
    doji = (body <= (range_ * 0.1)) & (range_ > 0)
    bull_marubozu = (body > (range_ * 0.9)) & (df["Close"] > df["Open"])
    bear_marubozu = (body > (range_ * 0.9)) & (df["Close"] < df["Open"])
    morning_star = (df["Close"].shift(2) < df["Open"].shift(2)) & ((df["Open"].shift(1) - df["Close"].shift(1)).abs() < (df["Open"].shift(2) - df["Close"].shift(2)).abs() * 0.3) & (df["Close"] > df["Open"]) & (df["Close"] > (df["Open"].shift(2) + df["Close"].shift(2)) / 2)
    evening_star = (df["Close"].shift(2) > df["Open"].shift(2)) & ((df["Open"].shift(1) - df["Close"].shift(1)).abs() < (df["Close"].shift(2) - df["Open"].shift(2)).abs() * 0.3) & (df["Close"] < df["Open"]) & (df["Close"] < (df["Open"].shift(2) + df["Close"].shift(2)) / 2)
    uptrend = df["Close"] > df["Close"].shift(5)
    downtrend = df["Close"] < df["Close"].shift(5)
    hanging_man = hammer_raw & uptrend
    inverted_hammer = shooting_raw & downtrend
    hammer = hammer_raw & downtrend
    shooting_star = shooting_raw & uptrend
    piercing_line = (df["Close"].shift(1) < df["Open"].shift(1)) & (df["Open"] < df["Low"].shift(1)) & (df["Close"] > (df["Open"].shift(1) + df["Close"].shift(1)) / 2) & (df["Close"] < df["Open"].shift(1))
    dark_cloud = (df["Close"].shift(1) > df["Open"].shift(1)) & (df["Open"] > df["High"].shift(1)) & (df["Close"] < (df["Open"].shift(1) + df["Close"].shift(1)) / 2) & (df["Close"] > df["Open"].shift(1))
    three_white_soldiers = (df["Close"] > df["Open"]) & (df["Close"].shift(1) > df["Open"].shift(1)) & (df["Close"].shift(2) > df["Open"].shift(2)) & (df["Close"] > df["Close"].shift(1)) & (df["Close"].shift(1) > df["Close"].shift(2)) & (df["Open"] > df["Open"].shift(1)) & (df["Open"] < df["Close"].shift(1)) & (upper_wick < body * 0.2) & (df["High"].shift(1) - df[["Open", "Close"]].shift(1).max(axis=1) < (df["Close"].shift(1) - df["Open"].shift(1)) * 0.2)
    three_black_crows = (df["Close"] < df["Open"]) & (df["Close"].shift(1) < df["Open"].shift(1)) & (df["Close"].shift(2) < df["Open"].shift(2)) & (df["Close"] < df["Close"].shift(1)) & (df["Close"].shift(1) < df["Close"].shift(2)) & (df["Open"] < df["Open"].shift(1)) & (df["Open"] > df["Close"].shift(1)) & (lower_wick < body * 0.2) & (df[["Open", "Close"]].shift(1).min(axis=1) - df["Low"].shift(1) < (df["Open"].shift(1) - df["Close"].shift(1)) * 0.2)
    bull_harami = (df["Close"].shift(1) < df["Open"].shift(1)) & (df["Close"] > df["Open"]) & (df["Open"] > df["Close"].shift(1)) & (df["Close"] < df["Open"].shift(1))
    bear_harami = (df["Close"].shift(1) > df["Open"].shift(1)) & (df["Close"] < df["Open"]) & (df["Open"] < df["Close"].shift(1)) & (df["Close"] > df["Open"].shift(1))
    tweezer_bot = ((df["Low"] - df["Low"].shift(1)).abs() <= (range_ * 0.05)) & (df["Close"].shift(1) < df["Open"].shift(1)) & (df["Close"] > df["Open"])
    tweezer_top = ((df["High"] - df["High"].shift(1)).abs() <= (range_ * 0.05)) & (df["Close"].shift(1) > df["Open"].shift(1)) & (df["Close"] < df["Open"])
    three_inside_up = bull_harami.shift(1) & (df["Close"] > df["Close"].shift(1)) & (df["Close"] > df["Open"])
    three_inside_down = bear_harami.shift(1) & (df["Close"] < df["Close"].shift(1)) & (df["Close"] < df["Open"])
    three_outside_up = bull_eng.shift(1) & (df["Close"] > df["Close"].shift(1)) & (df["Close"] > df["Open"])
    three_outside_down = bear_eng.shift(1) & (df["Close"] < df["Close"].shift(1)) & (df["Close"] < df["Open"])
    dragonfly_doji = doji & (lower_wick > body * 3) & (upper_wick < body)
    gravestone_doji = doji & (upper_wick > body * 3) & (lower_wick < body)

    df["cdl_bullish"] = bull_eng | hammer | inverted_hammer | bull_marubozu | morning_star | piercing_line | three_white_soldiers | dragonfly_doji | bull_harami | tweezer_bot | three_inside_up | three_outside_up
    df["cdl_bearish"] = bear_eng | shooting_star | hanging_man | bear_marubozu | evening_star | dark_cloud | three_black_crows | gravestone_doji | bear_harami | tweezer_top | three_inside_down | three_outside_down
    df["cdl_name"] = "Neutral"
    df.loc[doji, "cdl_name"] = "Doji"
    df.loc[bull_marubozu, "cdl_name"] = "Bull Marubozu"
    df.loc[bear_marubozu, "cdl_name"] = "Bear Marubozu"
    df.loc[hammer, "cdl_name"] = "Hammer"
    df.loc[inverted_hammer, "cdl_name"] = "Inverted Hammer"
    df.loc[shooting_star, "cdl_name"] = "Shooting Star"
    df.loc[hanging_man, "cdl_name"] = "Hanging Man"
    df.loc[dragonfly_doji, "cdl_name"] = "Dragonfly Doji"
    df.loc[gravestone_doji, "cdl_name"] = "Gravestone Doji"
    df.loc[bull_harami, "cdl_name"] = "Bull Harami"
    df.loc[bear_harami, "cdl_name"] = "Bear Harami"
    df.loc[tweezer_bot, "cdl_name"] = "Tweezer Bottom"
    df.loc[tweezer_top, "cdl_name"] = "Tweezer Top"
    df.loc[bull_eng, "cdl_name"] = "Bull Engulfing"
    df.loc[bear_eng, "cdl_name"] = "Bear Engulfing"
    df.loc[piercing_line, "cdl_name"] = "Piercing Line"
    df.loc[dark_cloud, "cdl_name"] = "Dark Cloud Cover"
    df.loc[morning_star, "cdl_name"] = "Morning Star"
    df.loc[evening_star, "cdl_name"] = "Evening Star"
    df.loc[three_white_soldiers, "cdl_name"] = "3 White Soldiers"
    df.loc[three_black_crows, "cdl_name"] = "3 Black Crows"
    df.loc[three_inside_up, "cdl_name"] = "3 Inside Up"
    df.loc[three_inside_down, "cdl_name"] = "3 Inside Down"
    df.loc[three_outside_up, "cdl_name"] = "3 Outside Up"
    df.loc[three_outside_down, "cdl_name"] = "3 Outside Down"


def get_full_analysis(ticker, timeframe="5Min", data_source="alpaca", settings=None):
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
            live_count_map = {
                "30Sec": 1200,
                "1Min": 1000,
                "2Min": 900,
                "3Min": 800,
                "5Min": 800,
                "10Min": 700,
                "15Min": 700,
                "30Min": 700,
            }
            bars = _get_wb_data_provider().get_bars(ticker, timespan=wb_tf, count=live_count_map.get(timeframe, 1200))
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
            usr_cfg = get_user_config()
            api_key = getattr(usr_cfg, 'ALPACA_API_KEY', '').strip()
            secret_key = getattr(usr_cfg, 'ALPACA_SECRET_KEY', '').strip()
            credential_scope = hashlib.sha256(f"{api_key}:{secret_key}".encode()).hexdigest()[:16]
            cache_key = (credential_scope, ticker.upper(), timeframe.upper())

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
                lookback_days = 10
            elif tf_str == "10MIN":
                tf_obj = TimeFrame(10, TimeFrameUnit.Minute)
                lookback_days = 15
            elif tf_str == "15MIN":
                tf_obj = TimeFrame(15, TimeFrameUnit.Minute)
                lookback_days = 20
            elif tf_str == "30MIN":
                tf_obj = TimeFrame(30, TimeFrameUnit.Minute)
                lookback_days = 35
            elif tf_str in ["1HOUR", "60MIN"]:
                tf_obj = TimeFrame.Hour
                lookback_days = 100
            elif tf_str == "2HOUR":
                tf_obj = TimeFrame(2, TimeFrameUnit.Hour)
                lookback_days = 200
            elif tf_str == "4HOUR":
                tf_obj = TimeFrame(4, TimeFrameUnit.Hour)
                lookback_days = 400
            elif tf_str in ["1D", "1DAY"]:
                tf_obj = TimeFrame.Day
                lookback_days = 3650
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

        return calculate_indicators_for_df(df, timeframe, ticker, settings=settings)

    except Exception as exc:
        print(f"[indicators] Fetch error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Core engine: calculate_indicators_for_df
# ---------------------------------------------------------------------------
def calculate_indicators_for_df(df, timeframe="5Min", ticker="UNKNOWN", settings=None):
    """
    Compute every indicator on *df* and return a result dict.
    Designed to work identically for live analysis and backtesting.
    """
    def _param(key, fallback=None):
        return _resolve_param(key, fallback, settings=settings)

    try:
        enabled_indicators = None
        if isinstance(settings, dict) and isinstance(settings.get("enabled_indicators"), (list, tuple, set)):
            enabled_indicators = {str(v) for v in settings.get("enabled_indicators") if v}
        buy_mode = str((settings or {}).get("buy_mode", "") if isinstance(settings, dict) else "").lower()
        sell_mode = str((settings or {}).get("sell_mode", "") if isinstance(settings, dict) else "").lower()
        structural_modes = {"pivot_points", "hybrid_pivot", "high_low", "hybrid_high_low", "swing_points", "hybrid_swing"}
        needs_prior_session = (
            enabled_indicators is None
            or bool(enabled_indicators & {"Prior Day High/Low", "Pivot Points (S/R)", "Volume Profile"})
            or buy_mode in structural_modes
            or sell_mode in structural_modes
        )
        needs_swing_points = (
            enabled_indicators is None
            or "Swing Points" in enabled_indicators
            or buy_mode in {"swing_points", "hybrid_swing"}
            or sell_mode in {"swing_points", "hybrid_swing"}
        )

        # --- Column normalisation ------------------------------------------
        for col in ("Open", "High", "Low", "Close", "Volume"):
            lc = col.lower()
            if lc in df.columns and col not in df.columns:
                df[col] = df[lc]

        df = df.loc[:, ~df.columns.duplicated()]
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col].squeeze(), errors="coerce")

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        def _init_structural_columns():
            df["Daily_HighLow_High"] = np.nan
            df["Daily_HighLow_Low"] = np.nan
            df["Daily_HighLow_High_Trigger"] = np.nan
            df["Daily_HighLow_Low_Trigger"] = np.nan
            df["Daily_HighLow_Bullish"] = False
            df["Daily_HighLow_Bearish"] = False
            df["Volume_Profile_POC"] = np.nan
            df["Volume_Profile_VAH"] = np.nan
            df["Volume_Profile_VAL"] = np.nan
            df["Volume_Profile_Bullish"] = False
            df["Volume_Profile_Bearish"] = False
            for pivot_col in (
                "Pivot_P",
                "Pivot_R1", "Pivot_S1",
                "Pivot_R2", "Pivot_S2",
                "Pivot_R3", "Pivot_S3",
                "Pivot_R4", "Pivot_S4",
                "Pivot_R5", "Pivot_S5",
            ):
                df[pivot_col] = np.nan
            df["Pivot_Bullish"] = False
            df["Pivot_Bearish"] = False

        # Previous-session levels are only needed by structural indicators/strategies.
        try:
            if not needs_prior_session:
                _init_structural_columns()
            else:
                if getattr(df.index, "tz", None) is not None:
                    session_dates = df.index.tz_convert("America/Chicago").date
                else:
                    session_dates = df.index.date
                session_key = pd.Series(session_dates, index=df.index)
                daily_levels = df.groupby(session_key).agg({"High": "max", "Low": "min", "Close": "last"})
                prior_high_by_session = daily_levels["High"].shift(1)
                prior_low_by_session = daily_levels["Low"].shift(1)
                prior_close_by_session = daily_levels["Close"].shift(1)
                df["Daily_HighLow_High"] = session_key.map(prior_high_by_session).astype(float)
                df["Daily_HighLow_Low"] = session_key.map(prior_low_by_session).astype(float)
                df["Pivot_P"] = (df["Daily_HighLow_High"] + df["Daily_HighLow_Low"] + session_key.map(prior_close_by_session).astype(float)) / 3.0
                df["Pivot_R1"] = (2.0 * df["Pivot_P"]) - df["Daily_HighLow_Low"]
                df["Pivot_S1"] = (2.0 * df["Pivot_P"]) - df["Daily_HighLow_High"]
                prior_range = df["Daily_HighLow_High"] - df["Daily_HighLow_Low"]
                df["Pivot_R2"] = df["Pivot_P"] + prior_range
                df["Pivot_S2"] = df["Pivot_P"] - prior_range
                df["Pivot_R3"] = df["Daily_HighLow_High"] + (2.0 * (df["Pivot_P"] - df["Daily_HighLow_Low"]))
                df["Pivot_S3"] = df["Daily_HighLow_Low"] - (2.0 * (df["Daily_HighLow_High"] - df["Pivot_P"]))
                df["Pivot_R4"] = df["Pivot_R3"] + prior_range
                df["Pivot_S4"] = df["Pivot_S3"] - prior_range
                df["Pivot_R5"] = df["Pivot_R4"] + prior_range
                df["Pivot_S5"] = df["Pivot_S4"] - prior_range

                if enabled_indicators is None or "Volume Profile" in enabled_indicators:
                    vp_rows = _param("VOLUME_PROFILE_ROWS", 48)
                    vp_value_area_pct = _param("VOLUME_PROFILE_VALUE_AREA_PCT", 70.0)
                    profile_by_session = {}
                    for session_date, session_df in df.groupby(session_key):
                        profile_by_session[session_date] = _volume_profile_levels(
                            session_df,
                            rows=vp_rows,
                            value_area_pct=vp_value_area_pct,
                        )
                    profile_df = pd.DataFrame.from_dict(
                        profile_by_session,
                        orient="index",
                        columns=["Volume_Profile_POC", "Volume_Profile_VAH", "Volume_Profile_VAL"],
                    ).sort_index()
                    prior_profile_df = profile_df.shift(1)
                    df["Volume_Profile_POC"] = session_key.map(prior_profile_df["Volume_Profile_POC"]).astype(float)
                    df["Volume_Profile_VAH"] = session_key.map(prior_profile_df["Volume_Profile_VAH"]).astype(float)
                    df["Volume_Profile_VAL"] = session_key.map(prior_profile_df["Volume_Profile_VAL"]).astype(float)
                else:
                    df["Volume_Profile_POC"] = np.nan
                    df["Volume_Profile_VAH"] = np.nan
                    df["Volume_Profile_VAL"] = np.nan

                hl_buffer = max(0.0, float(_param("DAILY_HIGH_LOW_BUFFER_PCT", 0.0))) / 100.0
                high_trigger = df["Daily_HighLow_High"] * (1.0 + hl_buffer)
                low_trigger = df["Daily_HighLow_Low"] * (1.0 - hl_buffer)
                df["Daily_HighLow_High_Trigger"] = high_trigger if hl_buffer > 0 else np.nan
                df["Daily_HighLow_Low_Trigger"] = low_trigger if hl_buffer > 0 else np.nan
                prev_close_series = close.shift(1)
                df["Daily_HighLow_Bullish"] = ((close > high_trigger) & (prev_close_series <= high_trigger)).fillna(False)
                df["Daily_HighLow_Bearish"] = ((close < low_trigger) & (prev_close_series >= low_trigger)).fillna(False)
                pivot_tolerance_pct = max(0.0, float(_param("PIVOT_TOLERANCE_PCT", 0.0))) / 100.0
                pivot_tolerance = close * pivot_tolerance_pct
                df["Pivot_Bullish"] = (
                    ((low <= df["Pivot_S1"] + pivot_tolerance) & (close > df["Pivot_S1"]))
                    | ((close > df["Pivot_P"]) & (prev_close_series <= df["Pivot_P"]))
                ).fillna(False)
                df["Pivot_Bearish"] = (
                    ((high >= df["Pivot_R1"] - pivot_tolerance) & (close < df["Pivot_R1"]))
                    | ((close < df["Pivot_P"]) & (prev_close_series >= df["Pivot_P"]))
                ).fillna(False)
                if enabled_indicators is None or "Volume Profile" in enabled_indicators:
                    vp_tolerance_pct = max(0.0, float(_param("VOLUME_PROFILE_TOLERANCE_PCT", 0.15))) / 100.0
                    vp_tol = close * vp_tolerance_pct
                    vp_confirm = max(1, min(5, int(float(_param("VOLUME_PROFILE_CONFIRMATION_CANDLES", 1)))))
                    vp_accepted_above = (
                        (close > df["Volume_Profile_VAH"] + vp_tol)
                        .rolling(vp_confirm, min_periods=vp_confirm)
                        .sum()
                        .ge(vp_confirm)
                    )
                    vp_accepted_below = (
                        (close < df["Volume_Profile_VAL"] - vp_tol)
                        .rolling(vp_confirm, min_periods=vp_confirm)
                        .sum()
                        .ge(vp_confirm)
                    )
                    df["Volume_Profile_Bullish"] = (
                        ((close > df["Volume_Profile_POC"]) & (prev_close_series <= df["Volume_Profile_POC"]))
                        | ((low <= df["Volume_Profile_VAL"] + vp_tol) & (close > df["Volume_Profile_VAL"]))
                        | vp_accepted_above
                    ).fillna(False)
                    df["Volume_Profile_Bearish"] = (
                        ((close < df["Volume_Profile_POC"]) & (prev_close_series >= df["Volume_Profile_POC"]))
                        | ((high >= df["Volume_Profile_VAH"] - vp_tol) & (close < df["Volume_Profile_VAH"]))
                        | vp_accepted_below
                    ).fillna(False)
                else:
                    df["Volume_Profile_Bullish"] = False
                    df["Volume_Profile_Bearish"] = False
        except Exception as hl_exc:
            print(f"[indicators] Prior-session levels error: {hl_exc}")
            _init_structural_columns()

        if needs_swing_points:
            _add_swing_point_columns(df, ticker, settings, _param, timeframe=timeframe)

        # --- RSI -----------------------------------------------------------
        df["RSI"] = ta_lib.momentum.rsi(close, window=int(_param("RSI_PERIOD", 14)))

        # --- EMA -----------------------------------------------------------
        df["EMA_Fast"] = ta_lib.trend.ema_indicator(close, window=int(_param("EMA_FAST", 9)))
        df["EMA_Slow"] = ta_lib.trend.ema_indicator(close, window=int(_param("EMA_SLOW", 21)))

        # --- SMA -----------------------------------------------------------
        df["SMA"] = ta_lib.trend.sma_indicator(close, window=int(_param("SMA_PERIOD", 200)))

        # --- MACD ----------------------------------------------------------
        macd_ind = ta_lib.trend.MACD(
            close,
            window_fast=int(_param("MACD_FAST", 12)),
            window_slow=int(_param("MACD_SLOW", 26)),
            window_sign=int(_param("MACD_SIGNAL", 9)),
        )
        df["MACD_Line"] = macd_ind.macd()
        df["MACD_Signal"] = macd_ind.macd_signal()
        df["MACD_Hist"] = macd_ind.macd_diff()

        # --- Bollinger Bands -----------------------------------------------
        boll_ind = ta_lib.volatility.BollingerBands(
            close, window=int(_param("BOLL_PERIOD", 20)), window_dev=float(_param("BOLL_STD_DEV", 2.0))
        )
        df["BOLL_Upper"] = boll_ind.bollinger_hband()
        df["BOLL_Lower"] = boll_ind.bollinger_lband()
        df["BOLL_Middle"] = boll_ind.bollinger_mavg()

        simple_fast_path = (
            enabled_indicators is not None
            and enabled_indicators.issubset({"RSI", "Bollinger", "VWAP"})
            and buy_mode in {"", "indicator"}
            and sell_mode in {"", "sltp", "percentage", "custom_sltp", "custom_underlying_sltp"}
        )
        if simple_fast_path:
            # Keep the common backtest path lean: RSI/Bollinger/VWAP signals plus ATR exits.
            try:
                typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
                pv = typical_price * df["Volume"]
                cpv = pv.cumsum()
                cvol = df["Volume"].cumsum()
                vol_ma = df["Volume"].rolling(window=int(float(_param("VOL_MA_PERIOD", 20))), min_periods=1).mean()
                spike_condition = df["Volume"] > (vol_ma * float(_param("VOL_SPIKE_MULTIPLIER", 1.5)))
                int_indices = pd.Series(range(len(df)), index=df.index)
                current_anchor_idx = int_indices.where(spike_condition).ffill()

                date_key = pd.Series(df.index.date, index=df.index)
                daily_pv = pv.groupby(date_key).cumsum()
                daily_vol = df["Volume"].groupby(date_key).cumsum()
                daily_vwap = (daily_pv / daily_vol.replace(0, np.nan)).fillna(close)

                cpv_arr = cpv.to_numpy()
                cvol_arr = cvol.to_numpy()
                anchor_arr = current_anchor_idx.to_numpy()
                daily_vwap_arr = daily_vwap.to_numpy()
                close_arr_fast = close.to_numpy()
                avwap_vals = np.empty(len(df))
                for t_idx, a_idx in enumerate(anchor_arr):
                    if pd.isna(a_idx):
                        avwap_vals[t_idx] = daily_vwap_arr[t_idx]
                    else:
                        a_idx = int(a_idx)
                        sum_pv = cpv_arr[t_idx] - (cpv_arr[a_idx - 1] if a_idx > 0 else 0)
                        sum_vol = cvol_arr[t_idx] - (cvol_arr[a_idx - 1] if a_idx > 0 else 0)
                        avwap_vals[t_idx] = sum_pv / sum_vol if sum_vol > 0 else close_arr_fast[t_idx]
                df["VWAP"] = avwap_vals
                df["Anchor_Date"] = current_anchor_idx.map(lambda idx: df.index[int(idx)] if not pd.isna(idx) else pd.NaT)
            except Exception as e:
                print(f"[indicators] Fast VWAP calc error: {e}")
                df["VWAP"] = close.copy()
                df["Anchor_Date"] = pd.NaT

            tr1_fast = high - low
            tr2_fast = (high - close.shift(1)).abs()
            tr3_fast = (low - close.shift(1)).abs()
            tr_fast = pd.concat([tr1_fast, tr2_fast, tr3_fast], axis=1).max(axis=1)
            atr_period = int(float(_param("ATR_PERIOD", 14)))
            df["ATR"] = tr_fast.ewm(alpha=1 / atr_period, min_periods=atr_period, adjust=False).mean()
            _add_fast_candlestick_columns(df)

            for col in ["RSI", "BOLL_Upper", "BOLL_Lower", "BOLL_Middle", "VWAP", "ATR"]:
                df[col] = df[col].ffill().bfill()

            from user_config import active_evaluation_context
            eval_context = active_evaluation_context.get()
            if eval_context in ("dashboard", "bots"):
                chart_df = _completed_candles_for_live_chart(df, timeframe)
                latest = chart_df.iloc[-1]
                prev = chart_df.iloc[-2] if len(chart_df) > 1 else latest
                recent_bars = chart_df.tail(5)
            else:
                chart_df = df
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest
                recent_bars = df.tail(5)

            if eval_context != "backtest" and len(chart_df) > 0:
                mask_not_last = df.index != chart_df.index[-1]
                df.loc[mask_not_last, "cdl_name"] = "Neutral"
                df.loc[mask_not_last, ["cdl_bullish", "cdl_bearish"]] = False

            signals = _generate_signals(latest, prev, settings=settings, recent_bars=recent_bars, chart_latest=latest)

            def _safe_fast(val):
                try:
                    v = float(val)
                    return None if (v != v or v == float('inf') or v == float('-inf')) else v
                except Exception:
                    return None

            price_history = []
            if eval_context in ("dashboard", "bots"):
                price_history = [
                    {
                        "time": int(t.timestamp()),
                        "open": _safe_fast(row["Open"]),
                        "high": _safe_fast(row["High"]),
                        "low": _safe_fast(row["Low"]),
                        "close": _safe_fast(row["Close"]),
                        "volume": _safe_fast(row["Volume"]),
                        "vwap": _safe_fast(row.get("VWAP")),
                        "boll_upper": _safe_fast(row.get("BOLL_Upper")),
                        "boll_middle": _safe_fast(row.get("BOLL_Middle")),
                        "boll_lower": _safe_fast(row.get("BOLL_Lower")),
                        "rsi": _safe_fast(row.get("RSI")),
                        "cdl_name": str(row.get("cdl_name", "Neutral")),
                        "cdl_bullish": bool(row.get("cdl_bullish", False)),
                        "cdl_bearish": bool(row.get("cdl_bearish", False)),
                    } for t, row in chart_df.iterrows()
                ]
            return {
                "ticker": ticker,
                "price": float(latest["Close"]),
                "atr": round(float(latest["ATR"]), 8),
                "rsi": round(float(latest["RSI"]), 2),
                "vwap": float(latest["VWAP"]),
                "bullish_count": sum(s.get("weight", 1) for s in signals.values() if s["signal"] == "BULLISH"),
                "bearish_count": sum(s.get("weight", 1) for s in signals.values() if s["signal"] == "BEARISH"),
                "signals": signals,
                "price_history": price_history,
                "df": df,
            }

        # --- Supertrend (Webull/TradingView Compatible Wilder's Smoothing) ---
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Wilder's Smoothing (Exponential Moving Average with alpha = 1/N)
        st_period = int(float(_param("SUPERTREND_PERIOD", 10)))
        st_atr = tr.ewm(alpha=1/st_period, min_periods=st_period, adjust=False).mean()
        
        hl2 = (high + low) / 2.0
        st_mult = float(_param("SUPERTREND_MULTIPLIER", 3.0))
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
            vol_ma = df["Volume"].rolling(window=int(float(_param('VOL_MA_PERIOD', 20))), min_periods=1).mean()
            spike_condition = df["Volume"] > (vol_ma * float(_param('VOL_SPIKE_MULTIPLIER', 1.5)))
            
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
        atr_period = int(float(_param("ATR_PERIOD", 14)))
        df["ATR"] = tr.ewm(alpha=1/atr_period, min_periods=atr_period, adjust=False).mean()

        # --- ADX (Standard Welles Wilder ADX / DI+ / DI-) -------------------
        adx_ind = ta_lib.trend.ADXIndicator(
            high=high, low=low, close=close, window=int(_param("ADX_PERIOD", 14))
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

        # 7. Signal Logic (Threshold based on MYSTIC_PULSE_THRESHOLD parameter)
        mystic_thresh = float(_param("MYSTIC_PULSE_THRESHOLD", 5))
        df["mp_buy_trigger"] = (df["Bull_Pulse"] >= mystic_thresh) & (df["Bull_Pulse"].shift(1) < mystic_thresh)
        df["mp_sell_trigger"] = (df["Bear_Pulse"] >= mystic_thresh) & (df["Bear_Pulse"].shift(1) < mystic_thresh)

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

        # Harami (Inside Bar Reversal)
        bull_harami = (df["Close"].shift(1) < df["Open"].shift(1)) & \
                      (df["Close"] > df["Open"]) & \
                      (df["Open"] > df["Close"].shift(1)) & \
                      (df["Close"] < df["Open"].shift(1))
        bear_harami = (df["Close"].shift(1) > df["Open"].shift(1)) & \
                      (df["Close"] < df["Open"]) & \
                      (df["Open"] < df["Close"].shift(1)) & \
                      (df["Close"] > df["Open"].shift(1))

        # Tweezer Bottom / Top
        tweezer_bot = ((df["Low"] - df["Low"].shift(1)).abs() <= (range_ * 0.05)) & \
                      (df["Close"].shift(1) < df["Open"].shift(1)) & (df["Close"] > df["Open"])
        tweezer_top = ((df["High"] - df["High"].shift(1)).abs() <= (range_ * 0.05)) & \
                      (df["Close"].shift(1) > df["Open"].shift(1)) & (df["Close"] < df["Open"])

        # Three Inside Up / Down (Harami + Breakout confirmation)
        three_inside_up = bull_harami.shift(1) & (df["Close"] > df["Close"].shift(1)) & (df["Close"] > df["Open"])
        three_inside_down = bear_harami.shift(1) & (df["Close"] < df["Close"].shift(1)) & (df["Close"] < df["Open"])

        # Three Outside Up / Down (Engulfing + Breakout confirmation)
        three_outside_up = bull_eng.shift(1) & (df["Close"] > df["Close"].shift(1)) & (df["Close"] > df["Open"])
        three_outside_down = bear_eng.shift(1) & (df["Close"] < df["Close"].shift(1)) & (df["Close"] < df["Open"])

        # Specialized Dojis
        dragonfly_doji = doji & (lower_wick > body * 3) & (upper_wick < body)
        gravestone_doji = doji & (upper_wick > body * 3) & (lower_wick < body)

        df["cdl_bullish"] = bull_eng | hammer | inverted_hammer | bull_marubozu | morning_star | piercing_line | three_white_soldiers | dragonfly_doji | bull_harami | tweezer_bot | three_inside_up | three_outside_up
        df["cdl_bearish"] = bear_eng | shooting_star | hanging_man | bear_marubozu | evening_star | dark_cloud | three_black_crows | gravestone_doji | bear_harami | tweezer_top | three_inside_down | three_outside_down
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
        df.loc[bull_harami, "cdl_name"] = "Bull Harami"
        df.loc[bear_harami, "cdl_name"] = "Bear Harami"
        df.loc[tweezer_bot, "cdl_name"] = "Tweezer Bottom"
        df.loc[tweezer_top, "cdl_name"] = "Tweezer Top"
        df.loc[bull_eng, "cdl_name"] = "Bull Engulfing"
        df.loc[bear_eng, "cdl_name"] = "Bear Engulfing"
        df.loc[piercing_line, "cdl_name"] = "Piercing Line"
        df.loc[dark_cloud, "cdl_name"] = "Dark Cloud Cover"
        df.loc[morning_star, "cdl_name"] = "Morning Star"
        df.loc[evening_star, "cdl_name"] = "Evening Star"
        df.loc[three_white_soldiers, "cdl_name"] = "3 White Soldiers"
        df.loc[three_black_crows, "cdl_name"] = "3 Black Crows"
        df.loc[three_inside_up, "cdl_name"] = "3 Inside Up"
        df.loc[three_inside_down, "cdl_name"] = "3 Inside Down"
        df.loc[three_outside_up, "cdl_name"] = "3 Outside Up"
        df.loc[three_outside_down, "cdl_name"] = "3 Outside Down"

        # ═══════════════════════════════════════════════════════════════════
        # PREMIUM INDICATORS — BotBulls1, BotBulls2, BotBulls3
        # ═══════════════════════════════════════════════════════════════════

        # --- BotBulls1: Premium Oscillator WaveTrend + Institutional MFI --------
        try:
            _n1 = max(5, min(50, int(float(_param("BOTBULLS1_WT_CHANNEL", 10)))))   # Channel Length
            _n2 = max(5, min(100, int(float(_param("BOTBULLS1_WT_AVERAGE", 21)))))  # Average Length
            ap = (high + low + close) / 3  # hlc3
            esa = ta_lib.trend.ema_indicator(ap, window=_n1)
            d_wt = ta_lib.trend.ema_indicator((ap - esa).abs(), window=_n1)
            # Avoid division by zero
            d_wt_safe = d_wt.replace(0, np.nan)
            ci = (ap - esa) / (0.015 * d_wt_safe)
            ci = ci.fillna(0)
            df["WT1"] = ta_lib.trend.ema_indicator(ci, window=_n2)
            df["WT2"] = df["WT1"].rolling(window=4).mean()

            # MFI (14-period)
            mfi_ind = ta_lib.volume.MFIIndicator(
                high=high, low=low, close=close, volume=volume, window=14
            )
            df["MFI_14"] = mfi_ind.money_flow_index()

            # Signal: confirmed WaveTrend reversal from an extreme pocket.
            # BUY waits for money flow to recover; SELL waits for flow to weaken.
            wt1_arr = df["WT1"].values
            wt2_arr = df["WT2"].values
            mfi_arr = df["MFI_14"].values
            close_arr = close.to_numpy()
            open_arr = df["Open"].to_numpy()
            price_confirm_ema_period = max(5, min(50, int(float(_param("BOTBULLS1_PRICE_CONFIRM_EMA", 9)))))
            price_confirm_ema = ta_lib.trend.ema_indicator(close, window=price_confirm_ema_period).to_numpy()
            ba1_buy = np.zeros(len(df), dtype=bool)
            ba1_sell = np.zeros(len(df), dtype=bool)
            ba1_bullish = np.zeros(len(df), dtype=bool)
            ba1_bearish = np.zeros(len(df), dtype=bool)
            wt_oversold = min(-10.0, max(-100.0, float(_param("BOTBULLS1_WT_OVERSOLD", -55))))
            wt_overbought = max(10.0, min(100.0, float(_param("BOTBULLS1_WT_OVERBOUGHT", 55))))
            mfi_buy_reclaim = min(60.0, max(5.0, float(_param("BOTBULLS1_MFI_BUY_RECLAIM", 35))))
            mfi_sell_reject = max(40.0, min(95.0, float(_param("BOTBULLS1_MFI_SELL_REJECT", 65))))
            confirm_lookback = max(1, min(10, int(float(_param("BOTBULLS1_CONFIRM_LOOKBACK", 3)))))
            require_mfi_confirm = bool(int(float(_param("BOTBULLS1_REQUIRE_MFI_CONFIRM", 1))))
            require_candle_confirm = bool(int(float(_param("BOTBULLS1_REQUIRE_CANDLE_CONFIRM", 1))))
            require_price_confirm = bool(int(float(_param("BOTBULLS1_REQUIRE_PRICE_CONFIRM", 1))))
            price_confirm_window = max(0, min(10, int(float(_param("BOTBULLS1_PRICE_CONFIRM_LOOKBACK", 4)))))
            pending_buy_idx = None
            pending_sell_idx = None
            for i in range(1, len(df)):
                if not (np.isnan(wt1_arr[i]) or np.isnan(wt2_arr[i]) or np.isnan(wt1_arr[i-1]) or np.isnan(wt2_arr[i-1])):
                    cross_up = wt1_arr[i] > wt2_arr[i] and wt1_arr[i-1] <= wt2_arr[i-1]
                    cross_dn = wt1_arr[i] < wt2_arr[i] and wt1_arr[i-1] >= wt2_arr[i-1]
                    mfi_v = mfi_arr[i] if not np.isnan(mfi_arr[i]) else 50
                    mfi_prev = mfi_arr[i - 1] if not np.isnan(mfi_arr[i - 1]) else mfi_v
                    start = max(0, i - confirm_lookback + 1)
                    recent_wt = wt1_arr[start:i + 1]
                    recent_low = np.nanmin(recent_wt) if len(recent_wt) else wt1_arr[i]
                    recent_high = np.nanmax(recent_wt) if len(recent_wt) else wt1_arr[i]
                    mfi_buy_ok = mfi_v >= mfi_buy_reclaim and mfi_v > mfi_prev
                    mfi_sell_ok = mfi_v <= mfi_sell_reject and mfi_v < mfi_prev
                    bull_candle = close_arr[i] > close_arr[i - 1] and close_arr[i] >= open_arr[i]
                    bear_candle = close_arr[i] < close_arr[i - 1] and close_arr[i] <= open_arr[i]
                    ema_v = price_confirm_ema[i] if not np.isnan(price_confirm_ema[i]) else close_arr[i]
                    ema_prev = price_confirm_ema[i - 1] if not np.isnan(price_confirm_ema[i - 1]) else ema_v
                    price_buy_ok = close_arr[i] >= ema_v and ema_v >= ema_prev
                    price_sell_ok = close_arr[i] <= ema_v and ema_v <= ema_prev
                    buy_setup = (
                        cross_up
                        and recent_low <= wt_oversold
                        and (mfi_buy_ok or not require_mfi_confirm)
                        and (bull_candle or not require_candle_confirm)
                    )
                    sell_setup = (
                        cross_dn
                        and recent_high >= wt_overbought
                        and (mfi_sell_ok or not require_mfi_confirm)
                        and (bear_candle or not require_candle_confirm)
                    )

                    if buy_setup:
                        pending_buy_idx = i
                        pending_sell_idx = None
                    elif sell_setup:
                        pending_sell_idx = i
                        pending_buy_idx = None

                    if require_price_confirm:
                        buy_pending_valid = pending_buy_idx is not None and (i - pending_buy_idx) <= price_confirm_window
                        sell_pending_valid = pending_sell_idx is not None and (i - pending_sell_idx) <= price_confirm_window
                        buy_confirmed = buy_pending_valid and price_buy_ok and (bull_candle or not require_candle_confirm)
                        sell_confirmed = sell_pending_valid and price_sell_ok and (bear_candle or not require_candle_confirm)
                    else:
                        buy_confirmed = buy_setup
                        sell_confirmed = sell_setup

                    if buy_confirmed:
                        ba1_buy[i] = True
                        pending_buy_idx = None
                        pending_sell_idx = None
                    elif sell_confirmed:
                        ba1_sell[i] = True
                        pending_buy_idx = None
                        pending_sell_idx = None

                    if pending_buy_idx is not None and (i - pending_buy_idx) > price_confirm_window:
                        pending_buy_idx = None
                    if pending_sell_idx is not None and (i - pending_sell_idx) > price_confirm_window:
                        pending_sell_idx = None

            # Continuous state used by confluence/bots/backtests.
            # This state is driven only by confirmed chart markers:
            # after BB1 BUY -> bullish until the next BB1 SELL, and vice versa.
            bb1_state = 0
            for i in range(len(df)):
                if ba1_buy[i]:
                    bb1_state = 1
                elif ba1_sell[i]:
                    bb1_state = -1
                ba1_bullish[i] = bb1_state == 1
                ba1_bearish[i] = bb1_state == -1
            df["BA1_Buy"] = ba1_buy
            df["BA1_Sell"] = ba1_sell
            df["BA1_Bullish"] = ba1_bullish
            df["BA1_Bearish"] = ba1_bearish
        except Exception as e:
            print(f"[indicators] BotBulls1 error: {e}")
            df["WT1"] = 0.0
            df["WT2"] = 0.0
            df["MFI_14"] = 50.0
            df["BA1_Buy"] = False
            df["BA1_Sell"] = False
            df["BA1_Bullish"] = False
            df["BA1_Bearish"] = False

        # --- BotBulls2: UT (Universal Trailing Stop) + Trend Tracer + Reversal Zones ---
        try:
            _lx_atr_len = 14
            _lx_atr_mult = float(_param("BOTBULLS2_ATR_MULT", 2.0))
            # Reuse existing ATR or compute dedicated one
            lx_atr = df["ATR"].values
            close_arr_lx = close.to_numpy()
            n_lx = len(df)

            # Component 1: Smart Trail (ATR Trailing Stop)
            lx_trail = np.full(n_lx, np.nan)
            lx_trend = np.ones(n_lx, dtype=bool)  # True = UP
            # Init first valid bar
            first_valid = 0
            for idx_lx in range(n_lx):
                if not np.isnan(lx_atr[idx_lx]):
                    first_valid = idx_lx
                    break
            lx_trail[first_valid] = close_arr_lx[first_valid] - lx_atr[first_valid] * _lx_atr_mult
            lx_trend[first_valid] = True

            for i in range(first_valid + 1, n_lx):
                nLoss = lx_atr[i] * _lx_atr_mult if not np.isnan(lx_atr[i]) else 0
                if close_arr_lx[i] > lx_trail[i-1] and close_arr_lx[i-1] > lx_trail[i-1]:
                    lx_trail[i] = max(lx_trail[i-1], close_arr_lx[i] - nLoss)
                    lx_trend[i] = True
                elif close_arr_lx[i] < lx_trail[i-1] and close_arr_lx[i-1] < lx_trail[i-1]:
                    lx_trail[i] = min(lx_trail[i-1], close_arr_lx[i] + nLoss)
                    lx_trend[i] = False
                elif close_arr_lx[i] > lx_trail[i-1]:
                    lx_trail[i] = close_arr_lx[i] - nLoss
                    lx_trend[i] = True
                else:
                    lx_trail[i] = close_arr_lx[i] + nLoss
                    lx_trend[i] = False

            df["LX_SmartTrail"] = lx_trail
            df["LX_SmartTrend"] = lx_trend

            # Component 2: Trend Tracer (Hull-inspired double-smooth EMA)
            _tt_period = int(_param("BOTBULLS2_TREND_TRACER_PERIOD", 50))
            ema_full = ta_lib.trend.ema_indicator(close, window=_tt_period)
            ema_half = ta_lib.trend.ema_indicator(close, window=max(_tt_period // 2, 2))
            df["LX_TrendTracer"] = 2 * ema_half - ema_full

            # Component 3: Reversal Zones (±2σ from 50-SMA)
            _rz_period = int(_param("BOTBULLS2_REVERSAL_ZONE_PERIOD", 50))
            rz_mean = close.rolling(window=_rz_period).mean()
            rz_std = close.rolling(window=_rz_period).std()
            df["LX_RZ_Upper"] = rz_mean + 2 * rz_std
            df["LX_RZ_Lower"] = rz_mean - 2 * rz_std

            # Component 4: Signal Classification (Score 0-4)
            rsi_arr = df["RSI"].values
            tt_arr = df["LX_TrendTracer"].values
            rz_upper_arr = df["LX_RZ_Upper"].values
            rz_lower_arr = df["LX_RZ_Lower"].values
            lx_score = np.zeros(n_lx, dtype=int)

            for i in range(n_lx):
                s = 0
                # +1: Smart Trail direction is UP
                if lx_trend[i]:
                    s += 1
                # +1: Price above Trend Tracer
                if not np.isnan(tt_arr[i]) and close_arr_lx[i] > tt_arr[i]:
                    s += 1
                # +1: Not in bearish reversal zone (overbought)
                if not np.isnan(rz_upper_arr[i]) and close_arr_lx[i] < rz_upper_arr[i]:
                    s += 1
                # +1: RSI in healthy bullish range (40-70)
                if not np.isnan(rsi_arr[i]) and 40 < rsi_arr[i] < 70:
                    s += 1
                lx_score[i] = s
            df["LX_Score"] = lx_score

        except Exception as e:
            print(f"[indicators] BotBulls2 error: {e}")
            df["LX_SmartTrail"] = close.copy()
            df["LX_SmartTrend"] = True
            df["LX_TrendTracer"] = close.copy()
            df["LX_RZ_Upper"] = close.copy()
            df["LX_RZ_Lower"] = close.copy()
            df["LX_Score"] = 0

        # --- BotBulls3: Heikin-Ashi + UT Bot ATR Trailing Stop ---------------
        try:
            _ut_key = float(_param("BOTBULLS3_ATR_MULT", 1.0))       # Sensitivity multiplier (QuantNomad default)
            _ut_atr_period = int(_param("BOTBULLS3_ATR_PERIOD", 10)) # ATR lookback (QuantNomad default)
            open_arr = df["Open"].to_numpy()
            high_arr = high.to_numpy()
            low_arr = low.to_numpy()
            close_arr_ut = close.to_numpy()
            n_ut = len(df)

            # 1. Heikin-Ashi candles
            ha_close = (open_arr + high_arr + low_arr + close_arr_ut) / 4
            ha_open = np.empty(n_ut)
            ha_open[0] = (open_arr[0] + close_arr_ut[0]) / 2
            for i in range(1, n_ut):
                ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2
            ha_high = np.maximum(high_arr, np.maximum(ha_open, ha_close))
            ha_low = np.minimum(low_arr, np.minimum(ha_open, ha_close))

            # 2. ATR on Heikin-Ashi candles (Wilder's)
            ha_tr = np.zeros(n_ut)
            ha_tr[0] = ha_high[0] - ha_low[0]
            for i in range(1, n_ut):
                ha_tr[i] = max(
                    ha_high[i] - ha_low[i],
                    abs(ha_high[i] - ha_close[i-1]),
                    abs(ha_low[i] - ha_close[i-1])
                )
            # Wilder's smoothing for ATR
            ha_atr = np.zeros(n_ut)
            ha_atr[:_ut_atr_period] = np.nan
            if n_ut >= _ut_atr_period:
                ha_atr[_ut_atr_period - 1] = np.mean(ha_tr[:_ut_atr_period])
                for i in range(_ut_atr_period, n_ut):
                    ha_atr[i] = (ha_atr[i-1] * (_ut_atr_period - 1) + ha_tr[i]) / _ut_atr_period

            nLoss_ut = ha_atr * _ut_key

            # 3. Recursive trailing stop on HA Close
            ut_trail = np.full(n_ut, np.nan)
            ut_above = np.ones(n_ut, dtype=bool)
            # Find first valid bar
            ut_start = _ut_atr_period - 1 if n_ut >= _ut_atr_period else 0
            if not np.isnan(nLoss_ut[ut_start]):
                ut_trail[ut_start] = ha_close[ut_start] - nLoss_ut[ut_start]
                ut_above[ut_start] = True

            for i in range(ut_start + 1, n_ut):
                nl = nLoss_ut[i] if not np.isnan(nLoss_ut[i]) else 0
                if ha_close[i] > ut_trail[i-1] and ha_close[i-1] > ut_trail[i-1]:
                    ut_trail[i] = max(ut_trail[i-1], ha_close[i] - nl)
                    ut_above[i] = True
                elif ha_close[i] < ut_trail[i-1] and ha_close[i-1] < ut_trail[i-1]:
                    ut_trail[i] = min(ut_trail[i-1], ha_close[i] + nl)
                    ut_above[i] = False
                elif ha_close[i] > ut_trail[i-1]:
                    ut_trail[i] = ha_close[i] - nl
                    ut_above[i] = True
                else:
                    ut_trail[i] = ha_close[i] + nl
                    ut_above[i] = False

            df["HA_Close"] = ha_close
            df["UT_Trail"] = ut_trail
            df["UT_Above"] = ut_above

        except Exception as e:
            print(f"[indicators] BotBulls3 error: {e}")
            df["HA_Close"] = close.copy()
            df["UT_Trail"] = close.copy()
            df["UT_Above"] = True

        # --- Build result --------------------------------------------------
        # Forward-fill indicator warm-up gaps, but never back-fill prior-session
        # levels. Back-filling PDH/PDL or POC/VAH/VAL would leak future sessions
        # into earlier candles and distort backtests.
        indicator_cols = [
            "RSI", "EMA_Fast", "EMA_Slow", "SMA",
            "MACD_Line", "MACD_Signal", "MACD_Hist",
            "BOLL_Upper", "BOLL_Lower", "BOLL_Middle",
            "Supertrend", "ATR", "VWAP",
            "ADX", "DI_Plus", "DI_Minus",
            "Daily_HighLow_High", "Daily_HighLow_Low",
            "Daily_HighLow_High_Trigger", "Daily_HighLow_Low_Trigger",
            "Volume_Profile_POC", "Volume_Profile_VAH", "Volume_Profile_VAL",
            "Swing_Support_1", "Swing_Support_2", "Swing_Support_3", "Swing_Support_4", "Swing_Support_5",
            "Swing_Resistance_1", "Swing_Resistance_2", "Swing_Resistance_3", "Swing_Resistance_4", "Swing_Resistance_5",
            # Premium indicator columns
            "WT1", "WT2", "MFI_14",
            "LX_SmartTrail", "LX_TrendTracer", "LX_RZ_Upper", "LX_RZ_Lower",
            "HA_Close", "UT_Trail",
        ]
        prior_session_cols = {
            "Daily_HighLow_High", "Daily_HighLow_Low",
            "Daily_HighLow_High_Trigger", "Daily_HighLow_Low_Trigger",
            "Volume_Profile_POC", "Volume_Profile_VAH", "Volume_Profile_VAL",
            "Swing_Support_1", "Swing_Support_2", "Swing_Support_3", "Swing_Support_4", "Swing_Support_5",
            "Swing_Resistance_1", "Swing_Resistance_2", "Swing_Resistance_3", "Swing_Resistance_4", "Swing_Resistance_5",
        }
        for col in indicator_cols:
            if col in df.columns:
                df[col] = df[col].ffill() if col in prior_session_cols else df[col].ffill().bfill()

        from user_config import active_evaluation_context
        eval_context = active_evaluation_context.get()
        if eval_context in ("dashboard", "bots"):
            chart_df = _completed_candles_for_live_chart(df, timeframe)
            latest = chart_df.iloc[-1]  # Dashboard and bots use the latest fully closed/completed candle.
            prev = chart_df.iloc[-2] if len(chart_df) > 1 else latest
            recent_bars = chart_df.tail(5)
        else:
            chart_df = df
            latest = df.iloc[-1]  # Backtest/custom analysis can use the current row
            prev = df.iloc[-2]
            recent_bars = df.tail(5)

        signal_chart_latest = latest if eval_context in ("dashboard", "bots") else df.iloc[-1]
        signals = _generate_signals(latest, prev, settings=settings, recent_bars=recent_bars, chart_latest=signal_chart_latest)

        # For dashboard and bots preview charts, mask candlestick markers to the displayed latest candle only.
        # During backtesting simulation, we keep all bars active so the historical loop can evaluate rolling pattern windows at each bar i.
        if eval_context != "backtest" and len(chart_df) > 0:
            mask_not_last = df.index != chart_df.index[-1]
            df.loc[mask_not_last, "cdl_name"] = "Neutral"
            df.loc[mask_not_last, ["cdl_bullish", "cdl_bearish"]] = False

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
            "atr": round(float(latest["ATR"]), 8),
            "rsi": round(float(latest["RSI"]), 2),
            "vwap": float(latest["VWAP"]) if "VWAP" in latest else float(latest["Close"]),
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
                    "daily_high_low_high": _safe(row.get("Daily_HighLow_High")),
                    "daily_high_low_low":  _safe(row.get("Daily_HighLow_Low")),
                    "daily_high_low_high_trigger": _safe(row.get("Daily_HighLow_High_Trigger")),
                    "daily_high_low_low_trigger":  _safe(row.get("Daily_HighLow_Low_Trigger")),
                    "daily_high_low_bullish": bool(row.get("Daily_HighLow_Bullish", False)),
                    "daily_high_low_bearish": bool(row.get("Daily_HighLow_Bearish", False)),
                    "pivot_p":       _safe(row.get("Pivot_P")),
                    "pivot_r1":      _safe(row.get("Pivot_R1")),
                    "pivot_s1":      _safe(row.get("Pivot_S1")),
                    "pivot_r2":      _safe(row.get("Pivot_R2")),
                    "pivot_s2":      _safe(row.get("Pivot_S2")),
                    "pivot_r3":      _safe(row.get("Pivot_R3")),
                    "pivot_s3":      _safe(row.get("Pivot_S3")),
                    "pivot_r4":      _safe(row.get("Pivot_R4")),
                    "pivot_s4":      _safe(row.get("Pivot_S4")),
                    "pivot_r5":      _safe(row.get("Pivot_R5")),
                    "pivot_s5":      _safe(row.get("Pivot_S5")),
                    "pivot_bullish": bool(row.get("Pivot_Bullish", False)),
                    "pivot_bearish": bool(row.get("Pivot_Bearish", False)),
                    "volume_profile_poc": _safe(row.get("Volume_Profile_POC")),
                    "volume_profile_vah": _safe(row.get("Volume_Profile_VAH")),
                    "volume_profile_val": _safe(row.get("Volume_Profile_VAL")),
                    "volume_profile_bullish": bool(row.get("Volume_Profile_Bullish", False)),
                    "volume_profile_bearish": bool(row.get("Volume_Profile_Bearish", False)),
                    "swing_support_1": _safe(row.get("Swing_Support_1")),
                    "swing_support_2": _safe(row.get("Swing_Support_2")),
                    "swing_support_3": _safe(row.get("Swing_Support_3")),
                    "swing_support_4": _safe(row.get("Swing_Support_4")),
                    "swing_support_5": _safe(row.get("Swing_Support_5")),
                    "swing_resistance_1": _safe(row.get("Swing_Resistance_1")),
                    "swing_resistance_2": _safe(row.get("Swing_Resistance_2")),
                    "swing_resistance_3": _safe(row.get("Swing_Resistance_3")),
                    "swing_resistance_4": _safe(row.get("Swing_Resistance_4")),
                    "swing_resistance_5": _safe(row.get("Swing_Resistance_5")),
                    "swing_bullish": bool(row.get("Swing_Bullish", False)),
                    "swing_bearish": bool(row.get("Swing_Bearish", False)),
                    # Oscillators (drawn in sub-pane)
                    "rsi":           _safe(row.get("RSI")),
                    "macd_line":     _safe(row.get("MACD_Line")),
                    "macd_signal":   _safe(row.get("MACD_Signal")),
                    "macd_hist":     _safe(row.get("MACD_Hist")),
                    "mystic_bull":   _safe(row.get("Bull_Pulse")),
                    "mystic_bear":   _safe(row.get("Bear_Pulse")),
                    "adx":           _safe(row.get("ADX")),
                    "di_plus":       _safe(row.get("DI_Plus")),
                    "di_minus":      _safe(row.get("DI_Minus")),
                    "cdl_name":      str(row.get("cdl_name", "Neutral")),
                    "cdl_bullish":   bool(row.get("cdl_bullish", False)),
                    "cdl_bearish":   bool(row.get("cdl_bearish", False)),
                    # Premium indicators
                    "wt1":            _safe(row.get("WT1")),
                    "wt2":            _safe(row.get("WT2")),
                    "mfi":            _safe(row.get("MFI_14")),
                    "ba1_buy":        bool(row.get("BA1_Buy", False)),
                    "ba1_sell":       bool(row.get("BA1_Sell", False)),
                    "ba1_bullish":    bool(row.get("BA1_Bullish", False)),
                    "ba1_bearish":    bool(row.get("BA1_Bearish", False)),
                    "lx_smart_trail": _safe(row.get("LX_SmartTrail")),
                    "lx_smart_trend": bool(row.get("LX_SmartTrend", True)),
                    "lx_trend_tracer":_safe(row.get("LX_TrendTracer")),
                    "lx_rz_upper":    _safe(row.get("LX_RZ_Upper")),
                    "lx_rz_lower":    _safe(row.get("LX_RZ_Lower")),
                    "lx_score":       _safe(row.get("LX_Score")),
                    "ha_close":       _safe(row.get("HA_Close")),
                    "ut_trail":       _safe(row.get("UT_Trail")),
                    "ut_above":       bool(row.get("UT_Above", True)),
                } for t, row in chart_df.iterrows()
            ],
            "df": df,
        }

    except Exception as exc:
        print(f"[indicators] Calc error for {ticker}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Signal generation (used by live engine AND backtester)
# ---------------------------------------------------------------------------
def _generate_signals(latest, prev, settings=None, recent_bars=None, chart_latest=None):
    """
    Read indicator values from a single bar and return signal verdicts.
    """
    def _param(key, fallback=None):
        return _resolve_param(key, fallback, settings=settings)

    try:
        enabled = None
        if isinstance(settings, dict) and isinstance(settings.get("enabled_indicators"), (list, tuple, set)):
            enabled = {str(v) for v in settings.get("enabled_indicators") if v}
        if enabled is not None and not enabled:
            return {}

        if enabled and enabled.issubset({"RSI", "Bollinger", "VWAP"}):
            rsi_val = float(latest["RSI"])
            price = float(latest["Close"])
            boll_lo = float(latest["BOLL_Lower"])
            boll_hi = float(latest["BOLL_Upper"])
            vwap = float(latest["VWAP"])
            prev_vwap = float(prev.get("VWAP", vwap))
            bull_candle = bool(latest.get("cdl_bullish", False))
            prev_close = float(prev.get("Close", price))
            is_near_vwap = abs(price - vwap) / vwap < 0.015 if vwap else False
            rsi_os = float(_param("RSI_OVERSOLD", 30))
            rsi_ob = float(_param("RSI_OVERBOUGHT", 70))

            signals = {}
            if "RSI" in enabled:
                signals["RSI"] = {
                    "signal": (
                        "BULLISH" if rsi_val < rsi_os
                        else "BEARISH" if rsi_val > rsi_ob
                        else "NEUTRAL"
                    ),
                    "reason": (
                        f"Oversold {rsi_val:.1f}" if rsi_val < rsi_os
                        else f"Overbought {rsi_val:.1f}" if rsi_val > rsi_ob
                        else f"Neutral {rsi_val:.1f}"
                    ),
                    "weight": 1,
                }
            if "Bollinger" in enabled:
                boll_width = boll_hi - boll_lo
                signals["Bollinger"] = {
                    "signal": (
                        "BULLISH" if price <= (boll_lo + boll_width * 0.2)
                        else "BEARISH" if price >= (boll_hi - boll_width * 0.2)
                        else "NEUTRAL"
                    ),
                    "reason": (
                        f"Near Low {boll_lo:.1f}" if price <= (boll_lo + boll_width * 0.2)
                        else f"Near High {boll_hi:.1f}" if price >= (boll_hi - boll_width * 0.2)
                        else "In Channel"
                    ),
                    "weight": 1,
                }
            if "VWAP" in enabled:
                signals["VWAP"] = {
                    "signal": (
                        "BULLISH" if (price >= vwap and prev_close < prev_vwap) or (price >= vwap and is_near_vwap and bull_candle)
                        else "BEARISH" if price < vwap
                        else "NEUTRAL"
                    ),
                    "reason": (
                        f"AVWAP Breakout ({vwap:.1f})" if (price >= vwap and prev_close < prev_vwap)
                        else f"Bounce off AVWAP ({vwap:.1f})" if (price >= vwap and is_near_vwap and bull_candle)
                        else f"Below AVWAP ({vwap:.1f})" if price < vwap
                        else f"Holding above AVWAP ({vwap:.1f})"
                    ),
                    "weight": 1,
                }
            return signals

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
        dhl_high = float(latest.get("Daily_HighLow_High", np.nan))
        dhl_low = float(latest.get("Daily_HighLow_Low", np.nan))
        dhl_buffer_pct = max(0.0, float(_param("DAILY_HIGH_LOW_BUFFER_PCT", 0.0)))
        dhl_ready = np.isfinite(dhl_high) and np.isfinite(dhl_low) and dhl_high > dhl_low > 0
        if dhl_ready:
            dhl_high_trigger = dhl_high * (1.0 + dhl_buffer_pct / 100.0)
            dhl_low_trigger = dhl_low * (1.0 - dhl_buffer_pct / 100.0)
            dhl_bull = price > dhl_high_trigger and prev_close <= dhl_high_trigger
            dhl_bear = price < dhl_low_trigger and prev_close >= dhl_low_trigger
            dhl_buffer_note = f" + {dhl_buffer_pct:.2f}% buffer" if dhl_buffer_pct else ""
            if dhl_bull:
                dhl_signal = "BULLISH"
                dhl_reason = f"Prior high breakout above {dhl_high_trigger:.2f}{dhl_buffer_note}"
            elif dhl_bear:
                dhl_signal = "BEARISH"
                dhl_reason = f"Prior low breakdown below {dhl_low_trigger:.2f}{dhl_buffer_note}"
            else:
                dhl_signal = "NEUTRAL"
                if dhl_buffer_pct:
                    dhl_reason = f"Inside buffered trigger range H {dhl_high_trigger:.2f} / L {dhl_low_trigger:.2f} (PDH {dhl_high:.2f} / PDL {dhl_low:.2f})"
                else:
                    dhl_reason = f"Inside prior range H {dhl_high:.2f} / L {dhl_low:.2f}"
        else:
            dhl_signal = "NEUTRAL"
            dhl_reason = "Awaiting prior session high/low"

        pivot_p = float(latest.get("Pivot_P", np.nan))
        pivot_r1 = float(latest.get("Pivot_R1", np.nan))
        pivot_s1 = float(latest.get("Pivot_S1", np.nan))
        pivot_r2 = float(latest.get("Pivot_R2", np.nan))
        pivot_s2 = float(latest.get("Pivot_S2", np.nan))
        pivot_r3 = float(latest.get("Pivot_R3", np.nan))
        pivot_s3 = float(latest.get("Pivot_S3", np.nan))
        pivot_r4 = float(latest.get("Pivot_R4", np.nan))
        pivot_s4 = float(latest.get("Pivot_S4", np.nan))
        pivot_r5 = float(latest.get("Pivot_R5", np.nan))
        pivot_s5 = float(latest.get("Pivot_S5", np.nan))
        pivot_level_count = min(5, max(1, int(float(_param("PIVOT_LEVEL_COUNT", 1)))))
        pivot_tolerance_pct = max(0.0, float(_param("PIVOT_TOLERANCE_PCT", 0.0)))
        pivot_ready = np.isfinite(pivot_p) and np.isfinite(pivot_r1) and np.isfinite(pivot_s1) and pivot_r1 > pivot_p > pivot_s1
        if pivot_ready:
            pivot_tolerance = max(price * (pivot_tolerance_pct / 100.0), 0.0)
            low_now = float(latest.get("Low", price))
            high_now = float(latest.get("High", price))
            pivot_bull = ((low_now <= pivot_s1 + pivot_tolerance) and price > pivot_s1) or (price > pivot_p and prev_close <= pivot_p)
            pivot_bear = ((high_now >= pivot_r1 - pivot_tolerance) and price < pivot_r1) or (price < pivot_p and prev_close >= pivot_p)
            if pivot_bull:
                pivot_signal = "BULLISH"
                pivot_reason = f"Pivot support/reclaim confirmed | P {pivot_p:.2f}, S1 {pivot_s1:.2f}, R1 {pivot_r1:.2f}, tolerance {pivot_tolerance_pct:.2f}%"
            elif pivot_bear:
                pivot_signal = "BEARISH"
                pivot_reason = f"Pivot resistance/rejection confirmed | P {pivot_p:.2f}, S1 {pivot_s1:.2f}, R1 {pivot_r1:.2f}, tolerance {pivot_tolerance_pct:.2f}%"
            else:
                pivot_signal = "NEUTRAL"
                extra_levels = f", S{pivot_level_count}/R{pivot_level_count} active" if pivot_level_count > 1 else ""
                pivot_reason = f"Between pivot S/R levels | P {pivot_p:.2f}, S1 {pivot_s1:.2f}, R1 {pivot_r1:.2f}{extra_levels}"
        else:
            pivot_signal = "NEUTRAL"
            pivot_reason = "Awaiting prior session pivot levels"

        vp_poc = float(latest.get("Volume_Profile_POC", np.nan))
        vp_vah = float(latest.get("Volume_Profile_VAH", np.nan))
        vp_val = float(latest.get("Volume_Profile_VAL", np.nan))
        vp_tolerance_pct = max(0.0, float(_param("VOLUME_PROFILE_TOLERANCE_PCT", 0.15)))
        vp_confirm = max(1, min(5, int(float(_param("VOLUME_PROFILE_CONFIRMATION_CANDLES", 1)))))
        vp_ready = np.isfinite(vp_poc) and np.isfinite(vp_vah) and np.isfinite(vp_val) and vp_vah >= vp_poc >= vp_val > 0
        if vp_ready:
            vp_tol = max(price * (vp_tolerance_pct / 100.0), 0.0)
            low_now = float(latest.get("Low", price))
            high_now = float(latest.get("High", price))
            recent_closes = []
            if recent_bars is not None and len(recent_bars) > 0:
                try:
                    recent_closes = [float(v) for v in recent_bars["Close"].tail(vp_confirm).tolist()]
                except Exception:
                    recent_closes = []
            if len(recent_closes) < vp_confirm:
                recent_closes = [price]
            accepted_above_value = len(recent_closes) >= vp_confirm and all(c > vp_vah + vp_tol for c in recent_closes[-vp_confirm:])
            accepted_below_value = len(recent_closes) >= vp_confirm and all(c < vp_val - vp_tol for c in recent_closes[-vp_confirm:])
            poc_reclaim = price > vp_poc and prev_close <= vp_poc
            poc_loss = price < vp_poc and prev_close >= vp_poc
            val_bounce = low_now <= vp_val + vp_tol and price > vp_val
            vah_rejection = high_now >= vp_vah - vp_tol and price < vp_vah

            if accepted_above_value:
                vp_signal = "BULLISH"
                vp_reason = f"Accepted above VAH {vp_vah:.2f} after {vp_confirm} candle(s) | POC {vp_poc:.2f}"
            elif accepted_below_value:
                vp_signal = "BEARISH"
                vp_reason = f"Accepted below VAL {vp_val:.2f} after {vp_confirm} candle(s) | POC {vp_poc:.2f}"
            elif poc_reclaim:
                vp_signal = "BULLISH"
                vp_reason = f"POC reclaim above {vp_poc:.2f} | VAH {vp_vah:.2f} / VAL {vp_val:.2f}"
            elif poc_loss:
                vp_signal = "BEARISH"
                vp_reason = f"POC loss below {vp_poc:.2f} | VAH {vp_vah:.2f} / VAL {vp_val:.2f}"
            elif val_bounce:
                vp_signal = "BULLISH"
                vp_reason = f"VAL support bounce near {vp_val:.2f} | POC {vp_poc:.2f}"
            elif vah_rejection:
                vp_signal = "BEARISH"
                vp_reason = f"VAH rejection near {vp_vah:.2f} | POC {vp_poc:.2f}"
            else:
                vp_signal = "NEUTRAL"
                vp_reason = f"Inside value area | VAL {vp_val:.2f} / POC {vp_poc:.2f} / VAH {vp_vah:.2f}"
        else:
            vp_signal = "NEUTRAL"
            vp_reason = "Awaiting prior-session volume profile"

        swing_support = float(latest.get("Swing_Support_1", np.nan))
        swing_resistance = float(latest.get("Swing_Resistance_1", np.nan))
        swing_level_count = max(1, min(5, int(float(_param("SWING_POINTS_LEVEL_COUNT", 1)))))
        swing_mode = str(_param("SWING_POINTS_ENTRY_MODE", "bounce_retest") or "bounce_retest")
        swing_lookback = max(1, min(365, int(float(_param("SWING_POINTS_LOOKBACK_DAYS", 20)))))
        swing_ready = np.isfinite(swing_support) or np.isfinite(swing_resistance)
        if bool(latest.get("Swing_Bullish", False)):
            swing_signal = "BULLISH"
            swing_reason = f"Swing support/reclaim confirmed | Support {swing_support:.2f}" if np.isfinite(swing_support) else "Swing bullish structure confirmed"
        elif bool(latest.get("Swing_Bearish", False)):
            swing_signal = "BEARISH"
            swing_reason = f"Swing resistance/rejection confirmed | Resistance {swing_resistance:.2f}" if np.isfinite(swing_resistance) else "Swing bearish structure confirmed"
        elif swing_ready:
            parts = []
            if np.isfinite(swing_support):
                parts.append(f"Support {swing_support:.2f}")
            if np.isfinite(swing_resistance):
                parts.append(f"Resistance {swing_resistance:.2f}")
            swing_signal = "NEUTRAL"
            swing_reason = f"Watching swing zones | {' / '.join(parts)} | {swing_lookback}D {swing_mode.replace('_', ' ')}"
        else:
            swing_signal = "NEUTRAL"
            swing_reason = "Awaiting swing support/resistance levels"

        adx_val = float(latest.get("ADX", 0))
        di_plus = float(latest.get("DI_Plus", 0))
        di_minus = float(latest.get("DI_Minus", 0))

        # Check if trend strength exists (> 25)
        is_trending = adx_val > float(_param("ADX_TRENDING_THRESHOLD", 25))

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

        # Check Candle Patterns on the latest/new candle (analyzed using previous 5 bars during formula calculation)
        cdl_bar = chart_latest if chart_latest is not None else latest
        p_name = str(cdl_bar.get("cdl_name", "Neutral"))
        is_bull = bool(cdl_bar.get("cdl_bullish", False))
        is_bear = bool(cdl_bar.get("cdl_bearish", False))
        if (is_bull or is_bear) and p_name not in ("Neutral", "None", ""):
            cdl_signal = "BULLISH" if is_bull else "BEARISH"
            cdl_reason = f"Pattern: {p_name}"
        else:
            cdl_signal = "NEUTRAL"
            cdl_reason = f"Pattern: {p_name if p_name not in ('Neutral', '', 'None') else 'None'}"

        rsi_os = float(_param("RSI_OVERSOLD", 30))
        rsi_ob = float(_param("RSI_OVERBOUGHT", 70))
        signals = {
            "RSI": {
                "signal": (
                    "BULLISH" if rsi_val < rsi_os
                    else "BEARISH" if rsi_val > rsi_ob
                    else "NEUTRAL"
                ),
                "reason": (
                    f"Oversold {rsi_val:.1f}" if rsi_val < rsi_os
                    else f"Overbought {rsi_val:.1f}" if rsi_val > rsi_ob
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
            "Prior Day High/Low": {
                "signal": dhl_signal,
                "reason": dhl_reason,
                "weight": 1,
                "levels": {
                    "high": dhl_high if dhl_ready else None,
                    "low": dhl_low if dhl_ready else None,
                    "high_trigger": dhl_high_trigger if dhl_ready else None,
                    "low_trigger": dhl_low_trigger if dhl_ready else None,
                    "buffer_pct": dhl_buffer_pct if dhl_ready else None,
                },
            },
            "Pivot Points (S/R)": {
                "signal": pivot_signal,
                "reason": pivot_reason,
                "weight": 1,
                "levels": {
                    "pivot": pivot_p if pivot_ready else None,
                    "r1": pivot_r1 if pivot_ready else None,
                    "s1": pivot_s1 if pivot_ready else None,
                    "r2": pivot_r2 if pivot_ready and pivot_level_count >= 2 and np.isfinite(pivot_r2) else None,
                    "s2": pivot_s2 if pivot_ready and pivot_level_count >= 2 and np.isfinite(pivot_s2) else None,
                    "r3": pivot_r3 if pivot_ready and pivot_level_count >= 3 and np.isfinite(pivot_r3) else None,
                    "s3": pivot_s3 if pivot_ready and pivot_level_count >= 3 and np.isfinite(pivot_s3) else None,
                    "r4": pivot_r4 if pivot_ready and pivot_level_count >= 4 and np.isfinite(pivot_r4) else None,
                    "s4": pivot_s4 if pivot_ready and pivot_level_count >= 4 and np.isfinite(pivot_s4) else None,
                    "r5": pivot_r5 if pivot_ready and pivot_level_count >= 5 and np.isfinite(pivot_r5) else None,
                    "s5": pivot_s5 if pivot_ready and pivot_level_count >= 5 and np.isfinite(pivot_s5) else None,
                    "level_count": pivot_level_count if pivot_ready else None,
                    "tolerance_pct": pivot_tolerance_pct if pivot_ready else None,
                },
            },
            "Volume Profile": {
                "signal": vp_signal,
                "reason": vp_reason,
                "weight": 1,
                "levels": {
                    "poc": vp_poc if vp_ready else None,
                    "vah": vp_vah if vp_ready else None,
                    "val": vp_val if vp_ready else None,
                    "value_area_pct": float(_param("VOLUME_PROFILE_VALUE_AREA_PCT", 70.0)) if vp_ready else None,
                    "tolerance_pct": vp_tolerance_pct if vp_ready else None,
                    "confirmation_candles": vp_confirm if vp_ready else None,
                },
            },
            "Swing Points": {
                "signal": swing_signal,
                "reason": swing_reason,
                "weight": 1,
                "levels": {
                    "support_1": float(latest.get("Swing_Support_1", np.nan)) if np.isfinite(float(latest.get("Swing_Support_1", np.nan))) else None,
                    "support_2": float(latest.get("Swing_Support_2", np.nan)) if swing_level_count >= 2 and np.isfinite(float(latest.get("Swing_Support_2", np.nan))) else None,
                    "support_3": float(latest.get("Swing_Support_3", np.nan)) if swing_level_count >= 3 and np.isfinite(float(latest.get("Swing_Support_3", np.nan))) else None,
                    "support_4": float(latest.get("Swing_Support_4", np.nan)) if swing_level_count >= 4 and np.isfinite(float(latest.get("Swing_Support_4", np.nan))) else None,
                    "support_5": float(latest.get("Swing_Support_5", np.nan)) if swing_level_count >= 5 and np.isfinite(float(latest.get("Swing_Support_5", np.nan))) else None,
                    "resistance_1": float(latest.get("Swing_Resistance_1", np.nan)) if np.isfinite(float(latest.get("Swing_Resistance_1", np.nan))) else None,
                    "resistance_2": float(latest.get("Swing_Resistance_2", np.nan)) if swing_level_count >= 2 and np.isfinite(float(latest.get("Swing_Resistance_2", np.nan))) else None,
                    "resistance_3": float(latest.get("Swing_Resistance_3", np.nan)) if swing_level_count >= 3 and np.isfinite(float(latest.get("Swing_Resistance_3", np.nan))) else None,
                    "resistance_4": float(latest.get("Swing_Resistance_4", np.nan)) if swing_level_count >= 4 and np.isfinite(float(latest.get("Swing_Resistance_4", np.nan))) else None,
                    "resistance_5": float(latest.get("Swing_Resistance_5", np.nan)) if swing_level_count >= 5 and np.isfinite(float(latest.get("Swing_Resistance_5", np.nan))) else None,
                    "level_count": swing_level_count if swing_ready else None,
                    "lookback_days": swing_lookback if swing_ready else None,
                    "entry_mode": swing_mode if swing_ready else None,
                    "settings_source": _bot_swing_settings_source(settings) if swing_ready else None,
                },
            },
            "Mystic Pulse": {
                "signal": (
                    "BULLISH" if bull_p >= float(_param("MYSTIC_PULSE_THRESHOLD", 5))
                    else "BEARISH" if bear_p >= float(_param("MYSTIC_PULSE_THRESHOLD", 5))
                    else "NEUTRAL"
                ),
                "reason": f"Strength {int(bull_p if bull_p > bear_p else bear_p)}/{int(float(_param('MYSTIC_PULSE_THRESHOLD', 5)))}",
                "weight": 1,
            },
            "Candle Patterns": {
                "signal": cdl_signal,
                "reason": cdl_reason,
                "weight": 1,
            },
            # ═══════════════ Premium Indicators ═══════════════
            "BotBulls1": {
                "signal": (
                    "BULLISH" if bool(latest.get("BA1_Buy", False)) or bool(latest.get("BA1_Bullish", False))
                    else "BEARISH" if bool(latest.get("BA1_Sell", False)) or bool(latest.get("BA1_Bearish", False))
                    else "NEUTRAL"
                ),
                "reason": (
                    f"BB1 BUY (WT reclaim {float(latest.get('WT1', 0)):.0f}) confirmed above EMA" if bool(latest.get("BA1_Buy", False))
                    else f"BB1 SELL (WT rejection {float(latest.get('WT1', 0)):.0f}) confirmed below EMA" if bool(latest.get("BA1_Sell", False))
                    else f"BB1 bullish after last confirmed BUY (MFI {float(latest.get('MFI_14', 50)):.0f})" if bool(latest.get("BA1_Bullish", False))
                    else f"BB1 bearish after last confirmed SELL (MFI {float(latest.get('MFI_14', 50)):.0f})" if bool(latest.get("BA1_Bearish", False))
                    else f"WT1: {float(latest.get('WT1', 0)):.1f} | MFI: {float(latest.get('MFI_14', 50)):.0f}"
                ),
                "weight": 1,
                "premium": True,
            },
            "BotBulls2": {
                "signal": (
                    # Continuous zone signal: BULLISH if in Smart Trend, else BEARISH
                    "BULLISH" if bool(latest.get("LX_SmartTrend", True)) else "BEARISH"
                ),
                "reason": (
                    f"BB2 {'BUY' if bool(latest.get('LX_SmartTrend', True)) else 'SELL'} "
                    f"(SmartTrail: {'↑' if bool(latest.get('LX_SmartTrend', True)) else '↓'}, "
                    f"Confluence: {int(latest.get('LX_Score', 0))}/4, "
                    f"Tracer: {'Bullish' if (not np.isnan(float(latest.get('LX_TrendTracer', 0))) and price > float(latest.get('LX_TrendTracer', 0))) else 'Bearish'})"
                ),
                "weight": 1,
                "premium": True,
            },
            "BotBulls3": {
                "signal": (
                    # Continuous zone signal: BULLISH if above UT Trail, else BEARISH
                    "BULLISH" if bool(latest.get("UT_Above", True)) else "BEARISH"
                ),
                "reason": (
                    f"BB3 {'BUY' if bool(latest.get('UT_Above', True)) else 'SELL'} (HA {'Above' if bool(latest.get('UT_Above', True)) else 'Below'} Trail {float(latest.get('UT_Trail', 0)):.2f})"
                    + (" | FLIP ↑" if (bool(latest.get("UT_Above", True)) and not bool(prev.get("UT_Above", True))) else "")
                    + (" | FLIP ↓" if (not bool(latest.get("UT_Above", True)) and bool(prev.get("UT_Above", True))) else "")
                ),
                "weight": 1,
                "premium": True,
            },
        }
        if enabled is not None:
            signals = {name: data for name, data in signals.items() if name in enabled}
        return signals

    except Exception:
        return {}
