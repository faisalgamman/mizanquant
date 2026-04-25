"""Guard blocking duplicate symbol entries across strategies in the same window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.guards.base import GuardContext, GuardResult


def guard(ctx: GuardContext) -> GuardResult:
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
            recent = db.query(TradeHistory).filter(
                TradeHistory.symbol == ctx.symbol.upper(),
                TradeHistory.created_at >= cutoff,
                TradeHistory.strategy_id != ctx.strategy_id,
                TradeHistory.status.in_(["submitted", "armed", "filled", "partially_filled"]),
            ).first()
        finally:
            db.close()
    except Exception:
        recent = None

    if recent is not None:
        return GuardResult(
            "cross_strategy_veto",
            False,
            True,
            f"Another strategy opened {ctx.symbol.upper()} within the last 5 minutes.",
            "CROSS_STRATEGY_VETO",
        )
    return GuardResult("cross_strategy_veto", True, True, "No recent cross-strategy conflict.", "CROSS_STRATEGY_OK")
