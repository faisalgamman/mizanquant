"""Professional Dashboard — delegates to V1 API modules.

All business logic lives in ``app/api/v1/``. This file re-exports
endpoints under the legacy ``/api/*`` paths so existing dashboard
HTML and third-party consumers work without changes.
"""
from __future__ import annotations

import logging

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


@router.post("/api/model/promote/{name}")
async def api_promote_model(name: str):
    from app.services.model_registry import resolve, promote_to_production
    staging = resolve(name, "staging")
    if not staging:
        return {"success": False, "error": f"No staging version for '{name}'"}
    promote_to_production(name, staging["version"], staging["artifact_path"], staging.get("metrics"))
    return {"success": True, "name": name, "version": staging["version"]}
