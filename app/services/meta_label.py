"""② Meta-labeling (López de Prado, AFML ch.3) — a SECONDARY model that scores the
PRIMARY signal instead of replacing it.

The scanner still generates the buy signal. The meta-model, trained on the alpha-capture
panel, answers one question: *given these entry factors, what is the probability this long
WINS over the horizon?* That probability sizes the bet (full / half / skip) rather than
flipping it. We keep a path-aware triple-barrier primitive (first touch of +pt / −sl / time)
for the labelling upgrade; the shipped label is the vertical-barrier outcome (forward-horizon
win), which the daily capture already provides.

The fitted model is stored as plain JSON (standardiser + logistic coefficients) — portable,
no pickle. Fails open (None) until enough labelled rows accrue.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone

logger = logging.getLogger("screener")

_FEATURES = ("rs", "rsi", "above_ema20", "atr_pct", "dist_ema20_pct", "mom_12_1")
_MIN_TRAIN = 120


def triple_barrier_labels(closes, entry_idx: int, pt_pct: float, sl_pct: float,
                          max_days: int) -> int | None:
    """Path-aware first-touch label for ONE event (pure/testable): from ``entry_idx``,
    return 1 if the +pt_pct barrier is touched before −sl_pct within ``max_days`` bars,
    else 0 (lower or vertical barrier first). None if the path is too short."""
    import numpy as np
    c = np.asarray(closes, dtype=float)
    if entry_idx < 0 or entry_idx + 1 >= len(c):
        return None
    entry = c[entry_idx]
    if entry <= 0:
        return None
    up, dn = entry * (1 + pt_pct / 100.0), entry * (1 - sl_pct / 100.0)
    end = min(len(c), entry_idx + 1 + max_days)
    for i in range(entry_idx + 1, end):
        if c[i] >= up:
            return 1
        if c[i] <= dn:
            return 0
    return 1 if c[end - 1] > entry else 0     # vertical barrier → sign


def _path():
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "meta_model.json")


def _training_rows(horizon_days: int):
    """(feature-vector, label) pairs from labelled snapshots. label = forward win."""
    from app.db.database import SessionLocal
    from app.db.models import FactorSnapshot
    h = str(int(horizon_days))
    db = SessionLocal()
    try:
        rows = db.query(FactorSnapshot.factors, FactorSnapshot.fwd_ret).filter(
            FactorSnapshot.fwd_ret.isnot(None)).all()
    finally:
        db.close()
    X, y = [], []
    for fac, fwd in rows:
        if not (isinstance(fac, dict) and isinstance(fwd, dict) and h in fwd):
            continue
        vec = [fac.get(f) for f in _FEATURES]
        if any(v is None or not isinstance(v, (int, float)) for v in vec):
            continue
        X.append([float(v) for v in vec])
        y.append(1 if float(fwd[h]) > 0 else 0)
    return X, y


def train_meta_model(horizon_days: int = 10) -> dict:
    """Fit a standardised logistic meta-model on the capture panel; persist as JSON."""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score
    except Exception as e:
        return {"error": f"sklearn unavailable: {e}"}

    X, y = _training_rows(horizon_days)
    n = len(y)
    if n < _MIN_TRAIN or len(set(y)) < 2:
        return {"status": "accumulating", "n": n, "need": _MIN_TRAIN}

    Xa, ya = np.asarray(X, float), np.asarray(y, int)
    scaler = StandardScaler().fit(Xa)
    Xs = scaler.transform(Xa)
    clf = LogisticRegression(max_iter=1000, C=1.0).fit(Xs, ya)
    try:
        auc = round(float(roc_auc_score(ya, clf.predict_proba(Xs)[:, 1])), 3)
    except Exception:
        auc = None

    model = {
        "features": list(_FEATURES),
        "mean": [float(m) for m in scaler.mean_],
        "scale": [float(s) if s else 1.0 for s in scaler.scale_],
        "coef": [float(c) for c in clf.coef_[0]],
        "intercept": float(clf.intercept_[0]),
        "n": n, "auc_in_sample": auc, "base_rate": round(float(ya.mean()), 3),
        "horizon_days": int(horizon_days),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(_path(), "w", encoding="utf-8") as fh:
            json.dump(model, fh, indent=2)
    except Exception as e:
        return {"error": str(e)}
    return {"status": "trained", "n": n, "auc_in_sample": auc, "base_rate": model["base_rate"]}


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def meta_probability(factors: dict) -> float | None:
    """P(win) for a pick from its entry factors, via the persisted model. None if the model
    isn't trained yet or a feature is missing (caller falls back to base sizing)."""
    m = _load()
    if not m or not isinstance(factors, dict):
        return None
    try:
        z = m["intercept"]
        for i, f in enumerate(m["features"]):
            v = factors.get(f)
            if v is None or not isinstance(v, (int, float)):
                return None
            z += m["coef"][i] * ((float(v) - m["mean"][i]) / m["scale"][i])
        return round(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z)))), 4)
    except Exception:
        return None


def meta_size_fraction(factors: dict) -> float:
    """Map P(win) → a size fraction via fractional Kelly. 1.0 (base) when the model is
    absent, so the overlay is additive and safe."""
    p = meta_probability(factors)
    if p is None:
        return 1.0
    from app.services.position_sizing import fractional_kelly
    return fractional_kelly(p, win_loss_ratio=1.0)


def meta_model_status() -> dict:
    m = _load()
    if not m:
        return {"status": "untrained"}
    return {"status": "trained", "n": m.get("n"), "auc_in_sample": m.get("auc_in_sample"),
            "base_rate": m.get("base_rate"), "trained_at": m.get("trained_at"),
            "top_features": sorted(zip(m["features"], m["coef"]), key=lambda t: -abs(t[1]))[:3]}


__all__ = ["triple_barrier_labels", "train_meta_model", "meta_probability",
           "meta_size_fraction", "meta_model_status"]
