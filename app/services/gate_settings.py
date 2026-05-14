"""In-memory gate settings with user overrides.

Singleton pattern — stores user-defined min_gate / strong_gate values
that override the GATE_CONFIG defaults in scoring.py. Resets on
application restart (DB persistence deferred to Phase B).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger("screener")

_DEFAULT_MIN_GATE = 60
_DEFAULT_STRONG_GATE = 75


class _GateSettings:
    """Thread-safe singleton for dynamic gate thresholds."""

    _instance: Optional[_GateSettings] = None
    _lock = threading.Lock()

    def __new__(cls) -> _GateSettings:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._min_gate = _DEFAULT_MIN_GATE
                    cls._instance._strong_gate = _DEFAULT_STRONG_GATE
                    cls._instance._user_overridden = False
        return cls._instance

    @property
    def min_gate(self) -> int:
        return self._min_gate

    @property
    def strong_gate(self) -> int:
        return self._strong_gate

    @property
    def is_overridden(self) -> bool:
        return self._user_overridden

    def update(self, min_gate: int, strong_gate: int) -> None:
        min_gate = max(0, min(min_gate, 100))
        strong_gate = max(0, min(strong_gate, 100))
        self._min_gate = min_gate
        self._strong_gate = strong_gate
        self._user_overridden = True
        logger.info(
            "Gate settings updated: min_gate=%d, strong_gate=%d",
            min_gate, strong_gate,
        )

    def reset(self) -> None:
        self._min_gate = _DEFAULT_MIN_GATE
        self._strong_gate = _DEFAULT_STRONG_GATE
        self._user_overridden = False
        logger.info("Gate settings reset to defaults")


def get_gate_settings() -> _GateSettings:
    return _GateSettings()


def get_active_gates() -> dict:
    """Return current gate values as a dict (for API responses)."""
    s = get_gate_settings()
    return {
        "min_gate": s.min_gate,
        "strong_gate": s.strong_gate,
        "user_overridden": s.is_overridden,
        "defaults": {"min_gate": _DEFAULT_MIN_GATE, "strong_gate": _DEFAULT_STRONG_GATE},
    }


__all__ = ["get_gate_settings", "get_active_gates"]
