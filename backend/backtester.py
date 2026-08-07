import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import uuid
from typing import Dict, List, Optional
import asyncio
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_signals
from swing_strategy import build_entry_signal, build_swing_exit
from trader import get_confluence_decision
import config as global_config
from indicator_catalog import EXTENSION_CHART_FIELDS, EXTENSION_CHART_GROUPS
from user_config import get_user_config
from prior_day_high_low import dynamic_high_low_exit_levels, prior_day_levels_from_history, resolve_high_low_buffer_pct

def _unix_timestamp(t):
    try:
        if hasattr(t, "tz") and t.tz is None:
            t = t.tz_localize("America/New_York")
        return int(t.timestamp())
    except Exception:
        return int(t.timestamp()) if hasattr(t, "timestamp") else int(t)

def _safe_chart_level(row, key, digits):
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

def _chart_indicator_fields(row, digits, enabled_indicators=None, buy_mode="", sell_mode=""):
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
        "mystic_bull": _safe_chart_level(row, "Bull_Pulse", 4),
        "mystic_bear": _safe_chart_level(row, "Bear_Pulse", 4),
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
        "bb5_upper": _safe_chart_level(row, "BB5_Upper", digits),
        "bb5_lower": _safe_chart_level(row, "BB5_Lower", digits),
        "bb5_target": _safe_chart_level(row, "BB5_Target", digits),
        "bb5_invalidation": _safe_chart_level(row, "BB5_Invalidation", digits),
        "bb5_breakout_level": _safe_chart_level(row, "BB5_Breakout_Level", digits),
        "bb5_upper_start": _safe_chart_level(row, "BB5_Upper_Start", digits),
        "bb5_lower_start": _safe_chart_level(row, "BB5_Lower_Start", digits),
        "bb5_formation_start_time": _safe_chart_level(row, "BB5_Formation_Start_Time", 0),
        "bb5_formation_end_time": _safe_chart_level(row, "BB5_Formation_End_Time", 0),
        "bb5_pole_start_time": _safe_chart_level(row, "BB5_Pole_Start_Time", 0),
        "bb5_pole_end_time": _safe_chart_level(row, "BB5_Pole_End_Time", 0),
        "bb5_formation_bars": int(_safe_chart_level(row, "BB5_Formation_Bars", 0) or 0),
        "bb5_confidence": _safe_chart_level(row, "BB5_Confidence", 2),
        "bb5_direction": int(_safe_chart_level(row, "BB5_Direction", 0) or 0),
        "bb5_reasons": str(row.get("BB5_Reasons", "")),
        "bb5_breakout_bull": _safe_chart_bool(row, "BB5_Breakout_Bull"),
        "bb5_breakout_bear": _safe_chart_bool(row, "BB5_Breakout_Bear"),
        "bb5_pattern": str(row.get("BB5_Pattern", "NONE")),
        "bb5_state": str(row.get("BB5_State", "NONE")),
        "cdl_name": str(row.get("cdl_name", "Neutral")),
        "cdl_bullish": _safe_chart_bool(row, "cdl_bullish"),
        "cdl_bearish": _safe_chart_bool(row, "cdl_bearish"),
    }
    fields.update({
        api_key: _safe_chart_level(row, column, 6)
        for api_key, column in EXTENSION_CHART_FIELDS.items()
    })
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
        "Mystic Pulse": {"mystic_bull", "mystic_bear"},
        "Volume Profile": {"volume_profile_poc", "volume_profile_vah", "volume_profile_val", "volume_profile_bullish", "volume_profile_bearish"},
        "BotBulls1": {"wt1", "wt2", "mfi", "ba1_buy", "ba1_sell", "ba1_bullish", "ba1_bearish"},
        "BotBulls2": {"lx_smart_trail", "lx_trend_tracer", "lx_rz_upper", "lx_rz_lower", "ba2_buy", "ba2_sell"},
        "BotBulls3": {"ha_close", "ut_trail", "ba3_buy", "ba3_sell"},
        "BotBulls5": {"bb5_upper", "bb5_lower", "bb5_target", "bb5_invalidation", "bb5_breakout_level", "bb5_upper_start", "bb5_lower_start", "bb5_formation_start_time", "bb5_formation_end_time", "bb5_pole_start_time", "bb5_pole_end_time", "bb5_formation_bars", "bb5_confidence", "bb5_direction", "bb5_reasons", "bb5_breakout_bull", "bb5_breakout_bear", "bb5_pattern", "bb5_state"},
        "Candle Patterns": {"cdl_name", "cdl_bullish", "cdl_bearish"},
    }
    groups.update({
        indicator: set(field_names)
        for indicator, field_names in EXTENSION_CHART_GROUPS.items()
    })
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

class Backtester:
    def __init__(self, ticker, timeframe, start_date, end_date, initial_capital=1000.0, threshold=5, sell_threshold=3, enabled_indicators=None, risk_per_trade=0.02, max_pos_pct=0.25, ext_hours=True, buy_mode="indicator", custom_buy_price=0.0, sell_mode="indicator", atr_stop_multiplier=2.0, atr_trail_multiplier=3.0, atr_take_profit_multiplier=4.0, enable_atr_trail=True, ai_autopilot=False, ai_interval=10, ai_model="llama-3.3-70b-versatile", enable_cooldown=True, cooldown_minutes=None, regular_stop_loss_pct=0.05, regular_take_profit_pct=0.10, regular_trailing_pct=0.03, enable_pct_trail=True, cancel_event=None, entry_atr_multiplier=None, entry_pct_move=None, entry_atr_style="pullback_reclaim", entry_pct_style="dip_reclaim", swing_entry_mode="bounce_retest", swing_entry_lookback_days=20, swing_exit_lookback_days=20, market_session="Regular", allocation_mode="fixed", trade_amount=None, custom_stop_loss_price=0.0, custom_take_profit_price=0.0, enable_signal_reset=False, enable_smart_reentry=False, smart_reentry_type="RSI", smart_reentry_rsi_call_max=70.0, smart_reentry_rsi_put_min=30.0, smart_reentry_vwap_call_max_dist_pct=0.5, smart_reentry_vwap_put_max_dist_pct=0.5, enable_risk=True, daily_high_low_buffer_pct=None, trade_direction_mode="long", max_completed_trades=None, indicator_overrides=None):
        self.ticker = ticker
        self.timeframe = timeframe
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.threshold = int(threshold or 0)
        self.sell_threshold = int(sell_threshold or 0)
        self.enabled_indicators = enabled_indicators or []
        if self.enabled_indicators:
            self.threshold = max(1, self.threshold)
            self.sell_threshold = max(1, self.sell_threshold)
        self.allocation_mode = "compound" if str(allocation_mode).lower() == "compound" else "fixed"
        self.trade_amount = max(1.0, float(trade_amount if trade_amount is not None else initial_capital))
        self.current_trade_amount = self.trade_amount
        self.risk_per_trade = risk_per_trade
        self.max_pos_pct = max_pos_pct
        self.enable_risk = enable_risk
        self.ext_hours = ext_hours
        self.buy_mode = buy_mode
        self.custom_buy_price = custom_buy_price
        self.sell_mode = sell_mode
        self.atr_stop_multiplier = atr_stop_multiplier
        self.atr_trail_multiplier = atr_trail_multiplier
        self.atr_take_profit_multiplier = atr_take_profit_multiplier
        self.enable_atr_trail = bool(enable_atr_trail)
        self.entry_atr_multiplier = entry_atr_multiplier or atr_stop_multiplier
        self.entry_pct_move = entry_pct_move or max(1.0, regular_stop_loss_pct * 100.0)
        self.entry_atr_style = entry_atr_style or "pullback_reclaim"
        self.entry_pct_style = entry_pct_style or "dip_reclaim"
        self.swing_entry_mode = swing_entry_mode or "bounce_retest"
        self.swing_entry_lookback_days = swing_entry_lookback_days or 20
        self.swing_exit_lookback_days = swing_exit_lookback_days or 20
        self.market_session = market_session or "Regular"
        self.custom_stop_loss_price = float(custom_stop_loss_price or 0.0)
        self.custom_take_profit_price = float(custom_take_profit_price or 0.0)
        self.enable_signal_reset = bool(enable_signal_reset)
        self.enable_smart_reentry = bool(enable_smart_reentry)
        self.trade_direction_mode = str(trade_direction_mode or "long").lower()
        if self.trade_direction_mode not in {"long", "short", "both"}:
            self.trade_direction_mode = "long"
        self.daily_high_low_buffer_pct = resolve_high_low_buffer_pct(fallback=daily_high_low_buffer_pct)
        self.indicator_settings = {
            **(indicator_overrides if isinstance(indicator_overrides, dict) else {}),
            "DAILY_HIGH_LOW_BUFFER_PCT": self.daily_high_low_buffer_pct,
            "SWING_POINTS_LOOKBACK_DAYS": self.swing_entry_lookback_days,
            "SWING_POINTS_ENTRY_MODE": self.swing_entry_mode,
            "market_session": self.market_session,
        }
        self.smart_reentry_type = str(smart_reentry_type or "RSI").upper()
        self.smart_reentry_rsi_call_max = float(smart_reentry_rsi_call_max or 70.0)
        self.smart_reentry_rsi_put_min = float(smart_reentry_rsi_put_min or 30.0)
        self.smart_reentry_vwap_call_max_dist_pct = float(smart_reentry_vwap_call_max_dist_pct or 0.5)
        self.smart_reentry_vwap_put_max_dist_pct = float(smart_reentry_vwap_put_max_dist_pct or 0.5)
        self.ai_autopilot = ai_autopilot or (sell_mode == "ai_autopilot")
        self.ai_interval = ai_interval
        self.ai_model = ai_model
        self.enable_cooldown = enable_cooldown
        self.cooldown_minutes = cooldown_minutes
        self.regular_stop_loss_pct = regular_stop_loss_pct
        self.regular_take_profit_pct = regular_take_profit_pct
        self.regular_trailing_pct = regular_trailing_pct
        self.enable_pct_trail = bool(enable_pct_trail)
        self.cancel_event = cancel_event
        self._last_ai_decision = None
        self.last_exit_time = None
        self.signal_locked_direction = None
        
        self.equity = initial_capital
        self.position = None 
        self.trades = []
        self.equity_history = []
        self.df = None
        self.data_source = "yahoo"
        self.max_completed_trades = int(max_completed_trades) if max_completed_trades else None
        self.overtrade_limit_hit = False

        # Pre-cache timezones to prevent massive slowdowns in tight loops
        import pytz
        clean_ticker = self.ticker.upper().replace("/", "")
        self.is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        self.local_tz = pytz.utc if self.is_crypto else pytz.timezone("America/New_York")
        self.central_tz = pytz.timezone("America/Chicago")
        self.utc_tz = pytz.utc

    def _history_window(self, records, end_index, lookback_days=None, padding_days=2):
        """Return only the recent history needed by structure strategies.

        Swing / prior-session strategy helpers already filter by lookback days;
        passing the full growing list every bar makes structural grid tests
        unnecessarily slow. Keep a timestamp-bounded slice with a small buffer
        for weekends/overnight session boundaries.
        """
        if not records:
            return []
        end_index = max(0, min(int(end_index), len(records) - 1))
        latest_time = records[end_index].get("time")
        if latest_time is None:
            start = max(0, end_index + 1 - 2500)
            return records[start:end_index + 1]

        try:
            cutoff = float(latest_time) - (max(float(lookback_days or 1), 1.0) + float(padding_days or 0)) * 86400.0
        except Exception:
            start = max(0, end_index + 1 - 2500)
            return records[start:end_index + 1]

        start = end_index
        while start > 0:
            try:
                if float(records[start - 1].get("time", 0)) < cutoff:
                    break
            except Exception:
                break
            start -= 1
        return records[start:end_index + 1]

    def _localize_dt(self, ts):
        localized_dt = self.local_tz.localize(ts)
        return localized_dt.astimezone(self.utc_tz)

    def _to_utc_iso(self, ts):
        utc_dt = self._localize_dt(ts)
        return utc_dt.strftime("%Y-%m-%d %H:%M:%SZ")

    def _to_unix(self, ts):
        utc_dt = self._localize_dt(ts)
        return int(utc_dt.timestamp())

    def _to_central_str(self, ts):
        utc_dt = self._localize_dt(ts)
        central_dt = utc_dt.astimezone(self.central_tz)
        return central_dt.strftime("%Y-%m-%d %H:%M")

    def _direction_allowed(self, side):
        side = str(side or "").lower()
        if self.trade_direction_mode == "both":
            return side in {"long", "short"}
        return self.trade_direction_mode == side

    def _build_simple_signal_cache(self):
        """Vectorize the common RSI/Bollinger/AVWAP signal set for fast backtests."""
        enabled = set(self.enabled_indicators or [])
        if not enabled or not enabled.issubset({"RSI", "Bollinger", "VWAP"}):
            return None

        cfg = get_user_config()
        rsi_os = float(self.indicator_settings.get("RSI_OVERSOLD", cfg.get("RSI_OVERSOLD", 30)))
        rsi_ob = float(self.indicator_settings.get("RSI_OVERBOUGHT", cfg.get("RSI_OVERBOUGHT", 70)))
        close = self.df["Close"].astype(float)
        prev_close = close.shift(1)
        rsi = self.df["RSI"].astype(float)
        boll_lo = self.df["BOLL_Lower"].astype(float)
        boll_hi = self.df["BOLL_Upper"].astype(float)
        vwap = self.df["VWAP"].astype(float)
        prev_vwap = vwap.shift(1).fillna(vwap)
        bull_candle = self.df.get("cdl_bullish", pd.Series(False, index=self.df.index)).fillna(False).astype(bool)
        is_near_vwap = ((close - vwap).abs() / vwap.replace(0, np.nan)).lt(0.015).fillna(False)

        cache = []
        for idx in range(len(self.df)):
            signals = {}
            price = float(close.iloc[idx])
            if "RSI" in enabled:
                rsi_val = float(rsi.iloc[idx])
                previous_rsi = float(rsi.iloc[idx - 1]) if idx > 0 and pd.notna(rsi.iloc[idx - 1]) else rsi_val
                rsi_signal = (
                    "BULLISH" if rsi_val < rsi_os and rsi_val > previous_rsi
                    else "BEARISH" if rsi_val > rsi_ob and rsi_val < previous_rsi
                    else "NEUTRAL"
                )
                rsi_reason = (
                    f"Oversold and rising {rsi_val:.1f}" if rsi_signal == "BULLISH"
                    else f"Overbought and falling {rsi_val:.1f}" if rsi_signal == "BEARISH"
                    else f"Neutral {rsi_val:.1f}"
                )
                signals["RSI"] = {"signal": rsi_signal, "reason": rsi_reason, "weight": 1}
            if "Bollinger" in enabled:
                lo = float(boll_lo.iloc[idx])
                hi = float(boll_hi.iloc[idx])
                width = hi - lo
                boll_signal = "BULLISH" if price <= (lo + width * 0.2) else "BEARISH" if price >= (hi - width * 0.2) else "NEUTRAL"
                boll_reason = (
                    f"Near Low {lo:.1f}" if boll_signal == "BULLISH"
                    else f"Near High {hi:.1f}" if boll_signal == "BEARISH"
                    else "In Channel"
                )
                signals["Bollinger"] = {"signal": boll_signal, "reason": boll_reason, "weight": 1}
            if "VWAP" in enabled:
                vw = float(vwap.iloc[idx])
                prev_vw = float(prev_vwap.iloc[idx])
                prev_c = float(prev_close.iloc[idx]) if pd.notna(prev_close.iloc[idx]) else price
                crossed_up = price >= vw and prev_c < prev_vw
                bounced = price >= vw and bool(is_near_vwap.iloc[idx]) and bool(bull_candle.iloc[idx])
                vwap_signal = "BULLISH" if crossed_up or bounced else "BEARISH" if price < vw else "NEUTRAL"
                vwap_reason = (
                    f"AVWAP Breakout ({vw:.1f})" if crossed_up
                    else f"Bounce off AVWAP ({vw:.1f})" if bounced
                    else f"Below AVWAP ({vw:.1f})" if price < vw
                    else f"Holding above AVWAP ({vw:.1f})"
                )
                signals["VWAP"] = {"signal": vwap_signal, "reason": vwap_reason, "weight": 1}
            cache.append(signals)
        return cache

    def run(self, precomputed_df=None, precomputed_signal_cache=None, fast_metrics=False):
        # Force naive datetimes to match pandas index
        if hasattr(self.start_date, "tzinfo") and self.start_date.tzinfo is not None:
            self.start_date = self.start_date.replace(tzinfo=None)
        if hasattr(self.end_date, "tzinfo") and self.end_date.tzinfo is not None:
            self.end_date = self.end_date.replace(tzinfo=None)

        if precomputed_df is not None:
            self.df = precomputed_df.loc[self.start_date:self.end_date]
        else:
            # 1. Fetch Data with padding where the provider can actually satisfy it.
            from datetime import timedelta
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
            padded_start = self.start_date - timedelta(days=warmup_days)
            raw_df = get_historical_data(self.ticker, self.timeframe, padded_start, self.end_date, force_yahoo=True)
            
            if raw_df is None or raw_df.empty:
                return {"error": "No data found for the selected range."}

            # Filter for Regular Hours (RTH) if stocks & self.ext_hours is False and not daily
            clean_ticker = self.ticker.upper().replace("/", "")
            is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
            if not is_crypto and not self.ext_hours and self.timeframe != "1Day":
                try:
                    time_idx = raw_df.index.time
                    start_time = pd.to_datetime("09:30:00").time()
                    end_time = pd.to_datetime("16:00:00").time()
                    rth_mask = (time_idx >= start_time) & (time_idx <= end_time)
                    raw_df = raw_df[rth_mask]
                    print(f"[backtest] Filtered for Regular Trading Hours (RTH) only. {len(raw_df)} bars remaining.")
                except Exception as rth_exc:
                    print(f"[backtest] RTH filtering failed: {rth_exc}")

            # 2. Pre-calculate indicators on the massive padded dataframe
            calc_settings = {
                **(self.indicator_settings or {}),
                "enabled_indicators": self.enabled_indicators,
                "buy_mode": self.buy_mode,
                "sell_mode": self.sell_mode,
                "_require_rsi_dependency": bool(
                    self.ai_autopilot
                    or (
                        self.enable_smart_reentry
                        and str(self.smart_reentry_type or "RSI").upper() == "RSI"
                    )
                ),
            }
            result = calculate_indicators_for_df(raw_df, self.timeframe, self.ticker, settings=calc_settings)
            if not result:
                return {"error": "Failed to calculate indicators."}
            
            full_df = result['df']
            
            # 3. Slice the dataframe perfectly back down to the user's requested date window
            self.df = full_df.loc[self.start_date:self.end_date]
        
        if self.df.empty:
            return {"error": "No data available in the requested range after filtering."}

        signal_cache = None
        if precomputed_signal_cache is not None and len(precomputed_signal_cache) == len(self.df):
            signal_cache = precomputed_signal_cache

        structure_entry_modes = {
            "custom_price", "atr", "hybrid_atr", "percentage", "hybrid_pct",
            "pivot_points", "hybrid_pivot", "high_low", "hybrid_high_low",
            "swing_points", "hybrid_swing",
        }
        structure_exit_modes = {"swing_points", "hybrid_swing", "high_low", "hybrid_high_low"}
        needs_price_history = self.buy_mode in structure_entry_modes or self.sell_mode in structure_exit_modes
        price_history_records = []
        if needs_price_history:
            for ts, row in self.df.iterrows():
                try:
                    ts_et = None
                    try:
                        ts_et = ts.tz_localize(self.utc_tz).astimezone(self.local_tz) if getattr(ts, "tzinfo", None) is None else ts.astimezone(self.local_tz)
                    except Exception:
                        ts_et = None
                    price_history_records.append({
                        "time": _unix_timestamp(ts),
                        "dt_et": ts_et,
                        "open": float(row.get("Open", row.get("open", row.get("Close", 0.0)))),
                        "high": float(row.get("High", row.get("high", row.get("Close", 0.0)))),
                        "low": float(row.get("Low", row.get("low", row.get("Close", 0.0)))),
                        "close": float(row.get("Close", row.get("close", 0.0))),
                        "volume": float(row.get("Volume", row.get("volume", 0.0))),
                    })
                except Exception:
                    continue

        if signal_cache is None and not self.ai_autopilot and self.buy_mode == "indicator":
            signal_cache = self._build_simple_signal_cache()

        # 4. Simulate bar-by-bar
        for i in range(len(self.df)):
            if self.overtrade_limit_hit:
                break
            if self.cancel_event and self.cancel_event.is_set():
                print(f"[backtest] Cancelled by user halfway through execution ({i}/{len(self.df)} bars)")
                return {"error": "Backtest cancelled by user"}
                
            current_bar = self.df.iloc[i]
            timestamp = self.df.index[i]

            # Wait for indicators to warm up (usually 30 bars for RSI/EMA/etc)
            if i < 30:
                continue

            prev_bar = self.df.iloc[i-1]
            current_price = current_bar['Close']
            
            # Check for SL / TP if in position
            if self.position:
                current_price = current_bar['Close']
                precision = 6 if current_price < 1.0 else 2
                position_side = str(self.position.get("side", "long")).lower()
                if position_side not in {"long", "short"}:
                    position_side = "long"

                # ── ATR / AI-based hard stops (existing modes) ──────────────────
                if self.sell_mode in ["sltp", "hybrid", "trend_rider", "ai_autopilot"]:
                    stop_hit = current_bar['Low'] <= self.position['stop_loss'] if position_side == "long" else current_bar['High'] >= self.position['stop_loss']
                    target_hit = current_bar['High'] >= self.position['take_profit'] if position_side == "long" else current_bar['Low'] <= self.position['take_profit']
                    if stop_hit:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Stop Loss Hit (${self.position['stop_loss']:.{precision}f})",
                            exit_price_override=self.position['stop_loss'],
                            exit_stop_price=self.position['stop_loss'],
                        )
                        self._last_ai_decision = None
                        continue
                    elif target_hit:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Take Profit Hit (${self.position['take_profit']:.{precision}f})",
                            exit_price_override=self.position['take_profit'],
                            exit_stop_price=self.position['take_profit'],
                        )
                        self._last_ai_decision = None
                        continue

                # ── NEW: Percentage-based hard stops ────────────────────────────
                elif self.sell_mode in ['percentage', 'hybrid_pct']:
                    # Ratchet trailing stop upward only if trailing is enabled
                    if self.enable_pct_trail and self.regular_trailing_pct > 0:
                        if position_side == "short":
                            candidate = round(current_price * (1 + self.regular_trailing_pct), precision)
                            if candidate < self.position['stop_loss']:
                                self.position['stop_loss'] = candidate
                                self.position['trailing_stop_loss'] = candidate
                        else:
                            candidate = round(current_price * (1 - self.regular_trailing_pct), precision)
                            if candidate > self.position['stop_loss']:
                                self.position['stop_loss'] = candidate
                                self.position['trailing_stop_loss'] = candidate
                    trail_note = f" / {self.regular_trailing_pct*100:.1f}% Trail" if self.enable_pct_trail and self.regular_trailing_pct > 0 else ""
                    
                    stop_hit = current_bar['Low'] <= self.position['stop_loss'] if position_side == "long" else current_bar['High'] >= self.position['stop_loss']
                    target_hit = current_bar['High'] >= self.position['take_profit'] if position_side == "long" else current_bar['Low'] <= self.position['take_profit']
                    if stop_hit:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Percentage Stop Loss Hit ({self.regular_stop_loss_pct*100:.1f}% SL{trail_note})",
                            exit_price_override=self.position['stop_loss'],
                            exit_stop_price=self.position['stop_loss'],
                        )
                        continue
                    elif target_hit:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Percentage Take Profit Hit ({self.regular_take_profit_pct*100:.1f}% TP)",
                            exit_price_override=self.position['take_profit'],
                            exit_stop_price=self.position['take_profit'],
                        )
                        continue

                # ── Custom fixed price exits ───────────────────────────────────
                elif self.sell_mode in ['custom_sltp', 'custom_underlying_sltp']:
                    stop_hit = self.custom_stop_loss_price > 0 and (
                        current_bar['Low'] <= self.custom_stop_loss_price if position_side == "long" else current_bar['High'] >= self.custom_stop_loss_price
                    )
                    target_hit = self.custom_take_profit_price > 0 and (
                        current_bar['High'] >= self.custom_take_profit_price if position_side == "long" else current_bar['Low'] <= self.custom_take_profit_price
                    )
                    if stop_hit:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Custom Stop Loss Hit (${self.custom_stop_loss_price:.{precision}f})",
                            exit_price_override=self.custom_stop_loss_price,
                            exit_stop_price=self.custom_stop_loss_price,
                        )
                        continue
                    elif target_hit:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Custom Take Profit Hit (${self.custom_take_profit_price:.{precision}f})",
                            exit_price_override=self.custom_take_profit_price,
                            exit_stop_price=self.custom_take_profit_price,
                        )
                        continue

                # ── Swing support/resistance exits ─────────────────────────────
                elif self.sell_mode in ['swing_points', 'hybrid_swing']:
                    is_long_side = position_side != "short"
                    if is_long_side:
                        swing_stop = current_bar.get("Swing_Support_1")
                        swing_target = current_bar.get("Swing_Resistance_1")
                    else:
                        swing_stop = current_bar.get("Swing_Resistance_1")
                        swing_target = current_bar.get("Swing_Support_1")
                    if swing_stop is not None and pd.isna(swing_stop):
                        swing_stop = None
                    if swing_target is not None and pd.isna(swing_target):
                        swing_target = None
                    stop_hit = swing_stop is not None and (
                        current_bar['Low'] <= swing_stop if is_long_side else current_bar['High'] >= swing_stop
                    )
                    target_hit = swing_target is not None and (
                        current_bar['High'] >= swing_target if is_long_side else current_bar['Low'] <= swing_target
                    )
                    if stop_hit:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Swing Stop Loss Hit (${swing_stop:.{precision}f})",
                            exit_price_override=swing_stop,
                            exit_stop_price=swing_stop,
                        )
                        continue
                    elif target_hit:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Swing Take Profit Hit (${swing_target:.{precision}f})",
                            exit_price_override=swing_target,
                            exit_stop_price=swing_target,
                        )
                        continue

                # ── NEW: Pivot Point exits ───────────────────────────────────────
                elif self.sell_mode in ['pivot_points', 'hybrid_pivot']:
                    if i > 0:  # Need at least 1 prior bar
                        prev_bar_data = self.df.iloc[i - 1]
                        prev_h = float(prev_bar_data.get('High', current_price))
                        prev_l = float(prev_bar_data.get('Low', current_price))
                        prev_c = float(prev_bar_data.get('Close', current_price))
                        P  = (prev_h + prev_l + prev_c) / 3.0
                        R1 = round(2 * P - prev_l, precision)
                        S1 = round(2 * P - prev_h, precision)
                        if position_side == "short":
                            if current_bar['High'] >= R1:
                                self._exit_trade(current_bar, timestamp, f"Pivot Stop Loss Hit (R1=${R1:.{precision}f})", exit_price_override=R1)
                                continue
                            elif current_bar['Low'] <= S1:
                                self._exit_trade(current_bar, timestamp, f"Pivot Take Profit Hit (S1=${S1:.{precision}f})", exit_price_override=S1)
                                continue
                        else:
                            if current_bar['Low'] <= S1:
                                self._exit_trade(current_bar, timestamp, f"Pivot Stop Loss Hit (S1=${S1:.{precision}f})", exit_price_override=S1)
                                continue
                            elif current_bar['High'] >= R1:
                                self._exit_trade(current_bar, timestamp, f"Pivot Take Profit Hit (R1=${R1:.{precision}f})", exit_price_override=R1)
                                continue

                # ── Prior Day High/Low Breakout ─────────────────────────────────
                elif self.sell_mode in ['high_low', 'hybrid_high_low']:
                    prev_h = current_bar.get("Daily_HighLow_High")
                    prev_l = current_bar.get("Daily_HighLow_Low")
                    if prev_h is not None and prev_l is not None and not pd.isna(prev_h) and not pd.isna(prev_l):
                        hl_levels = dynamic_high_low_exit_levels(prev_h, prev_l, current_price, self.daily_high_low_buffer_pct)
                    else:
                        hl_levels = prior_day_levels_from_history(
                            self._history_window(price_history_records, i, 7, padding_days=1),
                            buffer_pct=self.daily_high_low_buffer_pct,
                        )
                    if hl_levels:
                        if position_side == "short":
                            if current_price > hl_levels["high_trigger"]:
                                self._exit_trade(
                                    current_bar,
                                    timestamp,
                                    f"Prior High Breakout (Trigger=${hl_levels['high_trigger']:.{precision}f}, PDH=${hl_levels['high']:.{precision}f}, Buffer={hl_levels['buffer_pct']:.2f}%)",
                                    exit_price_override=current_price,
                                )
                                continue
                            elif current_price < hl_levels["low_trigger"]:
                                self._exit_trade(
                                    current_bar,
                                    timestamp,
                                    f"Prior Low Breakdown (Trigger=${hl_levels['low_trigger']:.{precision}f}, PDL=${hl_levels['low']:.{precision}f}, Buffer={hl_levels['buffer_pct']:.2f}%)",
                                    exit_price_override=current_price,
                                )
                                continue
                        else:
                            if current_price < hl_levels["low_trigger"]:
                                self._exit_trade(
                                    current_bar,
                                    timestamp,
                                    f"Prior Low Breakdown (Trigger=${hl_levels['low_trigger']:.{precision}f}, PDL=${hl_levels['low']:.{precision}f}, Buffer={hl_levels['buffer_pct']:.2f}%)",
                                    exit_price_override=current_price,
                                )
                                continue
                            elif current_price > hl_levels["high_trigger"]:
                                self._exit_trade(
                                    current_bar,
                                    timestamp,
                                    f"Prior High Breakout (Trigger=${hl_levels['high_trigger']:.{precision}f}, PDH=${hl_levels['high']:.{precision}f}, Buffer={hl_levels['buffer_pct']:.2f}%)",
                                    exit_price_override=current_price,
                                )
                                continue

                # ── ATR trailing stop movement (existing) ───────────────────────
                if self.sell_mode in ["sltp", "hybrid", "trend_rider"]:
                    atr = current_bar['ATR']
                    trail_distance = atr * self.atr_trail_multiplier
                    if self.enable_atr_trail and self.position.get('qty', 0) > 0:
                        if position_side == "short":
                            new_stop = current_price + trail_distance
                            if new_stop < self.position['stop_loss']:
                                self.position['stop_loss'] = round(new_stop, precision)
                        else:
                            new_stop = current_price - trail_distance
                            if new_stop > self.position['stop_loss']:
                                self.position['stop_loss'] = round(new_stop, precision)

            action = "HOLD"
            reason = "Neutral"

            if self.ai_autopilot:
                # Call AI every N bars, or if we have no last decision
                if (i % self.ai_interval == 0) or (self._last_ai_decision is None):
                    from ai_indicator import get_ai_decision_for_bar
                    
                    bar_dict = current_bar.to_dict()
                    ai_pos = None
                    if self.position:
                        ai_pos = {
                            'entry_price': self.position['entry_price'],
                            'qty': self.position['qty'],
                            'stop_loss': self.position['stop_loss'],
                            'take_profit': self.position['take_profit'],
                        }
                    
                    self._last_ai_decision = get_ai_decision_for_bar(
                        ticker=self.ticker,
                        bar_data=bar_dict,
                        account_equity=self.equity,
                        available_cash=self.equity,
                        position=ai_pos,
                        timeframe=self.timeframe,
                        model=self.ai_model
                    )
                
                if self._last_ai_decision:
                    action = self._last_ai_decision.get("signal", "HOLD")
                    reason = self._last_ai_decision.get("reason", "AI Autopilot Decision")
                    position_side = str(self.position.get("side", "long")).lower() if self.position else None

                    if not self.position:
                        if self.enable_signal_reset and self.signal_locked_direction == "BUY" and action != "BUY":
                            self.signal_locked_direction = None
                        if self.enable_signal_reset and self.signal_locked_direction == "SELL" and action != "SELL":
                            self.signal_locked_direction = None

                        if action in {"BUY", "SELL"}:
                            entry_side = "long" if action == "BUY" else "short"
                            if self._direction_allowed(entry_side):
                                allowed, block_reason = self._entry_guard_allows(action, current_bar, current_price, timestamp, side=entry_side)
                                if not allowed:
                                    action = "HOLD"
                                    reason = block_reason
                                    self._last_ai_decision = None
                                else:
                                    self._enter_trade_ai(current_bar, timestamp, self._last_ai_decision, side=entry_side)
                                    self._last_ai_decision = None
                            else:
                                self._last_ai_decision = None
                    elif position_side == "long" and action == "SELL":
                        # Hybrid / Dynamic Sell check: exit only if sell_mode supports indicator/hybrid
                        if self.sell_mode in ["indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing", "ai_autopilot"]:
                            self._exit_trade(current_bar, timestamp, f"AI Exit: {reason}")
                            self._last_ai_decision = None  # Reset to re-evaluate next bars
                        else:
                            # SL/TP only mode: ignore dynamic sell signal
                            self._last_ai_decision = None
                    elif position_side == "short" and action == "BUY":
                        if self.sell_mode in ["indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing", "ai_autopilot"]:
                            self._exit_trade(current_bar, timestamp, f"AI Exit: {reason}")
                            self._last_ai_decision = None
                        else:
                            self._last_ai_decision = None
            else:
                # Get Strategy Decision
                if signal_cache is not None:
                    signals = signal_cache[i] or {}
                else:
                    signals = _generate_signals(
                        current_bar,
                        prev_bar,
                        settings={
                            **(self.indicator_settings or {}),
                            "enabled_indicators": self.enabled_indicators,
                        },
                        recent_bars=self.df.iloc[max(0, i - 4) : i + 1],
                    )
                
                # Filter signals based on enabled indicators
                filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
                
                analysis = {
                    'bullish_count': sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BULLISH'),
                    'bearish_count': sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BEARISH'),
                    'signals': filtered_signals
                }
                bullish_ready = analysis['bullish_count'] >= self.threshold
                bearish_ready = analysis['bearish_count'] >= self.sell_threshold
                bullish_dominates = bullish_ready and (not bearish_ready or analysis['bullish_count'] > analysis['bearish_count'])
                bearish_dominates = bearish_ready and (not bullish_ready or analysis['bearish_count'] > analysis['bullish_count'])
                
                if self.sell_mode == "trend_rider":
                    ema_f = current_bar.get("EMA_Fast", 0)
                    ema_s = current_bar.get("EMA_Slow", 0)
                    prev_ema_f = prev_bar.get("EMA_Fast", 0)
                    prev_ema_s = prev_bar.get("EMA_Slow", 0)
                    st_bull = current_bar.get("Supertrend_Trend", True)
                    sma_val = current_bar.get("SMA", 0)
                    
                    # Entry: Golden Cross (or already crossed) AND Supertrend Bullish
                    golden_cross = (ema_f > ema_s) and (prev_ema_f <= prev_ema_s)
                    if golden_cross and st_bull:
                        action = "BUY"
                        reason = "Trend Rider Entry (Golden Cross + Supertrend)"
                    elif bullish_dominates:
                        action = "BUY"
                        names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                        reason = f"Trend Rider Entry: {analysis['bullish_count']} signals ({', '.join(names)})"

                    # Exit: Trend Break (Death Cross or below SMA)
                    death_cross = (ema_f < ema_s) and (prev_ema_f >= prev_ema_s)
                    if death_cross or current_price < sma_val:
                        action = "SELL"
                        reason = "Trend Rider Exit: Trend Broken (Death Cross or < SMA 200)"

                else:
                    entry_mode = self.buy_mode
                    price_action_modes = ['custom_price', 'atr', 'hybrid_atr', 'percentage', 'hybrid_pct', 'pivot_points', 'hybrid_pivot', 'high_low', 'hybrid_high_low', 'swing_points', 'hybrid_swing']
                    simple_price_action_modes = {'custom_price', 'atr', 'hybrid_atr', 'percentage', 'hybrid_pct'}
                    hybrid_modes = {'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'}
                    entry_candidates = []
                    if entry_mode in price_action_modes:
                        action = "HOLD"
                        cached_structure_modes = {'pivot_points', 'hybrid_pivot', 'high_low', 'hybrid_high_low', 'swing_points', 'hybrid_swing'}
                        if entry_mode in cached_structure_modes:
                            entry_signal = {"action": "HOLD", "structure_direction": "NONE", "reason": "Cached structure: waiting for confirmation"}
                            if entry_mode in {'pivot_points', 'hybrid_pivot'}:
                                if bool(current_bar.get("Pivot_Bullish", False)):
                                    level = current_bar.get("Pivot_S1", current_price)
                                    entry_signal = {"action": "BUY", "structure_direction": "BULLISH", "reason": f"Pivot reclaim confirmed from cached levels near ${float(level):.2f}"}
                                elif bool(current_bar.get("Pivot_Bearish", False)):
                                    level = current_bar.get("Pivot_R1", current_price)
                                    entry_signal = {"action": "SELL", "structure_direction": "BEARISH", "reason": f"Pivot rejection confirmed from cached levels near ${float(level):.2f}"}
                            elif entry_mode in {'high_low', 'hybrid_high_low'}:
                                if bool(current_bar.get("Daily_HighLow_Bullish", False)):
                                    level = current_bar.get("Daily_HighLow_High_Trigger", current_bar.get("Daily_HighLow_High", current_price))
                                    entry_signal = {"action": "BUY", "structure_direction": "BULLISH", "reason": f"Prior high breakout confirmed from cached level near ${float(level):.2f}"}
                                elif bool(current_bar.get("Daily_HighLow_Bearish", False)):
                                    level = current_bar.get("Daily_HighLow_Low_Trigger", current_bar.get("Daily_HighLow_Low", current_price))
                                    entry_signal = {"action": "SELL", "structure_direction": "BEARISH", "reason": f"Prior low breakdown confirmed from cached level near ${float(level):.2f}"}
                            elif entry_mode in {'swing_points', 'hybrid_swing'}:
                                if bool(current_bar.get("Swing_Bullish", False)):
                                    level = current_bar.get("Swing_Support_1", current_price)
                                    entry_signal = {"action": "BUY", "structure_direction": "BULLISH", "reason": f"Swing support bounce confirmed from cached level near ${float(level):.2f}"}
                                elif bool(current_bar.get("Swing_Bearish", False)):
                                    level = current_bar.get("Swing_Resistance_1", current_price)
                                    entry_signal = {"action": "SELL", "structure_direction": "BEARISH", "reason": f"Swing resistance rejection confirmed from cached level near ${float(level):.2f}"}
                        elif entry_mode in simple_price_action_modes:
                            recent_history = price_history_records[max(0, i - 1):i + 1]
                            entry_signal = build_entry_signal(
                                recent_history,
                                current_price=current_price,
                                entry_mode=entry_mode,
                                lookback_days=self.swing_entry_lookback_days,
                                market_session=self.market_session,
                                is_crypto=self.is_crypto,
                                atr=current_bar.get('ATR', current_price * 0.01),
                                custom_buy_price=self.custom_buy_price,
                                entry_atr_mult=self.entry_atr_multiplier,
                                entry_pct_move=self.entry_pct_move,
                                entry_atr_style=self.entry_atr_style,
                                entry_pct_style=self.entry_pct_style,
                                swing_entry_mode=self.swing_entry_mode,
                                desired_direction=None if entry_mode not in hybrid_modes else "BULLISH",
                                high_low_buffer_pct=self.daily_high_low_buffer_pct,
                            )
                        else:
                            recent_history = self._history_window(price_history_records, i, self.swing_entry_lookback_days)
                            entry_signal = build_entry_signal(
                                recent_history,
                                current_price=current_price,
                                entry_mode=entry_mode,
                                lookback_days=self.swing_entry_lookback_days,
                                market_session=self.market_session,
                                is_crypto=self.is_crypto,
                                atr=current_bar.get('ATR', current_price * 0.01),
                                custom_buy_price=self.custom_buy_price,
                                entry_atr_mult=self.entry_atr_multiplier,
                                entry_pct_move=self.entry_pct_move,
                                entry_atr_style=self.entry_atr_style,
                                entry_pct_style=self.entry_pct_style,
                                swing_entry_mode=self.swing_entry_mode,
                                desired_direction=None if entry_mode not in hybrid_modes else "BULLISH",
                                high_low_buffer_pct=self.daily_high_low_buffer_pct,
                            )
                        signal_action = entry_signal.get("action", "HOLD")
                        if self._direction_allowed("long"):
                            if entry_mode in hybrid_modes:
                                structure_ok = signal_action == "BUY" and entry_signal.get("structure_direction") == "BULLISH"
                                indicator_ok = bullish_dominates
                                if indicator_ok and structure_ok:
                                    names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                                    entry_candidates.append((analysis['bullish_count'], "long", f"Hybrid BUY: {analysis['bullish_count']} signals ({', '.join(names)}) + {entry_signal.get('reason', 'bullish structure')}"))
                            elif signal_action == "BUY":
                                entry_candidates.append((max(analysis['bullish_count'], 1), "long", entry_signal.get("reason", reason)))
                        if self._direction_allowed("short"):
                            if entry_mode in cached_structure_modes:
                                short_signal = entry_signal
                            else:
                                short_signal = build_entry_signal(
                                    recent_history,
                                    current_price=current_price,
                                    entry_mode=entry_mode,
                                    lookback_days=self.swing_entry_lookback_days,
                                    market_session=self.market_session,
                                    is_crypto=self.is_crypto,
                                    atr=current_bar.get('ATR', current_price * 0.01),
                                    custom_buy_price=self.custom_buy_price,
                                    entry_atr_mult=self.entry_atr_multiplier,
                                    entry_pct_move=self.entry_pct_move,
                                    entry_atr_style=self.entry_atr_style,
                                    entry_pct_style=self.entry_pct_style,
                                    swing_entry_mode=self.swing_entry_mode,
                                    desired_direction="BEARISH" if entry_mode in hybrid_modes else None,
                                    high_low_buffer_pct=self.daily_high_low_buffer_pct,
                                )
                            short_action = short_signal.get("action", "HOLD")
                            if entry_mode in hybrid_modes:
                                structure_ok = short_action == "SELL" and short_signal.get("structure_direction") == "BEARISH"
                                indicator_ok = bearish_dominates
                                if indicator_ok and structure_ok:
                                    names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                                    entry_candidates.append((analysis['bearish_count'], "short", f"Hybrid SELL: {analysis['bearish_count']} signals ({', '.join(names)}) + {short_signal.get('reason', 'bearish structure')}"))
                            elif short_action == "SELL":
                                entry_candidates.append((max(analysis['bearish_count'], 1), "short", short_signal.get("reason", reason)))

                    if action == 'HOLD' and entry_mode not in price_action_modes and self.enabled_indicators:
                        if self._direction_allowed("long") and bullish_dominates:
                            names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                            entry_candidates.append((analysis['bullish_count'], "long", f"{analysis['bullish_count']} signals ({', '.join(names)})"))
                        if self._direction_allowed("short") and bearish_dominates:
                            names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                            entry_candidates.append((analysis['bearish_count'], "short", f"Dynamic Sell: {analysis['bearish_count']} signals ({', '.join(names)})"))

                    if entry_candidates:
                        entry_candidates.sort(key=lambda item: (item[0], 1 if item[1] == "long" else 0), reverse=True)
                        _, entry_side, chosen_reason = entry_candidates[0]
                        action = "BUY" if entry_side == "long" else "SELL"
                        reason = chosen_reason
                    else:
                        action = "HOLD"

                    if self.position and self.sell_mode in ["indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"]:
                        current_side = str(self.position.get("side", "long")).lower()
                        if current_side == "long" and bearish_ready and analysis['bearish_count'] >= analysis['bullish_count']:
                            names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                            action = "SELL"
                            reason = f"Indicator Exit: {analysis['bearish_count']} bearish signals ({', '.join(names)})"
                        elif current_side == "short" and bullish_ready and analysis['bullish_count'] >= analysis['bearish_count']:
                            names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                            action = "BUY"
                            reason = f"Indicator Exit: {analysis['bullish_count']} bullish signals ({', '.join(names)})"

                if self.enable_signal_reset and not self.position and self.signal_locked_direction == "BUY" and action != "BUY" and analysis['bullish_count'] < self.threshold:
                    self.signal_locked_direction = None
                if self.enable_signal_reset and not self.position and self.signal_locked_direction == "SELL" and action != "SELL" and analysis['bearish_count'] < self.sell_threshold:
                    self.signal_locked_direction = None

                if action in {'BUY', 'SELL'} and not self.position:
                    entry_side = "long" if action == "BUY" else "short"
                    allowed, block_reason = self._entry_guard_allows(action, current_bar, current_price, timestamp, side=entry_side)
                    if not allowed:
                        action = "HOLD"
                        reason = block_reason
                    elif action == 'BUY' and self._direction_allowed("long"):
                        self._enter_trade(current_bar, timestamp, reason, side=entry_side)
                    elif action == 'SELL' and self._direction_allowed("short"):
                        self._enter_trade(current_bar, timestamp, reason, side=entry_side)
                elif action == 'SELL' and self.position and position_side == "long":
                    self._exit_trade(current_bar, timestamp, reason)
                elif action == 'BUY' and self.position and position_side == "short":
                    self._exit_trade(current_bar, timestamp, reason)

            if not fast_metrics:
                self.equity_history.append({
                    # The metrics renderer only reads equity here; avoid costly tz formatting per bar.
                    'time': timestamp,
                    'equity': self.equity + (self._get_unrealized_pl(current_price=current_bar['Close']) if self.position else 0)
                })

        # Force close any open position at the end of the backtest
        if self.position:
            last_bar = self.df.iloc[-1]
            last_timestamp = self.df.index[-1]
            self._exit_trade(last_bar, last_timestamp, 'End of Backtest')
        results = self._calculate_fast_metrics() if fast_metrics else self._calculate_metrics()
        if "summary" in results:
            s = results["summary"]
            print(f"\n[backtest] COMPLETED for {self.ticker}")
            print(f"[backtest] ROI: {s['roi_pct']}% | Trades: {s['total_trades']} | Win Rate: {s['win_rate_pct']}%")
        return results

    def _enter_trade_ai(self, bar, timestamp, ai_decision, side="long"):
        price = bar['Close']
        
        # Use AI's suggested position size percent (default to 10% if not specified)
        pos_pct = ai_decision.get("position_size_pct", 0.10)
        if pos_pct <= 0:
            pos_pct = 0.10
        # Limit to max_pos_pct
        pos_pct = min(pos_pct, self.max_pos_pct)
        
        # Calculate quantity based on cash allocation
        allocated_cash = self.equity * pos_pct
        qty = allocated_cash / price
        
        # Use AI suggested stop loss and take profit
        stop_loss = ai_decision.get("stop_loss")
        take_profit = ai_decision.get("sell_target")
        
        # Fallbacks if AI didn't provide stop/target
        atr = bar.get('ATR', price * 0.02)
        precision = 6 if price < 1.0 else 2  # Crypto precision fix
        side = str(side or "long").lower()
        if not stop_loss:
            stop_loss = round(price - atr * self.atr_stop_multiplier, precision) if side == "long" else round(price + atr * self.atr_stop_multiplier, precision)
        if not take_profit:
            take_profit = round(price + atr * self.atr_take_profit_multiplier, precision) if side == "long" else round(price - atr * self.atr_take_profit_multiplier, precision)
            
        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'side': side,
            'reason': ai_decision.get("summary", "AI Autopilot Entry")
        }
        entry_label = "BUY" if side == "long" else "SELL"
        print(f"[{self._to_central_str(timestamp)}] {entry_label}  {self.ticker} @ ${price:.2f} | Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f} | Reason: AI Autopilot Entry (Conf: {ai_decision.get('confidence'):.0%})")

    def _enter_trade(self, bar, timestamp, reason, side="long"):
        price = bar['Close']
        atr = bar['ATR']
        if atr <= 0:
            atr = price * 0.01  # Fallback: treat 1% of price as synthetic ATR

        stop_dist = atr * self.atr_stop_multiplier
        if stop_dist <= 0:
            stop_dist = price * 0.01

        side = str(side or "long").lower()
        base_notional = self.current_trade_amount if self.allocation_mode == "compound" else self.trade_amount
        base_notional = max(1.0, float(base_notional))
        max_notional = self.equity * self.max_pos_pct if self.enable_risk else self.equity
        allocated_cash = min(base_notional, max_notional, self.equity)
        qty = allocated_cash / price if price > 0 else 0

        if qty <= 0:
            print(f"[{self._to_central_str(timestamp)}] HOLD {self.ticker} | No allocation available for entry")
            return

        # Volatility Regime Sizing Filter: Scale down quantity by 50% if volatility (ATR / Price) is hyper-extended (> 3.0%)
        volatility_ratio = atr / price
        if self.enable_risk and volatility_ratio > 0.03:
            qty *= 0.5
            reason += " (Volatility Protection Active)"

        precision = 6 if price < 1.0 else 2

        if self.sell_mode in ['percentage', 'hybrid_pct']:
            stop_loss = round(price * (1 - self.regular_stop_loss_pct), precision) if side == "long" else round(price * (1 + self.regular_stop_loss_pct), precision)
            take_profit = round(price * (1 + self.regular_take_profit_pct), precision) if side == "long" else round(price * (1 - self.regular_take_profit_pct), precision)
        elif self.sell_mode in ['custom_sltp', 'custom_underlying_sltp']:
            if side == "long":
                stop_loss = round(self.custom_stop_loss_price, precision) if self.custom_stop_loss_price > 0 else round(price - stop_dist, precision)
                take_profit = round(self.custom_take_profit_price, precision) if self.custom_take_profit_price > 0 else round(price + (atr * self.atr_take_profit_multiplier), precision)
            else:
                stop_loss = round(self.custom_stop_loss_price, precision) if self.custom_stop_loss_price > 0 else round(price + stop_dist, precision)
                take_profit = round(self.custom_take_profit_price, precision) if self.custom_take_profit_price > 0 else round(price - (atr * self.atr_take_profit_multiplier), precision)
        else:
            stop_loss = round(price - stop_dist, precision) if side == "long" else round(price + stop_dist, precision)
            take_profit = round(price + (atr * self.atr_take_profit_multiplier), precision) if side == "long" else round(price - (atr * self.atr_take_profit_multiplier), precision)

        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': stop_loss, 
            'take_profit': take_profit, 
            'initial_stop_loss': stop_loss,
            'initial_take_profit': take_profit,
            'side': side,
            'reason': reason
        }
        entry_label = "BUY" if side == "long" else "SELL"
        print(f"[{self._to_central_str(timestamp)}] {entry_label}  {self.ticker} @ ${price:.2f} | Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f} | Reason: {reason}")

    def _exit_trade(self, bar, timestamp, reason, exit_price_override=None, exit_stop_price=None):
        if not self.position: return
        side = str(self.position.get("side", "long")).lower()
        exit_price = exit_price_override if exit_price_override is not None else bar['Close']
        entry_cost = self.position['qty'] * self.position['entry_price']
        exit_value = self.position['qty'] * exit_price
        
        # 0.1% Fee per side for Crypto (Entry + Exit), 0.0% for Stocks
        clean_ticker = self.ticker.upper().replace("/", "")
        is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        fee = round((entry_cost + exit_value) * 0.001, 2) if is_crypto else 0.0
        if side == "short":
            pl = (self.position['entry_price'] - exit_price) * self.position['qty'] - fee
        else:
            pl = (exit_price - self.position['entry_price']) * self.position['qty'] - fee
        pl_pct = (pl / entry_cost) * 100
        if side == "short":
            self.equity += (entry_cost - exit_value - fee)
        else:
            self.equity += (exit_value - entry_cost - fee)
        
        stop_note = ""
        if exit_stop_price is not None:
            initial_stop = self.position.get('initial_stop_loss')
            if initial_stop is not None and abs(float(exit_stop_price) - float(initial_stop)) > 1e-9:
                stop_note = f" | Stop Used: ${float(exit_stop_price):.2f} (initial ${float(initial_stop):.2f})"
            else:
                stop_note = f" | Stop Used: ${float(exit_stop_price):.2f}"
        exit_label = "SELL" if side == "long" else "BUY"
        print(f"[{self._to_central_str(timestamp)}] {exit_label} {self.ticker} @ ${exit_price:.2f} | P/L: ${pl:.2f} ({pl_pct:+.2f}%) | Fee: ${fee:.2f} | Reason: {reason}{stop_note}")

        self.trades.append({
            'ticker': self.ticker,
            'entry_time': self._to_utc_iso(self.position['entry_time']),
            'exit_time': self._to_utc_iso(timestamp),
            'qty': round(self.position['qty'], 4),
            'entry_price': round(self.position['entry_price'], 8 if is_crypto else 2),
            'exit_price': round(exit_price, 8 if is_crypto else 2),
            'entry_cost': round(entry_cost, 2),
            'exit_value': round(exit_value, 2),
            'fees': fee,
            'pl': round(pl, 2),
            'pl_pct': round(pl_pct, 2),
            'reason': reason,
            'side': side,
            'stop_loss': round(self.position.get('stop_loss', 0), 8 if is_crypto else 2),
            'take_profit': round(self.position.get('take_profit', 0), 8 if is_crypto else 2),
            'initial_stop_loss': round(self.position.get('initial_stop_loss', 0), 8 if is_crypto else 2),
            'initial_take_profit': round(self.position.get('initial_take_profit', 0), 8 if is_crypto else 2),
            'exit_stop_price': round(exit_stop_price, 8 if is_crypto else 2) if exit_stop_price is not None else None,
        })
        if self.max_completed_trades and len(self.trades) >= self.max_completed_trades:
            self.overtrade_limit_hit = True
        self.position = None
        self.last_exit_time = timestamp
        if self.allocation_mode == "compound":
            self.current_trade_amount = max(1.0, self.current_trade_amount + pl)
        if self.enable_signal_reset:
            self.signal_locked_direction = "BUY" if side == "long" else "SELL"

    def _cooldown_block_reason(self, timestamp):
        if not self.enable_cooldown or not self.last_exit_time:
            return None
        from engine import parse_timeframe_to_seconds
        tf_seconds = parse_timeframe_to_seconds(self.timeframe)
        cooldown_secs = self.cooldown_minutes * 60 if self.cooldown_minutes is not None else tf_seconds * 4
        if (timestamp - self.last_exit_time).total_seconds() < cooldown_secs:
            return "Trade Cooldown Active"
        return None

    def _entry_guard_allows(self, action, bar, current_price, timestamp, side="long"):
        side = str(side or "long").lower()
        if side == "long" and action != "BUY":
            return True, None
        if side == "short" and action != "SELL":
            return True, None

        if self.enable_signal_reset and self.signal_locked_direction == ("BUY" if side == "long" else "SELL"):
            waiting = "bullish" if side == "long" else "bearish"
            return False, f"Signal Reset Active: waiting for {waiting} signal reset below {self.threshold}"

        cooldown_reason = self._cooldown_block_reason(timestamp)
        if cooldown_reason:
            return False, cooldown_reason

        return self._smart_reentry_allows(bar, current_price, side=side)

    def _smart_reentry_allows(self, bar, current_price, side="long"):
        if not self.enable_smart_reentry:
            return True, None

        filter_type = (self.smart_reentry_type or "RSI").upper()
        if filter_type == "VWAP":
            vwap = bar.get("VWAP", bar.get("VWAP_Daily", None))
            try:
                vwap = float(vwap)
                current_price = float(current_price)
            except (TypeError, ValueError):
                return True, None
            if vwap <= 0 or current_price <= 0:
                return True, None
            distance_pct = abs((current_price - vwap) / vwap) * 100.0
            max_dist = self.smart_reentry_vwap_call_max_dist_pct if str(side).lower() == "long" else self.smart_reentry_vwap_put_max_dist_pct
            if distance_pct > max_dist:
                return False, f"Smart Re-entry AVWAP Filter: distance {distance_pct:.2f}% > max {max_dist:.2f}%"
            return True, None

        rsi = bar.get("RSI", None)
        try:
            rsi = float(rsi)
        except (TypeError, ValueError):
            return True, None
        if str(side).lower() == "long":
            if rsi > self.smart_reentry_rsi_call_max:
                return False, f"Smart Re-entry RSI Filter: RSI {rsi:.1f} > max {self.smart_reentry_rsi_call_max:.1f}"
        else:
            if rsi < self.smart_reentry_rsi_put_min:
                return False, f"Smart Re-entry RSI Filter: RSI {rsi:.1f} < min {self.smart_reentry_rsi_put_min:.1f}"
        return True, None

    def _check_exit_conditions(self, bar, timestamp):
        if not self.position:
            return
        side = str(self.position.get("side", "long")).lower()
        if side == "short":
            if bar['High'] >= self.position['stop_loss']:
                self._exit_trade({'Close': self.position['stop_loss']}, timestamp, "Stop Loss Hit")
            elif bar['Low'] <= self.position['take_profit']:
                self._exit_trade({'Close': self.position['take_profit']}, timestamp, "Take Profit Hit")
        else:
            if bar['Low'] <= self.position['stop_loss']:
                self._exit_trade({'Close': self.position['stop_loss']}, timestamp, "Stop Loss Hit")
            elif bar['High'] >= self.position['take_profit']:
                self._exit_trade({'Close': self.position['take_profit']}, timestamp, "Take Profit Hit")

    def _get_unrealized_pl(self, current_price):
        if not self.position:
            return 0.0
        side = str(self.position.get("side", "long")).lower()
        if side == "short":
            return (self.position['entry_price'] - current_price) * self.position['qty']
        return (current_price - self.position['entry_price']) * self.position['qty']

    def _calculate_fast_metrics(self):
        start_price = self.df.iloc[0]['Close']
        end_price = self.df.iloc[-1]['Close']
        buy_hold_equity = (end_price / start_price) * self.initial_capital if start_price > 0 else self.initial_capital
        buy_hold_roi = ((end_price - start_price) / start_price) * 100 if start_price > 0 else 0.0
        total_pl = self.equity - self.initial_capital
        roi = (total_pl / self.initial_capital) * 100 if self.initial_capital > 0 else 0.0

        if not self.trades:
            no_trade_reason = "No trades executed during the period. Try lowering thresholds, enabling more indicators, or choosing a looser entry strategy."
            return {
                "summary": {
                    "ticker": self.ticker,
                    "timeframe": self.timeframe,
                    "period_days": (self.end_date - self.start_date).days,
                    "start_date_utc": self.df.index[0].isoformat(),
                    "end_date_utc": self.df.index[-1].isoformat(),
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
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "avg_win_pct": 0.0,
                    "avg_loss_pct": 0.0,
                    "win_loss_ratio": 0.0,
                    "avg_hold_hours": 0.0,
                    "max_win_streak": 0,
                    "max_loss_streak": 0,
                    "expectancy": 0.0,
                    "no_trade_reason": no_trade_reason,
                },
                "equity_curve": [],
                "trades": [],
                "candles": [],
                "diagnostics": {"fast_metrics": True, "no_trade_reason": no_trade_reason},
            }

        df_trades = pd.DataFrame(self.trades)
        win_rate = (df_trades['pl'] > 0).mean() * 100
        winning_pl = df_trades[df_trades['pl'] > 0]['pl']
        losing_pl = df_trades[df_trades['pl'] <= 0]['pl'].abs()
        gross_profit = winning_pl.sum()
        gross_loss = losing_pl.sum()
        # JSON has no representation for Infinity. A zero-loss sample has an
        # unbounded profit factor; return null and let the UI render it as ∞.
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if gross_profit > 0 else 0.0)
        avg_win = round(winning_pl.mean(), 2) if len(winning_pl) > 0 else 0.0
        avg_loss = round(losing_pl.mean(), 2) if len(losing_pl) > 0 else 0.0
        win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (None if avg_win > 0 else 0.0)

        pl_series = df_trades['pl'].values
        entry_costs = df_trades['entry_cost'].values
        returns = np.where(entry_costs > 0, pl_series / entry_costs, 0.0)
        sharpe = 0.0
        if len(returns) > 1 and returns.std() > 0:
            sharpe = round((returns.mean() / returns.std()) * np.sqrt(252), 2)
        sortino = 0.0
        downside = returns[returns < 0]
        if len(downside) > 1 and downside.std() > 0:
            sortino = round((returns.mean() / downside.std()) * np.sqrt(252), 2)

        avg_hold_hours = 0.0
        try:
            entry_times = pd.to_datetime(df_trades['entry_time'])
            exit_times = pd.to_datetime(df_trades['exit_time'])
            avg_hold_hours = round(((exit_times - entry_times).dt.total_seconds() / 3600).mean(), 1)
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

        wr = win_rate / 100
        expectancy = round((wr * avg_win) - ((1 - wr) * avg_loss), 2)
        avg_win_pct = round(df_trades[df_trades['pl'] > 0]['pl_pct'].mean(), 2) if len(winning_pl) > 0 else 0.0
        avg_loss_pct = round(df_trades[df_trades['pl'] <= 0]['pl_pct'].mean(), 2) if len(losing_pl) > 0 else 0.0
        period_days = max((self.end_date - self.start_date).days, 1)
        annualised_roi = roi * (365 / period_days)

        return {
            "summary": {
                "ticker": self.ticker,
                "timeframe": self.timeframe,
                "period_days": period_days,
                "start_date_utc": self.df.index[0].isoformat(),
                "end_date_utc": self.df.index[-1].isoformat(),
                "data_source": self.data_source,
                "initial_capital": self.initial_capital,
                "final_equity": round(self.equity, 2),
                "total_pl": round(total_pl, 2),
                "roi_pct": round(roi, 2),
                "buy_hold_equity": round(buy_hold_equity, 2),
                "buy_hold_roi_pct": round(buy_hold_roi, 2),
                "win_rate_pct": round(win_rate, 2),
                "total_trades": len(self.trades),
                "profitable_trades": len(df_trades[df_trades['pl'] > 0]),
                "losing_trades": len(df_trades[df_trades['pl'] <= 0]),
                "avg_trade_pl": round(df_trades['pl'].mean(), 2),
                "best_trade": round(df_trades['pl'].max(), 2),
                "worst_trade": round(df_trades['pl'].min(), 2),
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": sharpe,
                "sortino_ratio": sortino,
                "calmar_ratio": 0.0,
                "profit_factor": profit_factor,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "avg_win_pct": avg_win_pct,
                "avg_loss_pct": avg_loss_pct,
                "win_loss_ratio": win_loss_ratio,
                "avg_hold_hours": avg_hold_hours,
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "expectancy": expectancy,
                "annualised_roi_pct": round(annualised_roi, 2),
            },
            "equity_curve": [],
            "trades": self.trades[::-1],
            "candles": [],
            "diagnostics": {"fast_metrics": True},
        }

    def _calculate_metrics(self):
        if not self.trades:
            start_price = self.df.iloc[0]['Close']
            end_price = self.df.iloc[-1]['Close']
            buy_hold_equity = (end_price / start_price) * self.initial_capital
            buy_hold_roi = ((end_price - start_price) / start_price) * 100

            total_bars = len(self.df)
            equity_step = max(1, total_bars // 500)
            candles = []
            equity_curve_downsampled = []
            for i in range(total_bars):
                row = self.df.iloc[i]
                ts = self.df.index[i]
                try:
                    unix_time = self._to_unix(ts)
                    o = float(row.get('Open', row.get('open', 0)))
                    h = float(row.get('High', row.get('high', 0)))
                    l = float(row.get('Low', row.get('low', 0)))
                    c = float(row.get('Close', row.get('close', 0)))
                    v = int(row.get('Volume', row.get('volume', 0)))
                    if o > 0 and c > 0:
                        digits = 8 if "USD" in self.ticker.upper() else 4
                        candles.append({
                            "time": unix_time,
                            "open": round(o, digits),
                            "high": round(h, digits),
                            "low": round(l, digits),
                            "close": round(c, digits),
                            "volume": v,
                            **_chart_indicator_fields(row, digits, self.enabled_indicators, self.buy_mode, self.sell_mode),
                        })
                        if i % equity_step == 0 or i == total_bars - 1:
                            equity_curve_downsampled.append({
                                "time": self._to_utc_iso(ts),
                                "equity": self.initial_capital,
                            })
                except Exception:
                    continue

            # Mask out candlestick patterns for all candles except the single last candle to eliminate chart noise
            if "Candle Patterns" in self.enabled_indicators and len(candles) > 1:
                for c in candles[:-1]:
                    c["cdl_name"] = "Neutral"
                    c["cdl_bullish"] = False
                    c["cdl_bearish"] = False

            no_trade_reason = "No trades executed during the period. Try lowering thresholds, enabling more indicators, or choosing a looser entry strategy."
            return {
                "summary": {
                    "ticker": self.ticker,
                    "timeframe": self.timeframe,
                    "period_days": (self.end_date - self.start_date).days,
                    "start_date_utc": self.df.index[0].isoformat(),
                    "end_date_utc": self.df.index[-1].isoformat(),
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
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "avg_win_pct": 0.0,
                    "avg_loss_pct": 0.0,
                    "win_loss_ratio": 0.0,
                    "avg_hold_hours": 0.0,
                    "max_win_streak": 0,
                    "max_loss_streak": 0,
                    "expectancy": 0.0,
                    "no_trade_reason": no_trade_reason,
                },
                "equity_curve": equity_curve_downsampled,
                "trades": [],
                "candles": candles,
                "diagnostics": {
                    "no_trade_reason": no_trade_reason,
                },
            }
        df_trades = pd.DataFrame(self.trades)
        win_rate = (df_trades['pl'] > 0).mean() * 100
        total_pl = self.equity - self.initial_capital
        roi = (total_pl / self.initial_capital) * 100
        
        # Calculate Buy & Hold from the very first available bar
        start_price = self.df.iloc[0]['Close']
        end_price = self.df.iloc[-1]['Close']
        buy_hold_equity = (end_price / start_price) * self.initial_capital
        buy_hold_roi = ((end_price - start_price) / start_price) * 100

        # Max drawdown from equity history
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

        # ── Professional Risk-Adjusted Metrics ──────────────────────────────────
        pl_series = df_trades['pl'].values
        entry_costs = df_trades['entry_cost'].values
        returns = np.where(entry_costs > 0, pl_series / entry_costs, 0.0)

        winning_pl = df_trades[df_trades['pl'] > 0]['pl']
        losing_pl  = df_trades[df_trades['pl'] <= 0]['pl'].abs()

        # Profit Factor
        gross_profit = winning_pl.sum()
        gross_loss   = losing_pl.sum()
        # Keep the API JSON-compliant when every completed trade is profitable.
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if gross_profit > 0 else 0.0)

        # Avg Win / Avg Loss
        avg_win  = round(winning_pl.mean(), 2) if len(winning_pl) > 0 else 0.0
        avg_loss = round(losing_pl.mean(),  2) if len(losing_pl)  > 0 else 0.0
        win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (None if avg_win > 0 else 0.0)

        # Expectancy (expected $ per trade)
        wr = win_rate / 100
        expectancy = round((wr * avg_win) - ((1 - wr) * avg_loss), 2)

        # Sharpe Ratio (annualised, using per-trade returns)
        sharpe = 0.0
        if len(returns) > 1 and returns.std() > 0:
            sharpe = round((returns.mean() / returns.std()) * np.sqrt(252), 2)

        # Sortino Ratio (only penalises downside volatility)
        sortino = 0.0
        downside = returns[returns < 0]
        if len(downside) > 1 and downside.std() > 0:
            sortino = round((returns.mean() / downside.std()) * np.sqrt(252), 2)

        # Calmar Ratio (annualised return / max drawdown)
        calmar = 0.0
        period_days = max((self.end_date - self.start_date).days, 1)
        annualised_roi = roi * (365 / period_days)
        if max_dd > 0:
            calmar = round(annualised_roi / max_dd, 2)

        # Avg Hold Time (hours)
        avg_hold_hours = 0.0
        try:
            entry_times = pd.to_datetime(df_trades['entry_time'])
            exit_times  = pd.to_datetime(df_trades['exit_time'])
            hold_durations = (exit_times - entry_times).dt.total_seconds() / 3600
            avg_hold_hours = round(hold_durations.mean(), 1)
        except Exception:
            pass

        # Win / Loss Streak
        max_win_streak = max_loss_streak = cur_win = cur_loss = 0
        for pl_val in df_trades['pl']:
            if pl_val > 0:
                cur_win += 1; cur_loss = 0
            else:
                cur_loss += 1; cur_win = 0
            max_win_streak  = max(max_win_streak,  cur_win)
            max_loss_streak = max(max_loss_streak, cur_loss)

        # Avg win/loss as percentage
        avg_win_pct  = round(df_trades[df_trades['pl'] > 0]['pl_pct'].mean(),  2) if len(winning_pl) > 0 else 0.0
        avg_loss_pct = round(df_trades[df_trades['pl'] <= 0]['pl_pct'].mean(), 2) if len(losing_pl)  > 0 else 0.0
        # ────────────────────────────────────────────────────────────────────────

        # Preserve every tested OHLCV bar so the result chart retains the
        # selected timeframe. Only the equity overlay is downsampled.
        total_bars = len(self.df)
        equity_step = max(1, total_bars // 500)
        candles = []
        equity_curve_downsampled = []
        for i in range(total_bars):
            row = self.df.iloc[i]
            ts = self.df.index[i]
            try:
                unix_time = self._to_unix(ts)
                o = float(row.get('Open', row.get('open', 0)))
                h = float(row.get('High', row.get('high', 0)))
                l = float(row.get('Low', row.get('low', 0)))
                c = float(row.get('Close', row.get('close', 0)))
                v = int(row.get('Volume', row.get('volume', 0)))
                if o > 0 and c > 0:
                    digits = 8 if "USD" in self.ticker.upper() else 4
                    candles.append({
                        "time": unix_time,
                        "open": round(o, digits),
                        "high": round(h, digits),
                        "low": round(l, digits),
                        "close": round(c, digits),
                        "volume": v,
                        **_chart_indicator_fields(row, digits, self.enabled_indicators, self.buy_mode, self.sell_mode),
                    })
                    
                    # Grab perfectly aligned equity point
                    eq_idx = i - 30
                    if 0 <= eq_idx < len(self.equity_history):
                        eq_val = self.equity_history[eq_idx]['equity']
                    else:
                        eq_val = self.initial_capital
                        
                    if i % equity_step == 0 or i == total_bars - 1:
                        equity_curve_downsampled.append({
                            "time": self._to_utc_iso(ts),
                            "equity": eq_val
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
                "period_days": (self.end_date - self.start_date).days,
                "start_date_utc": self.df.index[0].isoformat(),
                "end_date_utc": self.df.index[-1].isoformat(),
                "data_source": self.data_source,
                "initial_capital": self.initial_capital,
                "final_equity": round(self.equity, 2),
                "total_pl": round(total_pl, 2),
                "roi_pct": round(roi, 2),
                "buy_hold_equity": round(buy_hold_equity, 2),
                "buy_hold_roi_pct": round(buy_hold_roi, 2),
                "win_rate_pct": round(win_rate, 2),
                "total_trades": len(self.trades),
                "profitable_trades": len(df_trades[df_trades['pl'] > 0]),
                "losing_trades": len(df_trades[df_trades['pl'] <= 0]),
                "avg_trade_pl": round(df_trades['pl'].mean(), 2),
                "best_trade": round(df_trades['pl'].max(), 2),
                "worst_trade": round(df_trades['pl'].min(), 2),
                "max_drawdown_pct": round(max_dd, 2),
                # ── Professional Metrics ──
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
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "expectancy": expectancy,
                "annualised_roi_pct": round(annualised_roi, 2),
            },
            "equity_curve": equity_curve_downsampled,
            "trades": self.trades[::-1],
            "candles": candles,
        }
