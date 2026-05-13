"""Professional Dashboard — unified API for the live trading dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query

logger = logging.getLogger("screener")

router = APIRouter(tags=["Dashboard"])


async def _dashboard_health():
    """Check service health directly."""
    from app.config import settings
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
            from halal_screener import _has_alpaca_broker_config
            if _has_alpaca_broker_config():
                from halal_screener import alpaca_get_account
                acct = alpaca_get_account()
                checks["broker"] = "connected" if acct and acct.get("status") == "ACTIVE" else "error"
            else:
                checks["broker"] = "not_configured"
    except Exception:
        checks["broker"] = "error" if broker_type == "ibkr" else "not_configured"

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
        "broker_type": checks["broker_type"],
        "database": "connected" if db_ok else "disconnected",
        "market_data": "available" if md_ok else "unavailable",
        "telegram": checks["telegram"],
        "data_source": "yfinance" if md_ok else "unknown",
        "auto_trading": "enabled" if settings.AUTO_TRADE_ENABLED else "disabled",
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
    from halal_screener import settings
    from app.config import STRATEGY_CONFIGS

    broker_type = os.environ.get("BROKER_TYPE", "alpaca").lower()
    sid = None
    for sid_key in STRATEGY_CONFIGS:
        sid = sid_key
        break

    if broker_type == "ibkr":
        return {
            "broker_type": "ibkr",
            "equity": 0, "cash": 0, "buying_power": 0, "portfolio_value": 0,
            "daily_pnl": 0, "daily_pnl_pct": 0,
            "open_positions": 0,
            "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
            "positions": [],
            "message": "IBKR account details not yet available via REST API. Use IB Gateway desktop to view positions.",
        }

    from halal_screener import (
        alpaca_get_account, alpaca_get_positions,
    )

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
        "broker_type": "alpaca",
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


@router.get("/api/pipeline/status")
async def api_pipeline_status():
    """Unified pipeline status for the dashboard."""
    from app.db.database import SessionLocal
    from app.db.models import PortfolioSnapshot

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
        db = SessionLocal()
        snapshot = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.id.desc()).first()
        if snapshot:
            latest_positions = snapshot.positions_json
        db.close()
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


@router.get("/api/market/context")
async def api_market_context(force_refresh: bool = False):
    """Return market context indicators: VIX, SPY Regime, Breadth, Credit, Liquidity."""
    from app.services.market_context import get_market_context
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(get_market_context, force_refresh),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.warning("/api/market/context exceeded 10s — returning empty stale shell")
        return {
            "vix": {}, "spy_regime": {}, "breadth": {}, "credit": {}, "liquidity": {},
            "stale": True, "error": "timeout",
            "cached_at": datetime.utcnow().isoformat(),
        }


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
    from app.services.scoring import weighted_score, _score_to_dict
    df = fetch(symbol, period="6mo")
    if df is None:
        return {"error": f"No data for {symbol}"}
    spy_df = fetch("SPY", period="6mo")
    result = weighted_score(df, spy_df=spy_df)
    result_dict = _score_to_dict(result)
    result_dict["symbol"] = symbol.upper()
    return result_dict


@router.get("/api/trade/plan")
async def api_trade_plan(symbol: str = "AAPL", portfolio: float = 100000.0):
    """Return trade plan (strategy + TP/SL + position sizing) for a symbol."""
    from app.services.market_data import fetch
    from app.services.trade_plan import generate_trade_plan
    import yfinance as yf
    import pandas as pd
    df = fetch(symbol, period="6mo")
    if df is None:
        return {"error": f"No data for {symbol}"}
    plan = generate_trade_plan(df, portfolio_equity=portfolio)
    plan["symbol"] = symbol.upper()

    # Strategy context
    try:
        spy_df = fetch("SPY", period="6mo")
        from app.workspace_server import _get_symbol_strategy
        sig = _get_symbol_strategy(symbol, df, spy_df)
        if sig:
            plan["strategy"] = sig.strategy
            plan["strategy_score"] = sig.score
            plan["strategy_reason"] = sig.reason
            plan["strategy_confidence"] = round(sig.confidence, 2)
            plan["hold_days_min"] = sig.hold_days_min
            plan["hold_days_max"] = sig.hold_days_max
            plan["strategy_entry"] = sig.entry
            plan["strategy_stop"] = sig.stop
            plan["strategy_tp1"] = sig.tp1
            plan["strategy_tp2"] = sig.tp2
            plan["strategy_tp3"] = sig.tp3
            plan["details"] = sig.details
            plan["pipeline"] = {
                "data": "loaded",
                "halal": "pending",
                "smart": "scored",
                "strategy_selector": f"{sig.strategy} (score={sig.score})",
                "ai_confirm": f"{'confirmed' if sig.score >= 65 else 'rejected'}",
            }
        else:
            plan["strategy"] = "WAIT"
            plan["strategy_reason"] = "No strategy triggered"
    except Exception as e:
        plan["strategy"] = "WAIT"
        plan["strategy_reason"] = f"Strategy error: {e}"

    return plan


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


BACKTEST_CACHE_PATH = Path(__file__).parent.parent / ".cache" / "backtest_summary.json"
BACKTEST_CACHE_TTL = 3600  # 1 hour


def _quality_flag(sharpe: float, profit_factor: float, win_rate: float) -> str:
    """Classify backtest quality: AVOID / WEAK / OK / GOOD / EXCELLENT."""
    if sharpe < 0 or profit_factor < 0.5 or win_rate < 30:
        return "AVOID"
    if sharpe < 0.5:
        return "WEAK"
    if sharpe < 1.0:
        return "OK"
    if sharpe < 2.0:
        return "GOOD"
    return "EXCELLENT"


@router.get("/api/strategies/backtest-data")
async def api_strategies_backtest_data(force_refresh: bool = False):
    """Return cached backtest results for all 4 strategies on 25 symbols (2015-2026)."""
    now = time.time()
    if not force_refresh and BACKTEST_CACHE_PATH.exists():
        try:
            data = json.loads(BACKTEST_CACHE_PATH.read_text())
            age = now - data.get("_ts", 0)
            if age < BACKTEST_CACHE_TTL:
                return {k: v for k, v in data.items() if k != "_ts"}
        except Exception:
            pass

    # Run backtest and collect structured data
    from app.strategies.backtest import (
        SYMBOLS, backtest_momentum, backtest_reversion,
        backtest_breakout, backtest_swing, fetch_data
    )
    import numpy as np

    spy_df = fetch_data("SPY")
    if spy_df is None:
        return {"error": "Cannot fetch SPY data"}

    strategies_data = {
        "Momentum": {"func": backtest_momentum, "extra": spy_df},
        "Mean Reversion": {"func": backtest_reversion, "extra": None},
        "Breakout": {"func": backtest_breakout, "extra": None},
        "Swing": {"func": backtest_swing, "extra": spy_df},
    }

    result = {"strategies": {}, "symbols": {}}
    for symbol in SYMBOLS:
        df = fetch_data(symbol)
        if df is None:
            continue
        result["symbols"][symbol] = {}
        for sname, cfg in strategies_data.items():
            try:
                if cfg["extra"] is not None:
                    r = cfg["func"](df, cfg["extra"], symbol)
                else:
                    r = cfg["func"](df, symbol)
                quality = _quality_flag(r.sharpe, r.profit_factor, r.win_rate)
                result["symbols"][symbol][sname] = {
                    "trades": r.total_trades,
                    "win_rate": r.win_rate,
                    "avg_return": r.avg_return,
                    "total_return": r.total_return,
                    "sharpe": r.sharpe,
                    "max_dd": r.max_drawdown,
                    "avg_hold": r.avg_hold_days,
                    "profit_factor": r.profit_factor,
                    "quality": quality,
                }
            except Exception:
                pass

    # Per-strategy averages
    for sname in strategies_data:
        sym_data = {sym: result["symbols"][sym].get(sname) for sym in result["symbols"]
                    if sname in result["symbols"][sym] and result["symbols"][sym][sname]["trades"] > 0}
        sym_data = {k: v for k, v in sym_data.items() if v}
        if not sym_data:
            continue
        trades_total = sum(v["trades"] for v in sym_data.values())
        wr = float(np.mean([v["win_rate"] for v in sym_data.values()]))
        sh = float(np.mean([v["sharpe"] for v in sym_data.values()]))
        dd = float(np.mean([v["max_dd"] for v in sym_data.values()]))
        ret = float(np.mean([v["avg_return"] for v in sym_data.values()]))
        pf = float(np.mean([v["profit_factor"] for v in sym_data.values()]))
        result["strategies"][sname] = {
            "total_trades": trades_total,
            "avg_win_rate": round(wr, 1),
            "avg_sharpe": round(sh, 2),
            "avg_max_dd": round(dd, 2),
            "avg_return": round(ret, 2),
            "avg_profit_factor": round(pf, 2),
            "symbol_count": len(sym_data),
            "pass_win_rate": wr > 50,
            "pass_sharpe": sh > 1.5,
            "pass_max_dd": dd > -20,
        }

    # Best symbol per strategy
    for sname in strategies_data:
        best_sym = None
        best_sharpe = -999
        for sym, sdata in result["symbols"].items():
            sd = sdata.get(sname)
            if sd and sd["trades"] >= 5 and sd["sharpe"] > best_sharpe:
                best_sharpe = sd["sharpe"]
                best_sym = sym
        if best_sym:
            result["strategies"][sname]["best_symbol"] = best_sym
            result["strategies"][sname]["best_symbol_sharpe"] = best_sharpe

    # Overall verdict
    passes = [v.get("pass_sharpe", False) for v in result["strategies"].values()]
    result["verdict"] = "all_pass" if all(passes) else "needs_improvement"
    result["symbol_count"] = len(result["symbols"])

    # Cache
    BACKTEST_CACHE_PATH.parent.mkdir(exist_ok=True)
    result["_ts"] = now
    BACKTEST_CACHE_PATH.write_text(json.dumps(result, default=str))

    return {k: v for k, v in result.items() if k != "_ts"}


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


@router.post("/api/model/promote/{name}")
async def api_promote_model(name: str):
    """Promote a staging model to production."""
    from app.services.model_registry import resolve, promote_to_production
    staging = resolve(name, "staging")
    if not staging:
        return {"success": False, "error": f"No staging version for '{name}'"}
    promote_to_production(name, staging["version"], staging["artifact_path"], staging.get("metrics"))
    return {"success": True, "name": name, "version": staging["version"]}
