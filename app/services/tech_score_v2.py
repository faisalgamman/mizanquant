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

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The monthly walk-forward fitter writes the OOS-validated artifact to the DURABLE
# CACHE_DIR (Railway /data volume) so an adopted v2 survives restarts; we fall back to
# the repo copy. mtime-aware so a freshly-fitted artifact is picked up WITHOUT a restart.
_CACHE_DIR = os.environ.get("CACHE_DIR") or os.path.join(_PROJ_ROOT, ".cache")
_DURABLE_PATH = os.path.join(_CACHE_DIR, "tech_v2_weights.json")
_REPO_PATH = os.path.join(_PROJ_ROOT, "data", "tech_v2_weights.json")

_artifact_cache: dict | None = None
_artifact_mtime: float = -1.0
_cache_lock = threading.Lock()


def _artifact_file() -> str:
    return _DURABLE_PATH if os.path.exists(_DURABLE_PATH) else _REPO_PATH


def _load_artifact() -> dict | None:
    global _artifact_cache, _artifact_mtime
    path = _artifact_file()
    try:
        mtime = os.path.getmtime(path) if os.path.exists(path) else -1.0
    except OSError:
        mtime = -1.0
    if _artifact_cache is not None and mtime == _artifact_mtime:
        return _artifact_cache
    with _cache_lock:
        if _artifact_cache is not None and mtime == _artifact_mtime:
            return _artifact_cache
        _artifact_mtime = mtime
        try:
            if not os.path.exists(path):
                _artifact_cache = {}
            else:
                with open(path) as f:
                    art = json.load(f)
                if art.get("verdict") != "PASS":
                    logger.debug("tech_score_v2: artifact verdict is %s (staying v1)", art.get("verdict"))
                    _artifact_cache = {}
                else:
                    required = ["features", "means", "stds", "coefs", "intercept", "calibration_quantiles"]
                    missing = [k for k in required if k not in art]
                    if missing:
                        logger.debug("tech_score_v2: artifact missing keys %s", missing)
                        _artifact_cache = {}
                    else:
                        _artifact_cache = art
                        logger.info("tech_score_v2: adopted OOS-validated v2 weights (%s)", art.get("version"))
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
