"""Structured logging configuration with request-id propagation.

Replaces plain `logging` with `structlog` for:
  - JSON-formatted log lines (machine-parseable)
  - Automatic request-id injection via ASGI middleware
  - Correlation IDs propagated to DB rows and Telegram messages

Usage in app entry point:
    from app.logging_config import setup_logging, RequestIDMiddleware
    setup_logging()
    app.add_middleware(RequestIDMiddleware)
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict
from typing import Callable

# ---------------------------------------------------------------------------
# Rate limiter: prevents log storms from exceeding Railway's 500 logs/sec cap
# ---------------------------------------------------------------------------

class RateLimitFilter(logging.Filter):
    """Only emit one log record per *key* every *interval* seconds.

    Keys are derived from ``(name, levelno, msg)`` so repeated error
    messages (e.g. timeout storms) are collapsed into a single log line
    per interval.
    """

    def __init__(self, interval: float = 5.0) -> None:
        super().__init__()
        self._interval = interval
        self._history: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        key = f"{record.name}:{record.levelno}:{record.msg}"
        now = time.monotonic()
        last = self._history.get(key, 0.0)
        if now - last < self._interval:
            return False
        self._history[key] = now
        return True


# ---------------------------------------------------------------------------
# Structured logging (structlog)
# ---------------------------------------------------------------------------


def setup_logging(log_level: str | None = None) -> None:
    """Configure structlog as the global logging framework.

    Falls back to plain logging if structlog is not installed.
    """
    level = (log_level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.dev.ConsoleRenderer() if os.environ.get("DEVELOPMENT")
                else structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        # Redirect standard logging to structlog
        logging.basicConfig(
            format="%(message)s",
            level=getattr(logging, level, logging.INFO),
            force=True,
        )

        # Attach rate limiter to the root logger (applies to all children)
        for handler in logging.root.handlers:
            handler.addFilter(RateLimitFilter(interval=5.0))

        logger = structlog.get_logger("app")
        logger.info("Structured logging initialized", log_level=level)

    except ImportError:
        # Fallback: plain logging
        logging.basicConfig(
            level=getattr(logging, level, logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            force=True,
        )
        for handler in logging.root.handlers:
            handler.addFilter(RateLimitFilter(interval=5.0))
        logging.getLogger("app").info("structlog not available — using plain logging")


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware:
    """ASGI middleware that injects a unique request_id into each request.

    The request_id is:
      - Added to the ASGI scope (scope["request_id"])
      - Injected into structlog context vars
      - Sent as X-Request-ID response header
      - Available for propagation to DB rows and Telegram messages
    """

    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Generate or accept inbound request_id
        req_id = None
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"x-request-id":
                req_id = header_value.decode("utf-8", errors="replace")
                break

        if not req_id:
            req_id = str(uuid.uuid4())

        scope["request_id"] = req_id

        # Inject into structlog context
        try:
            import structlog
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(request_id=req_id)
        except ImportError:
            pass

        # Wrap send to inject response header
        original_send = send

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", req_id.encode()))
                message["headers"] = headers
            await original_send(message)

        await self.app(scope, receive, send_with_header)


# ---------------------------------------------------------------------------
# Convenience: get current request_id from anywhere
# ---------------------------------------------------------------------------


def get_request_id() -> str:
    """Return the current request_id from structlog context, or empty string."""
    try:
        import structlog
        return structlog.contextvars.get_contextvars().get("request_id", "")
    except ImportError:
        return ""
