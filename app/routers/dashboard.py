"""Professional Dashboard — unified API for the live trading dashboard."""
from __future__ import annotations

import logging
import os
from datetime import datetime

from fastapi import APIRouter, Query

logger = logging.getLogger("screener")

router = APIRouter(tags=["Dashboard"])


async def _dashboard_health():
    """Check service health directly."""
    checks = {"openbb_forecast": False, "market_data": False, "database": False, "broker": None}
    try:
        import openbb_forecast  # noqa: F401
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
    try:
        from halal_screener import _has_alpaca_broker_config
        if _has_alpaca_broker_config():
            from halal_screener import alpaca_get_account
            acct = alpaca_get_account()
            checks["broker"] = "connected" if acct and acct.get("status") == "ACTIVE" else "error"
        else:
            checks["broker"] = "not_configured"
    except Exception:
        checks["broker"] = "error"
    telegram_ok = bool(os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TGBOT"))
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
        "broker_type": "alpaca",
        "database": "connected" if db_ok else "disconnected",
        "market_data": "available" if md_ok else "unavailable",
        "telegram": checks["telegram"],
        "data_source": "yfinance" if md_ok else "unknown",
        "auto_trading": "enabled" if os.environ.get("AUTO_TRADE_ENABLED", "").lower() in ("true", "1") else "disabled",
        "uptime_seconds": metrics.get("uptime", 0),
    }


@router.get("/api/symbols/universe")
async def api_symbols_universe():
    """Return the full halal universe."""
    from halal_screener import _universe_symbols
    symbols = sorted(_universe_symbols())
    return {"count": len(symbols), "symbols": symbols}


@router.get("/api/symbols/search")
async def api_symbols_search(q: str = "", limit: int = 20):
    """Search halal universe by symbol or name."""
    from halal_screener import _universe_symbols
    symbols = _universe_symbols()
    if q:
        q = q.upper().strip()
        matches = [s for s in symbols if q in s]
    else:
        matches = sorted(symbols)
    return {"query": q, "count": len(matches[:limit]), "symbols": matches[:limit]}


@router.get("/api/trading/summary")
async def api_trading_summary():
    """Portfolio + positions + P&L summary."""
    from halal_screener import (
        alpaca_get_account, alpaca_get_positions, settings,
    )
    sid = None
    from app.config import STRATEGY_CONFIGS
    for sid_key in STRATEGY_CONFIGS:
        sid = sid_key
        break

    account = alpaca_get_account(strategy_id=sid) if sid else None
    positions = alpaca_get_positions(strategy_id=sid) if sid else []

    if account:
        equity = float(account.get("equity", 0))
        cash = float(account.get("cash", 0))
        buying_power = float(account.get("buying_power", 0))
        portfolio_value = float(account.get("portfolio_value", 0))
        last_equity = float(account.get("last_equity", 0))
        daily_pnl = round(equity - last_equity, 2)
        daily_pnl_pct = round((daily_pnl / last_equity * 100), 2) if last_equity > 0 else 0
    else:
        equity = cash = buying_power = portfolio_value = daily_pnl = daily_pnl_pct = 0

    return {
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "portfolio_value": portfolio_value,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct,
        "open_positions": len(positions),
        "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
        "positions": [
            {
                "symbol": p["symbol"], "qty": float(p.get("qty", 0)),
                "avg_entry": float(p.get("avg_entry_price", 0)),
                "current_price": float(p.get("current_price", 0)),
                "market_value": float(p.get("market_value", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
                "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
            }
            for p in (positions or [])
        ],
    }


@router.get("/api/guards/recent")
async def api_guards_recent(limit: int = 50):
    """Recent guard activity."""
    from app.db.database import SessionLocal
    from app.db.models import GuardLog
    db = SessionLocal()
    try:
        rows = db.query(GuardLog).order_by(GuardLog.ts.desc()).limit(min(max(limit, 1), 200)).all()
        return [
            {
                "ts": row.ts.isoformat() if row.ts else "",
                "symbol": row.symbol, "guard_name": row.guard_name,
                "passed": row.passed, "code": row.code, "reason": row.reason,
            }
            for row in rows
        ]
    finally:
        db.close()


@router.get("/api/guards/summary")
async def api_guards_summary():
    """Guard rejection counts by guard name."""
    from app.db.database import SessionLocal
    from app.db.models import GuardLog
    from sqlalchemy import func

    db = SessionLocal()
    try:
        today = datetime.utcnow().date()
        rows = (
            db.query(GuardLog.guard_name, func.count(GuardLog.id))
            .filter(
                not GuardLog.passed,
                func.date(GuardLog.ts) == today,
            )
            .group_by(GuardLog.guard_name)
            .all()
        )
        return {row[0]: row[1] for row in rows}
    finally:
        db.close()


@router.get("/api/scheduler/status")
async def api_scheduler_status():
    """Scheduler health and run counts."""
    from app.services.scheduler_metrics import scheduler_metrics
    from app.services.scheduler import _scheduler_running

    health = scheduler_metrics.health()
    health["running"] = _scheduler_running
    health["worker_mode"] = os.environ.get("WORKER_SERVICE", "").lower() == "true"
    return health


@router.get("/api/trading/controls")
async def api_trading_controls():
    """Current trading control settings."""
    from halal_screener import settings
    from app.core.config import app_cfg

    return {
        "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
        "kill_switch": app_cfg.killed,
        "min_confidence": settings.MIN_TRADE_CONFIDENCE,
        "trade_risk_pct": settings.TRADE_RISK_PCT,
        "max_position_pct": settings.MAX_POSITION_PCT,
        "max_open_positions": settings.MAX_OPEN_POSITIONS,
        "daily_loss_limit_pct": settings.DAILY_LOSS_LIMIT_PCT,
        "risk_capital": settings.RISK_CAPITAL,
        "live_whitelist": settings.live_whitelist,
        "live_whitelist_raw": settings.LIVE_WHITELIST,
    }


@router.post("/api/trading/controls/kill-switch")
async def api_kill_switch(killed: bool = True, x_api_key: str = Query(None)):
    """Toggle kill switch from dashboard."""
    from halal_screener import _require_api_key, app_cfg, tg_send
    _require_api_key(x_api_key)
    app_cfg.killed = killed
    tg_send(f"🔴 KILL SWITCH {'ENABLED' if killed else 'DISABLED'} from Dashboard")
    return {"killed": app_cfg.killed}


@router.post("/api/trading/controls/auto-trade")
async def api_auto_trade(enabled: bool = True, x_api_key: str = Query(None)):
    """Enable/disable auto-trading from dashboard."""
    from halal_screener import _require_api_key, settings, tg_send
    _require_api_key(x_api_key)
    settings.AUTO_TRADE_ENABLED = enabled
    tg_send(f"{'🟢' if enabled else '🔴'} AUTO-TRADING {'ENABLED' if enabled else 'DISABLED'} from Dashboard")
    return {"auto_trade_enabled": settings.AUTO_TRADE_ENABLED}


@router.get("/api/trading/history/recent")
async def api_trading_history(limit: int = 20):
    """Recent trade history."""
    from halal_screener import get_trade_history
    return get_trade_history(limit=limit)


@router.get("/api/halal/check")
async def api_halal_check(symbol: str = "AAPL"):
    """Check halal status for a symbol."""
    from halal_screener import validate_symbol, verify_halal
    symbol = validate_symbol(symbol)
    is_halal, reason = verify_halal(symbol)
    return {
        "symbol": symbol,
        "halal": is_halal,
        "details": {"reason": reason},
    }


@router.get("/api/halal/universe")
async def api_halal_universe():
    """List all halal symbols."""
    from halal_screener import _universe_symbols
    symbols = sorted(_universe_symbols())
    return {"count": len(symbols), "symbols": symbols[:100], "total": len(symbols)}


@router.get("/api/backtest")
async def api_backtest(
    symbol: str = "AAPL",
    start_date: str = "2022-01-01",
    end_date: str = "2024-12-31",
    portfolio: float = 100000,
    risk_pct: float = 1.0,
    hold_days: int = 3,
):
    """Run walk-forward backtest on a symbol."""
    from halal_screener import run_backtest, _serve_or_compute, _cache_key, validate_symbol, validate_date, validate_range

    s = validate_symbol(symbol)
    validate_date(start_date)
    validate_date(end_date)
    validate_range(portfolio, "portfolio", 1000, 10_000_000)
    validate_range(risk_pct, "risk_pct", 0.1, 10.0)
    validate_range(hold_days, "hold_days", 1, 60)

    key = _cache_key("backtest", symbol=s, start=start_date, end=end_date, portfolio=portfolio, risk=risk_pct, hold=hold_days)
    return _serve_or_compute(key, run_backtest, args=(s, start_date, end_date, portfolio, risk_pct, hold_days), msg=f"Computing backtest for {s}...")


@router.get("/api/system/status")
async def api_system_status():
    """Full system status for the dashboard overview."""
    from halal_screener import app_cfg
    from app.services.regime import get_regime

    regime = get_regime()
    health = await _dashboard_health()

    return {
        "status": health.get("status", "unknown"),
        "broker": health.get("broker", "unknown"),
        "database": health.get("database", "unknown"),
        "telegram": health.get("telegram", "unknown"),
        "data_source": health.get("data_source", "unknown"),
        "auto_trading": health.get("auto_trading", "unknown"),
        "kill_switch": app_cfg.killed,
        "regime": regime.state if regime else "UNKNOWN",
        "regime_changed": regime.changed if regime else False,
        "uptime_seconds": health.get("uptime_seconds", 0),
    }


@router.get("/api/pipeline/status")
async def api_pipeline_status():
    """Unified pipeline status for the dashboard."""
    from app.services.pipeline_orchestrator import PipelineReport
    from app.db.database import SessionLocal
    from app.db.models import PortfolioSnapshot

    latest_positions = None
    try:
        db = SessionLocal()
        snapshot = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.id.desc()).first()
        if snapshot:
            latest_positions = snapshot.positions_json
        db.close()
    except Exception:
        pass

    return {
        "pipeline_runs_today": 0,
        "last_run": None,
        "stages": [
            {"name": "collect", "label": "Data Collection", "status": "idle"},
            {"name": "halal", "label": "Halal Filter", "status": "idle"},
            {"name": "smart", "label": "Smart Filter", "status": "idle"},
            {"name": "consensus", "label": "AI Consensus", "status": "idle"},
            {"name": "kelly", "label": "Kelly Allocation", "status": "idle"},
            {"name": "guardian", "label": "Guardian Approval", "status": "idle"},
            {"name": "execute", "label": "Alpaca Execution", "status": "idle"},
            {"name": "report", "label": "Report & Snapshot", "status": "idle"},
        ],
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


@router.get("/api/market/context")
async def api_market_context(force_refresh: bool = False):
    """Return market context indicators: VIX, SPY Regime, Breadth, Credit, Liquidity."""
    from app.services.market_context import get_market_context
    return get_market_context(force_refresh=force_refresh)


@router.get("/api/market/status")
async def api_market_status(force_refresh: bool = False):
    """USX PRO V4.1 Market Status: RISK ON / CAUTION / CREDIT STRESS / EXTREME FEAR with gates."""
    from app.services.market_context import get_market_status
    return get_market_status(force_refresh=force_refresh)


@router.get("/api/sectors/performance")
async def api_sector_performance(force_refresh: bool = False):
    """Return performance for all 11 sector ETFs."""
    from app.services.sector_analysis import get_sector_performance
    return get_sector_performance(force_refresh=force_refresh)


@router.get("/api/scoring/weighted")
async def api_weighted_score(symbol: str = "AAPL"):
    """Return 100-point weighted score for a symbol."""
    from app.services.market_data import fetch
    from app.services.scoring import weighted_score
    df = fetch(symbol, period="6mo")
    if df is None:
        return {"error": f"No data for {symbol}"}
    spy_df = fetch("SPY", period="6mo")
    result = weighted_score(df, spy_df=spy_df)
    result["symbol"] = symbol.upper()
    return result


@router.get("/api/trade/plan")
async def api_trade_plan(symbol: str = "AAPL", portfolio: float = 100000.0):
    """Return trade plan (TP/SL + position sizing) for a symbol."""
    from app.services.market_data import fetch
    from app.services.trade_plan import generate_trade_plan
    df = fetch(symbol, period="6mo")
    if df is None:
        return {"error": f"No data for {symbol}"}
    result = generate_trade_plan(df, portfolio_equity=portfolio)
    result["symbol"] = symbol.upper()
    return result


@router.get("/api/block/status")
async def api_block_status():
    """Return current BLOCK system level."""
    from app.services.guards.block_system import get_block_level
    return {"block_level": get_block_level()}


@router.get("/api/pipeline/run")
async def api_pipeline_run(
    dry_run: bool = True,
    strategy: str = "ABC",
):
    """Manually trigger the unified pipeline. Returns PipelineReport."""
    from app.services.pipeline_orchestrator import run_pipeline

    sids = tuple(c for c in (strategy or "ABC").upper() if c in "ABC")
    if not sids:
        sids = ("A", "B", "C")

    report = run_pipeline(
        strategy_ids=sids,
        dry_run=dry_run,
    )
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


# ── Watchlist ──


@router.get("/api/watchlist")
async def api_get_watchlist():
    """Return current watchlist symbols."""
    from app.services.watchlist_service import get_watchlist
    return {"symbols": get_watchlist()}


@router.put("/api/watchlist")
async def api_set_watchlist(body: dict):
    """Replace entire watchlist. Body: {symbols: [...]}."""
    from app.services.watchlist_service import set_watchlist
    symbols = body.get("symbols", [])
    saved = set_watchlist(symbols)
    return {"symbols": saved, "count": len(saved)}


@router.post("/api/watchlist/add/{symbol}")
async def api_add_to_watchlist(symbol: str):
    """Add a symbol to watchlist."""
    from app.services.watchlist_service import add_symbol
    updated = add_symbol(symbol)
    return {"symbols": updated, "count": len(updated)}


@router.delete("/api/watchlist/remove/{symbol}")
async def api_remove_from_watchlist(symbol: str):
    """Remove a symbol from watchlist."""
    from app.services.watchlist_service import remove_symbol
    updated = remove_symbol(symbol)
    return {"symbols": updated, "count": len(updated)}
