"""Tests for the USX early-entry overlay — leading-signal scoring + gates."""
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


def test_bb_squeeze_detects_contraction():
    # Wide-then-tight: first 100 bars volatile, last 30 nearly flat → low bandwidth now
    noisy = 100 + np.cumsum(np.random.RandomState(0).normal(0, 2, 100))
    tight = np.full(40, noisy[-1]) + np.random.RandomState(1).normal(0, 0.05, 40)
    df = _df(np.concatenate([noisy, tight]))
    score, on = usx._bb_squeeze_score(df)
    assert on is True and score == usx.W_SQUEEZE


def test_volume_dryup_scores_on_low_recent_volume():
    vols = np.concatenate([np.full(20, 1_000_000.0), np.full(5, 400_000.0)])  # recent dry-up
    df = _df(np.full(25, 100.0), vols=vols)
    score, on = usx._volume_dryup_score(df)
    assert on is True and score == usx.W_VOLDRY


def test_proximity_52w_high_vs_low():
    rising = np.linspace(50, 100, 260)             # ends at the high
    s_high, prox = usx._proximity_52w_score(_df(rising))
    assert s_high == usx.W_52W and prox >= 85
    # price far below high
    arr = np.concatenate([np.linspace(50, 100, 130), np.linspace(100, 55, 130)])
    s_low, prox2 = usx._proximity_52w_score(_df(arr))
    assert s_low == 0.0 and prox2 < 75


def test_rs_line_new_high_when_outperforming_spy():
    n = 200
    spy = _df(np.linspace(100, 110, n))            # SPY +10%
    sym = _df(np.linspace(100, 160, n))            # symbol +60% → RS at new high
    score, on = usx._rs_line_score(sym, spy)
    assert on is True and score == usx.W_RS


def test_gate_bear_blocks_pass_even_with_high_score():
    # Perfect leading setup but BEAR regime → usx_pass must be False
    noisy = 100 + np.cumsum(np.random.RandomState(2).normal(0, 2, 100))
    closes = np.concatenate([noisy, np.linspace(noisy[-1], noisy[-1] * 1.4, 160)])
    df = _df(closes, vols=np.concatenate([np.full(255, 1e6), np.full(5, 3e5)]))
    spy = _df(np.linspace(100, 105, len(closes)))
    res = usx.compute_usx_early(df, spy, BEAR)
    assert res["gate_pass"] is False and res["usx_pass"] is False
    assert "BEAR" in res["gate_reason"]


def test_gate_credit_stress_blocks_pass():
    df = _df(np.linspace(50, 100, 260))
    spy = _df(np.linspace(100, 102, 260))
    res = usx.compute_usx_early(df, spy, STRESS)
    assert res["gate_pass"] is False and "credit" in res["gate_reason"].lower()


def test_full_setup_passes_under_risk_on():
    # Strong leading setup + RISK ON/BULL → usx_pass True, score >= threshold
    n = 260
    sym = _df(np.linspace(60, 100, n),
              vols=np.concatenate([np.full(n - 5, 1e6), np.full(5, 3e5)]))
    spy = _df(np.linspace(100, 104, n))
    res = usx.compute_usx_early(sym, spy, RISK_ON)
    assert res["usx_score"] >= usx.USX_PASS_THRESHOLD
    assert res["usx_pass"] is True
    assert res["gate_pass"] is True


def test_enrich_picks_annotates_and_sorts():
    strong = _df(np.linspace(60, 100, 260),
                 vols=np.concatenate([np.full(255, 1e6), np.full(5, 3e5)]))
    weak = _df(np.concatenate([np.linspace(100, 60, 260)]))
    spy = _df(np.linspace(100, 104, 260))

    def _fetch(sym):
        return {"GOOD": strong, "BAD": weak, "SPY": spy}.get(sym)

    picks = [{"symbol": "BAD"}, {"symbol": "GOOD"}]
    out = usx.enrich_picks_with_usx(picks, _fetch=_fetch, _spy_df=spy, _status=RISK_ON)
    assert all("usx_score" in p for p in out)
    # GOOD (usx_pass True) should score higher than BAD
    by = {p["symbol"]: p for p in out}
    assert by["GOOD"]["usx_score"] > by["BAD"]["usx_score"]
