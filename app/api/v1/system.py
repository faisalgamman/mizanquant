from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from fastapi import APIRouter

from app.config import settings
from app.core.config import app_cfg

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-system"])


def _fetch_universe() -> list[str]:
    from app.db.database import SessionLocal
    from app.services.universe import get_universe_symbols
    db = SessionLocal()
    try:
        return get_universe_symbols(db)
    finally:
        db.close()


async def _dashboard_health():
    checks = {"openbb_forecast": False, "market_data": False, "database": False, "broker": None}
    try:
        import openbb_forecast
        checks["openbb_forecast"] = True
    except Exception:
        pass
    try:
        import yfinance as yf
        ticker = yf.Ticker("AAPL")
        info = ticker.info or {}
        checks["market_data"] = bool(info.get("regularMarketPrice") or info.get("currentPrice"))
    except Exception:
        try:
            from app.services.yfinance_utils import fetch_yf
            df = fetch_yf("AAPL", period="1mo")
            checks["market_data"] = df is not None and len(df) > 0
        except Exception:
            pass
    try:
        from app.db.database import engine
        engine.connect().close()
        checks["database"] = True
    except Exception:
        pass

    broker_type = os.environ.get("BROKER_TYPE", "alpaca").lower()
    checks["broker_type"] = broker_type

    try:
        if broker_type == "ibkr":
            import socket
            host = os.environ.get("IBKR_HOST", "127.0.0.1")
            port = int(os.environ.get("IBKR_PORT", "7497"))
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            checks["broker"] = "connected"
        else:
            from app.services.broker.factory import get_broker
            from app.config import STRATEGY_CONFIGS
            sid = next(iter(STRATEGY_CONFIGS), None)
            broker = get_broker(strategy_id=sid)
            if broker:
                acct = broker.get_account(strategy_id=sid)
                checks["broker"] = "connected" if acct and acct.get("status") == "ACTIVE" else "error"
            else:
                checks["broker"] = "not_configured"
    except Exception:
        checks["broker"] = "error" if broker_type == "ibkr" else "not_configured"

    telegram_ok = bool(settings.TELEGRAM_BOT_TOKEN or os.environ.get("TGBOT"))
    checks["telegram"] = "active" if telegram_ok else "not_configured"
    from app.services.scheduler_metrics import scheduler_metrics
    metrics = scheduler_metrics.health()
    broker_ok = checks["broker"] == "connected"
    db_ok = checks["database"]
    md_ok = checks["market_data"]
    if broker_ok and db_ok:
        status = "operational"
    elif broker_ok or db_ok:
        status = "degraded"
    else:
        status = "down"
    return {
        "status": status,
        "broker": checks["broker"],
        "broker_type": checks["broker_type"],
        "database": "connected" if db_ok else "disconnected",
        "market_data": "available" if md_ok else "unavailable",
        "telegram": checks["telegram"],
        "data_source": "yfinance" if md_ok else "unknown",
        "auto_trading": "enabled" if settings.AUTO_TRADE_ENABLED else "disabled",
        "uptime_seconds": metrics.get("uptime", 0),
    }


@router.get("/system/status")
async def v1_system_status():
    from app.services.regime import get_regime

    regime = get_regime()
    health = await _dashboard_health()

    return {
        "status": health.get("status", "unknown"),
        "broker": health.get("broker", "unknown"),
        "broker_type": health.get("broker_type", "unknown"),
        "database": health.get("database", "unknown"),
        "telegram": health.get("telegram", "unknown"),
        "data_source": health.get("data_source", "unknown"),
        "auto_trading": health.get("auto_trading", "unknown"),
        "kill_switch": app_cfg.killed,
        "regime": regime.state if regime else "UNKNOWN",
        "regime_changed": regime.changed if regime else False,
        "uptime_seconds": health.get("uptime_seconds", 0),
    }


@router.get("/system/health")
async def v1_system_health():
    return await _dashboard_health()


@router.get("/scheduler/status")
async def v1_scheduler_status():
    from app.services.scheduler_metrics import scheduler_metrics
    from app.services.scheduler import _scheduler_running

    health = scheduler_metrics.health()
    health["running"] = _scheduler_running
    health["worker_mode"] = os.environ.get("WORKER_SERVICE", "").lower() == "true"
    # Add per-cycle run counts for frontend scheduler timeline
    snap = scheduler_metrics.snapshot()
    health["run_counts"] = {name: c["total_runs"] for name, c in snap.get("cycles", {}).items()}
    health["runs"] = health["run_counts"]
    return health


@router.get("/block/status")
async def v1_block_status():
    from app.services.guards.block_system import get_block_level
    return {"block_level": get_block_level()}


@router.get("/symbols/universe")
async def v1_symbols_universe():
    from app.services.universe import get_universe_symbols

    symbols = sorted(await asyncio.to_thread(_fetch_universe))
    return {"count": len(symbols), "symbols": symbols}


@router.get("/symbols/search")
async def v1_symbols_search(q: str = "", limit: int = 20):
    symbols = await asyncio.to_thread(_fetch_universe)

    if q:
        q = q.upper().strip()
        matches = [s for s in symbols if q in s]
    else:
        matches = sorted(symbols)
    return {"query": q, "count": len(matches[:limit]), "symbols": matches[:limit]}
