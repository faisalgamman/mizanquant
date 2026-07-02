"""Persisted, reversible, audited gate-threshold override (paper-ledger only)."""

from __future__ import annotations

import importlib

import pytest  # noqa: F401


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("WEEKLY_MIN_RS", raising=False)
    import app.services.gate_config as gc
    return importlib.reload(gc)


def test_default_when_unset(monkeypatch, tmp_path):
    gc = _fresh(monkeypatch, tmp_path)
    assert gc.get_min_rs() == -2.0
    assert gc.gate_config_state()["source"] == "default"


def test_env_default_respected(monkeypatch, tmp_path):
    gc = _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("WEEKLY_MIN_RS", "-4")
    assert gc.get_min_rs() == -4.0


def test_set_persists_and_audits(monkeypatch, tmp_path):
    gc = _fresh(monkeypatch, tmp_path)
    out = gc.set_min_rs(0.0, evidence={"test_t": 3.1})
    assert out["min_rs"] == 0.0 and out["history"][-1]["to"] == 0.0
    assert out["history"][-1]["evidence"]["test_t"] == 3.1
    # a fresh read of the file sees the approved value + provenance
    gc2 = _fresh(monkeypatch, tmp_path)
    assert gc2.get_min_rs() == 0.0
    assert gc2.gate_config_state()["source"] == "approved"


def test_bounds_rejected(monkeypatch, tmp_path):
    gc = _fresh(monkeypatch, tmp_path)
    assert "error" in gc.set_min_rs(999)
    assert "error" in gc.set_min_rs("nope")
    assert gc.get_min_rs() == -2.0          # unchanged after a rejected write


def test_reset_reverts(monkeypatch, tmp_path):
    gc = _fresh(monkeypatch, tmp_path)
    gc.set_min_rs(1.0)
    assert gc.get_min_rs() == 1.0
    r = gc.reset_min_rs()
    assert r["reset"] is True
    gc2 = _fresh(monkeypatch, tmp_path)
    assert gc2.get_min_rs() == -2.0 and gc2.gate_config_state()["source"] == "default"
