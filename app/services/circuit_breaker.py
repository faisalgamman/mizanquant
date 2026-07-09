"""The user's written circuit-breaker card — a discipline contract, persisted & reversible.

SCOPE / SAFETY: this stores NOTHING that touches a broker. It is the user's own written
protocol for the small-money experimental phase of the Core Portfolio: the cumulative-loss
line at which they STOP and review, the drift-from-basket line that trips a "discipline"
alert, and the standing rule that an HMM "crisis" bell is INFORMATIONAL ONLY (we measured
that auto-exit on the regime bell hurts — see the walk-forward finding). The system never
enforces these on a real account; the user does. Stored as JSON in CACHE_DIR so the card
survives restarts, with an audit history and a reset. Mirrors :mod:`app.services.factor_weights`.

Approving the card is a USER decision — nothing here is auto-applied and it never places or
blocks an order. All getters are fail-safe and never raise.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_LOCK = threading.Lock()

# name -> (coded default, min, max).  Percentages are of the EXPERIMENTAL capital the user
# commits to the core basket — never their whole net worth.
_FIELDS: dict[str, tuple[float, float, float]] = {
    "max_cumulative_loss_pct": (20.0, 1.0, 50.0),   # cumulative drawdown → STOP & review
    "max_deviation_pct": (10.0, 2.0, 50.0),          # |your weights − basket| → discipline alert
    "capital_experimental": (0.0, 0.0, 1e9),         # $ the user is willing to lose fully
}


def _path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "circuit_breaker.json")


def _read() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _clamp(name: str, val) -> float | None:
    if name not in _FIELDS:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    _, lo, hi = _FIELDS[name]
    return max(lo, min(hi, v))


def approve_card(values: dict, *, approved_by: str = "user") -> dict:
    """Approve/update the written circuit-breaker card. Persists the thresholds, appends an
    audit entry, and stamps ``approved_at``. Out-of-range values are clamped; unknown keys
    rejected. Returns the new state. This changes NO trading behavior — it records the user's
    own written protocol."""
    if not isinstance(values, dict) or not values:
        return {"error": "no values given"}
    clean: dict[str, float] = {}
    for name, val in values.items():
        if name not in _FIELDS:
            return {"error": f"unknown field {name!r}"}
        v = _clamp(name, val)
        if v is None:
            return {"error": f"invalid value for {name}: {val!r}"}
        clean[name] = v

    with _LOCK:
        cfg = _read()
        prev = dict(cfg.get("values") or {})
        merged = {**prev, **clean}
        now = datetime.now(timezone.utc).isoformat()
        entry = {"at": now, "from": prev, "to": merged, "approved_by": approved_by}
        history = list(cfg.get("history") or [])[-19:] + [entry]
        cfg = {"values": merged, "approved_at": now, "approved_by": approved_by,
               "hmm_crisis_info_only": True, "history": history}
        try:
            with open(_path(), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("circuit_breaker write failed: %s", e)
            return {"error": str(e)}
        logger.info("circuit_breaker approved: %s (by %s)", merged, approved_by)
        return circuit_breaker_state()


def reset_card() -> dict:
    """Revoke the approved card (delete the override, audited). The written protocol reverts to
    'not yet approved' — a reminder that the user must re-approve before real orders."""
    with _LOCK:
        cfg = _read()
        prev = dict(cfg.get("values") or {})
        try:
            if os.path.exists(_path()):
                os.remove(_path())
        except Exception as e:
            logger.error("circuit_breaker reset failed: %s", e)
            return {"error": str(e)}
        logger.info("circuit_breaker: reset (was %s)", prev)
        return {"reset": True, "was": prev, "state": circuit_breaker_state()}


def circuit_breaker_state() -> dict:
    """Current card values + provenance + bounds (for display / inputs). Never raises."""
    cfg = _read()
    vals = cfg.get("values") or {}
    approved = bool(vals) and bool(cfg.get("approved_at"))
    return {
        "values": {name: (vals.get(name) if isinstance(vals.get(name), (int, float))
                          else _FIELDS[name][0]) for name in _FIELDS},
        "defaults": {name: _FIELDS[name][0] for name in _FIELDS},
        "bounds": {name: {"min": _FIELDS[name][1], "max": _FIELDS[name][2]} for name in _FIELDS},
        "approved": approved,
        "approved_at": cfg.get("approved_at"),
        "approved_by": cfg.get("approved_by"),
        "hmm_crisis_info_only": True,
        "history": cfg.get("history") or [],
    }


__all__ = ["approve_card", "reset_card", "circuit_breaker_state"]
