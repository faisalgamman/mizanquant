"""Backtest engine — metrics math, a factor, and an end-to-end on a synthetic matrix."""

import numpy as np
import pandas as pd

from app.services.backtest_engine import (
    _metrics,
    factor_momentum_12_1,
    factor_rs_vs_spy,
    run_backtest,
)


def test_metrics_basic():
    r = _metrics([0.02, -0.01, 0.03], [0.0, 0.0, 0.0], rebalance_days=20)
    assert r["n"] == 3
    assert r["win_rate_pct"] == round(200 / 3, 1)          # 2 of 3
    assert r["mean_alpha_pct"] == round((0.02 - 0.01 + 0.03) / 3 * 100, 3)
    assert r["profit_factor"] == round((0.02 + 0.03) / 0.01, 2)


def test_metrics_empty():
    assert _metrics([], [], rebalance_days=20)["n"] == 0


def test_rs_factor_positive_when_outperforming():
    up = np.array([100.0 * (1.01 ** i) for i in range(70)])     # +1%/day
    flat = np.array([100.0] * 70)
    assert factor_rs_vs_spy(up, flat, lookback=63) > 0
    assert factor_rs_vs_spy(flat, up, lookback=63) < 0


def _synth_matrix():
    idx = pd.date_range("2022-01-03", periods=400, freq="B", tz="UTC")
    data = {"SPY": 100.0 * (1.0002 ** np.arange(400))}
    for s in ("UP1", "UP2", "UP3"):
        data[s] = 100.0 * (1.002 ** np.arange(400))    # strong uptrend
    for s in ("DN1", "DN2", "DN3"):
        data[s] = 100.0 * (0.998 ** np.arange(400))    # downtrend
    return pd.DataFrame(data, index=idx)


def test_momentum_backtest_picks_the_trenders():
    mat = _synth_matrix()
    rep = run_backtest(["UP1", "UP2", "UP3", "DN1", "DN2", "DN3"], factor_momentum_12_1,
                       top_n=3, warmup=252, hold_days=20, rebalance_days=20, _mat=mat)
    assert rep["n"] > 0
    assert rep["mean_ret_pct"] > 0            # it longs the up-trenders
    assert rep["win_rate_pct"] >= 90.0        # they keep rising
    assert rep["mean_alpha_pct"] > 0          # and beat flat-ish SPY


def test_backtest_insufficient_data():
    small = _synth_matrix().iloc[:100]
    out = run_backtest(["UP1", "DN1"], factor_momentum_12_1, warmup=252, _mat=small)
    assert "error" in out
