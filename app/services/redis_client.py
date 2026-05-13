"""Async Redis client with lazy singleton init and graceful fallback.

Usage:
    from app.services.redis_client import get_redis

    redis = await get_redis()
    if redis is not None:
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)

Returns None when REDIS_URL is unset or Redis is unreachable.
The client is created once and reused across all requests.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("screener")

_redis_client: Optional["Redis"] = None


def _build_redis_url() -> str:
    return os.environ.get("REDIS_URL", "")


async def get_redis() -> Optional["Redis"]:
    """Get the singleton async Redis client, or None if unavailable."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    url = _build_redis_url()
    if not url:
        return None
    try:
        from redis.asyncio import from_url

        _redis_client = await from_url(url, decode_responses=True)
        await _redis_client.ping()
        logger.info("Redis connected — caching enabled")
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable — caching disabled: %s", exc)
        return None


async def cached_or_compute(
    redis_key: str,
    ttl_seconds: int,
    compute_func,
    force_refresh: bool = False,
    json_encoder=None,
):
    """Fetch from Redis cache or compute-and-store.

    Args:
        redis_key: Cache key (e.g. ``market:context``)
        ttl_seconds: TTL in seconds
        compute_func: Async callable returning a JSON-serialisable dict
        force_refresh: Skip cache and re-compute
        json_encoder: Optional custom JSON encoder (default ``str``)

    Returns:
        The computed (or cached) data dict.
    """
    if not force_refresh:
        redis = await get_redis()
        if redis is not None:
            try:
                cached = await redis.get(redis_key)
                if cached is not None:
                    return json.loads(cached)
            except Exception as exc:
                logger.debug("Redis cache read error for %s: %s", redis_key, exc)

    data = await compute_func()

    redis = await get_redis()
    if redis is not None:
        try:
            await redis.setex(redis_key, ttl_seconds, json.dumps(data, default=json_encoder or str))
        except Exception as exc:
            logger.debug("Redis cache write error for %s: %s", redis_key, exc)

    return data
