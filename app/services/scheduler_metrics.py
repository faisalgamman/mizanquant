"""Scheduler metrics — tracks cycle timing, success/failure, and health.

Thread-safe; uses a Lock for all mutations.  Exposes a snapshot dict
that can be served via admin endpoints or included in Telegram reports.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass
class _CycleMetrics:
    last_start: Optional[datetime] = None
    last_end: Optional[datetime] = None
    last_duration: Optional[float] = None
    last_success: Optional[bool] = None
    last_error: Optional[str] = None
    total_runs: int = 0
    total_success: int = 0
    total_failures: int = 0


class SchedulerMetrics:
    """Aggregated metrics for all scheduler cycle types."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._uptime: Optional[datetime] = None
        self._cycles: Dict[str, _CycleMetrics] = {}
        self._cycle_types = [
            "pre_market",
            "market_scan",
            "post_market",
            "signals_scan",
            "optimizer",
            "train_models",
            "signal_audit",
            "reference_data",
        ]
        for name in self._cycle_types:
            self._cycles[name] = _CycleMetrics()
        # Ring buffer of recent errors (max 50)
        self._recent_errors: list[dict] = []

    # ── lifecycle ──

    def mark_started(self) -> None:
        with self._lock:
            if self._uptime is None:
                self._uptime = datetime.now(timezone.utc)

    def mark_stopped(self) -> None:
        with self._lock:
            self._uptime = None

    # ── per-cycle recording ──

    def record_cycle_start(self, cycle: str) -> None:
        with self._lock:
            metrics = self._cycles.get(cycle)
            if metrics is None:
                return
            metrics.last_start = datetime.now(timezone.utc)

    def record_cycle_end(
        self, cycle: str, success: bool, error: Optional[str] = None
    ) -> None:
        with self._lock:
            metrics = self._cycles.get(cycle)
            if metrics is None:
                return
            now = datetime.now(timezone.utc)
            metrics.last_end = now
            if metrics.last_start:
                metrics.last_duration = (now - metrics.last_start).total_seconds()
            metrics.last_success = success
            if error:
                metrics.last_error = error
            metrics.total_runs += 1
            if success:
                metrics.total_success += 1
            else:
                metrics.total_failures += 1
            if error:
                self._recent_errors.append(
                    {
                        "ts": now.isoformat(),
                        "cycle": cycle,
                        "error": error[:500],
                    }
                )
                if len(self._recent_errors) > 50:
                    self._recent_errors = self._recent_errors[-50:]

    def record_error(self, cycle: str, error: str) -> None:
        """Shorthand for record_cycle_end(cycle, success=False, error=error)."""
        self.record_cycle_end(cycle, success=False, error=error)

    # ── helpers for cycle wrappers (context-manager style) ──

    def run_cycle(self, cycle: str, fn, *args, **kwargs):
        """Execute *fn* wrapped with start/end recording."""
        self.record_cycle_start(cycle)
        try:
            result = fn(*args, **kwargs)
            self.record_cycle_end(cycle, success=True)
            return result
        except Exception as exc:
            self.record_cycle_end(cycle, success=False, error=str(exc))
            raise

    # ── snapshot ──

    def snapshot(self) -> dict:
        """Return a JSON-safe snapshot of all metrics."""
        with self._lock:
            cycles = {}
            for name, m in self._cycles.items():
                cycles[name] = {
                    "last_start": m.last_start.isoformat() if m.last_start else None,
                    "last_end": m.last_end.isoformat() if m.last_end else None,
                    "last_duration_s": round(m.last_duration, 2) if m.last_duration is not None else None,
                    "last_success": m.last_success,
                    "last_error": m.last_error,
                    "total_runs": m.total_runs,
                    "total_success": m.total_success,
                    "total_failures": m.total_failures,
                }
            return {
                "uptime": self._uptime.isoformat() if self._uptime else None,
                "cycles": cycles,
                "recent_errors": list(self._recent_errors),
            }

    def health(self) -> dict:
        """Lightweight health summary (no detailed cycle data)."""
        with self._lock:
            total_runs = sum(m.total_runs for m in self._cycles.values())
            total_ok = sum(m.total_success for m in self._cycles.values())
            total_err = sum(m.total_failures for m in self._cycles.values())
            last_error_count = len(self._recent_errors)
            failing_cycles = [
                name
                for name, m in self._cycles.items()
                if m.last_success is False
            ]
            return {
                "uptime": self._uptime.isoformat() if self._uptime else None,
                "total_runs": total_runs,
                "total_success": total_ok,
                "total_failures": total_err,
                "success_rate_pct": round(total_ok / total_runs * 100, 1) if total_runs else 100.0,
                "total_cycles": len(self._cycle_types),
                "last_error_count": last_error_count,
                "failing_cycles": failing_cycles,
            }


# Module-level singleton
scheduler_metrics = SchedulerMetrics()
