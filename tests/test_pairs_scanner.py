"""Tests for pairs_scanner (Phase 3A).

Verifies:
1. PairReport.as_dict has the required keys.
2. find_cointegrated_pairs discovers a planted cointegrated pair within a
   sector and rejects an independent random walk.
3. The correlation pre-filter drops uncorrelated candidates.
4. clear_cache resets state.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ── synthetic series (mirrors test_cointegration.py) ─────────────────────────

def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-04", periods=n, freq="B")


def _random_walk_series(name: str, n: int = 400, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    vals = np.cumsum(rng.normal(0.0, 1.0, n)) + 100.0
    return pd.Series(vals, index=_dates(n), name=name)


def _cointegrated_series(
    name_y: str, name_x: str, n: int = 400, beta: float = 1.0,
    alpha: float = 5.0, theta: float = 0.2, seed: int = 0,
) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0.0, 1.0, n)) + 100.0
    eps = np.empty(n)
    eps[0] = 0.0
    for t in range(1, n):
        eps[t] = (1.0 - theta) * eps[t - 1] + rng.normal(0.0, 1.0)
    y = alpha + beta * x + eps
    idx = _dates(n)
    return pd.Series(y, index=idx, name=name_y), pd.Series(x, index=idx, name=name_x)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_pair_report_as_dict_keys():
    from app.services.pairs_scanner import PairReport

    r = PairReport(
        y_symbol="AA", x_symbol="BB", sector="TECH", pvalue=0.01,
        hedge_ratio=1.2, intercept=3.0, half_life=12.0, zscore=-2.1,
    )
    d = r.as_dict()
    for key in ("pair", "y_symbol", "x_symbol", "sector", "pvalue",
                "hedge_ratio", "half_life_bars", "zscore"):
        assert key in d, f"missing key {key}"
    assert d["pair"] == "AA/BB"


def test_find_cointegrated_pairs_discovers_planted_pair(monkeypatch):
    pytest.importorskip("statsmodels")
    from app.services import pairs_scanner as scn

    # Build one cointegrated pair (AA, BB) and one independent walk (CC)
    y, x = _cointegrated_series("AA", "BB", n=400, beta=1.0, seed=11)
    cc = _random_walk_series("CC", n=400, seed=999)
    series_map = {"AA": y, "BB": x, "CC": cc}

    # All three in one sector
    monkeypatch.setattr(scn, "_group_by_sector", lambda: {"TECH": ["AA", "BB", "CC"]})
    monkeypatch.setattr(scn, "_close_series", lambda sym, lookback: series_map.get(sym))
    # Isolate cointegration logic from the correlation pre-filter
    monkeypatch.setattr(scn, "PAIRS_CORRELATION_MIN", 0.0)
    # Keep hermetic — no disk cache
    monkeypatch.setattr(scn, "_save_cache", lambda pairs: None)
    monkeypatch.setattr(scn, "_load_disk_cache", lambda: None)
    scn.clear_cache()

    pairs = scn.find_cointegrated_pairs(force_refresh=True)
    found = {p.pair_key for p in pairs}

    assert "AA/BB" in found, f"planted cointegrated pair not found; got {found}"
    # CC is an independent random walk — it should not cointegrate with AA/BB
    assert "AA/CC" not in found and "BB/CC" not in found, (
        f"independent random walk wrongly flagged cointegrated: {found}"
    )


def test_correlation_prefilter_drops_uncorrelated(monkeypatch):
    pytest.importorskip("statsmodels")
    from app.services import pairs_scanner as scn

    y, x = _cointegrated_series("AA", "BB", n=400, beta=1.0, seed=21)
    cc = _random_walk_series("CC", n=400, seed=555)
    series_map = {"AA": y, "BB": x, "CC": cc}

    monkeypatch.setattr(scn, "_group_by_sector", lambda: {"TECH": ["AA", "BB", "CC"]})
    monkeypatch.setattr(scn, "_close_series", lambda sym, lookback: series_map.get(sym))
    # Impossibly high correlation requirement → nothing survives the pre-filter
    monkeypatch.setattr(scn, "PAIRS_CORRELATION_MIN", 0.999)
    monkeypatch.setattr(scn, "_save_cache", lambda pairs: None)
    monkeypatch.setattr(scn, "_load_disk_cache", lambda: None)
    scn.clear_cache()

    pairs = scn.find_cointegrated_pairs(force_refresh=True)
    assert pairs == [], "correlation pre-filter should have dropped all candidates"


def test_clear_cache_resets(monkeypatch):
    from app.services import pairs_scanner as scn
    scn._cache_pairs = ["sentinel"]
    scn._cache_ts = 1.0
    scn.clear_cache()
    assert scn._cache_pairs is None
    assert scn._cache_ts == 0.0
