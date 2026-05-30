import pandas as pd
import numpy as np
from datetime import datetime
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_signals
from trader import get_confluence_decision
import config as global_config
from user_config import get_user_config

class Backtester:
    def __init__(self, ticker, timeframe, start_date, end_date, initial_capital=1000.0, threshold=5, sell_threshold=3, enabled_indicators=None, risk_per_trade=0.02, max_pos_pct=0.25, ext_hours=True, sell_mode="indicator", atr_stop_multiplier=2.0, atr_trail_multiplier=3.0, atr_take_profit_multiplier=4.0, ai_autopilot=False, ai_interval=10, ai_model="llama-3.3-70b-versatile"):
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
        self.sell_mode = sell_mode
        self.atr_stop_multiplier = atr_stop_multiplier
        self.atr_trail_multiplier = atr_trail_multiplier
        self.atr_take_profit_multiplier = atr_take_profit_multiplier
        self.ai_autopilot = ai_autopilot or (sell_mode == "ai_autopilot")
        self.ai_interval = ai_interval
        self.ai_model = ai_model
        self._last_ai_decision = None
        
        self.equity = initial_capital
        self.position = None 
        self.trades = []
        self.equity_history = []
        self.df = None

    def run(self):
        # 1. Fetch Data
        self.df = get_historical_data(self.ticker, self.timeframe, self.start_date, self.end_date)
        if self.df is None or self.df.empty:
            return {"error": "No data found for the selected range."}

        # Filter for Regular Hours (RTH) if stocks & self.ext_hours is False and not daily
        clean_ticker = self.ticker.upper().replace("/", "")
        is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        if not is_crypto and not self.ext_hours and self.timeframe != "1Day":
            try:
                time_idx = self.df.index.time
                start_time = pd.to_datetime("09:30:00").time()
                end_time = pd.to_datetime("16:00:00").time()
                rth_mask = (time_idx >= start_time) & (time_idx <= end_time)
                self.df = self.df[rth_mask]
                print(f"[backtest] Filtered for Regular Trading Hours (RTH) only. {len(self.df)} bars remaining.")
            except Exception as rth_exc:
                print(f"[backtest] RTH filtering failed: {rth_exc}")

        # 2. Pre-calculate indicators
        result = calculate_indicators_for_df(self.df, self.timeframe, self.ticker)
        if not result:
            return {"error": "Failed to calculate indicators."}
        
        self.df = result['df']
        
        # 3. Simulate bar-by-bar
        for i in range(len(self.df)):
            current_bar = self.df.iloc[i]
            timestamp = self.df.index[i]

            # Wait for indicators to warm up (usually 30 bars for RSI/EMA/etc)
            if i < 30:
                continue

            prev_bar = self.df.iloc[i-1]
            current_price = current_bar['Close']
            
            # Check for SL / TP if in position
            if self.position:
                # Standard / AI Autopilot hard stop/target checks
                if current_bar['Low'] <= self.position['stop_loss']:
                    self._exit_trade(current_bar, timestamp, f"Stop Loss Hit (${self.position['stop_loss']:.2f})", exit_price_override=self.position['stop_loss'])
                    self._last_ai_decision = None  # Reset AI decision after exit
                    continue
                elif current_bar['High'] >= self.position['take_profit']:
                    self._exit_trade(current_bar, timestamp, f"Take Profit Hit (${self.position['take_profit']:.2f})", exit_price_override=self.position['take_profit'])
                    self._last_ai_decision = None  # Reset AI decision after exit
                    continue
                
                # Dynamic Trailing Stop movement for standard hybrid/sltp modes
                if self.sell_mode in ["sltp", "hybrid", "trend_rider"]:
                    atr = current_bar['ATR']
                    trail_distance = atr * self.atr_trail_multiplier
                    if self.position.get('qty', 0) > 0:
                        new_stop = current_price - trail_distance
                        if new_stop > self.position['stop_loss']:
                            self.position['stop_loss'] = round(new_stop, 2)

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
                        self._enter_trade_ai(current_bar, timestamp, self._last_ai_decision)
                        self._last_ai_decision = None  # Reset to re-evaluate next bars
                    elif action == "SELL" and self.position:
                        self._exit_trade(current_bar, timestamp, f"AI Exit: {reason}")
                        self._last_ai_decision = None  # Reset to re-evaluate next bars
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
                    if analysis['bullish_count'] >= self.threshold:
                        action = "BUY"
                        names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                        reason = f"{analysis['bullish_count']} signals ({', '.join(names)})"
                    elif analysis['bearish_count'] >= self.sell_threshold:
                        if self.sell_mode in ["indicator", "hybrid"]:
                            action = "SELL"
                            names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                            reason = f"Dynamic Sell: {analysis['bearish_count']} signals ({', '.join(names)})"

                if action == 'BUY' and not self.position:
                    self._enter_trade(current_bar, timestamp, reason)
                elif action == 'SELL' and self.position:
                    self._exit_trade(current_bar, timestamp, reason)

            self.equity_history.append({
                'time': timestamp.strftime("%Y-%m-%d %H:%M"),
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
        if not stop_loss:
            stop_loss = round(price - atr * self.atr_stop_multiplier, 2)
        if not take_profit:
            take_profit = round(price + atr * self.atr_take_profit_multiplier, 2)
            
        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'reason': ai_decision.get("summary", "AI Autopilot Entry")
        }
        print(f"[{timestamp.strftime('%Y-%m-%d %H:%M')}] BUY  {self.ticker} @ ${price:.2f} | Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f} | Reason: AI Autopilot Entry (Conf: {ai_decision.get('confidence'):.0%})")

    def _enter_trade(self, bar, timestamp, reason):
        price = bar['Close']
        atr = bar['ATR']
        risk_amount = self.equity * self.risk_per_trade
        stop_dist = atr * self.atr_stop_multiplier
        if stop_dist == 0: return

        qty = risk_amount / stop_dist
        max_notional = self.equity * self.max_pos_pct
        if (qty * price) > max_notional:
            qty = max_notional / price

        # Volatility Regime Sizing Filter: Scale down quantity by 50% if volatility (ATR / Price) is hyper-extended (> 3.0%)
        volatility_ratio = atr / price
        if volatility_ratio > 0.03:
            qty *= 0.5
            reason += " (Volatility Protection Active)"

        stop_loss = round(price - stop_dist, 2)
        take_profit = round(price + (atr * self.atr_take_profit_multiplier), 2)

        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': stop_loss, 
            'take_profit': take_profit, 
            'reason': reason
        }
        print(f"[{timestamp.strftime('%Y-%m-%d %H:%M')}] BUY  {self.ticker} @ ${price:.2f} | Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f} | Reason: {reason}")

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
        
        print(f"[{timestamp.strftime('%Y-%m-%d %H:%M')}] SELL {self.ticker} @ ${exit_price:.2f} | P/L: ${pl:.2f} ({pl_pct:+.2f}%) | Fee: ${fee:.2f} | Reason: {reason}")

        self.trades.append({
            'ticker': self.ticker,
            'entry_time': self.position['entry_time'].strftime("%Y-%m-%d %H:%M"),
            'exit_time': timestamp.strftime("%Y-%m-%d %H:%M"),
            'qty': round(self.position['qty'], 4),
            'entry_price': round(self.position['entry_price'], 2),
            'exit_price': round(exit_price, 2),
            'entry_cost': round(entry_cost, 2),
            'exit_value': round(exit_value, 2),
            'fees': fee,
            'pl': round(pl, 2),
            'pl_pct': round(pl_pct, 2),
            'reason': reason,
            'stop_loss': round(self.position.get('stop_loss', 0), 2),
            'take_profit': round(self.position.get('take_profit', 0), 2),
        })
        self.position = None

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
        # Downsample to max 500 bars to keep JSON payload manageable (~150KB)
        total_bars = len(self.df)
        step = max(1, total_bars // 500)
        candles = []
        for i in range(0, total_bars, step):
            row = self.df.iloc[i]
            ts = self.df.index[i]
            try:
                unix_time = int(ts.timestamp())
                o = float(row.get('Open', row.get('open', 0)))
                h = float(row.get('High', row.get('high', 0)))
                l = float(row.get('Low', row.get('low', 0)))
                c = float(row.get('Close', row.get('close', 0)))
                v = int(row.get('Volume', row.get('volume', 0)))
                if o > 0 and c > 0:
                    candles.append({
                        "time": unix_time,
                        "open": round(o, 4),
                        "high": round(h, 4),
                        "low": round(l, 4),
                        "close": round(c, 4),
                        "volume": v,
                    })
            except Exception:
                continue

        return {
            "summary": {
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
                "worst_trade": round(df_trades['pl'].min(), 2)
            },
            "equity_curve": self.equity_history[::max(1, len(self.equity_history)//200)],
            "trades": self.trades[::-1],
            "candles": candles,
        }
