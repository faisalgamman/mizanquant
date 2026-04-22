"""Tests for scheduler script hooks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import scheduler


def test_run_train_models_invokes_script(monkeypatch):
    calls = {}

    def fake_run(cmd, check, capture_output, text, cwd, env):
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        calls["env"] = env
        return SimpleNamespace(stdout="ok")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    scheduler._run_train_models()

    assert calls["cmd"][-1] == "scripts/train_models.py"
    assert Path(calls["cwd"]).name == "trading_app"
    assert "openbb_forecast" in calls["env"]["PYTHONPATH"]


def test_run_signal_audit_invokes_script(monkeypatch):
    calls = {}

    def fake_run(cmd, check, capture_output, text, cwd, env):
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        calls["env"] = env
        return SimpleNamespace(stdout="ok")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    scheduler._run_signal_audit()

    assert calls["cmd"][-1] == "scripts/audit_signals.py"
    assert Path(calls["cwd"]).name == "trading_app"
    assert "openbb_forecast" in calls["env"]["PYTHONPATH"]


def test_reference_data_refresh_runs_assets_and_earnings(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        scheduler,
        "_run_reference_data_refresh",
        scheduler._run_reference_data_refresh,
    )

    from app.services import reference_data as rd

    monkeypatch.setattr(rd, "get_tradable_symbols", lambda force_refresh=False: {"AAPL", "MSFT"})
    monkeypatch.setattr(rd, "refresh_earnings_calendar", lambda symbols=None: calls.setdefault("symbols", list(symbols or [])) or {})

    scheduler._run_reference_data_refresh()

    assert sorted(calls["symbols"]) == ["AAPL", "MSFT"]
