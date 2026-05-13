from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter

from app.services.redis_client import get_redis

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-market"])


@router.get("/market/context")
async def v1_market_context(force_refresh: bool = False):
    from app.services.market_context import get_market_context

    redis = await get_redis()
    cache_key = "market:context"

    if not force_refresh and redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:
            logger.debug("Redis read error for %s: %s", cache_key, exc)

    try:
        data = await asyncio.wait_for(
            asyncio.to_thread(get_market_context, False),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.warning("/api/v1/market/context exceeded 10s — returning empty stale shell")
        return {
            "vix": {}, "spy_regime": {}, "breadth": {}, "credit": {}, "liquidity": {},
            "stale": True, "error": "timeout",
            "cached_at": datetime.utcnow().isoformat(),
        }

    if redis is not None:
        try:
            await redis.setex(cache_key, 300, json.dumps(data, default=str))
        except Exception as exc:
            logger.debug("Redis write error for %s: %s", cache_key, exc)

    return data


@router.get("/market/status")
async def v1_market_status(force_refresh: bool = False):
    from app.services.market_context import get_market_status
    return get_market_status(force_refresh=force_refresh)


@router.get("/sectors/performance")
async def v1_sector_performance(force_refresh: bool = False):
    from app.services.sector_analysis import get_sector_performance

    redis = await get_redis()
    cache_key = "sectors:performance"

    if not force_refresh and redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:
            logger.debug("Redis read error for %s: %s", cache_key, exc)

    data = get_sector_performance(force_refresh=False)

    if redis is not None:
        try:
            await redis.setex(cache_key, 600, json.dumps(data, default=str))
        except Exception as exc:
            logger.debug("Redis write error for %s: %s", cache_key, exc)

    return data
