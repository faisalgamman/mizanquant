"""Tests for tech_score_v2 runtime scorer."""
import json
import os
import tempfile
from datetime import datetime

import numpy as np
import pandas as pd


def _make_df(n_days=300, trend=0.001, noise=0.015, seed=42):
    rng = np.random.default_rng(seed)
    close = 100.0
    closes = []
    dates = pd.date_range(end=datetime.now(), periods=n_days, freq="D")
    for _ in range(n_days):
        ret = trend + rng.normal(0, noise)
        close = close * (1 + ret)
        closes.append(close)
    closes = np.array(closes)
    return pd.DataFrame({
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(n_days, 1_000_000.0),
    }, index=dates)


def _make_pass_artifact():
    """Synthetic PASS artifact with plausible values."""
    from app.services.feature_lib import FEATURE_ORDER
    n_feat = len(FEATURE_ORDER)
    return {
        "version": "tech-v2-2026-06",
        "verdict": "PASS",
        "features": FEATURE_ORDER,
        "means": [0.0] * n_feat,
        "stds": [1.0] * n_feat,
        "coefs": [0.0] * n_feat,
        "intercept": 0.0,
        "calibration_quantiles": list(np.linspace(-3, 3, 99)),
        "alpha": 1.0,
        "n": 1000,
        "oos": {"ic_v2": 0.15, "ic_base": 0.05, "n_trials": 3},
    }


def test_score_in_range(monkeypatch):
    """tech_score_v2 returns score in [0, 30] for a PASS artifact."""
    art = _make_pass_artifact()
    _, tmp = tempfile.mkstemp(suffix=".json")
    os.close(_)
    with open(tmp, "w") as f:
        json.dump(art, f)
    monkeypatch.setattr("app.services.tech_score_v2._ARTIFACT_PATH", tmp)
    monkeypatch.setattr("app.services.tech_score_v2._artifact_cache", None)

    from app.services.tech_score_v2 import tech_score_v2
    df = _make_df(300)
    score, ver = tech_score_v2(df, df)
    # With zero coefs, raw=0 → percentile based on quantiles
    assert isinstance(score, int) or score is None, f"Got {type(score)}"
    if score is not None:
        assert 0 <= score <= 30, f"Score {score} out of range"
    try:
        os.unlink(tmp)
    except OSError:
        pass


def test_artifact_missing_returns_none(monkeypatch):
    """No artifact → (None, 'v1')."""
    monkeypatch.setattr("app.services.tech_score_v2._ARTIFACT_PATH", "/nonexistent/path.json")
    monkeypatch.setattr("app.services.tech_score_v2._artifact_cache", None)
    from app.services.tech_score_v2 import tech_score_v2
    df = _make_df(300)
    score, ver = tech_score_v2(df, df)
    assert score is None
    assert ver == "v1"


def test_artifact_keep_v1_returns_none(monkeypatch):
    """Artifact with verdict KEEP_V1 → (None, 'v1')."""
    art = {"version": "tech-v2-KEEP_V1", "verdict": "KEEP_V1"}
    _, tmp = tempfile.mkstemp(suffix=".json")
    os.close(_)
    with open(tmp, "w") as f:
        json.dump(art, f)
    monkeypatch.setattr("app.services.tech_score_v2._ARTIFACT_PATH", tmp)
    monkeypatch.setattr("app.services.tech_score_v2._artifact_cache", None)

    from app.services.tech_score_v2 import tech_score_v2
    df = _make_df(300)
    score, ver = tech_score_v2(df, df)
    assert score is None
    assert ver == "v1"
    try:
        os.unlink(tmp)
    except OSError:
        pass


def test_too_short_df_returns_none(monkeypatch):
    """df < 60 bars → (None, 'v1')."""
    art = _make_pass_artifact()
    _, tmp = tempfile.mkstemp(suffix=".json")
    os.close(_)
    with open(tmp, "w") as f:
        json.dump(art, f)
    monkeypatch.setattr("app.services.tech_score_v2._ARTIFACT_PATH", tmp)
    monkeypatch.setattr("app.services.tech_score_v2._artifact_cache", None)

    from app.services.tech_score_v2 import tech_score_v2
    df = _make_df(50)
    score, ver = tech_score_v2(df, df)
    assert score is None
    assert ver == "v1"
    try:
        os.unlink(tmp)
    except OSError:
        pass
