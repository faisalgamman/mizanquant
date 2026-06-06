"""Prove the vectorised scorer is byte-identical to the scalar one.

`score_series` replaces `df.apply(get_score, axis=1)` in the backtest hot path
(M-C). It is only a safe substitution if it returns the *exact* same integer for
every row, so this test fuzzes a wide grid of inputs — including the branch
boundaries (RSI 35/40/60/65, hist sign flips, vol 1.2/1.5, atr 1%/4%) — and
asserts elementwise equality against the original `get_score`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.technical import get_score, score_series


_COLS = ["close", "ema21", "rsi", "rsi_prev", "hist", "hist_prev",
         "vol_ratio", "atr_v", "support"]


def _random_frame(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = rng.uniform(10, 500, n)
    return pd.DataFrame({
        "close": close,
        "ema21": close * rng.uniform(0.9, 1.1, n),
        # span the RSI branch boundaries (35/40/60/65) densely
        "rsi": rng.uniform(20, 80, n),
        "rsi_prev": rng.uniform(20, 80, n),
        # straddle zero so both hist arms fire
        "hist": rng.uniform(-2, 2, n),
        "hist_prev": rng.uniform(-2, 2, n),
        # straddle the 1.2 / 1.5 volume thresholds
        "vol_ratio": rng.uniform(0.8, 2.0, n),
        # straddle the 1%–4% ATR band
        "atr_v": close * rng.uniform(0.005, 0.06, n),
        "support": close * rng.uniform(0.95, 1.05, n),
    })


def test_vectorised_matches_scalar_on_fuzzed_data():
    for seed in range(25):
        df = _random_frame(200, seed)
        expected = df.apply(get_score, axis=1).astype(int)
        actual = score_series(df)
        assert (actual.values == expected.values).all(), f"mismatch at seed {seed}"


def test_exact_boundary_values():
    # Hand-built rows sitting ON each branch boundary.
    df = pd.DataFrame({
        "close":     [100, 100, 100, 100],
        "ema21":     [100, 99,  101, 100],   # >ema only for row1
        "rsi":       [60,  65,  40,  35],    # inclusive upper/lower bounds
        "rsi_prev":  [59,  64,  39,  34],    # all rising
        "hist":      [1,   1,   -1,  0],     # row0 fresh cross, row1 rising, others off
        "hist_prev": [-1,  0.5, -2,  0],
        "vol_ratio": [1.5, 1.2, 1.19, 0.5],  # exact thresholds
        "atr_v":     [1.0, 4.0, 0.99, 5.0],  # exact 1% and 4% band edges
        "support":   [98,  98,  97.9, 90],   # within / outside 2%
    })
    expected = df.apply(get_score, axis=1).astype(int)
    actual = score_series(df)
    assert list(actual.values) == list(expected.values)
