"""
Broker Factory — Single-line broker swap for the trading engine.

Usage in main.py:
    from broker_factory import create_broker
    broker = create_broker()

Set BROKER_TYPE in config.py or .env to switch:
    BROKER_TYPE=alpaca  (default)
    BROKER_TYPE=webull
"""

import config
import hmac

_alpaca_data_client_cache = {}


def invalidate_alpaca_client_cache(api_key: str = None, secret_key: str = None):
    """Remove cached data clients so unlinked credentials cannot be reused."""
    if api_key is None and secret_key is None:
        keys = list(_alpaca_data_client_cache)
    else:
        keys = [(str(api_key or ""), str(secret_key or ""))]
    for cache_key in keys:
        clients = _alpaca_data_client_cache.pop(cache_key, None)
        for client in clients or ():
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _live_uid_is_authorized(uid: str) -> bool:
    configured_uid = str(getattr(config, "BROKER_LIVE_TENANT_UID", "") or "").strip()
    supplied_uid = str(uid or "").strip()
    return bool(configured_uid and supplied_uid and hmac.compare_digest(configured_uid, supplied_uid))


def _live_account_binding_is_configured() -> bool:
    configured = str(getattr(config, "BROKER_LIVE_ACCOUNT_FINGERPRINT", "") or "").strip().lower()
    return len(configured) == 16 and all(char in "0123456789abcdef" for char in configured)


def create_broker(uid: str = None):
    """Creates the appropriate broker based on config.BROKER_TYPE."""
    broker_type = getattr(config, 'BROKER_TYPE', 'alpaca').lower()

    if broker_type == 'webull':
        from webull_broker import WebullBroker
        data_only = bool(getattr(config, "WEBULL_DATA_ONLY", True))
        live_enabled = bool(getattr(config, "WEBULL_LIVE_TRADING_ENABLED", False))
        single_tenant = bool(getattr(config, "WEBULL_SINGLE_TENANT_MODE", False))
        if not data_only and (
            not live_enabled
            or not single_tenant
            or not _live_uid_is_authorized(uid)
            or not _live_account_binding_is_configured()
        ):
            raise RuntimeError(
                "Webull live trading is disabled unless WEBULL_LIVE_TRADING_ENABLED=true "
                "WEBULL_SINGLE_TENANT_MODE=true, BROKER_LIVE_TENANT_UID matches the authenticated user, "
                "and BROKER_LIVE_ACCOUNT_FINGERPRINT is configured."
            )
        print(f"[factory] Initializing WebullBroker ({'data-only' if data_only else 'live'})...")
        broker = WebullBroker(data_only=data_only)
        broker.live_tenant_authorized = _live_uid_is_authorized(uid) and _live_account_binding_is_configured()
        return broker
    else:
        from alpaca_broker import AlpacaBroker
        print(f"[factory] Initializing AlpacaBroker...")
        broker = AlpacaBroker()
        broker.live_tenant_authorized = _live_uid_is_authorized(uid) and _live_account_binding_is_configured()
        return broker


def get_alpaca_data_credentials():
    """Return ``(api_key, secret_key)`` for market-data requests, or ``(None, None)``.

    The user's own linked keys win; the server's keys are the fallback. The
    values are never logged.
    """
    from user_config import get_user_config

    usr_cfg = get_user_config()
    api_key = (getattr(usr_cfg, 'ALPACA_API_KEY', '') or '').strip()
    secret_key = (getattr(usr_cfg, 'ALPACA_SECRET_KEY', '') or '').strip()

    if not api_key or api_key.startswith("your_alpaca"):
        api_key = (getattr(config, 'ALPACA_API_KEY', '') or '').strip()
        secret_key = (getattr(config, 'ALPACA_SECRET_KEY', '') or '').strip()

    if not api_key or api_key.startswith("your_alpaca"):
        return None, None
    return api_key, secret_key


def get_alpaca_clients():
    """Returns (StockHistoricalDataClient, CryptoHistoricalDataClient) for data fetching."""
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient

    api_key, secret_key = get_alpaca_data_credentials()
    if not api_key:
        return None, None

    cache_key = (api_key, secret_key)
    if cache_key in _alpaca_data_client_cache:
        return _alpaca_data_client_cache[cache_key]

    s_client = StockHistoricalDataClient(api_key, secret_key)
    c_client = CryptoHistoricalDataClient(api_key, secret_key)
    _alpaca_data_client_cache[cache_key] = (s_client, c_client)
    return _alpaca_data_client_cache[cache_key]
