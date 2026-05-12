"""Broker factory.

`get_broker(strategy_id)` returns the adapter to use for a given
strategy. Today this always resolves to the Alpaca adapter.

The factory and broker protocol exist so alternate brokers can be
added behind a per-strategy or global config switch with no callsite
changes, should the need arise in the future.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from app.services.broker.alpaca_adapter import AlpacaBroker
from app.services.broker.base import BrokerClient

logger = logging.getLogger("screener")


def _resolve_broker_name(strategy_id: str | None) -> str:
    if strategy_id:
        per_strategy = os.environ.get(f"STRATEGY_BROKER_{strategy_id.upper()}")
        if per_strategy:
            return per_strategy.strip().lower()
    return os.environ.get("BROKER_TYPE", "alpaca").strip().lower()


@lru_cache(maxsize=8)
def _build(broker_name: str) -> BrokerClient:
    if broker_name == "alpaca":
        return AlpacaBroker()
    if broker_name == "ibkr":
        from app.services.broker.ibkr_adapter import IBBroker
        return IBBroker()
    logger.warning("Unknown broker '%s'; falling back to Alpaca", broker_name)
    return AlpacaBroker()


def get_broker(strategy_id: str | None = None) -> BrokerClient:
    """Resolve the broker adapter for a strategy. Cached per-name."""
    return _build(_resolve_broker_name(strategy_id))


__all__ = ["get_broker"]
