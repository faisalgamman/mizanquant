"""Alpaca adapter for the broker interface.

Delegates to the existing functions in `app/services/alpaca_client.py`
and a small number of helpers in `app/services/trading_engine.py`. No
new I/O — this class is a thin pass-through whose only job is to put
an interface boundary in front of Alpaca-specific calls so a second
broker (Interactive Brokers, Phase B) can be introduced without
rewriting the engine.
"""

from __future__ import annotations

from typing import Optional

from app.services.alpaca_client import (
    get_account as _alpaca_get_account,
    get_orders as _alpaca_get_orders,
    get_positions as _alpaca_get_positions,
)


class AlpacaBroker:
    name = "alpaca"

    def get_account(self, strategy_id: str | None = None) -> Optional[dict]:
        return _alpaca_get_account(strategy_id=strategy_id)

    def get_positions(self, strategy_id: str | None = None) -> list[dict]:
        return _alpaca_get_positions(strategy_id=strategy_id) or []

    def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
        strategy_id: str | None = None,
    ) -> list[dict]:
        return _alpaca_get_orders(status=status, limit=limit, strategy_id=strategy_id) or []

    def get_order(self, order_id: str, strategy_id: str | None = None) -> Optional[dict]:
        # Defer to trading_engine's helper which already handles auth/strategy
        # routing — importing lazily to avoid a circular import at module load.
        from app.services.trading_engine import _get_order

        return _get_order(order_id, strategy_id)

    def submit_order(self, payload: dict, strategy_id: str | None = None) -> Optional[dict]:
        from app.services.trading_engine import _submit_order

        return _submit_order(payload, strategy_id=strategy_id)

    def cancel_order(self, order_id: str, strategy_id: str | None = None) -> bool:
        from app.services.trading_engine import _cancel_order

        result = _cancel_order(order_id, strategy_id=strategy_id)
        return bool(result)

    def close_position(self, symbol: str, strategy_id: str | None = None) -> Optional[dict]:
        from app.services.trading_engine import _close_position

        return _close_position(symbol, strategy_id=strategy_id)


__all__ = ["AlpacaBroker"]
