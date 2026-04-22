"""Guard preventing duplicate symbol exposure inside one strategy account."""

from __future__ import annotations

from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    held = {str(p.get("symbol", "")).upper() for p in ctx.positions}
    if ctx.side == "buy" and ctx.symbol.upper() in held:
        return GuardResult(
            "duplicate_symbol",
            False,
            True,
            f"Already holding {ctx.symbol.upper()}.",
            "DUPLICATE_SYMBOL",
        )
    return GuardResult("duplicate_symbol", True, True, "No duplicate symbol exposure.", "DUPLICATE_SYMBOL_OK")
