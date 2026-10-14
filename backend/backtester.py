import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import uuid
from typing import Dict, List, Optional
import asyncio
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_rsi_signal, _generate_signals
from swing_strategy import build_entry_signal, build_swing_exit
from trader import get_confluence_decision
import config as global_config
from indicator_catalog import EXTENSION_CHART_FIELDS, EXTENSION_CHART_GROUPS
from user_config import get_user_config
from prior_day_high_low import dynamic_high_low_exit_levels, prior_day_levels_from_history, resolve_high_low_buffer_pct
from trade_guards import is_stop_exit

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
        "sma_secondary": _safe_chart_level(row, "SMA_Secondary", digits),
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
        "cdl_name": str(row.get("cdl_name", "Neutral")),
        "cdl_bullish": _safe_chart_bool(row, "cdl_bullish"),
        "cdl_bearish": _safe_chart_bool(row, "cdl_bearish"),
        "cp_name": str(row.get("CP_Name", "Neutral")),
        "cp_bullish": _safe_chart_bool(row, "CP_Bullish"),
        "cp_bearish": _safe_chart_bool(row, "CP_Bearish"),
        "cp_quality": _safe_chart_level(row, "CP_Quality", 0),
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
        "SMA": {"sma", "sma_secondary"},
        "VWAP": {"vwap"},
        "Auto-AVWAP": {"auto_avwap"},
        "Supertrend": {"supertrend", "supertrend_up"},
        "Bollinger": {"boll_upper", "boll_middle", "boll_lower"},
        "RSI": {"rsi"},
        "MACD": {"macd_line", "macd_signal", "macd_hist"},
        "ADX Trend": {"adx", "di_plus", "di_minus"},
        "Mystic Pulse": {"mystic_bull", "mystic_bear"},
        "Volume Profile": {"volume_profile_poc", "volume_profile_vah", "volume_profile_val", "volume_profile_bullish", "volume_profile_bearish"},
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
    def __init__(self, ticker, timeframe, start_date, end_date, initial_capital=1000.0, threshold=5, sell_threshold=3, enabled_indicators=None, risk_per_trade=0.02, max_pos_pct=0.25, ext_hours=True, buy_mode="indicator", custom_buy_price=0.0, sell_mode="indicator", atr_stop_multiplier=2.0, atr_trail_multiplier=3.0, atr_take_profit_multiplier=4.0, enable_atr_trail=True, ai_autopilot=False, ai_interval=10, ai_model="llama-3.3-70b-versatile", enable_cooldown=True, cooldown_minutes=None, regular_stop_loss_pct=0.05, regular_take_profit_pct=0.10, regular_trailing_pct=0.03, enable_pct_trail=True, cancel_event=None, entry_atr_multiplier=None, entry_pct_move=None, entry_atr_style="pullback_reclaim", entry_pct_style="dip_reclaim", swing_entry_mode="bounce_retest", swing_entry_lookback_days=20, swing_exit_lookback_days=20, market_session="Regular", allocation_mode="fixed", trade_amount=None, custom_stop_loss_price=0.0, custom_take_profit_price=0.0, enable_signal_reset=True, enable_smart_reentry=False, smart_reentry_type="RSI", smart_reentry_rsi_call_max=70.0, smart_reentry_rsi_put_min=30.0, smart_reentry_vwap_call_max_dist_pct=0.5, smart_reentry_vwap_put_max_dist_pct=0.5, enable_risk=True, daily_high_low_buffer_pct=None, trade_direction_mode="long", max_completed_trades=None, indicator_overrides=None, post_stop_stabilization_bars=3, max_consecutive_losses=2, max_daily_loss_pct=0.0, enable_liquidity_filter=True, min_relative_volume=0.10, continuous_grid_settings=None, strategy_preset=None, strategy_preset_variant=None, allocation_settings=None, session_settings=None):
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
        self.continuous_grid_settings = dict(continuous_grid_settings or {})
        self.continuous_grid_settings.update({
            "buy_mode": self.buy_mode,
            "sell_mode": self.sell_mode,
            "allocation_mode": self.allocation_mode,
            "amount": self.trade_amount,
            "compound_amount": self.current_trade_amount,
        })
        pct_grid = bool(
            self.continuous_grid_settings.get("grid_continuous_mode")
            and self.continuous_grid_settings.get("exit_grid_continuous_mode")
            and self.buy_mode in {"percentage", "hybrid_pct"}
            and self.sell_mode in {"percentage", "hybrid_pct"}
        )
        atr_grid = bool(
            self.continuous_grid_settings.get("atr_grid_continuous_mode")
            and self.continuous_grid_settings.get("exit_atr_grid_continuous_mode")
            and self.buy_mode in {"atr", "hybrid_atr"}
            and self.sell_mode in {"sltp", "hybrid", "atr", "hybrid_atr"}
        )
        self.adaptive_atr_grid = atr_grid
        self.adaptive_continuous_grid = bool(
            (pct_grid or atr_grid) and str(trade_direction_mode or "long").lower() == "long"
        )
        if self.adaptive_continuous_grid:
            # Fixed percentage SL/TP/trailing controls are intentionally
            # replaced by the adaptive sell ladder in both live and backtest.
            self.enable_pct_trail = False
            if self.adaptive_atr_grid:
                self.enable_atr_trail = False
        self.cancel_event = cancel_event
        self._last_ai_decision = None
        self.last_exit_time = None
        self.signal_locked_direction = None
        self.post_stop_stabilization_bars = max(0, int(post_stop_stabilization_bars or 0))
        self.max_consecutive_losses = max(0, int(max_consecutive_losses or 0))
        self.max_daily_loss_pct = max(0.0, float(max_daily_loss_pct or 0.0))
        self.enable_liquidity_filter = bool(enable_liquidity_filter)
        self.min_relative_volume = max(0.0, float(min_relative_volume or 0.0))
        self.stop_exit_state = None
        self.consecutive_losses = 0
        self.daily_realized_pl = 0.0
        self.guard_session_date = None
        self.current_bar_index = 0
        # Signal entries of the indicator/structure modes are decided on a
        # completed bar and filled at the next bar's open (like live bots).
        self.pending_entry = None
        
        self.equity = initial_capital
        self.position = None 
        self.trades = []
        self.equity_history = []
        self.df = None
        self.volume_profile = {}
        self.data_source = "yahoo"
        self.max_completed_trades = int(max_completed_trades) if max_completed_trades else None
        self.overtrade_limit_hit = False
        self.data_feed = None
        self.data_quality = "OK"
        self.price_adjustment = "split"
        self.equity_history_offset = 30
        self.strategy_preset = None
        self.strategy_preset_variant = None
        self.allocation_settings = dict(allocation_settings or {})
        self.session_settings = dict(session_settings or {})
        if strategy_preset:
            import strategy_presets as _sp
            if _sp.get_preset(strategy_preset) is not None:
                self.strategy_preset = _sp.get_preset(strategy_preset).id
                self.strategy_preset_variant = _sp.get_variant(_sp.get_preset(strategy_preset), strategy_preset_variant).id
                # The strategy owns entries and exits: manual Entry/Exit, indicators,
                # thresholds, signal reset and smart re-entry do not apply.
                self.buy_mode = self.sell_mode = _sp.PRESET_MODE
                self.enabled_indicators = []
                self.threshold = self.sell_threshold = 0
                self.enable_signal_reset = False
                self.enable_smart_reentry = False
                self.enable_cooldown = False
                self.trade_direction_mode = "long"
                self.equity_history_offset = 0

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
        """Vectorize the common RSI/Bollinger/VWAP signal set for fast backtests."""
        enabled = set(self.enabled_indicators or [])
        if not enabled or not enabled.issubset({"RSI", "Bollinger", "VWAP", "Auto-AVWAP"}):
            return None

        cfg = get_user_config()
        rsi_os = float(self.indicator_settings.get("RSI_OVERSOLD", cfg.get("RSI_OVERSOLD", 30)))
        rsi_ob = float(self.indicator_settings.get("RSI_OVERBOUGHT", cfg.get("RSI_OVERBOUGHT", 70)))
        rsi_period = self.indicator_settings.get("RSI_PERIOD", cfg.get("RSI_PERIOD", 14))
        close = self.df["Close"].astype(float)
        prev_close = close.shift(1)
        rsi = self.df["RSI"].astype(float)
        boll_lo = self.df["BOLL_Lower"].astype(float)
        boll_hi = self.df["BOLL_Upper"].astype(float)
        vwap = self.df["VWAP"].astype(float)
        prev_vwap = vwap.shift(1).fillna(vwap)
        auto_avwap = self.df["Auto_AVWAP"].astype(float) if "Auto_AVWAP" in self.df else close
        prev_auto_avwap = auto_avwap.shift(1).fillna(auto_avwap)
        bull_candle = self.df.get("cdl_bullish", pd.Series(False, index=self.df.index)).fillna(False).astype(bool)
        is_near_vwap = ((close - vwap).abs() / vwap.replace(0, np.nan)).lt(0.015).fillna(False)

        cache = []
        for idx in range(len(self.df)):
            signals = {}
            price = float(close.iloc[idx])
            if "RSI" in enabled:
                rsi_val = float(rsi.iloc[idx])
                previous_rsi = float(rsi.iloc[idx - 1]) if idx > 0 and pd.notna(rsi.iloc[idx - 1]) else np.nan
                signals["RSI"] = _generate_rsi_signal(
                    rsi_val,
                    previous_rsi,
                    oversold=rsi_os,
                    overbought=rsi_ob,
                    period=rsi_period,
                )
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
                    f"Session VWAP Breakout ({vw:.1f})" if crossed_up
                    else f"Bounce off Session VWAP ({vw:.1f})" if bounced
                    else f"Below Session VWAP ({vw:.1f})" if price < vw
                    else f"Holding above Session VWAP ({vw:.1f})"
                )
                signals["VWAP"] = {"signal": vwap_signal, "reason": vwap_reason, "weight": 1}
            if "Auto-AVWAP" in enabled:
                av = float(auto_avwap.iloc[idx])
                prev_av = float(prev_auto_avwap.iloc[idx])
                crossed_up = price >= av and prev_c < prev_av
                bounced = price >= av and bool((abs(price - av) / av if av else 0) < 0.015) and bool(bull_candle.iloc[idx])
                auto_signal = "BULLISH" if crossed_up or bounced else "BEARISH" if price < av else "NEUTRAL"
                auto_reason = (
                    f"Auto-AVWAP Breakout ({av:.1f})" if crossed_up
                    else f"Bounce off Auto-AVWAP ({av:.1f})" if bounced
                    else f"Below Auto-AVWAP ({av:.1f})" if price < av
                    else f"Holding above Auto-AVWAP ({av:.1f})"
                )
                signals["Auto-AVWAP"] = {"signal": auto_signal, "reason": auto_reason, "weight": 1}
            cache.append(signals)
        return cache

    def run(self, precomputed_df=None, precomputed_signal_cache=None, fast_metrics=False):
        # Force naive datetimes to match pandas index
        if hasattr(self.start_date, "tzinfo") and self.start_date.tzinfo is not None:
            self.start_date = self.start_date.replace(tzinfo=None)
        if hasattr(self.end_date, "tzinfo") and self.end_date.tzinfo is not None:
            self.end_date = self.end_date.replace(tzinfo=None)

        if self.strategy_preset:
            import strategy_presets as _sp
            if _sp.is_allocation(self.strategy_preset):
                return self._run_allocation_strategy()
            if _sp.is_schedule(self.strategy_preset):
                return self._run_session_strategy()
            return self._run_strategy_preset(precomputed_df=precomputed_df, fast_metrics=fast_metrics)

        if precomputed_df is not None:
            self.df = precomputed_df.loc[self.start_date:self.end_date]
        else:
            # 1. Fetch Data with padding for indicator warmup
            from datetime import timedelta
            warmup_days = 100
            padded_start = self.start_date - timedelta(days=warmup_days)
            raw_df = get_historical_data(self.ticker, self.timeframe, padded_start, self.end_date, force_yahoo=False)

            if raw_df is None or raw_df.empty:
                return {"error": "No data found for the selected range."}
            self._capture_provenance(raw_df)

            # Filter for Regular Hours (RTH) if stocks & self.ext_hours is False and not daily
            clean_ticker = self.ticker.upper().replace("/", "")
            is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
            if not is_crypto and not self.ext_hours and self.timeframe != "1Day":
                try:
                    time_index = pd.DatetimeIndex(raw_df.index)
                    if time_index.tz is None:
                        time_index = time_index.tz_localize(
                            "America/New_York",
                            ambiguous="infer",
                            nonexistent="shift_forward",
                        )
                    else:
                        time_index = time_index.tz_convert("America/New_York")
                    time_idx = time_index.time
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
            self.volume_profile = result.get("volume_profile") or {}
            
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
        self.pending_entry = None
        self._equity_recorded_index = None
        for i in range(len(self.df)):
            self.current_bar_index = i
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

            # Exit paths `continue` before the per-bar equity record below. Record a
            # skipped bar here so the history keeps exactly one point per bar and the
            # equity chart (matched to candles by position) stays aligned.
            if not fast_metrics and i > 30 and self._equity_recorded_index != i - 1:
                self.equity_history.append({
                    'time': self.df.index[i - 1],
                    'equity': self.equity + (self._get_unrealized_pl(current_price=prev_bar['Close']) if self.position else 0)
                })
                self._equity_recorded_index = i - 1

            if self.pending_entry is not None:
                pending = self.pending_entry
                self.pending_entry = None
                if not self.position:
                    self._fill_pending_entry(pending, current_bar, timestamp)

            if self.adaptive_continuous_grid:
                from engine import _evaluate_continuous_grid
                grid_decision = _evaluate_continuous_grid(
                    self.continuous_grid_settings,
                    current_price=float(current_price),
                    has_position=bool(self.position),
                    average_entry_price=float(self.position.get("entry_price", 0.0)) if self.position else 0.0,
                    position_qty=float(self.position.get("qty", 0.0)) if self.position else 0.0,
                    current_atr=float(current_bar.get("ATR", 0.0) or 0.0),
                    price_history=self.df.iloc[max(0, i - 79):i + 1],
                )
                if grid_decision.get("action") == "BUY":
                    if self._execute_continuous_grid_buy(current_bar, timestamp, grid_decision):
                        continue
                elif grid_decision.get("action") == "SELL" and self.position:
                    if self._execute_continuous_grid_sell(current_bar, timestamp, grid_decision):
                        continue
            
            # Check for SL / TP if in position
            if self.position:
                current_price = current_bar['Close']
                precision = 6 if current_price < 1.0 else 2
                position_side = str(self.position.get("side", "long")).lower()
                if position_side not in {"long", "short"}:
                    position_side = "long"

                # ── ATR / AI-based hard stops (existing modes) ──────────────────
                if self.sell_mode in ["sltp", "hybrid", "trend_rider", "ai_autopilot"] and not self.adaptive_atr_grid:
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
                elif self.sell_mode in ['percentage', 'hybrid_pct'] and not self.adaptive_continuous_grid:
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
                    ema_primary = current_bar.get("EMA_Fast", np.nan)
                    ema_secondary = current_bar.get("EMA_Slow", np.nan)
                    prev_ema_primary = prev_bar.get("EMA_Fast", np.nan)
                    prev_ema_secondary = prev_bar.get("EMA_Slow", np.nan)
                    try:
                        ema_primary_period = max(1, min(500, int(float(self.indicator_settings.get("EMA_FAST", 9)))))
                        ema_secondary_period = max(1, min(500, int(float(self.indicator_settings.get("EMA_SLOW", 21)))))
                    except (TypeError, ValueError):
                        ema_primary_period, ema_secondary_period = 9, 21
                    if ema_primary_period <= ema_secondary_period:
                        ema_f, ema_s = ema_primary, ema_secondary
                        prev_ema_f, prev_ema_s = prev_ema_primary, prev_ema_secondary
                        ema_fast_period, ema_slow_period = ema_primary_period, ema_secondary_period
                    else:
                        ema_f, ema_s = ema_secondary, ema_primary
                        prev_ema_f, prev_ema_s = prev_ema_secondary, prev_ema_primary
                        ema_fast_period, ema_slow_period = ema_secondary_period, ema_primary_period
                    st_bull = current_bar.get("Supertrend_Trend", True)
                    sma_val = current_bar.get("SMA", np.nan)
                    try:
                        sma_break = np.isfinite(float(sma_val)) and current_price < float(sma_val)
                    except (TypeError, ValueError):
                        sma_break = False
                    try:
                        sma_period = max(1, min(500, int(float(self.indicator_settings.get("SMA_PERIOD", 200)))))
                    except (TypeError, ValueError):
                        sma_period = 200
                    
                    # Entry: Golden Cross (or already crossed) AND Supertrend Bullish
                    golden_cross = (ema_f > ema_s) and (prev_ema_f <= prev_ema_s)
                    if golden_cross and st_bull:
                        action = "BUY"
                        reason = f"Trend Rider Entry (EMA {ema_fast_period}/{ema_slow_period} Golden Cross + Supertrend)"
                    elif bullish_dominates:
                        action = "BUY"
                        names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                        reason = f"Trend Rider Entry: {analysis['bullish_count']} signals ({', '.join(names)})"

                    # Exit: Trend Break (Death Cross or below SMA)
                    death_cross = (ema_f < ema_s) and (prev_ema_f >= prev_ema_s)
                    if death_cross or sma_break:
                        action = "SELL"
                        reason = f"Trend Rider Exit: Trend Broken (Death Cross or < SMA {sma_period})"

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

                if self.enable_signal_reset and not self.position and self.signal_locked_direction == "BUY" and action != "BUY" and analysis['bullish_count'] <= 0:
                    self.signal_locked_direction = None
                if self.enable_signal_reset and not self.position and self.signal_locked_direction == "SELL" and action != "SELL" and analysis['bearish_count'] <= 0:
                    self.signal_locked_direction = None

                if action in {'BUY', 'SELL'} and not self.position:
                    entry_side = "long" if action == "BUY" else "short"
                    allowed, block_reason = self._entry_guard_allows(action, current_bar, current_price, timestamp, side=entry_side)
                    if not allowed:
                        action = "HOLD"
                        reason = block_reason
                    elif action == 'BUY' and self._direction_allowed("long"):
                        self._queue_entry(current_bar, timestamp, reason, entry_side, i)
                    elif action == 'SELL' and self._direction_allowed("short"):
                        self._queue_entry(current_bar, timestamp, reason, entry_side, i)
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
                self._equity_recorded_index = i

        if not fast_metrics and len(self.df) > 30 and self._equity_recorded_index != len(self.df) - 1:
            last_close = self.df.iloc[-1]['Close']
            self.equity_history.append({
                'time': self.df.index[-1],
                'equity': self.equity + (self._get_unrealized_pl(current_price=last_close) if self.position else 0)
            })
            self._equity_recorded_index = len(self.df) - 1

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

    # ── Data provenance ─────────────────────────────────────────────────────
    def _capture_provenance(self, frame):
        attrs = getattr(frame, "attrs", {}) or {}
        self.data_source = str(attrs.get("data_source") or self.data_source or "yahoo").lower()
        self.data_feed = attrs.get("feed")
        self.data_quality = str(attrs.get("data_quality") or "OK").upper()
        self.price_adjustment = attrs.get("adjustment") or "split"

    def _provenance_summary(self):
        return {
            "data_feed": self.data_feed,
            "data_quality": self.data_quality,
            "price_adjustment": self.price_adjustment,
        }

    # ── Strategy presets ────────────────────────────────────────────────────
    @staticmethod
    def _regular_hours_only(df):
        """Keep bars that open inside the 09:30-16:00 ET regular session."""
        index = pd.DatetimeIndex(df.index)
        local = index.tz_localize("America/New_York", ambiguous="infer", nonexistent="shift_forward") if index.tz is None else index.tz_convert("America/New_York")
        minutes = local.hour * 60 + local.minute
        return df[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)]

    def _sizing_equity(self):
        """Capital the entry caps (cash, max position, risk) are measured against.

        Fixed: every buy uses the same Amount, so caps are measured against the starting capital
        and a losing streak can never shrink later buys (the simulated account is assumed to fund
        the Amount; ROI is still measured on the starting capital). Compound: the account itself,
        because the next buy is the Amount plus realized P/L."""
        return float(self.initial_capital) if self.allocation_mode == "fixed" else float(self.equity)

    def _reset_state(self):
        """Fresh simulation state (used for the variant-comparison clones)."""
        self.equity = self.initial_capital
        self.position = None
        self.trades = []
        self.equity_history = []
        self.df = None
        self.current_trade_amount = self.trade_amount
        self.last_exit_time = None
        self.stop_exit_state = None
        self.consecutive_losses = 0
        self.daily_realized_pl = 0.0
        self.guard_session_date = None
        self.current_bar_index = 0
        self.overtrade_limit_hit = False

    def _preset_open_position(self, qty, price, timestamp, stop, reason, signal_time):
        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': stop,
            'take_profit': 0.0,
            'initial_stop_loss': stop,
            'initial_take_profit': 0.0,
            'side': 'long',
            'reason': reason,
            'signal_time': signal_time,
        }
        print(f"[{self._to_central_str(timestamp)}] BUY  {self.ticker} @ ${price:.2f} | Stop: ${stop:.2f} | Reason: {reason}")

    def _preset_exit(self, bar, timestamp, reason, price, exit_signal_time, stop=None):
        entry_signal_time = (self.position or {}).get('signal_time')
        self._exit_trade(bar, timestamp, reason, exit_price_override=price, exit_stop_price=stop)
        if self.trades:
            self.trades[-1]["entry_signal_time"] = entry_signal_time
            self.trades[-1]["exit_signal_time"] = exit_signal_time

    def _run_strategy_preset(self, precomputed_df=None, fast_metrics=False, _compare=True):
        """Simulate a strategy preset on completed daily bars.

        Signals are decided on the close of a completed bar and filled at the
        next bar's open (live trading cannot act before the candle closes).
        Stocks and ETFs, long-only. Fees and slippage are not modelled.
        """
        import copy
        from datetime import timedelta
        import strategy_presets as sp

        preset = sp.get_preset(self.strategy_preset)
        if preset is None:
            return {"error": "Unknown strategy preset."}
        variant = sp.get_variant(preset, self.strategy_preset_variant)
        errors = sp.validate_strategy_preset(
            {"strategy_preset_enabled": True, "strategy_preset": preset.id},
            ticker=self.ticker, is_crypto=self.is_crypto,
        )
        if errors:
            return {"error": errors[0]}
        if self.timeframe not in preset.allowed_timeframes:
            return {"error": f"{preset.label} runs on {', '.join(preset.allowed_timeframes)} (selected {self.timeframe})."}

        # Pad by the longest warmup of any variant so every variant sees the same history.
        padding_days = max(sp.preset_backtest_padding_days(preset.id, v.id, self.timeframe) for v in preset.variants)
        padded_start = self.start_date - timedelta(days=padding_days)
        raw_df = precomputed_df
        if raw_df is None or len(raw_df) == 0 or raw_df.index[0] > padded_start + timedelta(days=7):
            raw_df = get_historical_data(self.ticker, self.timeframe, padded_start, self.end_date, force_yahoo=False)
        if raw_df is None or raw_df.empty:
            return {"error": "No data found for the selected range."}
        self._capture_provenance(raw_df)
        if self.data_quality == "DEGRADED":
            return {"error": "Market data failed the split-adjustment check, so the strategy was not run. Try again later or switch the data source."}

        prepared = getattr(self, "_preset_prepared", None)
        if prepared is None or prepared["raw"] is not raw_df:
            source_df = raw_df
            if self.timeframe != "1Day" and not self.is_crypto and str(self.market_session or "Regular").lower() == "regular":
                source_df = self._regular_hours_only(raw_df)
                if source_df.empty:
                    return {"error": "No regular-session bars are available for the selected range."}
                source_df.attrs.update(getattr(raw_df, "attrs", {}) or {})
            bars = sp.completed_bars(source_df, self.timeframe)
            if bars.empty:
                return {"error": "No completed bars are available for the selected range."}
            bars.attrs.update(getattr(raw_df, "attrs", {}) or {})
            calc_settings = {
                **(self.indicator_settings or {}),
                # VWAP/Auto-AVWAP are computed (not placeholders equal to Close) because the post-stop
                # reclaim guard compares the signal bar's close with them; they cast no strategy vote.
                "enabled_indicators": ["VWAP", "Auto-AVWAP"],
                "buy_mode": "indicator",
                "sell_mode": "indicator",
            }
            result = calculate_indicators_for_df(bars, self.timeframe, self.ticker, settings=calc_settings)
            if not result:
                return {"error": "Failed to prepare price data."}
            # Variant-comparison clones reuse this prepared frame instead of recomputing it.
            prepared = {"raw": raw_df, "df": result["df"], "volume_profile": result.get("volume_profile") or {}}
            self._preset_prepared = prepared
        self.volume_profile = prepared["volume_profile"]
        frame = sp.compute_strategy_frame(prepared["df"], preset.id, variant.id)
        self.df = frame.loc[self.start_date:self.end_date]
        if self.df.empty:
            return {"error": "No data available in the requested range after filtering."}

        rows = len(self.df)
        opens = self.df["Open"].to_numpy(dtype=float)
        highs = self.df["High"].to_numpy(dtype=float)
        lows = self.df["Low"].to_numpy(dtype=float)
        closes = self.df["Close"].to_numpy(dtype=float)
        atrs = self.df["SP_atr"].to_numpy(dtype=float)
        entries = self.df["SP_entry"].to_numpy(dtype=bool)
        exits = self.df["SP_exit_cond"].to_numpy(dtype=bool)
        ready = self.df["SP_ready"].to_numpy(dtype=bool)

        risk_fraction = float(self.risk_per_trade or 0.0)
        risk_fraction = risk_fraction / 100.0 if risk_fraction > 1.0 else risk_fraction
        if not self.enable_risk:
            risk_fraction = 0.0
        max_position_fraction = float(self.max_pos_pct) if self.enable_risk else 1.0
        max_position_fraction = max_position_fraction / 100.0 if max_position_fraction > 1.0 else max_position_fraction
        stop_mult = preset.protective_stop_atr_mult

        pending = None
        bars_in_position = 0
        blocked_entries = []
        for i in range(rows):
            self.current_bar_index = i
            if self.cancel_event and self.cancel_event.is_set():
                return {"error": "Backtest cancelled by user"}
            bar = self.df.iloc[i]
            timestamp = self.df.index[i]
            self._refresh_guard_session(timestamp)

            # 1. fill the order decided on the previous completed bar at this open
            if pending is not None:
                if pending["action"] == "SELL" and self.position:
                    self._preset_exit(bar, timestamp, pending["reason"], float(opens[i]), pending["signal_time"])
                elif pending["action"] == "BUY" and not self.position:
                    # The order fills at this bar's open, before this bar's own close or volume
                    # exist. Guards (post-stop structure/VWAP, relative volume) must therefore
                    # judge the completed signal bar, never the fill bar.
                    fill_index, self.current_bar_index = self.current_bar_index, pending["index"]
                    try:
                        allowed, why = self._entry_guard_allows("BUY", pending["bar"], float(opens[i]), timestamp)
                    finally:
                        self.current_bar_index = fill_index
                    if not allowed:
                        blocked_entries.append({"time": pending["signal_time"], "reason": why})
                    else:
                        base_amount = self.current_trade_amount if self.allocation_mode == "compound" else self.trade_amount
                        sizing = sp.preset_position_size(
                            equity=self._sizing_equity(), price=float(opens[i]), atr=pending["atr"], base_amount=base_amount,
                            risk_fraction=risk_fraction, max_position_fraction=max_position_fraction,
                            stop_atr_mult=stop_mult, fractional=True,
                        )
                        if sizing["qty"] > 0:
                            stop = float(opens[i]) - stop_mult * pending["atr"]
                            self._preset_open_position(
                                sizing["qty"], float(opens[i]), timestamp, stop,
                                f"{preset.label}: entry signal ({sizing['limit']} sized)", pending["signal_time"],
                            )
                pending = None

            # 2. protective stop against this bar's low (a gap below the stop fills at the open)
            if self.position:
                stop = float(self.position["stop_loss"])
                if lows[i] <= stop:
                    fill = min(stop, float(opens[i]))
                    self._preset_exit(
                        bar, timestamp, f"Stop Loss Hit (${stop:.2f}, {stop_mult:g}×ATR protective stop)",
                        fill, None, stop=stop,
                    )

            # 3. decide on this completed bar for the next open (shared decision rule)
            if ready[i]:
                action = sp.preset_action(bool(self.position), bool(entries[i]), bool(exits[i]))
                if action != "HOLD":
                    signal_time = self._to_utc_iso(timestamp)
                    pending = {
                        "action": action,
                        "bar": bar,
                        "index": i,
                        "atr": float(atrs[i]),
                        "signal_time": signal_time,
                        "reason": (f"Strategy exit: {preset.label} exit condition met"
                                   if action == "SELL" else f"{preset.label} entry"),
                    }
            if self.position:
                bars_in_position += 1
            if not fast_metrics or getattr(self, "_record_equity_in_fast_mode", False):
                self.equity_history.append({
                    'time': timestamp,
                    'equity': self.equity + (self._get_unrealized_pl(current_price=float(closes[i])) if self.position else 0),
                })
            if self.overtrade_limit_hit:
                break

        if self.position:
            self._preset_exit(self.df.iloc[-1], self.df.index[-1], 'End of Backtest', float(closes[-1]), None)

        results = self._calculate_fast_metrics() if fast_metrics else self._calculate_metrics()
        summary = results.get("summary")
        if summary is None:
            return results

        first_ready = next((self.df.index[i] for i in range(rows) if ready[i]), None)
        info = {
            "id": preset.id,
            "variant_id": variant.id,
            "label": preset.label,
            "variant_label": variant.label.replace(" · Recommended", ""),
            "direction": "long_only",
            "catalog_version": sp.CATALOG_VERSION,
            "fill_model": "next_bar_open",
            "execution_note": f"Signals are decided on a completed {self.timeframe} bar and filled at the next bar's open. No fees or slippage are modelled.",
            "untested_note": sp.untested_settings_note(preset.id, self.timeframe, self.market_session, variant.id),
            "protective_stop_atr_mult": stop_mult,
            "warmup_bars": sp.warmup_bars(preset.id, variant.id),
            "first_ready_date": first_ready.isoformat() if first_ready is not None else None,
            "exposure_pct": round(100.0 * bars_in_position / rows, 2) if rows else 0.0,
            "low_sample_warning": len(self.trades) < 30,
            "blocked_entries": blocked_entries[:20],
            "pending_signal": ({"action": pending["action"], "signal_time": pending["signal_time"]} if pending else None),
        }
        if first_ready is None:
            info["warning"] = (f"Not enough history: {sp.warmup_bars(preset.id, variant.id)} completed bars are required "
                               f"before {preset.label} can signal.")
        if not self.trades:
            summary["no_trade_reason"] = info.get("warning") or (
                f"{preset.label} produced no completed trade in this window. Entries need a fresh signal on a completed bar."
            )
        if _compare and not fast_metrics:
            rows_out = []
            for candidate in preset.variants:
                if candidate.id == variant.id:
                    source = summary
                else:
                    clone = copy.copy(self)
                    clone._reset_state()
                    clone.strategy_preset_variant = candidate.id
                    clone._record_equity_in_fast_mode = True
                    other = clone._run_strategy_preset(precomputed_df=raw_df, fast_metrics=True, _compare=False)
                    source = (other or {}).get("summary") or {}
                rows_out.append({
                    "id": candidate.id,
                    "label": candidate.label.replace(" · Recommended", ""),
                    "selected": candidate.id == variant.id,
                    "roi_pct": source.get("roi_pct"),
                    "annualised_roi_pct": source.get("annualised_roi_pct"),
                    "max_drawdown_pct": source.get("max_drawdown_pct"),
                    "calmar_ratio": source.get("calmar_ratio"),
                    "total_trades": source.get("total_trades"),
                    "win_rate_pct": source.get("win_rate_pct"),
                    "profit_factor": source.get("profit_factor"),
                })
            info["variants"] = rows_out
        summary["strategy_preset"] = info
        print(f"[backtest] STRATEGY {preset.label} ({variant.id}) for {self.ticker}: "
              f"ROI {summary.get('roi_pct')}% | Trades {summary.get('total_trades')}")
        return results

    # ------------------------------------------------------------------ Malik allocation strategies
    @staticmethod
    def _title_ohlc(frame):
        rename = {c: c.capitalize() for c in frame.columns if str(c).lower() in ("open", "high", "low", "close", "volume")}
        return frame.rename(columns=rename)

    def _allocation_intraday_plan(self, signal, legs, days, exec_setting, closes):
        """Decision price at T and fill price after T from 15-minute bars (bars are stamped at their start).

        Decision = close of the last bar completed by T; fill = open of the first bar starting at or after T.
        Returns None when intraday data cannot be loaded for the signal or a leg."""
        import allocation_strategy as A
        from datetime import timedelta
        start = days[0] - timedelta(days=3)
        end = self.end_date
        bars = {}
        for sym in [signal] + [s for s in legs if s]:
            frame = get_historical_data(sym, "15Min", start, end, force_yahoo=False)
            if frame is None or frame.empty or frame.index[0] > days[0] + timedelta(days=5):
                return None
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
            bars[sym] = {c.lower(): frame[c] for c in frame.columns if c.lower() in ("open", "close")}
        rows = []
        for day in days:
            dt = A.decision_time(day.date(), exec_setting)
            if dt is None:
                continue
            T = pd.Timestamp(dt["at"].replace(tzinfo=None))
            floor = T.floor("15min")
            sig_bar = floor - pd.Timedelta(minutes=15)
            fill_bar = floor if floor == T else floor + pd.Timedelta(minutes=15)
            row = {"day": day, "T": T, "adjusted": dt["adjusted"],
                   "P": float(bars[signal]["close"].get(sig_bar, np.nan))}
            for key, sym in zip(("fill_bull", "fill_bear"), legs):
                row[key] = float(bars[sym]["open"].get(fill_bar, np.nan)) if sym else np.nan
            rows.append(row)
        return pd.DataFrame(rows).set_index("day")

    def _run_allocation_strategy(self):
        """Simulate a Malik leveraged-ETF allocation strategy (shared rules in allocation_strategy.py).

        The benchmark's regime is decided once a day at the execution time from completed daily closes
        plus the price at that time; the chosen ETF legs are rebalanced to their target % right after.
        No fees, slippage or dividends are modelled."""
        from datetime import timedelta
        import allocation_strategy as A
        import strategy_presets as sp

        preset = sp.get_preset(self.strategy_preset)
        settings = {**self.allocation_settings, "strategy_preset_variant": self.strategy_preset_variant}
        errors = A.validate_settings(preset.id, settings, self.ticker)
        if errors:
            return {"error": errors[0]}
        if self.timeframe != "1Day":
            return {"error": f"{preset.label} decides once a day; set the timeframe to 1Day."}
        direction = A.normalize_direction(self.strategy_preset_variant)
        legs = A.resolve_legs(preset.id, settings)
        alloc = A.normalized_settings(preset.id, settings)
        bull, bear_sel = legs["bull"], legs["bear"]
        bear_any = bear_sel or alloc["allocation_bear_etf"]  # loaded for the direction comparison
        exec_setting = alloc["allocation_exec_time"]

        padded_start = self.start_date - timedelta(days=int(A.WARMUP_BARS * 7 / 5) + 20)
        sig_raw = get_historical_data(self.ticker, "1Day", padded_start, self.end_date, force_yahoo=False)
        if sig_raw is None or sig_raw.empty:
            return {"error": f"No {self.ticker} data found for the selected range."}
        self._capture_provenance(sig_raw)
        if self.data_quality == "DEGRADED":
            return {"error": "Market data failed the split-adjustment check, so the strategy was not run. Try again later."}
        sig = sp.completed_bars(sig_raw, "1Day")
        sig = self._title_ohlc(sig)
        sig.index = pd.DatetimeIndex(sig.index).normalize()
        sig = sig[~sig.index.duplicated(keep="last")]
        closes = sig["Close"].astype(float)
        leg_daily = {}
        for sym in (bull, bear_any):
            frame = get_historical_data(sym, "1Day", self.start_date - timedelta(days=10), self.end_date, force_yahoo=False)
            if frame is None or frame.empty:
                return {"error": f"No {sym} data found for the selected range."}
            if str((frame.attrs or {}).get("data_quality", "OK")).upper() == "DEGRADED":
                return {"error": f"{sym} data failed the split-adjustment check, so the strategy was not run."}
            frame = self._title_ohlc(sp.completed_bars(frame, "1Day"))
            frame.index = pd.DatetimeIndex(frame.index).normalize()
            leg_daily[sym] = frame[~frame.index.duplicated(keep="last")]

        window = sig.loc[self.start_date:self.end_date]
        if window.empty:
            return {"error": "No data available in the requested range after filtering."}
        days = window.index

        model = "intraday"
        intraday = self._allocation_intraday_plan(self.ticker, (bull, bear_any), days, exec_setting, closes)
        if intraday is None:
            model = "next_open"
        if model == "intraday":
            provisional = intraday["P"]
            fill_day = pd.Series(intraday.index, index=intraday.index)
            fills_bull, fills_bear = intraday["fill_bull"], intraday["fill_bear"]
            decision_label = intraday["T"].dt.strftime("%Y-%m-%d %H:%M ET")
            adjusted_days = int(intraday["adjusted"].sum())
        else:
            provisional = window["Close"]
            nxt = pd.Series(list(days[1:]) + [pd.NaT], index=days)
            fill_day = nxt
            fills_bull = leg_daily[bull]["Open"].reindex(nxt.values).set_axis(days)
            fills_bear = leg_daily[bear_any]["Open"].reindex(nxt.values).set_axis(days)
            decision_label = pd.Series([f"{d.date()} close" for d in days], index=days)
            adjusted_days = 0

        max_frac = None
        if self.enable_risk and self.max_pos_pct:
            max_frac = float(self.max_pos_pct) / 100.0 if float(self.max_pos_pct) > 1.0 else float(self.max_pos_pct)
        marks = {s: leg_daily[s]["Close"] for s in leg_daily}

        def run(direction_, weights=None):
            hist = A.regime_history(closes, provisional, direction_)
            plan = pd.DataFrame({
                "fill_day": fill_day.reindex(hist.index).values,
                "decision_time": decision_label.reindex(hist.index).values,
                "regime": hist["regime"].values,
                "w_bull": (weights if weights is not None else hist["w_bull"]).values,
                "w_bear": hist["w_bear"].values if direction_ == A.DIRECTION_INVERSE else np.where(hist["regime"].isna(), np.nan, 0.0),
                "fill_bull": fills_bull.reindex(hist.index).values,
                "fill_bear": fills_bear.reindex(hist.index).values,
            }, index=hist.index)
            out = A.simulate(plan, marks, bull=bull, bear=(bear_sel or bear_any) if direction_ == A.DIRECTION_INVERSE else None,
                             initial_capital=float(self.initial_capital), allocation_mode=self.allocation_mode,
                             base_amount=self.trade_amount, max_position_fraction=max_frac, calendar=days)
            return hist, out

        hist, out = run(direction)
        if out["daily"].empty:
            return {"error": "The strategy produced no results for this range."}

        # Standard result shape: signal candles, average-cost round trips per leg, daily equity.
        self.df = window
        self.equity_history_offset = 0
        self.equity_history = [{"time": d, "equity": float(e)} for d, e in out["daily"]["equity"].items()]
        self.equity = float(out["daily"]["equity"].iloc[-1])
        trades = list(out["trades"])
        last_day = days[-1]
        for sym, leg in out["open_legs"].items():
            px = float(marks[sym].reindex([last_day]).ffill().iloc[0])
            trades.append({"symbol": sym, "entry_time": leg["opened_at"], "exit_time": last_day, "qty": leg["qty"],
                           "entry_price": leg["avg"], "exit_price": px, "entry_cost": leg["qty"] * leg["avg"],
                           "exit_value": leg["qty"] * px, "pl": (px - leg["avg"]) * leg["qty"],
                           "pl_pct": (px / leg["avg"] - 1) * 100 if leg["avg"] else 0.0, "reason": "End of Backtest"})
        self.trades = [{
            "ticker": t["symbol"], "entry_time": self._to_utc_iso(t["entry_time"]), "exit_time": self._to_utc_iso(t["exit_time"]),
            "qty": round(t["qty"], 4), "entry_price": round(t["entry_price"], 2), "exit_price": round(t["exit_price"], 2),
            "entry_cost": round(t["entry_cost"], 2), "exit_value": round(t["exit_value"], 2), "fees": 0.0,
            "pl": round(t["pl"], 2), "pl_pct": round(t["pl_pct"], 2), "reason": f"{t['symbol']}: {t['reason']}", "side": "long",
            "stop_loss": 0.0, "take_profit": 0.0, "initial_stop_loss": 0.0, "initial_take_profit": 0.0, "exit_stop_price": None,
            "entry_signal_time": None, "exit_signal_time": None,
        } for t in trades]
        results = self._calculate_metrics()
        summary = results.get("summary")
        if summary is None:
            return results

        def stats(curve):
            curve = curve.dropna()
            if len(curve) < 2 or curve.iloc[0] <= 0:
                return {}
            years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-9)
            dd = float((curve / curve.cummax() - 1).min() * 100)
            cagr = ((curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1) * 100
            return {"roi_pct": round((curve.iloc[-1] / curve.iloc[0] - 1) * 100, 2), "annualised_roi_pct": round(cagr, 2),
                    "max_drawdown_pct": round(dd, 2), "mar": round(cagr / abs(dd), 2) if dd < 0 else None}

        start_eq = pd.Series([float(self.initial_capital)], index=[days[0] - pd.Timedelta(days=1)])
        own_curve = pd.concat([start_eq, out["daily"]["equity"]])
        rule_w = pd.Series(np.where(hist["regime"].isna(), np.nan, (hist["P"] > hist["sma_slow"]).astype(float)), index=hist.index)
        _, rule_out = run(A.DIRECTION_LONG_ONLY, weights=rule_w)
        other_dir = A.DIRECTION_INVERSE if direction == A.DIRECTION_LONG_ONLY else A.DIRECTION_LONG_ONLY
        _, other_out = run(other_dir)
        benchmarks = [
            {"label": f"Buy & hold {self.ticker}", **stats(closes.loc[days[0]:days[-1]])},
            {"label": f"Buy & hold {bull}", **stats(leg_daily[bull]["Close"].loc[days[0]:days[-1]])},
            {"label": f"250-day rule on {bull}",
             **stats(pd.concat([start_eq, rule_out["daily"]["equity"]]))},
        ]
        regimes = hist["regime"].reindex(days)
        share = regimes.dropna().value_counts(normalize=True)
        segments = []
        for day, reg in regimes.dropna().items():
            if segments and segments[-1]["regime"] == reg:
                segments[-1]["end"] = day.date().isoformat()
            else:
                segments.append({"regime": reg, "label": A.REGIMES[reg], "start": day.date().isoformat(), "end": day.date().isoformat()})
        daily = out["daily"]
        step = max(1, len(daily) // 400)
        alloc_cols = [c for c in daily.columns if c.startswith("w_")]
        allocation_series = [{"time": d.date().isoformat(), **{c[2:]: round(float(daily.loc[d, c]) * 100, 1) for c in alloc_cols}}
                             for d in daily.index[::step]]
        variant_rows = []
        for vid, o in ((direction, out), (other_dir, other_out)):
            st = stats(pd.concat([start_eq, o["daily"]["equity"]]))
            v = sp.get_variant(preset, vid)
            variant_rows.append({"id": vid, "label": v.label.replace(" · Recommended", ""), "selected": vid == direction,
                                 "roi_pct": st.get("roi_pct"), "annualised_roi_pct": st.get("annualised_roi_pct"),
                                 "max_drawdown_pct": abs(st.get("max_drawdown_pct") or 0.0),
                                 "total_trades": len(o["fills"]), "win_rate_pct": None, "profit_factor": None})
        exec_label = next((o["label"] for o in A.exec_time_options() if o["value"] == exec_setting), f"{exec_setting} ET")
        fill_note = ("Decided on the benchmark's last 15-minute bar completed by the execution time (completed daily closes "
                     "through yesterday plus that price) and filled at the next 15-minute bar's open."
                     if model == "intraday" else
                     "Intraday bars were unavailable, so this run decides on each completed daily close and fills at the next open (an approximation).")
        info = {
            "id": preset.id, "variant_id": direction, "label": preset.label,
            "variant_label": sp.get_variant(preset, direction).label.replace(" · Recommended", ""),
            "kind": A.KIND, "direction": direction, "catalog_version": sp.CATALOG_VERSION,
            "fill_model": model, "execution_time": exec_setting, "execution_time_label": exec_label,
            "execution_note": (f"Rebalances once a day at {exec_label}. {fill_note} Legs trade only when more than "
                               f"{A.REBALANCE_BAND * 100:.0f}% of the sleeve off target. No fees, slippage or dividends."),
            "legs": {"bull": bull, "bear": bear_sel}, "signal": self.ticker,
            "research_note": A.research_note(preset.id), "research_report_url": A.RESEARCH_REPORT_URL,
            "untested_note": "", "warmup_bars": A.WARMUP_BARS,
            "exposure_pct": round(float(daily["exposure"].mean() * 100), 2),
            "rebalances": len({f["day"] for f in out["fills"]}), "fills": len(out["fills"]),
            "skipped_days": int(out["skipped_days"]), "no_decision_days": int(regimes.isna().sum()),
            "early_close_adjusted_days": adjusted_days,
            "regime_share": {A.REGIMES[k]: round(float(v) * 100, 1) for k, v in share.items()},
            "regime_segments": segments[-200:], "allocation_series": allocation_series,
            "benchmarks": benchmarks, "strategy_stats": stats(own_curve),
            "low_sample_warning": len(days) < 250,
            "variants": variant_rows,
            "rebalance_log": [{"day": f["day"].date().isoformat(), "time": f["decision_time"], "symbol": f["symbol"], "side": f["side"],
                               "qty": round(f["qty"], 4), "price": round(f["price"], 2), "notional": round(f["notional"], 2),
                               "regime": A.REGIMES.get(f["regime"], f["regime"]), "limit": f.get("limit")}
                              for f in out["fills"][-300:]][::-1],
        }
        if regimes.dropna().empty:
            info["warning"] = f"Not enough history: {A.MIN_COMPLETED_CLOSES} completed daily closes are required before the strategy can decide."
        if not self.trades:
            summary["no_trade_reason"] = info.get("warning") or f"{preset.label} held cash for the whole window."
        summary["strategy_preset"] = info
        print(f"[backtest] ALLOCATION {preset.label} ({direction}, {bull}/{bear_sel}) {self.ticker}: ROI {summary.get('roi_pct')}% | fills {len(out['fills'])}")
        return results

    # ── Session Rider (schedule) ────────────────────────────────────────────
    SESSION_BAR_TOLERANCE = pd.Timedelta(minutes=5)

    def _session_intraday_bars(self, timeframe, first_day, last_day, window_last=None):
        """Alpaca intraday bars for the window, fetched one calendar year at a time.

        Returns (frame, error). Yahoo keeps only days of intraday history, so a Yahoo
        fallback is refused rather than silently testing a few days."""
        from datetime import timedelta
        frames = []
        year_start = first_day
        while year_start <= last_day:
            year_end = min(last_day, pd.Timestamp(year_start.year, 12, 31))
            frame = get_historical_data(self.ticker, timeframe, year_start.to_pydatetime(),
                                        (year_end + timedelta(days=1)).to_pydatetime(), force_yahoo=False)
            if frame is None or frame.empty:
                if window_last is not None and year_start > window_last:
                    break  # only the padding after the end date (a sell after the window) is missing
                return None, f"No {timeframe} bars for {self.ticker} in {year_start.year}."
            source = str((frame.attrs or {}).get("data_source") or "").lower()
            if source != "alpaca":
                return None, (f"Session Rider needs Alpaca {timeframe} bars, but the data came from {source or 'another source'} "
                              f"(Yahoo keeps only 7 days of 1-minute and 60 days of 5-minute bars). Link your Alpaca keys or try again later.")
            if str((frame.attrs or {}).get("data_quality", "OK")).upper() == "DEGRADED":
                return None, f"{self.ticker} {timeframe} bars failed the split-adjustment check, so the strategy was not run."
            self.data_feed = (frame.attrs or {}).get("feed") or self.data_feed
            frames.append(frame)
            year_start = pd.Timestamp(year_start.year + 1, 1, 1)
        bars = pd.concat(frames)
        bars.index = pd.DatetimeIndex(bars.index)
        if bars.index.tz is not None:
            bars.index = bars.index.tz_convert("America/New_York").tz_localize(None)
        bars = bars[~bars.index.duplicated(keep="last")].sort_index()
        bars = self._title_ohlc(self._regular_hours_only(bars))
        first_bar_day = bars.index[0].normalize() if not bars.empty else None
        if first_bar_day is None or first_bar_day > first_day + pd.Timedelta(days=5):
            return None, (f"{timeframe} bars for {self.ticker} only start on "
                          f"{first_bar_day.date() if first_bar_day is not None else 'n/a'}; choose a start date after that.")
        return bars, None

    def _session_price(self, opens, at, *, exit_side=False):
        """Open of the bar that starts at ``at``; else the next bar within 5 minutes.

        An exit may not be skipped, so it falls back to the next bar at any later time."""
        pos = opens.index.searchsorted(at, side="left")
        if pos >= len(opens):
            return None, None
        ts = opens.index[pos]
        if ts - at <= self.SESSION_BAR_TOLERANCE or exit_side:
            return ts, float(opens.iloc[pos])
        return None, None

    def _run_session_strategy(self):
        """Simulate Session Rider: buy at the Buy time, sell at the Sell time (rules in session_schedule.py).

        Fills are the open of the intraday bar that starts at the scheduled time (bars are stamped at
        their start). The entry ignores price, so there is no signal lookahead. No fees, slippage or
        dividends in the headline; a separate cost table shows 2/5/10 bps per side."""
        from datetime import timedelta
        import session_schedule as S
        import strategy_presets as sp

        preset = sp.get_preset(self.strategy_preset)
        errors = S.validate(self.session_settings)
        if errors:
            return {"error": errors[0]}
        cfg = S.normalized_settings(self.session_settings)
        buy, sell, mode = cfg[S.BUY_KEY], cfg[S.SELL_KEY], cfg["mode"]
        if self.timeframe != "1Day":
            return {"error": f"{preset.label} uses its own clock times; set the timeframe to 1Day."}
        if self.is_crypto:
            return {"error": f"{preset.label} supports US stocks and ETFs only."}

        daily_raw = get_historical_data(self.ticker, "1Day", self.start_date - timedelta(days=10), self.end_date, force_yahoo=False)
        if daily_raw is None or daily_raw.empty:
            return {"error": f"No {self.ticker} data found for the selected range."}
        self._capture_provenance(daily_raw)
        if self.data_quality == "DEGRADED":
            return {"error": "Market data failed the split-adjustment check, so the strategy was not run. Try again later."}
        daily = self._title_ohlc(sp.completed_bars(daily_raw, "1Day"))
        daily.index = pd.DatetimeIndex(daily.index).normalize()
        daily = daily[~daily.index.duplicated(keep="last")]
        window = daily.loc[self.start_date:self.end_date]
        if window.empty:
            return {"error": "No data available in the requested range after filtering."}
        days = window.index

        bar_tf = "1Min" if S.needs_minute_bars(buy, sell) else "5Min"
        bars, err = self._session_intraday_bars(bar_tf, days[0], days[-1] + pd.Timedelta(days=7), window_last=days[-1])
        if err:
            return {"error": err}
        opens = bars["Open"].astype(float).dropna()
        closes = window["Close"].astype(float)

        # One schedule, priced once; the cost table reuses it.
        plan, skipped = [], {"missing bar": 0, "early close": 0, "weekend/holiday rule": 0}
        adjusted = 0
        last_exit_ts = None
        for day in days:
            reason = S.skip_reason(day.date(), cfg)
            if reason:
                if "early close" in reason:
                    skipped["early close"] += 1
                elif "weekend" in reason:
                    skipped["weekend/holiday rule"] += 1
                continue
            ev = S.buy_event(day.date(), cfg)
            t_buy = ev["at"].replace(tzinfo=None)
            if last_exit_ts is not None and t_buy <= last_exit_ts:
                skipped["missing bar"] += 1  # the previous exit was late; never hold two positions
                continue
            b_ts, b_px = self._session_price(opens, pd.Timestamp(t_buy))
            if b_ts is None or b_ts.normalize() != day:
                skipped["missing bar"] += 1
                continue
            t_sell = ev["sell"]["at"].replace(tzinfo=None)
            if pd.Timestamp(t_sell).normalize() > days[-1]:
                break  # its sell falls after the end date: the last entry is not taken
            s_ts, s_px = self._session_price(opens, pd.Timestamp(t_sell), exit_side=True)
            if s_ts is None:
                break  # no bar left to sell into
            late = s_ts - pd.Timestamp(t_sell) > self.SESSION_BAR_TOLERANCE
            adjusted += int(ev["adjusted"] or ev["sell"]["adjusted"])
            plan.append({"day": day, "buy_ts": b_ts, "buy_px": b_px, "sell_ts": s_ts, "sell_px": s_px, "late": late})
            last_exit_ts = s_ts
        max_frac = None
        if self.enable_risk and self.max_pos_pct:
            max_frac = float(self.max_pos_pct) / 100.0 if float(self.max_pos_pct) > 1.0 else float(self.max_pos_pct)

        def simulate(cost_bps=0.0):
            cost = cost_bps / 10000.0
            realized, trades = 0.0, []
            for row in plan:
                equity_now = float(self.initial_capital) + realized
                amount = self.trade_amount + (realized if self.allocation_mode == "compound" else 0.0)
                cap_base = float(self.initial_capital) if self.allocation_mode == "fixed" else equity_now
                if max_frac is not None:
                    amount = min(amount, cap_base * max_frac)
                if self.allocation_mode == "compound":
                    amount = min(amount, equity_now)
                if amount < 1.0:
                    continue
                entry = row["buy_px"] * (1 + cost)
                qty = amount / entry
                exit_px = row["sell_px"] * (1 - cost)
                pl = (exit_px - entry) * qty
                realized += pl
                trades.append({"row": row, "qty": qty, "entry": entry, "exit": exit_px, "pl": pl})
            curve = []
            running, ti = 0.0, 0
            closed = sorted(trades, key=lambda t: t["row"]["sell_ts"])
            for day in days:
                while ti < len(closed) and closed[ti]["row"]["sell_ts"].normalize() <= day:
                    running += closed[ti]["pl"]
                    ti += 1
                eq = float(self.initial_capital) + running
                held = next((t for t in trades if t["row"]["buy_ts"].normalize() <= day < t["row"]["sell_ts"].normalize()), None)
                if held is not None:
                    eq += (float(closes.loc[day]) - held["entry"]) * held["qty"]
                curve.append(eq)
            return trades, pd.Series(curve, index=days)

        trades, equity = simulate(0.0)
        self.df = window
        self.equity_history_offset = 0
        self.equity_history = [{"time": d, "equity": float(e)} for d, e in equity.items()]
        self.equity = float(equity.iloc[-1])
        label_buy, label_sell = S.et_ct_label(buy), S.et_ct_label(sell)
        rows = []
        for t in trades:
            r = t["row"]
            rows.append({"entry_time": r["buy_ts"], "exit_time": r["sell_ts"], "qty": t["qty"], "entry": t["entry"], "exit": t["exit"],
                         "pl": t["pl"], "reason": "Scheduled sell (late: no bar at the sell time)" if r["late"] else "Scheduled sell"})
        self.trades = [{
            "ticker": self.ticker, "entry_time": self._to_utc_iso(r["entry_time"]), "exit_time": self._to_utc_iso(r["exit_time"]),
            "qty": round(r["qty"], 6), "entry_price": round(r["entry"], 4), "exit_price": round(r["exit"], 4),
            "entry_cost": round(r["qty"] * r["entry"], 2), "exit_value": round(r["qty"] * r["exit"], 2), "fees": 0.0,
            "pl": round(r["pl"], 2), "pl_pct": round((r["exit"] / r["entry"] - 1) * 100, 3) if r["entry"] else 0.0,
            "reason": r["reason"], "side": "long", "stop_loss": 0.0, "take_profit": 0.0, "initial_stop_loss": 0.0,
            "initial_take_profit": 0.0, "exit_stop_price": None, "entry_signal_time": None, "exit_signal_time": None,
        } for r in rows]
        results = self._calculate_metrics()
        summary = results.get("summary")
        if summary is None:
            return results

        def total_pct(series):
            series = series.dropna()
            return round((float(series.prod()) - 1) * 100, 2) if len(series) else None

        opens_d, closes_d = window["Open"].astype(float), closes
        overnight_parts = opens_d / closes_d.shift(1)
        intraday_parts = closes_d / opens_d
        start_eq = float(self.initial_capital)
        by_year = []
        eq_with_start = pd.concat([pd.Series([start_eq], index=[days[0] - pd.Timedelta(days=1)]), equity])
        for year in sorted({d.year for d in days}):
            in_year = eq_with_start[eq_with_start.index.year <= year]
            prior = eq_with_start[eq_with_start.index.year < year]
            base = float(prior.iloc[-1]) if len(prior) else start_eq
            yc = closes[closes.index.year == year]
            prev_close = closes[closes.index.year < year]
            bh_base = float(prev_close.iloc[-1]) if len(prev_close) else float(opens_d[opens_d.index.year == year].iloc[0])
            by_year.append({
                "year": int(year),
                "strategy_pct": round((float(in_year.iloc[-1]) / base - 1) * 100, 2) if base else None,
                "buy_hold_pct": round((float(yc.iloc[-1]) / bh_base - 1) * 100, 2) if len(yc) and bh_base else None,
                "trades": sum(1 for t in trades if t["row"]["sell_ts"].year == year),
            })
        cost_rows = []
        for bps in (2, 5, 10):
            _, c_equity = simulate(float(bps))
            cost_rows.append({"bps_per_side": bps, "roi_pct": round((float(c_equity.iloc[-1]) / start_eq - 1) * 100, 2)})
        pause_after = cfg[S.LOSS_PAUSE_KEY] or S.LOSS_PAUSE_DEFAULT
        pauses, streak = 0, 0
        for t in trades:
            streak = streak + 1 if t["pl"] < 0 else 0
            if streak >= pause_after:
                pauses += 1
                streak = 0
        notes = [
            "No fees, slippage or dividends in the headline result (cost table below).",
            "Prices are split-adjusted only, so overnight results are understated: the ex-dividend drop happens overnight and the dividend is not credited.",
            "The first and last minutes of the session have wider spreads and more noise; live fills can differ from these bar prices.",
            f"The live bot pauses after {pause_after} losses in a row and waits for your review; the backtest keeps trading.",
        ]
        info = {
            "id": preset.id, "variant_id": self.strategy_preset_variant, "label": preset.label,
            "variant_label": "Scheduled buy and sell", "kind": S.KIND, "catalog_version": sp.CATALOG_VERSION,
            "mode": mode, "buy_time": buy, "sell_time": sell, "buy_label": f"{S.describe(buy)} · {label_buy}" if not buy[0].isdigit() else label_buy,
            "sell_label": f"{S.describe(sell)} · {label_sell}" if not sell[0].isdigit() else label_sell,
            "bar_timeframe": bar_tf, "fill_model": "bar_open_at_time",
            "execution_note": (f"Buys at the open of the {bar_tf} bar starting at the buy time and sells at the open of the bar starting at "
                               "the sell time (next bar within 5 minutes if one is missing). The entry ignores price, so nothing after "
                               "the buy time is used to decide it."),
            "round_trips": len(trades), "skipped_days": skipped, "early_close_adjusted_trades": adjusted,
            "late_exits": sum(1 for t in trades if t["row"]["late"]),
            "would_pause_count": pauses, "loss_pause_after": pause_after,
            "buy_hold_split": [
                {"label": f"Buy & hold {self.ticker} (first open → last close)", "roi_pct": total_pct(pd.concat([overnight_parts, intraday_parts]))},
                {"label": "Overnight part (close → next open)", "roi_pct": total_pct(overnight_parts)},
                {"label": "Intraday part (open → close)", "roi_pct": total_pct(intraday_parts)},
            ],
            "by_year": by_year, "cost_table": cost_rows, "notes": notes,
            "low_sample_warning": len(trades) < 100, "untested_note": "", "warmup_bars": 0,
        }
        if not self.trades:
            summary["no_trade_reason"] = "No scheduled trade could be filled in this window."
        summary["strategy_preset"] = info
        print(f"[backtest] SESSION RIDER {self.ticker} {mode} {buy}->{sell}: ROI {summary.get('roi_pct')}% | round trips {len(trades)}")
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

    def _queue_entry(self, signal_bar, signal_timestamp, reason, side, signal_index):
        """Hold an entry decided on a completed bar until the next bar's open."""
        if signal_index >= len(self.df) - 1:
            print(f"[{self._to_central_str(signal_timestamp)}] HOLD {self.ticker} | Entry signal on the final bar; no next bar to fill.")
            return
        self.pending_entry = {
            'bar': signal_bar,
            'timestamp': signal_timestamp,
            'reason': reason,
            'side': side,
            'index': signal_index,
        }

    def _fill_pending_entry(self, pending, fill_bar, fill_timestamp):
        try:
            fill_price = float(fill_bar.get('Open', fill_bar['Close']))
        except (TypeError, ValueError):
            fill_price = 0.0
        if not np.isfinite(fill_price) or fill_price <= 0:
            fill_price = float(fill_bar['Close'])
        self._enter_trade(
            pending['bar'], fill_timestamp, pending['reason'], side=pending['side'],
            fill_price=fill_price, signal_index=pending['index'],
        )
        if self.position is not None:
            self.position['signal_time'] = pending['timestamp']

    def _enter_trade(self, bar, timestamp, reason, side="long", fill_price=None, signal_index=None):
        # ``bar`` is the completed signal bar (ATR, structure); ``fill_price``
        # is the next bar's open when the entry was queued.
        price = float(fill_price) if fill_price is not None else bar['Close']
        atr = bar['ATR']
        if atr <= 0:
            atr = price * 0.01  # Fallback: treat 1% of price as synthetic ATR

        stop_dist = atr * self.atr_stop_multiplier
        if stop_dist <= 0:
            stop_dist = price * 0.01

        side = str(side or "long").lower()
        base_notional = self.current_trade_amount if self.allocation_mode == "compound" else self.trade_amount
        base_notional = max(1.0, float(base_notional))
        initial_grid = {}
        if self.adaptive_continuous_grid and side == "long":
            from engine import _initial_continuous_grid_entry_instruction
            self.continuous_grid_settings["compound_amount"] = self.current_trade_amount
            history_end = self.current_bar_index if signal_index is None else int(signal_index)
            grid_history = (
                self.df.iloc[max(0, history_end - 79):history_end + 1]
                if self.df is not None
                else [bar.to_dict() if hasattr(bar, "to_dict") else dict(bar)]
            )
            initial_grid = _initial_continuous_grid_entry_instruction(
                self.continuous_grid_settings,
                price_history=grid_history,
                current_atr=float(bar.get("ATR", 0.0) or 0.0),
            )
            if initial_grid:
                base_notional = max(0.0, float(initial_grid.get("level_amount", 0.0)))
                reason = (
                    f"Adaptive Grid Initial Buy L{initial_grid['level']}/5: "
                    f"{initial_grid['allocation_pct']:.1f}% (${base_notional:.2f}). {reason}"
                )
        sizing_equity = self._sizing_equity()
        max_notional = sizing_equity * self.max_pos_pct if self.enable_risk else sizing_equity
        allocated_cash = min(base_notional, max_notional, sizing_equity)
        qty = allocated_cash / price if price > 0 else 0

        if qty <= 0:
            print(f"[{self._to_central_str(timestamp)}] HOLD {self.ticker} | No allocation available for entry")
            return

        # Volatility Regime Sizing Filter: Scale down quantity by 50% if volatility (ATR / Price) is hyper-extended (> 3.0%)
        volatility_ratio = atr / price
        if self.enable_risk and volatility_ratio > 0.03 and not initial_grid:
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
        if initial_grid:
            from engine import _record_continuous_grid_fill
            _record_continuous_grid_fill(
                self.continuous_grid_settings,
                side="buy",
                fill_price=float(price),
                was_grid_order=True,
                had_position=False,
                filled_level=initial_grid.get("level"),
                current_atr=float(atr),
            )
        entry_label = "BUY" if side == "long" else "SELL"
        print(f"[{self._to_central_str(timestamp)}] {entry_label}  {self.ticker} @ ${price:.2f} | Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f} | Reason: {reason}")

    def _execute_continuous_grid_buy(self, bar, timestamp, decision):
        from engine import _record_continuous_grid_fill

        price = float(bar['Close'])
        requested = max(0.0, float(decision.get("level_amount", 0.0) or 0.0))
        if price <= 0 or requested < 1.0:
            return False
        current_cost = 0.0
        current_market_value = 0.0
        if self.position:
            current_cost = float(self.position.get("qty", 0.0)) * float(self.position.get("entry_price", 0.0))
            current_market_value = float(self.position.get("qty", 0.0)) * price
        max_notional = self.equity * self.max_pos_pct if self.enable_risk else self.equity
        available = max(0.0, min(self.equity - current_cost, max_notional - current_market_value))
        if available + 0.01 < requested:
            return False

        qty = requested / price
        had_position = bool(self.position)
        if had_position:
            old_qty = float(self.position.get("qty", 0.0))
            old_entry = float(self.position.get("entry_price", price))
            new_qty = old_qty + qty
            self.position["qty"] = new_qty
            self.position["entry_price"] = ((old_qty * old_entry) + (qty * price)) / new_qty
            self.position["reason"] = str(decision.get("reason") or "Adaptive grid scale-in")
        else:
            precision = 6 if price < 1.0 else 2
            self.position = {
                "qty": qty,
                "entry_price": price,
                "entry_time": timestamp,
                "stop_loss": round(price * (1 - self.regular_stop_loss_pct), precision),
                "take_profit": round(price * (1 + self.regular_take_profit_pct), precision),
                "initial_stop_loss": round(price * (1 - self.regular_stop_loss_pct), precision),
                "initial_take_profit": round(price * (1 + self.regular_take_profit_pct), precision),
                "side": "long",
                "reason": str(decision.get("reason") or "Adaptive grid buy-back"),
            }

        _record_continuous_grid_fill(
            self.continuous_grid_settings,
            side="buy",
            fill_price=price,
            was_grid_order=True,
            had_position=had_position,
            filled_level=decision.get("level"),
            current_atr=float(bar.get("ATR", 0.0) or 0.0),
        )
        levels = "+".join(
            f"L{level}" for level in range(
                int(decision.get("from_level", decision.get("level", 1))),
                int(decision.get("level", 1)) + 1,
            )
            if level in decision.get("enabled_levels", range(1, 6))
        )
        level_label = levels or f"L{decision.get('level', 1)}"
        print(
            f"[{self._to_central_str(timestamp)}] BUY  {self.ticker} @ ${price:.4f} | "
            f"Adaptive Grid {level_label} | ${requested:.2f}"
        )
        return True

    def _execute_continuous_grid_sell(self, bar, timestamp, decision):
        from engine import _continuous_grid_sell_fraction, _record_continuous_grid_fill

        if not self.position or str(self.position.get("side", "long")).lower() != "long":
            return False
        price = float(bar['Close'])
        total_qty = max(0.0, float(self.position.get("qty", 0.0)))
        if price <= 0 or total_qty <= 0:
            return False
        result = {
            "continuous_grid_side": "exit",
            "continuous_grid_from_level": decision.get("from_level", decision.get("level")),
            "continuous_grid_level": decision.get("level"),
            "continuous_grid_runner_exit": bool(decision.get("continuous_grid_runner_exit")),
        }
        fraction = _continuous_grid_sell_fraction(
            self.continuous_grid_settings,
            result,
            current_qty=total_qty,
        )
        sell_qty = min(total_qty, max(0.0, total_qty * fraction))
        if sell_qty <= 0:
            return False
        closes_position = sell_qty >= total_qty - 0.00000001
        reason = str(decision.get("reason") or "Adaptive grid scale-out")
        if closes_position:
            self._exit_trade(bar, timestamp, reason, exit_price_override=price)
            self.continuous_grid_settings["compound_amount"] = self.current_trade_amount
            _record_continuous_grid_fill(
                self.continuous_grid_settings,
                side="sell",
                fill_price=price,
                was_grid_order=True,
                had_position=True,
                position_closed=True,
                continue_cycle=True,
                filled_level=decision.get("level"),
                current_atr=float(bar.get("ATR", 0.0) or 0.0),
            )
            return True

        entry_price = float(self.position.get("entry_price", 0.0))
        entry_cost = sell_qty * entry_price
        exit_value = sell_qty * price
        is_crypto = self.is_crypto
        fee = round((entry_cost + exit_value) * 0.001, 2) if is_crypto else 0.0
        pl = (price - entry_price) * sell_qty - fee
        pl_pct = (pl / entry_cost * 100.0) if entry_cost > 0 else 0.0
        self.equity += pl
        self.position["qty"] = total_qty - sell_qty
        self.trades.append({
            "ticker": self.ticker,
            "entry_time": self._to_utc_iso(self.position["entry_time"]),
            "exit_time": self._to_utc_iso(timestamp),
            "qty": round(sell_qty, 4),
            "entry_price": round(entry_price, 8 if is_crypto else 2),
            "exit_price": round(price, 8 if is_crypto else 2),
            "entry_cost": round(entry_cost, 2),
            "exit_value": round(exit_value, 2),
            "fees": fee,
            "pl": round(pl, 2),
            "pl_pct": round(pl_pct, 2),
            "reason": reason,
            "side": "long",
            "stop_loss": round(float(self.position.get("stop_loss", 0.0)), 8 if is_crypto else 2),
            "take_profit": round(float(self.position.get("take_profit", 0.0)), 8 if is_crypto else 2),
            "initial_stop_loss": round(float(self.position.get("initial_stop_loss", 0.0)), 8 if is_crypto else 2),
            "initial_take_profit": round(float(self.position.get("initial_take_profit", 0.0)), 8 if is_crypto else 2),
            "exit_stop_price": None,
        })
        if self.max_completed_trades and len(self.trades) >= self.max_completed_trades:
            self.overtrade_limit_hit = True
        self._refresh_guard_session(timestamp)
        self.daily_realized_pl += float(pl)
        self.consecutive_losses = self.consecutive_losses + 1 if pl < 0 else 0
        if self.allocation_mode == "compound":
            self.current_trade_amount = max(1.0, self.current_trade_amount + pl)
            self.continuous_grid_settings["compound_amount"] = self.current_trade_amount
        _record_continuous_grid_fill(
            self.continuous_grid_settings,
            side="sell",
            fill_price=price,
            was_grid_order=True,
            had_position=True,
            position_closed=False,
            filled_level=decision.get("level"),
            current_atr=float(bar.get("ATR", 0.0) or 0.0),
        )
        print(
            f"[{self._to_central_str(timestamp)}] SELL {self.ticker} @ ${price:.4f} | "
            f"Adaptive Grid L{decision.get('level', 1)} | Qty {sell_qty:.6f} | P/L ${pl:+.2f}"
        )
        return True

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
        self._refresh_guard_session(timestamp)
        self.daily_realized_pl += float(pl)
        self.consecutive_losses = self.consecutive_losses + 1 if pl < 0 else 0
        if is_stop_exit(reason):
            self.stop_exit_state = {
                "direction": "BUY" if side == "long" else "SELL",
                "exit_price": float(exit_price),
                "time": timestamp,
            }
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

    def _refresh_guard_session(self, timestamp):
        session_date = timestamp.date()
        if self.guard_session_date != session_date:
            self.guard_session_date = session_date
            self.daily_realized_pl = 0.0
            self.consecutive_losses = 0

    def _loss_circuit_reason(self, timestamp):
        self._refresh_guard_session(timestamp)
        if self.max_consecutive_losses > 0 and self.consecutive_losses >= self.max_consecutive_losses:
            return f"Loss circuit breaker active ({self.consecutive_losses} consecutive losses)"
        loss_limit = self.initial_capital * self.max_daily_loss_pct
        if loss_limit > 0 and self.daily_realized_pl <= -loss_limit:
            return f"Daily strategy-loss limit active (${abs(self.daily_realized_pl):.2f} loss)"
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

        circuit_reason = self._loss_circuit_reason(timestamp)
        if circuit_reason:
            return False, circuit_reason

        if self.stop_exit_state:
            from engine import parse_timeframe_to_seconds
            from trade_guards import stabilization_reason
            elapsed_seconds = max(0.0, (timestamp - self.stop_exit_state["time"]).total_seconds())
            elapsed_bars = int(elapsed_seconds // max(1, parse_timeframe_to_seconds(self.timeframe)))
            stabilization = stabilization_reason(
                action,
                bar,
                self.stop_exit_state,
                elapsed_bars,
                self.post_stop_stabilization_bars,
            )
            if stabilization:
                return False, stabilization
            self.stop_exit_state = None

        if self.enable_liquidity_filter and self.df is not None:
            from trade_guards import historical_volume_reason
            start = max(0, self.current_bar_index - 20)
            recent = self.df.iloc[start:self.current_bar_index]["Volume"].tolist() if "Volume" in self.df.columns else []
            liquidity = historical_volume_reason(bar.get("Volume", 0), recent, self.min_relative_volume)
            if liquidity:
                return False, liquidity

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
                    **self._provenance_summary(),
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
        import backtest_metrics as bm
        annualised_roi = bm.annualized_return(
            roi, bm.basis_days(self.df.index), self.allocation_mode)["annualised_roi_pct"]
        fast_max_dd = bm.max_drawdown_pct([e["equity"] for e in self.equity_history]) if self.equity_history else 0.0
        fast_calmar = (round(annualised_roi / fast_max_dd, 2)
                       if (annualised_roi is not None and fast_max_dd > 0) else (None if self.equity_history else 0.0))

        return {
            "summary": {
                "ticker": self.ticker,
                "timeframe": self.timeframe,
                "period_days": period_days,
                "start_date_utc": self.df.index[0].isoformat(),
                "end_date_utc": self.df.index[-1].isoformat(),
                "data_source": self.data_source,
                    **self._provenance_summary(),
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
                "max_drawdown_pct": round(fast_max_dd, 2),
                "sharpe_ratio": sharpe,
                "sortino_ratio": sortino,
                "calmar_ratio": fast_calmar,
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
                "annualised_roi_pct": annualised_roi,
            },
            "equity_curve": [],
            "trades": self.trades[::-1],
            "candles": [],
            "volume_profile": self.volume_profile,
            "diagnostics": {"fast_metrics": True},
        }

    def _calculate_metrics(self):
        results = self._calculate_metrics_base()
        try:
            self._enrich_metrics(results)
        except Exception as exc:  # analytics must never break a backtest result
            print(f"[backtest] Result analytics unavailable: {type(exc).__name__}: {exc}")
        return results

    def _enrich_metrics(self, results):
        """Corrected annualisation and risk ratios, buy & hold edge, exposure,
        drawdown profile, monthly returns, period split, trade tags and the run
        record (see backtest_metrics.py). Shared by every backtest path."""
        import backtest_metrics as bm

        summary = results.get("summary") if isinstance(results, dict) else None
        if not isinstance(summary, dict) or self.df is None or self.df.empty:
            return
        initial = float(self.initial_capital or 0.0)
        fmt = self._to_utc_iso

        def to_utc(index):
            idx = pd.DatetimeIndex(index)
            if idx.tz is None:  # bars are exchange-local, like _localize_dt
                idx = idx.tz_localize(self.local_tz, ambiguous=False, nonexistent="shift_forward")
            return idx.tz_convert("UTC")

        def unix_of(index):
            delta = to_utc(index) - pd.Timestamp(0, tz="UTC")
            return [int(v) for v in (delta // pd.Timedelta(seconds=1))]

        equity = bm.equity_series(self.equity_history)
        if equity.empty:
            equity = pd.Series([initial, float(self.equity)],
                               index=pd.DatetimeIndex([self.df.index[0], self.df.index[-1]]))
        roi = float(summary.get("roi_pct") or 0.0)
        max_dd = abs(float(summary.get("max_drawdown_pct") or 0.0))

        summary.update(bm.annualized_return(roi, bm.basis_days(equity.index), self.allocation_mode))
        annualised = summary.get("annualised_roi_pct")
        summary["calmar_ratio"] = round(annualised / max_dd, 2) if (annualised is not None and max_dd > 0) else None
        summary.update(bm.risk_ratios(equity, initial, self.is_crypto))

        closes = pd.to_numeric(self.df["Close"], errors="coerce")
        bh_curve = bm.buy_hold_curve(closes, initial)
        bh_dd = bm.max_drawdown_pct(bh_curve.to_numpy()) if not bh_curve.empty else 0.0
        summary["buy_hold_max_drawdown_pct"] = round(bh_dd, 2)
        summary["edge_vs_buy_hold"] = bm.edge_vs_buy_hold(roi, max_dd, summary.get("buy_hold_roi_pct"), bh_dd)
        if not bh_curve.empty and results.get("equity_curve"):
            lookup = dict(zip(to_utc(bh_curve.index).strftime("%Y-%m-%d %H:%M:%SZ"), bh_curve.to_numpy(dtype=float)))
            results["buy_hold_curve"] = [
                {"time": p["time"], "equity": round(lookup[p["time"]], 2)}
                for p in results["equity_curve"] if p.get("time") in lookup
            ]

        def iso_unix(value):
            try:
                stamp = pd.Timestamp(value)
                return None if pd.isna(stamp) else int(stamp.timestamp())
            except (TypeError, ValueError):
                return None

        trades = list(self.trades or [])
        summary["exposure_pct"] = bm.exposure_pct(
            unix_of(self.df.index),
            [(iso_unix(t.get("entry_time")), iso_unix(t.get("exit_time"))) for t in trades],
        )
        summary.update(bm.drawdown_profile(equity, fmt))
        summary["monthly_returns"] = bm.monthly_returns(equity, initial)
        summary["period_split"] = bm.period_split(
            equity, unix_of(equity.index), initial, trades,
            lambda t: iso_unix(t.get("exit_time")), fmt,
        )
        for trade in results.get("trades") or []:
            trade["exit_category"] = bm.classify_exit(trade.get("reason"), trade.get("side", "long"))
            trade["hold_hours"] = bm.hold_hours(trade.get("entry_time"), trade.get("exit_time"))
        summary["run_settings"] = bm.run_settings(
            self, fill_model="signal_bar_close" if self.ai_autopilot else "next_bar_open")
        summary["metrics_version"] = bm.METRICS_VERSION

    def _calculate_metrics_base(self):
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

            no_trade_reason = "No trades executed during the period. Try lowering thresholds, enabling more indicators, or choosing a looser entry strategy."
            return {
                "summary": {
                    "ticker": self.ticker,
                    "timeframe": self.timeframe,
                    "period_days": (self.end_date - self.start_date).days,
                    "start_date_utc": self.df.index[0].isoformat(),
                    "end_date_utc": self.df.index[-1].isoformat(),
                    "data_source": self.data_source,
                    **self._provenance_summary(),
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
                "volume_profile": self.volume_profile,
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
                    eq_idx = i - self.equity_history_offset
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

        return {
            "summary": {
                "ticker": self.ticker,
                "timeframe": self.timeframe,
                "period_days": (self.end_date - self.start_date).days,
                "start_date_utc": self.df.index[0].isoformat(),
                "end_date_utc": self.df.index[-1].isoformat(),
                "data_source": self.data_source,
                    **self._provenance_summary(),
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
            "volume_profile": self.volume_profile,
        }
