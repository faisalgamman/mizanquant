"""Tests for nightly model retrain promotion and rollback."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import train_models


def test_train_models_keeps_previous_latest_on_regression(monkeypatch, capsys):
    artifact = Path.cwd() / "test_artifacts" / "rollback.pt"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_text("artifact", encoding="utf-8")

    monkeypatch.setattr(
        train_models,
        "_train_one",
        lambda name, symbol, version: {
            "artifact": artifact,
            "val_sharpe": 0.1,
            "val_acc": 0.5,
            "test_sharpe": 0.2,
            "test_acc": 0.5,
            "test_window_days": 63,
        },
    )
    monkeypatch.setattr(train_models, "_previous_sharpe", lambda name: 1.0)

    written = []
    monkeypatch.setattr(train_models, "write_latest", lambda name, version: written.append((name, version)))
    notified = {}
    monkeypatch.setattr(
        train_models,
        "notify",
        lambda event_type, dedup_key="", message="", **kwargs: notified.update(
            {"event_type": event_type, "dedup_key": dedup_key, "message": message}
        )
        or True,
    )
    monkeypatch.setattr(sys, "argv", ["train_models.py", "--models", "lstm", "--symbols", "AAPL", "--version", "20260417"])

    train_models.main()
    output = json.loads(capsys.readouterr().out)

    assert written == []
    assert notified["event_type"] == "critical_error"
    assert output["lstm"]["promoted"] is False


def test_train_models_promotes_when_sharpe_is_within_guardrail(monkeypatch, capsys):
    artifact = Path.cwd() / "test_artifacts" / "promote.pt"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_text("artifact", encoding="utf-8")

    monkeypatch.setattr(
        train_models,
        "_train_one",
        lambda name, symbol, version: {
            "artifact": artifact,
            "val_sharpe": 0.9,
            "val_acc": 0.6,
            "test_sharpe": 0.8,
            "test_acc": 0.6,
            "test_window_days": 63,
        },
    )
    monkeypatch.setattr(train_models, "_previous_sharpe", lambda name: 1.0)

    written = []
    monkeypatch.setattr(train_models, "write_latest", lambda name, version: written.append((name, version)))
    monkeypatch.setattr(train_models, "notify", lambda *args, **kwargs: True)
    monkeypatch.setattr(sys, "argv", ["train_models.py", "--models", "lstm", "--symbols", "AAPL", "--version", "20260417"])

    train_models.main()
    output = json.loads(capsys.readouterr().out)

    assert written == [("lstm", "20260417")]
    assert output["lstm"]["promoted"] is True
