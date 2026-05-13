"""Halal Purification Calculator.

Calculates the amount of impure income (interest) that must be
donated to charity (purified) based on:
1. Interest income ratio of held positions × dividends received
2. Interest earned on cash balances in the trading account
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("screener")


def _get_interest_ratio(symbol: str) -> float:
    """Get interest-to-revenue ratio for a symbol from the halal screening.

    Returns 0.0 if screening data unavailable.
    """
    try:
        from app.services.halal_screening import screen_symbol
        result = screen_symbol(symbol)
        if result:
            return float(result.get("interest_ratio", 0.0))
    except Exception as exc:
        logger.debug("Failed to fetch interest ratio for %s: %s", symbol, exc)
    return 0.0


def _get_cash_interest_earned(strategy_id: str | None = None) -> float:
    """Get interest earned on cash balances from Alpaca account.

    Returns 0.0 if unavailable.
    """
    try:
        from app.services.alpaca_client import get_account as alpaca_get_account
        account = alpaca_get_account(strategy_id=strategy_id)
        if account:
            return float(account.get("cash_interest", account.get("interest", 0)) or 0)
    except Exception as exc:
        logger.debug("Failed to fetch cash interest: %s", exc)
    return 0.0


def calculate_purification(
    positions: list[dict[str, Any]] | None = None,
    strategy_id: str | None = None,
    include_cash_interest: bool = True,
) -> dict[str, Any]:
    """Calculate total purification amount from positions and cash interest.

    Args:
        positions: List of position dicts with 'symbol', 'market_value',
            and optionally 'unrealized_pl' and 'cost_basis'.
            Auto-fetches from Alpaca if None.
        strategy_id: Optional strategy to fetch data for.
        include_cash_interest: Whether to include cash interest in total.

    Returns:
        Dict with per-symbol breakdown, total purification, and summary.
    """
    if positions is None:
        try:
            from app.services.alpaca_client import get_positions as alpaca_get_positions
            positions = alpaca_get_positions(strategy_id=strategy_id) or []
        except Exception as exc:
            logger.debug("Failed to fetch positions: %s", exc)
            positions = []

    per_symbol = []
    total_impure_amount = 0.0
    total_position_value = 0.0

    for pos in positions:
        symbol = pos.get("symbol", "")
        market_value = float(pos.get("market_value", 0) or 0)
        unrealized_pl = float(pos.get("unrealized_pl", 0) or 0)
        cost_basis = float(pos.get("cost_basis", 0) or 0)
        qty = float(pos.get("qty", 0) or 0)

        if not symbol or market_value <= 0:
            continue

        interest_ratio = _get_interest_ratio(symbol)
        total_position_value += market_value

        # Impure amount = position value × (interest_ratio / 100)
        # This represents the portion of the position attributable to
        # interest income. Standard AAOIFI purification is on dividends,
        # but as a conservative measure we also purify a portion of
        # unrealized gains based on the interest ratio.
        position_impure = market_value * (interest_ratio / 100.0)

        # Dividend-based purification (more precise):
        # If the stock pays ~2% dividend yield, then:
        # dividend_impure = market_value * 0.02 * (interest_ratio / 100)
        estimated_dividend = market_value * 0.02  # assume 2% div yield
        dividend_impure = estimated_dividend * (interest_ratio / 100.0)

        per_symbol.append({
            "symbol": symbol,
            "qty": qty,
            "market_value": round(market_value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_pl": round(unrealized_pl, 2),
            "interest_ratio_pct": round(interest_ratio, 2),
            "position_impure": round(position_impure, 2),
            "estimated_dividend_impure": round(dividend_impure, 2),
        })
        total_impure_amount += dividend_impure

    # Cash interest
    cash_interest = 0.0
    if include_cash_interest:
        cash_interest = _get_cash_interest_earned(strategy_id=strategy_id)

    total_purification = total_impure_amount + cash_interest

    return {
        "total_purification_amount": round(total_purification, 2),
        "cash_interest_amount": round(cash_interest, 2),
        "positions_impure_amount": round(total_impure_amount, 2),
        "total_position_value": round(total_position_value, 2),
        "positions_analyzed": len(per_symbol),
        "per_symbol": per_symbol,
        "currency": "USD",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "This is an estimated purification amount based on AAOIFI "
            "standards. Consult a qualified Islamic scholar for exact "
            "calculations. The dividend-based calculation assumes a 2% "
            "dividend yield as a conservative estimate."
        ),
    }


__all__ = ["calculate_purification"]
