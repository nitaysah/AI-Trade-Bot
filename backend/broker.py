"""
Alpaca Broker Integration.

Handles all communication with the Alpaca API:
- Account info (equity, buying power, P/L)
- Placing fractional market orders (notional-based)
- Getting open positions
- Order history
- Paper trading support
"""

import config

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    print("[broker] alpaca-py not installed. Running in simulation mode.")


class AlpacaBroker:
    """Wrapper around Alpaca's Trading API."""

    def __init__(self):
        self.simulation_mode = True  # Start in simulation, only go live on successful connection
        self.client = None

        # Simulation state
        self.sim_equity = 100.0
        self.sim_cash = 100.0
        self.sim_positions = {}
        self.sim_orders = []

        if config.ALPACA_API_KEY and config.ALPACA_API_KEY != "your_alpaca_api_key_here":
            self.connect(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, config.ALPACA_PAPER)

    def connect(self, api_key: str, secret_key: str, paper: bool = True) -> bool:
        """Attempts to connect to Alpaca with provided keys."""
        if not ALPACA_AVAILABLE:
            print("[broker] alpaca-py not installed. Cannot connect.")
            return False

        try:
            # Clean keys to prevent invisible whitespace errors
            api_key = api_key.strip()
            secret_key = secret_key.strip()
            
            print(f"[broker] Attempting connection (Paper={paper})...")
            self.client = TradingClient(api_key, secret_key, paper=paper)
            # Test connection
            account = self.client.get_account()
            self.simulation_mode = False
            print(f"[broker] SUCCESS: Connected to Alpaca ({account.account_number}) - Equity: ${account.equity}")
            return True
        except Exception as e:
            print(f"[broker] CONNECTION FAILED: {str(e)}")
            self.simulation_mode = True
            self.client = None
            return False

    def get_account_info(self) -> dict:
        """Returns current account details."""
        if self.simulation_mode:
            return {
                'equity': self.sim_equity,
                'cash': self.sim_cash,
                'buying_power': self.sim_cash,
                'daily_pl': round(self.sim_equity - 100.0, 2),
                'daily_pl_pct': round(((self.sim_equity / 100.0) - 1) * 100, 2),
                'simulation': True
            }

        try:
            account = self.client.get_account()
            equity = float(account.equity)
            last_equity = float(account.last_equity)
            daily_pl = equity - last_equity
            daily_pl_pct = (daily_pl / last_equity * 100) if last_equity > 0 else 0

            # Use non_marginable_buying_power, but fallback to cash if it's 0 (common in paper trading/settlement delays)
            non_marginable = float(getattr(account, 'non_marginable_buying_power', account.cash))
            if non_marginable <= 0:
                non_marginable = float(account.cash)

            return {
                'equity': round(equity, 2),
                'cash': round(float(account.cash), 2),
                'buying_power': round(float(account.buying_power), 2),
                'non_marginable_buying_power': max(0.0, round(non_marginable, 2)),
                'daily_pl': round(daily_pl, 2),
                'daily_pl_pct': round(daily_pl_pct, 2),
                'simulation': False
            }
        except Exception as e:
            print(f"[broker] Error getting account info: {e}")
            return {
                'equity': 0, 'cash': 0, 'buying_power': 0,
                'daily_pl': 0, 'daily_pl_pct': 0, 'simulation': True,
                'error': str(e)
            }

    def get_positions(self) -> list:
        """Returns list of open positions."""
        if self.simulation_mode:
            positions = []
            for symbol, pos in self.sim_positions.items():
                current_val = pos['qty'] * pos.get('current_price', pos['avg_price'])
                cost_basis = pos['qty'] * pos['avg_price']
                pl = current_val - cost_basis
                positions.append({
                    'symbol': symbol,
                    'qty': round(pos['qty'], 6),
                    'avg_price': round(pos['avg_price'], 2),
                    'current_price': round(pos.get('current_price', pos['avg_price']), 2),
                    'market_value': round(current_val, 2),
                    'unrealized_pl': round(pl, 2),
                    'unrealized_pl_pct': round((pl / cost_basis) * 100, 2) if cost_basis > 0 else 0,
                    'stop_loss': pos.get('stop_loss', 0),
                    'take_profit': pos.get('take_profit', 0),
                })
            return positions

        try:
            raw_positions = self.client.get_all_positions()
            positions = []
            for p in raw_positions:
                positions.append({
                    'symbol': p.symbol,
                    'qty': float(p.qty),
                    'avg_price': round(float(p.avg_entry_price), 2),
                    'current_price': round(float(p.current_price), 2),
                    'market_value': round(float(p.market_value), 2),
                    'unrealized_pl': round(float(p.unrealized_pl), 2),
                    'unrealized_pl_pct': round(float(p.unrealized_plpc) * 100, 2),
                    'stop_loss': 0,
                    'take_profit': 0,
                })
            print(f"[broker] Fetched {len(positions)} active positions from Alpaca")
            return positions
        except Exception as e:
            print(f"[broker] Error getting positions: {e}")
            return []

    def get_open_orders(self) -> list:
        """Returns list of currently open (pending) orders."""
        if self.simulation_mode:
            return [] # Sim mode fills immediately

        try:
            # Get only open orders
            orders = self.client.get_orders(filter=None) # Default filter is open orders
            open_list = []
            for o in orders:
                open_list.append({
                    'id': str(o.id),
                    'symbol': o.symbol.replace("/", "").upper(),
                    'side': str(o.side),
                    'status': str(o.status),
                    'qty': float(o.qty) if o.qty else 0,
                    'notional': float(o.notional) if o.notional else 0,
                })
            return open_list
        except Exception as e:
            print(f"[broker] Error getting open orders: {e}")
            return []

    def place_order(self, symbol: str, notional: float, side: str = 'buy',
                    stop_loss: float = 0, take_profit: float = 0) -> dict:
        """
        Places a notional (dollar-based) market order for fractional shares.
        """
        if self.simulation_mode:
            return self._sim_order(symbol, notional, side, stop_loss, take_profit)

        try:
            order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
            
            # Crypto logic: Must use GTC and slash-format symbol
            request_symbol = symbol.upper().replace("/", "")
            is_crypto = any(request_symbol.endswith(base) for base in ["USD", "USDT", "USDC"])
            
            tif = TimeInForce.GTC if is_crypto else TimeInForce.DAY
            if is_crypto:
                # Format as BASE/QUOTE (e.g., BTC/USD)
                for base in ["USDT", "USDC", "USD"]:
                    if request_symbol.endswith(base):
                        request_symbol = request_symbol.replace(base, f"/{base}")
                        break
            
            order_data = MarketOrderRequest(
                symbol=request_symbol,
                notional=round(notional, 2),
                side=order_side,
                time_in_force=tif
            )
            order = self.client.submit_order(order_data=order_data)
            
            # Estimate fee (Crypto is ~0.1%, Stocks are $0 in Alpaca usually)
            fee = round(notional * 0.001, 2) if is_crypto else 0.00
            
            return {
                'success': True,
                'order_id': str(order.id),
                'symbol': symbol,
                'side': side.upper(),
                'qty': order.qty if order.qty else 0, # Note: Notional orders might have None qty until filled
                'notional': round(notional, 2),
                'total_cost': round(notional + fee, 2),
                'fees': fee,
                'status': str(order.status),
            }
        except Exception as e:
            print(f"[broker] Order error: {e}")
            return {'success': False, 'error': str(e)}

    def close_position(self, symbol: str) -> dict:
        """Closes an entire position for a given symbol."""
        if self.simulation_mode:
            if symbol in self.sim_positions:
                pos = self.sim_positions.pop(symbol)
                proceeds = pos['qty'] * pos.get('current_price', pos['avg_price'])
                self.sim_cash += proceeds
                self.sim_equity = self.sim_cash + sum(
                    p['qty'] * p.get('current_price', p['avg_price'])
                    for p in self.sim_positions.values()
                )
                return {'success': True, 'symbol': symbol, 'proceeds': round(proceeds, 2)}
            return {'success': False, 'error': f'No position in {symbol}'}

        try:
            # 1. Format crypto symbol if needed (e.g. BTCUSD -> BTC/USD)
            request_symbol = symbol.upper().replace("/", "")
            is_crypto = any(request_symbol.endswith(base) for base in ["USD", "USDT", "USDC"])
            
            formats_to_try = [request_symbol] # Try no-slash first
            if is_crypto:
                for base in ["USDT", "USDC", "USD"]:
                    if request_symbol.endswith(base):
                        formats_to_try.append(request_symbol.replace(base, f"/{base}")) # Add slashed version
                        break
            
            # 2. Attempt to close using available formats
            last_err = "Unknown"
            for sym in formats_to_try:
                try:
                    self.client.close_position(sym)
                    print(f"[broker] Successfully closed position for {sym}")
                    return {'success': True, 'symbol': sym}
                except Exception as e:
                    last_err = str(e)
                    if "not found" in last_err.lower():
                        continue # Try the next format
                    else:
                        break # Critical error, stop
            
            return {'success': False, 'error': last_err}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def close_all_positions(self) -> dict:
        """Liquidates all positions immediately."""
        if self.simulation_mode:
            count = len(self.sim_positions)
            self.sim_positions = {}
            # Reset equity to cash if all positions closed
            self.sim_equity = self.sim_cash 
            return {'success': True, 'count': count}

        try:
            self.client.close_all_positions(cancel_orders=True)
            return {'success': True}
        except Exception as e:
            print(f"[broker] Error closing all positions: {e}")
            return {'success': False, 'error': str(e)}

    def _sim_order(self, symbol: str, notional: float, side: str,
                   stop_loss: float, take_profit: float) -> dict:
        """Simulates an order in local state with detailed receipts."""
        if side.lower() == 'buy':
            if notional > self.sim_cash:
                return {'success': False, 'error': 'Insufficient buying power'}

            # Detailed receipt data
            fee = round(notional * 0.001, 2)
            total_spent = notional + fee
            
            # Use $100 as a placeholder price for qty calculation
            price = 100.0 
            qty = round(notional / price, 6)

            if symbol in self.sim_positions:
                existing = self.sim_positions[symbol]
                total_pos_cost = (existing['qty'] * existing['avg_price']) + notional
                existing['qty'] += qty
                existing['avg_price'] = total_pos_cost / existing['qty']
            else:
                self.sim_positions[symbol] = {
                    'qty': qty,
                    'avg_price': price,
                    'current_price': price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                }

            self.sim_cash -= total_spent
            self.sim_equity = self.sim_cash + sum(
                p['qty'] * p.get('current_price', p['avg_price'])
                for p in self.sim_positions.values()
            )

            return {
                'success': True,
                'order_id': f'SIM-{len(self.sim_positions)}',
                'symbol': symbol,
                'side': 'BUY',
                'qty': qty,
                'notional': round(notional, 2),
                'total_cost': round(total_spent, 2),
                'fees': fee,
                'status': 'filled',
            }

        elif side.lower() == 'sell':
            return self.close_position(symbol)

        return {'success': False, 'error': 'Invalid side'}
