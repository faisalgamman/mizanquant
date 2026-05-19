"""Rolling retrainer — periodic walk-forward retraining of production models.

Called by:
  - Cronjob: 0 2 * * 0 (weekly Sunday 2am)
  - API: POST /api/model-registry/retrain
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import yfinance as yf

logger = logging.getLogger("retrainer")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # mizanquant/


def _fetch_data(symbol: str, period: str = "2y") -> np.ndarray | None:
    """Fetch OHLCV data for a symbol."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        if df.empty:
            logger.warning("No data for %s", symbol)
            return None
        prices = np.array(df["Close"].values, dtype=np.float64).flatten()
        prices = prices[~np.isnan(prices)]
        return prices if len(prices) > 60 else None
    except Exception as e:
        logger.warning("Failed to fetch data for %s: %s", symbol, e)
        return None


def _retrain_dqn(symbol: str, episodes: int) -> dict | None:
    """Retrain DoubleDQN agent and promote if quality gates pass."""
    try:
        from openbb_forecast.agents.double_dqn import DoubleDQNAgent

        prices = _fetch_data(symbol)
        if prices is None:
            return {"error": f"No data for {symbol}", "model": "double_dqn"}

        agent = DoubleDQNAgent(
            state_size=30, action_size=3,
            early_stop_patience=10,
            checkpoint_dir="model_checkpoints",
            checkpoint_name="double_dqn",
        )
        result = agent.walk_forward_evaluate(
            prices=prices, train_ratio=0.7, episodes=episodes,
        )
        backtest = result.get("backtest_summary", {})
        sharpe = float(backtest.get("sharpe_ratio", 0))
        win_rate = float(backtest.get("win_rate", 0))
        max_dd = float(backtest.get("max_drawdown", 0))
        test_acc = float(backtest.get("accuracy", win_rate / 100.0))

        try:
            from app.services.model_registry import promote_to_production
            promote_to_production(
                "double_dqn",
                datetime.now().strftime("%Y%m%d_%H%M"),
                str(agent.best_checkpoint_path or "model_checkpoints/double_dqn_best.pt"),
                metrics={"sharpe": sharpe, "win_rate": win_rate,
                         "max_drawdown": max_dd, "test_accuracy": test_acc},
            )
            logger.info("Double DQN promoted: Sharpe=%.2f WR=%.0f%%", sharpe, win_rate)
        except ValueError:
            logger.info("Double DQN did not pass quality gates: Sharpe=%.2f WR=%.0f%%", sharpe, win_rate)

        return {
            "model": "double_dqn",
            "sharpe": round(sharpe, 2),
            "win_rate": round(win_rate, 1),
            "max_drawdown": round(max_dd, 2),
            "early_stopped": result.get("early_stopped", False),
        }
    except Exception as e:
        logger.error("Double DQN retrain failed: %s", e)
        return {"error": str(e), "model": "double_dqn"}


def _retrain_policy_gradient(symbol: str, episodes: int) -> dict | None:
    """Retrain PolicyGradient agent and promote if quality gates pass."""
    try:
        from openbb_forecast.agents.policy_gradient import PolicyGradientAgent

        prices = _fetch_data(symbol)
        if prices is None:
            return {"error": f"No data for {symbol}", "model": "policy_gradient"}

        agent = PolicyGradientAgent(
            state_size=30, action_size=3,
            early_stop_patience=10,
            checkpoint_dir="model_checkpoints",
            checkpoint_name="policy_gradient",
        )
        result = agent.walk_forward_evaluate(
            prices=prices, train_ratio=0.7, episodes=episodes,
        )
        backtest = result.get("backtest_summary", {})
        sharpe = float(backtest.get("sharpe_ratio", 0))
        win_rate = float(backtest.get("win_rate", 0))
        max_dd = float(backtest.get("max_drawdown", 0))
        test_acc = float(backtest.get("accuracy", win_rate / 100.0))

        try:
            from app.services.model_registry import promote_to_production
            promote_to_production(
                "policy_gradient",
                datetime.now().strftime("%Y%m%d_%H%M"),
                str(agent.best_checkpoint_path or "model_checkpoints/policy_gradient_best.pt"),
                metrics={"sharpe": sharpe, "win_rate": win_rate,
                         "max_drawdown": max_dd, "test_accuracy": test_acc},
            )
            logger.info("Policy Gradient promoted: Sharpe=%.2f WR=%.0f%%", sharpe, win_rate)
        except ValueError:
            logger.info("Policy Gradient did not pass quality gates: Sharpe=%.2f WR=%.0f%%", sharpe, win_rate)

        return {
            "model": "policy_gradient",
            "sharpe": round(sharpe, 2),
            "win_rate": round(win_rate, 1),
            "max_drawdown": round(max_dd, 2),
            "early_stopped": result.get("early_stopped", False),
        }
    except Exception as e:
        logger.error("PolicyGradient retrain failed: %s", e)
        return {"error": str(e), "model": "policy_gradient"}


_RETRAIN_REGISTRY = {
    "double_dqn": _retrain_dqn,
    "policy_gradient": _retrain_policy_gradient,
}


def rolling_retrain(
    symbol: str = "AAPL",
    models: Optional[list[str]] = None,
    episodes: int = 200,
    register_after: bool = True,
) -> dict:
    """Retrain specified models (or all registered) with walk-forward evaluation.

    Args:
        symbol: Stock symbol to train on.
        models: List of model names or None for all registered.
        episodes: Training episodes per model.
        register_after: If True, automatically promote passing models.

    Returns:
        Dict with results per model and overall summary.
    """
    target_models = models or list(_RETRAIN_REGISTRY.keys())
    results: dict[str, dict] = {}
    promoted = []
    failed = []

    for model_name in target_models:
        train_func = _RETRAIN_REGISTRY.get(model_name)
        if train_func is None:
            results[model_name] = {"error": f"Unknown model: {model_name}"}
            continue
        try:
            r = train_func(symbol, episodes)
            if r and r.get("sharpe", 0) > 0:
                results[model_name] = r
                if r.get("sharpe", 0) >= 1.0 and r.get("win_rate", 50) >= 55:
                    promoted.append(model_name)
            elif r:
                results[model_name] = r
                failed.append(model_name)
        except Exception as e:
            results[model_name] = {"error": str(e)}
            failed.append(model_name)

    logger.info(
        "Rolling retrain complete: %d models, %d promoted, %d failed",
        len(results), len(promoted), len(failed),
    )

    return {
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "models_trained": len(results),
        "promoted": promoted,
        "failed": failed,
        "results": results,
    }
