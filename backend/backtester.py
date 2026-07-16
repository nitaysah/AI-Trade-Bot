import pandas as pd
import numpy as np
from datetime import datetime
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_signals
from swing_strategy import build_entry_signal, build_swing_exit
from trader import get_confluence_decision
import config as global_config
from user_config import get_user_config
from prior_day_high_low import dynamic_high_low_exit_levels, prior_day_levels_from_history, resolve_high_low_buffer_pct

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

def _chart_indicator_fields(row, digits):
    return {
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
        "ba2_buy": _safe_chart_bool(row, "BA2_Buy"),
        "ba2_sell": _safe_chart_bool(row, "BA2_Sell"),
        "ba3_buy": _safe_chart_bool(row, "BA3_Buy"),
        "ba3_sell": _safe_chart_bool(row, "BA3_Sell"),
        "cdl_name": str(row.get("cdl_name", "Neutral")),
        "cdl_bullish": _safe_chart_bool(row, "cdl_bullish"),
        "cdl_bearish": _safe_chart_bool(row, "cdl_bearish"),
    }

class Backtester:
    def __init__(self, ticker, timeframe, start_date, end_date, initial_capital=1000.0, threshold=5, sell_threshold=3, enabled_indicators=None, risk_per_trade=0.02, max_pos_pct=0.25, ext_hours=True, buy_mode="indicator", custom_buy_price=0.0, sell_mode="indicator", atr_stop_multiplier=2.0, atr_trail_multiplier=3.0, atr_take_profit_multiplier=4.0, enable_atr_trail=True, ai_autopilot=False, ai_interval=10, ai_model="llama-3.3-70b-versatile", enable_cooldown=True, cooldown_minutes=None, regular_stop_loss_pct=0.05, regular_take_profit_pct=0.10, regular_trailing_pct=0.03, enable_pct_trail=True, cancel_event=None, entry_atr_multiplier=None, entry_pct_move=None, entry_atr_style="pullback_reclaim", entry_pct_style="dip_reclaim", swing_entry_mode="bounce_retest", swing_entry_lookback_days=20, swing_exit_lookback_days=20, market_session="Regular", allocation_mode="fixed", trade_amount=None, custom_stop_loss_price=0.0, custom_take_profit_price=0.0, enable_signal_reset=False, enable_smart_reentry=False, smart_reentry_type="RSI", smart_reentry_rsi_call_max=70.0, smart_reentry_rsi_put_min=30.0, smart_reentry_vwap_call_max_dist_pct=0.5, smart_reentry_vwap_put_max_dist_pct=0.5, enable_risk=True, daily_high_low_buffer_pct=None):
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
        self.daily_high_low_buffer_pct = resolve_high_low_buffer_pct(fallback=daily_high_low_buffer_pct)
        self.indicator_settings = {
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

        # Pre-cache timezones to prevent massive slowdowns in tight loops
        import pytz
        clean_ticker = self.ticker.upper().replace("/", "")
        self.is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        self.local_tz = pytz.utc if self.is_crypto else pytz.timezone("America/New_York")
        self.central_tz = pytz.timezone("America/Chicago")
        self.utc_tz = pytz.utc

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

    def run(self, precomputed_df=None):
        # Force naive datetimes to match pandas index
        if hasattr(self.start_date, "tzinfo") and self.start_date.tzinfo is not None:
            self.start_date = self.start_date.replace(tzinfo=None)
        if hasattr(self.end_date, "tzinfo") and self.end_date.tzinfo is not None:
            self.end_date = self.end_date.replace(tzinfo=None)

        if precomputed_df is not None:
            self.df = precomputed_df.loc[self.start_date:self.end_date]
        else:
            # 1. Fetch Data with 100 days of padding to ensure indicators (EMA200, etc) can warm up
            from datetime import timedelta
            padded_start = self.start_date - timedelta(days=100)
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
            result = calculate_indicators_for_df(raw_df, self.timeframe, self.ticker, settings=self.indicator_settings)
            if not result:
                return {"error": "Failed to calculate indicators."}
            
            full_df = result['df']
            
            # 3. Slice the dataframe perfectly back down to the user's requested date window
            self.df = full_df.loc[self.start_date:self.end_date]
        
        if self.df.empty:
            return {"error": "No data available in the requested range after filtering."}

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

        # 4. Simulate bar-by-bar
        for i in range(len(self.df)):
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

                # ── ATR / AI-based hard stops (existing modes) ──────────────────
                if self.sell_mode in ["sltp", "hybrid", "trend_rider", "ai_autopilot"]:
                    if current_bar['Low'] <= self.position['stop_loss']:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Stop Loss Hit (${self.position['stop_loss']:.{precision}f})",
                            exit_price_override=self.position['stop_loss'],
                            exit_stop_price=self.position['stop_loss'],
                        )
                        self._last_ai_decision = None
                        continue
                    elif current_bar['High'] >= self.position['take_profit']:
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
                        candidate = round(current_price * (1 - self.regular_trailing_pct), precision)
                        if candidate > self.position['stop_loss']:
                            self.position['stop_loss'] = candidate
                            self.position['trailing_stop_loss'] = candidate
                    trail_note = f" / {self.regular_trailing_pct*100:.1f}% Trail" if self.enable_pct_trail and self.regular_trailing_pct > 0 else ""
                    
                    if current_bar['Low'] <= self.position['stop_loss']:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Percentage Stop Loss Hit ({self.regular_stop_loss_pct*100:.1f}% SL{trail_note})",
                            exit_price_override=self.position['stop_loss'],
                            exit_stop_price=self.position['stop_loss'],
                        )
                        continue
                    elif current_bar['High'] >= self.position['take_profit']:
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
                    if self.custom_stop_loss_price > 0 and current_bar['Low'] <= self.custom_stop_loss_price:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Custom Stop Loss Hit (${self.custom_stop_loss_price:.{precision}f})",
                            exit_price_override=self.custom_stop_loss_price,
                            exit_stop_price=self.custom_stop_loss_price,
                        )
                        continue
                    elif self.custom_take_profit_price > 0 and current_bar['High'] >= self.custom_take_profit_price:
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
                    swing_exit = build_swing_exit(
                        price_history_records[: i + 1],
                        current_price=current_price,
                        is_long=True,
                        lookback_days=self.swing_exit_lookback_days,
                        market_session=self.market_session,
                        is_crypto=self.is_crypto,
                    )
                    swing_stop = swing_exit.get('stop_loss')
                    swing_target = swing_exit.get('take_profit')
                    if swing_stop is not None and current_bar['Low'] <= swing_stop:
                        self._exit_trade(
                            current_bar,
                            timestamp,
                            f"Swing Stop Loss Hit (${swing_stop:.{precision}f})",
                            exit_price_override=swing_stop,
                            exit_stop_price=swing_stop,
                        )
                        continue
                    elif swing_target is not None and current_bar['High'] >= swing_target:
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
                            price_history_records[: i + 1],
                            buffer_pct=self.daily_high_low_buffer_pct,
                        )
                    if hl_levels:
                        if hl_levels and current_price < hl_levels["low_trigger"]:
                            self._exit_trade(
                                current_bar,
                                timestamp,
                                f"Prior Low Breakdown (Trigger=${hl_levels['low_trigger']:.{precision}f}, PDL=${hl_levels['low']:.{precision}f}, Buffer={hl_levels['buffer_pct']:.2f}%)",
                                exit_price_override=current_price,
                            )
                            continue
                        elif hl_levels and current_price > hl_levels["high_trigger"]:
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
                    
                    if not self.position and self.enable_signal_reset and self.signal_locked_direction == "BUY" and action != "BUY":
                        self.signal_locked_direction = None

                    if action == "BUY" and not self.position:
                        allowed, block_reason = self._entry_guard_allows(action, current_bar, current_price, timestamp)
                        if not allowed:
                            action = "HOLD"
                            reason = block_reason
                            self._last_ai_decision = None

                        if action == "BUY":
                            self._enter_trade_ai(current_bar, timestamp, self._last_ai_decision)
                            self._last_ai_decision = None  # Reset to re-evaluate next bars
                    elif action == "SELL" and self.position:
                        # Hybrid / Dynamic Sell check: exit only if sell_mode supports indicator/hybrid
                        if self.sell_mode in ["indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing", "ai_autopilot"]:
                            self._exit_trade(current_bar, timestamp, f"AI Exit: {reason}")
                            self._last_ai_decision = None  # Reset to re-evaluate next bars
                        else:
                            # SL/TP only mode: ignore dynamic sell signal
                            self._last_ai_decision = None
            else:
                # Get Strategy Decision
                signals = _generate_signals(current_bar, prev_bar, recent_bars=self.df.iloc[max(0, i - 4) : i + 1])
                
                # Filter signals based on enabled indicators
                filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
                
                analysis = {
                    'bullish_count': sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BULLISH'),
                    'bearish_count': sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BEARISH'),
                    'signals': filtered_signals
                }
                
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
                    elif analysis['bullish_count'] >= self.threshold:
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
                    hybrid_modes = {'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'}
                    if entry_mode in price_action_modes:
                        action = "HOLD"
                        entry_signal = build_entry_signal(
                            price_history_records[: i + 1],
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
                            desired_direction="BULLISH" if entry_mode in hybrid_modes else None,
                            high_low_buffer_pct=self.daily_high_low_buffer_pct,
                        )
                        signal_action = entry_signal.get("action", "HOLD")
                        if entry_mode in hybrid_modes:
                            structure_ok = signal_action == "BUY" and entry_signal.get("structure_direction") == "BULLISH"
                            indicator_ok = analysis['bullish_count'] >= self.threshold
                            if indicator_ok and structure_ok:
                                action = "BUY"
                                names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                                reason = f"Hybrid BUY: {analysis['bullish_count']} signals ({', '.join(names)}) + {entry_signal.get('reason', 'bullish structure')}"
                        elif signal_action == "BUY":
                            action = "BUY"
                            reason = entry_signal.get("reason", reason)

                    if action == 'HOLD' and entry_mode not in price_action_modes and self.enabled_indicators:
                        if analysis['bullish_count'] >= self.threshold:
                            action = "BUY"
                            names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                            reason = f"{analysis['bullish_count']} signals ({', '.join(names)})"

                    if action != "BUY" and analysis['bearish_count'] >= self.sell_threshold:
                        if self.sell_mode in ["indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"]:
                            action = "SELL"
                            names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                            reason = f"Dynamic Sell: {analysis['bearish_count']} signals ({', '.join(names)})"

                if self.enable_signal_reset and not self.position and self.signal_locked_direction == "BUY" and action != "BUY" and analysis['bullish_count'] < self.threshold:
                    self.signal_locked_direction = None

                if action == 'BUY' and not self.position:
                    allowed, block_reason = self._entry_guard_allows(action, current_bar, current_price, timestamp)
                    if not allowed:
                        action = "HOLD"
                        reason = block_reason
                            
                    if action == 'BUY':
                        self._enter_trade(current_bar, timestamp, reason)
                elif action == 'SELL' and self.position:
                    self._exit_trade(current_bar, timestamp, reason)

            self.equity_history.append({
                'time': self._to_utc_iso(timestamp),
                'equity': self.equity + (self._get_unrealized_pl(current_price=current_bar['Close']) if self.position else 0)
            })

        # Force close any open position at the end of the backtest
        if self.position:
            last_bar = self.df.iloc[-1]
            last_timestamp = self.df.index[-1]
            self._exit_trade(last_bar, last_timestamp, 'End of Backtest')
        results = self._calculate_metrics()
        if "summary" in results:
            s = results["summary"]
            print(f"\n[backtest] COMPLETED for {self.ticker}")
            print(f"[backtest] ROI: {s['roi_pct']}% | Trades: {s['total_trades']} | Win Rate: {s['win_rate_pct']}%")
        return results

    def _enter_trade_ai(self, bar, timestamp, ai_decision):
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
        if not stop_loss:
            stop_loss = round(price - atr * self.atr_stop_multiplier, precision)
        if not take_profit:
            take_profit = round(price + atr * self.atr_take_profit_multiplier, precision)
            
        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'reason': ai_decision.get("summary", "AI Autopilot Entry")
        }
        print(f"[{self._to_central_str(timestamp)}] BUY  {self.ticker} @ ${price:.2f} | Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f} | Reason: AI Autopilot Entry (Conf: {ai_decision.get('confidence'):.0%})")

    def _enter_trade(self, bar, timestamp, reason):
        price = bar['Close']
        atr = bar['ATR']
        if atr <= 0:
            atr = price * 0.01  # Fallback: treat 1% of price as synthetic ATR

        stop_dist = atr * self.atr_stop_multiplier
        if stop_dist <= 0:
            stop_dist = price * 0.01

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
            stop_loss = round(price * (1 - self.regular_stop_loss_pct), precision)
            take_profit = round(price * (1 + self.regular_take_profit_pct), precision)
        elif self.sell_mode in ['custom_sltp', 'custom_underlying_sltp']:
            stop_loss = round(self.custom_stop_loss_price, precision) if self.custom_stop_loss_price > 0 else round(price - stop_dist, precision)
            take_profit = round(self.custom_take_profit_price, precision) if self.custom_take_profit_price > 0 else round(price + (atr * self.atr_take_profit_multiplier), precision)
        else:
            stop_loss = round(price - stop_dist, precision)
            take_profit = round(price + (atr * self.atr_take_profit_multiplier), precision)

        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': stop_loss, 
            'take_profit': take_profit, 
            'initial_stop_loss': stop_loss,
            'initial_take_profit': take_profit,
            'reason': reason
        }
        print(f"[{self._to_central_str(timestamp)}] BUY  {self.ticker} @ ${price:.2f} | Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f} | Reason: {reason}")

    def _exit_trade(self, bar, timestamp, reason, exit_price_override=None, exit_stop_price=None):
        if not self.position: return
        exit_price = exit_price_override if exit_price_override is not None else bar['Close']
        entry_cost = self.position['qty'] * self.position['entry_price']
        exit_value = self.position['qty'] * exit_price
        
        # 0.1% Fee per side for Crypto (Entry + Exit), 0.0% for Stocks
        clean_ticker = self.ticker.upper().replace("/", "")
        is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        fee = round((entry_cost + exit_value) * 0.001, 2) if is_crypto else 0.0
        pl = (exit_price - self.position['entry_price']) * self.position['qty'] - fee
        pl_pct = (pl / entry_cost) * 100
        self.equity += (exit_value - entry_cost - fee)
        
        stop_note = ""
        if exit_stop_price is not None:
            initial_stop = self.position.get('initial_stop_loss')
            if initial_stop is not None and abs(float(exit_stop_price) - float(initial_stop)) > 1e-9:
                stop_note = f" | Stop Used: ${float(exit_stop_price):.2f} (initial ${float(initial_stop):.2f})"
            else:
                stop_note = f" | Stop Used: ${float(exit_stop_price):.2f}"
        print(f"[{self._to_central_str(timestamp)}] SELL {self.ticker} @ ${exit_price:.2f} | P/L: ${pl:.2f} ({pl_pct:+.2f}%) | Fee: ${fee:.2f} | Reason: {reason}{stop_note}")

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
            'stop_loss': round(self.position.get('stop_loss', 0), 8 if is_crypto else 2),
            'take_profit': round(self.position.get('take_profit', 0), 8 if is_crypto else 2),
            'initial_stop_loss': round(self.position.get('initial_stop_loss', 0), 8 if is_crypto else 2),
            'initial_take_profit': round(self.position.get('initial_take_profit', 0), 8 if is_crypto else 2),
            'exit_stop_price': round(exit_stop_price, 8 if is_crypto else 2) if exit_stop_price is not None else None,
        })
        self.position = None
        self.last_exit_time = timestamp
        if self.allocation_mode == "compound":
            self.current_trade_amount = max(1.0, self.current_trade_amount + pl)
        if self.enable_signal_reset:
            self.signal_locked_direction = "BUY"

    def _cooldown_block_reason(self, timestamp):
        if not self.enable_cooldown or not self.last_exit_time:
            return None
        from engine import parse_timeframe_to_seconds
        tf_seconds = parse_timeframe_to_seconds(self.timeframe)
        cooldown_secs = self.cooldown_minutes * 60 if self.cooldown_minutes is not None else tf_seconds * 4
        if (timestamp - self.last_exit_time).total_seconds() < cooldown_secs:
            return "Trade Cooldown Active"
        return None

    def _entry_guard_allows(self, action, bar, current_price, timestamp):
        if action != "BUY":
            return True, None

        if self.enable_signal_reset and self.signal_locked_direction == "BUY":
            return False, f"Signal Reset Active: waiting for bullish signal reset below {self.threshold}"

        cooldown_reason = self._cooldown_block_reason(timestamp)
        if cooldown_reason:
            return False, cooldown_reason

        return self._smart_reentry_allows(bar, current_price)

    def _smart_reentry_allows(self, bar, current_price):
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
            if distance_pct > self.smart_reentry_vwap_call_max_dist_pct:
                return False, f"Smart Re-entry VWAP Filter: distance {distance_pct:.2f}% > max {self.smart_reentry_vwap_call_max_dist_pct:.2f}%"
            return True, None

        rsi = bar.get("RSI", None)
        try:
            rsi = float(rsi)
        except (TypeError, ValueError):
            return True, None
        if rsi > self.smart_reentry_rsi_call_max:
            return False, f"Smart Re-entry RSI Filter: RSI {rsi:.1f} > max {self.smart_reentry_rsi_call_max:.1f}"
        return True, None

    def _check_exit_conditions(self, bar, timestamp):
        if bar['Low'] <= self.position['stop_loss']:
            self._exit_trade({'Close': self.position['stop_loss']}, timestamp, "Stop Loss Hit")
        elif bar['High'] >= self.position['take_profit']:
            self._exit_trade({'Close': self.position['take_profit']}, timestamp, "Take Profit Hit")

    def _get_unrealized_pl(self, current_price):
        return (current_price - self.position['entry_price']) * self.position['qty']

    def _calculate_metrics(self):
        if not self.trades:
            start_price = self.df.iloc[0]['Close']
            end_price = self.df.iloc[-1]['Close']
            buy_hold_equity = (end_price / start_price) * self.initial_capital
            buy_hold_roi = ((end_price - start_price) / start_price) * 100

            total_bars = len(self.df)
            step = max(1, total_bars // 500)
            candles = []
            equity_curve_downsampled = []
            for i in range(0, total_bars, step):
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
                            **_chart_indicator_fields(row, digits),
                            "daily_high_low_high": _safe_chart_level(row, "Daily_HighLow_High", 8 if "USD" in self.ticker.upper() else 4),
                            "daily_high_low_low": _safe_chart_level(row, "Daily_HighLow_Low", 8 if "USD" in self.ticker.upper() else 4),
                            "daily_high_low_high_trigger": _safe_chart_level(row, "Daily_HighLow_High_Trigger", 8 if "USD" in self.ticker.upper() else 4),
                            "daily_high_low_low_trigger": _safe_chart_level(row, "Daily_HighLow_Low_Trigger", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_p": _safe_chart_level(row, "Pivot_P", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_r1": _safe_chart_level(row, "Pivot_R1", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_s1": _safe_chart_level(row, "Pivot_S1", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_r2": _safe_chart_level(row, "Pivot_R2", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_s2": _safe_chart_level(row, "Pivot_S2", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_r3": _safe_chart_level(row, "Pivot_R3", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_s3": _safe_chart_level(row, "Pivot_S3", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_r4": _safe_chart_level(row, "Pivot_R4", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_s4": _safe_chart_level(row, "Pivot_S4", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_r5": _safe_chart_level(row, "Pivot_R5", 8 if "USD" in self.ticker.upper() else 4),
                            "pivot_s5": _safe_chart_level(row, "Pivot_S5", 8 if "USD" in self.ticker.upper() else 4),
                            "cdl_name": str(row.get('cdl_name', 'Neutral')),
                            "cdl_bullish": bool(row.get('cdl_bullish', False)),
                            "cdl_bearish": bool(row.get('cdl_bearish', False)),
                        })
                        equity_curve_downsampled.append({
                            "time": self._to_utc_iso(ts),
                            "equity": self.initial_capital,
                        })
                except Exception:
                    continue

            # Mask out candlestick patterns for all candles except the single last candle to eliminate chart noise
            if len(candles) > 1:
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
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

        # Avg Win / Avg Loss
        avg_win  = round(winning_pl.mean(), 2) if len(winning_pl) > 0 else 0.0
        avg_loss = round(losing_pl.mean(),  2) if len(losing_pl)  > 0 else 0.0
        win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else float('inf')

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

        # Downsample to max 500 bars to keep JSON payload manageable (~150KB)
        total_bars = len(self.df)
        step = max(1, total_bars // 500)
        candles = []
        equity_curve_downsampled = []
        for i in range(0, total_bars, step):
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
                        **_chart_indicator_fields(row, digits),
                        "daily_high_low_high": _safe_chart_level(row, "Daily_HighLow_High", 8 if "USD" in self.ticker.upper() else 4),
                        "daily_high_low_low": _safe_chart_level(row, "Daily_HighLow_Low", 8 if "USD" in self.ticker.upper() else 4),
                        "daily_high_low_high_trigger": _safe_chart_level(row, "Daily_HighLow_High_Trigger", 8 if "USD" in self.ticker.upper() else 4),
                        "daily_high_low_low_trigger": _safe_chart_level(row, "Daily_HighLow_Low_Trigger", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_p": _safe_chart_level(row, "Pivot_P", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_r1": _safe_chart_level(row, "Pivot_R1", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_s1": _safe_chart_level(row, "Pivot_S1", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_r2": _safe_chart_level(row, "Pivot_R2", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_s2": _safe_chart_level(row, "Pivot_S2", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_r3": _safe_chart_level(row, "Pivot_R3", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_s3": _safe_chart_level(row, "Pivot_S3", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_r4": _safe_chart_level(row, "Pivot_R4", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_s4": _safe_chart_level(row, "Pivot_S4", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_r5": _safe_chart_level(row, "Pivot_R5", 8 if "USD" in self.ticker.upper() else 4),
                        "pivot_s5": _safe_chart_level(row, "Pivot_S5", 8 if "USD" in self.ticker.upper() else 4),
                        "cdl_name": str(row.get('cdl_name', 'Neutral')),
                        "cdl_bullish": bool(row.get('cdl_bullish', False)),
                        "cdl_bearish": bool(row.get('cdl_bearish', False)),
                    })
                    
                    # Grab perfectly aligned equity point
                    eq_idx = i - 30
                    if 0 <= eq_idx < len(self.equity_history):
                        eq_val = self.equity_history[eq_idx]['equity']
                    else:
                        eq_val = self.initial_capital
                        
                    equity_curve_downsampled.append({
                        "time": self._to_utc_iso(ts),
                        "equity": eq_val
                    })
            except Exception:
                continue

        # Mask out candlestick patterns for all candles except the single last candle to eliminate chart noise
        if len(candles) > 1:
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
