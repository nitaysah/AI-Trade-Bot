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

_alpaca_data_client_cache = {}


def create_broker():
    """Creates the appropriate broker based on config.BROKER_TYPE."""
    broker_type = getattr(config, 'BROKER_TYPE', 'alpaca').lower()

    if broker_type == 'webull':
        from webull_broker import WebullBroker
        print(f"[factory] Initializing WebullBroker...")
        return WebullBroker()
    else:
        from alpaca_broker import AlpacaBroker
        print(f"[factory] Initializing AlpacaBroker...")
        return AlpacaBroker()


def get_alpaca_clients():
    """Returns (StockHistoricalDataClient, CryptoHistoricalDataClient) for data fetching."""
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
    from user_config import get_user_config
    
    usr_cfg = get_user_config()
    api_key = getattr(usr_cfg, 'ALPACA_API_KEY', '').strip()
    secret_key = getattr(usr_cfg, 'ALPACA_SECRET_KEY', '').strip()

    if not api_key or api_key.startswith("your_alpaca"):
        api_key = getattr(config, 'ALPACA_API_KEY', '').strip()
        secret_key = getattr(config, 'ALPACA_SECRET_KEY', '').strip()

    if not api_key or api_key.startswith("your_alpaca"):
        return None, None

    cache_key = (api_key, secret_key)
    if cache_key in _alpaca_data_client_cache:
        return _alpaca_data_client_cache[cache_key]

    s_client = StockHistoricalDataClient(api_key, secret_key)
    c_client = CryptoHistoricalDataClient(api_key, secret_key)
    _alpaca_data_client_cache[cache_key] = (s_client, c_client)
    return _alpaca_data_client_cache[cache_key]
