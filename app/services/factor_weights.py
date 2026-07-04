"""Persisted, reversible overrides for the composite-score factor weights.

SCOPE / SAFETY: these weights only change how the PAPER screener RANKS/SCORES stocks —
they never touch a real broker order. Stored as JSON in CACHE_DIR so an override survives
restarts; each weight falls back to its ``COMPOSITE_*_WEIGHT`` env value (else the coded
default) when unset. Every change is appended to an audit history and is fully reversible
(``reset_weights`` / delete the file). Mirrors :mod:`app.services.gate_config`.

The knobs are USER decisions — nothing here is auto-applied. get_weight() is fail-safe and
never raises, so a missing/corrupt file just means "use env/default" (scoring is unchanged).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_LOCK = threading.Lock()

# name -> (coded default, sane [min, max]).  These are the exact env knobs the composite
# scorer reads in workspace_server._enrich_one; keep this table in sync with them.
_KNOBS: dict[str, tuple[int, int, int]] = {
    "COMPOSITE_MOM121_WEIGHT": (12, 0, 40),
    "COMPOSITE_MOMENTUM_WEIGHT": (10, 0, 40),
    "COMPOSITE_SENT_WEIGHT": (8, 0, 40),
}


def _path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "factor_weights.json")


def _read() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _env_default(name: str) -> int:
    coded = _KNOBS.get(name, (0, 0, 40))[0]
    try:
        return int(os.environ.get(name, str(coded)))
    except (TypeError, ValueError):
        return coded


def get_weight(name: str) -> int:
    """Live weight for ``name``: the persisted override if present & valid, else the
    ``COMPOSITE_*_WEIGHT`` env value, else the coded default. Never raises."""
    cfg = _read()
    w = (cfg.get("weights") or {}).get(name)
    if isinstance(w, (int, float)):
        lo, hi = _KNOBS.get(name, (0, 0, 40))[1:]
        return int(max(lo, min(hi, w)))
    return _env_default(name)


def set_weights(weights: dict, *, approved_by: str = "user") -> dict:
    """Approve new factor weights (paper scoring only). Persists them, mirrors each into the
    in-process env for the running workers, and appends an audit entry. Unknown keys and
    out-of-bounds values are rejected. Returns the new state."""
    if not isinstance(weights, dict) or not weights:
        return {"error": "no weights given"}
    clean: dict[str, int] = {}
    for name, val in weights.items():
        if name not in _KNOBS:
            return {"error": f"unknown weight {name!r}"}
        try:
            v = int(val)
        except (TypeError, ValueError):
            return {"error": f"invalid value for {name}: {val!r}"}
        lo, hi = _KNOBS[name][1:]
        if not (lo <= v <= hi):
            return {"error": f"{name} {v} out of bounds [{lo}, {hi}]"}
        clean[name] = v

    with _LOCK:
        cfg = _read()
        prev = dict(cfg.get("weights") or {})
        merged = {**prev, **clean}
        entry = {"at": datetime.now(timezone.utc).isoformat(), "from": prev, "to": merged,
                 "approved_by": approved_by}
        history = list(cfg.get("history") or [])[-19:] + [entry]
        cfg = {"weights": merged, "updated_at": entry["at"], "approved_by": approved_by,
               "history": history}
        try:
            with open(_path(), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("factor_weights write failed: %s", e)
            return {"error": str(e)}
        for name, v in merged.items():
            os.environ[name] = str(v)   # immediate effect for the running process
        logger.info("factor_weights: %s → %s (by %s)", prev, merged, approved_by)
        return factor_weights_state()


def reset_weights() -> dict:
    """Revert every weight to its env/coded default (delete the override, audited)."""
    with _LOCK:
        cfg = _read()
        prev = dict(cfg.get("weights") or {})
        try:
            if os.path.exists(_path()):
                os.remove(_path())
        except Exception as e:
            logger.error("factor_weights reset failed: %s", e)
            return {"error": str(e)}
        for name in _KNOBS:
            os.environ.pop(name, None)
        logger.info("factor_weights: reset (was %s) → env/default", prev)
        return {"reset": True, "was": prev, "state": factor_weights_state()}


def factor_weights_state() -> dict:
    """Current weights + provenance + bounds (for display / sliders)."""
    cfg = _read()
    overridden = cfg.get("weights") or {}
    return {
        "weights": {name: get_weight(name) for name in _KNOBS},
        "defaults": {name: _KNOBS[name][0] for name in _KNOBS},
        "bounds": {name: {"min": _KNOBS[name][1], "max": _KNOBS[name][2]} for name in _KNOBS},
        "source": "approved" if overridden else "default",
        "updated_at": cfg.get("updated_at"),
        "approved_by": cfg.get("approved_by"),
        "history": cfg.get("history") or [],
    }


__all__ = ["get_weight", "set_weights", "reset_weights", "factor_weights_state"]
