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
        return getattr(get_user_config(), 'RISK_PER_TRADE', None)

    @property
    def max_daily_drawdown(self):
        return getattr(get_user_config(), 'MAX_DAILY_DRAWDOWN', None)

    @property
    def max_position_pct(self):
        return getattr(get_user_config(), 'MAX_POSITION_PCT', None)

    @property
    def global_hard_stop(self):
        return getattr(get_user_config(), 'GLOBAL_HARD_STOP_PCT', None)



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

        t_settings = getattr(get_user_config(), 'TICKER_SETTINGS', {}).get(ticker.upper(), {}) if ticker else {}
        if not t_settings.get('enable_risk_management', True):
            return True

        drawdown = (self.daily_starting_equity - current_equity) / self.daily_starting_equity

        # 1. Check Global Drawdown
        if self.max_daily_drawdown is not None and drawdown >= self.max_daily_drawdown:
            self.trading_halted = True
            self.halt_reason = f"Global daily drawdown {drawdown:.1%} >= {self.max_daily_drawdown:.1%} limit"
            return False

        # 2. Check Global Hard Stop (Loss to Equity)
        if self.global_hard_stop is not None and drawdown >= self.global_hard_stop:
            self.trading_halted = True
            self.halt_reason = f"Global Hard Stop {drawdown:.1%} >= {self.global_hard_stop:.1%} limit"
            return False

        return True

    def calculate_position_size(self, account_equity: float, entry_price: float, atr: float, available_cash: float = None, ticker: str = None, side: str = 'long') -> dict:
        """
        Calculates the optimal position size based on ATR risk or Ticker Overrides.
        Includes a buying power check if available_cash is provided.
        """
        capital_basis = self.daily_starting_equity if (self.daily_starting_equity and self.daily_starting_equity > 0) else account_equity
        if entry_price <= 0 or capital_basis <= 0:
            return {
                'shares': 0,
                'notional': 0.0,
                'stop_loss': 0.0,
                'take_profit': 0.0,
                'risk_amount': 0.0,
                'stop_distance': 0.0,
                'error': 'Invalid inputs for position sizing'
            }
            
        if atr <= 0:
            # Fallback to a tiny artificial ATR to allow Percentage/Fixed Amount sizing to proceed
            atr = entry_price * 0.01

        # 0. Check for Per-Ticker Settings Override
        t_settings = getattr(get_user_config(), 'TICKER_SETTINGS', {}).get(ticker.upper(), {}) if ticker else {}
        risk_amount = 0.0
        
        # Use ticker-specific amount if provided, otherwise check legacy TICKER_AMOUNTS.
        allocation_mode = str(t_settings.get('allocation_mode', 'fixed') or 'fixed').lower()
        if allocation_mode == 'compound':
            custom_amount = t_settings.get('compound_amount')
            if custom_amount is None:
                custom_amount = t_settings.get('amount')
        else:
            custom_amount = t_settings.get('amount')
        if custom_amount is None:
            custom_amount = getattr(get_user_config(), 'TICKER_AMOUNTS', {}).get(ticker.upper())

        # Determine Risk Parameters (Ticker-specific or Fallback to Instance Globals)
        enable_risk = t_settings.get('enable_risk_management', True)
        risk_pct = t_settings.get('risk_per_trade', self.risk_per_trade)
        stop_mult = t_settings.get('atr_stop_multiplier', 2.0)

        # Determine Sell Mode for cleaner logging
        sell_mode = t_settings.get('sell_mode', 'indicator')

        # Determine Stop Distance based on Exit Strategy (sell_mode)
        if sell_mode in ['percentage', 'hybrid_pct']:
            sl_pct = float(t_settings.get('regular_stop_loss_pct', 0.05))
            stop_distance = entry_price * sl_pct
        else:
            # Default to ATR for sltp, hybrid, or indicator modes
            stop_distance = atr * stop_mult

        if custom_amount is not None:
            # Fixed Amount Sizing
            notional = float(custom_amount)
            
            # Context-aware logging
            mode_label = "compound" if allocation_mode == 'compound' else "fixed"
            log_msg = f"[risk] {ticker}: Allocating ${notional:.2f} ({mode_label})"
            if sell_mode in ['sltp', 'hybrid']:
                log_msg += f" (SL Mult: {stop_mult})"
            elif sell_mode in ['percentage', 'hybrid_pct']:
                log_msg += f" (SL PCT: {sl_pct*100:.1f}%)"
            print(log_msg)
        elif not enable_risk:
            # Risk Management Disabled: Allocate entire capital basis without % risk logic
            notional = float(capital_basis)
            print(f"[risk] Risk Management Disabled for {ticker}. Allocating full basis: ${notional:.2f}")
        else:
            # Risk-based position sizing based on initial capital allocated
            risk_amount_target = capital_basis * risk_pct
            shares = risk_amount_target / stop_distance if stop_distance > 0 else 0
            notional = shares * entry_price
            
            print(f"[risk] Using % risk sizing for {ticker}: Risking ${risk_amount_target:.2f} ({risk_pct*100}%)")
            
            # Cap position size to max portfolio concentration
            bot_max_pos = t_settings.get('max_position_pct', self.max_position_pct)
            if bot_max_pos is not None:
                max_notional = capital_basis * bot_max_pos
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

        # Calculate stop and target prices ONLY for AI Autopilot fallback usage
        precision = 6 if entry_price < 1.0 else 2
        stop_loss = None
        take_profit = None
        
        if t_settings.get('ai_autopilot', False):
            if sell_mode in ['percentage', 'hybrid_pct']:
                tp_pct = float(t_settings.get('regular_take_profit_pct', 0.10))
                if side == 'long':
                    stop_loss = round(entry_price * (1 - sl_pct), precision)
                    take_profit = round(entry_price * (1 + tp_pct), precision)
                else:
                    stop_loss = round(entry_price * (1 + sl_pct), precision)
                    take_profit = round(entry_price * (1 - tp_pct), precision)
            else:
                tp_mult = t_settings.get('take_profit_multiplier', 4.0)
                if side == 'long':
                    stop_loss = round(entry_price - stop_distance, precision)
                    take_profit = round(entry_price + (atr * tp_mult), precision)
                else:
                    stop_loss = round(entry_price + stop_distance, precision)
                    take_profit = round(entry_price - (atr * tp_mult), precision)

        return {
            'shares': round(shares, 6),
            'notional': round(notional, 2),
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'risk_amount': round(risk_amount, 2),
            'stop_distance': round(stop_distance, 2),
        }



    def get_risk_status(self, account_equity: float, ticker: str = None) -> dict:
        """Returns current risk status for the dashboard, factoring in ticker overrides."""
        drawdown_pct = 0.0
        if self.daily_starting_equity and self.daily_starting_equity > 0:
            drawdown_pct = (self.daily_starting_equity - account_equity) / self.daily_starting_equity

        t_settings = getattr(get_user_config(), 'TICKER_SETTINGS', {}).get(ticker.upper(), {}) if ticker else {}
        
        max_dd = t_settings.get('max_daily_drawdown', self.max_daily_drawdown)
        risk_pct = t_settings.get('risk_per_trade', self.risk_per_trade)
        max_pos = t_settings.get('max_position_pct', self.max_position_pct)

        return {
            'trading_halted': self.trading_halted,
            'halt_reason': self.halt_reason,
            'daily_drawdown_pct': round(drawdown_pct * 100, 2),
            'max_drawdown_limit': (max_dd * 100) if max_dd is not None else 'Off',
            'risk_per_trade': (risk_pct * 100) if risk_pct is not None else 'Off',
            'max_position_pct': (max_pos * 100) if max_pos is not None else 'Off',
        }
