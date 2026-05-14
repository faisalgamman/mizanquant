"""Professional Dashboard — delegates to V1 API modules.

All business logic lives in ``app/api/v1/``. This file re-exports
endpoints under the legacy ``/api/*`` paths so existing dashboard
HTML and third-party consumers work without changes.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter

from app.api.v1.system import (
    _dashboard_health,
    v1_scheduler_status as _scheduler_status,
    v1_block_status as _block_status,
    v1_symbols_universe as _symbols_universe,
    v1_symbols_search as _symbols_search,
)
from app.api.v1.system import v1_system_status as _system_status
from app.api.v1.market import (
    v1_market_context as _market_context,
    v1_market_status as _market_status,
    v1_sector_performance as _sector_performance,
)
from app.api.v1.trading import (
    v1_trading_summary as _trading_summary,
    v1_trading_controls as _trading_controls,
    v1_kill_switch as _kill_switch,
    v1_auto_trade as _auto_trade,
    v1_trading_history as _trading_history,
    v1_halal_check as _halal_check,
    v1_halal_universe as _halal_universe,
    v1_backtest as _backtest,
)
from app.api.v1.pipeline import (
    v1_pipeline_status as _pipeline_status,
    v1_pipeline_run as _pipeline_run,
)
from app.api.v1.guards import (
    v1_guards_recent as _guards_recent,
    v1_guards_summary as _guards_summary,
)
from app.api.v1.scoring import (
    v1_weighted_score as _weighted_score,
    v1_trade_plan as _trade_plan,
)
from app.api.v1.watchlist import (
    v1_get_watchlist as _get_watchlist,
    v1_set_watchlist as _set_watchlist,
    v1_add_to_watchlist as _add_to_watchlist,
    v1_remove_from_watchlist as _remove_from_watchlist,
)

logger = logging.getLogger("screener")

router = APIRouter(tags=["Dashboard"])

# ── Legacy aliases — all delegate to V1 ──

router.get("/api/system/status")(_system_status)
router.get("/api/scheduler/status")(_scheduler_status)
router.get("/api/block/status")(_block_status)
router.get("/api/symbols/universe")(_symbols_universe)
router.get("/api/symbols/search")(_symbols_search)

router.get("/api/market/context")(_market_context)
router.get("/api/market/status")(_market_status)
router.get("/api/sectors/performance")(_sector_performance)

router.get("/api/trading/summary")(_trading_summary)
router.get("/api/trading/controls")(_trading_controls)
router.post("/api/trading/controls/kill-switch")(_kill_switch)
router.post("/api/trading/controls/auto-trade")(_auto_trade)
router.get("/api/trading/history/recent")(_trading_history)
router.get("/api/halal/check")(_halal_check)
router.get("/api/halal/universe")(_halal_universe)
router.get("/api/backtest")(_backtest)

router.get("/api/pipeline/status")(_pipeline_status)
router.get("/api/pipeline/run")(_pipeline_run)

router.get("/api/guards/recent")(_guards_recent)
router.get("/api/guards/summary")(_guards_summary)

router.get("/api/scoring/weighted")(_weighted_score)
router.get("/api/trade/plan")(_trade_plan)

# ── Gate Settings (dynamic overrides) ──


@router.get("/api/settings/gates")
async def api_get_gate_settings():
    from app.services.gate_settings import get_active_gates
    return get_active_gates()


@router.put("/api/settings/gates")
async def api_update_gate_settings(body: dict):
    from app.services.gate_settings import get_gate_settings
    mg = body.get("min_gate", 60)
    sg = body.get("strong_gate", 75)
    gs = get_gate_settings()
    gs.update(mg, sg)
    try:
        from app.services.notify import send_message
        send_message(
            f"\u2699\ufe0f Gate Settings Updated\n"
            f"Min: {gs.min_gate}\nStrong: {gs.strong_gate}\nActive until reset"
        )
    except Exception:
        pass
    return {"status": "ok", "min_gate": gs.min_gate, "strong_gate": gs.strong_gate}


@router.post("/api/settings/gates/reset")
async def api_reset_gate_settings():
    from app.services.gate_settings import get_gate_settings
    gs = get_gate_settings()
    gs.reset()
    try:
        from app.services.notify import send_message
        send_message(f"\u2699\ufe0f Gate Settings Reset to defaults (min={gs.min_gate}, strong={gs.strong_gate})")
    except Exception:
        pass
    return {"status": "ok", "min_gate": gs.min_gate, "strong_gate": gs.strong_gate}

router.get("/api/watchlist")(_get_watchlist)
router.put("/api/watchlist")(_set_watchlist)
router.post("/api/watchlist/add/{symbol}")(_add_to_watchlist)
router.delete("/api/watchlist/remove/{symbol}")(_remove_from_watchlist)


# ── Endpoints without V1 equivalents yet ──

BACKTEST_CACHE_PATH = Path(__file__).parent.parent / ".cache" / "backtest_summary.json"
BACKTEST_CACHE_TTL = 3600


def _quality_flag(sharpe: float, profit_factor: float, win_rate: float) -> str:
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
    now = time.time()

    # Redis cache (primary, 1h TTL)
    from app.services.redis_client import get_redis

    redis = await get_redis()
    cache_key = "strategies:backtest-data"

    if not force_refresh and redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:
            logger.debug("Redis read error for %s: %s", cache_key, exc)

    # File cache fallback
    if not force_refresh and BACKTEST_CACHE_PATH.exists():
        try:
            data = json.loads(BACKTEST_CACHE_PATH.read_text())
            age = now - data.get("_ts", 0)
            if age < BACKTEST_CACHE_TTL:
                return {k: v for k, v in data.items() if k != "_ts"}
        except Exception:
            pass

    from app.strategies.backtest import (
        SYMBOLS, backtest_momentum, backtest_reversion,
        backtest_breakout, backtest_swing, backtest_momentum_burst, fetch_data,
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
        "Momentum Burst": {"func": backtest_momentum_burst, "extra": spy_df},
    }

    result = {"strategies": {}, "symbols": {}}
    for symbol in SYMBOLS:
        df = fetch_data(symbol)
        if df is None:
            continue
        result["symbols"][symbol] = {}
        for sname, cfg in strategies_data.items():
            try:
                r = cfg["func"](df, cfg["extra"], symbol) if cfg["extra"] is not None else cfg["func"](df, symbol)
                quality = _quality_flag(r.sharpe, r.profit_factor, r.win_rate)
                result["symbols"][symbol][sname] = {
                    "trades": r.total_trades, "win_rate": r.win_rate,
                    "avg_return": r.avg_return, "total_return": r.total_return,
                    "sharpe": r.sharpe, "max_dd": r.max_drawdown,
                    "avg_hold": r.avg_hold_days, "profit_factor": r.profit_factor,
                    "quality": quality,
                }
            except Exception:
                pass

    for sname in strategies_data:
        sym_data = {
            sym: result["symbols"][sym].get(sname)
            for sym in result["symbols"]
            if sname in result["symbols"][sym] and result["symbols"][sym][sname]["trades"] > 0
        }
        sym_data = {k: v for k, v in sym_data.items() if v}
        if not sym_data:
            continue
        result["strategies"][sname] = {
            "total_trades": sum(v["trades"] for v in sym_data.values()),
            "avg_win_rate": round(float(np.mean([v["win_rate"] for v in sym_data.values()])), 1),
            "avg_sharpe": round(float(np.mean([v["sharpe"] for v in sym_data.values()])), 2),
            "avg_max_dd": round(float(np.mean([v["max_dd"] for v in sym_data.values()])), 2),
            "avg_return": round(float(np.mean([v["avg_return"] for v in sym_data.values()])), 2),
            "avg_profit_factor": round(float(np.mean([v["profit_factor"] for v in sym_data.values()])), 2),
            "symbol_count": len(sym_data),
            "pass_win_rate": float(np.mean([v["win_rate"] for v in sym_data.values()])) > 50,
            "pass_sharpe": float(np.mean([v["sharpe"] for v in sym_data.values()])) > 1.5,
            "pass_max_dd": float(np.mean([v["max_dd"] for v in sym_data.values()])) > -20,
        }

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

    passes = [v.get("pass_sharpe", False) for v in result["strategies"].values()]
    result["verdict"] = "all_pass" if all(passes) else "needs_improvement"
    result["symbol_count"] = len(result["symbols"])

    BACKTEST_CACHE_PATH.parent.mkdir(exist_ok=True)
    result["_ts"] = now
    BACKTEST_CACHE_PATH.write_text(json.dumps(result, default=str))

    if redis is not None:
        try:
            clean = {k: v for k, v in result.items() if k != "_ts"}
            await redis.setex(cache_key, 3600, json.dumps(clean, default=str))
        except Exception as exc:
            logger.debug("Redis write error for %s: %s", cache_key, exc)

    return {k: v for k, v in result.items() if k != "_ts"}


@router.post("/api/model/promote/{name}")
async def api_promote_model(name: str):
    from app.services.model_registry import resolve, promote_to_production
    staging = resolve(name, "staging")
    if not staging:
        return {"success": False, "error": f"No staging version for '{name}'"}
    promote_to_production(name, staging["version"], staging["artifact_path"], staging.get("metrics"))
    return {"success": True, "name": name, "version": staging["version"]}
