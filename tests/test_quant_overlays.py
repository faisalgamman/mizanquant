"""Pure-core tests for the quant-fund overlays: PBO/CSCV, vol targeting + fractional
Kelly, sector-neutral z-scores, and the HMM regime model. All offline."""

from __future__ import annotations

import numpy as np
import pytest  # noqa: F401

from app.services.overfitting import pbo_cscv
from app.services.position_sizing import vol_target_multiplier, fractional_kelly
from app.services.sector_neutral import sector_neutral_zscores
from app.services.regime_hmm import regime_probabilities
from app.services.concentration import effective_number_of_bets


# ── ⑤ PBO / CSCV ──────────────────────────────────────────────────────────────

def test_pbo_low_for_genuine_edge():
    rng = np.random.default_rng(1)
    T, N = 120, 6
    M = rng.normal(0, 1, (T, N))
    M[:, 0] += 3.0                       # config 0 is genuinely, consistently best
    res = pbo_cscv(M, s_splits=10)
    assert res is not None and res["pbo"] <= 0.20 and res["trust"] == "high"


def test_pbo_high_for_pure_noise():
    rng = np.random.default_rng(2)
    M = rng.normal(0, 1, (120, 8))       # no real winner → IS-best is luck
    res = pbo_cscv(M, s_splits=10)
    assert res is not None and res["pbo"] >= 0.30      # substantial overfit probability


def test_pbo_none_when_too_small():
    assert pbo_cscv(np.zeros((3, 1)), s_splits=10) is None


# ── ③ vol targeting + fractional Kelly ───────────────────────────────────────

def test_vol_target_scales_inversely():
    assert vol_target_multiplier(0.07, target_vol=0.14) == 2.0 or \
           vol_target_multiplier(0.07, target_vol=0.14, cap=3.0) == 2.0
    hi = vol_target_multiplier(0.28, target_vol=0.14)   # high vol → trim below 1
    assert hi < 1.0
    assert vol_target_multiplier(None) == 1.0           # unknown → neutral


def test_vol_target_clamped():
    assert vol_target_multiplier(0.001, target_vol=0.14, cap=1.5) == 1.5   # capped
    assert vol_target_multiplier(10.0, target_vol=0.14) >= 0.3             # floored


def test_fractional_kelly_edge_and_clamp():
    assert fractional_kelly(None) == 1.0
    strong = fractional_kelly(0.65, win_loss_ratio=1.0)
    weak = fractional_kelly(0.50, win_loss_ratio=1.0)
    assert strong > weak                                # more edge → larger fraction
    assert 0.0 <= fractional_kelly(0.20, 1.0) <= 1.0    # negative edge floored at 0
    assert fractional_kelly(0.20, 1.0) == 0.0


# ── ⑥ sector-neutral z-scores ────────────────────────────────────────────────

def test_sector_neutral_strips_sector_level():
    # Tech is a high-RS sector; Energy low. Within each, one name leads its peers.
    values = {"AAA": 10, "BBB": 11, "CCC": 12,      # tech (high absolute)
              "XXX": 1, "YYY": 2, "ZZZ": 3}          # energy (low absolute)
    sectors = {k: "Tech" for k in ("AAA", "BBB", "CCC")}
    sectors.update({k: "Energy" for k in ("XXX", "YYY", "ZZZ")})
    z = sector_neutral_zscores(values, sectors, min_peers=3)
    # the sector LEADER is the same rank in both despite very different absolute values
    assert z["CCC"] == pytest.approx(z["ZZZ"], abs=1e-6)
    assert z["CCC"] > 0 and z["AAA"] < 0            # neutralised within sector


def test_sector_neutral_unknown_falls_back_global():
    z = sector_neutral_zscores({"A": 1, "B": 2, "C": 3}, {}, min_peers=3)
    assert set(z) == {"A", "B", "C"} and z["C"] > z["A"]   # global z, nobody dropped


# ── ④ HMM regime ─────────────────────────────────────────────────────────────

def test_regime_hmm_detects_crisis_tail():
    rng = np.random.default_rng(5)
    calm = 100 * np.cumprod(1 + rng.normal(0.0004, 0.004, 240))     # calm uptrend
    crash = calm[-1] * np.cumprod(1 + rng.normal(-0.01, 0.03, 60))   # volatile drawdown
    closes = np.concatenate([calm, crash])
    r = regime_probabilities(closes)
    if r is None:                       # deps missing → soft skip
        pytest.skip("hmm deps unavailable")
    assert abs(r["calm_bull"] + r["choppy"] + r["crisis"] - 1.0) < 1e-6
    assert r["crisis"] >= r["calm_bull"]           # the tail is the volatile regime


def test_regime_hmm_short_history_none():
    assert regime_probabilities(np.linspace(100, 110, 30)) is None


# ── ② effective number of bets ───────────────────────────────────────────────

def test_enb_uncorrelated_equals_n():
    assert effective_number_of_bets(np.eye(5)) == pytest.approx(5.0, abs=0.1)


def test_enb_collapses_when_correlated():
    corr = np.full((5, 5), 0.999) + np.eye(5) * 0.001    # one hidden bet
    enb = effective_number_of_bets(corr)
    assert enb is not None and enb < 1.5


def test_enb_between_for_block_correlation():
    # two independent blocks of 3 highly-correlated names → ~2 effective bets
    b = np.full((3, 3), 0.95) + np.eye(3) * 0.05
    C = np.block([[b, np.zeros((3, 3))], [np.zeros((3, 3)), b]])
    enb = effective_number_of_bets(C)
    assert 1.5 < enb < 3.5


def test_corr_alignment_handles_uneven_lengths():
    """Regression: uneven-length return series must NOT collapse the correlation matrix
    (a naive dropna over misaligned indices degenerates ENB to ~1)."""
    from app.services.concentration import _corr_from_returns
    rng = np.random.default_rng(9)
    base = rng.normal(0, 1, 200)
    rets = {"A": base + rng.normal(0, 0.4, 200),
            "B": (base + rng.normal(0, 0.4, 200))[-150:],   # correlated, shorter
            "C": rng.normal(0, 1, 180)}                      # independent, shorter
    syms, C = _corr_from_returns(rets)
    assert C is not None and C.shape == (3, 3)
    enb = effective_number_of_bets(C)
    assert enb is not None and enb > 1.5             # A+B cluster, C apart → ~2, not collapsed
