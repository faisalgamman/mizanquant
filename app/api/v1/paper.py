from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
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
async def v1_paper_execute(body: PaperExecuteRequest, db: AsyncSession | None = Depends(get_async_db)):
    if db is None:
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})
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
async def v1_paper_trades(limit: int = Query(default=50, le=200),
                          strategy_id: str | None = Query(default=None),
                          db: AsyncSession | None = Depends(get_async_db)):
    if db is None:
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})
    from app.db.models import TradeHistory

    try:
        q = select(TradeHistory).order_by(TradeHistory.created_at.desc())
        if strategy_id:
            q = q.filter(TradeHistory.strategy_id == strategy_id)
        result = await db.execute(q.limit(limit))
        trades = result.scalars().all()
    except Exception as exc:
        logger.error("paper/trades DB query failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Database query failed: {exc}"},
        )

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
            "exit_price": t.exit_price,
            "strategy_id": t.strategy_id,
            "pair": (t.signal_details or {}).get("pair") if isinstance(t.signal_details, dict) else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        }
        for t in trades
    ]


@router.get("/paper/status")
async def v1_paper_status(strategy_id: str | None = Query(default=None)):
    from app.services.paper_trade_gate import paper_trade_status_async

    result = await paper_trade_status_async(strategy_id=strategy_id)
    return result.as_dict()


@router.post("/paper/close/{trade_id}")
async def v1_paper_close(trade_id: int, db: AsyncSession | None = Depends(get_async_db)):
    if db is None:
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})
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


# ── Broker health + real execution (IBKR paper, strategy "MANUAL") ──────────

@router.get("/broker/health")
async def v1_broker_health():
    """Honest IBKR paper connectivity probe for the manual 'Send to paper' button.
    connected=False (not an error) when the IB Gateway is unreachable.
    """
    import os
    from app.services.broker.factory import get_broker
    from app.services.broker.ibkr_config import get_ibkr_config

    strategy = "MANUAL"
    broker_name = os.environ.get("STRATEGY_BROKER_MANUAL", os.environ.get("BROKER_TYPE", "alpaca")).lower()
    cfg = get_ibkr_config()
    out = {"strategy": strategy, "broker": broker_name,
           "host": cfg["host"], "port": cfg["port"], "mode": cfg["mode"],
           "connected": False, "account": None}
    try:
        broker = get_broker(strategy_id=strategy)
        acct = await asyncio.to_thread(broker.get_account, strategy)
        if acct:
            out["connected"] = True
            out["account"] = {"equity": acct.get("equity"), "cash": acct.get("cash"),
                              "buying_power": acct.get("buying_power"),
                              "account_type": acct.get("account_type")}
    except Exception as e:
        logger.warning("broker health probe failed: %s", e)
        out["error"] = str(e)[:160]
    return out


class BrokerExecuteRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    side: Literal["buy", "sell"] = Field(default="buy")
    entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    shares: float = Field(..., gt=0)
    risk_amount: float | None = None
    confidence: float = Field(default=0, ge=0, le=1)


@router.post("/broker/execute")
async def v1_broker_execute(body: BrokerExecuteRequest, db: AsyncSession | None = Depends(get_async_db)):
    """Place a REAL bracket order on the IBKR paper account (strategy 'MANUAL') and record it.

    Honest failure modes:
      - halal gate fail  -> 200 {success:false, reason:'halal_blocked'}
      - broker offline    -> 200 {success:false, reason:'broker_offline', ...}  (no DB row)
      - broker rejected   -> 200 {success:false, reason:<broker reason>}        (no DB row)
    """
    from datetime import datetime, timezone

    sym = body.symbol.upper().strip()

    # 1. Server-side halal guard (defense in depth; UI already gates).
    try:
        from app.services.halal_screening import verify_halal
        ok_halal, halal_reason = verify_halal(sym)
    except Exception:
        ok_halal, halal_reason = True, "halal check unavailable"
    if not ok_halal:
        return {"success": False, "reason": "halal_blocked", "detail": halal_reason, "symbol": sym}

    # 2. Submit bracket via the IBKR-routed manual order manager (sync -> thread).
    def _submit():
        from app.services.order_manager import get_order_manager
        mgr = get_order_manager(strategy_id="MANUAL")
        return mgr.submit_bracket(
            symbol=sym, side=body.side, qty=int(body.shares),
            limit_price=float(body.entry_price),
            stop_loss_price=float(body.stop_loss),
            take_profit_price=float(body.take_profit),
            time_in_force="gtc",
            entry_type="market",
        )
    try:
        res = await asyncio.to_thread(_submit)
    except Exception as e:
        logger.error("broker/execute submit error for %s: %s", sym, e)
        return {"success": False, "reason": "broker_error", "detail": str(e)[:160], "symbol": sym}

    broker_id = (res or {}).get("order_id") or ""
    if not res or not res.get("success") or not broker_id:
        # Broker unreachable or rejected — do NOT write a misleading 'submitted' row.
        reason = (res or {}).get("reason") or "broker_offline"
        low = reason.lower()
        tag = "broker_offline" if ("empty response" in low or "broker_error" in low or not res) else reason
        return {"success": False, "reason": tag, "detail": reason, "symbol": sym}

    # 3. Broker accepted -> record TradeHistory so the dashboard shows it.
    db_id = None
    if db is not None:
        try:
            from app.db.models import TradeHistory
            trade = TradeHistory(
                symbol=sym, side=body.side, entry_price=body.entry_price,
                stop_loss=body.stop_loss, take_profit=body.take_profit,
                qty=body.shares, position_value=body.shares * body.entry_price,
                risk_amount=body.risk_amount, confidence=body.confidence,
                status="submitted", strategy_id="MANUAL",
                signal_details={"source": "manual_send_to_paper", "broker": "ibkr",
                                "broker_order_id": broker_id, "order_class": "bracket"},
                created_at=datetime.now(timezone.utc),
            )
            db.add(trade)
            await db.commit()
            await db.refresh(trade)
            db_id = trade.id
        except Exception as e:
            logger.error("broker/execute DB record failed for %s: %s", sym, e)

    return {"success": True, "symbol": sym, "broker": "ibkr",
            "broker_order_id": broker_id, "status": res.get("status", "submitted"),
            "shares": body.shares, "entry_price": body.entry_price,
            "stop_loss": body.stop_loss, "take_profit": body.take_profit, "db_id": db_id}


class BrokerCloseRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)


@router.post("/broker/close")
async def v1_broker_close(body: BrokerCloseRequest):
    """Market-close (sell) a position on the IBKR MANUAL account. Honest on failure."""
    sym = body.symbol.upper().strip()

    def _close():
        from app.services.broker.factory import get_broker
        b = get_broker(strategy_id="MANUAL")
        return b.close_position(sym, strategy_id="MANUAL") if b else None

    try:
        res = await asyncio.to_thread(_close)
    except Exception as e:
        logger.error("broker/close %s: %s", sym, e)
        return {"success": False, "reason": "broker_error", "detail": str(e)[:160], "symbol": sym}
    if not res:
        return {"success": False, "reason": "broker_offline", "symbol": sym}
    return {"success": True, "symbol": sym, "order_id": res.get("id", ""),
            "status": res.get("status", "submitted")}


@router.post("/paper/record-now")
async def v1_paper_record_now(scanner: str = Query("weekly")):
    """Manually record the current scanner picks into the paper-validation ledger.

    Session-gated (the dashboard login already protects /api/v1/*), so it needs NO operator
    API key in the browser. Kicks off the same record/rebalance the scheduler runs.
    """
    import threading
    s = (scanner or "weekly").lower()
    try:
        if s.startswith("month"):
            from app.services.paper_validation import rebalance_monthly
            threading.Thread(target=rebalance_monthly, daemon=True).start()
        elif s.startswith("pair"):
            from app.services.paper_validation import record_pairs_signals
            threading.Thread(target=record_pairs_signals, daemon=True).start()
        else:
            from app.services.paper_validation import record_weekly_picks
            threading.Thread(target=record_weekly_picks, daemon=True).start()
        return {"status": "started", "scanner": s,
                "message": "Recording started (1-3 min) — poll the ledger status."}
    except Exception as e:
        logger.error("paper record-now %s: %s", s, e)
        return {"status": "error", "detail": str(e)[:160]}


@router.post("/paper/auto-run")
async def v1_paper_auto_run(scanner: str = Query("weekly")):
    """Manually trigger the auto-paper executor (places the scanner's halal picks as IBKR
    PAPER bracket orders, capped & deduped). Session-gated; honors AUTO_PAPER_TRADE and the
    paper-only guard. The blocking broker calls run in a worker thread."""
    from app.services.auto_paper import run_auto_paper
    return await asyncio.to_thread(run_auto_paper, scanner)


@router.get("/broker/orders")
async def v1_broker_orders():
    """Open orders on the IBKR paper account (strategy 'MANUAL'). Returns [] when the
    gateway is offline — never raises, so the cockpit degrades gracefully."""
    from app.services.broker.factory import get_broker
    try:
        broker = get_broker(strategy_id="MANUAL")
        orders = await asyncio.to_thread(broker.get_orders, "open", 50, "MANUAL")
        return orders or []
    except Exception as e:
        logger.warning("broker/orders failed: %s", e)
        return []
