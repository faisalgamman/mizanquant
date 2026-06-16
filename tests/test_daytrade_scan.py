"""Technical 'explosion' day-trade scanner (research-only).

Covers the ranking math (explosive > flat) and that scan_explosion ranks desc + threads a
cache-only halal flag. No FMP / no live halal screen are involved.
"""
import numpy as np
import pandas as pd

import app.services.daytrade_scan as dt


def _df(vol, vlast, jump=0.0, gap=0.0, n=120):
    rng = np.random.default_rng(2)
    close = np.maximum(100 + np.cumsum(rng.normal(0, vol, n)), 1.0)
    if jump:
        close[-1] = close[-2] * (1 + jump)
    op = close.copy()
    if gap:
        op[-1] = close[-2] * (1 + gap)
    volume = np.where(np.arange(n) == n - 1, 1e6 * vlast, 1e6)
    return pd.DataFrame({"open": op, "high": np.maximum(close, op) * 1.01,
                         "low": np.minimum(close, op) * 0.99, "close": close, "volume": volume})


def test_explosion_score_ranks_explosive_above_flat():
    se, fields = dt.explosion_score(_df(0.6, 4, jump=0.06, gap=0.03))
    sf, _ = dt.explosion_score(_df(0.3, 1))
    assert 0 <= sf < se <= 100
    assert fields["components"]["rvol"] > 0 and "price" in fields


def test_explosion_score_too_short_returns_none():
    assert dt.explosion_score(_df(0.5, 1, n=10)) is None


def test_scan_explosion_ranks_and_flags(monkeypatch):
    dfs = {"BOOM": _df(0.6, 5, jump=0.08, gap=0.04), "FLAT": _df(0.2, 1)}
    monkeypatch.setattr("app.services.market_data.fetch_alpaca_batch",
                        lambda syms, period="1y": dfs)
    # cache-only verdict flag (no DB / no screening)
    monkeypatch.setattr(dt, "_cached_verdicts", lambda syms: {"BOOM": "non_compliant"})

    rows = dt.scan_explosion(["BOOM", "FLAT"], limit=10)
    assert [r["symbol"] for r in rows] == ["BOOM", "FLAT"]      # ranked desc by explosion score
    assert rows[0]["explosion_score"] > rows[1]["explosion_score"]
    assert rows[0]["halal_verdict"] == "non_compliant"          # flag threaded through (research view)
    assert rows[1]["halal_verdict"] is None                     # not cached → None (UI shows "—")
    assert "components" in rows[0]
