"""OpenBB Forecast Router — all commands registered here.

Usage with OpenBB:
    from openbb import obb

    # Fetch data
    data = obb.equity.price.historical("AAPL", provider="yfinance").results

    # Forecast
    result = obb.forecast.lstm(data, forecast_horizon=5)
    result = obb.forecast.transformer(data, forecast_horizon=5)
    result = obb.forecast.ensemble(data, forecast_horizon=5)

    # RL Trading Agents
    result = obb.forecast.double_dqn(data, initial_capital=10000)
    result = obb.forecast.policy_gradient(data, initial_capital=10000)
    result = obb.forecast.evolution_strategy(data, initial_capital=10000)

    # Simulation
    result = obb.forecast.monte_carlo(data, n_simulations=1000)

    # Metrics
    result = obb.forecast.metrics(data)
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from openbb_core.app.model.obbject import OBBject
from openbb_core.app.router import Router
from openbb_core.provider.abstract.data import Data

router = Router(prefix="/forecast", description="Stock forecasting, RL trading agents, and backtesting")


def _data_to_df(data: list[Data]) -> pd.DataFrame:
    """Convert OpenBB list[Data] to DataFrame."""
    records = [d.model_dump() for d in data]
    df = pd.DataFrame(records)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


def _extract_prices(df: pd.DataFrame, target_column: str) -> np.ndarray:
    """Extract target column as numpy array."""
    if target_column not in df.columns:
        raise ValueError(
            f"Column '{target_column}' not found. Available: {list(df.columns)}"
        )
    return df[target_column].astype(float).values


def _get_dates(df: pd.DataFrame) -> list[str]:
    """Extract date strings from DataFrame."""
    if "date" in df.columns:
        return df["date"].astype(str).tolist()
    return [str(i) for i in range(len(df))]


# ---------------------------------------------------------------------------
# Deep Learning Forecasters
# ---------------------------------------------------------------------------


@router.command(methods=["POST"])
async def lstm(
    data: list[Data],
    target_column: str = "close",
    forecast_horizon: int = 5,
    sequence_length: int = 30,
    hidden_size: int = 128,
    num_layers: int = 2,
    epochs: int = 100,
    learning_rate: float = 0.001,
    n_splits: int = 5,
) -> OBBject:
    """LSTM time-series forecast with walk-forward validation.

    Trains an LSTM neural network on historical price data using expanding-window
    cross-validation. The scaler is re-fit on each fold's training data to prevent
    data leakage. Reports directional accuracy, MAE, RMSE, and MAPE.
    """
    from openbb_forecast.models.base import compute_forecast_metrics
    from openbb_forecast.models.lstm import LSTMForecaster
    from openbb_forecast.schemas.forecast import ForecastResult, ForecastSummary

    df = _data_to_df(data)
    prices = _extract_prices(df, target_column)
    dates = _get_dates(df)

    model = LSTMForecaster(
        hidden_size=hidden_size,
        num_layers=num_layers,
        epochs=epochs,
        learning_rate=learning_rate,
    )

    fold_results = model.walk_forward_predict(
        data=prices,
        sequence_length=sequence_length,
        forecast_horizon=forecast_horizon,
        n_splits=n_splits,
    )

    # Build output
    results = []
    for fold in fold_results:
        for j in range(fold.test_size):
            idx = int(fold.test_indices[j]) if j < len(fold.test_indices) else 0
            date_str = dates[idx] if idx < len(dates) else str(idx)
            pred_val = float(fold.predictions[j, 0]) if fold.predictions.ndim > 1 else float(fold.predictions[j])
            actual_val = float(fold.actuals[j, 0]) if fold.actuals.ndim > 1 else float(fold.actuals[j])
            results.append(
                ForecastResult(
                    date=date_str,
                    actual=actual_val,
                    predicted=pred_val,
                    fold=fold.fold,
                )
            )

    metrics = compute_forecast_metrics(fold_results)
    summary = ForecastSummary(
        model_name="LSTM",
        n_folds=len(fold_results),
        directional_accuracy=metrics["directional_accuracy"],
        mae=metrics["mae"],
        rmse=metrics["rmse"],
        mape=metrics["mape"],
        mean_forecast=[],
        std_forecast=[],
    )

    return OBBject(results=results, extra={"summary": summary.model_dump()})


@router.command(methods=["POST"])
async def transformer(
    data: list[Data],
    target_column: str = "close",
    forecast_horizon: int = 5,
    sequence_length: int = 30,
    d_model: int = 128,
    n_heads: int = 8,
    num_encoder_layers: int = 2,
    epochs: int = 100,
    learning_rate: float = 0.0005,
    n_splits: int = 5,
) -> OBBject:
    """Transformer (attention) time-series forecast with walk-forward validation.

    Uses a multi-head self-attention encoder with causal masking to predict
    future prices. Trained with cosine annealing learning rate schedule.
    """
    from openbb_forecast.models.base import compute_forecast_metrics
    from openbb_forecast.models.transformer import TransformerForecaster
    from openbb_forecast.schemas.forecast import ForecastResult, ForecastSummary

    df = _data_to_df(data)
    prices = _extract_prices(df, target_column)
    dates = _get_dates(df)

    model = TransformerForecaster(
        d_model=d_model,
        n_heads=n_heads,
        num_layers=num_encoder_layers,
        epochs=epochs,
        learning_rate=learning_rate,
    )

    fold_results = model.walk_forward_predict(
        data=prices,
        sequence_length=sequence_length,
        forecast_horizon=forecast_horizon,
        n_splits=n_splits,
    )

    results = []
    for fold in fold_results:
        for j in range(fold.test_size):
            idx = int(fold.test_indices[j]) if j < len(fold.test_indices) else 0
            date_str = dates[idx] if idx < len(dates) else str(idx)
            pred_val = float(fold.predictions[j, 0]) if fold.predictions.ndim > 1 else float(fold.predictions[j])
            actual_val = float(fold.actuals[j, 0]) if fold.actuals.ndim > 1 else float(fold.actuals[j])
            results.append(
                ForecastResult(
                    date=date_str,
                    actual=actual_val,
                    predicted=pred_val,
                    fold=fold.fold,
                )
            )

    metrics = compute_forecast_metrics(fold_results)
    summary = ForecastSummary(
        model_name="Transformer",
        n_folds=len(fold_results),
        directional_accuracy=metrics["directional_accuracy"],
        mae=metrics["mae"],
        rmse=metrics["rmse"],
        mape=metrics["mape"],
        mean_forecast=[],
        std_forecast=[],
    )

    return OBBject(results=results, extra={"summary": summary.model_dump()})


@router.command(methods=["POST"])
async def ensemble(
    data: list[Data],
    target_column: str = "close",
    forecast_horizon: int = 5,
    sequence_length: int = 30,
    n_splits: int = 5,
) -> OBBject:
    """Stacking ensemble forecast (XGBoost + RandomForest + GradientBoosting -> Ridge).

    Uses out-of-fold predictions for meta-learner training to prevent data leakage.
    Base models are tree-based; meta-learner is regularized linear regression.
    """
    from openbb_forecast.models.base import compute_forecast_metrics
    from openbb_forecast.models.ensemble import StackingForecaster
    from openbb_forecast.schemas.forecast import ForecastResult, ForecastSummary

    df = _data_to_df(data)
    prices = _extract_prices(df, target_column)
    dates = _get_dates(df)

    model = StackingForecaster()

    fold_results = model.walk_forward_predict(
        data=prices,
        sequence_length=sequence_length,
        forecast_horizon=forecast_horizon,
        n_splits=n_splits,
    )

    results = []
    for fold in fold_results:
        for j in range(fold.test_size):
            idx = int(fold.test_indices[j]) if j < len(fold.test_indices) else 0
            date_str = dates[idx] if idx < len(dates) else str(idx)
            pred_val = float(fold.predictions[j, 0]) if fold.predictions.ndim > 1 else float(fold.predictions[j])
            actual_val = float(fold.actuals[j, 0]) if fold.actuals.ndim > 1 else float(fold.actuals[j])
            results.append(
                ForecastResult(
                    date=date_str,
                    actual=actual_val,
                    predicted=pred_val,
                    fold=fold.fold,
                )
            )

    metrics = compute_forecast_metrics(fold_results)
    summary = ForecastSummary(
        model_name="StackingEnsemble",
        n_folds=len(fold_results),
        directional_accuracy=metrics["directional_accuracy"],
        mae=metrics["mae"],
        rmse=metrics["rmse"],
        mape=metrics["mape"],
        mean_forecast=[],
        std_forecast=[],
    )

    return OBBject(results=results, extra={"summary": summary.model_dump()})


# ---------------------------------------------------------------------------
# RL Trading Agents
# ---------------------------------------------------------------------------


def _run_agent(agent_cls, agent_name, data, target_column, initial_capital,
               commission_bps, slippage_bps, window_size, episodes,
               max_position, stop_loss_pct, max_drawdown_pct, train_ratio,
               **agent_kwargs):
    """Shared logic for all agent commands."""
    from openbb_forecast.schemas.agent import AgentBacktestResult, AgentSummary
    from openbb_forecast.schemas.backtest import BacktestSummary

    df = _data_to_df(data)
    prices = _extract_prices(df, target_column)
    dates = _get_dates(df)

    state_size = window_size + 3  # price changes + position + pnl + cash ratios

    agent = agent_cls(state_size=state_size, **agent_kwargs)

    wf_result = agent.walk_forward_evaluate(
        prices=prices,
        train_ratio=train_ratio,
        initial_capital=initial_capital,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        window_size=window_size,
        episodes=episodes,
        max_position=max_position,
        stop_loss_pct=stop_loss_pct,
        max_drawdown_pct=max_drawdown_pct,
    )

    # Build result rows from test evaluation
    test_results = wf_result["test_results"]
    split_idx = int(len(prices) * train_ratio)
    test_dates = dates[split_idx:]
    equity = test_results["equity_curve"]
    benchmark = wf_result["benchmark_curve"]
    actions_log = test_results["actions_log"]

    results = []
    for t in range(min(len(equity), len(test_dates))):
        action_info = actions_log[t] if t < len(actions_log) else {}
        results.append(
            AgentBacktestResult(
                date=test_dates[t],
                portfolio_value=float(equity[t]) if t < len(equity) else float(equity[-1]),
                benchmark_value=float(benchmark[t]) if t < len(benchmark) else float(benchmark[-1]),
                position=0,
                action=action_info.get("action_taken", "hold"),
                trade_price=action_info.get("trade_price"),
                commission=action_info.get("commission"),
                slippage=action_info.get("slippage"),
                reward=0.0,
                cumulative_return=(float(equity[t]) / initial_capital - 1.0) if t < len(equity) else 0.0,
                drawdown=0.0,
                risk_event=action_info.get("risk_event"),
            )
        )

    bs = wf_result["backtest_summary"]
    train_rewards = wf_result["training_rewards"]

    summary = AgentSummary(
        agent_name=agent_name,
        train_episodes=episodes,
        train_period=f"{dates[0]} to {dates[split_idx - 1]}",
        test_period=f"{test_dates[0]} to {test_dates[-1]}" if test_dates else "",
        final_train_reward=float(np.mean(train_rewards[-10:])) if len(train_rewards) >= 10 else float(np.mean(train_rewards)) if train_rewards else 0.0,
        training_rewards=train_rewards,
        backtest=BacktestSummary(
            test_start=test_dates[0] if test_dates else "",
            test_end=test_dates[-1] if test_dates else "",
            **bs,
        ),
        risk_events=wf_result["risk_events"],
        stop_losses_triggered=wf_result["stop_losses"],
        drawdown_breakers_triggered=wf_result["drawdown_breakers"],
    )

    return OBBject(results=results, extra={"summary": summary.model_dump()})


@router.command(methods=["POST"])
async def double_dqn(
    data: list[Data],
    target_column: str = "close",
    initial_capital: float = 10_000.0,
    commission_bps: float = 10.0,
    slippage_bps: float = 5.0,
    window_size: int = 30,
    episodes: int = 200,
    max_position: int = 5,
    stop_loss_pct: float = 0.02,
    max_drawdown_pct: float = 0.10,
    train_ratio: float = 0.7,
) -> OBBject:
    """Double DQN trading agent with risk management.

    Trains a Double Deep Q-Network on the first portion of data (train_ratio),
    then evaluates with greedy policy on the unseen remainder. Includes transaction
    costs, position limits, stop-losses, and drawdown circuit breaker.
    """
    from openbb_forecast.agents.double_dqn import DoubleDQNAgent

    return _run_agent(
        DoubleDQNAgent, "DoubleDQN", data, target_column, initial_capital,
        commission_bps, slippage_bps, window_size, episodes,
        max_position, stop_loss_pct, max_drawdown_pct, train_ratio,
    )


@router.command(methods=["POST"])
async def policy_gradient(
    data: list[Data],
    target_column: str = "close",
    initial_capital: float = 10_000.0,
    commission_bps: float = 10.0,
    slippage_bps: float = 5.0,
    window_size: int = 30,
    episodes: int = 200,
    max_position: int = 5,
    stop_loss_pct: float = 0.02,
    max_drawdown_pct: float = 0.10,
    train_ratio: float = 0.7,
) -> OBBject:
    """REINFORCE policy gradient agent with learned baseline.

    Uses a policy network (state -> action probabilities) with a value network
    baseline to reduce variance. Includes entropy regularization for exploration.
    """
    from openbb_forecast.agents.policy_gradient import PolicyGradientAgent

    return _run_agent(
        PolicyGradientAgent, "PolicyGradient", data, target_column, initial_capital,
        commission_bps, slippage_bps, window_size, episodes,
        max_position, stop_loss_pct, max_drawdown_pct, train_ratio,
    )


@router.command(methods=["POST"])
async def evolution_strategy(
    data: list[Data],
    target_column: str = "close",
    initial_capital: float = 10_000.0,
    commission_bps: float = 10.0,
    slippage_bps: float = 5.0,
    window_size: int = 30,
    episodes: int = 500,
    max_position: int = 5,
    stop_loss_pct: float = 0.02,
    max_drawdown_pct: float = 0.10,
    train_ratio: float = 0.7,
    population_size: int = 15,
    sigma: float = 0.1,
) -> OBBject:
    """Natural Evolution Strategy trading agent.

    Evolves neural network weights using population-based optimization.
    Fitness is measured by Sharpe ratio (risk-adjusted returns), not raw P&L.
    Uses antithetic sampling and rank-based fitness shaping.
    """
    from openbb_forecast.agents.evolution_strategy import EvolutionStrategyAgent

    return _run_agent(
        EvolutionStrategyAgent, "EvolutionStrategy", data, target_column, initial_capital,
        commission_bps, slippage_bps, window_size, episodes,
        max_position, stop_loss_pct, max_drawdown_pct, train_ratio,
        population_size=population_size, sigma=sigma,
    )


# ---------------------------------------------------------------------------
# Monte Carlo Simulation
# ---------------------------------------------------------------------------


@router.command(methods=["POST"])
async def monte_carlo(
    data: list[Data],
    target_column: str = "close",
    n_simulations: int = 1000,
    forecast_days: int = 30,
    dynamic_volatility: bool = False,
    seed: int = 42,
) -> OBBject:
    """Monte Carlo simulation with Geometric Brownian Motion.

    Simulates future price paths using estimated drift and volatility from
    historical data. Reports confidence intervals, VaR, and CVaR.
    Supports static or dynamic (rolling) volatility estimation.
    """
    from openbb_forecast.schemas.simulation import MonteCarloResult, MonteCarloSummary
    from openbb_forecast.simulation.monte_carlo import MonteCarloSimulator

    df = _data_to_df(data)
    prices = _extract_prices(df, target_column)

    sim = MonteCarloSimulator(seed=seed)
    result = sim.simulate(
        prices=prices,
        n_simulations=n_simulations,
        forecast_days=forecast_days,
        dynamic_volatility=dynamic_volatility,
    )

    results = [MonteCarloResult(**day) for day in result["day_stats"]]
    summary = MonteCarloSummary(**result["summary"])

    return OBBject(results=results, extra={"summary": summary.model_dump()})


# ---------------------------------------------------------------------------
# Risk Metrics
# ---------------------------------------------------------------------------


@router.command(methods=["POST"])
async def metrics(
    data: list[Data],
    target_column: str = "close",
    risk_free_rate: float = 0.0,
) -> OBBject:
    """Compute risk and return metrics for a price series.

    Calculates: Sharpe ratio, Sortino ratio, max drawdown, Calmar ratio,
    annualized return, annualized volatility, VaR (95%), CVaR (95%).
    """
    from openbb_forecast.risk import metrics as rm

    df = _data_to_df(data)
    prices = _extract_prices(df, target_column)

    log_returns = np.diff(np.log(prices))
    equity = prices / prices[0] * 10_000  # Normalize to $10k start

    result = {
        "annualized_return": rm.annualized_return(log_returns),
        "annualized_volatility": float(np.std(log_returns) * np.sqrt(252)),
        "sharpe_ratio": rm.sharpe_ratio(log_returns, risk_free_rate),
        "sortino_ratio": rm.sortino_ratio(log_returns, risk_free_rate),
        "max_drawdown": rm.max_drawdown(equity),
        "calmar_ratio": rm.calmar_ratio(log_returns, equity),
        "var_95": rm.value_at_risk(log_returns, 0.95),
        "cvar_95": rm.conditional_var(log_returns, 0.95),
    }

    return OBBject(results=result)
