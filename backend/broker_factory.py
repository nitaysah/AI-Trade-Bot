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
