"""Model explainability — SHAP values + ensemble attribution.

Works immediately for weighted-consensus attribution (no model needed).
Ready for SHAP TreeExplainer when XGBoost/LightGBM/sklearn tree models
are added to the pipeline.

Reference
---------
- Jansen (2020), Ch.12 — Boosting, SHAP, and model interpretation
- Lundberg & Lee (2017) — SHAP: A Unified Approach to Interpreting Models
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensemble attribution — works NOW, no trained model required
# ---------------------------------------------------------------------------


def get_ensemble_attribution(
    tool_votes: list[dict[str, Any]],
    model_weights: dict[str, float] | None = None,
) -> dict:
    """Explain WHY the weighted consensus reached its verdict.

    Breaks down the contribution of each tool/model to the final
    BUY/SELL decision so users can see which signal drove the outcome.

    Parameters
    ----------
    tool_votes : list[dict]
        Each dict: {"Tool": str, "Vote": "BUY"|"SELL"|"HOLD"|"SKIP", ...}
    model_weights : dict or None
        Optional {model_name: weight} override.

    Returns
    -------
    dict with keys: verdict, confidence, contributions, top_drivers
    """
    if model_weights is None:
        try:
            from app.services.smart_ensemble import get_model_weights
            model_weights = get_model_weights()
        except Exception:
            model_weights = {}

    # Map tools to models (same mapping as smart_ensemble._TOOL_TO_MODEL)
    _TOOL_TO_MODEL = {
        "Halal Screener": "turtle",
        "USX Pro": "moving_average",
        "Backtest 2Y": "evolution_strategy",
        "Monte Carlo": "neuro_evolution_novelty",
        "Bollinger Bands": "moving_average",
        "EMA Alignment": "turtle",
        "XGBoost": "evolution_strategy",
        "Momentum": "turtle",
        "Volume-Price": "moving_average",
        "LSTM": "neuro_evolution_novelty",
        "Transformer": "neuro_evolution_novelty",
        "Ensemble": "evolution_strategy",
        "DQN": "neuro_evolution_novelty",
        "PolicyGrad": "evolution_strategy",
    }

    contributions: list[dict] = []
    buy_weight = 0.0
    sell_weight = 0.0
    total_weight = 0.0

    for vote in tool_votes:
        tool = vote.get("Tool", "")
        vote_str = vote.get("Vote", "HOLD")
        if vote_str in ("SKIP", "-", "ERROR", "HOLD"):
            continue

        model_name = _TOOL_TO_MODEL.get(tool, "unknown")
        weight = model_weights.get(model_name, 0.1)

        contrib = {
            "tool": tool,
            "model": model_name,
            "vote": vote_str,
            "weight": round(weight, 4),
            "impact": 0.0,
        }

        if vote_str == "BUY":
            buy_weight += weight
            contrib["impact"] = round(weight, 4)
        elif vote_str == "SELL":
            sell_weight += weight
            contrib["impact"] = round(-weight, 4)

        total_weight += weight
        contributions.append(contrib)

    # Normalize
    for c in contributions:
        if total_weight > 0:
            c["impact"] = round(c["impact"] / total_weight * 100, 1)

    verdict = "HOLD"
    if buy_weight > sell_weight:
        verdict = "BUY"
    elif sell_weight > buy_weight:
        verdict = "SELL"

    confidence = (
        round(max(buy_weight, sell_weight) / total_weight * 100, 1)
        if total_weight > 0
        else 0.0
    )

    # Sort by absolute impact
    contributions.sort(key=lambda c: abs(c["impact"]), reverse=True)

    return {
        "verdict": verdict,
        "confidence_pct": confidence,
        "buy_weight": round(buy_weight, 4),
        "sell_weight": round(sell_weight, 4),
        "total_weight": round(total_weight, 4),
        "contributions": contributions,
        "top_drivers": [
            c for c in contributions if abs(c["impact"]) > 5.0
        ],
    }


# ---------------------------------------------------------------------------
# SHAP explainer — ready for tree models when added
# ---------------------------------------------------------------------------


def explain_model(
    model: Any,
    X: np.ndarray,
    feature_names: list[str] | None = None,
    max_samples: int = 100,
) -> dict:
    """Compute SHAP values for a trained tree model.

    Parameters
    ----------
    model : XGBRegressor, XGBClassifier, or sklearn tree model
        Trained model object with .predict() method.
    X : np.ndarray
        Feature matrix (n_samples, n_features).
    feature_names : list[str] or None
        Feature names for labelling.
    max_samples : int
        Cap background samples for performance.

    Returns
    -------
    dict with keys: feature_importance, shap_summary, n_features
    Returns empty dict on failure (shap not installed, etc.).
    """
    try:
        import shap
    except ImportError:
        logger.debug("model_explainer: shap not installed — install with pip install shap")
        return {"error": "shap not installed", "feature_importance": []}

    try:
        # Limit background samples
        background = X[:max_samples] if len(X) > max_samples else X

        # TreeExplainer for XGBoost / sklearn trees
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X[:max_samples])

        # Handle multi-class: take mean absolute SHAP
        if isinstance(shap_values, list):
            shap_abs = np.abs(np.mean(shap_values, axis=0))
        else:
            shap_abs = np.abs(shap_values).mean(axis=0)

        if feature_names is None:
            feature_names = [f"feat_{i}" for i in range(X.shape[1])]

        # Build importance list
        importance = [
            {
                "feature": feature_names[i],
                "shap_importance": round(float(shap_abs[i]), 6),
                "rank": i + 1,
            }
            for i in np.argsort(-shap_abs)
        ]

        return {
            "n_features": X.shape[1],
            "n_samples_explained": min(len(X), max_samples),
            "feature_importance": importance,
            "top_feature": importance[0]["feature"] if importance else "",
        }

    except Exception as exc:
        logger.debug("model_explainer: SHAP failed — %s", exc)
        return {"error": str(exc)[:120], "feature_importance": []}


def explain_prediction(
    model: Any,
    X_row: np.ndarray,
    feature_names: list[str] | None = None,
) -> dict:
    """Explain a SINGLE prediction — which features pushed the decision.

    Parameters
    ----------
    model : Trained tree model.
    X_row : np.ndarray
        Single row shape (n_features,) or (1, n_features).
    feature_names : list[str] or None

    Returns
    -------
    dict with keys: prediction, base_value, contributions
    """
    try:
        import shap
    except ImportError:
        return {"error": "shap not installed"}

    try:
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_row.reshape(1, -1))

        if isinstance(shap_values, list):
            sv = shap_values[0][0]
            base = explainer.expected_value[0]
        else:
            sv = shap_values[0]
            base = float(explainer.expected_value)

        if feature_names is None:
            feature_names = [f"feat_{i}" for i in range(len(sv))]

        contributions = [
            {
                "feature": feature_names[i],
                "shap_value": round(float(sv[i]), 6),
                "direction": "up" if sv[i] > 0 else "down",
            }
            for i in np.argsort(-np.abs(sv))
        ]

        pred = float(model.predict(X_row.reshape(1, -1))[0])

        return {
            "prediction": round(pred, 6),
            "base_value": round(base, 6),
            "contributions": contributions,
            "top_push_up": [c for c in contributions if c["direction"] == "up"][:3],
            "top_push_down": [c for c in contributions if c["direction"] == "down"][:3],
        }

    except Exception as exc:
        logger.debug("model_explainer: prediction SHAP failed — %s", exc)
        return {"error": str(exc)[:120]}
