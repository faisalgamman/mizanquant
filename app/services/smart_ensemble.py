"""Smart Ensemble — weighted consensus with top-k models.

Each model gets weight = Sharpe × (Win Rate / 100).
Top 5 models by weight drive the final verdict.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("screener")

_MODEL_REGISTRY_DIR = Path("model_registry")
_TOP_K = 5
_STRONG_THRESHOLD = 0.65


def _load_all_models() -> dict[str, dict[str, Any]]:
    """Load all production models from registry."""
    models = {}
    reg_dir = _MODEL_REGISTRY_DIR
    if not reg_dir.exists():
        return models
    for model_dir in reg_dir.iterdir():
        if not model_dir.is_dir():
            continue
        prod_file = model_dir / "production.json"
        if not prod_file.exists():
            continue
        try:
            data = json.loads(prod_file.read_text())
            metrics = data.get("metrics", {})
            data["name"] = model_dir.name
            data["sharpe"] = float(metrics.get("sharpe", 0))
            data["win_rate"] = float(metrics.get("win_rate", 50))
            data["weight"] = round(data["sharpe"] * (data["win_rate"] / 100), 4)
            models[model_dir.name] = data
        except Exception as exc:
            logger.debug("Failed to load model %s: %s", model_dir.name, exc)
    return models


def get_model_weights() -> dict[str, float]:
    """Return {model_name: weight} for all registered production models."""
    models = _load_all_models()
    return {name: m["weight"] for name, m in models.items()}


def get_top_models(n: int = _TOP_K) -> list[dict[str, Any]]:
    """Return top n models sorted by weight descending."""
    models = list(_load_all_models().values())
    models.sort(key=lambda m: m["weight"], reverse=True)
    return models[:n]


def weighted_consensus(
    tool_votes: list[dict[str, Any]],
    model_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute weighted consensus from tool votes.

    Args:
        tool_votes: List of dicts with keys 'Tool', 'Vote' ('BUY'/'SELL'/'HOLD'/'SKIP')
        model_weights: Optional override weights. Auto-loaded if None.

    Returns:
        Dict with verdict, confidence, buy_weight, total_weight, top_models
    """
    if model_weights is None:
        top = get_top_models(_TOP_K)
        model_weights = {m["name"]: m["weight"] for m in top}

    # Map tool names to model names
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

    buy_weight = 0.0
    total_weight = 0.0
    used_tools = []

    for vote in tool_votes:
        tool = vote.get("Tool", "")
        vote_str = vote.get("Vote", "HOLD")
        if vote_str in ("SKIP", "-", "ERROR"):
            continue

        model_name = _TOOL_TO_MODEL.get(tool)
        weight = model_weights.get(model_name, 1.0) if model_name else 1.0
        total_weight += weight

        if vote_str == "BUY":
            buy_weight += weight
            used_tools.append({"tool": tool, "vote": vote_str, "weight": weight})
        elif vote_str == "SELL":
            used_tools.append({"tool": tool, "vote": vote_str, "weight": weight})
        else:
            used_tools.append({"tool": tool, "vote": vote_str, "weight": weight})

    if total_weight == 0:
        return {
            "verdict": "NEUTRAL",
            "confidence": 0.0,
            "buy_weight": 0.0,
            "total_weight": 0.0,
            "buy_ratio": 0.0,
            "top_models": list(model_weights.keys()),
        }

    buy_ratio = buy_weight / total_weight

    if buy_ratio >= _STRONG_THRESHOLD:
        verdict = "STRONG BUY"
    elif buy_ratio >= 0.50:
        verdict = "BUY"
    elif buy_ratio <= 0.35:
        verdict = "SELL"
    else:
        verdict = "NEUTRAL"

    return {
        "verdict": verdict,
        "confidence": round(buy_ratio * 100, 1),
        "buy_weight": round(buy_weight, 4),
        "total_weight": round(total_weight, 4),
        "buy_ratio": round(buy_ratio, 4),
        "top_models": list(model_weights.keys()),
        "used_tools": used_tools,
    }


def update_weights_from_trades(trade_results: list[dict]) -> None:
    """Update model weights based on last 30 paper/live trades.

    Args:
        trade_results: List of dicts with 'model_used' and 'pnl_pct'
    """
    if not trade_results:
        return

    from collections import defaultdict

    model_trades = defaultdict(list)
    for trade in trade_results[-30:]:
        model = trade.get("model_used", "")
        pnl = trade.get("pnl_pct", 0)
        if model:
            model_trades[model].append(pnl)

    models = _load_all_models()
    for name, m in models.items():
        trades = model_trades.get(name, [])
        if len(trades) < 5:
            continue
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades)
        avg_return = sum(trades) / len(trades)
        sharpe = (avg_return / (max(__import__("numpy").std(trades), 1e-6))
                  if len(trades) > 1 else 0.5)
        new_weight = round(sharpe * (win_rate / 100), 4)

        prod_file = _MODEL_REGISTRY_DIR / name / "production.json"
        if prod_file.exists():
            try:
                data = json.loads(prod_file.read_text())
                data["metrics"]["live_sharpe"] = round(sharpe, 4)
                data["metrics"]["live_win_rate"] = round(win_rate * 100, 1)
                data["metrics"]["live_avg_return"] = round(avg_return, 4)
                data["metrics"]["live_weight"] = new_weight
                prod_file.write_text(json.dumps(data, indent=2, default=str))
                logger.info(
                    "Updated %s: weight %.4f (live WR %.0f%%, Sharpe %.2f, %d trades)",
                    name, new_weight, win_rate * 100, sharpe, len(trades),
                )
            except Exception as exc:
                logger.debug("Failed to update %s: %s", name, exc)
