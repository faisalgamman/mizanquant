"""MLflow tracking integration.

Logs every training run (model + agent) with:
  - Dataset hash, git SHA, code hash, config hash
  - Hyper-parameters
  - Performance metrics (Sharpe, Sortino, etc.)
  - Artifact paths

Designed to work with any MLflow-compatible tracking server
(hosted MLflow, Databricks, or local file-based).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from app.core.reproducibility import code_hash, config_hash, git_sha, is_dirty

_MLFLOW_AVAILABLE = False
_mlflow = None

_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "")
_EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT", "openbb-trading")

if _TRACKING_URI:
    try:
        import mlflow as _mlflow

        _mlflow.set_tracking_uri(_TRACKING_URI)
        _mlflow.set_experiment(_EXPERIMENT_NAME)
        _MLFLOW_AVAILABLE = True
    except Exception:
        _mlflow = None


def is_available() -> bool:
    return _MLFLOW_AVAILABLE


def tracking_uri() -> str:
    return _TRACKING_URI or "(not configured)"


# ── Run context manager ──


class TrainingRun:
    """Context manager that wraps an MLflow run.

    Usage::

        with TrainingRun("lstm", {"symbol": "AAPL", "lr": 0.001}) as run:
            run.log_metric("val_sharpe", 1.2)
            run.log_artifact("models/lstm/20260417.pt")
    """

    def __init__(
        self,
        run_name: str,
        params: Optional[dict[str, Any]] = None,
        tags: Optional[dict[str, str]] = None,
    ):
        self.run_name = run_name
        self.params = params or {}
        self.tags = tags or {}
        self._active = False
        self._run_id: Optional[str] = None

    def __enter__(self):
        if not _MLFLOW_AVAILABLE or _mlflow is None:
            return self
        _mlflow.start_run(run_name=self.run_name, nested=False)
        self._active = True
        self._run_id = _mlflow.active_run().info.run_id

        # ── Reproducibility tags ──
        _mlflow.set_tags({
            "git_sha": git_sha(short=True),
            "is_dirty": str(is_dirty()),
            "code_hash": code_hash(),
            "config_hash": config_hash(),
            **self.tags,
        })

        # ── Parameters ──
        for k, v in self.params.items():
            _mlflow.log_param(k, v)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._active and _mlflow is not None:
            if exc_type is not None:
                _mlflow.set_tag("status", "failed")
            else:
                _mlflow.set_tag("status", "completed")
            _mlflow.end_run()
            self._active = False

    @property
    def run_id(self) -> Optional[str]:
        return self._run_id

    def log_metric(self, key: str, value: float, step: Optional[int] = None):
        if self._active and _mlflow is not None:
            _mlflow.log_metric(key, value, step=step)

    def log_metrics(self, metrics: dict[str, float], step: Optional[int] = None):
        if self._active and _mlflow is not None:
            _mlflow.log_metrics(metrics, step=step)

    def log_param(self, key: str, value: Any):
        if self._active and _mlflow is not None:
            _mlflow.log_param(key, value)

    def log_artifact(self, local_path: str):
        if self._active and _mlflow is not None:
            _mlflow.log_artifact(local_path)

    def log_artifacts(self, local_dir: str):
        if self._active and _mlflow is not None:
            _mlflow.log_artifacts(local_dir)

    def set_tag(self, key: str, value: str):
        if self._active and _mlflow is not None:
            _mlflow.set_tag(key, value)


def start_run(
    run_name: str,
    params: Optional[dict[str, Any]] = None,
    tags: Optional[dict[str, str]] = None,
) -> TrainingRun:
    """Convenience factory for TrainingRun."""
    return TrainingRun(run_name, params=params, tags=tags)
