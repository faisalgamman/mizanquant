"""Broker abstraction layer.

The current trading engine calls Alpaca directly through
`app/services/alpaca_client.py` and a few helper functions in
`trading_engine.py`. That is fine while there is one broker, but
binds every callsite to Alpaca's request shapes. Adding a second
broker (Interactive Brokers) without abstraction means duplicating
the engine, the fill watcher, the reconciliation loop, the orphan
re-armer, and so on — a maintenance trap.

This package exposes a narrow interface (`BrokerClient`) that captures
exactly the operations the engine needs from any broker:

    get_account, get_positions, get_orders, get_order,
    submit_order, cancel_order, close_position

Plus a factory (`get_broker`) that returns the right adapter for a
given strategy. The default adapter (`AlpacaBroker`) delegates to the
existing alpaca_client functions — so this layer is purely additive
and does not change runtime behavior. The Interactive Brokers adapter
plugs in via the same factory once Phase B lands.
"""

from app.services.broker.base import BrokerClient
from app.services.broker.factory import get_broker

__all__ = ["BrokerClient", "get_broker"]
