"""Tests for champion/challenger shadow scorer."""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services import model_registry as mr
from app.services import shadow_scorer as ss


@pytest.fixture(autouse=True)
def _temp_dirs(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_reg:
        with tempfile.TemporaryDirectory() as tmp_shadow:
            monkeypatch.setattr(mr, "_MODEL_REGISTRY_DIR", mr.Path(tmp_reg))
            monkeypatch.setattr(ss, "_SHADOW_DIR", ss.Path(tmp_shadow))
            yield


def test_record_and_summary():
    ss.record_paper_result("lstm", "staging", 0.01)
    ss.record_paper_result("lstm", "staging", 0.02)
    ss.record_paper_result("lstm", "production", -0.005)
    summary = ss.paper_summary("lstm")
    assert summary["name"] == "lstm"
    assert summary["staging_days"] == 2
    assert summary["production_days"] == 1


def test_paper_sharpe_insufficient_data():
    assert ss.paper_sharpe("lstm", "staging") == 0.0


def test_paper_sharpe_with_data():
    for i in range(10):
        ss.record_paper_result("lstm", "staging", 0.001 + 0.0005 * (i % 3 - 1))
    sharpe = ss.paper_sharpe("lstm", "staging")
    assert sharpe > 0


def test_evaluate_no_staging():
    assert ss.evaluate("lstm") == "no_staging"


def test_evaluate_no_production_promotes_staging():
    mr.register("lstm", alias="staging", artifact_path="models/lstm/v2.pt", version="v2")
    result = ss.evaluate("lstm")
    assert result == "no_production"
    prod = mr.resolve("lstm", "production")
    assert prod is not None
    assert prod["version"] == "v2"


def test_evaluate_insufficient_data():
    mr.register("lstm", alias="staging", artifact_path="models/lstm/v2.pt", version="v2")
    mr.register("lstm", alias="production", artifact_path="models/lstm/v1.pt", version="v1")
    assert ss.evaluate("lstm") == "staging_insufficient"


def test_evaluate_promotes_after_outperformance(monkeypatch):
    monkeypatch.setattr(ss, "_OUTPERFORMANCE_REQUIRED", 2)
    monkeypatch.setattr(ss, "_DAYS_REQUIRED", 5)
    monkeypatch.setattr(ss, "_MIN_WINDOW", 3)

    mr.register("lstm", alias="staging", artifact_path="models/lstm/v2.pt", version="v2", metrics={"sharpe": 2.0})
    mr.register("lstm", alias="production", artifact_path="models/lstm/v1.pt", version="v1", metrics={"sharpe": 1.0})

    # Production does poorly, staging does well (add noise so sharpe != 0)
    for i in range(7):
        ss.record_paper_result("lstm", "staging", 0.005 + 0.001 * (i % 3 - 1))
        ss.record_paper_result("lstm", "production", -0.005 + 0.001 * (i % 2))

    result = ss.evaluate("lstm")
    assert result == "promoted"
    prod = mr.resolve("lstm", "production")
    assert prod["version"] == "v2"


def test_evaluate_unchanged_when_staging_underperforms(monkeypatch):
    monkeypatch.setattr(ss, "_OUTPERFORMANCE_REQUIRED", 2)
    monkeypatch.setattr(ss, "_DAYS_REQUIRED", 5)
    monkeypatch.setattr(ss, "_MIN_WINDOW", 3)

    mr.register("lstm", alias="staging", artifact_path="models/lstm/v2.pt", version="v2")
    mr.register("lstm", alias="production", artifact_path="models/lstm/v1.pt", version="v1")

    for _ in range(7):
        ss.record_paper_result("lstm", "staging", -0.01)
        ss.record_paper_result("lstm", "production", 0.005)

    result = ss.evaluate("lstm")
    assert result == "unchanged"


def test_reset_scores():
    ss.record_paper_result("lstm", "staging", 0.01)
    assert ss.paper_summary("lstm")["staging_days"] == 1
    ss.reset_scores("lstm")
    assert ss.paper_summary("lstm")["staging_days"] == 0


def test_sharpe_zero_for_single_return():
    assert ss._sharpe([0.01]) == 0.0


def test_sharpe_positive_for_consistent_gains():
    assert ss._sharpe([0.001, 0.002, 0.0015, 0.003, 0.002]) > 0


def test_sharpe_negative_for_consistent_losses():
    assert ss._sharpe([-0.001, -0.002, -0.0015, -0.003, -0.002]) < 0
