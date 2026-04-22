"""Pytest configuration & fixtures shared across unit tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the project root importable as `app.*` when running `pytest` from repo root.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Force test-safe defaults BEFORE any app module is imported.
os.environ.setdefault("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
os.environ.setdefault("AUTO_TRADE_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("LOG_LEVEL", "WARNING")


@pytest.fixture(autouse=True)
def reset_runtime_state():
    """Keep singleton-like runtime state isolated between tests."""
    from app.core.config import app_cfg
    from app.services import notify, regime

    regime._last_confirmed = "NEUTRAL"
    regime._last_candidate = None
    regime._current_snapshot = None
    regime._forced_state = None
    notify._dedup.clear()
    app_cfg.killed = False
    app_cfg.risk.use_modular_guards = True
    yield


@pytest.fixture
def valid_entry_kwargs():
    """Canonical 'happy path' order inputs."""
    return dict(
        symbol="AAPL",
        price=180.0,
        stop_loss=175.0,
        take_profit=190.0,
        qty=10,
        side="buy",
    )
