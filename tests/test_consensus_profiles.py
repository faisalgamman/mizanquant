"""Tests for consolidated consensus profiles."""

from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("numpy")
pytest.importorskip("fastapi")

import halal_screener as hs


def test_consensus_profile_weights_sum_to_one():
    for profile in hs.CONSENSUS_PROFILES.values():
        assert sum(profile.weights.values()) == pytest.approx(1.0)


def test_profile_wrappers_forward_to_run_consensus(monkeypatch):
    calls = []

    def fake_run_consensus(symbol, horizon=5, episodes=10, profile="base", df_override=None, as_of=None):
        calls.append((symbol, horizon, episodes, profile, as_of))
        return [{"Symbol": symbol, "Verdict": "BUY"}]

    monkeypatch.setattr(hs, "run_consensus", fake_run_consensus)

    hs.run_consensus_momentum("AAPL", horizon=5, as_of="2024-07-15T12:00:00-04:00")
    hs.run_consensus_reversion("MSFT", horizon=3)
    hs.run_consensus_ml("NVDA", horizon=7, episodes=4)

    assert calls == [
        ("AAPL", 5, hs.CONSENSUS_PROFILES["momentum"].default_episodes, "momentum", "2024-07-15T12:00:00-04:00"),
        ("MSFT", 3, hs.CONSENSUS_PROFILES["reversion"].default_episodes, "reversion", None),
        ("NVDA", 7, 4, "ml", None),
    ]
