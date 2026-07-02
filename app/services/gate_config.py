"""Persisted, reversible override for the weekly entry gate's MIN_RS threshold — the
"approved by evidence" knob of the self-calibrating gate.

SCOPE / SAFETY: this gates only what enters the PAPER-VALIDATION ledger (PV) — it never
touches a real broker order. Stored as JSON in CACHE_DIR so an approval survives restarts;
falls back to the ``WEEKLY_MIN_RS`` env default when unset. Every change is appended to an
audit history and is fully reversible (``reset_min_rs`` / delete the file).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_LOCK = threading.Lock()
_DEFAULT = -2.0
_MIN, _MAX = -20.0, 10.0   # sane bounds — a threshold outside this is almost surely a mistake


def _path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "gate_config.json")


def _read() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def get_min_rs() -> float:
    """The live MIN_RS threshold: the approved persisted value if present, else the
    ``WEEKLY_MIN_RS`` env default. Never raises."""
    cfg = _read()
    v = cfg.get("min_rs")
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(os.environ.get("WEEKLY_MIN_RS", str(_DEFAULT)))
    except (TypeError, ValueError):
        return _DEFAULT


def set_min_rs(value, *, evidence: dict | None = None, approved_by: str = "user") -> dict:
    """Approve a new MIN_RS (paper gate only). Persists it, sets the in-process env for the
    current workers, and appends an audit entry. Returns the new config state."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return {"error": f"invalid min_rs {value!r}"}
    if not (_MIN <= v <= _MAX):
        return {"error": f"min_rs {v} out of bounds [{_MIN}, {_MAX}]"}

    with _LOCK:
        cfg = _read()
        prev = cfg.get("min_rs")
        entry = {"at": datetime.now(timezone.utc).isoformat(), "from": prev, "to": v,
                 "approved_by": approved_by, "evidence": evidence or {}}
        history = list(cfg.get("history") or [])[-19:] + [entry]
        cfg = {"min_rs": v, "updated_at": entry["at"], "approved_by": approved_by,
               "evidence": evidence or {}, "history": history}
        try:
            with open(_path(), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("gate_config write failed: %s", e)
            return {"error": str(e)}
        os.environ["WEEKLY_MIN_RS"] = str(v)   # immediate effect for the running process
        logger.info("gate_config: MIN_RS %s → %s (by %s)", prev, v, approved_by)
        return cfg


def reset_min_rs() -> dict:
    """Revert to the env/default threshold (delete the persisted override, audited)."""
    with _LOCK:
        cfg = _read()
        prev = cfg.get("min_rs")
        try:
            if os.path.exists(_path()):
                os.remove(_path())
        except Exception as e:
            logger.error("gate_config reset failed: %s", e)
            return {"error": str(e)}
        os.environ.pop("WEEKLY_MIN_RS", None)
        logger.info("gate_config: MIN_RS reset (was %s) → env/default", prev)
        return {"reset": True, "was": prev, "now": get_min_rs()}


def gate_config_state() -> dict:
    """Current threshold + provenance (for display)."""
    cfg = _read()
    return {
        "min_rs": get_min_rs(),
        "source": "approved" if isinstance(cfg.get("min_rs"), (int, float)) else "default",
        "updated_at": cfg.get("updated_at"),
        "approved_by": cfg.get("approved_by"),
        "history": cfg.get("history") or [],
    }


__all__ = ["get_min_rs", "set_min_rs", "reset_min_rs", "gate_config_state"]
