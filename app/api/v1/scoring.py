from __future__ import annotations

import json
import logging

from fastapi import APIRouter

from app.services.redis_client import get_redis

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-scoring"])


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

    return plan
