"""Informational guard highlighting trades allowed during bear regimes."""

from __future__ import annotations

from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    if ctx.regime.state == "BEAR" and ctx.side == "buy":
        return GuardResult(
            "regime_bear_freeze",
            False,
            False,
            "Trade still permitted in BEAR regime; operator review recommended.",
            "BEAR_WARN",
        )
    return GuardResult("regime_bear_freeze", True, False, "No BEAR warning.", "BEAR_OK")
