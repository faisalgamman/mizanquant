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


_health_cache: tuple[float, dict] | None = None
_HEALTH_CACHE_TTL = 60  # seconds


async def _dashboard_health():
    global _health_cache
    now = asyncio.get_event_loop().time()
    if _health_cache and (now - _health_cache[0]) < _HEALTH_CACHE_TTL:
        return _health_cache[1]

    checks = {"openbb_forecast": False, "market_data": False, "database": False, "broker": None}
    try:
        import openbb_forecast
        checks["openbb_forecast"] = True
    except Exception:
        pass

    broker_type = os.environ.get("BROKER_TYPE", "alpaca").lower()
    checks["broker_type"] = broker_type
    # Check if the broker's API keys are configured
    if broker_type == "alpaca":
        has_key = bool(os.environ.get("ALPACA_API_KEY") or getattr(settings, "ALPACA_API_KEY", None))
        has_secret = bool(os.environ.get("ALPACA_SECRET_KEY") or getattr(settings, "ALPACA_SECRET_KEY", None))
        checks["broker"] = "connected" if (has_key and has_secret) else "not_configured"
    elif broker_type == "ibkr":
        checks["broker"] = "connected"  # IBKR doesn't need API keys, uses gateway
    else:
        checks["broker"] = "not_configured"

    try:
        from app.db.database import engine
        with engine.connect() as conn:
            conn.close()
        checks["database"] = True
    except Exception:
        pass

    telegram_ok = bool(settings.TELEGRAM_BOT_TOKEN or os.environ.get("TGBOT"))
    checks["telegram"] = "active" if telegram_ok else "not_configured"
    from app.services.scheduler_metrics import scheduler_metrics
    metrics = scheduler_metrics.health()
    broker_ok = checks["broker"] == "connected"
    broker_unconfigured = checks["broker"] == "not_configured"
    db_ok = checks["database"]
    md_ok = checks["market_data"]
    if db_ok and (broker_ok or broker_unconfigured):
        # DB connected + broker either active or just not configured → ok
        status = "ok"
    elif broker_ok or db_ok:
        # One critical component has an error (not just unconfigured)
        status = "degraded"
    else:
        status = "down"
    result = {
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
    _health_cache = (now, result)
    return result


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


@router.get("/broker-diagnose")
async def v1_broker_diagnose():
    bt = os.environ.get("BROKER_TYPE", "alpaca").lower()
    result = {"broker_type": bt, "checks": {}}

    import socket
    from app.services.broker.ibkr_config import get_ibkr_config
    _cfg = get_ibkr_config()
    host = _cfg["host"]
    port = _cfg["port"]
    result["config"] = {"host": host, "port": port, "mode": _cfg["mode"]}

    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        result["checks"]["socket"] = "connected"
    except Exception as exc:
        result["checks"]["socket"] = f"FAILED — {exc}"
        return result

    try:
        from app.services.broker.ibkr_adapter import IBBroker, disconnect_all
        disconnect_all()
        broker = IBBroker()
        acct = broker.get_account()
        if acct is not None:
            result["checks"]["ib_api"] = "connected"
            result["account"] = acct
        else:
            result["checks"]["ib_api"] = "connected_but_no_account_data"
    except Exception as exc:
        result["checks"]["ib_api"] = f"FAILED — {exc}"

    try:
        broker = IBBroker()
        positions = broker.get_positions()
        result["checks"]["positions"] = f"ok ({len(positions)} positions)"
    except Exception as exc:
        result["checks"]["positions"] = f"FAILED — {exc}"

    return result
