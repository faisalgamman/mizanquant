"""Tests for model registry (staging/production aliases)."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from app.services import model_registry as mr


@pytest.fixture(autouse=True)
def _temp_registry(monkeypatch):
    """Use a temp directory for the registry to avoid polluting real files."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(mr, "_MODEL_REGISTRY_DIR", mr.Path(tmp))
        yield


def test_resolve_none_when_not_registered():
    assert mr.resolve("lstm") is None
    assert mr.resolve("lstm", alias="staging") is None


def test_register_and_resolve():
    mr.register("lstm", alias="production", artifact_path="models/lstm/20260417.pt", version="20260417")
    entry = mr.resolve("lstm")
    assert entry is not None
    assert entry["artifact_path"] == "models/lstm/20260417.pt"
    assert entry["version"] == "20260417"


def test_register_staging():
    mr.register("transformer", alias="staging", artifact_path="models/transformer/v2.pt", version="v2")
    entry = mr.resolve("transformer", alias="staging")
    assert entry is not None
    assert entry["version"] == "v2"

    # Production should still be None
    assert mr.resolve("transformer") is None


def test_promote_to_production():
    mr.register("ensemble", alias="staging", artifact_path="models/ensemble/challenger.pt", version="v3")
    # Must supply metrics that pass ALL quality gates (Sharpe, Acc, MaxDD, DSR, p-value)
    mr.promote_to_production(
        "ensemble",
        version="v3",
        artifact_path="models/ensemble/challenger.pt",
        metrics={
            "test_sharpe": 1.5,
            "test_acc": 0.62,
            "max_drawdown": 8.0,
            "deflated_sharpe": 0.75,
            "permutation_pvalue": 0.02,
        },
    )
    prod = mr.resolve("ensemble")
    assert prod is not None
    assert prod["version"] == "v3"


def test_demote():
    mr.register("lstm", alias="production", artifact_path="models/lstm/old.pt", version="v1")
    assert mr.resolve("lstm") is not None
    mr.demote("lstm")
    assert mr.resolve("lstm") is None


def test_registered_versions():
    mr.register("arima", alias="staging", artifact_path="models/arima/v1.pt", version="v1")
    mr.register("arima", alias="production", artifact_path="models/arima/v2.pt", version="v2")
    versions = mr.registered_versions("arima")
    assert len(versions) == 2
    aliases = {v["alias"] for v in versions}
    assert aliases == {"staging", "production"}


def test_current_production_version():
    assert mr.current_production_version("lstm") is None
    mr.register("lstm", alias="production", artifact_path="models/lstm/v1.pt", version="v1")
    assert mr.current_production_version("lstm") == "v1"


def test_current_staging_version():
    assert mr.current_staging_version("lstm") is None
    mr.register("lstm", alias="staging", artifact_path="models/lstm/v2.pt", version="v2")
    assert mr.current_staging_version("lstm") == "v2"


def test_swap_staging_production():
    mr.register("lstm", alias="staging", artifact_path="models/lstm/v2.pt", version="v2", metrics={"sharpe": 1.2})
    mr.register("lstm", alias="production", artifact_path="models/lstm/v1.pt", version="v1", metrics={"sharpe": 1.0})
    mr.swap_staging_production("lstm")
    assert mr.current_production_version("lstm") == "v2"
    assert mr.current_staging_version("lstm") == "v1"


def test_invalid_alias_raises():
    with pytest.raises(ValueError, match="Alias must be one of"):
        mr.register("lstm", alias="invalid", artifact_path="x.pt", version="v1")


def test_register_with_metrics():
    mr.register("xgboost", alias="production", artifact_path="models/xgboost/v1.pt", version="v1",
                metrics={"sharpe": 1.5, "accuracy": 0.65})
    entry = mr.resolve("xgboost")
    assert entry["metrics"]["sharpe"] == 1.5
