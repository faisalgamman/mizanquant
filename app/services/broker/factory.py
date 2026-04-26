"""Broker factory.

`get_broker(strategy_id)` returns the adapter to use for a given
strategy. Today this always resolves to the Alpaca adapter — the
factory exists so Phase B (Interactive Brokers) can drop in `IBBroker`
behind a per-strategy or global config switch with no callsite changes.

Routing rules:

1. **Per-strategy override** — `STRATEGY_BROKER_<id>` env var (e.g.
   `STRATEGY_BROKER_A=ibkr`) takes precedence. Lets you run Strategy A
   on IB while B and C stay on Alpaca during migration.
2. **Global default** — `BROKER_TYPE` env var (default `"alpaca"`).
3. **Hardcoded fallback** — Alpaca, since that is what everything is
   wired to today.

Unknown broker names log a warning and fall back to Alpaca rather than
crashing — a typo in env vars must not take live trading offline.
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
    if broker_name in ("ibkr", "ib", "interactive_brokers"):
        # Phase B: implemented in app/services/broker/ibkr_adapter.py
        try:
            from app.services.broker.ibkr_adapter import IBBroker  # type: ignore

            return IBBroker()
        except Exception as exc:
            logger.error(
                "IBKR broker requested but adapter not available (%s); "
                "falling back to Alpaca",
                exc,
            )
            return AlpacaBroker()
    logger.warning("Unknown broker '%s'; falling back to Alpaca", broker_name)
    return AlpacaBroker()


def get_broker(strategy_id: str | None = None) -> BrokerClient:
    """Resolve the broker adapter for a strategy. Cached per-name."""
    return _build(_resolve_broker_name(strategy_id))


__all__ = ["get_broker"]
