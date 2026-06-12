"""Test that /scoring/weighted passes spy_df to _analyze_smart."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pd = pytest.importorskip("pandas")


@pytest.fixture
def client():
    from app.workspace_server import app
    return TestClient(app)


def test_scoring_passes_spy_df_to_analyze_smart(client, monkeypatch):
    """When SPY data is available, _analyze_smart receives spy_df."""
    n = 130
    import numpy as np
    closes = np.linspace(100, 110, n)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    df_sym = pd.DataFrame({
        "date": dates, "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes, "volume": np.full(n, 10_000_000),
    })

    def _mock_fetch(sym, period=None):
        return df_sym

    monkeypatch.setattr("app.services.market_data.fetch", _mock_fetch)

    # Capture _analyze_smart calls
    calls = []

    def _capture_analyze(symbol, spy_df=None):
        calls.append({"symbol": symbol, "spy_df_is_none": spy_df is None})
        return {"smart_score": 85, "verdict": "STRONG BUY"}

    monkeypatch.setattr("app.workspace_server._analyze_smart", _capture_analyze)

    resp = client.get("/api/v1/scoring/weighted", params={"symbol": "AAPL"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("smart_score") == 85
    assert data.get("smart_verdict") == "STRONG BUY"
    assert len(calls) >= 1
    assert not calls[0]["spy_df_is_none"], "_analyze_smart should receive spy_df"


def test_scoring_marks_rs_unavailable_when_spy_fails(client, monkeypatch):
    """When _analyze_smart raises, endpoint still returns 200 with rs_unavailable: true."""
    n = 130
    import numpy as np
    closes = np.linspace(100, 110, n)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    df_sym = pd.DataFrame({
        "date": dates, "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes, "volume": np.full(n, 10_000_000),
    })

    def _mock_fetch(sym, period=None):
        return df_sym

    monkeypatch.setattr("app.services.market_data.fetch", _mock_fetch)

    def _raise_analyze(*args, **kwargs):
        raise RuntimeError("SPY unavailable")

    monkeypatch.setattr("app.workspace_server._analyze_smart", _raise_analyze)

    resp = client.get("/api/v1/scoring/weighted", params={"symbol": "AAPL"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("rs_unavailable") is True
    assert "smart_score" not in data
