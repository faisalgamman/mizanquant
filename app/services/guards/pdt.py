"""Guard enforcing PDT safety buffer."""

from __future__ import annotations

from app.services.guards.base import GuardContext, GuardResult
from app.services.pdt_tracker import MAX_DAY_TRADES, can_day_trade, get_day_trade_count


def guard(ctx: GuardContext) -> GuardResult:
    used = get_day_trade_count()
    if not can_day_trade():
        return GuardResult(
            "pdt",
            False,
            True,
            f"Day-trade limit reached ({used}/{MAX_DAY_TRADES}).",
            "PDT_LIMIT",
        )
    return GuardResult("pdt", True, True, f"Day-trade buffer available ({used}/{MAX_DAY_TRADES}).", "PDT_OK")
