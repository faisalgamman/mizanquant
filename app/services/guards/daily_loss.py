"""Guard enforcing daily loss breaker."""

from __future__ import annotations

from app.core.config import app_cfg
from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    equity = float(ctx.account.get("equity", 0) or 0)
    last_equity = float(ctx.account.get("last_equity", 0) or 0)
    if last_equity <= 0:
        return GuardResult("daily_loss", True, True, "No prior equity baseline.", "DAILY_LOSS_NO_BASE")
    daily_pnl_pct = (equity - last_equity) / last_equity * 100.0
    if daily_pnl_pct <= -app_cfg.risk.daily_loss_limit_pct:
        return GuardResult(
            "daily_loss",
            False,
            True,
            f"Daily loss {daily_pnl_pct:.2f}% breached limit -{app_cfg.risk.daily_loss_limit_pct:.2f}%.",
            "DAILY_LOSS_LIMIT",
        )
    return GuardResult("daily_loss", True, True, "Daily loss breaker not triggered.", "DAILY_LOSS_OK")
