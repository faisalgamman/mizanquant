"""Broker abstraction layer.

The trading engine uses Alpaca as its broker through
`app/services/alpaca_client.py` and helper functions in
`trading_engine.py`. This package exposes a narrow interface
(`BrokerClient`) that captures exactly the operations the engine
needs from any broker:

    get_account, get_positions, get_orders, get_order,
    submit_order, cancel_order, close_position

Plus a factory (`get_broker`) that returns the right adapter for a
given strategy. The default adapter (`AlpacaBroker`) delegates to the
existing alpaca_client and trading_engine functions.
"""

from app.services.broker.base import BrokerClient
from app.services.broker.factory import get_broker

__all__ = ["BrokerClient", "get_broker"]
