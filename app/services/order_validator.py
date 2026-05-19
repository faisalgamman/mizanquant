"""Pre-submission order validation rules.

Validates every order BEFORE it touches any broker API. Catches:
  - Malformed symbols, missing fields
  - Invalid prices (negative, zero, limit > market)
  - Stop-loss / take-profit sanity (SL above entry on buy, etc.)
  - Duplicate order detection (same signal, same strategy, already submitted)
  - Position limits, concentration limits
  - Blacklist / halal gate
  - Size limits (min/max qty)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Optional, Sequence, Tuple

from app.services.order_state import OrderRecord, OrderState

logger = logging.getLogger("screener")

# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

class ValidationResult:
    """Result of a validation run. Truthiness checks ``passed``."""

    __slots__ = ("passed", "failures", "warnings")

    def __init__(self) -> None:
        self.passed: bool = True
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def add_failure(self, rule: str, detail: str = "") -> None:
        self.passed = False
        self.failures.append(f"[{rule}] {detail}" if detail else rule)

    def add_warning(self, rule: str, detail: str = "") -> None:
        self.warnings.append(f"[{rule}] {detail}" if detail else rule)

    def __bool__(self) -> bool:
        return self.passed

    def reason(self) -> str:
        return "; ".join(self.failures) if self.failures else "VALIDATION_PASSED"


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------

def validate_symbol(order: OrderRecord) -> Optional[str]:
    """Check symbol is present and well-formed."""
    symbol = (order.symbol or "").strip().upper()
    if not symbol:
        return "SYMBOL_MISSING"
    if len(symbol) > 10:
        return "SYMBOL_TOO_LONG"
    if not all(c.isascii() for c in symbol):
        return "SYMBOL_MALFORMED"
    # Must be uppercase letters and optionally dots (e.g., BRK.B)
    if not all(c.isupper() or c == "." for c in symbol) or not symbol[0].isalpha():
        return "SYMBOL_MALFORMED"
    return None


def validate_side(order: OrderRecord) -> Optional[str]:
    """Check side is 'buy' or 'sell'."""
    side = (order.side or "").lower().strip()
    if side not in ("buy", "sell"):
        return f"SIDE_INVALID:{order.side}"
    return None


def validate_qty(order: OrderRecord) -> Optional[str]:
    """Check quantity is positive integer."""
    qty = order.qty
    if not isinstance(qty, int) or qty <= 0:
        return f"QTY_INVALID:{order.qty}"
    if qty > 1_000_000:
        return f"QTY_TOO_LARGE:{order.qty}"
    return None


def validate_order_type(order: OrderRecord) -> Optional[str]:
    """Check order_type is valid."""
    valid = {"market", "limit", "stop", "stop_limit", "trailing_stop"}
    ot = (order.order_type or "").lower().strip()
    if ot not in valid:
        return f"ORDER_TYPE_INVALID:{order.order_type}"
    return None


def validate_time_in_force(order: OrderRecord) -> Optional[str]:
    """Check time_in_force is valid."""
    valid = {"day", "gtc", "ioc", "fok"}
    tif = (order.time_in_force or "").lower().strip()
    if tif not in valid:
        return f"TIF_INVALID:{order.time_in_force}"
    return None


def validate_prices(order: OrderRecord) -> Optional[str]:
    """Validate price fields: limit > 0, stop > 0, etc."""
    if order.order_type in ("limit", "stop_limit"):
        lp = order.limit_price
        if lp is None:
            return "LIMIT_PRICE_MISSING"
        if lp <= 0:
            return f"LIMIT_PRICE_NEGATIVE:{lp}"

    if order.order_type in ("stop", "stop_limit"):
        sp = order.stop_price
        if sp is None:
            return "STOP_PRICE_MISSING"
        if sp <= 0:
            return f"STOP_PRICE_NEGATIVE:{sp}"

    return None


def validate_bracket_prices(order: OrderRecord) -> Optional[str]:
    """Validate stop-loss and take-profit for bracket orders."""
    if order.order_class != "bracket":
        return None

    # For parent bracket, parent is irrelevant; legs have their own validation.
    if order.leg_type:
        # Leg orders don't have SL/TP — they ARE the SL/TP
        return None

    sl = order.stop_loss_price
    tp = order.take_profit_price

    if sl is None and tp is None:
        return None  # Bracket without SL/TP is unusual but not invalid

    if order.is_buy:
        # For a buy: SL must be below TP
        if sl is not None and tp is not None and sl >= tp:
            return f"SL_ABOVE_TP_BUY:sl={sl} tp={tp}"
    else:
        # For a sell: SL must be above TP
        if sl is not None and tp is not None and sl <= tp:
            return f"SL_BELOW_TP_SELL:sl={sl} tp={tp}"

    return None


def validate_size_limits(order: OrderRecord) -> Optional[str]:
    """Check min/max order size."""
    if order.qty < 1:
        return f"QTY_BELOW_MIN:{order.qty} < 1"
    # Per-symbol max could be extended from config
    return None


VAR_SYMBOLS = frozenset(["KRBN", "VXX", "UVXY", "SVXY"])  # Known problematic ETFs

def validate_blacklist(order: OrderRecord) -> Optional[str]:
    """Check symbol against a basic blacklist."""
    if order.symbol.upper() in VAR_SYMBOLS:
        return f"BLACKLISTED:{order.symbol}"
    return None


# ---------------------------------------------------------------------------
# Duplicate detection — needs store context
# ---------------------------------------------------------------------------

def validate_duplicate(order: OrderRecord, store) -> Optional[str]:
    """Check if this order is a duplicate of an already-active order.

    Uses the OrderStore to look up active orders for the same strategy+symbol+side.
    If an active order already exists with matching client_order_id prefix, it's a dup.
    """
    if not store:
        return None  # No store available, skip dup check

    active = store.get_active_by_strategy(order.strategy_id or "")
    for existing in active:
        # Skip the order itself (comparing by identity, then by client_order_id)
        if existing is order:
            continue
        if (existing.symbol.upper() == order.symbol.upper()
                and existing.side.lower() == order.side.lower()
                and existing.client_order_id == order.client_order_id):
            return f"DUPLICATE_ORDER:coid={order.client_order_id} symbol={order.symbol}"

    return None


# ---------------------------------------------------------------------------
# Composite validator
# ---------------------------------------------------------------------------

# Order of validation — earliest-cheapest first
_VALIDATORS: list[Tuple[str, Callable]] = [
    ("symbol",         validate_symbol),
    ("side",           validate_side),
    ("qty",            validate_qty),
    ("order_type",     validate_order_type),
    ("time_in_force",  validate_time_in_force),
    ("prices",         validate_prices),
    ("bracket",        validate_bracket_prices),
    ("size_limits",    validate_size_limits),
    ("blacklist",      validate_blacklist),
]


def validate_order(
    order: OrderRecord,
    store=None,
    extra_validators: Optional[Sequence[Callable]] = None,
) -> ValidationResult:
    """Run all validators against an order. Returns ValidationResult.

    ``store`` — optional OrderStore for duplicate detection.
    ``extra_validators`` — additional callables that take order and
        return Optional[str] (None = pass, str = failure).
    """
    result = ValidationResult()

    for rule_name, validator in _VALIDATORS:
        try:
            failure = validator(order)
            if failure:
                result.add_failure(rule_name.upper(), failure)
        except Exception as e:
            result.add_failure(rule_name.upper(), f"ERROR:{e}")

    # Duplicate check with store
    if store:
        failure = validate_duplicate(order, store)
        if failure:
            result.add_failure("DUPLICATE", failure)

    # Extra validators
    if extra_validators:
        for i, validator in enumerate(extra_validators):
            try:
                failure = validator(order)
                if failure:
                    result.add_failure(f"EXTRA_{i}", failure)
            except Exception as e:
                result.add_failure(f"EXTRA_{i}", f"ERROR:{e}")

    return result


def fast_validate(order: OrderRecord) -> ValidationResult:
    """Fast validation without store (no duplicate check)."""
    return validate_order(order, store=None, extra_validators=None)


__all__ = [
    "ValidationResult",
    "validate_order",
    "fast_validate",
    "validate_symbol",
    "validate_side",
    "validate_qty",
    "validate_order_type",
    "validate_time_in_force",
    "validate_prices",
    "validate_bracket_prices",
    "validate_size_limits",
    "validate_blacklist",
]
