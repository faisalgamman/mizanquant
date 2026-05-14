from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.async_database import get_async_db
from app.db.models import PortfolioSnapshot

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-pipeline"])


@router.get("/pipeline/status")
async def v1_pipeline_status(db: AsyncSession = Depends(get_async_db)):
    pipeline_stages = []
    pipeline_runs_today = 0
    last_run = None
    try:
        from app.services.pipeline_orchestrator import _orchestrator
        if _orchestrator is not None and _orchestrator.report:
            rpt = _orchestrator.report
            pipeline_runs_today = 1 if rpt.date_utc else 0
            last_run = rpt.started_at or None
            for s in rpt.stages:
                status_map = {"ok": "completed", "skipped": "completed", "failed": "failed"}
                pipeline_stages.append({
                    "name": s.stage,
                    "label": s.stage.replace("_", " ").title(),
                    "status": status_map.get(s.status, "idle"),
                    "count": max(s.count_in, s.count_out),
                    "elapsed_s": round(s.elapsed_s, 1) if s.elapsed_s else 0,
                })
    except Exception:
        pass

    if not pipeline_stages:
        pipeline_stages = [
            {"name": "collect", "label": "Data Collection", "status": "idle"},
            {"name": "halal", "label": "Halal Filter", "status": "idle"},
            {"name": "smart", "label": "Smart Filter", "status": "idle"},
            {"name": "consensus", "label": "AI Consensus", "status": "idle"},
            {"name": "kelly", "label": "Kelly Allocation", "status": "idle"},
            {"name": "guardian", "label": "Guardian Approval", "status": "idle"},
            {"name": "execute", "label": "Alpaca Execution", "status": "idle"},
            {"name": "report", "label": "Report & Snapshot", "status": "idle"},
        ]

    latest_positions = None
    try:
        stmt = select(PortfolioSnapshot).order_by(PortfolioSnapshot.id.desc()).limit(1)
        result = await db.execute(stmt)
        snapshot = result.scalar_one_or_none()
        if snapshot:
            latest_positions = snapshot.positions_json
    except Exception:
        pass

    return {
        "pipeline_runs_today": pipeline_runs_today,
        "last_run": last_run,
        "stages": pipeline_stages,
        "positions": latest_positions or [],
        "schedule": [
            {"time": "02:00", "task": "Model retraining", "type": "maintenance"},
            {"time": "08:00", "task": "Data collection", "type": "pipeline"},
            {"time": "08:30", "task": "Halal + Smart filter", "type": "pipeline"},
            {"time": "09:00", "task": "AI consensus + Kelly + Guardian + Alpaca", "type": "pipeline"},
            {"time": "10:30", "task": "Intraday signals scan", "type": "signal"},
            {"time": "12:00", "task": "Midday signals scan", "type": "signal"},
            {"time": "14:30", "task": "Afternoon signals scan", "type": "signal"},
            {"time": "16:00", "task": "Post-market report", "type": "pipeline"},
            {"time": "16:30", "task": "Signal audit", "type": "maintenance"},
        ],
    }


@router.get("/pipeline/run")
async def v1_pipeline_run(dry_run: bool = True, strategy: str = "ABC"):
    import asyncio
    from app.services.pipeline_orchestrator import run_pipeline

    sids = tuple(c for c in (strategy or "ABC").upper() if c in "ABC")
    if not sids:
        sids = ("A", "B", "C")

    report = await asyncio.to_thread(run_pipeline, strategy_ids=sids, dry_run=dry_run)
    return {
        "date_utc": report.date_utc,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "elapsed_s": report.elapsed_s,
        "signals_passed": report.signals_passed,
        "signals_rejected": report.signals_rejected,
        "signals_executed": report.signals_executed,
        "stages": [
            {
                "stage": s.stage,
                "status": s.status,
                "elapsed_s": round(s.elapsed_s, 2),
                "count_in": s.count_in,
                "count_out": s.count_out,
                "error": s.error,
            }
            for s in report.stages
        ],
        "error": report.error,
    }
