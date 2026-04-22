"""Guard enforcing portfolio drawdown cap across recent snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import app_cfg
from app.services.guards.base import GuardContext, GuardResult


def _recent_drawdown(strategy_id: str | None) -> float | None:
    try:
        from app.db.database import SessionLocal
        from app.db.models import PortfolioSnapshot

        db = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=app_cfg.risk.portfolio_dd_window_days)
            query = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.created_at >= cutoff)
            snapshots = query.order_by(PortfolioSnapshot.created_at.asc()).all()
        finally:
            db.close()
    except Exception:
        return None

    equities = [float(s.total_equity or 0) for s in snapshots if s.total_equity]
    if len(equities) < 2:
        return None
    peak = equities[0]
    max_dd = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    return max_dd


def guard(ctx: GuardContext) -> GuardResult:
    drawdown = _recent_drawdown(ctx.strategy_id)
    if drawdown is None:
        return GuardResult(
            "portfolio_drawdown",
            True,
            True,
            "No recent portfolio history for drawdown guard.",
            "PORTFOLIO_DD_NO_DATA",
        )
    if drawdown > app_cfg.risk.portfolio_dd_cap_pct:
        return GuardResult(
            "portfolio_drawdown",
            False,
            True,
            f"30-day drawdown {drawdown:.2f}% exceeds cap {app_cfg.risk.portfolio_dd_cap_pct:.2f}%.",
            "PORTFOLIO_DD_LIMIT",
        )
    return GuardResult("portfolio_drawdown", True, True, "Portfolio drawdown within cap.", "PORTFOLIO_DD_OK")
