"""v1 probabilistic price-forecast endpoint.

GET /api/v1/forecast/{symbol}?horizon=20&sims=1000 → Monte Carlo (GBM) expected
range + confidence bands + probability of profit, derived from the symbol's own
historical drift/volatility. Honest: a probability distribution, not a point
prediction. Read-only; cached 5 min in Redis (mirrors the scoring endpoint).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter

from app.services.redis_client import get_redis

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-forecast"])


@router.get("/forecast/{symbol}")
async def v1_forecast(symbol: str, horizon: int = 20, sims: int = 1000):
    from app.api.request_validators import validate_symbol, validate_range
    from app.services.market_data import fetch
    from app.services.price_forecast import monte_carlo_forecast

    sym = validate_symbol(symbol)
    validate_range(horizon, "horizon", 5, 60)
    validate_range(sims, "sims", 200, 2000)

    redis = await get_redis()
    cache_key = f"forecast:mc:{sym}:{horizon}:{sims}"
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:
            logger.debug("Redis read error for %s: %s", cache_key, exc)

    df = fetch(sym, period="1y")
    if df is None or "close" not in getattr(df, "columns", []):
        return {"symbol": sym, "error": f"No data for {sym}"}

    result = monte_carlo_forecast(df["close"].values, horizon=horizon, sims=sims)
    result["symbol"] = sym

    if redis is not None and "error" not in result:
        try:
            await redis.setex(cache_key, 300, json.dumps(result, default=str))
        except Exception as exc:
            logger.debug("Redis write error for %s: %s", cache_key, exc)

    return result
