"""Unit tests for the regime detector."""
from __future__ import annotations

from app.services import regime


def _reset_regime_state():
    regime._last_confirmed = "NEUTRAL"
    regime._last_candidate = None
    regime._current_snapshot = None
    regime._forced_state = None


def test_regime_requires_two_scans_to_confirm(monkeypatch):
    _reset_regime_state()
    monkeypatch.setattr(regime, "_persist_snapshot", lambda snapshot: None)
    monkeypatch.setattr(regime, "_emit_change", lambda snapshot: None)
    monkeypatch.setattr(regime, "_fetch_spy_ema_slope_pct", lambda: 1.2)

    def fred(series_id: str):
        if series_id == "VIXCLS":
            return [15.0] * 20
        if series_id == "T10Y2Y":
            return [0.25] * 20
        raise AssertionError(series_id)

    monkeypatch.setattr(regime, "_fetch_fred_series", fred)

    first = regime.refresh_regime()
    second = regime.refresh_regime()

    assert first.state == "NEUTRAL"
    assert first.changed is False
    assert second.state == "BULL"
    assert second.changed is True


def test_single_scan_spike_does_not_flip_confirmed_regime(monkeypatch):
    _reset_regime_state()
    monkeypatch.setattr(regime, "_persist_snapshot", lambda snapshot: None)
    monkeypatch.setattr(regime, "_emit_change", lambda snapshot: None)

    current = {"slope": 1.0, "vix": 15.0, "spread": 0.25}

    def fred(series_id: str):
        if series_id == "VIXCLS":
            return [15.0] * 19 + [current["vix"]]
        if series_id == "T10Y2Y":
            return [0.25] * 19 + [current["spread"]]
        raise AssertionError(series_id)

    monkeypatch.setattr(regime, "_fetch_fred_series", fred)
    monkeypatch.setattr(regime, "_fetch_spy_ema_slope_pct", lambda: current["slope"])

    regime.refresh_regime()
    bull = regime.refresh_regime()
    assert bull.state == "BULL"

    current.update({"slope": -1.0, "vix": 90.0, "spread": -0.5})
    spike = regime.refresh_regime()

    assert spike.state == "BULL"
    assert spike.changed is False


def test_force_regime_overrides_refresh(monkeypatch):
    _reset_regime_state()
    monkeypatch.setattr(regime, "_persist_snapshot", lambda snapshot: None)
    monkeypatch.setattr(regime, "_emit_change", lambda snapshot: None)

    forced = regime.force_regime("BEAR")
    assert forced.state == "BEAR"
    assert forced.changed is True

    refreshed = regime.refresh_regime()
    assert refreshed.state == "BEAR"

    cleared = regime.force_regime(None)
    assert cleared.state == "BEAR"
    assert regime._forced_state is None
