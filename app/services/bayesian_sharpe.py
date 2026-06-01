"""Bayesian Sharpe Ratio — full posterior distribution.

Replaces point-estimate Sharpe with a Bayesian posterior that provides
credible intervals, probability of positive Sharpe, and expected loss.
Improves strategy stop/start decisions (Jansen Ch.10).

Reference
---------
- Jansen (2020), Ch.10 — Bayesian Sharpe ratio and decision-making
- Kruschke (2014) — Doing Bayesian Data Analysis
"""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np

logger = logging.getLogger(__name__)

# Default priors (weakly informative for daily returns)
DEFAULT_PRIOR_MEAN: float = 0.0     # prior mean return (daily)
DEFAULT_PRIOR_STD: float = 0.02      # prior std return (daily, ~2%)
DEFAULT_PRIOR_DF: int = 5            # prior degrees of freedom (moderate)


def bayesian_sharpe(
    returns: Iterable[float],
    risk_free: float = 0.0,
    prior_mean: float = DEFAULT_PRIOR_MEAN,
    prior_std: float = DEFAULT_PRIOR_STD,
    prior_df: int = DEFAULT_PRIOR_DF,
    n_posterior: int = 10_000,
) -> dict:
    """Compute Bayesian posterior distribution of the Sharpe ratio.

    Uses a Normal-Inverse-Gamma conjugate prior over (mean, variance)
    of returns, then draws from the posterior to estimate Sharpe.

    Parameters
    ----------
    returns : iterable of float
        Per-period returns (daily, monthly — whatever the caller uses).
    risk_free : float
        Risk-free rate per period (default 0).
    prior_mean : float
        Prior belief about mean return.
    prior_std : float
        Prior belief about return std.
    prior_df : int
        Prior degrees of freedom (higher = stronger prior).
    n_posterior : int
        Number of posterior draws.

    Returns
    -------
    dict with keys: sharpe_mean, sharpe_median, sharpe_std,
                    ci_95_lower, ci_95_upper, p_positive, p_greater_1,
                    expected_loss, n_returns, prior_params
    """
    arr = np.asarray(list(returns), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)

    if n < 5:
        return {
            "sharpe_mean": 0.0, "sharpe_median": 0.0, "sharpe_std": 0.0,
            "ci_95_lower": 0.0, "ci_95_upper": 0.0,
            "p_positive": 0.5, "p_greater_1": 0.0,
            "expected_loss": 0.0, "n_returns": n,
            "error": "Need >= 5 returns",
        }

    excess = arr - risk_free
    sample_mean = float(np.mean(excess))
    sample_var = float(np.var(excess, ddof=1)) if n > 1 else 1e-8
    if sample_var <= 0:
        sample_var = 1e-8

    # Posterior parameters (Normal-Inverse-Gamma conjugate)
    post_n = prior_df + n
    post_mean = (prior_df * prior_mean + n * sample_mean) / post_n
    post_scale = (
        prior_df * prior_std**2
        + n * sample_var
        + (prior_df * n / post_n) * (sample_mean - prior_mean)**2
    )
    post_var = post_scale / post_n

    # Draw from posterior: sigma^2 ~ Inv-Gamma, then mu | sigma^2 ~ Normal
    rng = np.random.default_rng(42)
    sigma2_draws = 1.0 / rng.gamma(post_n / 2, 2.0 / post_scale, size=n_posterior)
    sigma_draws = np.sqrt(sigma2_draws)
    mu_draws = rng.normal(post_mean, np.sqrt(sigma2_draws / post_n))

    sharpe_draws = mu_draws / np.maximum(sigma_draws, 1e-8)

    # Posterior summaries
    sharpe_mean = float(np.mean(sharpe_draws))
    sharpe_median = float(np.median(sharpe_draws))
    sharpe_std = float(np.std(sharpe_draws))
    ci_lower = float(np.percentile(sharpe_draws, 2.5))
    ci_upper = float(np.percentile(sharpe_draws, 97.5))
    p_positive = float(np.mean(sharpe_draws > 0))
    p_greater_1 = float(np.mean(sharpe_draws > 1.0))

    # Expected loss: E[min(Sharpe, 0)] * sqrt(252) annualised
    expected_loss = float(np.mean(np.minimum(sharpe_draws, 0)))

    return {
        "sharpe_mean": round(sharpe_mean, 4),
        "sharpe_median": round(sharpe_median, 4),
        "sharpe_std": round(sharpe_std, 4),
        "ci_95_lower": round(ci_lower, 4),
        "ci_95_upper": round(ci_upper, 4),
        "p_positive": round(p_positive, 4),
        "p_greater_1": round(p_greater_1, 4),
        "expected_loss": round(expected_loss, 4),
        "n_returns": n,
        "prior_params": {
            "mean": prior_mean, "std": prior_std, "df": prior_df,
        },
    }


def bayesian_sharpe_annual(
    daily_returns: Iterable[float],
    periods_per_year: int = 252,
    **kwargs,
) -> dict:
    """Bayesian Sharpe annualised from daily returns.

    Rescales the posterior draws by sqrt(periods_per_year).
    """
    daily = bayesian_sharpe(daily_returns, **kwargs)
    sqrt_p = np.sqrt(periods_per_year)

    return {
        **daily,
        "sharpe_mean": round(daily["sharpe_mean"] * sqrt_p, 4),
        "sharpe_median": round(daily["sharpe_median"] * sqrt_p, 4),
        "sharpe_std": round(daily["sharpe_std"] * sqrt_p, 4),
        "ci_95_lower": round(daily["ci_95_lower"] * sqrt_p, 4),
        "ci_95_upper": round(daily["ci_95_upper"] * sqrt_p, 4),
        "expected_loss": round(daily["expected_loss"] * sqrt_p, 4),
        "annualised": True,
        "periods_per_year": periods_per_year,
    }
