"""Model registry — staging / production aliases with quality gates.

The trading engine loads models only from the **production** alias.
Champion/challenger shadow scoring runs on **staging** before promotion.

Quality gates (enforced on promote_to_production):
  - test_sharpe >= 1.0
  - test_acc >= 0.55
  - max_drawdown <= 15%
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

_MODEL_REGISTRY_DIR = Path(
    os.environ.get("MODEL_REGISTRY_DIR", "model_registry")
)

_STAGE_ALIASES = {"staging", "production"}

logger = logging.getLogger("model_registry")

# ── Quality Gate Thresholds ──

QUALITY_GATE_MIN_SHARPE = float(os.environ.get("QUALITY_GATE_MIN_SHARPE", "1.0"))
QUALITY_GATE_MIN_ACC = float(os.environ.get("QUALITY_GATE_MIN_ACC", "0.55"))
QUALITY_GATE_MAX_DRAWDOWN_PCT = float(os.environ.get("QUALITY_GATE_MAX_DD_PCT", "15.0"))


def _registry_path(name: str, alias: str) -> Path:
    return _MODEL_REGISTRY_DIR / name / f"{alias}.json"


def ensure_registry():
    _MODEL_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)


# ── Read ──


def resolve(name: str, alias: str = "production") -> Optional[dict[str, Any]]:
    """Return the registered model metadata, or None if not registered.

    The returned dict contains at minimum:

        {"artifact_path": "models/lstm/20260417.pt", "version": "20260417", ...}
    """
    path = _registry_path(name, alias)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def registered_versions(name: str) -> list[dict[str, Any]]:
    """Return all registered versions for *name* across all aliases."""
    ensure_registry()
    versions = []
    for alias in sorted(_STAGE_ALIASES):
        entry = resolve(name, alias)
        if entry:
            entry["alias"] = alias
            versions.append(entry)
    return versions


# ── Write ──


def register(
    name: str,
    alias: str,
    artifact_path: str,
    version: str,
    metrics: Optional[dict[str, float]] = None,
    **extra,
):
    """Register a model version under *alias* (staging or production)."""
    if alias not in _STAGE_ALIASES:
        raise ValueError(f"Alias must be one of {_STAGE_ALIASES}, got '{alias}'")
    ensure_registry()
    path = _registry_path(name, alias)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "artifact_path": artifact_path,
        "version": version,
        "metrics": metrics or {},
        **extra,
    }
    path.write_text(json.dumps(entry, indent=2, default=str))


def promote_to_production(name: str, version: str, artifact_path: str, metrics=None):
    """Promote a staging model to production — with quality gate enforcement.

    Quality gate checks:
        - test_sharpe >= QUALITY_GATE_MIN_SHARPE
        - test_acc >= QUALITY_GATE_MIN_ACC
        - max_drawdown <= QUALITY_GATE_MAX_DRAWDOWN_PCT

    Raises ValueError if quality gates are not met.
    """
    failures = _check_quality_gates(metrics or {})
    if failures:
        msg = f"Quality gate failed for {name}/{version}: " + "; ".join(failures)
        logger.warning(msg)
        raise ValueError(msg)
    register(name, "production", artifact_path, version, metrics=metrics)
    logger.info("Promoted %s/%s to production ✓", name, version)


def _check_quality_gates(metrics: dict[str, Any]) -> list[str]:
    """Check model metrics against quality gates. Returns list of failure messages."""
    failures = []
    sharpe = float(metrics.get("test_sharpe",
                    metrics.get("Sharpe Ratio",
                    metrics.get("sharpe", 0))))
    if sharpe < QUALITY_GATE_MIN_SHARPE:
        failures.append(f"Sharpe {sharpe:.2f} < {QUALITY_GATE_MIN_SHARPE}")
    acc = float(metrics.get("test_acc",
               metrics.get("directional_acc",
               metrics.get("Win Rate %", 0)) / 100.0))
    if acc < QUALITY_GATE_MIN_ACC:
        failures.append(f"Acc {acc:.3f} < {QUALITY_GATE_MIN_ACC}")
    max_dd = float(metrics.get("max_drawdown",
                   metrics.get("Max Drawdown %", 0)))
    if max_dd > QUALITY_GATE_MAX_DRAWDOWN_PCT:
        failures.append(f"MaxDD {max_dd:.1f}% > {QUALITY_GATE_MAX_DRAWDOWN_PCT}%")
    return failures


def validate_quality(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    """Public quality gate check. Returns (passed, failures)."""
    failures = _check_quality_gates(metrics)
    return len(failures) == 0, failures


def demote(name: str, alias: str = "production"):
    """Remove a registered alias (rollback)."""
    path = _registry_path(name, alias)
    if path.exists():
        path.unlink()


# ── Staging helpers for champion/challenger ──


def current_production_version(name: str) -> Optional[str]:
    """Return the production version string, or None."""
    entry = resolve(name, "production")
    return entry.get("version") if entry else None


def current_staging_version(name: str) -> Optional[str]:
    """Return the staging version string, or None."""
    entry = resolve(name, "staging")
    return entry.get("version") if entry else None


def swap_staging_production(name: str):
    """Swap staging and production aliases (after challenger wins)."""
    prod = resolve(name, "production")
    stag = resolve(name, "staging")
    if stag:
        register(name, "production", stag["artifact_path"], stag["version"],
                 metrics=stag.get("metrics"))
    if prod:
        register(name, "staging", prod["artifact_path"], prod["version"],
                 metrics=prod.get("metrics"))
