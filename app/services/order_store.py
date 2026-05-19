"""In-memory order store with optional DB persistence.

Provides fast O(1) lookups by order_id, client_order_id, and symbol
with secondary indices. Thread-safe for concurrent strategy access.

The store is the single source of truth for all live orders. Terminated
orders are eventually evicted to the DB and pruned from memory.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional, Sequence

from app.services.order_state import (
    OrderRecord,
    OrderState,
    ACTIVE_STATES,
    TERMINAL_STATES,
)

logger = logging.getLogger("screener")

# Maximum orders to keep in memory before LRU eviction
MAX_IN_MEMORY = 10_000

# How long to keep terminal orders in memory before pruning
TERMINAL_TTL = timedelta(minutes=30)


class OrderStore:
    """Thread-safe in-memory order store with indices.

    Usage:
        store = OrderStore()
        store.put(order)
        order = store.get_by_order_id("broker-123")
        active = store.get_active_by_strategy("A")
        store.transition(order.order_id, OrderState.FILLED)
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # Primary storage: order_id -> OrderRecord
        self._by_order_id: dict[str, OrderRecord] = OrderedDict()

        # Secondary indices
        self._by_coid: dict[str, str] = {}       # client_order_id -> order_id
        self._by_symbol: dict[str, set[str]] = {}  # symbol -> set of order_ids
        self._by_strategy: dict[str, set[str]] = {}  # strategy_id -> set of order_ids
        self._by_status: dict[OrderState, set[str]] = {}  # status -> set of order_ids

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def put(self, order: OrderRecord) -> None:
        """Insert or update an order record."""
        with self._lock:
            self._evict_if_needed()
            self._insert_or_update(order)

    def _insert_or_update(self, order: OrderRecord) -> None:
        """Upsert an order (assumes lock is held)."""
        oid = order.order_id
        coid = order.client_order_id

        # Remove old <coid> keyed entry if this order now has a real order_id
        if oid and coid:
            coid_key = f"__coid__{coid}"
            if coid_key in self._by_order_id and coid_key != oid:
                old_coid = self._by_order_id.pop(coid_key)
                self._remove_from_indices(old_coid)

        # Remove old indices if updating (same oid)
        if oid and oid in self._by_order_id:
            old = self._by_order_id[oid]
            self._remove_from_indices(old)

        # Add to primary store
        if oid:
            self._by_order_id[oid] = order
        elif coid:
            # Pre-acknowledgement: use coid as temporary key
            self._by_order_id[f"__coid__{coid}"] = order

        # Rebuild indices
        self._add_to_indices(order)

    def _add_to_indices(self, order: OrderRecord) -> None:
        """Add order to all secondary indices."""
        oid = order.order_id or f"__coid__{order.client_order_id}"

        if order.client_order_id:
            self._by_coid[order.client_order_id] = oid

        if order.symbol:
            self._by_symbol.setdefault(order.symbol, set()).add(oid)

        if order.strategy_id:
            self._by_strategy.setdefault(order.strategy_id, set()).add(oid)

        self._by_status.setdefault(order.status, set()).add(oid)

    def _remove_from_indices(self, order: OrderRecord) -> None:
        """Remove order from all secondary indices."""
        oid = order.order_id or f"__coid__{order.client_order_id}"

        if order.client_order_id:
            self._by_coid.pop(order.client_order_id, None)

        if order.symbol:
            symbols = self._by_symbol.get(order.symbol)
            if symbols:
                symbols.discard(oid)

        if order.strategy_id:
            strats = self._by_strategy.get(order.strategy_id)
            if strats:
                strats.discard(oid)

        status_set = self._by_status.get(order.status)
        if status_set:
            status_set.discard(oid)

    def get_by_order_id(self, order_id: str) -> Optional[OrderRecord]:
        """Lookup by broker-assigned order ID."""
        with self._lock:
            return self._by_order_id.get(order_id)

    def get_by_client_order_id(self, coid: str) -> Optional[OrderRecord]:
        """Lookup by our client_order_id."""
        with self._lock:
            oid = self._by_coid.get(coid)
            if oid:
                return self._by_order_id.get(oid)
            # Fallback: try __coid__ prefix for pre-ack orders
            return self._by_order_id.get(f"__coid__{coid}")

    def get_by_symbol(self, symbol: str) -> list[OrderRecord]:
        """Get all orders for a symbol."""
        with self._lock:
            oids = self._by_symbol.get(symbol.upper(), set())
            return [self._by_order_id[oid] for oid in oids if oid in self._by_order_id]

    def get_by_strategy(self, strategy_id: str) -> list[OrderRecord]:
        """Get all orders for a strategy."""
        with self._lock:
            oids = self._by_strategy.get(strategy_id, set())
            return [self._by_order_id[oid] for oid in oids if oid in self._by_order_id]

    def get_by_status(self, status: OrderState) -> list[OrderRecord]:
        """Get all orders in a given state."""
        with self._lock:
            oids = self._by_status.get(status, set())
            return [self._by_order_id[oid] for oid in oids if oid in self._by_order_id]

    def get_active(self) -> list[OrderRecord]:
        """Get all active (non-terminal) orders."""
        with self._lock:
            result: list[OrderRecord] = []
            for status in ACTIVE_STATES:
                oids = self._by_status.get(status, set())
                result.extend(
                    self._by_order_id[oid] for oid in oids if oid in self._by_order_id
                )
            return result

    def get_active_by_strategy(self, strategy_id: str) -> list[OrderRecord]:
        """Get active orders for a specific strategy."""
        with self._lock:
            strat_oids = self._by_strategy.get(strategy_id, set())
            active: list[OrderRecord] = []
            for oid in strat_oids:
                order = self._by_order_id.get(oid)
                if order and order.is_active:
                    active.append(order)
            return active

    def get_active_by_symbol(self, symbol: str) -> list[OrderRecord]:
        """Get active orders for a specific symbol."""
        with self._lock:
            sym_oids = self._by_symbol.get(symbol.upper(), set())
            active: list[OrderRecord] = []
            for oid in sym_oids:
                order = self._by_order_id.get(oid)
                if order and order.is_active:
                    active.append(order)
            return active

    def transition(self, order_id: str, new_state: OrderState) -> Optional[OrderRecord]:
        """Transition an order to a new state. Returns updated order or None."""
        with self._lock:
            order = self._by_order_id.get(order_id)
            if order is None:
                return None

            # Remove from old status index
            old_status_set = self._by_status.get(order.status)
            if old_status_set:
                old_status_set.discard(order_id)

            try:
                order.transition_to(new_state)
            except ValueError as e:
                logger.warning("Invalid order transition %s: %s", order_id, e)
                # Re-add to old status index
                self._by_status.setdefault(order.status, set()).add(order_id)
                return None

            # Add to new status index
            self._by_status.setdefault(new_state, set()).add(order_id)
            return order

    def remove(self, order_id: str) -> Optional[OrderRecord]:
        """Remove an order from the store entirely."""
        with self._lock:
            order = self._by_order_id.get(order_id)
            if order is None:
                return None
            self._remove_from_indices(order)
            del self._by_order_id[order_id]
            return order

    def count(self, status: Optional[OrderState] = None) -> int:
        """Count orders, optionally filtered by status."""
        with self._lock:
            if status is None:
                return len(self._by_order_id)
            return len(self._by_status.get(status, set()))

    def count_active(self) -> int:
        """Count active orders."""
        with self._lock:
            return sum(
                len(self._by_status.get(s, set())) for s in ACTIVE_STATES
            )

    # ------------------------------------------------------------------
    # Bulk queries
    # ------------------------------------------------------------------

    def find(self, **filters) -> list[OrderRecord]:
        """Flexible query by any combination of fields.

        Supported filters:
            symbol, strategy_id, side, status (OrderState),
            order_type, order_class, since (datetime), until (datetime)
        """
        with self._lock:
            # Determine candidate set — use narrowest index
            if "status" in filters and isinstance(filters["status"], OrderState):
                candidates = self._by_status.get(filters["status"], set())
            elif "strategy_id" in filters:
                candidates = self._by_strategy.get(filters["strategy_id"], set())
            elif "symbol" in filters:
                candidates = self._by_symbol.get(filters["symbol"].upper(), set())
            else:
                candidates = set(self._by_order_id.keys())

            result: list[OrderRecord] = []
            for oid in candidates:
                order = self._by_order_id.get(oid)
                if order is None:
                    continue
                if self._matches(order, **filters):
                    result.append(order)
            return result

    @staticmethod
    def _matches(order: OrderRecord, **filters) -> bool:
        """Check if an order matches all given filters."""
        for key, value in filters.items():
            if key == "symbol":
                if order.symbol.upper() != str(value).upper():
                    return False
            elif key == "strategy_id":
                if order.strategy_id != value:
                    return False
            elif key == "side":
                if order.side.lower() != str(value).lower():
                    return False
            elif key == "status":
                if order.status != value:
                    return False
            elif key == "order_type":
                if order.order_type != value:
                    return False
            elif key == "order_class":
                if order.order_class != value:
                    return False
            elif key == "since":
                if order.created_at < value:
                    return False
            elif key == "until":
                if order.created_at > value:
                    return False
        return True

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        """Evict oldest terminal orders when over capacity."""
        if len(self._by_order_id) < MAX_IN_MEMORY:
            return
        # Evict from the front (oldest) if terminal
        to_remove: list[str] = []
        for oid, order in self._by_order_id.items():
            if len(self._by_order_id) - len(to_remove) < MAX_IN_MEMORY:
                break
            if order.is_terminal:
                to_remove.append(oid)
        for oid in to_remove:
            order = self._by_order_id.get(oid)
            if order:
                self._remove_from_indices(order)
                del self._by_order_id[oid]
        if to_remove:
            logger.info("OrderStore: evicted %d terminal orders", len(to_remove))

    def prune_terminal(self, max_age: timedelta = TERMINAL_TTL) -> int:
        """Remove terminal orders older than max_age. Returns count pruned."""
        with self._lock:
            cutoff = datetime.now(timezone.utc) - max_age
            to_remove: list[str] = []
            for oid, order in self._by_order_id.items():
                if order.is_terminal and order.updated_at < cutoff:
                    to_remove.append(oid)
            for oid in to_remove:
                self._remove_from_indices(self._by_order_id[oid])
                del self._by_order_id[oid]
            if to_remove:
                logger.info("OrderStore: pruned %d terminal orders older than %s",
                           len(to_remove), max_age)
            return len(to_remove)

    # ------------------------------------------------------------------
    # Persistence to DB (delegates to existing TradeHistory)
    # ------------------------------------------------------------------

    def persist_to_db(self, order: OrderRecord) -> None:
        """Persist an order to the TradeHistory table."""
        try:
            from app.db.database import SessionLocal
            from app.db.models import TradeHistory

            db = SessionLocal()
            try:
                existing = db.query(TradeHistory).filter(
                    TradeHistory.client_order_id == order.client_order_id
                ).first()

                if existing:
                    existing.status = order.status.value
                    existing.filled_qty = order.filled_qty
                    existing.filled_avg_price = order.filled_avg_price
                    existing.order_id = order.order_id or existing.order_id
                else:
                    trade = TradeHistory(
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        entry_price=order.limit_price or order.filled_avg_price or 0.0,
                        stop_loss=order.stop_loss_price,
                        take_profit=order.take_profit_price,
                        confidence=0.0,
                        order_id=order.order_id,
                        client_order_id=order.client_order_id,
                        status=order.status.value,
                        strategy_id=order.strategy_id,
                        filled_qty=order.filled_qty,
                        filled_avg_price=order.filled_avg_price,
                        signal_details={"via": "order_manager", **order.tags},
                    )
                    db.add(trade)
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.error("OrderStore persist_to_db failed for %s: %s",
                        order.client_order_id, e)

    def load_active_from_db(self) -> int:
        """Load active orders from DB into memory. Returns count loaded."""
        try:
            from app.db.database import SessionLocal
            from app.db.models import TradeHistory

            db = SessionLocal()
            try:
                active_trades = db.query(TradeHistory).filter(
                    TradeHistory.status.in_([s.value for s in ACTIVE_STATES])
                ).all()

                loaded = 0
                for trade in active_trades:
                    order = OrderRecord(
                        order_id=trade.order_id or "",
                        client_order_id=trade.client_order_id or "",
                        symbol=trade.symbol,
                        side=trade.side,
                        order_type="market",
                        time_in_force="gtc",
                        qty=int(trade.qty) if trade.qty else 0,
                        filled_qty=trade.filled_qty or 0,
                        filled_avg_price=trade.filled_avg_price,
                        status=OrderState(trade.status or "submitted"),
                        strategy_id=trade.strategy_id,
                        stop_loss_price=trade.stop_loss,
                        take_profit_price=trade.take_profit,
                    )
                    self.put(order)
                    loaded += 1

                logger.info("OrderStore: loaded %d active orders from DB", loaded)
                return loaded
            finally:
                db.close()
        except Exception as e:
            logger.error("OrderStore load_active_from_db failed: %s", e)
            return 0

    # ------------------------------------------------------------------
    # Snapshot / debug
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a summary snapshot for monitoring."""
        with self._lock:
            return {
                "total_orders": len(self._by_order_id),
                "active": self.count_active(),
                "by_status": {
                    status.value: len(self._by_status.get(status, set()))
                    for status in OrderState
                },
                "by_strategy": {
                    sid: len(oids)
                    for sid, oids in self._by_strategy.items()
                },
            }

    def __len__(self) -> int:
        return len(self._by_order_id)

    def __contains__(self, order_id: str) -> bool:
        return order_id in self._by_order_id


# ---------------------------------------------------------------------------
# Global singleton for the trading engine
# ---------------------------------------------------------------------------

_global_store: Optional[OrderStore] = None
_store_lock = threading.Lock()


def get_order_store() -> OrderStore:
    """Get or create the global OrderStore singleton."""
    global _global_store
    if _global_store is None:
        with _store_lock:
            if _global_store is None:
                _global_store = OrderStore()
                # load active orders from DB at startup
                _global_store.load_active_from_db()
    return _global_store


__all__ = ["OrderStore", "get_order_store"]
