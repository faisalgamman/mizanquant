"""XGBoost buy-signal classifier.

Trains on historical trade outcomes (pnl > 0 = good signal) to predict
the probability that a new buy signal will be profitable. Acts as a
pre-execution quality gate.

Reference
---------
- Jansen (2020), Ch.12 — Boosting, XGBoost for trading signals
"""

from __future__ import annotations

import logging
import time
from typing import Iterable

import numpy as np

logger = logging.getLogger(__name__)

_HAS_XGB: bool = False
try:
    import xgboost as xgb
    _HAS_XGB = True
except ImportError:
    pass

# Minimum trades required before the classifier activates
MIN_TRADES: int = 30

# In-memory cache: model + training timestamp
_cache: dict = {"model": None, "ts": 0.0, "features": []}


def _build_features_from_trade_history() -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build training data from closed trades.

    X features: confidence, risk_pct, signal archetype score
    y labels: 1 if pnl > 0, 0 otherwise

    Returns (X, y, feature_names) or empty arrays if insufficient data.
    """
    try:
        from app.db.database import SessionLocal
        from app.db.models import TradeHistory

        db = SessionLocal()
        try:
            rows = (
                db.query(
                    TradeHistory.confidence,
                    TradeHistory.risk_pct,
                    TradeHistory.pnl,
                    TradeHistory.signal_details,
                )
                .filter(
                    TradeHistory.status == "closed",
                    TradeHistory.pnl.isnot(None),
                )
                .all()
            )
        finally:
            db.close()

        if len(rows) < MIN_TRADES:
            return np.array([]), np.array([]), []

        X_list = []
        y_list = []
        for confidence, risk_pct, pnl, signal_details in rows:
            feat = [
                float(confidence or 0.5),
                float(risk_pct or 0.02),
            ]
            # Extract archetype score if available
            if signal_details and isinstance(signal_details, dict):
                feat.append(float(signal_details.get("score", 0.5) or 0.5))
            else:
                feat.append(0.5)
            X_list.append(feat)
            y_list.append(1 if (pnl or 0) > 0 else 0)

        return (
            np.array(X_list, dtype=float),
            np.array(y_list, dtype=int),
            ["confidence", "risk_pct", "archetype_score"],
        )

    except Exception:
        logger.debug("signal_classifier: DB query failed", exc_info=True)
        return np.array([]), np.array([]), []


def _train_model() -> tuple[object | None, list[str]]:
    """Train XGBoost classifier on trade history. Cached 24h."""
    now = time.time()
    if _cache["model"] is not None and (now - _cache["ts"]) < 86400:
        return _cache["model"], _cache["features"]

    if not _HAS_XGB:
        return None, []

    X, y, features = _build_features_from_trade_history()
    if len(X) < MIN_TRADES:
        logger.debug("signal_classifier: need %d+ trades, got %d", MIN_TRADES, len(X))
        return None, []

    try:
        # Balance classes if needed
        pos = int(np.sum(y))
        neg = len(y) - pos
        scale_pos_weight = neg / max(pos, 1)

        model = xgb.XGBClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            scale_pos_weight=scale_pos_weight,
            use_label_encoder=False,
            eval_metric="logloss",
            verbosity=0,
        )
        model.fit(X, y)
        _cache["model"] = model
        _cache["ts"] = now
        _cache["features"] = features
        logger.info("signal_classifier: trained on %d trades (win_rate=%.1f%%)",
                     len(X), pos / len(X) * 100)
        return model, features

    except Exception:
        logger.debug("signal_classifier: training failed", exc_info=True)
        return None, []


def classify_buy_signal(
    confidence: float = 0.5,
    risk_pct: float = 0.02,
    archetype_score: float = 0.5,
) -> dict:
    """Predict probability that a buy signal will be profitable.

    Returns dict with keys: probability, pass_gate, model_available
    Gate threshold: probability >= 0.55 (slightly biased toward quality).
    Falls through to pass_gate=True if model unavailable.
    """
    model, features = _train_model()
    if model is None:
        return {"probability": 0.5, "pass_gate": True, "model_available": False}

    try:
        X = np.array([[confidence, risk_pct, archetype_score]], dtype=float)
        proba = float(model.predict_proba(X)[0][1])
        return {
            "probability": round(proba, 4),
            "pass_gate": proba >= 0.55,
            "model_available": True,
            "features_used": features,
        }
    except Exception:
        logger.debug("signal_classifier: prediction failed", exc_info=True)
        return {"probability": 0.5, "pass_gate": True, "model_available": False}
