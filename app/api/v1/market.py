from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter

from app.services.memory_cache import memory_cache

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-market"])


@router.get("/market/context")
async def v1_market_context(force_refresh: bool = False):
    from app.services.market_context import get_market_context

    async def compute():
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(get_market_context, False),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            logger.warning("/api/v1/market/context exceeded 25s — returning stale shell")
            return {
                "vix": {}, "spy_regime": {}, "breadth": {}, "credit": {}, "liquidity": {},
                "stale": True, "error": "timeout",
                "cached_at": datetime.utcnow().isoformat(),
            }

    return await memory_cache.get_or_compute(
        "market:context", 300, compute, force_refresh=force_refresh
    )


@router.get("/market/status")
async def v1_market_status(force_refresh: bool = False):
    from app.services.market_context import get_market_status
    return get_market_status(force_refresh=force_refresh)


@router.get("/sectors/performance")
async def v1_sector_performance(force_refresh: bool = False):
    from app.services.sector_analysis import get_sector_performance

    def compute():
        return get_sector_performance(force_refresh=False)

    return await memory_cache.get_or_compute(
        "sectors:performance", 600, compute, force_refresh=force_refresh
    )
