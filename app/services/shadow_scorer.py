"""Champion/challenger shadow scoring.

Runs staging models in paper mode alongside production, tracking daily
returns.  When the challenger outperforms production for a configurable
number of consecutive evaluations it is automatically promoted.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from app.services import model_registry as mr
from app.services.notify import send_message

logger = logging.getLogger("screener")

_SHADOW_DIR = Path(os.environ.get("SHADOW_SCORE_DIR", "shadow_scores"))

_DAYS_REQUIRED = int(os.environ.get("SHADOW_DAYS_REQUIRED", "5"))
_OUTPERFORMANCE_REQUIRED = int(os.environ.get("SHADOW_OUTPERFORMANCE_REQUIRED", "3"))
_MIN_WINDOW = max(_OUTPERFORMANCE_REQUIRED, 3)

# ── Persistence ──


def _score_path(name: str) -> Path:
    return _SHADOW_DIR / f"{name}.json"


def _load_scores(name: str) -> list[dict]:
    path = _score_path(name)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return []


def _save_scores(name: str, scores: list[dict]):
    _SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    _score_path(name).write_text(json.dumps(scores, indent=2, default=str))


# ── Core API ──


def record_paper_result(
    name: str,
    alias: str,
    daily_return: float,
    metadata: Optional[dict] = None,
):
    """Record a single day's paper-trading return for *alias* (staging/production)."""
    scores = _load_scores(name)
    scores.append({
        "alias": alias,
        "daily_return": daily_return,
        "timestamp": time.time(),
        "metadata": metadata or {},
    })
    _save_scores(name, scores)


def _windowed(scores: list[dict], alias: str, window: int) -> list[float]:
    """Return the last *window* daily returns for *alias*."""
    relevant = [s["daily_return"] for s in scores if s.get("alias") == alias]
    return relevant[-window:]


def _sharpe(returns: list[float], periods: int = 252) -> float:
    """Annualised Sharpe ratio from daily returns."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    if var_r == 0:
        return 0.0
    return (mean_r / (var_r ** 0.5)) * (periods ** 0.5)


def paper_sharpe(name: str, alias: str, window: int = _DAYS_REQUIRED) -> float:
    """Annualised Sharpe of paper returns for *alias* (last *window* days)."""
    scores = _load_scores(name)
    returns = _windowed(scores, alias, window)
    return _sharpe(returns)


def evaluate(name: str) -> str:
    """Compare staging vs production and auto-promote if warranted.

    Returns one of ``"promoted"``, ``"staging_insufficient"``,
    ``"no_production"``, ``"no_staging"``, or ``"unchanged"``.
    """
    prod_entry = mr.resolve(name, "production")
    stag_entry = mr.resolve(name, "staging")

    if stag_entry is None:
        return "no_staging"
    if prod_entry is None:
        mr.promote_to_production(
            name,
            version=stag_entry["version"],
            artifact_path=stag_entry["artifact_path"],
            metrics=stag_entry.get("metrics"),
        )
        send_message(
            f"🏆 *{name}*: no production entry found — staging promoted directly.",
            dedup_key=f"shadow_promote_{name}",
        )
        return "no_production"

    scores = _load_scores(name)
    prod_returns = _windowed(scores, "production", _DAYS_REQUIRED)
    stag_returns = _windowed(scores, "staging", _DAYS_REQUIRED)

    if len(stag_returns) < _MIN_WINDOW or len(prod_returns) < _MIN_WINDOW:
        return "staging_insufficient"

    prod_sharpe = _sharpe(prod_returns)
    stag_sharpe = _sharpe(stag_returns)

    out_count = 0
    for i in range(1, _OUTPERFORMANCE_REQUIRED + 1):
        sp = _sharpe(_windowed(scores, "staging", i + 1))
        pp = _sharpe(_windowed(scores, "production", i + 1))
        if sp > pp:
            out_count += 1

    if out_count >= _OUTPERFORMANCE_REQUIRED:
        mr.promote_to_production(
            name,
            version=stag_entry["version"],
            artifact_path=stag_entry["artifact_path"],
            metrics=stag_entry.get("metrics", {}),
        )
        send_message(
            f"🏆 *{name}* challenger promoted to production!\n"
            f"Staging Sharpe: {stag_sharpe:.3f} vs Production Sharpe: {prod_sharpe:.3f}",
            dedup_key=f"shadow_promote_{name}",
        )
        return "promoted"

    return "unchanged"


def paper_summary(name: str) -> dict:
    """Return a summary of paper stats for both aliases."""
    scores = _load_scores(name)
    prod_returns = _windowed(scores, "production", _DAYS_REQUIRED)
    stag_returns = _windowed(scores, "staging", _DAYS_REQUIRED)
    return {
        "name": name,
        "production_days": len(prod_returns),
        "staging_days": len(stag_returns),
        "production_sharpe": _sharpe(prod_returns),
        "staging_sharpe": _sharpe(stag_returns),
        "outperformance_required": _OUTPERFORMANCE_REQUIRED,
        "days_required": _DAYS_REQUIRED,
    }


def reset_scores(name: str):
    """Clear all paper scores for *name* (used in tests)."""
    path = _score_path(name)
    if path.exists():
        path.unlink()
