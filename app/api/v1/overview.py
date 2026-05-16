from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from app.services.memory_cache import memory_cache

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-overview"])


async def _get_system_status():
    from app.api.v1.system import _dashboard_health
    from app.services.regime import get_regime
    from app.core.config import app_cfg

    try:
        health = await _dashboard_health()
    except Exception as exc:
        logger.warning("Dashboard health check failed: %s", exc)
        health = {}

    try:
        regime = get_regime()
        regime_state = regime.state if regime else "UNKNOWN"
    except Exception:
        regime_state = "UNKNOWN"

    return {
        "status": health.get("status", "degraded"),
        "broker": health.get("broker", "unknown"),
        "broker_type": health.get("broker_type", "unknown"),
        "database": health.get("database", "unknown"),
        "telegram": health.get("telegram", "unknown"),
        "data_source": health.get("data_source", "unknown"),
        "auto_trading": health.get("auto_trading", "unknown"),
        "kill_switch": getattr(app_cfg, 'killed', False),
        "regime": regime_state,
        "uptime_seconds": health.get("uptime_seconds", 0),
    }


async def _get_portfolio():
    from app.config import settings as cfg, STRATEGY_CONFIGS
    from app.services.broker.factory import get_broker, _build

    sid = next(iter(STRATEGY_CONFIGS), None)
    broker = get_broker(strategy_id=sid)

    account = broker.get_account(strategy_id=sid) if broker else None
    positions = broker.get_positions(strategy_id=sid) if broker else []

    # Resilience fallback — primary broker (e.g. IBKR gateway down) returns
    # nothing → fall back to Alpaca so the portfolio panel shows live data.
    broker_label = broker.name if broker else "unknown"
    if account is None and broker_label != "alpaca":
        try:
            alpaca = _build("alpaca")
            fb = alpaca.get_account(strategy_id=sid)
            if fb:
                account = fb
                positions = alpaca.get_positions(strategy_id=sid) or []
                broker_label = "alpaca (fallback)"
        except Exception:
            pass

    if account:
        equity = float(account.get("equity", 0))
        cash = float(account.get("cash", 0))
        buying_power = float(account.get("buying_power", 0))
        portfolio_value = float(account.get("portfolio_value", 0))
        last_equity = float(account.get("last_equity", 0))
        daily_pnl = round(equity - last_equity, 2) if last_equity else 0
        daily_pnl_pct = round((daily_pnl / last_equity * 100), 2) if last_equity > 0 else 0
    else:
        equity = cash = buying_power = portfolio_value = daily_pnl = daily_pnl_pct = 0

    positions_list = [
        {
            "symbol": p["symbol"], "qty": float(p.get("qty", 0)),
            "avg_entry": float(p.get("avg_entry_price", 0)),
            "current_price": float(p.get("current_price", 0)),
            "market_value": float(p.get("market_value", 0)),
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
            "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
        }
        for p in (positions or [])
    ]

    return {
        "broker_type": broker_label,
        "equity": equity, "cash": cash, "buying_power": buying_power,
        "portfolio_value": portfolio_value, "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct, "open_positions": len(positions),
        "auto_trade_enabled": cfg.AUTO_TRADE_ENABLED,
        "positions": positions_list,
    }


async def _get_market():
    from app.services.market_context import get_market_context, get_market_status
    from app.services.sector_analysis import get_sector_performance

    context = {"stale": True, "error": "timeout"}
    try:
        context = await asyncio.wait_for(
            asyncio.to_thread(get_market_context, False),
            timeout=15.0,
        )
    except Exception as exc:
        logger.warning("Market context fetch failed: %s", exc)

    try:
        status = get_market_status(force_refresh=False)
    except Exception:
        status = {"market_open": False, "error": "unavailable"}

    try:
        sectors = get_sector_performance(force_refresh=False)
    except Exception:
        sectors = []

    context["_market_open"] = status.get("market_open", False)
    context["_sectors"] = sectors
    return context


async def _get_pipeline():
    from app.services.pipeline_orchestrator import _orchestrator

    pipeline_stages = []
    pipeline_runs_today = 0
    last_run = None
    try:
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
        from app.db.database import SessionLocal as SyncSessionLocal
        from app.db.models import PortfolioSnapshot

        def _fetch_snapshot():
            db = SyncSessionLocal()
            try:
                return db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.id.desc()).first()
            finally:
                db.close()

        snapshot = await asyncio.to_thread(_fetch_snapshot)
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


async def _get_top_signals(limit: int = 3):
    try:
        from app.db.database import SessionLocal as SyncSessionLocal
        from app.db.models import ConsensusLog

        def _fetch():
            db = SyncSessionLocal()
            try:
                return db.query(ConsensusLog).order_by(ConsensusLog.created_at.desc()).limit(limit).all()
            finally:
                db.close()

        rows = await asyncio.to_thread(_fetch)
        return [
            {
                "symbol": row.symbol,
                "consensus": row.verdict,
                "score_avg": row.confidence,
                "ts": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    except Exception:
        return []


async def _get_guard_summary():
    """Return {guard_name: count} for today's rejections — matches frontend expectation."""
    try:
        from app.db.database import SessionLocal as SyncSessionLocal
        from app.db.models import GuardLog
        from sqlalchemy import func
        from datetime import date

        def _fetch():
            db = SyncSessionLocal()
            try:
                today = date.today()
                rows = (
                    db.query(GuardLog.guard_name, func.count(GuardLog.id))
                    .filter(GuardLog.passed == False)
                    .filter(func.date(GuardLog.ts) == today)
                    .group_by(GuardLog.guard_name)
                    .all()
                )
                return {name: count for name, count in rows}
            finally:
                db.close()

        return await asyncio.to_thread(_fetch)
    except Exception:
        return {}


@router.get("/overview")
async def v1_overview(force_refresh: bool = False):

    async def _compute():
        results = await asyncio.gather(
            _get_system_status(),
            _get_portfolio(),
            _get_market(),
            _get_pipeline(),
            _get_top_signals(),
            _get_guard_summary(),
            return_exceptions=True,
        )

        def _r(val, default=None):
            return default if isinstance(val, Exception) else val

        return {
            "system": _r(results[0], {}),
            "portfolio": _r(results[1], {}),
            "market_context": _r(results[2], {}),
            "pipeline": _r(results[3], {}),
            "top_signals": _r(results[4], []),
            "guards_recent": _r(results[5], {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return await memory_cache.get_or_compute(
        "overview:aggregated", 30, _compute, force_refresh=force_refresh
    )
