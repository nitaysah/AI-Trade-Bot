"""
Webull Broker Integration.

Drop-in replacement for AlpacaBroker with identical method signatures.
Uses the official webull-openapi-python-sdk for:
- Account info (equity, buying power, positions)
- Placing market/limit orders (qty-based, converted from notional)
- Getting open positions and order history
- Test (paper) vs Production endpoint switching

Install: pip install webull-openapi-python-sdk
"""

import uuid
import time
import config

try:
    from webull.core.client import ApiClient
    from webull.trade.trade_client import TradeClient
    from webull.market.market_client import MarketClient
    WEBULL_AVAILABLE = True
except ImportError:
    WEBULL_AVAILABLE = False
    print("[webull] webull-openapi-python-sdk not installed. Running in simulation mode.")


# ──────────────────────────────────────────────
# Webull API Endpoints
# ──────────────────────────────────────────────
ENDPOINTS = {
    "trade": {
        True:  "us-openapi-alb.uat.webullbroker.com",   # Test/Paper
        False: "api.webull.com",                          # Production
    },
    "market_data": {
        True:  "us-openapi-alb.uat.webullbroker.com",
        False: "data-api.webull.com",
    },
    "events": {
        True:  "us-openapi-events.uat.webullbroker.com",
        False: "events-api.webull.com",
    },
}


class WebullBroker:
    """Wrapper around Webull's Trading API with AlpacaBroker-compatible interface."""

    def __init__(self):
        self.simulation_mode = True
        self.api_client = None
        self.trade_client = None
        self.market_client = None
        self.account_id = None

        # Simulation state (mirrors AlpacaBroker)
        self.sim_equity = 100.0
        self.sim_cash = 100.0
        self.sim_positions = {}
        self.sim_orders = []

        # Auto-connect if keys are configured
        app_key = getattr(config, 'WEBULL_APP_KEY', '')
        app_secret = getattr(config, 'WEBULL_APP_SECRET', '')
        if app_key and app_key != "your_webull_app_key_here":
            test_mode = getattr(config, 'WEBULL_TEST_MODE', True)
            self.connect(app_key, app_secret, test_mode)

    def connect(self, app_key: str, app_secret: str, test_mode: bool = True) -> bool:
        """Connects to Webull using App Key + App Secret (HMAC-SHA1 signed)."""
        if not WEBULL_AVAILABLE:
            print("[webull] SDK not installed. Cannot connect.")
            return False

        try:
            app_key = app_key.strip()
            app_secret = app_secret.strip()

            print(f"[webull] Attempting connection (Test={test_mode})...")

            # Initialize the official SDK client
            self.api_client = ApiClient(app_key, app_secret, "us")
            self.api_client.add_endpoint("us", ENDPOINTS["trade"][test_mode])

            # Initialize trade + market clients
            self.trade_client = TradeClient(self.api_client)

            try:
                market_api = ApiClient(app_key, app_secret, "us")
                market_api.add_endpoint("us", ENDPOINTS["market_data"][test_mode])
                self.market_client = MarketClient(market_api)
            except Exception as e:
                print(f"[webull] Market client init warning: {e}")
                self.market_client = None

            # Retrieve account ID (required for all trading calls)
            account_id = getattr(config, 'WEBULL_ACCOUNT_ID', '')
            if account_id:
                self.account_id = account_id.strip()
            else:
                # Auto-discover from API
                res = self._retry_request(self.trade_client.account_v2.get_account_list)
                if res.status_code == 200:
                    accounts = res.json()
                    if accounts:
                        self.account_id = accounts[0].get('account_id', '')
                        print(f"[webull] Auto-discovered account: {self.account_id}")

            if not self.account_id:
                print("[webull] WARNING: No account_id found. Set WEBULL_ACCOUNT_ID in .env")
                return False

            # Verify with a balance check
            res = self._retry_request(
                self.trade_client.account_v2.get_account_balance,
                self.account_id
            )
            if res.status_code == 200:
                self.simulation_mode = False
                bal = res.json()
                equity = bal.get('total_market_value', bal.get('net_liquidation', 0))
                print(f"[webull] SUCCESS: Connected (Account: {self.account_id}) - Equity: ${equity}")
                return True
            else:
                print(f"[webull] Balance check failed: {res.status_code} {res.text}")
                return False

        except Exception as e:
            print(f"[webull] CONNECTION FAILED: {str(e)}")
            self.simulation_mode = True
            self.api_client = None
            self.trade_client = None
            return False

    # ──────────────────────────────────────────────
    # Rate-Limit Retry Wrapper
    # ──────────────────────────────────────────────
    def _retry_request(self, func, *args, max_retries=3, **kwargs):
        """Wraps SDK calls with exponential backoff for rate limits."""
        for attempt in range(max_retries):
            try:
                res = func(*args, **kwargs)
                if hasattr(res, 'status_code') and res.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[webull] Rate limited. Retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                return res
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise e
        return res

    # ──────────────────────────────────────────────
    # Unified Interface Methods
    # ──────────────────────────────────────────────

    def get_account_info(self) -> dict:
        """Returns current account details (AlpacaBroker-compatible shape)."""
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
            res = self._retry_request(
                self.trade_client.account_v2.get_account_balance,
                self.account_id
            )
            if res.status_code != 200:
                raise Exception(f"Balance API error: {res.status_code}")

            bal = res.json()
            equity = float(bal.get('net_liquidation', 0))
            cash = float(bal.get('total_cash', 0))
            buying_power = float(bal.get('buying_power', cash))
            # Webull may not provide daily P/L directly — compute if available
            last_equity = float(bal.get('previous_close_equity', equity))
            daily_pl = equity - last_equity
            daily_pl_pct = (daily_pl / last_equity * 100) if last_equity > 0 else 0

            return {
                'equity': round(equity, 2),
                'cash': round(cash, 2),
                'buying_power': round(buying_power, 2),
                'non_marginable_buying_power': round(cash, 2),
                'daily_pl': round(daily_pl, 2),
                'daily_pl_pct': round(daily_pl_pct, 2),
                'simulation': False
            }
        except Exception as e:
            print(f"[webull] Error getting account info: {e}")
            return {
                'equity': 0, 'cash': 0, 'buying_power': 0,
                'daily_pl': 0, 'daily_pl_pct': 0, 'simulation': True,
                'error': str(e)
            }

    def get_positions(self) -> list:
        """Returns list of open positions."""
        if self.simulation_mode:
            return self._sim_positions_list()

        try:
            res = self._retry_request(
                self.trade_client.account_v2.get_account_position,
                self.account_id
            )
            if res.status_code != 200:
                return []

            positions = []
            for p in res.json():
                qty = float(p.get('quantity', 0))
                avg_price = float(p.get('cost_price', 0))
                current_price = float(p.get('last_price', avg_price))
                market_value = qty * current_price
                cost_basis = qty * avg_price
                pl = market_value - cost_basis

                positions.append({
                    'symbol': p.get('symbol', '').replace('/', '').upper(),
                    'qty': qty,
                    'avg_price': round(avg_price, 2),
                    'current_price': round(current_price, 2),
                    'market_value': round(market_value, 2),
                    'unrealized_pl': round(pl, 2),
                    'unrealized_pl_pct': round((pl / cost_basis) * 100, 2) if cost_basis > 0 else 0,
                    'stop_loss': 0,
                    'take_profit': 0,
                })
            print(f"[webull] Fetched {len(positions)} active positions")
            return positions
        except Exception as e:
            print(f"[webull] Error getting positions: {e}")
            return []

    def get_open_orders(self) -> list:
        """Returns list of currently open (pending) orders."""
        if self.simulation_mode:
            return []

        try:
            res = self._retry_request(
                self.trade_client.order_v2.get_open_orders,
                self.account_id
            )
            if res.status_code != 200:
                return []

            open_list = []
            for o in res.json():
                open_list.append({
                    'id': o.get('client_order_id', ''),
                    'symbol': o.get('symbol', '').replace('/', '').upper(),
                    'side': o.get('side', '').lower(),
                    'status': o.get('status', ''),
                    'qty': float(o.get('quantity', 0)),
                    'notional': float(o.get('quantity', 0)) * float(o.get('limit_price', 0)),
                })
            print(f"[webull] Fetched {len(open_list)} open orders")
            return open_list
        except Exception as e:
            print(f"[webull] Error getting open orders: {e}")
            return []

    def place_order(self, symbol: str, notional: float, side: str = 'buy',
                    stop_loss: float = 0, take_profit: float = 0) -> dict:
        """
        Places a market order. Webull doesn't support notional orders,
        so we fetch current price and convert to quantity.
        """
        if self.simulation_mode:
            return self._sim_order(symbol, notional, side, stop_loss, take_profit)

        try:
            # Step 1: Get current price to convert notional → qty
            current_price = self._get_current_price(symbol)
            if current_price <= 0:
                return {'success': False, 'error': f'Could not fetch price for {symbol}'}

            qty = notional / current_price

            # Step 2: Determine instrument type
            clean_symbol = symbol.upper().replace("/", "")
            is_crypto = any(clean_symbol.endswith(b) for b in ["USD", "USDT", "USDC"])
            instrument_type = "CRYPTO" if is_crypto else "EQUITY"

            # Step 3: Build order payload
            client_order_id = uuid.uuid4().hex
            order_payload = [{
                "combo_type": "NORMAL",
                "client_order_id": client_order_id,
                "symbol": clean_symbol,
                "instrument_type": instrument_type,
                "market": "US",
                "order_type": "MARKET",
                "quantity": str(round(qty, 6)),
                "side": side.upper(),
                "time_in_force": "GTC" if is_crypto else "DAY",
                "entrust_type": "QTY",
                "support_trading_session": "CORE",
            }]

            res = self._retry_request(
                self.trade_client.order_v2.place_order,
                self.account_id,
                order_payload
            )

            if res.status_code == 200:
                fee = round(notional * 0.001, 2) if is_crypto else 0.00
                return {
                    'success': True,
                    'order_id': client_order_id,
                    'symbol': symbol,
                    'side': side.upper(),
                    'qty': round(qty, 6),
                    'notional': round(notional, 2),
                    'total_cost': round(notional + fee, 2),
                    'fees': fee,
                    'status': 'submitted',
                }
            else:
                error_msg = res.text if hasattr(res, 'text') else str(res)
                print(f"[webull] Order error: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            print(f"[webull] Order error: {e}")
            return {'success': False, 'error': str(e)}

    def close_position(self, symbol: str) -> dict:
        """Closes an entire position by placing a sell order for full qty."""
        if self.simulation_mode:
            if symbol in self.sim_positions:
                pos = self.sim_positions.pop(symbol)
                proceeds = pos['qty'] * pos.get('current_price', pos['avg_price'])
                self.sim_cash += proceeds
                self._recalc_sim_equity()
                return {'success': True, 'symbol': symbol, 'proceeds': round(proceeds, 2)}
            return {'success': False, 'error': f'No position in {symbol}'}

        try:
            # Find position qty
            positions = self.get_positions()
            clean_sym = symbol.upper().replace("/", "")
            pos = next((p for p in positions if p['symbol'] == clean_sym), None)

            if not pos or pos['qty'] <= 0:
                return {'success': False, 'error': f'No position found for {symbol}'}

            # Place a sell order for the full quantity
            is_crypto = any(clean_sym.endswith(b) for b in ["USD", "USDT", "USDC"])
            client_order_id = uuid.uuid4().hex
            order_payload = [{
                "combo_type": "NORMAL",
                "client_order_id": client_order_id,
                "symbol": clean_sym,
                "instrument_type": "CRYPTO" if is_crypto else "EQUITY",
                "market": "US",
                "order_type": "MARKET",
                "quantity": str(pos['qty']),
                "side": "SELL",
                "time_in_force": "GTC" if is_crypto else "DAY",
                "entrust_type": "QTY",
                "support_trading_session": "CORE",
            }]

            res = self._retry_request(
                self.trade_client.order_v2.place_order,
                self.account_id,
                order_payload
            )

            if res.status_code == 200:
                return {'success': True, 'symbol': symbol, 'qty': pos['qty']}
            else:
                return {'success': False, 'error': res.text}

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def close_all_positions(self) -> dict:
        """Closes all open positions."""
        if self.simulation_mode:
            count = len(self.sim_positions)
            self.sim_positions = {}
            self.sim_equity = self.sim_cash
            return {'success': True, 'count': count}

        try:
            positions = self.get_positions()
            closed = 0
            for pos in positions:
                result = self.close_position(pos['symbol'])
                if result.get('success'):
                    closed += 1
            return {'success': True, 'count': closed}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_recent_trades(self, limit: int = 10) -> list:
        """Fetches recently filled orders for reconciliation."""
        if self.simulation_mode:
            return []

        try:
            res = self._retry_request(
                self.trade_client.order_v2.get_order_history,
                self.account_id,
                page_size=limit
            )
            if res.status_code != 200:
                return []

            trades = []
            for o in res.json():
                if o.get('status', '').upper() == 'FILLED':
                    trades.append({
                        'symbol': o.get('symbol', '').replace('/', '').upper(),
                        'qty': float(o.get('filled_quantity', 0)),
                        'price': float(o.get('avg_fill_price', 0)),
                        'side': o.get('side', '').upper(),
                        'time': o.get('update_time', ''),
                        'id': o.get('client_order_id', '')
                    })
            return trades[:limit]
        except Exception as e:
            print(f"[webull] Error getting recent trades: {e}")
            return []

    def search_assets(self, query: str) -> list:
        """Searches for assets matching the query. Proxies to Yahoo Finance for speed."""
        import requests
        try:
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, headers=headers, timeout=5).json()
            results = []
            for quote in res.get('quotes', []):
                if quote.get('quoteType') in ['EQUITY', 'CRYPTOCURRENCY', 'ETF']:
                    results.append({
                        "symbol": quote.get('symbol'),
                        "name": quote.get('shortname', quote.get('longname', quote.get('symbol')))
                    })
            return results[:10]
        except Exception as e:
            print(f"[webull] Search error: {e}")
            return []

    def get_bars(self, symbol: str, timespan: str = "m5", count: int = 200) -> list:
        """
        Fetches historical OHLCV bars from Webull.
        Timespan mapping: m1, m2, m5, m15, m30, h1, h2, h4, d1, w1
        """
        if self.market_client is None:
            return []

        try:
            # Step 1: Normalize symbol and determine category
            clean_symbol = symbol.upper().replace("/", "")
            is_crypto = any(clean_symbol.endswith(b) for b in ["USD", "USDT", "USDC"])
            category = "CRYPTO" if is_crypto else "STOCK"

            # Step 2: Request bars
            # Note: Method names in the official SDK: market_data.get_history_bar
            res = self._retry_request(
                self.market_client.get_history_bar,
                clean_symbol,
                category=category,
                timespan=timespan,
                count=count
            )

            if res.status_code == 200:
                data = res.json()
                bars = []
                for b in data:
                    # Webull returns timestamp in seconds or milliseconds
                    ts = b.get('time')
                    if ts and ts > 10**10: ts = ts / 1000 # convert ms to s
                    
                    bars.append({
                        'time': ts,
                        'open': float(b.get('open', 0)),
                        'high': float(b.get('high', 0)),
                        'low': float(b.get('low', 0)),
                        'close': float(b.get('close', 0)),
                        'volume': float(b.get('volume', 0))
                    })
                return bars
            else:
                print(f"[webull] Error fetching bars: {res.status_code} {res.text}")
                return []
        except Exception as e:
            print(f"[webull] Error fetching bars: {e}")
            return []

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _get_current_price(self, symbol: str) -> float:
        """Fetches current market price for notional→qty conversion."""
        if self.market_client is None:
            print(f"[webull] No market client. Using fallback price for {symbol}")
            return 0.0

        try:
            clean = symbol.upper().replace("/", "")
            res = self._retry_request(self.market_client.get_snapshot, [clean])
            if res.status_code == 200:
                data = res.json()
                if data:
                    return float(data[0].get('last_price', 0))
        except Exception as e:
            print(f"[webull] Price fetch error for {symbol}: {e}")
        return 0.0

    def _recalc_sim_equity(self):
        """Recalculates sim equity from cash + positions."""
        self.sim_equity = self.sim_cash + sum(
            p['qty'] * p.get('current_price', p['avg_price'])
            for p in self.sim_positions.values()
        )

    def _sim_positions_list(self) -> list:
        """Returns simulation positions in standard format."""
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
                'stop_loss': 0,
                'take_profit': 0,
            })
        return positions

    def _sim_order(self, symbol, notional, side, stop_loss, take_profit):
        """Simulates an order in local state."""
        if side.lower() == 'buy':
            if notional > self.sim_cash:
                return {'success': False, 'error': 'Insufficient buying power'}

            fee = round(notional * 0.001, 2)
            price = 100.0
            qty = round(notional / price, 6)

            if symbol in self.sim_positions:
                existing = self.sim_positions[symbol]
                total_cost = (existing['qty'] * existing['avg_price']) + notional
                existing['qty'] += qty
                existing['avg_price'] = total_cost / existing['qty']
            else:
                self.sim_positions[symbol] = {
                    'qty': qty, 'avg_price': price, 'current_price': price,
                    'stop_loss': stop_loss, 'take_profit': take_profit,
                }

            self.sim_cash -= (notional + fee)
            self._recalc_sim_equity()

            return {
                'success': True,
                'order_id': f'SIM-WB-{uuid.uuid4().hex[:8]}',
                'symbol': symbol, 'side': 'BUY',
                'qty': qty, 'notional': round(notional, 2),
                'total_cost': round(notional + fee, 2),
                'fees': fee, 'status': 'filled',
            }

        elif side.lower() == 'sell':
            return self.close_position(symbol)

        return {'success': False, 'error': 'Invalid side'}
