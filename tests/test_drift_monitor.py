"""Tests for PSI/KS drift monitor."""

from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

from app.services.drift_monitor import (
    DriftMonitor,
    compute_ks,
    compute_psi,
)


@pytest.fixture(autouse=True)
def _temp_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr("app.services.drift_monitor._DRIFT_DIR", type("_", (), {"__call__": lambda s, p: type("P", (), {"stem": tmp, "exists": lambda: True, "iterdir": lambda: []})()})())
        # Simpler: just override the Path object
        import pathlib
        orig = pathlib.Path
        monkeypatch.setattr("app.services.drift_monitor._DRIFT_DIR", orig(tmp))
        yield


# ── PSI tests ──


def test_psi_identical_distributions():
    data = [1.0] * 100 + [2.0] * 100
    psi = compute_psi(data, data, bins=10)
    assert psi < 0.01


def test_psi_completely_different():
    low = [1.0] * 100
    high = [100.0] * 100
    psi = compute_psi(low, high, bins=10)
    assert psi > 0.1


def test_psi_empty_expected():
    assert compute_psi([], [1.0, 2.0]) == 0.0


def test_psi_empty_actual():
    assert compute_psi([1.0, 2.0], []) == 0.0


def test_psi_zero_variance():
    data = [5.0] * 50
    other = [5.0] * 50
    assert compute_psi(data, other) == 0.0


def test_psi_single_bin():
    a = [1.0, 2.0, 3.0]
    b = [4.0, 5.0, 6.0]
    result = compute_psi(a, b, bins=1)
    assert isinstance(result, float)
    assert result >= 0


# ── KS tests ──


def test_ks_identical():
    np.random.seed(42)
    data = np.random.normal(0, 1, 1000).tolist()
    assert compute_ks(data, data) < 0.01


def test_ks_completely_different():
    a = [1.0] * 50
    b = [100.0] * 50
    assert compute_ks(a, b) > 0.9


def test_ks_empty_reference():
    assert compute_ks([], [1.0, 2.0]) == 0.0


def test_ks_empty_current():
    assert compute_ks([1.0, 2.0], []) == 0.0


def test_ks_partial_overlap():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [3.0, 4.0, 5.0, 6.0, 7.0]
    ks = compute_ks(a, b)
    assert 0.1 < ks < 0.9


# ── DriftMonitor tests ──


def test_monitor_set_and_get_reference():
    m = DriftMonitor()
    m.set_reference("feature_x", [1.0, 2.0, 3.0], "numeric")
    ref = m.get_reference("feature_x")
    assert ref is not None
    assert ref["type"] == "numeric"
    assert len(ref["values"]) == 3


def test_monitor_has_reference():
    m = DriftMonitor()
    assert not m.has_reference("feature_x")
    m.set_reference("feature_x", [1.0], "numeric")
    assert m.has_reference("feature_x")


def test_monitor_check_no_reference():
    m = DriftMonitor()
    result = m.check_numeric("missing", [1.0, 2.0])
    assert result["error"] == "no_reference"
    assert not result["drifted"]


def test_monitor_check_no_drift():
    m = DriftMonitor(psi_threshold=0.5, ks_threshold=0.5)
    m.set_reference("feature_x", [1.0] * 100 + [2.0] * 100, "numeric")
    result = m.check_numeric("feature_x", [1.0] * 100 + [2.0] * 100)
    assert not result["drifted"]


def test_monitor_check_drift_detected():
    m = DriftMonitor(psi_threshold=0.05, ks_threshold=0.05)
    m.set_reference("feature_x", [1.0] * 100, "numeric")
    result = m.check_numeric("feature_x", [100.0] * 100)
    assert result["drifted"]


def test_monitor_categorical_no_ref():
    m = DriftMonitor()
    result = m.check_categorical("feature_y", "a")
    assert result["error"] == "no_reference"


def test_monitor_categorical_no_drift():
    m = DriftMonitor(psi_threshold=0.2)
    result = m.check_categorical("feature_y", "a", reference_frequencies={"a": 1.0})
    assert not result["drifted"]


def test_monitor_categorical_drift():
    m = DriftMonitor(psi_threshold=0.2)
    result = m.check_categorical("feature_y", "b", reference_frequencies={"a": 1.0})
    assert result["drifted"]


def test_monitor_list_references():
    m = DriftMonitor()
    m.set_reference("feat_a", [1.0], "numeric")
    m.set_reference("feat_b", [2.0], "numeric")
    refs = m.list_references()
    assert "feat_a" in refs
    assert "feat_b" in refs


def test_monitor_alert_cooldown(monkeypatch):
    monkeypatch.setattr("app.services.drift_monitor._ALERT_COOLDOWN", 0.0)
    m = DriftMonitor(psi_threshold=0.01, ks_threshold=0.01)
    m.set_reference("feature_x", [1.0] * 100, "numeric")
    result1 = m.check_numeric("feature_x", [100.0] * 100)
    assert result1["alerted"]
    result2 = m.check_numeric("feature_x", [100.0] * 100)
    assert result2["alerted"]
