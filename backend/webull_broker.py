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

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*utcnow.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=".*webull.*")

import uuid
import time
import re
import pandas as pd

import config
import os
import logging
import hashlib
import hmac
import threading
import firebase_admin
from firebase_admin import firestore
import base64
import tempfile
from firebase_credentials import load_firebase_credential

_token_sync_lock = threading.Lock()
_last_token_sync_digest = None
_token_sync_retry_at = 0.0
_token_sync_failures = 0


def _safe_error_type(exc: BaseException) -> str:
    """Return diagnostics that cannot echo provider or credential material."""
    return type(exc).__name__


def _token_path() -> str:
    """Return one private runtime path, never a repository-relative path."""
    configured = os.getenv("BOTBULLS_TOKEN_DIR", "").strip()
    token_dir = configured or os.path.join(tempfile.gettempdir(), "botbulls-secrets")
    return os.path.join(os.path.abspath(token_dir), "token.txt")


def _write_token_securely(token_content: str) -> None:
    """Write the SDK token atomically with owner-only permissions."""
    token_path = _token_path()
    token_dir = os.path.dirname(token_path)
    os.makedirs(token_dir, mode=0o700, exist_ok=True)
    try:
        os.chmod(token_dir, 0o700)
    except OSError:
        pass
    fd, tmp_path = tempfile.mkstemp(prefix=".token.", dir=token_dir, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(token_content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, token_path)
        os.chmod(token_path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
try:
    from cryptography.fernet import Fernet
except ImportError:
    pass

def get_vault():
    secret_key = os.getenv("VAULT_SECRET_KEY", "").strip()
    if not secret_key:
        raise RuntimeError("VAULT_SECRET_KEY is required to access encrypted broker state")
    return Fernet(secret_key.encode())


def _load_webull_firestore_credentials(db):
    """Read encrypted tenant credentials and migrate one legacy plaintext record."""
    doc_ref = db.collection("system").document("webull_api")
    doc = doc_ref.get(timeout=2.0, retry=None)
    if not doc.exists:
        return {}
    data = doc.to_dict() or {}
    vault = get_vault()
    values = {}
    migrated = {}
    for field in ("app_key", "app_secret", "account_id"):
        encrypted = str(data.get(f"{field}_enc") or "")
        if encrypted:
            try:
                values[field] = vault.decrypt(encrypted.encode()).decode().strip()
            except Exception:
                raise RuntimeError(f"Encrypted Webull {field} could not be decrypted") from None
            continue
        # One-time migration for the historical schema.  The plaintext value
        # is never written back; the legacy fields are deleted immediately.
        legacy = str(data.get(field) or "").strip()
        if legacy:
            values[field] = legacy
            migrated[f"{field}_enc"] = vault.encrypt(legacy.encode()).decode()
    if migrated:
        migrated["updated_at"] = firestore.SERVER_TIMESTAMP
        for field in ("app_key", "app_secret", "account_id"):
            migrated[field] = firestore.DELETE_FIELD
        doc_ref.set(migrated, merge=True, timeout=2.0, retry=None)
        print("[webull] Migrated legacy Webull credential fields to encrypted storage.")
    return values

def get_firestore_db():
    try:
        if not firebase_admin._apps:
            cred = load_firebase_credential()
            if cred:
                firebase_admin.initialize_app(cred)
            else:
                firebase_admin.initialize_app(options={'projectId': 'bot-bulls'})
        return firestore.client()
    except Exception as e:
        print(f"[webull] Could not get Firestore client: {_safe_error_type(e)}")
        return None

def sync_token_from_firestore():
    db = get_firestore_db()
    if not db: return
    try:
        doc_ref = db.collection("system").document("webull_token")
        doc = doc_ref.get(timeout=2.0, retry=None)
        if doc.exists:
            data = doc.to_dict()
            token_content_enc = data.get("token_content", "")
            if token_content_enc:
                try:
                    f_net = get_vault()
                    token_content = f_net.decrypt(token_content_enc.encode()).decode()
                except Exception as e:
                    print("[webull] Could not decrypt stored token; refusing plaintext fallback")
                    return

                _write_token_securely(token_content)
                print("[webull] Restored Webull auth token from Firestore.")
    except Exception as e:
        print(f"[webull] Error restoring token from Firestore: {type(e).__name__}")

def sync_token_to_firestore():
    def _run():
        global _last_token_sync_digest, _token_sync_retry_at, _token_sync_failures
        if time.monotonic() < _token_sync_retry_at:
            return
        db = get_firestore_db()
        if not db: return
        try:
            token_path = _token_path()
            if os.path.exists(token_path):
                with open(token_path, "r") as f:
                    content = f.read()
                if content.strip():
                    content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    with _token_sync_lock:
                        if content_digest == _last_token_sync_digest:
                            return
                    try:
                        f_net = get_vault()
                        token_content_enc = f_net.encrypt(content.encode()).decode()
                    except Exception as e:
                        print(f"[webull] Error encrypting token: {type(e).__name__}")
                        return
                        
                    with _token_sync_lock:
                        if content_digest == _last_token_sync_digest:
                            return
                        db.collection("system").document("webull_token").set({
                            "token_content": token_content_enc,
                            "updated_at": firestore.SERVER_TIMESTAMP
                        }, timeout=2.0, retry=None)
                        _last_token_sync_digest = content_digest
                        _token_sync_failures = 0
                        _token_sync_retry_at = 0.0
                    print("[webull] Saved encrypted Webull auth token to Firestore.")
        except Exception as e:
            _token_sync_failures += 1
            delay = min(300.0, 30.0 * (2 ** min(_token_sync_failures - 1, 3)))
            _token_sync_retry_at = time.monotonic() + delay
            print(f"[webull] Error saving token to Firestore: {type(e).__name__}; backing off {delay:.0f}s.")
    threading.Thread(target=_run, daemon=True).start()


try:
    from webull.core.client import ApiClient
    from webull.trade.trade_client import TradeClient
    from webull.data.data_client import DataClient
    WEBULL_AVAILABLE = True
except ImportError:
    WEBULL_AVAILABLE = False
    print("[webull] webull-openapi-python-sdk not installed. Running in simulation mode.")


# ──────────────────────────────────────────────
# Webull API Endpoints (Production Only)
# ──────────────────────────────────────────────
ENDPOINTS = {
    "trade": "api.webull.com",
    "market_data": "api.webull.com",
    "events": "events-api.webull.com",
}


class WebullBroker:
    """Wrapper around Webull's Trading API with AlpacaBroker-compatible interface."""

    def __init__(self, data_only: bool = None, auto_connect: bool = True):
        self.simulation_mode = True
        self.live_trading_armed = False
        self.api_client = None
        self.trade_client = None
        self.market_client = None
        self.account_id = None
        self._risk_account_key = "webull:simulation"
        self.order_owner_tag = ""

        # Simulation state (mirrors AlpacaBroker)
        self.sim_equity = 100.0
        self.sim_cash = 100.0
        self.sim_positions = {}
        self.sim_orders = []
        # Bounded in-process candle history.  Crypto refreshes use the newest
        # segment after the initial load; metadata tracks when a full repair
        # refresh is due.
        self._bar_history_cache = {}
        self._bar_history_meta = {}
        self._bar_history_loaded = set()

        # Auto-connect if keys are configured
        app_key = getattr(config, 'WEBULL_APP_KEY', '')
        app_secret = getattr(config, 'WEBULL_APP_SECRET', '')
        account_id = getattr(config, 'WEBULL_ACCOUNT_ID', '')
        
        # Fallback to Firestore if .env keys are missing or default
        if not app_key or app_key == "your_webull_app_key_here":
            db = get_firestore_db()
            if db:
                try:
                    doc = db.collection("system").document("webull_api").get(timeout=2.0, retry=None)
                    if doc.exists:
                        data = _load_webull_firestore_credentials(db)
                        app_key = data.get("app_key", app_key)
                        app_secret = data.get("app_secret", app_secret)
                        if not account_id:
                            account_id = data.get("account_id", "")
                        print("[webull] Fetched Webull API credentials from Firestore.")
                except Exception as e:
                    print(f"[webull] Error fetching API credentials from Firestore: {_safe_error_type(e)}")

        # Update config dynamically so other parts of the app know
        config.WEBULL_APP_KEY = app_key
        config.WEBULL_APP_SECRET = app_secret
        if account_id:
            config.WEBULL_ACCOUNT_ID = account_id

        if auto_connect and app_key and app_key != "your_webull_app_key_here":
            if data_only is None:
                data_only = getattr(config, 'WEBULL_DATA_ONLY', False)
            self.connect(app_key, app_secret, data_only=data_only)

    def clear_credentials(self):
        """Release SDK clients and the private Webull session token."""
        self.api_client = None
        self.trade_client = None
        self.market_client = None
        self.account_id = None
        self.simulation_mode = True
        self.live_trading_armed = False
        self._risk_account_key = "webull:simulation"
        config.WEBULL_APP_KEY = ""
        config.WEBULL_APP_SECRET = ""
        config.WEBULL_ACCOUNT_ID = ""
        try:
            token_path = _token_path()
            if os.path.isfile(token_path):
                os.unlink(token_path)
        except OSError:
            pass

    @staticmethod
    def _quiet_sdk_logging(api_client):
        """Keep Webull SDK failures from dumping signed headers/tokens to stdout."""
        try:
            api_client.set_stream_logger(log_level=logging.CRITICAL)
        except Exception:
            pass

    def connect(self, app_key: str, app_secret: str, test_mode: bool = False, data_only: bool = False) -> bool:
        """Connects to Webull using App Key + App Secret (HMAC-SHA1 signed).

        data_only=True: initialise only the market data client (no trading, no account calls).
        """
        if not WEBULL_AVAILABLE:
            print("[webull] SDK not installed. Cannot connect.")
            return False

        try:
            app_key = app_key.strip()
            app_secret = app_secret.strip()
            mode_label = "Data-Only" if data_only else "Prod"
            print(f"[webull] Connecting ({mode_label})...")

            # Restore token from Firestore before initializing SDK client
            try:
                sync_token_from_firestore()
            except Exception as e:
                print(f"[webull] Firestore token restore error: {type(e).__name__}")

            # ── Market data client (always needed) ──────────────────────────
            market_api = ApiClient(app_key, app_secret, "us")
            self._quiet_sdk_logging(market_api)
            market_api.add_endpoint("us", ENDPOINTS["market_data"])
            self.market_client = DataClient(market_api)

            if data_only:
                # Skip trade client and account verification entirely
                self.simulation_mode = True  # no real trading through this broker
                print("[webull] Data-only mode: market data client ready. Trading disabled.")
                
                # Perform a dummy request to trigger token validation & MFA BEFORE saving to Firestore
                try:
                    print("[webull] Validating market data token...")
                    self.get_latest_prices(["SPY"])
                except Exception as e:
                    print(f"[webull] Warning during token validation: {_safe_error_type(e)}")
                    
                try:
                    sync_token_to_firestore()
                except Exception as e:
                    print(f"[webull] Firestore token save error: {_safe_error_type(e)}")
                return True

            # ── Trade client (only when trading is enabled) ─────────────────
            self.api_client = ApiClient(app_key, app_secret, "us")
            self._quiet_sdk_logging(self.api_client)
            self.api_client.add_endpoint("us", ENDPOINTS["trade"])
            self.trade_client = TradeClient(self.api_client)

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
                    print("[webull] Auto-discovered an account")

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
                account_fingerprint = hashlib.sha256(str(self.account_id).encode("utf-8")).hexdigest()[:16]
                configured_fingerprint = str(
                    getattr(config, "BROKER_LIVE_ACCOUNT_FINGERPRINT", "") or ""
                ).strip().lower()
                if not configured_fingerprint or not hmac.compare_digest(
                    configured_fingerprint, account_fingerprint
                ):
                    print("[webull] Live connection rejected: account binding mismatch.")
                    self.clear_credentials()
                    return False
                self._risk_account_key = f"webull:live:{account_fingerprint}"
                bal = res.json()
                equity = bal.get('total_market_value', bal.get('net_liquidation', 0))
                print("[webull] Connected to the configured account")
                try:
                    sync_token_to_firestore()
                except Exception as e:
                    print(f"[webull] Firestore token save error: {_safe_error_type(e)}")
                return True
            else:
                print(f"[webull] Balance check failed: HTTP {res.status_code}")
                return False

        except Exception as e:
            print(f"[webull] Connection failed: {type(e).__name__}")
            self.clear_credentials()
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
                err_str = str(e).lower()
                if "401" in err_str or "unauthorized" in err_str or "permission" in err_str or "403" in err_str:
                    raise e
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
                'daily_starting_equity': 100.0,
                'daily_pl': round(self.sim_equity - 100.0, 2),
                'daily_pl_pct': round(((self.sim_equity / 100.0) - 1) * 100, 2),
                'simulation': True,
                '_risk_account_key': self._risk_account_key,
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
                'daily_starting_equity': round(last_equity, 2),
                'daily_pl': round(daily_pl, 2),
                'daily_pl_pct': round(daily_pl_pct, 2),
                'simulation': False,
                '_risk_account_key': self._risk_account_key,
            }
        except Exception as e:
            print(f"[webull] Error getting account info: {_safe_error_type(e)}")
            return {
                'equity': 0, 'cash': 0, 'buying_power': 0,
                'daily_pl': 0, 'daily_pl_pct': 0, 'simulation': False,
                'error': 'Broker account data unavailable'
            }

    def _round_price(self, symbol: str, price) -> float:
        try:
            p = float(price)
            if p == 0:
                return 0.0
                
            if "USD" in str(symbol).upper() or "BTC" in str(symbol).upper():
                return round(p, 8)
                
            # Dynamic rounding based on size of the price
            if p < 0.0001:
                return round(p, 8)
            elif p < 0.01:
                return round(p, 6)
            elif p < 1.0:
                return round(p, 5)
            elif p < 10.0:
                return round(p, 4)
            else:
                return round(p, 2)
        except:
            return price

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

                sym = p.get('symbol', '').replace('/', '').upper()
                positions.append({
                    'symbol': sym,
                    'qty': qty,
                    'avg_price': self._round_price(sym, avg_price),
                    'current_price': self._round_price(sym, current_price),
                    'market_value': round(market_value, 2),
                    'cost_basis': round(cost_basis, 2),
                    'unrealized_pl': round(pl, 2),
                    'unrealized_pl_pct': round((pl / cost_basis) * 100, 2) if cost_basis > 0 else 0,
                    'stop_loss': 0,
                    'take_profit': 0,
                })
            print(f"[webull] Fetched {len(positions)} active positions")
            return positions
        except Exception as e:
            print(f"[webull] Error getting positions: {_safe_error_type(e)}")
            raise RuntimeError("Broker positions are unavailable; trading decisions are paused.") from None

    def get_open_orders(self) -> list:
        """Returns list of currently open (pending) orders."""
        if self.simulation_mode:
            return []

        try:
            order_api = self.trade_client.order_v2
            get_open = getattr(order_api, "get_open_orders", None) or getattr(order_api, "get_order_open", None)
            if not callable(get_open):
                raise AttributeError("Webull SDK does not expose an open-orders method")
            try:
                res = self._retry_request(get_open, self.account_id, page_size=100)
            except TypeError:
                # Compatibility with older Webull SDK builds whose alias only
                # accepts the account ID.
                res = self._retry_request(get_open, self.account_id)
            if res.status_code != 200:
                raise RuntimeError("Broker open orders are unavailable; trading decisions are paused.")

            open_list = []
            payload = res.json()
            if isinstance(payload, dict):
                payload = payload.get("orders") or payload.get("data") or payload.get("items") or []
            for o in payload if isinstance(payload, list) else []:
                open_list.append({
                    'id': o.get('client_order_id', ''),
                    'client_order_id': o.get('client_order_id', ''),
                    'symbol': o.get('symbol', '').replace('/', '').upper(),
                    'side': o.get('side', '').lower(),
                    'status': o.get('status', ''),
                    'qty': float(o.get('quantity', 0)),
                    'notional': float(o.get('quantity', 0)) * float(o.get('limit_price', 0)),
                })
            print(f"[webull] Fetched {len(open_list)} open orders")
            return open_list
        except Exception as e:
            print(f"[webull] Error getting open orders: {_safe_error_type(e)}")
            raise RuntimeError("Broker open orders are unavailable; trading decisions are paused.") from None

    def cancel_order_by_id(self, order_id: str) -> dict:
        """Cancel a Webull working order by client order ID."""
        if self.simulation_mode:
            return {'success': False, 'error': 'Cannot cancel orders in simple simulation mode.'}
        if not self.live_trading_armed:
            return {'success': False, 'error': 'Live trading is not armed for this session.'}
        try:
            if self.order_owner_tag:
                owned = next((o for o in self.get_open_orders() if str(o.get('id')) == str(order_id)), None)
                if not owned or not str(owned.get('client_order_id', '')).startswith(f"bb-{self.order_owner_tag}-"):
                    return {'success': False, 'error': 'Order is not owned by this trading session.'}
            cancel = getattr(self.trade_client.order_v2, "cancel_order", None)
            if not callable(cancel):
                raise AttributeError("Webull SDK does not expose order cancellation")
            res = self._retry_request(cancel, self.account_id, str(order_id))
            if res.status_code == 200:
                return {'success': True}
            return {'success': False, 'error': f"Webull cancellation failed (HTTP {res.status_code})."}
        except Exception as e:
            print(f"[webull] Error cancelling order {order_id}: {_safe_error_type(e)}")
            return {'success': False, 'error': 'Broker cancellation failed; reconcile the order before retrying.'}

    def cancel_orders_for_ticker(self, ticker: str, side_filter: str = None) -> dict:
        """Cancel working orders for one equity/crypto ticker or option underlying."""
        if not self.simulation_mode and not self.live_trading_armed:
            return {'success': False, 'error': 'Live trading is not armed for this session.'}
        import re

        ticker_norm = str(ticker or "").replace("/", "").upper()
        cancelled = 0
        errors = []
        for order in self.get_open_orders():
            if self.order_owner_tag and not str(order.get('client_order_id', '')).startswith(f"bb-{self.order_owner_tag}-"):
                continue
            order_symbol = str(order.get("symbol", "")).replace("/", "").upper()
            option_match = re.fullmatch(r"([A-Z]{1,6})\d{6}[CP]\d{8}", order_symbol)
            belongs_to_ticker = order_symbol == ticker_norm or bool(
                option_match and option_match.group(1) == ticker_norm
            )
            if not belongs_to_ticker:
                continue
            if side_filter and str(order.get("side", "")).lower() != str(side_filter).lower():
                continue
            result = self.cancel_order_by_id(str(order.get("id", "")))
            if result.get("success"):
                cancelled += 1
            else:
                errors.append(result.get("error", "Cancellation failed"))
        return {'success': not errors, 'cancelled': cancelled, 'errors': errors}

    def cancel_all_entry_orders(self) -> dict:
        """Cancel every working BUY order while preserving protective SELL exits."""
        if not self.simulation_mode and not self.live_trading_armed:
            return {'success': False, 'error': 'Live trading is not armed for this session.'}
        cancelled = 0
        errors = []
        for order in self.get_open_orders():
            if str(order.get("side", "")).lower() != "buy":
                continue
            if self.order_owner_tag and not str(order.get('client_order_id', '')).startswith(f"bb-{self.order_owner_tag}-"):
                continue
            result = self.cancel_order_by_id(str(order.get("id", "")))
            if result.get("success"):
                cancelled += 1
            else:
                errors.append(result.get("error", "Cancellation failed"))
        return {'success': not errors, 'cancelled': cancelled, 'errors': errors}

    def place_order(self, symbol: str, notional: float, side: str = 'buy',
                    stop_loss: float = 0, take_profit: float = 0,
                    client_order_id: str = None) -> dict:
        """
        Places a market order. Webull doesn't support notional orders,
        so we fetch current price and convert to quantity.
        """
        if self.simulation_mode:
            return self._sim_order(symbol, notional, side, stop_loss, take_profit)
        if not self.live_trading_armed:
            return {'success': False, 'error': 'Live trading is not armed for this session.'}

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
            client_order_id = client_order_id or uuid.uuid4().hex
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

            res = self.trade_client.order_v2.place_order(
                self.account_id,
                order_payload,
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
                print(f"[webull] Order error: HTTP {getattr(res, 'status_code', 'unknown')}")
                return {'success': False, 'error': 'Broker order request was rejected.'}

        except Exception as e:
            print(f"[webull] Order error: {_safe_error_type(e)}")
            return {
                'success': False,
                'indeterminate': True,
                'error': 'Broker order outcome is unknown; reconcile by client order ID before retrying.',
            }

    def close_position(self, symbol: str, market_session: str = "Regular", current_price: float = None,
                       client_order_id: str = None) -> dict:
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

            if not self.live_trading_armed:
                return {'success': False, 'error': 'Live trading is not armed for this session.'}

            # Place a sell order for the full quantity
            is_crypto = any(clean_sym.endswith(b) for b in ["USD", "USDT", "USDC"])
            if not client_order_id:
                intent = f"{self._risk_account_key}|{clean_sym}|{pos['qty']}|{pos.get('avg_price', 0)}|SELL"
                owner = f"{self.order_owner_tag}-" if self.order_owner_tag else ""
                client_order_id = f"bb-{owner}exit-" + hashlib.sha256(intent.encode("utf-8")).hexdigest()[:20]
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

            res = self.trade_client.order_v2.place_order(
                self.account_id,
                order_payload,
            )

            if res.status_code == 200:
                return {
                    'success': True,
                    'symbol': symbol,
                    'qty': pos['qty'],
                    'status': 'submitted',
                    'client_order_id': client_order_id,
                }
            else:
                print(f"[webull] Close-position request rejected: HTTP {getattr(res, 'status_code', 'unknown')}")
                return {'success': False, 'error': 'Broker close request was rejected.'}

        except Exception as e:
            return {
                'success': False,
                'indeterminate': True,
                'error': 'Broker close outcome is unknown; reconcile by client order ID before retrying.',
            }

    def close_all_positions(self) -> dict:
        """Closes all open positions."""
        if self.simulation_mode:
            count = len(self.sim_positions)
            self.sim_positions = {}
            self.sim_equity = self.sim_cash
            return {'success': True, 'count': count}

        if not self.live_trading_armed:
            return {'success': False, 'error': 'Live trading is not armed for this session.'}

        try:
            positions = self.get_positions()
            closed = 0
            for pos in positions:
                result = self.close_position(pos['symbol'])
                if result.get('success'):
                    closed += 1
            return {'success': True, 'count': closed}
        except Exception as e:
            return {'success': False, 'error': 'Unable to close all broker positions; reconcile before retrying.'}

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
            print(f"[webull] Error getting recent trades: {_safe_error_type(e)}")
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
                    raw_symbol = quote.get('symbol', '')
                    symbol = self._normalize_symbol(raw_symbol) if quote.get('quoteType') == 'CRYPTOCURRENCY' else raw_symbol
                    results.append({
                        "symbol": symbol,
                        "name": quote.get('shortname', quote.get('longname', symbol))
                    })
            return results[:10]
        except Exception as e:
            print(f"[webull] Search error: {_safe_error_type(e)}")
            return []

    def get_bars(
        self,
        symbol: str,
        timespan: str = "m5",
        count: int = 200,
        trading_sessions: str = None,
    ) -> list:
        """
        Fetches historical OHLCV bars from Webull.
        Timespan mapping: s30, m1, m2, m3, m5, m10, m15, m30, h1, h2, h4, d1, w1
        """
        if self.market_client is None:
            return []

        supported_timespans = ["S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240", "D", "W", "M", "Y"]
        if timespan.upper() not in supported_timespans:
            return []

        try:
            # Step 1: Normalize symbol and determine category
            clean_symbol = self._normalize_symbol(symbol)
            is_crypto = any(clean_symbol.endswith(b) for b in ["USD", "USDT", "USDC"])
            category = "US_CRYPTO" if is_crypto else "US_STOCK"

            requested_count = max(1, int(count or 200))
            sessions = str(trading_sessions or "RTH,PRE,ATH")
            history_cache = getattr(self, "_bar_history_cache", None)
            if not isinstance(history_cache, dict):
                history_cache = {}
                self._bar_history_cache = history_cache
            history_key = (clean_symbol, timespan.upper(), sessions)
            self._load_persisted_bar_history(history_key)
            cached_history = history_cache.get(history_key, [])
            history_meta = getattr(self, "_bar_history_meta", None)
            if not isinstance(history_meta, dict):
                history_meta = {}
                self._bar_history_meta = history_meta
            last_full_sync_at = float(
                (history_meta.get(history_key) or {}).get("last_full_sync_at", 0) or 0
            )
            now_ts = time.time()
            # A daily full refresh repairs provider corrections or a missed
            # interval while keeping ordinary candle updates incremental.
            full_sync_due = (
                not cached_history
                or len(cached_history) < min(200, requested_count)
                or not last_full_sync_at
                or now_ts - last_full_sync_at >= 24 * 60 * 60
            )

            def _time_value(raw_time):
                try:
                    numeric = float(raw_time)
                    if numeric > 10**10:
                        numeric /= 1000.0
                    return int(numeric * 1_000_000_000)
                except (TypeError, ValueError):
                    parsed = pd.to_datetime(raw_time, utc=True, errors='coerce')
                    return parsed.value if not pd.isna(parsed) else 0

            # Step 2: Request bars. Webull supports a last_time cursor for
            # stock bars. Page only when a caller genuinely needs more than a
            # single response; ordinary chart refreshes remain one API call.
            if is_crypto:
                if full_sync_due:
                    crypto_count = min(requested_count, 1200)
                else:
                    latest_time = _time_value(cached_history[-1].get("time")) / 1_000_000_000
                    tf_seconds_map = {
                        "S30": 30, "M1": 60, "M2": 120, "M3": 180, "M5": 300, "M10": 600,
                        "M15": 900, "M30": 1800, "M60": 3600, "M120": 7200,
                        "M240": 14400, "D": 86400, "W": 604800, "M": 2592000,
                    }
                    tf_sec = tf_seconds_map.get(timespan.upper(), 300)
                    elapsed_seconds = max(0.0, now_ts - latest_time)
                    needed_bars = int(elapsed_seconds / tf_sec) + 20
                    # The safety window lets the provider revise the most
                    # recent candles while avoiding a full crypto download.
                    crypto_count = min(1200, max(50, needed_bars))
                res = self._retry_request(
                    self.market_client.crypto_market_data.get_crypto_history_bar,
                    [clean_symbol],
                    category,
                    timespan.upper(),
                    count=str(crypto_count)
                )
                responses = [res]
            else:
                page_limit = 1650 if timespan.upper() == "M1" else 1200
                # Once a complete deep window has been cached, refresh only
                # the newest provider page and merge it with the older cache.
                # This avoids a second API call and its rate-limit delay on
                # every completed-candle refresh.
                latest_time = 0
                if cached_history:
                    latest_bar = cached_history[-1]
                    latest_time = _time_value(latest_bar.get('time')) / 1_000_000_000 # convert to seconds
                
                if len(cached_history) >= min(200, requested_count) and latest_time > 0:
                    tf_seconds_map = {
                        "S30": 30, "M1": 60, "M2": 120, "M3": 180, "M5": 300, "M10": 600,
                        "M15": 900, "M30": 1800, "M60": 3600, "M120": 7200,
                        "M240": 14400, "D": 86400, "W": 604800, "M": 2592000
                    }
                    tf_sec = tf_seconds_map.get(timespan.upper(), 300)
                    elapsed_seconds = time.time() - latest_time
                    needed_bars = int(elapsed_seconds / tf_sec) + 20 # add a 20-bar buffer for safety
                    remaining = min(page_limit, max(50, needed_bars))
                else:
                    remaining = requested_count
                last_time = None
                responses = []
                seen_cursors = set()

                while remaining > 0:
                    page_count = min(page_limit, remaining)
                    request_kwargs = {
                        "count": str(page_count),
                        "trading_sessions": sessions,
                    }
                    if last_time is not None:
                        request_kwargs["last_time"] = last_time
                    try:
                        res = self._retry_request(
                            self.market_client.market_data.get_history_bar,
                            clean_symbol,
                            category,
                            timespan.upper(),
                            **request_kwargs,
                        )
                    except Exception as page_exc:
                        if responses:
                            print(f"[webull] Older bar page unavailable; using the newest completed page: {_safe_error_type(page_exc)}")
                            break
                        raise
                    responses.append(res)
                    if not hasattr(res, "status_code") or res.status_code != 200:
                        break

                    page_data = res.json()
                    if not isinstance(page_data, list) or not page_data:
                        break
                    oldest = min(
                        (bar for bar in page_data if isinstance(bar, dict) and bar.get("time") is not None),
                        key=lambda bar: _time_value(bar.get("time")),
                        default=None,
                    )
                    cursor = oldest.get("time") if oldest else None
                    remaining -= len(page_data)
                    if len(page_data) < page_count or cursor is None or cursor in seen_cursors:
                        break
                    seen_cursors.add(cursor)
                    last_time = cursor
                    # The historical-bar endpoint is limited to one request
                    # per second per app key. This wait happens only for the
                    # second page of a cold, deep-history calculation.
                    time.sleep(1.05)

            raw_bars = []
            for res in responses:
                if not hasattr(res, "status_code") or res.status_code != 200:
                    status = getattr(res, "status_code", "unknown")
                    print(f"[webull] Error fetching bars: HTTP {status}")
                    continue
                data = res.json()

                # Crypto response is usually wrapped: [{"symbol": "BTCUSD", "result": [...]}]
                if is_crypto and isinstance(data, list) and data and isinstance(data[0], dict) and 'result' in data[0]:
                    data = data[0]['result']
                if isinstance(data, list):
                    raw_bars.extend(data)

            bars_by_time = {}
            for cached_bar in cached_history:
                cached_time = cached_bar.get('time') if isinstance(cached_bar, dict) else None
                if cached_time is not None:
                    bars_by_time[str(cached_time)] = dict(cached_bar)
            for b in raw_bars:
                if not isinstance(b, dict):
                    continue
                ts = b.get('time')
                if ts and isinstance(ts, (int, float)) and ts > 10**10:
                    ts = ts / 1000
                if ts is None:
                    continue
                bars_by_time[str(ts)] = {
                    'time': ts,
                    'open': float(b.get('open', 0)),
                    'high': float(b.get('high', 0)),
                    'low': float(b.get('low', 0)),
                    'close': float(b.get('close', 0)),
                    'volume': float(b.get('volume', 0))
                }

            result = sorted(bars_by_time.values(), key=lambda bar: _time_value(bar.get('time')))[-requested_count:]
            history_cache.pop(history_key, None)
            history_cache[history_key] = result
            if is_crypto and full_sync_due:
                history_meta[history_key] = {"last_full_sync_at": now_ts}
            while len(history_cache) > 24:
                history_cache.pop(next(iter(history_cache)))
            self._persist_bar_history(history_key, result, history_meta.get(history_key) or {})
            return result
        except Exception as e:
            print(f"[webull] Error fetching bars: {_safe_error_type(e)}")
            return []

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _bar_cache_namespace(history_key) -> str:
        digest = hashlib.sha256(repr(tuple(history_key)).encode("utf-8")).hexdigest()[:32]
        return f"webull_bars_{digest}"

    def _load_persisted_bar_history(self, history_key) -> None:
        """Hydrate one market-history key once per broker process.

        The SQLite store is shared by same-host workers, while the in-memory
        cache remains the hot path. Malformed or unavailable persistence is
        ignored so a provider refresh can still proceed.
        """
        loaded_keys = getattr(self, "_bar_history_loaded", None)
        if not isinstance(loaded_keys, set):
            loaded_keys = set()
            self._bar_history_loaded = loaded_keys
        if history_key in loaded_keys:
            return
        loaded_keys.add(history_key)
        try:
            from local_store import load_state
            payload = load_state("__market_data__", self._bar_cache_namespace(history_key)) or {}
            bars = payload.get("bars") if isinstance(payload, dict) else None
            if not isinstance(bars, list):
                return
            valid_bars = [bar for bar in bars[-1200:] if isinstance(bar, dict) and bar.get("time") is not None]
            if not valid_bars:
                return
            cache = getattr(self, "_bar_history_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._bar_history_cache = cache
            cache[history_key] = valid_bars
            metadata = getattr(self, "_bar_history_meta", None)
            if not isinstance(metadata, dict):
                metadata = {}
                self._bar_history_meta = metadata
            metadata[history_key] = {
                "last_full_sync_at": float(payload.get("last_full_sync_at", 0) or 0)
            }
        except Exception as exc:
            print(f"[webull] Persistent bar-cache load unavailable: {type(exc).__name__}")

    def _persist_bar_history(self, history_key, bars: list, metadata: dict) -> None:
        """Persist a bounded market-history snapshot for restart recovery."""
        if not isinstance(bars, list) or not bars:
            return
        try:
            from local_store import save_state
            save_state(
                "__market_data__",
                self._bar_cache_namespace(history_key),
                {
                    "version": 1,
                    "history_key": list(history_key),
                    "bars": bars[-1200:],
                    "last_full_sync_at": float((metadata or {}).get("last_full_sync_at", 0) or 0),
                    "saved_at": time.time(),
                },
            )
        except Exception as exc:
            # Persistence is an optimization and restart safeguard; it must
            # never turn a usable live provider response into a failed scan.
            print(f"[webull] Persistent bar-cache save unavailable: {type(exc).__name__}")

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        clean = str(symbol or "").upper().replace("/", "").strip()
        if re.fullmatch(r"[A-Z0-9]{1,12}-(?:USD|USDT|USDC)", clean):
            clean = clean.replace("-", "")
        return clean

    @staticmethod
    def _is_crypto_symbol(clean_symbol: str) -> bool:
        return any(clean_symbol.endswith(base) for base in ["USD", "USDT", "USDC"])

    @staticmethod
    def _snapshot_items(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("data", "result", "results", "items"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    return nested
                if isinstance(nested, dict):
                    return [nested]
            return [payload]
        return []

    def _extract_snapshot_price(self, item) -> float:
        if not isinstance(item, dict):
            return None

        for nested_key in ("snapshot", "data", "result", "quote"):
            nested = item.get(nested_key)
            if isinstance(nested, dict):
                nested_price = self._extract_snapshot_price(nested)
                if nested_price:
                    return nested_price
            elif isinstance(nested, list) and nested:
                nested_price = self._extract_snapshot_price(nested[0])
                if nested_price:
                    return nested_price

        for key in (
            "price", "extend_hour_last_price", "nightPrice", "overnightPrice",
            "afterHoursPrice", "afterPrice", "extPrice", "pPrice",
            "preMarketPrice", "latest_price", "latestPrice", "last_price",
            "lastPrice", "close", "last", "trade_price", "tradePrice",
            "lastSalePrice", "mark"
        ):
            raw = item.get(key)
            if raw is None:
                continue
            try:
                price = float(str(raw).replace(",", ""))
                if price > 0:
                    return price
            except (TypeError, ValueError):
                continue
        return None

    def _record_snapshot_price(self, prices: dict, requested_symbol: str, item: dict):
        price = self._extract_snapshot_price(item)
        if not price:
            return

        response_symbol = None
        if isinstance(item, dict):
            raw_sym = item.get("symbol") or item.get("ticker") or item.get("code")
            if raw_sym:
                response_symbol = self._normalize_symbol(raw_sym)

        clean_requested = self._normalize_symbol(requested_symbol) if requested_symbol else None
        target_symbol = response_symbol or clean_requested
        if target_symbol:
            prices[target_symbol] = price
            if clean_requested and clean_requested != target_symbol:
                if ''.join(e for e in clean_requested if e.isalnum()) == ''.join(e for e in target_symbol if e.isalnum()):
                    prices[clean_requested] = price

    def _get_current_price(self, symbol: str) -> float:
        """Fetches current market price for notional→qty conversion."""
        prices = self.get_latest_prices([symbol])
        try:
            return float(prices.get(self._normalize_symbol(symbol), 0.0) or 0.0)
        except Exception as e:
            print(f"[webull] Price fetch error for {symbol}: {_safe_error_type(e)}")
        return 0.0

    def get_latest_prices(self, symbols: list) -> dict:
        """Fetches current market prices for a list of symbols efficiently."""
        if self.market_client is None or not symbols:
            return {}

        prices = {}
        crypto_symbols = []
        stock_symbols = []

        for sym in symbols:
            clean = self._normalize_symbol(sym)
            if not clean:
                continue
            if self._is_crypto_symbol(clean):
                crypto_symbols.append(clean)
            else:
                stock_symbols.append(clean)

        def _record_snapshot_response(request_symbols, res, category):
            if res.status_code != 200:
                print(f"[webull] Snapshot error ({category}): HTTP {res.status_code}")
                return 0

            found = 0
            for idx, item in enumerate(self._snapshot_items(res.json())):
                requested = request_symbols[idx] if idx < len(request_symbols) else request_symbols[0]
                before = len(prices)
                self._record_snapshot_price(prices, requested, item)
                if len(prices) > before:
                    found += 1
            return found

        def _snapshot_client(category):
            if category == "US_CRYPTO":
                return self.market_client.crypto_market_data.get_crypto_snapshot
            return self.market_client.market_data.get_snapshot

        def _fetch_one(symbol, category):
            if category == "US_STOCK":
                try:
                    res = self._retry_request(_snapshot_client(category), symbol, category, extend_hour_required=True, overnight_required=True)
                except Exception:
                    res = self._retry_request(_snapshot_client(category), symbol, category, extend_hour_required=True)
            else:
                res = self._retry_request(_snapshot_client(category), symbol, category)
            return _record_snapshot_response([symbol], res, category)

        def _fetch_group(request_symbols, category):
            if not request_symbols:
                return

            if category == "US_STOCK":
                try:
                    res = self._retry_request(_snapshot_client(category), request_symbols, category, extend_hour_required=True, overnight_required=True)
                except Exception:
                    res = self._retry_request(_snapshot_client(category), request_symbols, category, extend_hour_required=True)
            else:
                res = self._retry_request(_snapshot_client(category), request_symbols, category)
            found = _record_snapshot_response(request_symbols, res, category)
            if found == 0:
                for symbol in request_symbols:
                    _fetch_one(symbol, category)

        try:
            _fetch_group(crypto_symbols, "US_CRYPTO")
            _fetch_group(stock_symbols, "US_STOCK")
        except Exception as e:
            print(f"[webull] Batch price fetch error: {_safe_error_type(e)}")

        return prices

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
                'avg_price': self._round_price(symbol, pos['avg_price']),
                'current_price': self._round_price(symbol, pos.get('current_price', pos['avg_price'])),
                'market_value': round(current_val, 2),
                'cost_basis': round(cost_basis, 2),
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
