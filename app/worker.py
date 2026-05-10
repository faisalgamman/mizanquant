"""Standalone scheduler worker process.

Decouples the scheduler from the API container so that:
  - A deploy or OOM on the API does not kill scheduled jobs
  - Intraday scans, fill-watching, retrains do not compete with HTTP serving
  - Horizontal scaling is possible without duplicate scheduler firings
  - Blocking calls inside guards/strategies do not stall request latency

Deployment (Railway):
  Add a second service with start command:  python -m app.worker
  Set WORKER_SERVICE=true in its environment.
  Both API and worker share the same DATABASE_URL.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ---------------------------------------------------------------------------
# Early imports — must happen before any service code
# ---------------------------------------------------------------------------

from app.config import settings
from app.core.config import app_cfg

# ── Sentry ──
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=os.environ.get("ENVIRONMENT", "production"),
            traces_sample_rate=0.1,
        )
        logging.getLogger("worker").info("Sentry initialized for worker")
    except Exception:
        pass

# ── Structured logging ──
_worker_logger = logging.getLogger("worker")
_worker_logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))

# ── Redis lock for exactly-once scheduling ──
_REDIS_URL = os.environ.get("REDIS_URL", "")
_HAS_REDIS = False
_redis_client = None

if _REDIS_URL:
    try:
        import redis
        _redis_client = redis.from_url(_REDIS_URL, decode_responses=True)
        _redis_client.ping()
        _HAS_REDIS = True
        _worker_logger.info("Worker: Redis connected for scheduler locking")
    except Exception as exc:
        _worker_logger.warning("Worker: Redis unavailable (%s), falling back to no-op lock", exc)

_LOCK_KEY = "scheduler:leader"
_LOCK_TTL = 60  # seconds — re-acquire every cycle


def _acquire_leader_lock() -> bool:
    """Try to acquire the scheduler leader lock via Redis SET NX EX."""
    if not _HAS_REDIS or _redis_client is None:
        return True  # single-worker mode
    try:
        return bool(_redis_client.set(_LOCK_KEY, os.environ.get("HOSTNAME", "worker"), nx=True, ex=_LOCK_TTL))
    except Exception:
        return True


def _renew_leader_lock() -> None:
    """Renew the leader lock TTL so the current leader stays leader."""
    if _HAS_REDIS and _redis_client is not None:
        try:
            _redis_client.expire(_LOCK_KEY, _LOCK_TTL)
        except Exception:
            pass


def _release_leader_lock() -> None:
    """Release the leader lock on shutdown."""
    if _HAS_REDIS and _redis_client is not None:
        try:
            _redis_client.delete(_LOCK_KEY)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

_running = True


def _shutdown(signum, frame):
    global _running
    _worker_logger.info("Worker received signal %s, shutting down...", signum)
    _running = False
    _release_leader_lock()


def main():
    """Entry point: start scheduler, run until signal."""
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    _worker_logger.info("=" * 60)
    _worker_logger.info("Worker starting — scheduler process")
    _worker_logger.info("Redis lock: %s", "enabled" if _HAS_REDIS else "disabled (single-worker mode)")
    _worker_logger.info("=" * 60)

    # ── Acquire leadership ──
    if not _acquire_leader_lock():
        _worker_logger.warning("Another worker instance holds the leader lock — exiting")
        sys.exit(0)

    # ── Bootstrap (DB init, etc.) ──
    try:
        from app.db.database import init_db
        init_db()
        _worker_logger.info("Database initialized")
    except Exception as exc:
        _worker_logger.error("DB init failed: %s", exc)
        _release_leader_lock()
        sys.exit(1)

    # ── Start the scheduler ──
    try:
        from app.services.scheduler import start_scheduler, stop_scheduler
        start_scheduler()
        _worker_logger.info("Scheduler started — running background jobs")
    except Exception as exc:
        _worker_logger.error("Scheduler start failed: %s", exc)
        _release_leader_lock()
        sys.exit(1)

    # ── Main loop: renew lock + keep alive ──
    try:
        while _running:
            time.sleep(15)
            _renew_leader_lock()
    except KeyboardInterrupt:
        pass
    finally:
        _worker_logger.info("Worker shutting down...")
        try:
            stop_scheduler()
        except Exception:
            pass
        _release_leader_lock()
        _worker_logger.info("Worker stopped cleanly")


if __name__ == "__main__":
    main()
