"""Daily NAV log for the paper ledgers (core PVC vs momentum satellite PVSA) — the race curve.

The ledger summaries give 'return since inception' but not the PATH: drawdown, volatility, and
when each strategy led. This appends ONE row per UTC day (open-position market value + unrealized
return + realized P/L + count) for each ledger, persisted as JSON in CACHE_DIR so the forward
out-of-sample record accumulates across restarts. Idempotent per day (a same-day re-run replaces
that day's row). Best-effort and fail-safe — a bad read/write never raises into the scheduler.

This is measurement only: it reads the simulated ledgers and writes a log. It places no orders and
changes no scoring. Mirrors the persisted-config pattern of :mod:`app.services.factor_weights`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_LOCK = threading.Lock()
_MAX_ROWS = 500          # ~2 years of daily rows; oldest trimmed


def _path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "ledger_nav.json")


def _read() -> list:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def record_nav() -> dict:
    """Compute today's NAV row for both ledgers and append it (replacing any existing same-day
    row). Returns the row. Never raises — each ledger read is guarded so one failure can't block
    the other or the caller (the scheduler)."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row: dict = {"date": day}
    try:
        from app.services.paper_validation import core_ledger_summary
        c = core_ledger_summary()
        row["core_upl"] = c.get("unrealized_pct")
        row["core_mv"] = c.get("market_value")
        row["core_realized"] = c.get("realized_pnl")
        row["core_open"] = c.get("open")
    except Exception as e:
        logger.debug("record_nav core failed: %s", e)
    try:
        from app.services.paper_validation import satellite_ledger_summary
        s = satellite_ledger_summary()
        row["sat_upl"] = s.get("unrealized_pct")
        row["sat_mv"] = s.get("market_value")
        row["sat_realized"] = s.get("realized_pnl")
        row["sat_open"] = s.get("open")
    except Exception as e:
        logger.debug("record_nav satellite failed: %s", e)
    try:
        from app.services.paper_validation import explorer_ledger_summary
        x = explorer_ledger_summary()
        row["exp_upl"] = x.get("unrealized_pct")
        row["exp_mv"] = x.get("market_value")
        row["exp_realized"] = x.get("realized_pnl")
        row["exp_open"] = x.get("open")
    except Exception as e:
        logger.debug("record_nav explorer failed: %s", e)

    with _LOCK:
        rows = [r for r in _read() if isinstance(r, dict) and r.get("date") != day]
        rows.append(row)
        rows = rows[-_MAX_ROWS:]
        try:
            with open(_path(), "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False)
        except Exception as e:
            logger.error("record_nav write failed: %s", e)
    return row


def nav_history() -> dict:
    """The full daily race series (ascending by date) + a small summary for the UI."""
    rows = sorted(_read(), key=lambda r: r.get("date", ""))
    latest = rows[-1] if rows else {}
    return {
        "rows": rows,
        "days": len(rows),
        "latest": latest,
        "note": "Daily out-of-sample race of the paper core (PVC) vs the momentum satellite (PVSA). "
                "Unrealized-return of open positions at each day's scan prices. Measurement only; never trades.",
    }


__all__ = ["record_nav", "nav_history"]
