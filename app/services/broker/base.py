"""Broker-agnostic interface used by the trading engine.

The methods below are the *minimum* surface required to run the
existing engine. Each method's contract is defined by what
`trading_engine.py`, `fill_watcher.py`, and `reconcile_positions`
already expect — the Alpaca adapter is the canonical reference
implementation and the IB adapter must match its return shapes.

Returns are dicts/lists of plain JSON-friendly types so the rest of
the engine doesn't need to know which broker it is talking to.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class BrokerClient(Protocol):
    """Protocol every broker adapter implements.

    Every method takes an optional `strategy_id` to support multiple
    accounts (one per strategy). When None, the adapter falls back to
    the default account.
    """

    name: str  # "alpaca" | "ibkr" | ...

    def get_account(self, strategy_id: str | None = None) -> Optional[dict]:
        """Return account snapshot: cash, equity, buying_power, status."""
        ...

    def get_positions(self, strategy_id: str | None = None) -> list[dict]:
        """Return open positions. Each item has at minimum:
        symbol, qty, avg_entry_price, unrealized_pl, unrealized_plpc."""
        ...

    def get_orders(
        self,
        status: str = "all",
        limit: int = 50,
        strategy_id: str | None = None,
    ) -> list[dict]:
        """Return orders. status ∈ {"open", "closed", "all"}.
        Each item has at minimum: id, client_order_id, symbol, side,
        qty, filled_qty, filled_avg_price, status, type, order_class,
        legs (optional)."""
        ...

    def get_order(
        self,
        order_id: str,
        strategy_id: str | None = None,
    ) -> Optional[dict]:
        """Return a single order by id."""
        ...

    def submit_order(
        self,
        payload: dict,
        strategy_id: str | None = None,
    ) -> Optional[dict]:
        """Submit a new order. `payload` follows Alpaca's REST shape;
        adapters for other brokers translate as needed."""
        ...

    def cancel_order(
        self,
        order_id: str,
        strategy_id: str | None = None,
    ) -> bool:
        """Cancel an open order. Returns True on success."""
        ...

    def close_position(
        self,
        symbol: str,
        strategy_id: str | None = None,
    ) -> Optional[dict]:
        """Close (DELETE) a position by symbol."""
        ...


__all__ = ["BrokerClient"]
