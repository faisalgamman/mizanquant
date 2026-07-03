"""Pure-core tests for VaR + cumulative-alpha (no network/DB)."""

from __future__ import annotations

from app.services.risk_metrics import parametric_var, cumulative_alpha


def test_parametric_var_scales():
    # 1M equity, 1% daily vol, 95% z=1.645 → ~16,450
    assert parametric_var(1_000_000, 0.01, 1.645) == 16450.0
    assert parametric_var(1_000_000, 0.02, 1.645) > parametric_var(1_000_000, 0.01, 1.645)
    assert parametric_var(0, 0.01) is None and parametric_var(1000, 0) is None
    assert parametric_var("x", 0.01) is None


def test_cumulative_alpha_running_sum():
    # SPY flat at 100 → alpha == trade return; cumulative sums in order
    spy = lambda ts: 100.0
    trades = [(2.0, "a", "b", "d1"), (-1.0, "c", "d", "d2"), (3.0, "e", "f", "d3")]
    out = cumulative_alpha(trades, spy)
    assert [r["cum_alpha"] for r in out] == [2.0, 1.0, 4.0]
    assert [r["date"] for r in out] == ["d1", "d2", "d3"]


def test_cumulative_alpha_subtracts_spy():
    # SPY doubles over the window (+100%): a +100% trade → 0 alpha
    spy = lambda ts: 100.0 if ts == "in" else 200.0
    out = cumulative_alpha([(100.0, "in", "out", "d1")], spy)
    assert abs(out[0]["cum_alpha"]) < 1e-6


def test_cumulative_alpha_skips_missing_spy():
    spy = lambda ts: None
    assert cumulative_alpha([(5.0, "a", "b", "d1")], spy) == []
