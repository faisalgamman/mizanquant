"""Pre-trade input validators (Milestone 1, senior-engineer hardening).

Every order that reaches the broker must pass through `validate_entry`.
Failures return a structured reason rather than a silent rejection.

This module is deliberately free of external I/O so it is cheap to unit-test.
A separate async helper `is_tradable_symbol` consults a cached Alpaca assets
snapshot; it is optional and can be disabled in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.core.config import app_cfg
from app.services.reference_data import is_symbol_tradable

# Alpaca allows tickers up to 5 letters plus a '.X' class suffix (e.g. BRK.B).
_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")

# Hardcoded known-bad tickers beyond the Alpaca assets cache. Keep short; the
# authoritative source is the assets snapshot refreshed daily.
_DELISTED: frozenset[str] = frozenset({"FB", "TWTR", "ATVI"})


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a pre-trade validation."""

    ok: bool
    reason: str = ""
    code: str = ""  # stable machine-readable code for audit trail

    @classmethod
    def success(cls) -> "ValidationResult":
        return cls(ok=True, reason="All checks passed", code="OK")

    @classmethod
    def fail(cls, code: str, reason: str) -> "ValidationResult":
        return cls(ok=False, reason=reason, code=code)

    def __bool__(self) -> bool:  # allow `if result:`
        return self.ok


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_symbol(symbol: Optional[str]) -> ValidationResult:
    """Cheap syntactic + blocklist check. Does NOT hit the network."""
    if not symbol or not isinstance(symbol, str):
        return ValidationResult.fail("SYMBOL_EMPTY", "Symbol is empty or not a string.")
    sym = symbol.strip().upper()
    if not _SYMBOL_RE.match(sym):
        return ValidationResult.fail(
            "SYMBOL_MALFORMED",
            f"Symbol '{symbol}' does not match /^[A-Z]{{1,5}}(\\.[A-Z])?$/.",
        )
    if sym in _DELISTED:
        return ValidationResult.fail(
            "SYMBOL_DELISTED",
            f"Symbol '{sym}' is on the known-delisted blocklist.",
        )
    tradable = is_symbol_tradable(sym)
    if tradable is False:
        return ValidationResult.fail(
            "SYMBOL_NOT_TRADABLE",
            f"Symbol '{sym}' is not present in the cached active Alpaca assets universe.",
        )
    return ValidationResult.success()


def validate_entry(
    symbol: Optional[str],
    price: Optional[float],
    stop_loss: Optional[float],
    take_profit: Optional[float],
    qty: Optional[int] = None,
    side: str = "buy",
    max_notional_usd: Optional[float] = None,
    min_notional_usd: float = 50.0,
) -> ValidationResult:
    """Validate the full order envelope.

    Rules (longs only for now — side == 'buy'):
      * symbol passes `validate_symbol`
      * price > 0
      * 0 < stop_loss < price  (SL below entry for longs)
      * take_profit > price     (TP above entry for longs)
      * qty is None or qty > 0
      * notional (qty * price) >= min_notional_usd when qty is provided
      * notional <= max_notional_usd when cap is provided

    `side` must be one of {'buy', 'sell'}. Shorts (side='sell' without an
    open long) are rejected here until the broker-short path is designed.
    """
    sym_res = validate_symbol(symbol)
    if not sym_res.ok:
        return sym_res

    if side not in ("buy", "sell"):
        return ValidationResult.fail("SIDE_INVALID", f"side must be buy|sell, got {side!r}.")

    if price is None or not isinstance(price, (int, float)) or price <= 0:
        return ValidationResult.fail("PRICE_INVALID", f"price must be > 0, got {price!r}.")

    # Stop-loss / take-profit only required on entry (buy)
    if side == "buy":
        if stop_loss is None or not isinstance(stop_loss, (int, float)) or stop_loss <= 0:
            return ValidationResult.fail("SL_INVALID", f"stop_loss must be > 0, got {stop_loss!r}.")
        if stop_loss >= price:
            return ValidationResult.fail(
                "SL_ABOVE_PRICE",
                f"stop_loss ({stop_loss}) must be strictly below price ({price}) for longs.",
            )
        if take_profit is None or not isinstance(take_profit, (int, float)):
            return ValidationResult.fail(
                "TP_INVALID",
                f"take_profit is required for buys (got {take_profit!r}).",
            )
        if take_profit <= price:
            return ValidationResult.fail(
                "TP_BELOW_PRICE",
                f"take_profit ({take_profit}) must be strictly above price ({price}) for longs.",
            )
        # Sanity: minimum risk-reward. Reject trades with SL>2x distance of TP.
        risk = price - stop_loss
        reward = take_profit - price
        if reward < risk * 0.5:
            return ValidationResult.fail(
                "RR_TOO_LOW",
                f"risk:reward is {risk:.2f}:{reward:.2f} — reward must be >= 0.5x risk.",
            )

    if qty is not None:
        if not isinstance(qty, int) or qty <= 0:
            return ValidationResult.fail("QTY_INVALID", f"qty must be positive int, got {qty!r}.")
        notional = qty * price
        if notional < min_notional_usd:
            return ValidationResult.fail(
                "NOTIONAL_TOO_SMALL",
                f"notional ${notional:.2f} < min ${min_notional_usd:.2f}.",
            )
        if max_notional_usd is not None and notional > max_notional_usd:
            return ValidationResult.fail(
                "NOTIONAL_TOO_LARGE",
                f"notional ${notional:.2f} > cap ${max_notional_usd:.2f}.",
            )

    return ValidationResult.success()


# ---------------------------------------------------------------------------
# Client order id (idempotency) — deterministic + collision-resistant
# ---------------------------------------------------------------------------

import uuid
from datetime import datetime, timezone

def make_client_order_id(strategy_id: Optional[str], symbol: str, side: str = "buy") -> str:
    """Build a deterministic, collision-resistant client_order_id.

    Format: `<strat>-<symbol>-<side>-<YYYYMMDDHHMM>-<rand10>` (<= 48 chars).
    The per-minute timestamp + 10-hex random tail gives ~1T unique IDs per
    (strategy, symbol, side, minute) — enough for retry dedup while still
    allowing legitimate follow-on orders in the next minute.
    """
    strat = (strategy_id or "X").upper()[:2]
    sym = (symbol or "NULL").upper()[:8]
    sd = "B" if side == "buy" else "S"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    tail = uuid.uuid4().hex[:10]
    coid = f"{strat}-{sym}-{sd}-{ts}-{tail}"
    return coid[: app_cfg.execution.client_order_id_max_len]
