"""Guard checking broker account status."""

from __future__ import annotations

from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    if ctx.account.get("trading_blocked") or ctx.account.get("account_blocked"):
        return GuardResult("account_status", False, True, "Account is blocked.", "ACCOUNT_BLOCKED")
    return GuardResult("account_status", True, True, "Account is tradable.", "ACCOUNT_OK")
