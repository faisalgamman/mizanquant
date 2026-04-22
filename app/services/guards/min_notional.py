"""Guard enforcing minimum trade notional."""

from __future__ import annotations

from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    if ctx.notional < 50.0:
        return GuardResult(
            "min_notional",
            False,
            True,
            f"Order notional ${ctx.notional:.2f} is below $50 minimum.",
            "MIN_NOTIONAL",
        )
    return GuardResult("min_notional", True, True, "Order notional above minimum.", "MIN_NOTIONAL_OK")
