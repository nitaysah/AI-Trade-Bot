"""
Risk Management Module.

Implements production-grade risk controls:
- ATR-based dynamic stop-loss calculation
- Position sizing based on % risk per trade
- Trailing stop management
- Daily drawdown circuit breaker
- Maximum position concentration limits
"""

from datetime import datetime

import config as global_config
import pytz
from user_config import active_evaluation_context, get_user_config


class RiskManager:
    """Manages all risk calculations and enforces trading rules."""

    def __init__(self):
        pass

    def _state(self):
        config = get_user_config()
        state = getattr(config, "global_risk_state", None)
        if not isinstance(state, dict):
            state = {}
            config.global_risk_state = state
        return state

    def _now(self):
        config = get_user_config()
        try:
            return datetime.now(pytz.timezone(getattr(config, "TIMEZONE", "US/Central")))
        except Exception:
            return datetime.now(pytz.UTC)

    @property
    def daily_starting_equity(self):
        return self._state().get("daily_starting_equity")

    @daily_starting_equity.setter
    def daily_starting_equity(self, value):
        self._state()["daily_starting_equity"] = value

    @property
    def trading_halted(self):
        return bool(self._state().get("halted", False))

    @trading_halted.setter
    def trading_halted(self, value):
        self._state()["halted"] = bool(value)

    @property
    def halt_reason(self):
        return str(self._state().get("halt_reason", "") or "")

    @halt_reason.setter
    def halt_reason(self, value):
        self._state()["halt_reason"] = str(value or "")

    # Dynamic fallback properties matching user configs
    @property
    def risk_per_trade(self):
        return getattr(get_user_config(), 'RISK_PER_TRADE', None)

    @property
    def max_daily_drawdown(self):
        config = get_user_config()
        bot_parameters = getattr(config, "contexts", {}).get("bots", {}).get("parameters", {})
        if "MAX_DAILY_DRAWDOWN" in bot_parameters:
            return bot_parameters["MAX_DAILY_DRAWDOWN"]
        return getattr(config, 'MAX_DAILY_DRAWDOWN', None)

    @property
    def max_position_pct(self):
        return getattr(get_user_config(), 'MAX_POSITION_PCT', None)

    @property
    def global_hard_stop(self):
        config = get_user_config()
        bot_parameters = getattr(config, "contexts", {}).get("bots", {}).get("parameters", {})
        if "GLOBAL_HARD_STOP_PCT" in bot_parameters:
            return bot_parameters["GLOBAL_HARD_STOP_PCT"]
        return getattr(config, 'GLOBAL_HARD_STOP_PCT', None)



    def _positive_limit(self, value):
        try:
            parsed = float(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None

    def refresh_equity_state(
        self,
        equity: float,
        account_key: str = None,
        daily_reference_equity: float = None,
    ):
        """Update daily and high-water baselines without clearing a latched halt."""
        equity = float(equity or 0.0)
        if equity <= 0:
            return self._state()
        state = self._state()
        now = self._now()
        today = now.strftime("%Y-%m-%d")
        normalized_account_key = str(account_key or "").strip()
        saved_account_key = str(state.get("account_key", "") or "").strip()
        if normalized_account_key and saved_account_key and normalized_account_key != saved_account_key:
            # Global risk belongs to a brokerage account, not merely the app
            # user. A paper/live or account switch must never inherit another
            # account's high-water mark or latched halt.
            state.clear()
            state.update({
                "halted": False,
                "halt_reason": "",
                "halt_code": "",
                "account_changed_at": now.isoformat(),
            })
        if normalized_account_key:
            # Migration from older state deliberately preserves any existing
            # halt and baselines while binding them to the current account.
            state["account_key"] = normalized_account_key
        try:
            daily_reference = float(daily_reference_equity or 0.0)
        except (TypeError, ValueError):
            daily_reference = 0.0
        is_new_risk_day = state.get("daily_baseline_date") != today
        if is_new_risk_day or float(state.get("daily_starting_equity", 0.0) or 0.0) <= 0:
            state["daily_baseline_date"] = today
            state["daily_starting_equity"] = daily_reference if daily_reference > 0 else equity
            state["manual_daily_baseline"] = False
        elif daily_reference > 0 and not state.get("manual_daily_baseline", False):
            # Broker prior equity captures losses incurred before this process
            # started. It is more accurate than using the first observed
            # balance after a midday restart.
            state["daily_starting_equity"] = daily_reference
        high_water = float(state.get("equity_high_watermark", 0.0) or 0.0)
        if high_water <= 0 or equity > high_water:
            state["equity_high_watermark"] = equity
        state["current_equity"] = equity
        state["last_evaluated_at"] = now.isoformat()
        return state

    def set_daily_equity(self, equity: float):
        """Compatibility wrapper; baseline refresh never reactivates a halt."""
        state = self.refresh_equity_state(equity)
        state["daily_starting_equity"] = float(equity or 0.0)
        state["daily_baseline_date"] = self._now().strftime("%Y-%m-%d")

    def reactivate(self, current_equity: float, account_key: str = None):
        """Manually clear a global halt and establish new risk baselines."""
        equity = float(current_equity or 0.0)
        now = self._now()
        state = self._state()
        state.clear()
        state.update({
            "halted": False,
            "halt_reason": "",
            "halt_code": "",
            "reactivated_at": now.isoformat(),
            "daily_baseline_date": now.strftime("%Y-%m-%d"),
            "daily_starting_equity": equity,
            "equity_high_watermark": equity,
            "current_equity": equity,
            "daily_drawdown": 0.0,
            "equity_drawdown": 0.0,
            "manual_daily_baseline": True,
        })
        if account_key:
            state["account_key"] = str(account_key).strip()
        return self.get_global_status(equity, account_key=account_key)

    def _trip_halt(self, code: str, reason: str, current_equity: float, drawdown: float, limit: float):
        state = self._state()
        now = self._now()
        state.update({
            "halted": True,
            "halt_code": code,
            "halt_reason": reason,
            "halted_at": now.isoformat(),
            "halt_event_id": f"{code}:{now.isoformat()}",
            "breach_drawdown": drawdown,
            "breach_limit": limit,
            "breach_equity": float(current_equity or 0.0),
            "requires_manual_reactivation": True,
        })

    def check_drawdown(
        self,
        current_equity: float,
        ticker: str = None,
        account_key: str = None,
        daily_reference_equity: float = None,
    ) -> bool:
        """
        Checks if daily drawdown limit has been breached.
        Returns True if trading should continue, False if halted.
        """
        state = self.refresh_equity_state(
            current_equity,
            account_key=account_key,
            daily_reference_equity=daily_reference_equity,
        )
        if self.trading_halted:
            return False
        if self.daily_starting_equity is None or self.daily_starting_equity <= 0:
            return True

        current_equity = float(current_equity or 0.0)
        daily_drawdown = max(0.0, (self.daily_starting_equity - current_equity) / self.daily_starting_equity)
        high_water = float(state.get("equity_high_watermark", current_equity) or current_equity)
        equity_drawdown = max(0.0, (high_water - current_equity) / high_water) if high_water > 0 else 0.0
        state["daily_drawdown"] = daily_drawdown
        state["equity_drawdown"] = equity_drawdown

        daily_limit = self._positive_limit(self.max_daily_drawdown)
        hard_limit = self._positive_limit(self.global_hard_stop)

        # The high-water equity hard stop is intentionally checked first. It
        # is a separate multi-day account-loss guard, not a duplicate of the
        # daily drawdown limit.
        if hard_limit is not None and equity_drawdown >= hard_limit:
            reason = f"Max loss to equity reached: {equity_drawdown:.2%} from high-water mark (limit {hard_limit:.2%})."
            self._trip_halt("equity_hard_stop", reason, current_equity, equity_drawdown, hard_limit)
            return False

        if daily_limit is not None and daily_drawdown >= daily_limit:
            reason = f"Max daily drawdown reached: {daily_drawdown:.2%} (limit {daily_limit:.2%})."
            self._trip_halt("daily_drawdown", reason, current_equity, daily_drawdown, daily_limit)
            return False

        return True

    def calculate_position_size(self, account_equity: float, entry_price: float, atr: float, available_cash: float = None, ticker: str = None, side: str = 'long') -> dict:
        """
        Calculates the optimal position size based on ATR risk or Ticker Overrides.
        Includes a buying power check if available_cash is provided.
        """
        live_bot_context = active_evaluation_context.get() == "bots"
        capital_basis = (
            self.daily_starting_equity
            if live_bot_context and self.daily_starting_equity and self.daily_starting_equity > 0
            else account_equity
        )
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

        # Accumulate strategy exit parameters for logging inside parenthesis
        strategy_parts = []
        if sell_mode in ['sltp', 'hybrid', 'atr', 'hybrid_atr']:
            strategy_parts.append(f"ATR: ${atr:.2f}")
            sl_price = entry_price - stop_distance if side == 'long' else entry_price + stop_distance
            strategy_parts.append(f"SL: ${sl_price:.2f} ({stop_mult}x ATR)")
            tp_mult = float(t_settings.get('take_profit_multiplier', 4.0))
            tp_price = entry_price + (atr * tp_mult) if side == 'long' else entry_price - (atr * tp_mult)
            strategy_parts.append(f"TP: ${tp_price:.2f} ({tp_mult}x ATR)")
            enable_atr_trail = bool(t_settings.get('enable_atr_trail', False))
            if enable_atr_trail:
                trail_mult_val = float(t_settings.get('atr_trail_multiplier', stop_mult))
                trail_price = entry_price - (atr * trail_mult_val) if side == 'long' else entry_price + (atr * trail_mult_val)
                strategy_parts.append(f"Trailing Stop: Enabled (${trail_price:.2f} / {trail_mult_val}x ATR)")
            else:
                strategy_parts.append("Trailing Stop: Disabled")
        elif sell_mode in ['percentage', 'hybrid_pct']:
            strategy_parts.append(f"ATR: ${atr:.2f}")
            sl_price = entry_price * (1 - sl_pct) if side == 'long' else entry_price * (1 + sl_pct)
            strategy_parts.append(f"SL: ${sl_price:.2f} ({sl_pct*100:.1f}%)")
            tp_pct = float(t_settings.get('regular_take_profit_pct', 0.10))
            tp_price = entry_price * (1 + tp_pct) if side == 'long' else entry_price * (1 - tp_pct)
            strategy_parts.append(f"TP: ${tp_price:.2f} ({tp_pct*100:.1f}%)")
            enable_pct_trail = bool(t_settings.get('enable_pct_trail', False))
            if enable_pct_trail:
                trail_pct_val = float(t_settings.get('regular_trailing_pct', 0.03))
                trail_price = entry_price * (1 - trail_pct_val) if side == 'long' else entry_price * (1 + trail_pct_val)
                strategy_parts.append(f"Trailing Stop: Enabled (${trail_price:.2f} / {trail_pct_val*100:.1f}%)")
            else:
                strategy_parts.append("Trailing Stop: Disabled")
        elif sell_mode in ['pivot_points', 'hybrid_pivot']:
            strategy_parts.append("Pivot Exit")
        elif sell_mode in ['high_low', 'hybrid_high_low']:
            strategy_parts.append("H/L Exit")
        elif sell_mode in ['swing_points', 'hybrid_swing']:
            strategy_parts.append("Swing Exit")
        elif sell_mode == 'custom_sltp':
            strategy_parts.append("Custom SL/TP")

        strategy_desc = f" ({', '.join(strategy_parts)})" if strategy_parts else ""

        if custom_amount is not None:
            # Fixed Amount Sizing
            notional = float(custom_amount)
            shares = notional / entry_price
            risk_amount = shares * stop_distance
            
            # Context-aware logging
            mode_label = "compound" if allocation_mode == 'compound' else "fixed"
            log_msg = f"[risk] {ticker}: Allocating ${notional:.2f} ({mode_label}){strategy_desc}"
            log_msg += f" | Entry Price: ${entry_price:.2f} | Shares: {shares:.6f} | Stop Distance: ${stop_distance:.2f} | Max Dollar Risk: ${risk_amount:.2f}"
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
            
            log_msg = f"[risk] {ticker}: Allocating ${notional:.2f} (Risk Sizing | Risking ${risk_amount_target:.2f} / {risk_pct*100:.1f}%){strategy_desc}"
            log_msg += f" | Entry Price: ${entry_price:.2f} | Shares: {shares:.6f} | Stop Distance: ${stop_distance:.2f} | Max Dollar Risk: ${risk_amount_target:.2f}"
            print(log_msg)
            
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



    def get_global_status(
        self,
        account_equity: float = None,
        account_key: str = None,
        daily_reference_equity: float = None,
    ) -> dict:
        if account_equity is not None and float(account_equity or 0.0) > 0:
            self.refresh_equity_state(
                account_equity,
                account_key=account_key,
                daily_reference_equity=daily_reference_equity,
            )
        state = self._state()
        daily_start = float(state.get("daily_starting_equity", 0.0) or 0.0)
        high_water = float(state.get("equity_high_watermark", 0.0) or 0.0)
        current = float(state.get("current_equity", account_equity or 0.0) or 0.0)
        daily_drawdown = max(0.0, (daily_start - current) / daily_start) if daily_start > 0 else 0.0
        equity_drawdown = max(0.0, (high_water - current) / high_water) if high_water > 0 else 0.0
        daily_limit = self._positive_limit(self.max_daily_drawdown)
        hard_limit = self._positive_limit(self.global_hard_stop)
        return {
            "halted": self.trading_halted,
            "halt_reason": self.halt_reason,
            "halt_code": state.get("halt_code", ""),
            "halted_at": state.get("halted_at"),
            "halt_event_id": state.get("halt_event_id"),
            "requires_manual_reactivation": bool(state.get("requires_manual_reactivation", False)),
            "daily_baseline_date": state.get("daily_baseline_date"),
            "daily_starting_equity": round(daily_start, 2),
            "equity_high_watermark": round(high_water, 2),
            "current_equity": round(current, 2),
            "daily_drawdown_pct": round(daily_drawdown * 100, 2),
            "equity_drawdown_pct": round(equity_drawdown * 100, 2),
            "max_daily_drawdown_pct": round(daily_limit * 100, 2) if daily_limit is not None else None,
            "max_loss_to_equity_pct": round(hard_limit * 100, 2) if hard_limit is not None else None,
        }

    def get_risk_status(self, account_equity: float, ticker: str = None) -> dict:
        """Return global halt state plus ticker-specific sizing controls."""
        # Reporting must be read-only. In particular, a dashboard or
        # historical backtest equity value must never move the live account's
        # high-water mark or daily baseline.
        global_status = self.get_global_status()

        t_settings = getattr(get_user_config(), 'TICKER_SETTINGS', {}).get(ticker.upper(), {}) if ticker else {}
        
        max_dd = self.max_daily_drawdown
        risk_pct = t_settings.get('risk_per_trade', self.risk_per_trade)
        max_pos = t_settings.get('max_position_pct', self.max_position_pct)

        return {
            'trading_halted': self.trading_halted,
            'halt_reason': self.halt_reason,
            'daily_drawdown_pct': global_status['daily_drawdown_pct'],
            'equity_drawdown_pct': global_status['equity_drawdown_pct'],
            'max_drawdown_limit': (max_dd * 100) if max_dd is not None else 'Off',
            'risk_per_trade': (risk_pct * 100) if risk_pct is not None else 'Off',
            'max_position_pct': (max_pos * 100) if max_pos is not None else 'Off',
        }
