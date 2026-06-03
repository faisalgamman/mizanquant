"""Central Order Manager — orchestrates order lifecycle.

The OrderManager is the single entry point for all order operations.
It coordinates:
  - Validation (order_validator)
  - Storage (order_store)
  - Broker submission (broker adapter)
  - State transitions
  - Smart routing (for large orders)

Strategy-specific instances are created by `get_order_manager(strategy_id)`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from app.services.broker.factory import get_broker
from app.services.order_state import (
    OrderRecord,
    OrderState,
    ACTIVE_STATES,
)
from app.services.order_store import OrderStore, get_order_store
from app.services.order_validator import (
    ValidationResult,
    validate_order as _validate_order,
    fast_validate as _fast_validate,
)

logger = logging.getLogger("screener")


class OrderManager:
    """Manages the full lifecycle of orders for a strategy.

    Usage:
        mgr = OrderManager(strategy_id="A")
        order = mgr.create_order(symbol="AAPL", side="buy", qty=10, ...)
        result = mgr.submit(order)
        mgr.cancel(order_id=result.order_id)
        active = mgr.get_active_orders()
    """

    def __init__(
        self,
        strategy_id: Optional[str] = None,
        store: Optional[OrderStore] = None,
    ) -> None:
        self.strategy_id = strategy_id
        self.store = store if store is not None else get_order_store()
        self._broker = get_broker(strategy_id=strategy_id)

    # ------------------------------------------------------------------
    # Order Creation
    # ------------------------------------------------------------------

    def create_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str = "market",
        time_in_force: str = "gtc",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        order_class: str = "simple",
        trail_percent: Optional[float] = None,
        client_order_id: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> OrderRecord:
        """Create a new OrderRecord. Does NOT submit — use submit() for that.

        Returns the created order with status=CREATED.
        """
        coid = client_order_id or self._generate_coid(symbol)
        now = datetime.now(timezone.utc)

        order = OrderRecord(
            order_id="",                    # Assigned by broker on submission
            client_order_id=coid,
            symbol=symbol.upper().strip(),
            side=side.lower().strip(),
            order_type=order_type.lower().strip(),
            time_in_force=time_in_force.lower().strip(),
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            status=OrderState.CREATED,
            order_class=order_class,
            strategy_id=self.strategy_id,
            trail_percent=trail_percent,
            tags=tags or {},
            created_at=now,
            updated_at=now,
        )

        # Validate the order BEFORE storing it
        result = _fast_validate(order)
        if not result.passed:
            logger.warning(
                "Order validation failed at creation: %s", result.reason()
            )

        self.store.put(order)
        logger.info(
            "OrderManager[%s]: created %s %s x%d (coid=%s)",
            self.strategy_id, side, symbol, qty, coid,
        )
        return order

    # ------------------------------------------------------------------
    # Order Submission
    # ------------------------------------------------------------------

    def submit(self, order: OrderRecord) -> dict:
        """Submit an order to the broker.

        Returns:
            dict with keys:
                success: bool
                order_id: str (broker ID)
                client_order_id: str
                status: str (new order state)
                reason: str
                order: OrderRecord (updated)
        """
        # 1. Validate
        result = _validate_order(order, store=self.store)
        if not result.passed:
            return self._fail(order, reason=f"VALIDATION_FAILED: {result.reason()}")

        # 2. Check state
        if order.status != OrderState.CREATED:
            return self._fail(order, reason=f"ORDER_NOT_CREATED: {order.status.value}")

        # 3. Build broker payload
        payload = self._to_broker_payload(order)

        # 4. Submit via broker adapter
        try:
            broker_response = self._broker.submit_order(payload, strategy_id=self.strategy_id)
        except Exception as e:
            logger.error("OrderManager[%s]: submission error: %s", self.strategy_id, e)
            order.reject_reason = str(e)
            self.store.transition(order.order_id or order.client_order_id, OrderState.REJECTED)
            order.reject_reason = f"BROKER_ERROR: {str(e)[:200]}"
            return self._fail(order, reason=f"BROKER_ERROR: {e}")

        # 5. Handle broker response
        if not broker_response:
            order.reject_reason = "Broker returned empty response"
            self.store.transition(
                order.order_id or f"__coid__{order.client_order_id}",
                OrderState.REJECTED,
            )
            return self._fail(order, reason="Broker returned empty response")

        broker_id = broker_response.get("id", "")
        broker_status = broker_response.get("status", "submitted")

        # Update order with broker info
        if broker_id:
            # Re-key in store from coid key to real order_id
            self.store.remove(f"__coid__{order.client_order_id}")
            order.order_id = broker_id
            order.status = OrderState(broker_status) if broker_status in OrderState._value2member_map_ else OrderState.SUBMITTED
            self.store.put(order)
        else:
            order.status = OrderState.REJECTED
            order.reject_reason = "Broker returned no order ID"

        # Transition state
        self.store.transition(order.order_id, order.status)

        # Persist to DB
        self.store.persist_to_db(order)

        logger.info(
            "OrderManager[%s]: submitted %s %s x%d → %s (id=%s)",
            self.strategy_id, order.side, order.symbol, order.qty,
            order.status.value, order.order_id,
        )

        return {
            "success": order.status not in (OrderState.REJECTED,),
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "status": order.status.value,
            "reason": "OK" if order.status != OrderState.REJECTED else order.reject_reason,
            "order": order,
        }

    def submit_bracket(
        self,
        symbol: str,
        side: str,
        qty: int,
        limit_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        time_in_force: str = "gtc",
        trail_percent: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """Create and submit a bracket order (parent + stop-loss + take-profit legs).

        Returns:
            dict with parent result + leg info
        """
        # Create parent order
        parent = self.create_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="limit",
            time_in_force=time_in_force,
            limit_price=limit_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            order_class="bracket",
            trail_percent=trail_percent,
            client_order_id=client_order_id,
        )

        result = self.submit(parent)
        result["bracket"] = {
            "take_profit": take_profit_price,
            "stop_loss": stop_loss_price,
        }
        return result

    # ------------------------------------------------------------------
    # Order Cancellation
    # ------------------------------------------------------------------

    def cancel(self, order_id: str) -> dict:
        """Cancel an active order by its order_id or client_order_id."""
        order = self.store.get_by_order_id(order_id)
        if order is None:
            order = self.store.get_by_client_order_id(order_id)
        if order is None:
            logger.warning("OrderManager[%s]: cancel failed — order not found: %s",
                          self.strategy_id, order_id)
            return {"success": False, "reason": f"ORDER_NOT_FOUND: {order_id}"}

        if not order.is_cancelable:
            return {
                "success": False,
                "reason": f"ORDER_NOT_CANCELABLE: {order.status.value}",
                "order": order,
            }

        try:
            broker_ok = self._broker.cancel_order(
                order.order_id or order.client_order_id,
                strategy_id=self.strategy_id,
            )
        except Exception as e:
            logger.error("OrderManager[%s]: cancel broker error: %s", self.strategy_id, e)
            broker_ok = False

        if broker_ok:
            self.store.transition(order.order_id, OrderState.CANCELED)
            self.store.persist_to_db(order)
            logger.info("OrderManager[%s]: canceled %s %s (id=%s)",
                       self.strategy_id, order.symbol, order.side, order.order_id)
            return {
                "success": True,
                "reason": "OK",
                "order": order,
            }
        else:
            # Broker failed to cancel — status remains unchanged
            return {
                "success": False,
                "reason": "BROKER_CANCEL_FAILED",
                "order": order,
            }

    def cancel_all(self) -> int:
        """Cancel all active orders for this strategy. Returns count."""
        active = self.get_active_orders()
        count = 0
        for order in active:
            result = self.cancel(order.order_id)
            if result["success"]:
                count += 1
        return count

    def cancel_by_symbol(self, symbol: str) -> int:
        """Cancel all active orders for a symbol. Returns count."""
        active = self.store.get_active_by_symbol(symbol)
        count = 0
        for order in active:
            if order.strategy_id == self.strategy_id:
                result = self.cancel(order.order_id)
                if result["success"]:
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Order Modification (price, qty)
    # ------------------------------------------------------------------

    def modify(
        self,
        order_id: str,
        qty: Optional[int] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> dict:
        """Replace a pending order with a modified one.

        Alpaca/IBKR: cancel + replace with new order.
        """
        order = self.store.get_by_order_id(order_id)
        if order is None:
            order = self.store.get_by_client_order_id(order_id)
        if order is None:
            return {"success": False, "reason": f"ORDER_NOT_FOUND: {order_id}"}

        if not order.is_cancelable:
            return {
                "success": False,
                "reason": f"ORDER_NOT_MODIFIABLE: {order.status.value}",
            }

        # Cancel old
        cancel_result = self.cancel(order.order_id or order.client_order_id)
        if not cancel_result["success"]:
            return {"success": False, "reason": f"CANCEL_FAILED: {cancel_result['reason']}"}

        # Verify cancel: re-check broker to guard against fill-during-cancel race
        try:
            broker_orders = self._broker.get_orders(
                status="all", limit=50, strategy_id=self.strategy_id
            )
            oid = order.order_id or ""
            coid = order.client_order_id or ""
            for bo in broker_orders:
                if bo["id"] == oid or bo["client_order_id"] == coid:
                    if bo["status"] in ("filled", "partially_filled"):
                        return {
                            "success": False,
                            "reason": (
                                f"Order filled during cancel "
                                f"({bo['filled_qty']} shares) — replacement blocked"
                            ),
                        }
                    break
        except Exception:
            pass  # broker check is best-effort; proceed below

        # Create replacement with new values
        replacement = self.create_order(
            symbol=order.symbol,
            side=order.side,
            qty=qty if qty is not None else order.qty,
            order_type=order.order_type,
            time_in_force=order.time_in_force,
            limit_price=limit_price if limit_price is not None else order.limit_price,
            stop_price=stop_price if stop_price is not None else order.stop_price,
            stop_loss_price=order.stop_loss_price,
            take_profit_price=order.take_profit_price,
            order_class=order.order_class,
            trail_percent=order.trail_percent,
            tags={**order.tags, "modified_from": order.client_order_id},
        )

        submit_result = self.submit(replacement)
        return {
            "success": submit_result["success"],
            "reason": submit_result["reason"],
            "old_order_id": order.order_id,
            "new_order": replacement,
            "new_result": submit_result,
        }

    # ------------------------------------------------------------------
    # Fill handling
    # ------------------------------------------------------------------

    def apply_fill(
        self,
        order_id: str,
        filled_qty: int,
        filled_avg_price: float,
    ) -> Optional[OrderRecord]:
        """Apply a fill event to an order, transitioning state if fully filled."""
        order = self.store.get_by_order_id(order_id)
        if order is None:
            order = self.store.get_by_client_order_id(order_id)
        if order is None:
            logger.warning("OrderManager[%s]: fill for unknown order %s",
                          self.strategy_id, order_id)
            return None

        order.filled_qty = filled_qty
        order.filled_avg_price = filled_avg_price

        new_status = order.status  # default: unchanged

        if filled_qty >= order.qty:
            new_status = OrderState.FILLED
        elif filled_qty > 0:
            new_status = OrderState.PARTIALLY_FILLED

        if new_status != order.status:
            self.store.transition(order.order_id, new_status)

        self.store.put(order)
        self.store.persist_to_db(order)

        logger.info(
            "OrderManager[%s]: fill %s %s %d/%d @ %.2f → %s",
            self.strategy_id, order.symbol, order.side,
            filled_qty, order.qty, filled_avg_price,
            order.status.value,
        )
        return order

    # ------------------------------------------------------------------
    # Order rejection / error handling
    # ------------------------------------------------------------------

    def reject(self, order_id: str, reason: str) -> Optional[OrderRecord]:
        """Mark an order as rejected with a reason."""
        order = self.store.get_by_order_id(order_id)
        if order is None:
            order = self.store.get_by_client_order_id(order_id)
        if order is None:
            return None

        order.reject_reason = reason
        success = self.store.transition(order.order_id, OrderState.REJECTED)
        if success:
            self.store.persist_to_db(order)
        logger.warning("OrderManager[%s]: rejected %s %s: %s",
                      self.strategy_id, order.symbol, order.order_id, reason)
        return order

    def expire(self, order_id: str) -> Optional[OrderRecord]:
        """Mark an order as expired (time-in-force elapsed)."""
        order = self.store.get_by_order_id(order_id)
        if order is None:
            return None

        if order.status in {OrderState.SUBMITTED, OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED}:
            self.store.transition(order.order_id, OrderState.EXPIRED)
            self.store.persist_to_db(order)
            logger.info("OrderManager[%s]: expired %s %s",
                       self.strategy_id, order.symbol, order.order_id)
        return order

    # ------------------------------------------------------------------
    # Order queries
    # ------------------------------------------------------------------

    def get_order(self, order_id: str) -> Optional[OrderRecord]:
        """Get a single order by ID or client_order_id."""
        order = self.store.get_by_order_id(order_id)
        if order is None:
            order = self.store.get_by_client_order_id(order_id)
        return order

    def get_active_orders(self) -> list[OrderRecord]:
        """Get all active orders for this strategy."""
        return self.store.get_active_by_strategy(self.strategy_id or "")

    def get_orders_by_symbol(self, symbol: str) -> list[OrderRecord]:
        """Get all orders for a symbol (filtered by strategy)."""
        all_orders = self.store.get_by_symbol(symbol)
        return [o for o in all_orders if o.strategy_id == self.strategy_id]

    def find_orders(self, **filters) -> list[OrderRecord]:
        """Flexible query."""
        if self.strategy_id:
            filters.setdefault("strategy_id", self.strategy_id)
        return self.store.find(**filters)

    def count_active(self) -> int:
        """Count active orders for this strategy."""
        return len(self.get_active_orders())

    # ------------------------------------------------------------------
    # Batch Operations
    # ------------------------------------------------------------------

    def batch_submit(self, orders: list[OrderRecord]) -> list[dict]:
        """Submit multiple orders and return results."""
        return [self.submit(order) for order in orders]

    def batch_cancel(self, order_ids: list[str]) -> list[dict]:
        """Cancel multiple orders by ID."""
        return [self.cancel(oid) for oid in order_ids]

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def reconcile(self) -> dict:
        """Reconcile local state with broker state.

        Updates order statuses and detects orphans.
        Returns reconciliation summary.
        """
        summary = {
            "strategy": self.strategy_id,
            "broker_orders": 0,
            "local_active": 0,
            "updated": 0,
            "orphans_local": [],   # Orders we have that broker doesn't know
            "orphans_broker": [],  # Orders broker has that we don't know
            "errors": [],
        }

        try:
            broker_orders = self._broker.get_orders(
                status="all",
                limit=200,
                strategy_id=self.strategy_id,
            )
        except Exception as e:
            summary["errors"].append(f"broker_fetch: {e}")
            return summary

        summary["broker_orders"] = len(broker_orders)
        broker_by_id: dict[str, dict] = {
            o.get("id"): o for o in broker_orders if o.get("id")
        }
        broker_by_coid: dict[str, dict] = {
            o.get("client_order_id"): o
            for o in broker_orders
            if o.get("client_order_id")
        }

        local_active = self.get_active_orders()
        summary["local_active"] = len(local_active)

        # Update local orders from broker data
        for order in local_active:
            broker = broker_by_id.get(order.order_id)
            if broker is None and order.client_order_id:
                broker = broker_by_coid.get(order.client_order_id)

            if broker:
                broker_status = broker.get("status", "")
                try:
                    bs = OrderState(broker_status)
                except ValueError:
                    bs = None

                if bs and bs != order.status:
                    self.store.transition(order.order_id, bs)
                    summary["updated"] += 1

                # Update fill data
                if broker.get("filled_qty") not in (None, ""):
                    filled_qty = int(float(broker["filled_qty"]))
                    filled_avg = (
                        float(broker["filled_avg_price"])
                        if broker.get("filled_avg_price") not in (None, "")
                        else None
                    )
                    if filled_avg is not None:
                        order.filled_qty = filled_qty
                        order.filled_avg_price = filled_avg
                        self.store.put(order)
            else:
                summary["orphans_local"].append(order.symbol)

        # Detect broker-side orders not in local store
        local_ids = {o.order_id for o in local_active}
        for oid in broker_by_id:
            if oid not in local_ids:
                bo = broker_by_id[oid]
                summary["orphans_broker"].append(bo.get("symbol", "?"))

        # Persist updated orders
        for order in local_active:
            self.store.persist_to_db(order)

        logger.info(
            "OrderManager[%s] reconcile: broker=%d local=%d updated=%d orphans_local=%d orphans_broker=%d",
            self.strategy_id, summary["broker_orders"], summary["local_active"],
            summary["updated"], len(summary["orphans_local"]), len(summary["orphans_broker"]),
        )
        return summary

    # ------------------------------------------------------------------
    # Timeout / stale order detection
    # ------------------------------------------------------------------

    def expire_stale(self, max_age_seconds: int = 300) -> int:
        """Auto-expire orders in SUBMITTED state older than max_age_seconds.

        Returns count of orders expired.
        """
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        count = 0
        for order in self.get_active_orders():
            if order.status in {OrderState.SUBMITTED, OrderState.ACKNOWLEDGED}:
                if order.created_at < cutoff:
                    self.expire(order.order_id)
                    count += 1
        if count:
            logger.info("OrderManager[%s]: expired %d stale orders", self.strategy_id, count)
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_coid(self, symbol: str) -> str:
        """Generate a unique client_order_id."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        uid = uuid.uuid4().hex[:8]
        strat = self.strategy_id or "D"
        return f"{strat}-{symbol}-{ts[:14]}-{uid}"

    def _to_broker_payload(self, order: OrderRecord) -> dict:
        """Convert an OrderRecord to a broker-submission payload."""
        payload: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side,
            "qty": str(order.qty),
            "type": order.order_type,
            "time_in_force": order.time_in_force,
            "client_order_id": order.client_order_id,
            "order_class": order.order_class,
        }

        if order.limit_price is not None:
            payload["limit_price"] = str(order.limit_price)
        if order.stop_price is not None:
            payload["stop_price"] = str(order.stop_price)
        if order.trail_percent is not None:
            payload["trail_percent"] = str(order.trail_percent)
        if order.stop_loss_price is not None:
            payload["stop_loss"] = {"stop_price": str(order.stop_loss_price)}
        if order.take_profit_price is not None:
            payload["take_profit"] = {"limit_price": str(order.take_profit_price)}

        return payload

    @staticmethod
    def _fail(order: OrderRecord, reason: str) -> dict:
        return {
            "success": False,
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "status": order.status.value,
            "reason": reason,
            "order": order,
        }


# ---------------------------------------------------------------------------
# Global manager access
# ---------------------------------------------------------------------------

_managers: dict[str, OrderManager] = {}


def get_order_manager(strategy_id: Optional[str] = None) -> OrderManager:
    """Get or create an OrderManager for a strategy."""
    key = strategy_id or "_default"
    if key not in _managers:
        _managers[key] = OrderManager(strategy_id=strategy_id)
    return _managers[key]


__all__ = ["OrderManager", "get_order_manager"]
