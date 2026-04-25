"""Phase 1: optimizer must apply costs and avoid same-bar look-ahead,
and must report Deflated Sharpe alongside raw Sharpe.

These are unit tests on the helpers; the full optimize_params() loop hits
the network (yfinance) so we don't run it here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.optimizer import _simulate_trades


def _frame(n: int = 100, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, 0.01, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame({
        "open":   close * (1.0 + rng.normal(0.0, 0.001, n)),
        "high":   close * (1.0 + np.abs(rng.normal(0.0, 0.003, n))),
        "low":    close * (1.0 - np.abs(rng.normal(0.0, 0.003, n))),
        "close":  close,
        "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    })


def test_simulate_trades_returns_pooled_pnls_for_dsr():
    df = _frame(n=80, seed=11)
    signals = [{"bar_idx": 30, "price": float(df["close"].iloc[30]),
                "atr": 1.0, "votes_buy": 7, "confidence": 70}]
    params = {"sl_atr_mult": 1.5, "tp1_atr_mult": 1.5}
    out = _simulate_trades(df, signals, params)
    assert out["n_trades"] == 1
    assert "_pnls_pct" in out
    assert isinstance(out["_pnls_pct"], list)
    assert len(out["_pnls_pct"]) == 1


def test_simulate_trades_uses_next_bar_open_not_signal_close(monkeypatch):
    """Mutating the SIGNAL bar's close must NOT change the simulated entry,
    because entry should be at next bar's OPEN."""
    df_a = _frame(n=80, seed=22)
    df_b = df_a.copy()
    # Bump signal-bar close by +50% but leave next bar's open unchanged.
    df_b.loc[30, "close"] = df_a.loc[30, "close"] * 1.5

    signals = [{"bar_idx": 30, "price": float(df_a["close"].iloc[30]),
                "atr": 1.0, "votes_buy": 7, "confidence": 70}]
    params = {"sl_atr_mult": 1.5, "tp1_atr_mult": 1.5}

    out_a = _simulate_trades(df_a, signals, params)
    out_b = _simulate_trades(df_b, signals, params)

    # Same trade outcome (entry comes from open[31], which is identical).
    assert out_a["_pnls_pct"] == out_b["_pnls_pct"]


def test_simulate_trades_applies_costs():
    """Round-trip cost should reduce a flat-price 'trade' to negative pnl."""
    n = 60
    flat_close = np.full(n, 100.0)
    df = pd.DataFrame({
        "open":   flat_close.copy(),
        "high":   flat_close + 0.001,
        "low":    flat_close - 0.001,
        "close":  flat_close,
        "volume": np.ones(n) * 1e6,
    })
    signals = [{"bar_idx": 10, "price": 100.0, "atr": 1.0,
                "votes_buy": 7, "confidence": 70}]
    params = {"sl_atr_mult": 1.5, "tp1_atr_mult": 1.5}
    out = _simulate_trades(df, signals, params)
    # With perfectly flat prices, the time-stop exit returns ~0 minus 40bps cost.
    pnl = out["_pnls_pct"][0]
    assert pnl < 0, f"expected negative pnl from costs, got {pnl}%"
    assert pnl > -1.0, f"loss too large for 40bps round-trip: {pnl}%"
