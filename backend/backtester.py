import pandas as pd
import numpy as np
from datetime import datetime
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_signals
from trader import get_confluence_decision
import config

class Backtester:
    def __init__(self, ticker, timeframe, start_date, end_date, initial_capital=1000.0, threshold=5, sell_threshold=3, enabled_indicators=None, risk_per_trade=1.0, max_pos_pct=1.0):
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

        # 2. Pre-calculate indicators
        result = calculate_indicators_for_df(self.df, self.timeframe, self.ticker)
        if not result:
            return {"error": "Failed to calculate indicators."}
        
        self.df = result['df']
        
        # 3. Simulate bar-by-bar
        # Indicators need warm-up. We start evaluation after the first 30 bars, 
        # but the Buy & Hold return is calculated from the very first bar (iloc[0]).
        for i in range(len(self.df)):
            current_bar = self.df.iloc[i]
            timestamp = self.df.index[i]

            # Wait for indicators to warm up (usually 30 bars for RSI/EMA/etc)
            if i < 30:
                continue

            prev_bar = self.df.iloc[i-1]
            
            # Check for SL / TP if in position (DISABLED by user request)
            # if self.position:
            #     self._check_exit_conditions(current_bar, timestamp)

            # Get Strategy Decision
            signals = _generate_signals(current_bar, prev_bar)
            
            # Filter signals based on enabled indicators
            filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
            
            analysis = {
                'bullish_count': sum(1 for s in filtered_signals.values() if s['signal'] == 'BULLISH'),
                'bearish_count': sum(1 for s in filtered_signals.values() if s['signal'] == 'BEARISH'),
                'signals': filtered_signals
            }
            
            # Use Manual Thresholds
            action = "HOLD"
            reason = "Neutral"
            if analysis['bullish_count'] >= self.threshold:
                action = "BUY"
                names = [k for k, v in filtered_signals.items() if v['signal'] == 'BULLISH']
                reason = f"{analysis['bullish_count']} signals ({', '.join(names)})"
            elif analysis['bearish_count'] >= self.sell_threshold:
                action = "SELL"
                names = [k for k, v in filtered_signals.items() if v['signal'] == 'BEARISH']
                reason = f"Manual Sell: {analysis['bearish_count']} signals ({', '.join(names)})"

            if action == 'BUY' and not self.position:
                self._enter_trade(current_bar, timestamp, reason)
            elif action == 'SELL' and self.position:
                self._exit_trade(current_bar, timestamp, reason)

            self.equity_history.append({
                'time': timestamp.strftime("%Y-%m-%d %H:%M"),
                'equity': self.equity + (self._get_unrealized_pl(current_price=current_bar['Close']) if self.position else 0)
            })

        results = self._calculate_metrics()
        if "summary" in results:
            s = results["summary"]
            print(f"\n[backtest] COMPLETED for {self.ticker}")
            print(f"[backtest] ROI: {s['roi_pct']}% | Trades: {s['total_trades']} | Win Rate: {s['win_rate_pct']}%")
        return results

    def _enter_trade(self, bar, timestamp, reason):
        price = bar['Close']
        atr = bar['ATR']
        risk_amount = self.equity * self.risk_per_trade
        stop_dist = atr * config.ATR_STOP_MULTIPLIER
        if stop_dist == 0: return

        qty = risk_amount / stop_dist
        max_notional = self.equity * self.max_pos_pct
        if (qty * price) > max_notional:
            qty = max_notional / price

        self.position = {
            'qty': qty,
            'entry_price': price,
            'entry_time': timestamp,
            'stop_loss': 0, 
            'take_profit': 9999999, 
            'reason': reason
        }
        print(f"[{timestamp.strftime('%Y-%m-%d %H:%M')}] BUY  {self.ticker} @ ${price:.2f} | Qty: {qty:.4f} | Reason: {reason}")

    def _exit_trade(self, bar, timestamp, reason):
        if not self.position: return
        exit_price = bar['Close']
        entry_cost = self.position['qty'] * self.position['entry_price']
        exit_value = self.position['qty'] * exit_price
        
        # 0.1% Fee per side (Entry + Exit)
        fee = round((entry_cost + exit_value) * 0.001, 2)
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
            'reason': reason
        })
        self.position = None
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
        total_pl = df_trades['pl'].sum()
        roi = (total_pl / self.initial_capital) * 100
        
        # Calculate Buy & Hold from the very first available bar
        start_price = self.df.iloc[0]['Close']
        end_price = self.df.iloc[-1]['Close']
        buy_hold_equity = (end_price / start_price) * self.initial_capital
        buy_hold_roi = ((end_price - start_price) / start_price) * 100
        
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
            "trades": self.trades[::-1]
        }
