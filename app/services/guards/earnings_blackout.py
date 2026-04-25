"""Guard blocking entries near earnings events."""

from __future__ import annotations

from app.core.config import app_cfg
from app.services.guards.base import GuardContext, GuardResult
from app.services.reference_data import business_days_until_earnings


def guard(ctx: GuardContext) -> GuardResult:
    days = business_days_until_earnings(ctx.symbol)
    if days is None:
        return GuardResult("earnings_blackout", True, True, "Earnings date unavailable.", "EARNINGS_UNKNOWN")
    if abs(days) <= app_cfg.risk.earnings_blackout_days:
        return GuardResult(
            "earnings_blackout",
            False,
            True,
            f"Earnings event is {days} business days away; blackout is ±{app_cfg.risk.earnings_blackout_days}.",
            "EARNINGS_BLACKOUT",
        )
    return GuardResult("earnings_blackout", True, True, "Outside earnings blackout window.", "EARNINGS_OK")
