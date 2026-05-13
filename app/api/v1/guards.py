from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.async_database import get_async_db
from app.db.models import GuardLog

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-guards"])


@router.get("/guards/recent")
async def v1_guards_recent(limit: int = 50, db: AsyncSession = Depends(get_async_db)):
    stmt = (
        select(GuardLog)
        .order_by(GuardLog.ts.desc())
        .limit(min(max(limit, 1), 200))
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "ts": row.ts.isoformat() if row.ts else "",
            "symbol": row.symbol,
            "guard_name": row.guard_name,
            "passed": row.passed,
            "code": row.code,
            "reason": row.reason,
        }
        for row in rows
    ]


@router.get("/guards/summary")
async def v1_guards_summary(db: AsyncSession = Depends(get_async_db)):
    today = datetime.utcnow().date()
    stmt = (
        select(GuardLog.guard_name, func.count(GuardLog.id))
        .where(GuardLog.passed == False)  # noqa: E712
        .where(func.date(GuardLog.ts) == today)
        .group_by(GuardLog.guard_name)
    )
    result = await db.execute(stmt)
    return {row[0]: row[1] for row in result}
