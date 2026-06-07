from __future__ import annotations

import json
import logging
import math

from fastapi import APIRouter

from app.services.redis_client import get_redis

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-scoring"])


def _to_jsonable(obj):
    """Recursively coerce numpy/pandas scalars to JSON-native types.

    FastAPI's ``jsonable_encoder`` cannot serialize numpy scalars — e.g. a
    ``numpy.bool_`` (which it tries to ``dict()``/``vars()`` and 500s on). The
    strategy ``details`` dict embedded in the trade plan can carry such values,
    so we sanitize the whole payload before returning. NaN/Inf floats → None.
    """
    import numpy as np

    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.ndarray):
        return [_to_jsonable(v) for v in obj.tolist()]
    if hasattr(obj, "item"):  # any other numpy scalar
        try:
            return _to_jsonable(obj.item())
        except Exception:
            return str(obj)
    return obj


@router.get("/scoring/weighted")
async def v1_weighted_score(symbol: str = "AAPL"):
    from app.services.market_data import fetch
    from app.services.scoring import weighted_score, _score_to_dict

    redis = await get_redis()
    cache_key = f"scoring:weighted:{symbol.upper()}"

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:
            logger.debug("Redis read error for %s: %s", cache_key, exc)

    df = fetch(symbol, period="6mo")
    if df is None:
        return {"error": f"No data for {symbol}"}
    spy_df = fetch("SPY", period="6mo")
    result = weighted_score(df, spy_df=spy_df)
    result_dict = _score_to_dict(result)
    result_dict["symbol"] = symbol.upper()

    if redis is not None:
        try:
            await redis.setex(cache_key, 300, json.dumps(result_dict, default=str))
        except Exception as exc:
            logger.debug("Redis write error for %s: %s", cache_key, exc)

    return result_dict


@router.get("/trade/plan")
async def v1_trade_plan(symbol: str = "AAPL", portfolio: float = 100000.0):
    from app.services.market_data import fetch
    from app.services.trade_plan import generate_trade_plan

    df = fetch(symbol, period="6mo")
    if df is None:
        return {"error": f"No data for {symbol}"}
    plan = generate_trade_plan(df, portfolio_equity=portfolio)
    plan["symbol"] = symbol.upper()

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

    # Make the per-strategy exit consistent with the validated Option-A plan from
    # generate_trade_plan (fixed 15% catastrophe stop + 20-day time exit). The UI
    # prefers strategy_* fields, so align them here; otherwise it would show the
    # strategy's own (tighter) stop and contradict the validated exit policy.
    if "stop_loss" in plan and "error" not in plan:
        plan["strategy_stop"] = plan["stop_loss"]
        for _k in ("tp1", "tp2", "tp3"):
            if plan.get(_k) is not None:
                plan["strategy_" + _k] = plan[_k]

    # Sanitize numpy scalars (e.g. numpy.bool_ inside sig.details) so FastAPI's
    # encoder can serialize the response instead of 500-ing.
    return _to_jsonable(plan)
