"""Offline validation of the factor lab engines (gate A/B replay + cross-sectional IC)
on a synthetic price panel — no network. A deterministic panel where up-trending names
(gate PASS, high RS) genuinely keep outperforming down-trending ones (gate FAIL) must
make the engines report: PASS beats FAIL, a positive alpha uplift, and a positive RS IC.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest  # noqa: F401

from app.services import factor_lab as fl
from app.services.backtest_engine import factor_rs_vs_spy, factor_random


def _panel(rows: int = 400) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-01", periods=rows, tz="UTC")
    cols = {"SPY": 100.0 * np.power(1.0003, np.arange(rows))}
    for k in range(6):                       # up-trending: above EMA20, RS > SPY
        cols[f"UP{k}"] = 100.0 * np.power(1.0011, np.arange(rows)) * (1 + 0.0005 * k)
    for k in range(6):                       # down-trending: below EMA20, RS < SPY
        cols[f"DN{k}"] = 100.0 * np.power(0.9990, np.arange(rows)) * (1 + 0.0005 * k)
    return pd.DataFrame(cols, index=idx)


def test_gate_ab_replay_prefers_with_trend(monkeypatch):
    monkeypatch.setenv("WEEKLY_ENTRY_FILTER", "true")
    monkeypatch.setenv("WEEKLY_MIN_RS", "-2")
    syms = [c for c in _panel().columns if c != "SPY"]
    r = fl.gate_ab_replay(syms, _mat=_panel(), warmup=120, rebalance_days=10, hold_days=20)
    assert "error" not in r
    assert r["pass"]["n"] > 0 and r["fail"]["n"] > 0
    # up-trend (PASS) forward returns beat down-trend (FAIL) ones
    assert r["pass"]["mean_ret_pct"] > r["fail"]["mean_ret_pct"]
    # the gate lifts alpha vs the pooled book, and the two arms genuinely differ
    assert r["alpha_uplift_pct"] > 0
    assert r["t_pass_vs_fail"] is not None and r["t_pass_vs_fail"] > 2


def test_cross_sectional_ic_positive_for_rs(monkeypatch):
    syms = [c for c in _panel().columns if c != "SPY"]
    ic = fl.cross_sectional_ic(syms, factor_rs_vs_spy, _mat=_panel(),
                               warmup=120, rebalance_days=10, hold_days=20)
    assert "error" not in ic and ic["n_dates"] > 0
    assert ic["mean_ic"] > 0            # higher RS → higher forward return
    assert ic["ic_ir"] is not None and ic["ic_ir"] > 0


def test_cross_sectional_ic_random_is_near_zero(monkeypatch):
    syms = [c for c in _panel().columns if c != "SPY"]
    ic = fl.cross_sectional_ic(syms, factor_random, _mat=_panel(),
                               warmup=120, rebalance_days=10, hold_days=20)
    # a no-skill factor should not show a strong information ratio
    assert ic["n_dates"] > 0
    assert ic["ic_ir"] is None or abs(ic["ic_ir"]) < 2.5


def test_gate_ab_insufficient_history_is_flagged():
    short = _panel(rows=150)
    syms = [c for c in short.columns if c != "SPY"]
    r = fl.gate_ab_replay(syms, _mat=short, warmup=252, rebalance_days=5, hold_days=20)
    assert r.get("error") == "insufficient history"


# --- ①② self-calibrating gate ------------------------------------------------

def test_eval_gate_threshold_excludes_low_rs():
    # records: (di, wk_rs, above_ema20, fwd, spy_fwd) — high-RS names return more
    recs = [(0, 5.0, 1, 0.10, 0.01), (0, 4.0, 1, 0.08, 0.01),
            (0, -6.0, 1, -0.04, 0.01), (0, -8.0, 0, -0.05, 0.01)]
    loose = fl._eval_gate(recs, min_rs=-10, require_ema20=True)   # passes 3 (ema ok)
    strict = fl._eval_gate(recs, min_rs=0, require_ema20=True)    # passes only the 2 high-RS
    assert loose["n_pass"] == 3 and strict["n_pass"] == 2
    assert strict["pass_ret_pct"] > loose["pass_ret_pct"]         # tighter → better avg


def test_gate_threshold_sweep_ranks_by_uplift():
    syms = [c for c in _panel().columns if c != "SPY"]
    r = fl.gate_threshold_sweep(syms, _mat=_panel(), warmup=120, rebalance_days=10, hold_days=20)
    assert "error" not in r and r["n_records"] > 0
    ups = [g["uplift_pct"] for g in r["grid"] if g["uplift_pct"] is not None]
    assert ups == sorted(ups, reverse=True)          # sorted best-first
    assert r["best"]["uplift_pct"] >= 0


def test_gate_calibration_oos_split_and_reco(monkeypatch):
    syms = [c for c in _panel(rows=460).columns if c != "SPY"]
    cal = fl.gate_calibration(_mat=_panel(rows=460), grid=[-10, -5, -2, 0, 2],
                              min_t=1.0, _cache=False)
    assert "error" not in cal
    assert cal["split"]["train"] > 0 and cal["split"]["test"] > 0
    # every grid row carries an OOS (train + test) evaluation
    assert all("train" in g and "test" in g for g in cal["grid"])
    assert "recommendation" in cal and cal["recommendation"]["action"] in ("keep", "raise", "lower")
