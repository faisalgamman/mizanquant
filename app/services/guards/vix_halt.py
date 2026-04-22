"""Guard halting buys when volatility is excessively elevated."""

from __future__ import annotations

from app.core.config import app_cfg
from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    if ctx.regime.vix_pctile > app_cfg.regime.vix_halt_pct:
        return GuardResult(
            "vix_halt",
            False,
            True,
            f"VIX percentile {ctx.regime.vix_pctile:.1f} exceeds halt threshold {app_cfg.regime.vix_halt_pct:.1f}.",
            "VIX_HALT",
        )
    return GuardResult("vix_halt", True, True, "Volatility within allowed range.", "VIX_OK")
