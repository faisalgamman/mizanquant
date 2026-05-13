"""In-memory TTL cache — drop-in fallback when Redis is unavailable.

Usage:
    from app.services.memory_cache import memory_cache

    data = await memory_cache.get_or_compute("market:context", 300, compute_func)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("screener")


class _TTLCache:
    """Thread-safe in-memory cache with per-key TTL."""

    def __init__(self) -> None:
        self._store: Dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    def _is_expired(self, key: str) -> bool:
        if key not in self._store:
            return True
        _, expires_at = self._store[key]
        return time.monotonic() > expires_at

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            if key in self._store and not self._is_expired(key):
                return self._store[key][0]
            return None

    async def set(self, key: str, value: Any, ttl: float) -> None:
        async with self._lock:
            self._store[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    async def cleanup(self) -> int:
        async with self._lock:
            now = time.monotonic()
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            return len(expired)

    @property
    def size(self) -> int:
        return len(self._store)


class MemoryCacheService:
    """High-level cache service that tries Redis first, falls back to in-memory."""

    def __init__(self) -> None:
        self._local = _TTLCache()
        self._redis_available: Optional[bool] = None

    async def _try_redis(self) -> Optional[Any]:
        try:
            from app.services.redis_client import get_redis
            redis = await get_redis()
            return redis
        except Exception:
            return None

    async def get_or_compute(
        self,
        key: str,
        ttl_seconds: int,
        compute_func: Callable,
        force_refresh: bool = False,
        json_encoder: Optional[Callable] = None,
    ) -> Any:
        if not force_refresh:
            redis = await self._try_redis()
            if redis is not None:
                try:
                    cached = await redis.get(key)
                    if cached is not None:
                        return json.loads(cached)
                except Exception:
                    pass
            else:
                local_val = await self._local.get(key)
                if local_val is not None:
                    return local_val

        data = await compute_func()

        redis = await self._try_redis()
        if redis is not None:
            try:
                await redis.setex(key, ttl_seconds, json.dumps(data, default=json_encoder or str))
            except Exception:
                pass
        else:
            await self._local.set(key, data, ttl_seconds)

        return data

    async def invalidate(self, key: str) -> None:
        redis = await self._try_redis()
        if redis is not None:
            try:
                await redis.delete(key)
            except Exception:
                pass
        await self._local.delete(key)

    async def invalidate_pattern(self, pattern: str) -> int:
        count = 0
        redis = await self._try_redis()
        if redis is not None:
            try:
                keys = []
                async for k in redis.scan_iter(match=pattern):
                    keys.append(k)
                if keys:
                    await redis.delete(*keys)
                    count += len(keys)
            except Exception:
                pass
        async with self._local._lock:
            prefix = pattern.rstrip("*")
            to_del = [k for k in self._local._store if k.startswith(prefix)]
            for k in to_del:
                del self._local._store[k]
            count += len(to_del)
        return count

    async def cleanup(self) -> int:
        return await self._local.cleanup()

    @property
    def stats(self) -> dict:
        return {
            "local_size": self._local.size,
            "redis_available": self._redis_available,
        }


memory_cache = MemoryCacheService()