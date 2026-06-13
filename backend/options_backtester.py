import pandas as pd
import numpy as np
import math
import datetime
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_signals
from options_pricing import black_scholes, calculate_historical_volatility

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
        options_type: str = "both",
        stop_loss_pct: float = 0.50,
        take_profit_pct: float = 1.00,
        exit_strategy: str = "hybrid",
        close_before_expiry_hours: float = 2.0,
        threshold: int = 5,
        sell_threshold: int = 3,
        enabled_indicators: list = None,
        ext_hours: bool = True,
        r: float = 0.05,
        cancel_event=None
    ):
        self.ticker = ticker
        self.timeframe = timeframe
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.trade_amount = trade_amount
        self.expiry_days = expiry_days
        self.strike_type = strike_type
        self.options_type = options_type
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.exit_strategy = exit_strategy  # "premium_pct", "pivot_points", "high_low", "hybrid"
        self.close_before_expiry_hours = close_before_expiry_hours
        self.threshold = threshold
        self.sell_threshold = sell_threshold
        self.enabled_indicators = enabled_indicators or []
        self.ext_hours = ext_hours
        self.r = r
        self.cancel_event = cancel_event
        
        # Real-market pricing proxy parameters
        self.q = 0.013 # SPY/broad market approx dividend yield
        self.iv_markup = 0.04 # 4% markup for Implied Volatility over Historical
        self.spread_width = 0.03 # Typical $0.03 spread on liquid ETF options

        self.equity = initial_capital
        self.position = None  # None or Dict representing option position
        self.trades = []
        self.equity_history = []
        self.raw_df = df
        self.df = None

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
            # Fetch with 100 days padding
            padded_start = self.start_date - datetime.timedelta(days=100)
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
            res = calculate_indicators_for_df(raw_df, self.timeframe, self.ticker)
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
        rolling_vol = rolling_vol.fillna(0.15).values
        full_df['Historical_Vol'] = rolling_vol

        # 4. Map daily high/low/close for previous-day pivot and range exits
        df_dates = pd.Series(full_df.index.date, index=full_df.index)
        daily_groups = full_df.groupby(df_dates)
        daily_highs = daily_groups['High'].max()
        daily_lows = daily_groups['Low'].min()
        daily_closes = daily_groups['Close'].last()

        unique_dates = sorted(list(daily_highs.index))
        prev_day_data = {}
        for idx, dt in enumerate(unique_dates):
            if idx == 0:
                prev_day_data[dt] = {
                    "High": daily_highs.iloc[0],
                    "Low": daily_lows.iloc[0],
                    "Close": daily_closes.iloc[0]
                }
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

        # 5. Simulate bar-by-bar options trading
        self._today_high = None
        self._today_low = None
        self._last_date = None

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
            current_date = timestamp.date()
            if self._last_date != current_date:
                self._today_high = current_bar['High']
                self._today_low = current_bar['Low']
                self._last_date = current_date
            else:
                self._today_high = max(self._today_high, current_bar['High'])
                self._today_low = min(self._today_low, current_bar['Low'])

            # Pre-fetch previous day boundaries
            p_data = prev_day_data.get(current_date, prev_day_data[unique_dates[0]])
            prev_h = p_data["High"]
            prev_l = p_data["Low"]
            prev_c = p_data["Close"]

            # Check exits if position is open
            if self.position:
                opt_type = self.position['option_type']
                strike = self.position['strike_price']
                entry_date = self.position['entry_time']

                # Compute current option premium with IV markup and dividend yield
                hours_elapsed = (timestamp - entry_date).total_seconds() / 3600.0
                t_remaining = max(0.00001, (self.expiry_days * 24.0 - hours_elapsed) / (365.0 * 24.0))
                opt_price_bs = black_scholes(current_price, strike, t_remaining, self.r, current_vol + self.iv_markup, opt_type, q=self.q)
                opt_price = max(0.01, opt_price_bs - (self.spread_width / 2.0)) # Sell at the bid
                
                # Check exit triggers
                exit_triggered = False
                exit_reason = ""
                
                # Expiration safeguard
                if hours_elapsed >= (self.expiry_days * 24.0 - self.close_before_expiry_hours):
                    exit_triggered = True
                    exit_reason = f"Expiration Safeguard ({self.close_before_expiry_hours}h remaining)"

                # Strategy A: Option Premium Percent-based TP/SL
                elif self.exit_strategy in ["premium_pct", "hybrid"]:
                    if opt_price <= self.position['entry_premium'] * (1 - self.stop_loss_pct):
                        exit_triggered = True
                        exit_reason = f"Premium Stop Loss Hit (-{self.stop_loss_pct*100:.1f}%)"
                    elif opt_price >= self.position['entry_premium'] * (1 + self.take_profit_pct):
                        exit_triggered = True
                        exit_reason = f"Premium Take Profit Hit (+{self.take_profit_pct*100:.1f}%)"

                # Strategy B: Dynamic Pivot Point Exit
                if not exit_triggered and self.exit_strategy in ["pivot_points", "hybrid"]:
                    P = (prev_h + prev_l + prev_c) / 3.0
                    R1 = 2 * P - prev_l
                    S1 = 2 * P - prev_h
                    R2 = P + (prev_h - prev_l)
                    S2 = P - (prev_h - prev_l)

                    if opt_type == "CALL":
                        if current_bar['High'] >= R1:
                            exit_triggered = True
                            exit_reason = f"Pivot Take Profit Hit (Underlying High >= R1: {R1:.2f})"
                        elif current_bar['High'] >= R2:
                            exit_triggered = True
                            exit_reason = f"Pivot Take Profit Hit (Underlying High >= R2: {R2:.2f})"
                        elif current_bar['Low'] <= S1:
                            exit_triggered = True
                            exit_reason = f"Pivot Stop Loss Hit (Underlying Low <= S1: {S1:.2f})"
                    else: # PUT
                        if current_bar['Low'] <= S1:
                            exit_triggered = True
                            exit_reason = f"Pivot Take Profit Hit (Underlying Low <= S1: {S1:.2f})"
                        elif current_bar['Low'] <= S2:
                            exit_triggered = True
                            exit_reason = f"Pivot Take Profit Hit (Underlying Low <= S2: {S2:.2f})"
                        elif current_bar['High'] >= R1:
                            exit_triggered = True
                            exit_reason = f"Pivot Stop Loss Hit (Underlying High >= R1: {R1:.2f})"

                # Strategy C: Daily High/Low ranges
                if not exit_triggered and self.exit_strategy in ["high_low", "hybrid"]:
                    # Intraday Check
                    if opt_type == "CALL" and current_price < self._today_low:
                        exit_triggered = True
                        exit_reason = f"Intraday Low Breach (Underlying Close < Today's Low: {self._today_low:.2f})"
                    elif opt_type == "PUT" and current_price > self._today_high:
                        exit_triggered = True
                        exit_reason = f"Intraday High Breach (Underlying Close > Today's High: {self._today_high:.2f})"
                    
                    # Prev Day Check
                    if not exit_triggered:
                        if opt_type == "CALL":
                            if current_bar['High'] >= prev_h:
                                exit_triggered = True
                                exit_reason = f"Prev Day High Breakout (Underlying High >= {prev_h:.2f})"
                            elif current_bar['Low'] <= prev_l:
                                exit_triggered = True
                                exit_reason = f"Prev Day Low Breakdown (Underlying Low <= {prev_l:.2f})"
                        else: # PUT
                            if current_bar['Low'] <= prev_l:
                                exit_triggered = True
                                exit_reason = f"Prev Day Low Breakout (Underlying Low <= {prev_l:.2f})"
                            elif current_bar['High'] >= prev_h:
                                exit_triggered = True
                                exit_reason = f"Prev Day High Breakdown (Underlying High >= {prev_h:.2f})"

                # Strategy D: Indicator Reversal (Indicator/Hybrid modes)
                if not exit_triggered and self.exit_strategy in ["indicator", "hybrid"]:
                    signals = _generate_signals(current_bar, prev_bar)
                    filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
                    bearish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BEARISH')
                    bullish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BULLISH')
                    
                    if opt_type == "CALL" and bearish_count >= self.sell_threshold:
                        exit_triggered = True
                        exit_reason = f"Indicator Reversal Signal ({bearish_count} Bearish)"
                    elif opt_type == "PUT" and bullish_count >= self.threshold:
                        exit_triggered = True
                        exit_reason = f"Indicator Reversal Signal ({bullish_count} Bullish)"

                if exit_triggered:
                    self._exit_option_trade(opt_price, current_price, timestamp, exit_reason)
                    continue

            # Evaluate entries if no position is held
            else:
                signals = _generate_signals(current_bar, prev_bar)
                filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
                
                bullish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BULLISH')
                bearish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BEARISH')

                if bullish_count >= self.threshold and self.options_type in ["both", "calls_only"]:
                    # Enter CALL
                    self._enter_option_trade(
                        option_type="CALL",
                        underlying_price=current_price,
                        vol=current_vol,
                        timestamp=timestamp,
                        reason=f"Bullish signals crossover ({bullish_count} Bullish)"
                    )
                elif bearish_count >= self.sell_threshold and self.options_type in ["both", "puts_only"]:
                    # Enter PUT
                    self._enter_option_trade(
                        option_type="PUT",
                        underlying_price=current_price,
                        vol=current_vol,
                        timestamp=timestamp,
                        reason=f"Bearish signals crossover ({bearish_count} Bearish)"
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
            hours_elapsed = (last_ts - self.position['entry_time']).total_seconds() / 3600.0
            t_remaining = max(0.00001, (self.expiry_days * 24.0 - hours_elapsed) / (365.0 * 24.0))
            opt_price_bs = black_scholes(last_bar['Close'], self.position['strike_price'], t_remaining, self.r, last_bar['Historical_Vol'] + self.iv_markup, self.position['option_type'], q=self.q)
            opt_price = max(0.01, opt_price_bs - (self.spread_width / 2.0))
            self._exit_option_trade(opt_price, last_bar['Close'], last_ts, "End of Backtest")

        return self._calculate_metrics()

    def _enter_option_trade(self, option_type: str, underlying_price: float, vol: float, timestamp: datetime.datetime, reason: str):
        # Determine Strike Price
        strike = underlying_price
        if self.strike_type == "ITM":
            strike = underlying_price * 0.98 if option_type == "CALL" else underlying_price * 1.02
        elif self.strike_type == "OTM":
            strike = underlying_price * 1.02 if option_type == "CALL" else underlying_price * 0.98
        
        # Round strike price to nearest integer or 0.50
        strike = round(strike * 2) / 2
        
        # Calculate theoretical option price (premium) at entry with IV markup and dividends
        t_years = (self.expiry_days * 24.0) / (365.0 * 24.0)
        entry_premium_bs = black_scholes(underlying_price, strike, t_years, self.r, vol + self.iv_markup, option_type, q=self.q)
        entry_premium = max(0.01, entry_premium_bs + (self.spread_width / 2.0)) # Pay the ask

        if entry_premium <= 0:
            return

        # Sizing rules: max size within self.trade_amount, but don't exceed cash
        contract_multiplier = 100.0
        single_contract_cost = entry_premium * contract_multiplier

        # Check budget
        budget = min(self.trade_amount, self.equity)
        qty = math.floor(budget / single_contract_cost)

        if qty <= 0:
            # Insufficient allocation budget for even one contract
            return

        total_cost = qty * single_contract_cost
        
        # Dedicate capital from equity
        self.equity -= total_cost

        # Record position
        self.position = {
            "option_type": option_type,
            "strike_price": strike,
            "entry_time": timestamp,
            "entry_underlying": underlying_price,
            "entry_premium": entry_premium,
            "qty": qty,
            "contract_cost": single_contract_cost,
            "total_cost": total_cost,
            "reason": reason
        }

    def _exit_option_trade(self, option_premium: float, underlying_price: float, timestamp: datetime.datetime, reason: str):
        contract_multiplier = 100.0
        exit_value = self.position['qty'] * option_premium * contract_multiplier
        pl = exit_value - self.position['total_cost']
        pl_pct = (pl / self.position['total_cost']) * 100.0

        # Refund equity
        self.equity += exit_value

        self.trades.append({
            'ticker': self.ticker,
            'entry_time': self._to_utc_iso(self.position['entry_time']),
            'exit_time': self._to_utc_iso(timestamp),
            'qty': int(self.position['qty']),
            'option_type': self.position['option_type'],
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
        })
        self.position = None

    def _get_unrealized_pl(self, current_price: float, vol: float, timestamp: datetime.datetime) -> float:
        opt_type = self.position['option_type']
        strike = self.position['strike_price']
        entry_date = self.position['entry_time']

        hours_elapsed = (timestamp - entry_date).total_seconds() / 3600.0
        t_remaining = max(0.00001, (self.expiry_days * 24.0 - hours_elapsed) / (365.0 * 24.0))
        opt_price_bs = black_scholes(current_price, strike, t_remaining, self.r, vol + self.iv_markup, opt_type, q=self.q)
        opt_price = max(0.01, opt_price_bs - (self.spread_width / 2.0))
        
        contract_multiplier = 100.0
        current_value = self.position['qty'] * opt_price * contract_multiplier
        return current_value - self.position['total_cost']

    def _calculate_metrics(self) -> dict:
        if not self.trades:
            return {"error": "No option trades executed during the period. Try lowering thresholds or enabling more indicators."}

        df_trades = pd.DataFrame(self.trades)
        win_rate = (df_trades['pl'] > 0).mean() * 100.0
        total_pl = self.equity - self.initial_capital
        roi = (total_pl / self.initial_capital) * 100.0

        # Calculate Buy & Hold from the first available bar
        start_price = self.df.iloc[0]['Close']
        end_price = self.df.iloc[-1]['Close']
        buy_hold_equity = (end_price / start_price) * self.initial_capital
        buy_hold_roi = ((end_price - start_price) / start_price) * 100.0

        total_bars = len(self.df)
        step = max(1, total_bars // 500)
        candles = []
        for i in range(0, total_bars, step):
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
                "profitable_trades": int(len(df_trades[df_trades['pl'] > 0])),
                "losing_trades": int(len(df_trades[df_trades['pl'] <= 0])),
                "avg_trade_pl": round(df_trades['pl'].mean(), 2),
                "best_trade": round(df_trades['pl'].max(), 2),
                "worst_trade": round(df_trades['pl'].min(), 2)
            },
            "equity_curve": self.equity_history[::max(1, len(self.equity_history)//200)],
            "trades": self.trades[::-1],
            "candles": candles,
        }
