"""Phase 8: paper-trade graduation gate tests.

Property tests on synthetic paper-trade samples:

- Sample with too few trades → blocked (n_trades reason)
- Sample spanning too few days → blocked (calendar_days reason)
- Sample on too few symbols → blocked (unique_symbols reason)
- Sample with negative mean → blocked (mean_return reason)
- Sample with weak edge (low DSR) → blocked (DSR reason)
- Strong, long, broad sample with positive edge → graduated
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from app.services.paper_trade_gate import evaluate_graduation


def _make_sample(
    n: int,
    mu: float,
    sigma: float,
    n_symbols: int,
    span_days: int,
    seed: int = 0,
):
    rng = np.random.default_rng(seed)
    rets = rng.normal(mu, sigma, n).tolist()
    base = datetime(2026, 1, 1)
    times = [
        base + timedelta(days=int(i * span_days / max(n - 1, 1)))
        for i in range(n)
    ]
    syms = [f"SYM{i % n_symbols}" for i in range(n)]
    return rets, times, syms


def test_too_few_trades_blocks():
    rets, times, syms = _make_sample(n=10, mu=0.01, sigma=0.02, n_symbols=10, span_days=30, seed=1)
    s = evaluate_graduation(rets, times, syms, min_trades=30)
    assert s.graduated is False
    assert "n_trades" in s.reason


def test_too_few_calendar_days_blocks():
    rets, times, syms = _make_sample(n=50, mu=0.01, sigma=0.02, n_symbols=10, span_days=5, seed=2)
    s = evaluate_graduation(rets, times, syms, min_calendar_days=14)
    assert s.graduated is False
    assert "calendar_days" in s.reason


def test_too_few_symbols_blocks():
    rets, times, syms = _make_sample(n=60, mu=0.01, sigma=0.02, n_symbols=2, span_days=30, seed=3)
    s = evaluate_graduation(rets, times, syms, min_unique_symbols=8)
    assert s.graduated is False
    assert "unique_symbols" in s.reason


def test_negative_mean_blocks():
    rets, times, syms = _make_sample(n=60, mu=-0.005, sigma=0.02, n_symbols=10, span_days=30, seed=4)
    s = evaluate_graduation(rets, times, syms)
    assert s.graduated is False
    assert "mean_return" in s.reason


def test_weak_edge_blocks_via_dsr():
    # Tiny edge: μ=0.0005, σ=0.02 → Sharpe ~0.025; DSR will be near 0.
    rets, times, syms = _make_sample(n=60, mu=0.0005, sigma=0.02, n_symbols=10, span_days=30, seed=5)
    s = evaluate_graduation(rets, times, syms, min_dsr=0.60)
    assert s.graduated is False
    assert "DSR" in s.reason


def test_strong_broad_long_sample_graduates():
    # μ=0.005, σ=0.01 → Sharpe ~0.5, annualized ~8 → DSR essentially 1.0.
    rets, times, syms = _make_sample(n=60, mu=0.005, sigma=0.01, n_symbols=10, span_days=30, seed=6)
    s = evaluate_graduation(
        rets, times, syms,
        min_trades=30, min_calendar_days=14, min_unique_symbols=8, min_dsr=0.60,
    )
    assert s.graduated is True, f"expected graduated, got: {s.reason}"
    assert s.dsr >= 0.60
    assert s.mean_return > 0


def test_n_trials_shrinks_dsr_and_can_block():
    """If we announce many trials (parameter searches), multiple-testing
    adjustment must shrink DSR. The same sample that graduates at
    n_trials=1 should be borderline / blocked at n_trials=1000."""
    rets, times, syms = _make_sample(n=60, mu=0.002, sigma=0.01, n_symbols=10, span_days=30, seed=7)
    s_one = evaluate_graduation(rets, times, syms, n_trials_announced=1)
    s_thousand = evaluate_graduation(rets, times, syms, n_trials_announced=1000)
    assert s_thousand.dsr <= s_one.dsr


def test_diagnostics_round_trip_through_dict():
    rets, times, syms = _make_sample(n=40, mu=0.003, sigma=0.01, n_symbols=10, span_days=20, seed=8)
    s = evaluate_graduation(rets, times, syms)
    d = s.as_dict()
    for key in ("graduated", "reason", "n_trades", "calendar_days",
                "unique_symbols", "mean_return_pct", "sharpe", "dsr"):
        assert key in d
