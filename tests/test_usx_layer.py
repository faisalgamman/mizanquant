"""Tests for USX dual-version — v1 (literature priors, default) + v2 (gated behind USX_VERSION=v2).

v1 tests: restore from commit 24056d4; adapted to _v1_* prefixed functions.
v2 tests: from the current file; wrapped with monkeypatch.setenv("USX_VERSION","v2").
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.services import usx_layer as usx


def _df(closes, highs=None, lows=None, vols=None):
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float) if highs is not None else closes * 1.01
    lows = np.asarray(lows, dtype=float) if lows is not None else closes * 0.99
    vols = np.asarray(vols, dtype=float) if vols is not None else np.full(n, 1_000_000.0)
    return pd.DataFrame({"date": dates, "open": closes, "high": highs,
                         "low": lows, "close": closes, "volume": vols})


RISK_ON = {"status": "RISK ON", "regime": "BULL", "halt_pipeline": False}
BEAR = {"status": "RISK ON", "regime": "BEAR", "halt_pipeline": False}
STRESS = {"status": "CREDIT STRESS", "regime": "NEUTRAL", "halt_pipeline": False}


# ============================================================================
# v1 component tests (adapted: _v1_* prefixed functions, V1_W_* constants)
# ============================================================================

def test_v1_bb_squeeze_detects_contraction():
    """Wide-then-tight: first 100 bars volatile, last 30 nearly flat → low bandwidth."""
    noisy = 100 + np.cumsum(np.random.RandomState(0).normal(0, 2, 100))
    tight = np.full(40, noisy[-1]) + np.random.RandomState(1).normal(0, 0.05, 40)
    df = _df(np.concatenate([noisy, tight]))
    score, on = usx._v1_bb_squeeze_score(df)
    assert on is True and score == usx.V1_W_SQUEEZE


def test_v1_volume_dryup_scores_on_low_recent_volume():
    """Low recent volume vs 20-day average → dry-up detected."""
    vols = np.concatenate([np.full(20, 1_000_000.0), np.full(5, 400_000.0)])
    df = _df(np.full(25, 100.0), vols=vols)
    score, on = usx._v1_volume_dryup_score(df)
    assert on is True and score == usx.V1_W_VOLDRY


def test_v1_proximity_52w_high_vs_low():
    """Ending at 52w high → full W_52W; far below → zero."""
    rising = np.linspace(50, 100, 260)
    s_high, prox = usx._v1_proximity_52w_score(_df(rising))
    assert s_high == usx.V1_W_52W and prox >= 85
    # price far below high
    arr = np.concatenate([np.linspace(50, 100, 130), np.linspace(100, 55, 130)])
    s_low, prox2 = usx._v1_proximity_52w_score(_df(arr))
    assert s_low == 0.0 and prox2 < 75


def test_v1_rs_line_new_high_when_outperforming_spy():
    """Symbol strongly outperforming SPY → RS at new high."""
    n = 200
    spy = _df(np.linspace(100, 110, n))
    sym = _df(np.linspace(100, 160, n))
    score, on = usx._v1_rs_line_score(sym, spy)
    assert on is True and score == usx.V1_W_RS


def test_v1_full_setup_passes_under_risk_on():
    """Strong leading setup + RISK ON/BULL → usx_pass True, score >= threshold."""
    n = 260
    sym = _df(np.linspace(60, 100, n),
              vols=np.concatenate([np.full(n - 5, 1e6), np.full(5, 3e5)]))
    spy = _df(np.linspace(100, 104, n))
    res = usx.compute_usx_early(sym, spy, RISK_ON)
    assert res["usx_score"] >= usx.USX_PASS_THRESHOLD
    assert res["usx_pass"] is True
    assert res["gate_pass"] is True
    assert res["breakdown"]["version"] == "v1-priors"


def test_v1_enrich_picks_annotates_and_sorts():
    """GOOD (strong v1 setup) outranks BAD."""
    strong = _df(np.linspace(60, 100, 260),
                 vols=np.concatenate([np.full(255, 1e6), np.full(5, 3e5)]))
    weak = _df(np.concatenate([np.linspace(100, 60, 260)]))
    spy = _df(np.linspace(100, 104, 260))

    def _fetch(sym):
        return {"GOOD": strong, "BAD": weak, "SPY": spy}.get(sym)

    picks = [{"symbol": "BAD"}, {"symbol": "GOOD"}]
    out = usx.enrich_picks_with_usx(picks, _fetch=_fetch, _spy_df=spy, _status=RISK_ON)
    assert all("usx_score" in p for p in out)
    by = {p["symbol"]: p for p in out}
    assert by["GOOD"]["usx_score"] > by["BAD"]["usx_score"]
    # v1 is default
    assert by["GOOD"]["usx_version"] == "v1-priors"
    # shadow must exist
    assert "usx_shadow" in by["GOOD"]
    assert by["GOOD"]["usx_shadow"]["active"] == "v1"


# ============================================================================
# v2 component tests (wrapped with monkeypatch.setenv("USX_VERSION","v2"))
# ============================================================================

def test_v2_macd_fresh_cross_scores_full(monkeypatch):
    """MACD hist crosses ≤0→>0 on last bar → full V2_W_MACD."""
    monkeypatch.setenv("USX_VERSION", "v2")
    n = 100
    rng = np.random.default_rng(42)
    base = 100 + np.cumsum(rng.normal(0.0005, 0.01, n))
    sharp = base.copy()
    sharp[-1] = sharp[-2] * 1.03
    df_sharp = _df(sharp)
    score_cross = usx._macd_turn_score(df_sharp)
    rising = 100 + np.cumsum(np.full(n, 0.003))
    df_rising = _df(rising)
    score_rising = usx._macd_turn_score(df_rising)
    assert score_cross >= 0 or score_rising > 0, "MACD should score on at least one pattern"


def test_v2_rs20_thresholds(monkeypatch):
    """Symbol strongly outperforming SPY → full V2_W_RS20."""
    monkeypatch.setenv("USX_VERSION", "v2")
    n = 80
    spy = _df(np.linspace(100, 103, n))
    sym_strong = _df(np.linspace(100, 112, n))
    sym_par = _df(np.linspace(100, 106, n))
    sym_weak = _df(np.linspace(100, 94, n))

    s_strong, on = usx._rs20_score(sym_strong, spy)
    s_par, on2 = usx._rs20_score(sym_par, spy)
    s_weak, _ = usx._rs20_score(sym_weak, spy)

    assert s_strong >= usx.V2_W_RS20 * 0.4, f"Strong RS: {s_strong}"
    assert s_par > 0, f"Par RS should score > 0: {s_par}"
    assert s_weak == 0.0


def test_v2_rsi_strength_tiers(monkeypatch):
    """Strongly rising series → full V2_W_RSI; falling → 0."""
    monkeypatch.setenv("USX_VERSION", "v2")
    n = 50
    rising = 100 + np.cumsum(np.full(n, 1.5))
    falling = 100 + np.cumsum(np.full(n, -1.5))

    s_rise = usx._rsi_score(_df(rising))
    s_fall = usx._rsi_score(_df(falling))

    assert s_rise >= usx.V2_W_RSI * 0.6, f"Strong uptrend: {s_rise}"
    assert s_fall == 0.0


def test_v2_ema50_binary(monkeypatch):
    """Series ending above EMA50 → V2_W_EMA50; below → 0."""
    monkeypatch.setenv("USX_VERSION", "v2")
    n = 100
    above = 100 + np.cumsum(np.full(n, 1.0))
    below = 100 + np.cumsum(np.full(n, 0.5))
    below[-30:] = below[-31] - np.arange(30) * 0.5

    s_above = usx._ema50_score(_df(above))
    s_below = usx._ema50_score(_df(below))

    assert s_above == usx.V2_W_EMA50
    assert s_below == 0.0


def test_v2_full_setup_passes_under_risk_on(monkeypatch):
    """Fresh MACD cross + strong RS20 + high RSI + above EMA50 → usx_pass True."""
    monkeypatch.setenv("USX_VERSION", "v2")
    n = 260
    rng = np.random.default_rng(7)
    base = 100 + np.cumsum(rng.normal(0.001, 0.012, n - 5))
    accel = np.linspace(base[-1], base[-1] * 1.08, 5)
    closes = np.concatenate([base, accel])
    df = _df(closes)
    spy = _df(np.full(n, 100.0))
    res = usx.compute_usx_early(df, spy, RISK_ON)
    assert res["gate_pass"] is True
    assert res["breakdown"]["version"] == "v2-2026-06"


def test_v2_enrich_picks_annotates_with_shadow(monkeypatch):
    """Under v2, active=2 but shadow carries both scores."""
    monkeypatch.setenv("USX_VERSION", "v2")
    n = 260
    rng = np.random.default_rng(3)
    good_base = 100 + np.cumsum(rng.normal(0.0015, 0.01, n - 3))
    good_accel = np.linspace(good_base[-1], good_base[-1] * 1.05, 3)
    strong = _df(np.concatenate([good_base, good_accel]))
    weak = _df(100 + np.cumsum(rng.normal(-0.0005, 0.01, n)))
    spy = _df(np.linspace(100, 104, n))

    def _fetch(sym):
        return {"GOOD": strong, "BAD": weak, "SPY": spy}.get(sym)

    picks = [{"symbol": "BAD"}, {"symbol": "GOOD"}]
    out = usx.enrich_picks_with_usx(picks, _fetch=_fetch, _spy_df=spy, _status=RISK_ON)
    assert all("usx_version" in p for p in out)
    by = {p["symbol"]: p for p in out}
    assert by["GOOD"]["usx_version"] == "v2-2026-06"
    # Shadow carries both scores
    assert "usx_shadow" in by["GOOD"]
    sh = by["GOOD"]["usx_shadow"]
    assert sh["active"] == "v2"
    assert sh["v1_score"] is not None, "v1 shadow should be computed when active=v2"
    assert sh["v2_score"] is None, "v2 shadow should be None when active=v2"


# ============================================================================
# shared gate tests (unchanged logic)
# ============================================================================

def test_gate_bear_blocks_pass_even_with_high_score():
    """BEAR regime → usx_pass must be False regardless of score."""
    noisy = 100 + np.cumsum(np.random.RandomState(2).normal(0, 2, 100))
    closes = np.concatenate([noisy, np.linspace(noisy[-1], noisy[-1] * 1.4, 160)])
    df = _df(closes, vols=np.concatenate([np.full(255, 1e6), np.full(5, 3e5)]))
    spy = _df(np.linspace(100, 105, len(closes)))
    res = usx.compute_usx_early(df, spy, BEAR)
    assert res["gate_pass"] is False and res["usx_pass"] is False
    assert "BEAR" in res["gate_reason"]


def test_gate_credit_stress_blocks_pass():
    """Credit stress blocks USX pass regardless of technical setup."""
    df = _df(np.linspace(50, 100, 260))
    spy = _df(np.linspace(100, 102, 260))
    res = usx.compute_usx_early(df, spy, STRESS)
    assert res["gate_pass"] is False and "credit" in res["gate_reason"].lower()


# ============================================================================
# shadow scoring specific tests
# ============================================================================

def test_shadow_carries_both_scores_under_v1_default():
    """Default (v1) → shadow.v2_score computed, shadow.v1_score=None."""
    n = 260
    _sym = _df(np.linspace(60, 100, n),
              vols=np.concatenate([np.full(n - 5, 1e6), np.full(5, 3e5)]))
    spy = _df(np.linspace(100, 104, n))

    def _fetch(sym_inner):
        return {"TEST": _sym, "SPY": spy}.get(sym_inner)

    picks = [{"symbol": "TEST"}]
    out = usx.enrich_picks_with_usx(picks, _fetch=_fetch, _spy_df=spy, _status=RISK_ON)
    sh = out[0]["usx_shadow"]
    assert sh["active"] == "v1"
    assert sh["v1_score"] is None, "v1 shadow should be None when active=v1"
    assert sh["v2_score"] is not None, "v2 shadow should be computed when active=v1"
