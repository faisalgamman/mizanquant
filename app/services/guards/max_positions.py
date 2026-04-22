"""Guard limiting concurrent holdings by regime."""

from __future__ import annotations

from app.core.config import app_cfg
from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    limit = app_cfg.risk.max_positions.get(ctx.regime.state, app_cfg.risk.max_positions["NEUTRAL"])
    current = len(ctx.positions)
    if ctx.side == "buy" and current >= limit:
        return GuardResult(
            "max_positions",
            False,
            True,
            f"Max positions reached ({current}/{limit}) for regime {ctx.regime.state}.",
            "MAX_POSITIONS",
        )
    return GuardResult("max_positions", True, True, "Open-position count within limit.", "MAX_POSITIONS_OK")
