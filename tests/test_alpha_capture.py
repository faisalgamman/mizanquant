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
