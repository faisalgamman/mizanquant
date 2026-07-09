"""Pre-registered graduation criteria for the shadow satellites (momentum + explorer).

The scientific point of pre-registration: LOCK the pass/fail rules BEFORE the forward data lands,
so we can't rationalize any outcome afterwards ("we'll find a reason for any result"). The user
approves three numbers once — how long to wait (rebalances), the minimum alpha over the core, and
the most-worse drawdown tolerated — and from then on the verdict is MECHANICAL: watching → graduated
/ archive, computed from the NAV race, with no human discretion at judgment time.

This records criteria and computes a verdict only. It never trades, never changes scoring, and does
NOT itself archive or promote a ledger — graduating to a real allocation is always the user's call.
Persisted/reversible/audited like :mod:`app.services.circuit_breaker`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_LOCK = threading.Lock()

# name -> (coded default, min, max)
_FIELDS: dict[str, tuple[float, float, float]] = {
    "min_rebalances": (4.0, 1.0, 24.0),       # wait this many monthly rebalances before judging
    "min_alpha_pct": (3.0, 0.0, 50.0),        # satellite must beat the core by ≥ this (cumulative %)
    "max_worse_dd_pct": (15.0, 0.0, 100.0),   # its drawdown may be at most this much worse than the core's
}
_REBALANCE_DAYS = 28                          # months ≈ satellite/explorer cadence


def _path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "graduation_criteria.json")


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


def approve_criteria(values: dict, *, approved_by: str = "user") -> dict:
    """Lock the graduation criteria (before the data). Persisted, audited, reversible. Out-of-range
    values are clamped; unknown keys rejected. Changes NO trading — it only sets the yardstick."""
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
        cfg = {"values": merged, "approved_at": now, "approved_by": approved_by, "history": history}
        try:
            with open(_path(), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("graduation_criteria write failed: %s", e)
            return {"error": str(e)}
        logger.info("graduation_criteria locked: %s (by %s)", merged, approved_by)
        return graduation_criteria_state()


def reset_criteria() -> dict:
    """Revoke the locked criteria (delete, audited) — reverts to 'not yet locked'."""
    with _LOCK:
        cfg = _read()
        prev = dict(cfg.get("values") or {})
        try:
            if os.path.exists(_path()):
                os.remove(_path())
        except Exception as e:
            logger.error("graduation_criteria reset failed: %s", e)
            return {"error": str(e)}
        logger.info("graduation_criteria: reset (was %s)", prev)
        return {"reset": True, "was": prev, "state": graduation_criteria_state()}


def _val(cfg_vals: dict, name: str) -> float:
    v = cfg_vals.get(name)
    return float(v) if isinstance(v, (int, float)) else _FIELDS[name][0]


def graduation_criteria_state() -> dict:
    """Current locked criteria + provenance + bounds. Never raises."""
    cfg = _read()
    vals = cfg.get("values") or {}
    return {
        "values": {name: _val(vals, name) for name in _FIELDS},
        "defaults": {name: _FIELDS[name][0] for name in _FIELDS},
        "bounds": {name: {"min": _FIELDS[name][1], "max": _FIELDS[name][2]} for name in _FIELDS},
        "locked": bool(vals) and bool(cfg.get("approved_at")),
        "approved_at": cfg.get("approved_at"),
        "approved_by": cfg.get("approved_by"),
        "history": cfg.get("history") or [],
    }


def _max_drawdown_pct(upls: list) -> float:
    """Max drawdown (%) of an equity built from a cumulative-return-% series."""
    peak = -1e18
    mdd = 0.0
    for u in upls:
        eq = 1.0 + (float(u) / 100.0)
        peak = max(peak, eq)
        if peak > 0:
            mdd = min(mdd, eq / peak - 1.0)
    return mdd * 100.0


def evaluate_satellites() -> dict:
    """Mechanical verdict for each satellite vs the LOCKED criteria, from the NAV race series.
    watching (not enough elapsed) → graduated (alpha & drawdown pass) / archive (failed after the
    wait). Verdict only — never archives or promotes anything. Fail-safe."""
    st = graduation_criteria_state()
    crit = st["values"]
    min_days = int(crit["min_rebalances"]) * _REBALANCE_DAYS
    try:
        from app.services.ledger_nav import nav_history
        rows = nav_history().get("rows") or []
    except Exception:
        rows = []
    span_days = 0
    if len(rows) >= 2:
        try:
            d0 = datetime.fromisoformat(rows[0]["date"])
            d1 = datetime.fromisoformat(rows[-1]["date"])
            span_days = max(0, (d1 - d0).days)
        except Exception:
            span_days = 0

    core = [r.get("core_upl") for r in rows if isinstance(r.get("core_upl"), (int, float))]
    core_dd = _max_drawdown_pct(core) if core else 0.0

    engines = {}
    for key, label in (("sat", "القمر (الزخم)"), ("exp", "المستكشف (الذيل)")):
        series = [(r.get(key + "_upl"), r.get("core_upl")) for r in rows
                  if isinstance(r.get(key + "_upl"), (int, float))]
        if not series:
            engines[key] = {"label": label, "verdict": "no_data"}
            continue
        upls = [s[0] for s in series]
        latest = series[-1]
        alpha = float(latest[0]) - float(latest[1] or 0)
        dd = _max_drawdown_pct(upls)
        worse_dd = abs(dd) - abs(core_dd)
        if not st["locked"]:
            verdict = "criteria_not_locked"
        elif span_days < min_days:
            verdict = "watching"
        elif alpha >= crit["min_alpha_pct"] and worse_dd <= crit["max_worse_dd_pct"]:
            verdict = "graduated"
        else:
            verdict = "archive"
        engines[key] = {"label": label, "alpha": round(alpha, 2), "dd": round(dd, 1),
                        "worse_dd_vs_core": round(worse_dd, 1), "verdict": verdict}
    return {
        "locked": st["locked"], "approved_at": st["approved_at"], "criteria": crit,
        "span_days": span_days, "min_days": min_days, "core_dd": round(core_dd, 1),
        "engines": engines,
        "note": "Mechanical verdict vs the pre-registered criteria — computed from the NAV race, no "
                "discretion. Verdict only: it never archives or promotes a ledger (that stays the user's call).",
    }


__all__ = ["approve_criteria", "reset_criteria", "graduation_criteria_state", "evaluate_satellites"]
