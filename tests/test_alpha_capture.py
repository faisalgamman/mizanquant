"""① alpha-capture attribution + ② meta-label model, on an in-memory snapshot panel."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.database as dbmod
from app.db.database import Base
import app.db.models  # noqa: F401 — register tables
from app.db.models import FactorSnapshot


@pytest.fixture
def sdb(monkeypatch, tmp_path):
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSession)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))     # meta_model.json lands here
    return TestSession


def _seed(session, n_dates=8, n_syms=24, seed=0):
    """Panel where rs predicts the forward win, rsi is pure noise."""
    rng = np.random.default_rng(seed)
    db = session()
    try:
        for d in range(n_dates):
            sd = datetime(2026, 1, 1) + timedelta(days=d)
            for k in range(n_syms):
                rs = float(rng.normal(0, 5))
                fwd = 0.6 * rs + float(rng.normal(0, 3))     # rs carries signal
                db.add(FactorSnapshot(
                    snap_date=sd, symbol=f"S{k}", price=100.0,
                    factors={"rs": rs, "rsi": float(rng.uniform(30, 70)),
                             "above_ema20": int(rng.random() > 0.5),
                             "atr_pct": 2.0, "dist_ema20_pct": 0.0,
                             "mom_12_1": float(rng.normal(0, 8))},
                    fwd_ret={"10": round(fwd, 3)}))
        db.commit()
    finally:
        db.close()


# ── ② triple barrier (pure) ──────────────────────────────────────────────────

def test_triple_barrier_upper_lower_vertical():
    from app.services.meta_label import triple_barrier_labels
    up = [100, 101, 106]                       # +6% hit → win
    dn = [100, 99, 94]                          # −6% hit → loss
    assert triple_barrier_labels(up, 0, pt_pct=5, sl_pct=5, max_days=5) == 1
    assert triple_barrier_labels(dn, 0, pt_pct=5, sl_pct=5, max_days=5) == 0
    flat_up = [100, 100.5, 101, 102]           # neither barrier → vertical sign (up)
    assert triple_barrier_labels(flat_up, 0, pt_pct=5, sl_pct=5, max_days=3) == 1


# ── ① snapshot attribution ───────────────────────────────────────────────────

def test_snapshot_attribution_finds_rs_signal(sdb):
    _seed(sdb)
    from app.services.alpha_capture import snapshot_attribution, capture_status
    rep = snapshot_attribution(horizon_days=10)
    assert rep["labelled_dates"] > 0
    rs = rep["factors"]["rs"]
    assert rs["n_dates"] > 0 and rs["mean_ic"] > 0.2      # rs carries the signal
    assert capture_status()["labelled"] > 0


# ── ② meta model round-trip ──────────────────────────────────────────────────

def test_meta_model_trains_and_scores(sdb):
    _seed(sdb, n_dates=10, n_syms=30)
    from app.services.meta_label import train_meta_model, meta_probability, meta_model_status
    res = train_meta_model(horizon_days=10)
    assert res["status"] == "trained" and res["auc_in_sample"] > 0.55
    # higher RS → higher modelled P(win)
    base = {"rsi": 50, "above_ema20": 1, "atr_pct": 2.0, "dist_ema20_pct": 0.0, "mom_12_1": 0.0}
    p_hi = meta_probability({**base, "rs": 8.0})
    p_lo = meta_probability({**base, "rs": -8.0})
    assert p_hi is not None and p_lo is not None and p_hi > p_lo
    assert meta_model_status()["status"] == "trained"


def test_meta_probability_none_when_untrained(sdb):
    from app.services.meta_label import meta_probability
    assert meta_probability({"rs": 1.0}) is None       # no model file yet → fall back


# ── ① historical backfill (+ ③ multi-horizon) ────────────────────────────────

def test_rsi_series_bounds():
    from app.services.alpha_capture import _rsi_series
    up = _rsi_series(np.linspace(100, 200, 60))       # steady uptrend → RSI high
    dn = _rsi_series(np.linspace(200, 100, 60))       # steady downtrend → RSI low
    assert up[-1] > 70 and dn[-1] < 30


def test_pit_regime_labels_are_causal_and_detect_crisis():
    rng = np.random.default_rng(6)
    calm = 100 * np.cumprod(1 + rng.normal(0.0004, 0.004, 240))
    crash = calm[-1] * np.cumprod(1 + rng.normal(-0.01, 0.03, 60))
    from app.services.alpha_capture import _pit_regime_labels
    lab = _pit_regime_labels(np.concatenate([calm, crash]))
    assert len(lab) == 300
    assert lab[-1] == "crisis"                         # volatile tail flagged
    assert "calm_bull" in lab[:240]                    # calm stretch seen


def test_backfill_multi_horizon_and_idempotent(sdb, monkeypatch):
    import pandas as pd
    idx = pd.bdate_range("2023-01-01", periods=400, tz="UTC")
    cols = {"SPY": 100 * np.cumprod(1 + np.random.default_rng(1).normal(0.0002, 0.008, 400))}
    for k in range(8):
        cols[f"S{k}"] = 100 * np.cumprod(1 + np.random.default_rng(k + 2).normal(0.0004, 0.012, 400))
    panel = pd.DataFrame(cols, index=idx)
    monkeypatch.setattr("app.services.backtest_engine._aligned_closes", lambda syms, period: panel)

    from app.services.alpha_capture import backfill_snapshots
    syms = [c for c in panel.columns if c != "SPY"]
    r = backfill_snapshots(symbols=syms, warmup=120, rebalance_days=10, horizons=(5, 10, 20))
    assert "error" not in r and r["stored"] > 100
    db = sdb()
    try:
        row = db.query(FactorSnapshot).first()
        assert set(row.fwd_ret.keys()) == {"5", "10", "20"}         # ③ multi-horizon labels
        assert "rs" in row.factors and row.factors.get("backfill") == 1
        assert "regime" in row.factors
    finally:
        db.close()
    r2 = backfill_snapshots(symbols=syms, warmup=120, rebalance_days=10, horizons=(5, 10, 20))
    assert r2["stored"] == 0 and r2["skipped"] > 0                  # idempotent


def test_unlabelled_rows_counted_and_matured(sdb, monkeypatch):
    """Regression: a fwd_ret=None row must count as UNlabelled and be picked up by
    label_snapshots (a JSON None can round-trip as JSON 'null', so an is_(None) SQL filter
    silently labels nothing)."""
    import pandas as pd
    from app.services.alpha_capture import capture_status, label_snapshots
    db = sdb()
    try:
        db.add(FactorSnapshot(snap_date=datetime(2026, 1, 1), symbol="AAA", price=100.0,
                              factors={"rs": 1.0}, fwd_ret=None))
        db.commit()
    finally:
        db.close()
    assert capture_status()["labelled"] == 0            # None → NOT labelled
    monkeypatch.setattr("app.services.market_data.fetch",
                        lambda *a, **k: pd.DataFrame({"close": [1.0] * 30}))
    post = pd.DataFrame({"close": [100, 100, 100, 100, 100, 100, 100, 100, 100, 112]})
    monkeypatch.setattr("app.services.smart_exit.post_entry_bars", lambda df, ts, n: post)
    res = label_snapshots(10)
    assert res["labeled"] == 1                           # the None row got matured
    assert capture_status()["labelled"] == 1


def _seed_regime(session, seed=1):
    """rs predicts fwd only in 'calm_bull' dates; pure noise in 'choppy' dates."""
    rng = np.random.default_rng(seed)
    db = session()
    try:
        for d in range(12):
            regime = "calm_bull" if d % 2 == 0 else "choppy"
            sd = datetime(2026, 1, 1) + timedelta(days=d)
            for k in range(24):
                rs = float(rng.normal(0, 5))
                fwd = (0.8 * rs if regime == "calm_bull" else 0.0) + float(rng.normal(0, 3))
                db.add(FactorSnapshot(
                    snap_date=sd, symbol=f"S{k}", price=100.0,
                    factors={"rs": rs, "rsi": 50.0, "above_ema20": 1, "atr_pct": 2.0,
                             "dist_ema20_pct": 0.0, "mom_12_1": 0.0, "regime": regime},
                    fwd_ret={"10": round(fwd, 3)}))
        db.commit()
    finally:
        db.close()


# ── ① regime-conditional IC ──────────────────────────────────────────────────

def test_regime_conditional_ic_differs_by_regime(sdb):
    _seed_regime(sdb)
    from app.services.alpha_capture import regime_conditional_ic
    rep = regime_conditional_ic(horizon_days=10)
    assert set(rep["regimes"]) >= {"calm_bull", "choppy"}
    rs = rep["ic_by_regime"]["rs"]
    assert rs["calm_bull"]["mean_ic"] > 0.3         # rs works in calm
    assert rs["calm_bull"]["mean_ic"] > (rs["choppy"]["mean_ic"] or 0) + 0.2   # ...not in choppy


# ── ④ purged walk-forward CV + meta trust gate ───────────────────────────────

def test_purged_cv_reports_oos_auc(sdb):
    _seed(sdb, n_dates=40, n_syms=30)                     # ≥ folds + 2·purge dates
    from app.services.meta_label import purged_cv_auc
    cv = purged_cv_auc(horizon_days=10, n_folds=5)
    assert cv.get("oos_auc") is not None and cv["oos_auc"] > 0.55 and cv["n_oos"] >= 20


def test_meta_size_fraction_gated_on_oos(sdb, monkeypatch):
    # pure-noise panel → OOS AUC ≈ 0.5 (< 0.53) → the model must NOT resize the book
    rng = np.random.default_rng(3)
    db = sdb()
    try:
        for d in range(40):
            sd = datetime(2026, 1, 1) + timedelta(days=d)
            for k in range(30):
                db.add(FactorSnapshot(snap_date=sd, symbol=f"N{k}", price=100.0,
                                      factors={"rs": float(rng.normal(0, 5)), "rsi": 50.0,
                                               "above_ema20": 1, "atr_pct": 2.0,
                                               "dist_ema20_pct": 0.0, "mom_12_1": 0.0},
                                      fwd_ret={"10": float(rng.normal(0, 3))}))
        db.commit()
    finally:
        db.close()
    from app.services.meta_label import train_meta_model, meta_size_fraction, meta_model_status
    train_meta_model(10)
    assert meta_model_status()["trusted"] is False       # noise → untrusted
    assert meta_size_fraction({"rs": 9.0, "rsi": 50, "above_ema20": 1,
                               "atr_pct": 2.0, "dist_ema20_pct": 0.0, "mom_12_1": 0.0}) == 1.0
