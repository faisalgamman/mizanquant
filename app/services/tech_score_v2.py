"""Runtime scorer for technical score v2 — artifact-driven, behind a flag.

Loads a fitted artifact (data/tech_v2_weights.json) once at module level.
If the artifact is missing, malformed, or verdict != PASS, returns (None, "v1")
so callers stay on v1 transparently.

Pure function: no network, no DB — just the dfs given.
"""
from __future__ import annotations

import json
import logging
import os
import threading

import numpy as np

logger = logging.getLogger("screener")

_ARTIFACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "tech_v2_weights.json",
)

_artifact_cache: dict | None = None
_cache_lock = threading.Lock()


def _load_artifact() -> dict | None:
    global _artifact_cache
    if _artifact_cache is not None:
        return _artifact_cache
    with _cache_lock:
        if _artifact_cache is not None:
            return _artifact_cache
        try:
            if not os.path.exists(_ARTIFACT_PATH):
                logger.debug("tech_score_v2: no artifact at %s", _ARTIFACT_PATH)
                _artifact_cache = {}
            else:
                with open(_ARTIFACT_PATH) as f:
                    art = json.load(f)
                if art.get("verdict") != "PASS":
                    logger.debug("tech_score_v2: artifact verdict is %s", art.get("verdict"))
                    _artifact_cache = {}
                else:
                    required = ["features", "means", "stds", "coefs", "intercept", "calibration_quantiles"]
                    missing = [k for k in required if k not in art]
                    if missing:
                        logger.debug("tech_score_v2: artifact missing keys %s", missing)
                        _artifact_cache = {}
                    else:
                        _artifact_cache = art
        except Exception as e:
            logger.debug("tech_score_v2: artifact load failed: %s", e)
            _artifact_cache = {}
        return _artifact_cache


def tech_score_v2(df, spy_df):
    """Compute technical score v2 from the fitted artifact.

    Args:
        df: price DataFrame for the symbol (must have OHLCV columns)
        spy_df: price DataFrame for SPY (same columns)

    Returns:
        (score: int | None, version: str)
        - (0-30, "tech-v2-YYYY-MM-DD") on success
        - (None, "v1") when v2 unavailable (artifact absent / KEEP_V1 / too-short df)
    """
    art = _load_artifact()
    if not art:
        return None, "v1"

    from app.services.feature_lib import FEATURE_ORDER, compute_features

    try:
        asof = df.index[-1] if hasattr(df.index, "__getitem__") else df.iloc[-1].name
        feats = compute_features(df, spy_df, asof)
        if feats is None:
            return None, "v1"

        # Build feature vector in FEATURE_ORDER
        X = np.array([float(feats[k]) for k in FEATURE_ORDER], dtype=float)
        if np.isnan(X).any() or np.isinf(X).any():
            return None, "v1"

        means = np.array(art["means"], dtype=float)
        stds = np.array(art["stds"], dtype=float)
        stds[stds == 0] = 1.0
        coefs = np.array(art["coefs"], dtype=float)
        intercept = float(art["intercept"])

        # Z-score + dot + intercept
        z = (X - means) / stds
        raw = float(np.dot(z, coefs) + intercept)

        # Percentile calibration → 0-30
        quantiles = np.array(art["calibration_quantiles"], dtype=float)
        pct = float(np.searchsorted(quantiles, raw) / len(quantiles))
        score = max(0, min(30, round(pct * 30)))

        return score, art.get("version", "tech-v2")
    except Exception as e:
        logger.debug("tech_score_v2: computation failed: %s", e)
        return None, "v1"
