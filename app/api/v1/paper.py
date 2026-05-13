from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.async_database import get_async_db

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-paper"])


class PaperExecuteRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    side: Literal["buy", "sell"] = Field(default="buy")
    entry_price: float = Field(..., gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    shares: float = Field(default=0, ge=0)
    position_value: float | None = None
    risk_amount: float | None = None
    confidence: float = Field(default=0, ge=0, le=1)


@router.post("/paper/execute")
async def v1_paper_execute(body: PaperExecuteRequest, db: AsyncSession = Depends(get_async_db)):
    from app.db.models import TradeHistory
    from datetime import datetime, timezone

    trade = TradeHistory(
        symbol=body.symbol.upper(),
        side=body.side,
        entry_price=body.entry_price,
        stop_loss=body.stop_loss,
        take_profit=body.take_profit,
        qty=body.shares,
        position_value=body.position_value or (body.shares * body.entry_price),
        risk_amount=body.risk_amount,
        confidence=body.confidence,
        status="submitted",
        created_at=datetime.now(timezone.utc),
    )
    db.add(trade)
    await db.commit()
    await db.refresh(trade)

    return {
        "id": trade.id,
        "symbol": trade.symbol,
        "status": trade.status,
        "entry_price": trade.entry_price,
        "shares": trade.qty,
        "created_at": trade.created_at.isoformat(),
    }


@router.get("/paper/trades")
async def v1_paper_trades(limit: int = Query(default=50, le=200), db: AsyncSession = Depends(get_async_db)):
    from app.db.models import TradeHistory

    result = await db.execute(
        select(TradeHistory)
        .order_by(TradeHistory.created_at.desc())
        .limit(limit)
    )
    trades = result.scalars().all()

    return [
        {
            "id": t.id,
            "symbol": t.symbol,
            "side": t.side,
            "entry_price": t.entry_price,
            "stop_loss": t.stop_loss,
            "take_profit": t.take_profit,
            "qty": t.qty,
            "position_value": t.position_value,
            "risk_amount": t.risk_amount,
            "confidence": t.confidence,
            "status": t.status,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "strategy_id": t.strategy_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in trades
    ]


@router.get("/paper/status")
async def v1_paper_status(strategy_id: str | None = Query(default=None)):
    from app.services.paper_trade_gate import paper_trade_status

    result = await asyncio.to_thread(paper_trade_status, strategy_id=strategy_id)
    return result.as_dict()


@router.post("/paper/close/{trade_id}")
async def v1_paper_close(trade_id: int, db: AsyncSession = Depends(get_async_db)):
    from app.db.models import TradeHistory
    from datetime import datetime, timezone

    result = await db.execute(select(TradeHistory).where(TradeHistory.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        return {"error": "Trade not found", "id": trade_id}
    trade.status = "closed"
    trade.closed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(trade)
    return {
        "id": trade.id,
        "symbol": trade.symbol,
        "status": trade.status,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
    }
