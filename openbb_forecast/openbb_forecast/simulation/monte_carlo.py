"""Monte Carlo simulation with Geometric Brownian Motion.

Replaces simulation/monte-carlo-simple.ipynb and simulation/monte-carlo-dynamic-volatility.ipynb.

Key differences from original:
  - Includes drift parameter (mu) — original used zero drift
  - Supports dynamic volatility (rolling window re-estimation)
  - Computes VaR and CVaR from terminal distribution
  - Proper confidence intervals at user-specified percentiles
  - 1000+ simulations by default (original used only 100)
"""

from __future__ import annotations

import numpy as np


class MonteCarloSimulator:
    """Geometric Brownian Motion Monte Carlo price simulation.

    Model:
        S_{t+1} = S_t * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)

    where:
        mu = annualized drift (estimated from historical log returns)
        sigma = annualized volatility
        dt = 1/252 (one trading day)
        Z ~ N(0, 1)

    Supports two modes:
        - Static volatility: sigma from full historical window
        - Dynamic volatility: sigma re-estimated from rolling window each step

    Args:
        seed: Random seed for reproducibility.
    """

    def __init__(self, seed: int = 42):
        self.seed = seed

    def simulate(
        self,
        prices: np.ndarray,
        n_simulations: int = 1000,
        forecast_days: int = 30,
        dynamic_volatility: bool = False,
        volatility_window: int = 30,
    ) -> dict:
        """Run Monte Carlo simulation.

        Args:
            prices: Historical close prices.
            n_simulations: Number of simulation paths.
            forecast_days: Days to simulate forward.
            dynamic_volatility: If True, re-estimate sigma each step.
            volatility_window: Rolling window for dynamic volatility.

        Returns:
            Dict with simulation paths, statistics per day, and summary.
        """
        prices = np.asarray(prices, dtype=np.float64)
        rng = np.random.default_rng(self.seed)

        # Estimate parameters from historical data
        log_returns = np.diff(np.log(prices))
        mu_daily = np.mean(log_returns)
        sigma_daily = np.std(log_returns, ddof=1)
        dt = 1.0  # daily

        mu_annual = mu_daily * 252
        sigma_annual = sigma_daily * np.sqrt(252)

        last_price = prices[-1]

        # Generate all random shocks at once
        Z = rng.standard_normal((n_simulations, forecast_days))

        # Simulate paths
        paths = np.zeros((n_simulations, forecast_days + 1))
        paths[:, 0] = last_price

        if not dynamic_volatility:
            # Static volatility: same sigma for all steps
            drift = (mu_daily - 0.5 * sigma_daily**2) * dt
            diffusion = sigma_daily * np.sqrt(dt)

            for t in range(forecast_days):
                paths[:, t + 1] = paths[:, t] * np.exp(drift + diffusion * Z[:, t])
        else:
            # Dynamic volatility: re-estimate from expanding window of simulated returns
            recent_returns = list(log_returns[-volatility_window:])

            for t in range(forecast_days):
                current_sigma = np.std(recent_returns[-volatility_window:], ddof=1)
                current_mu = np.mean(recent_returns[-volatility_window:])
                drift = (current_mu - 0.5 * current_sigma**2) * dt
                diffusion = current_sigma * np.sqrt(dt)

                step_returns = drift + diffusion * Z[:, t]
                paths[:, t + 1] = paths[:, t] * np.exp(step_returns)

                # Update rolling returns with median simulated return
                recent_returns.append(float(np.median(step_returns)))

        # Compute per-day statistics
        day_stats = []
        for t in range(1, forecast_days + 1):
            day_prices = paths[:, t]
            day_returns = (day_prices - last_price) / last_price

            percentile_5 = float(np.percentile(day_returns, 5))
            var_95 = max(0.0, -percentile_5)
            tail = day_returns[day_returns <= percentile_5]
            cvar_95 = float(-np.mean(tail)) if len(tail) > 0 else var_95

            day_stats.append({
                "day": t,
                "mean_price": float(np.mean(day_prices)),
                "median_price": float(np.median(day_prices)),
                "std_price": float(np.std(day_prices)),
                "percentile_5": float(np.percentile(day_prices, 5)),
                "percentile_25": float(np.percentile(day_prices, 25)),
                "percentile_75": float(np.percentile(day_prices, 75)),
                "percentile_95": float(np.percentile(day_prices, 95)),
                "var_95": var_95,
                "cvar_95": cvar_95,
            })

        # Terminal stats
        terminal_prices = paths[:, -1]
        terminal_returns = (terminal_prices - last_price) / last_price
        terminal_percentile_5 = float(np.percentile(terminal_returns, 5))
        terminal_var = max(0.0, -terminal_percentile_5)
        terminal_tail = terminal_returns[terminal_returns <= terminal_percentile_5]
        terminal_cvar = float(-np.mean(terminal_tail)) if len(terminal_tail) > 0 else terminal_var

        summary = {
            "n_simulations": n_simulations,
            "forecast_days": forecast_days,
            "initial_price": float(last_price),
            "annualized_drift": float(mu_annual),
            "annualized_volatility": float(sigma_annual),
            "prob_profit": float(np.mean(terminal_prices > last_price)),
            "expected_terminal_price": float(np.mean(terminal_prices)),
            "terminal_var_95": terminal_var,
            "terminal_cvar_95": terminal_cvar,
        }

        return {
            "paths": paths,
            "day_stats": day_stats,
            "summary": summary,
        }
