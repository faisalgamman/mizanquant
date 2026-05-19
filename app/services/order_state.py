"""Order state machine for the Order Management Layer.

Defines a formal lifecycle for every order, with valid transitions
enforced at every step. Inspired by FIX protocol order states but
simplified for the paper-trading broker abstraction.

Lifecycle:
  CREATED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
                    ↘ REJECTED
                    ↘ CANCELED
     SUBMITTED → CANCELED
     ACKNOWLEDGED → CANCELED
     PARTIALLY_FILLED → FILLED
     PARTIALLY_FILLED → CANCELED
     SUBMITTED → EXPIRED
     ACKNOWLEDGED → EXPIRED
     PARTIALLY_FILLED → EXPIRED
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class OrderState(str, enum.Enum):
    """Order lifecycle states — FIX-inspired, paper-trading subset."""

    CREATED = "created"                # Pending submission
    SUBMITTED = "submitted"            # Sent to broker, awaiting ack
    ACKNOWLEDGED = "acknowledged"      # Broker accepted
    PARTIALLY_FILLED = "partially_filled"  # Some quantity filled
    FILLED = "filled"                  # Fully filled
    CANCELED = "canceled"              # Canceled by user or system
    REJECTED = "rejected"              # Rejected by broker or validation
    EXPIRED = "expired"                # Time-in-force expired


# Valid transitions: source -> set of allowed targets
_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.CREATED:           {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.CANCELED},
    OrderState.SUBMITTED:         {OrderState.ACKNOWLEDGED, OrderState.REJECTED,
                                    OrderState.CANCELED, OrderState.EXPIRED,
                                    OrderState.PARTIALLY_FILLED, OrderState.FILLED},
    OrderState.ACKNOWLEDGED:      {OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                                    OrderState.CANCELED, OrderState.EXPIRED},
    OrderState.PARTIALLY_FILLED:  {OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED,
                                    OrderState.PARTIALLY_FILLED},  # self-loop for additional fills
    OrderState.FILLED:            set(),   # terminal
    OrderState.CANCELED:          set(),   # terminal
    OrderState.REJECTED:          set(),   # terminal
    OrderState.EXPIRED:           set(),   # terminal
}

# States considered "active" (order is live at broker)
ACTIVE_STATES: frozenset[OrderState] = frozenset({
    OrderState.SUBMITTED,
    OrderState.ACKNOWLEDGED,
    OrderState.PARTIALLY_FILLED,
})

# States considered "terminal" (no further transitions possible)
TERMINAL_STATES: frozenset[OrderState] = frozenset({
    OrderState.FILLED,
    OrderState.CANCELED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
})

# States where we can cancel
CANCELABLE_STATES: frozenset[OrderState] = frozenset({
    OrderState.SUBMITTED,
    OrderState.ACKNOWLEDGED,
    OrderState.PARTIALLY_FILLED,
})


def can_transition(from_state: OrderState, to_state: OrderState) -> bool:
    """Check if a state transition is valid."""
    return to_state in _TRANSITIONS.get(from_state, set())


def assert_transition(from_state: OrderState, to_state: OrderState) -> None:
    """Raise ValueError if the transition is invalid."""
    if not can_transition(from_state, to_state):
        raise ValueError(
            f"Invalid order state transition: {from_state.value} -> {to_state.value}"
        )


def allowed_transitions(state: OrderState) -> set[OrderState]:
    """Return the set of valid target states from the given state."""
    return _TRANSITIONS.get(state, set())


def is_active(state: OrderState) -> bool:
    """True if the order is live at the broker."""
    return state in ACTIVE_STATES


def is_terminal(state: OrderState) -> bool:
    """True if the order has reached a terminal state."""
    return state in TERMINAL_STATES


def is_cancelable(state: OrderState) -> bool:
    """True if the order can still be canceled."""
    return state in CANCELABLE_STATES


# ---------------------------------------------------------------------------
# Order record — plain data object shared across the layer
# ---------------------------------------------------------------------------

@dataclass
class OrderRecord:
    """Normalised order representation used throughout the Order Management Layer.

    This is the canonical record — all services in this layer read/write
    this shape. Adapters translate to/from broker-specific formats.
    """

    order_id: str                            # Broker-assigned ID (empty until ack'd)
    client_order_id: str                     # Our generated COID
    symbol: str
    side: str                                # "buy" or "sell"
    order_type: str                          # "market", "limit", "stop", "stop_limit"
    time_in_force: str                       # "day", "gtc", "ioc", "fok"
    qty: int                                 # Ordered quantity
    filled_qty: int = 0                      # Filled so far
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    filled_avg_price: Optional[float] = None
    status: OrderState = OrderState.CREATED
    order_class: str = "simple"              # "simple", "bracket", "oco"
    strategy_id: Optional[str] = None
    account_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    trail_percent: Optional[float] = None
    trail_price: Optional[float] = None
    # Bracket-order fields
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    # Parent/child relationship for bracket and OCO legs
    parent_order_id: Optional[str] = None
    leg_type: Optional[str] = None           # None, "stop_loss", "take_profit"
    # Additional metadata
    tags: dict = field(default_factory=dict)

    @property
    def is_buy(self) -> bool:
        return self.side.lower() == "buy"

    @property
    def is_sell(self) -> bool:
        return self.side.lower() == "sell"

    @property
    def is_active(self) -> bool:
        return is_active(self.status)

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self.status)

    @property
    def is_cancelable(self) -> bool:
        return is_cancelable(self.status)

    @property
    def remaining_qty(self) -> int:
        return max(0, self.qty - self.filled_qty)

    @property
    def fill_ratio(self) -> float:
        if self.qty <= 0:
            return 0.0
        return min(1.0, self.filled_qty / self.qty)

    def transition_to(self, new_state: OrderState) -> None:
        """Transition this order to a new state with validation."""
        assert_transition(self.status, new_state)
        self.status = new_state
        self.updated_at = datetime.now(timezone.utc)
        if new_state == OrderState.SUBMITTED:
            self.submitted_at = datetime.now(timezone.utc)
        elif new_state == OrderState.FILLED:
            self.filled_at = datetime.now(timezone.utc)
        elif new_state == OrderState.CANCELED:
            self.canceled_at = datetime.now(timezone.utc)
        elif new_state == OrderState.REJECTED:
            self.rejected_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        """Serialize to a dict for JSON APIs / broker responses."""
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "qty": self.qty,
            "filled_qty": self.filled_qty,
            "remaining_qty": self.remaining_qty,
            "fill_ratio": round(self.fill_ratio, 4),
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "filled_avg_price": self.filled_avg_price,
            "status": self.status.value,
            "order_class": self.order_class,
            "strategy_id": self.strategy_id,
            "account_id": self.account_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "reject_reason": self.reject_reason,
            "take_profit_price": self.take_profit_price,
            "stop_loss_price": self.stop_loss_price,
            "parent_order_id": self.parent_order_id,
            "leg_type": self.leg_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OrderRecord":
        """Deserialize from dict."""
        status_raw = data.get("status", "created")
        if isinstance(status_raw, OrderState):
            status = status_raw
        else:
            status = OrderState(str(status_raw))

        return cls(
            order_id=str(data.get("order_id") or ""),
            client_order_id=str(data.get("client_order_id") or ""),
            symbol=str(data.get("symbol") or ""),
            side=str(data.get("side") or "buy"),
            order_type=str(data.get("order_type") or "market"),
            time_in_force=str(data.get("time_in_force") or "day"),
            qty=int(data.get("qty") or 0),
            filled_qty=int(data.get("filled_qty") or 0),
            limit_price=float(data["limit_price"]) if data.get("limit_price") not in (None, "") else None,
            stop_price=float(data["stop_price"]) if data.get("stop_price") not in (None, "") else None,
            filled_avg_price=float(data["filled_avg_price"]) if data.get("filled_avg_price") not in (None, "") else None,
            status=status,
            order_class=str(data.get("order_class") or "simple"),
            strategy_id=data.get("strategy_id"),
            account_id=data.get("account_id"),
            reject_reason=data.get("reject_reason"),
            take_profit_price=float(data["take_profit_price"]) if data.get("take_profit_price") not in (None, "") else None,
            stop_loss_price=float(data["stop_loss_price"]) if data.get("stop_loss_price") not in (None, "") else None,
            parent_order_id=data.get("parent_order_id"),
            leg_type=data.get("leg_type"),
            tags=data.get("tags") or {},
        )


__all__ = [
    "OrderState",
    "OrderRecord",
    "can_transition",
    "assert_transition",
    "allowed_transitions",
    "is_active",
    "is_terminal",
    "is_cancelable",
    "ACTIVE_STATES",
    "TERMINAL_STATES",
    "CANCELABLE_STATES",
]
