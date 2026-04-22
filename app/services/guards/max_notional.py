"""Guard enforcing hard max notional per order."""

from __future__ import annotations

from app.core.config import app_cfg
from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    if ctx.notional > app_cfg.execution.max_notional_per_order_usd:
        return GuardResult(
            "max_notional",
            False,
            True,
            f"Order notional ${ctx.notional:.2f} exceeds cap ${app_cfg.execution.max_notional_per_order_usd:.2f}.",
            "MAX_NOTIONAL",
        )
    return GuardResult("max_notional", True, True, "Order notional within cap.", "MAX_NOTIONAL_OK")
