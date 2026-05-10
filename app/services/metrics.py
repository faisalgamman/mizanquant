"""Lightweight runtime instrumentation — counters, timings, cache stats.

Usage::

    from app.services.metrics import metrics

    metrics.incr("api_calls_total", provider="alpaca")
    with metrics.timing("scan_duration"):
        run_screener()
    print(metrics.snapshot())
"""

from __future__ import annotations

import functools
import time
from collections import defaultdict
from contextlib import contextmanager
from threading import Lock
from typing import Any


class Metrics:
    """Thread-safe metrics collector with counters, timings, and hit-rate stats."""

    def __init__(self):
        self._lock = Lock()
        # Counters: label_key -> int
        self._counters: dict[str, int] = defaultdict(int)
        # Timings: label_key -> list of durations
        self._timings: dict[str, list[float]] = defaultdict(list)
        # Cache: {layer: {"hits": int, "misses": int}}
        self._cache: dict[str, dict[str, int]] = defaultdict(
            lambda: {"hits": 0, "misses": 0}
        )

    # ── Counters ──

    def incr(self, name: str, **labels: str) -> None:
        """Increment a named counter with optional label dimensions."""
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += 1

    def count(self, name: str, **labels: str) -> int:
        """Read a counter value."""
        return self._counters.get(self._key(name, labels), 0)

    # ── Timings ──

    @contextmanager
    def timing(self, name: str, **labels: str):
        """Context manager that records elapsed seconds."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            key = self._key(name, labels)
            with self._lock:
                self._timings[key].append(elapsed)

    def record_timing(self, name: str, elapsed: float, **labels: str) -> None:
        """Manually record a timing value."""
        key = self._key(name, labels)
        with self._lock:
            self._timings[key].append(elapsed)

    def timing_stats(self, name: str, **labels: str) -> dict[str, float]:
        """Return count, mean, min, max, p95 for a timing metric."""
        vals = self._timings.get(self._key(name, labels), [])
        if not vals:
            return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0, "p95": 0.0}
        sorted_vals = sorted(vals)
        n = len(sorted_vals)
        idx95 = max(0, int(n * 0.95) - 1)
        return {
            "count": n,
            "mean": sum(sorted_vals) / n,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "p95": sorted_vals[idx95],
        }

    # ── Cache hit/miss ──

    def cache_hit(self, layer: str) -> None:
        with self._lock:
            self._cache[layer]["hits"] += 1

    def cache_miss(self, layer: str) -> None:
        with self._lock:
            self._cache[layer]["misses"] += 1

    def cache_rate(self, layer: str) -> float:
        stats = self._cache.get(layer, {"hits": 0, "misses": 0})
        total = stats["hits"] + stats["misses"]
        return stats["hits"] / total if total > 0 else 0.0

    def cache_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                layer: {
                    "hits": s["hits"],
                    "misses": s["misses"],
                    "rate": round(
                        s["hits"] / (s["hits"] + s["misses"]), 4
                    ) if (s["hits"] + s["misses"]) > 0 else 0.0,
                }
                for layer, s in sorted(self._cache.items())
            }

    # ── Snapshot ──

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable snapshot of all metrics."""
        with self._lock:
            timings_summary = {}
            for key in list(self._timings.keys()):
                vals = self._timings[key]
                n = len(vals)
                sorted_vals = sorted(vals)
                idx95 = max(0, int(n * 0.95) - 1)
                timings_summary[key] = {
                    "count": n,
                    "mean": round(sum(sorted_vals) / n, 4) if n else 0.0,
                    "p95": round(sorted_vals[idx95], 4) if n else 0.0,
                }

        return {
            "counters": dict(sorted(self._counters.items())),
            "timings": timings_summary,
            "cache": self.cache_stats(),
        }

    def reset(self) -> None:
        """Clear all metrics (for tests)."""
        with self._lock:
            self._counters.clear()
            self._timings.clear()
            self._cache.clear()

    # ── Helpers ──

    @staticmethod
    def _key(name: str, labels: dict[str, str]) -> str:
        if not labels:
            return name
        parts = [name]
        for k, v in sorted(labels.items()):
            parts.append(f"{k}={v}")
        return "|".join(parts)


# Global singleton
metrics = Metrics()


# ── Decorator convenience ──


def timed(name: str, **labels: str):
    """Decorator that records execution time of a function."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with metrics.timing(name, **labels):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def counted(name: str, **labels: str):
    """Decorator that increments a counter each call."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            metrics.incr(name, **labels)
            return func(*args, **kwargs)
        return wrapper
    return decorator
