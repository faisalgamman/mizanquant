"""Structured JSONL trade event logging."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "trades.jsonl"

_logger = logging.getLogger("trade_jsonl")
if not _logger.handlers:
    handler = RotatingFileHandler(LOG_PATH, maxBytes=50 * 1024 * 1024, backupCount=10, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def log_trade_event(event: str, **payload) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
    record.update(payload)
    _logger.info(json.dumps(record, sort_keys=True, default=str))
