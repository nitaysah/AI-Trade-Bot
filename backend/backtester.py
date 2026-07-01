import pandas as pd
import numpy as np
from datetime import datetime
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_signals
from swing_strategy import build_entry_signal
from trader import get_confluence_decision
import config as global_config
from user_config import get_user_config

class Backtester:
    def __init__(self, ticker, timeframe, start_date, end_date, initial_capital=1000.0, threshold=5, sell_threshold=3, enabled_indicators=None, risk_per_trade=0.02, max_pos_pct=0.25, ext_hours=True, buy_mode="indicator", custom_buy_price=0.0, sell_mode="indicator", atr_stop_multiplier=2.0, atr_trail_multiplier=3.0, atr_take_profit_multiplier=4.0, ai_autopilot=False, ai_interval=10, ai_model="llama-3.3-70b-versatile", enable_cooldown=True, cooldown_minutes=None, regular_stop_loss_pct=0.05, regular_take_profit_pct=0.10, regular_trailing_pct=0.03, cancel_event=None, entry_atr_multiplier=None, entry_pct_move=None, entry_atr_style="pullback_reclaim", entry_pct_style="dip_reclaim", swing_entry_mode="bounce_retest", swing_entry_lookback_days=20, market_session="Regular"):
        self.ticker = ticker
        self.timeframe = timeframe
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.threshold = threshold
        self.sell_threshold = sell_threshold
        self.enabled_indicators = enabled_indicators or []
        self.risk_per_trade = risk_per_trade
        self.max_pos_pct = max_pos_pct
        self.ext_hours = ext_hours
        self.buy_mode = buy_mode
        self.custom_buy_price = custom_buy_price
        self.sell_mode = sell_mode
        self.atr_stop_multiplier = atr_stop_multiplier
        self.atr_trail_multiplier = atr_trail_multiplier
        self.atr_take_profit_multiplier = atr_take_profit_multiplier
        self.entry_atr_multiplier = entry_atr_multiplier or atr_stop_multiplier
        self.entry_pct_move = entry_pct_move or max(1.0, regular_stop_loss_pct * 100.0)
        self.entry_atr_style = entry_atr_style or "pullback_reclaim"
        self.entry_pct_style = entry_pct_style or "dip_reclaim"
        self.swing_entry_mode = swing_entry_mode or "bounce_retest"
        self.swing_entry_lookback_days = swing_entry_lookback_days or 20
        self.market_session = market_session or "Regular"
        self.ai_autopilot = ai_autopilot or (sell_mode == "ai_autopilot")
        self.ai_interval = ai_interval
        self.ai_model = ai_model
        self.enable_cooldown = enable_cooldown
        self.cooldown_minutes = cooldown_minutes
        self.regular_stop_loss_pct = regular_stop_loss_pct
        self.regular_take_profit_pct = regular_take_profit_pct
        self.regular_trailing_pct = regular_trailing_pct
        self.cancel_event = cancel_event
        self._last_ai_decision = None
        self.last_exit_time = None
        
        self.equity = initial_capital
        self.position = None 
        self.trades = []
        self.equity_history = []
        self.df = None

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
            result = calculate_indicators_for_df(raw_df, self.timeframe, self.ticker)
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
                        self._exit_trade(current_bar, timestamp, f"Stop Loss Hit (${self.position['stop_loss']:.{precision}f})", exit_price_override=self.position['stop_loss'])
                        self._last_ai_decision = None
                        continue
                    elif current_bar['High'] >= self.position['take_profit']:
                        self._exit_trade(current_bar, timestamp, f"Take Profit Hit (${self.position['take_profit']:.{precision}f})", exit_price_override=self.position['take_profit'])
                        self._last_ai_decision = None
                        continue

                # ── NEW: Percentage-based hard stops ────────────────────────────
                elif self.sell_mode in ['percentage', 'hybrid_pct']:
                    # Ratchet trailing stop upward
                    candidate = round(current_price * (1 - self.regular_trailing_pct), precision)
                    if candidate > self.position['stop_loss']:
                        self.position['stop_loss'] = candidate
                    
                    if current_bar['Low'] <= self.position['stop_loss']:
                        self._exit_trade(current_bar, timestamp, f"Percentage Stop Loss Hit ({self.regular_stop_loss_pct*100:.1f}% SL / {self.regular_trailing_pct*100:.1f}% Trail)", exit_price_override=self.position['stop_loss'])
                        continue
                    elif current_bar['High'] >= self.position['take_profit']:
                        self._exit_trade(current_bar, timestamp, f"Percentage Take Profit Hit ({self.regular_take_profit_pct*100:.1f}% TP)", exit_price_override=self.position['take_profit'])
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

                # ── NEW: Daily High/Low Breakout ─────────────────────────────────
                elif self.sell_mode in ['high_low', 'hybrid_high_low']:
                    if i > 0:
                        prev_bar_data = self.df.iloc[i - 1]
                        prev_h = float(prev_bar_data.get('High', current_price))
                        prev_l = float(prev_bar_data.get('Low', current_price))
                        if current_price < prev_l:
                            self._exit_trade(current_bar, timestamp, f"Prior Low Breakout (Low=${prev_l:.{precision}f})", exit_price_override=current_price)
                            continue
                        elif current_price > prev_h:
                            self._exit_trade(current_bar, timestamp, f"Prior High Breakout (High=${prev_h:.{precision}f})", exit_price_override=current_price)
                            continue

                # ── ATR trailing stop movement (existing) ───────────────────────
                if self.sell_mode in ["sltp", "hybrid", "trend_rider"]:
                    atr = current_bar['ATR']
                    trail_distance = atr * self.atr_trail_multiplier
                    if self.position.get('qty', 0) > 0:
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
                    
                    if action == "BUY" and not self.position:
                        if self.enable_cooldown and self.last_exit_time:
                            from engine import parse_timeframe_to_seconds
                            tf_seconds = parse_timeframe_to_seconds(self.timeframe)
                            cooldown_secs = self.cooldown_minutes * 60 if self.cooldown_minutes is not None else tf_seconds * 4
                            if (timestamp - self.last_exit_time).total_seconds() < cooldown_secs:
                                action = "HOLD"
                                reason = "Trade Cooldown Active"

                        if action == "BUY":
                            self._enter_trade_ai(current_bar, timestamp, self._last_ai_decision)
                            self._last_ai_decision = None  # Reset to re-evaluate next bars
                    elif action == "SELL" and self.position:
                        # Hybrid / Dynamic Sell check: exit only if sell_mode supports indicator/hybrid
                        if self.sell_mode in ["indicator", "hybrid", "ai_autopilot"]:
                            self._exit_trade(current_bar, timestamp, f"AI Exit: {reason}")
                            self._last_ai_decision = None  # Reset to re-evaluate next bars
                        else:
                            # SL/TP only mode: ignore dynamic sell signal
                            self._last_ai_decision = None
            else:
                # Get Strategy Decision
                signals = _generate_signals(current_bar, prev_bar)
                
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
                        )
                        signal_action = entry_signal.get("action", "HOLD")
                        if signal_action != "HOLD":
                            action = signal_action
                            reason = entry_signal.get("reason", reason)
                        elif entry_mode.startswith("hybrid_"):
                            if analysis['bullish_count'] >= self.threshold:
                                action = "BUY"
                                names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                                reason = f"{analysis['bullish_count']} signals ({', '.join(names)})"
                            elif analysis['bearish_count'] >= self.sell_threshold:
                                action = "SELL"
                                names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                                reason = f"{analysis['bearish_count']} signals ({', '.join(names)})"
                        elif entry_mode == 'custom_price':
                            if analysis['bearish_count'] >= self.sell_threshold:
                                action = "SELL"
                                names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                                reason = f"Dynamic Sell: {analysis['bearish_count']} signals ({', '.join(names)})"
                    if action == 'HOLD' and entry_mode not in price_action_modes:
                        if self.buy_mode == 'custom_price':
                            if current_price > 0 and current_price <= self.custom_buy_price:
                                action = "BUY"
                                reason = f"Custom Price Entry: Price {current_price} <= {self.custom_buy_price}"
                        else:
                            if analysis['bullish_count'] >= self.threshold:
                                action = "BUY"
                                names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                                reason = f"{analysis['bullish_count']} signals ({', '.join(names)})"
                                
                        if action != "BUY" and analysis['bearish_count'] >= self.sell_threshold:
                            if self.sell_mode in ["indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low"]:
                                action = "SELL"
                                names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                                reason = f"Dynamic Sell: {analysis['bearish_count']} signals ({', '.join(names)})"

                if action == 'BUY' and not self.position:
                    if self.enable_cooldown and self.last_exit_time:
                        from engine import parse_timeframe_to_seconds
                        tf_seconds = parse_timeframe_to_seconds(self.timeframe)
                        cooldown_secs = self.cooldown_minutes * 60 if self.cooldown_minutes is not None else tf_seconds * 4
                        if (timestamp - self.last_exit_time).total_seconds() < cooldown_secs:
                            action = "HOLD"
                            reason = "Trade Cooldown Active"
                            
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

        risk_amount = self.equity * self.risk_per_trade
        stop_dist = atr * self.atr_stop_multiplier
        if stop_dist <= 0:
            stop_dist = price * 0.01

        qty = risk_amount / stop_dist
        max_notional = self.equity * self.max_pos_pct
        if (qty * price) > max_notional:
            qty = max_notional / price

        # Volatility Regime Sizing Filter: Scale down quantity by 50% if volatility (ATR / Price) is hyper-extended (> 3.0%)
        volatility_ratio = atr / price
        if volatility_ratio > 0.03:
            qty *= 0.5
            reason += " (Volatility Protection Active)"

        precision = 6 if price < 1.0 else 2

        if self.sell_mode in ['percentage', 'hybrid_pct']:
            stop_loss = round(price * (1 - self.regular_stop_loss_pct), precision)
            take_profit = round(price * (1 + self.regular_take_profit_pct), precision)
        else:
            stop_loss = round(price - stop_dist, precision)
            take_profit = round(price + (atr * self.atr_take_profit_multiplier), precision)

        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': stop_loss, 
            'take_profit': take_profit, 
            'reason': reason
        }
        print(f"[{self._to_central_str(timestamp)}] BUY  {self.ticker} @ ${price:.2f} | Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f} | Reason: {reason}")

    def _exit_trade(self, bar, timestamp, reason, exit_price_override=None):
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
        
        print(f"[{self._to_central_str(timestamp)}] SELL {self.ticker} @ ${exit_price:.2f} | P/L: ${pl:.2f} ({pl_pct:+.2f}%) | Fee: ${fee:.2f} | Reason: {reason}")

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
        })
        self.position = None
        self.last_exit_time = timestamp

    def _check_exit_conditions(self, bar, timestamp):
        if bar['Low'] <= self.position['stop_loss']:
            self._exit_trade({'Close': self.position['stop_loss']}, timestamp, "Stop Loss Hit")
        elif bar['High'] >= self.position['take_profit']:
            self._exit_trade({'Close': self.position['take_profit']}, timestamp, "Take Profit Hit")

    def _get_unrealized_pl(self, current_price):
        return (current_price - self.position['entry_price']) * self.position['qty']

    def _calculate_metrics(self):
        if not self.trades:
            return {"error": "No trades executed during the period. Try lowering your 'Buy Threshold' or enabling more indicators."}
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
                    candles.append({
                        "time": unix_time,
                        "open": round(o, 8 if "USD" in self.ticker.upper() else 4),
                        "high": round(h, 8 if "USD" in self.ticker.upper() else 4),
                        "low": round(l, 8 if "USD" in self.ticker.upper() else 4),
                        "close": round(c, 8 if "USD" in self.ticker.upper() else 4),
                        "volume": v,
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

        return {
            "summary": {
                "ticker": self.ticker,
                "timeframe": self.timeframe,
                "period_days": (self.end_date - self.start_date).days,
                "start_date_utc": self.df.index[0].isoformat(),
                "end_date_utc": self.df.index[-1].isoformat(),
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
