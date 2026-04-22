"""Guard capping sector exposure using cached profile metadata."""

from __future__ import annotations

from app.core.config import app_cfg
from app.services.guards.base import GuardContext, GuardResult
from app.services.reference_data import get_symbol_sector


def guard(ctx: GuardContext) -> GuardResult:
    equity = float(ctx.account.get("equity", 0) or 0)
    if equity <= 0:
        return GuardResult("sector_exposure", True, True, "No equity baseline for sector cap.", "SECTOR_NO_EQUITY")

    target_sector = get_symbol_sector(ctx.symbol)
    if not target_sector:
        return GuardResult("sector_exposure", True, True, "Sector data unavailable; guard skipped.", "SECTOR_UNKNOWN")

    current_sector_value = 0.0
    for pos in ctx.positions:
        if get_symbol_sector(str(pos.get("symbol", ""))) == target_sector:
            current_sector_value += float(pos.get("market_value", 0) or 0)

    projected_pct = (current_sector_value + ctx.notional) / equity * 100.0
    if projected_pct > app_cfg.risk.sector_exposure_cap_pct:
        return GuardResult(
            "sector_exposure",
            False,
            True,
            f"Projected {target_sector} exposure {projected_pct:.2f}% exceeds cap {app_cfg.risk.sector_exposure_cap_pct:.2f}%.",
            "SECTOR_EXPOSURE_CAP",
        )
    return GuardResult("sector_exposure", True, True, "Sector exposure within cap.", "SECTOR_EXPOSURE_OK")
