"""Backtest quality-control statistics (Chan Ch.2).

Implements three checks that any tradable backtest result must pass:

1. **Deflated Sharpe Ratio (DSR)** -- Bailey & Lopez de Prado (2014).
   Adjusts the observed Sharpe for (a) the number of strategy trials
   you ran searching for a winner and (b) the non-normality of the
   returns distribution. A raw Sharpe of 1.5 from 100 trials is far
   weaker than 1.5 from a single pre-registered trial.

2. **Permutation p-value** -- shuffles the sign / order of returns and
   asks: "How often does pure noise produce a Sharpe at least this
   good?" If p > 0.05 the strategy edge is not distinguishable from
   chance.

3. **White's Reality Check (single-strategy variant)** -- bootstrap
   confidence interval on the mean return; rejects strategies whose
   95% lower bound is <= 0.

These functions take a list/array of TRADE returns (one per closed
trade), not bar returns, so they work directly on the output of
`run_backtest` in halal_screener.py.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy import stats


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio
# ---------------------------------------------------------------------------

def _expected_max_z(n_trials: int) -> float:
    """E[max of N standard normals] -- approximation used in DSR.

    Bailey & Lopez de Prado (2014) eq. 6 -- valid for n_trials >= 1.
    """
    if n_trials <= 1:
        return 0.0
    euler_mascheroni = 0.5772156649
    inv_phi = stats.norm.ppf(1.0 - 1.0 / n_trials)
    inv_phi_e = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float((1.0 - euler_mascheroni) * inv_phi + euler_mascheroni * inv_phi_e)


def deflated_sharpe(
    returns: Sequence[float],
    n_trials: int = 1,
    annualization: int = 252,
) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    Args:
        returns: per-trade or per-period returns (simple, not log).
        n_trials: how many strategy variants you tested (e.g. parameter
            grid size). Setting this honestly is critical -- it is the
            single biggest defence against backtest over-fitting.
        annualization: 252 for daily, 12 for monthly. For per-trade
            returns, set to expected trades/year or pass 1 to skip.

    Returns:
        Deflated Sharpe in [0, 1] -- interpret as the probability that
        the true Sharpe is > 0 given trial count and distribution shape.
    """
    r = np.asarray(returns, dtype=np.float64)
    r = r[~np.isnan(r)]
    n = r.size
    if n < 5:
        return 0.0

    mean = float(r.mean())
    std = float(r.std(ddof=1))
    if std <= 0:
        return 0.0

    sr = mean / std * math.sqrt(annualization)

    # Skewness & excess kurtosis of returns
    skew = float(stats.skew(r))
    kurt = float(stats.kurtosis(r, fisher=True))  # excess kurtosis

    # Expected max Sharpe under H0 (no edge, n_trials independent strategies)
    sr0 = _expected_max_z(max(n_trials, 1)) / math.sqrt(annualization)

    # Variance of estimated Sharpe under non-normal returns
    # Mertens (2002) / Lo (2002):
    var_sr = (1.0 - skew * sr + (kurt / 4.0) * sr * sr) / (n - 1)
    if var_sr <= 0:
        return 0.0

    z = (sr - sr0 * math.sqrt(annualization)) / math.sqrt(var_sr * annualization)
    return float(stats.norm.cdf(z))


# ---------------------------------------------------------------------------
# Permutation p-value
# ---------------------------------------------------------------------------

def permutation_pvalue(
    returns: Sequence[float],
    n_perm: int = 1000,
    seed: int = 42,
) -> float:
    """Probability that a random reshuffling of return signs beats the strategy.

    Tests H0: mean return = 0. The strategy's observed Sharpe is compared
    against `n_perm` random sign-flipped versions of the same returns.
    Returns the fraction of permutations whose Sharpe is >= observed.

    p < 0.05 -> reject random-edge hypothesis.
    """
    r = np.asarray(returns, dtype=np.float64)
    r = r[~np.isnan(r)]
    n = r.size
    if n < 5:
        return 1.0

    mean = float(r.mean())
    std = float(r.std(ddof=1))
    if std <= 0:
        return 1.0
    sr_obs = mean / std

    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_perm):
        signs = rng.choice([-1.0, 1.0], size=n)
        rp = r * signs
        sp = rp.std(ddof=1)
        if sp <= 0:
            continue
        if rp.mean() / sp >= sr_obs:
            hits += 1
    return float(hits) / float(n_perm)


# ---------------------------------------------------------------------------
# Single-strategy Reality Check (bootstrap CI on mean return)
# ---------------------------------------------------------------------------

def reality_check_lower_bound(
    returns: Sequence[float],
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 42,
) -> float:
    """Bootstrap (1-alpha) lower bound on mean return.

    Returns the alpha-quantile of bootstrap mean-return distribution.
    If <= 0, the strategy's edge is not statistically positive.
    """
    r = np.asarray(returns, dtype=np.float64)
    r = r[~np.isnan(r)]
    n = r.size
    if n < 5:
        return float("-inf")

    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        sample = rng.choice(r, size=n, replace=True)
        means[i] = sample.mean()
    return float(np.quantile(means, alpha))


# ---------------------------------------------------------------------------
# Convenience: full QC report
# ---------------------------------------------------------------------------

def qc_report(
    returns: Sequence[float],
    n_trials: int = 1,
    annualization: int = 252,
) -> dict:
    """Run all three QC tests; return a dict ready to attach to a backtest summary."""
    return {
        "deflated_sharpe": deflated_sharpe(returns, n_trials=n_trials, annualization=annualization),
        "permutation_pvalue": permutation_pvalue(returns),
        "bootstrap_lower_5pct": reality_check_lower_bound(returns),
    }
