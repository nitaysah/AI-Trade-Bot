"""
Risk Management Module.

Implements production-grade risk controls:
- ATR-based dynamic stop-loss calculation
- Position sizing based on % risk per trade
- Trailing stop management
- Daily drawdown circuit breaker
- Maximum position concentration limits
"""

import config as global_config
from user_config import get_user_config


class RiskManager:
    """Manages all risk calculations and enforces trading rules."""

    def __init__(self):
        self._daily_starting_equity = {}
        self._trading_halted = {}
        self._halt_reason = {}

    @property
    def daily_starting_equity(self):
        uid = get_user_config().uid
        return self._daily_starting_equity.get(uid, None)

    @daily_starting_equity.setter
    def daily_starting_equity(self, value):
        uid = get_user_config().uid
        self._daily_starting_equity[uid] = value

    @property
    def trading_halted(self):
        uid = get_user_config().uid
        return self._trading_halted.get(uid, False)

    @trading_halted.setter
    def trading_halted(self, value):
        uid = get_user_config().uid
        self._trading_halted[uid] = value

    @property
    def halt_reason(self):
        uid = get_user_config().uid
        return self._halt_reason.get(uid, "")

    @halt_reason.setter
    def halt_reason(self, value):
        uid = get_user_config().uid
        self._halt_reason[uid] = value

    # Dynamic fallback properties matching user configs
    @property
    def risk_per_trade(self):
        return getattr(get_user_config(), 'RISK_PER_TRADE', 0.02)

    @property
    def max_daily_drawdown(self):
        return getattr(get_user_config(), 'MAX_DAILY_DRAWDOWN', 0.05)

    @property
    def max_position_pct(self):
        return getattr(get_user_config(), 'MAX_POSITION_PCT', 0.25)

    @property
    def atr_stop_multiplier(self):
        return getattr(get_user_config(), 'ATR_STOP_MULTIPLIER', 2.0)

    def set_daily_equity(self, equity: float):
        """Called at market open to set the baseline for drawdown tracking."""
        self.daily_starting_equity = equity
        self.trading_halted = False
        self.halt_reason = ""

    def check_drawdown(self, current_equity: float, ticker: str = None) -> bool:
        """
        Checks if daily drawdown limit has been breached.
        Returns True if trading should continue, False if halted.
        """
        if self.daily_starting_equity is None or self.daily_starting_equity <= 0:
            return True

        drawdown = (self.daily_starting_equity - current_equity) / self.daily_starting_equity

        # 1. Check Global Drawdown
        if drawdown >= self.max_daily_drawdown:
            self.trading_halted = True
            self.halt_reason = f"Global daily drawdown {drawdown:.1%} >= {self.max_daily_drawdown:.1%} limit"
            return False

        # 2. Check Ticker-Specific Drawdown Override (Stricter limits)
        if ticker:
            t_settings = getattr(get_user_config(), 'TICKER_SETTINGS', {}).get(ticker.upper(), {})
            ticker_max_dd = t_settings.get('max_daily_drawdown')
            if ticker_max_dd is not None and drawdown >= ticker_max_dd:
                self.halt_reason = f"{ticker} specific drawdown limit {drawdown:.1%} >= {ticker_max_dd:.1%} reached"
                return False

        return True

    def calculate_position_size(self, account_equity: float, entry_price: float, atr: float, available_cash: float = None, ticker: str = None, side: str = 'long') -> dict:
        """
        Calculates the optimal position size based on ATR risk or Ticker Overrides.
        Includes a buying power check if available_cash is provided.
        """
        capital_basis = self.daily_starting_equity if (self.daily_starting_equity and self.daily_starting_equity > 0) else account_equity
        if atr <= 0 or entry_price <= 0 or capital_basis <= 0:
            return {
                'shares': 0,
                'notional': 0.0,
                'stop_loss': 0.0,
                'take_profit': 0.0,
                'risk_amount': 0.0,
                'stop_distance': 0.0,
                'error': 'Invalid inputs for position sizing'
            }

        # 0. Check for Per-Ticker Settings Override
        t_settings = getattr(get_user_config(), 'TICKER_SETTINGS', {}).get(ticker.upper(), {}) if ticker else {}
        risk_amount = 0.0
        
        # Use ticker-specific amount if provided, otherwise check legacy TICKER_AMOUNTS
        custom_amount = t_settings.get('amount')
        if custom_amount is None:
            custom_amount = getattr(get_user_config(), 'TICKER_AMOUNTS', {}).get(ticker.upper())

        # Determine Risk Parameters (Ticker-specific or Fallback to Instance Globals)
        risk_pct = t_settings.get('risk_per_trade', self.risk_per_trade)
        stop_mult = t_settings.get('atr_stop_multiplier', self.atr_stop_multiplier)

        # Determine Sell Mode for cleaner logging
        sell_mode = t_settings.get('sell_mode', 'indicator')

        if custom_amount is not None:
            # Fixed Amount Sizing
            notional = float(custom_amount)
            stop_distance = atr * stop_mult
            
            # Context-aware logging
            log_msg = f"[risk] {ticker}: Allocating ${notional:.2f}"
            if sell_mode in ['sltp', 'hybrid']:
                log_msg += f" (SL Mult: {stop_mult})"
            print(log_msg)
        else:
            # Risk-based position sizing based on initial capital allocated
            stop_distance = atr * stop_mult
            risk_amount_target = capital_basis * risk_pct
            shares = risk_amount_target / stop_distance if stop_distance > 0 else 0
            notional = shares * entry_price
            
            print(f"[risk] Using % risk sizing for {ticker}: Risking ${risk_amount_target:.2f} ({risk_pct*100}%)")
            
            # Cap position size to max portfolio concentration
            max_notional = capital_basis * self.max_position_pct
            if notional > max_notional:
                notional = max_notional
                print(f"[risk] Capping trade size to max concentration: ${notional:.2f}")
        
        # 2. Buying Power Check (CRITICAL FIX)
        if available_cash is not None:
            # Leave a 2% buffer for fees/slippage
            max_allowed = available_cash * 0.98
            if notional > max_allowed:
                notional = max_allowed
                print(f"[risk] Capping trade size to available cash: ${notional:.2f}")

        shares = notional / entry_price
        risk_amount = shares * stop_distance

        # Calculate stop and target prices
        tp_mult = t_settings.get('take_profit_multiplier', getattr(get_user_config(), 'ATR_TAKE_PROFIT_MULTIPLIER', 4.0))
        
        if side == 'long':
            stop_loss = round(entry_price - stop_distance, 2)
            take_profit = round(entry_price + (atr * tp_mult), 2)
        else:
            stop_loss = round(entry_price + stop_distance, 2)
            take_profit = round(entry_price - (atr * tp_mult), 2)

        return {
            'shares': round(shares, 6),
            'notional': round(notional, 2),
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'risk_amount': round(risk_amount, 2),
            'stop_distance': round(stop_distance, 2),
            'risk_reward_ratio': round(tp_mult / stop_mult, 2),
        }

    def calculate_trailing_stop(self, current_price: float, atr: float, current_stop: float, side: str = 'long', ticker: str = None) -> float:
        """
        Calculates the trailing stop. Only moves in the favorable direction.

        For longs: new_stop = max(current_stop, price - ATR * multiplier)
        For shorts: new_stop = min(current_stop, price + ATR * multiplier)
        """
        t_settings = getattr(get_user_config(), 'TICKER_SETTINGS', {}).get(ticker.upper(), {}) if ticker else {}
        trail_mult = t_settings.get('atr_trail_multiplier', getattr(get_user_config(), 'ATR_TRAIL_MULTIPLIER', 3.0))
        trail_distance = atr * trail_mult

        if side == 'long':
            new_stop = current_price - trail_distance
            return round(max(current_stop, new_stop), 2)
        else:
            new_stop = current_price + trail_distance
            return round(min(current_stop, new_stop), 2)

    def should_exit(self, current_price: float, stop_loss: float, take_profit: float, side: str = 'long') -> dict:
        """
        Checks if a position should be exited based on stop-loss or take-profit.
        """
        if side == 'long':
            if current_price <= stop_loss:
                return {'exit': True, 'reason': f'Stop-loss hit at ${stop_loss:.2f}', 'type': 'STOP_LOSS'}
            if current_price >= take_profit:
                return {'exit': True, 'reason': f'Take-profit hit at ${take_profit:.2f}', 'type': 'TAKE_PROFIT'}
        elif side == 'short':
            if current_price >= stop_loss:
                return {'exit': True, 'reason': f'Stop-loss hit at ${stop_loss:.2f}', 'type': 'STOP_LOSS'}
            if current_price <= take_profit:
                return {'exit': True, 'reason': f'Take-profit hit at ${take_profit:.2f}', 'type': 'TAKE_PROFIT'}
        return {'exit': False, 'reason': '', 'type': ''}

    def get_risk_status(self, account_equity: float, ticker: str = None) -> dict:
        """Returns current risk status for the dashboard, factoring in ticker overrides."""
        drawdown_pct = 0.0
        if self.daily_starting_equity and self.daily_starting_equity > 0:
            drawdown_pct = (self.daily_starting_equity - account_equity) / self.daily_starting_equity

        t_settings = getattr(get_user_config(), 'TICKER_SETTINGS', {}).get(ticker.upper(), {}) if ticker else {}
        
        max_dd = t_settings.get('max_daily_drawdown', self.max_daily_drawdown)
        risk_pct = t_settings.get('risk_per_trade', self.risk_per_trade)
        stop_mult = t_settings.get('atr_stop_multiplier', self.atr_stop_multiplier)
        trail_mult = t_settings.get('atr_trail_multiplier', get_user_config().ATR_TRAIL_MULTIPLIER)

        return {
            'trading_halted': self.trading_halted,
            'halt_reason': self.halt_reason,
            'daily_drawdown_pct': round(drawdown_pct * 100, 2),
            'max_drawdown_limit': max_dd * 100,
            'risk_per_trade': risk_pct * 100,
            'atr_stop_multiplier': stop_mult,
            'atr_trail_multiplier': trail_mult,
            'max_position_pct': self.max_position_pct * 100,
        }
