"""Hidden-Markov regime model — a probabilistic upgrade over the binary SPY>EMA21 gate.

Quant desks don't flip a switch on regime; they carry a DISTRIBUTION over hidden states.
We fit a 3-state Gaussian HMM on SPY's (daily return, rolling vol): emissions via a Gaussian
mixture, a transition matrix estimated from the decoded sequence, then the forward algorithm
gives the FILTERED probability of each state today. States are ordered by mean return →
calm_bull / choppy / crisis. P(crisis)=0.7 lets us de-risk BEFORE the moving average breaks.

Fails soft (returns None) on short history or if SciPy/sklearn are unavailable — callers
keep their existing regime logic.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("screener")


def _features(closes: np.ndarray, vol_win: int = 21):
    c = np.asarray(closes, dtype=float)
    rets = np.diff(np.log(c))
    if len(rets) < vol_win + 5:
        return None
    vol = np.array([rets[max(0, i - vol_win):i + 1].std() for i in range(len(rets))])
    X = np.column_stack([rets, vol])
    return X[vol_win:]          # drop the warm-up where vol is unstable


def _transition_matrix(labels: np.ndarray, k: int) -> np.ndarray:
    A = np.ones((k, k))         # Laplace smoothing (no zero transitions)
    for a, b in zip(labels[:-1], labels[1:]):
        A[a, b] += 1
    return A / A.sum(axis=1, keepdims=True)


def regime_probabilities(closes, n_states: int = 3) -> dict | None:
    """Filtered P(regime today) from SPY closes → {calm_bull, choppy, crisis, dominant,
    crisis_prob}. None on insufficient data or missing deps."""
    try:
        from sklearn.mixture import GaussianMixture
        from scipy.stats import multivariate_normal
    except Exception as e:
        logger.debug("regime_hmm deps unavailable: %s", e)
        return None

    X = _features(closes)
    if X is None or len(X) < 60:
        return None
    try:
        gm = GaussianMixture(n_components=n_states, covariance_type="full",
                             random_state=7, n_init=3, reg_covar=1e-6).fit(X)
        labels = gm.predict(X)
        A = _transition_matrix(labels, n_states)
        means, covs = gm.means_, gm.covariances_

        # forward filtering → filtered state prob at the last observation
        alpha = gm.predict_proba(X[:1])[0]
        for t in range(1, len(X)):
            b = np.array([max(multivariate_normal.pdf(X[t], means[k], covs[k], allow_singular=True), 1e-300)
                          for k in range(n_states)])
            alpha = b * (alpha @ A)
            s = alpha.sum()
            alpha = alpha / s if s > 0 else np.full(n_states, 1.0 / n_states)

        order = np.argsort(means[:, 0])           # sort states by mean return
        crisis, choppy, calm = order[0], order[1], order[-1]
        names = ["crisis", "choppy", "calm_bull"]
        probs = {names[i]: round(float(alpha[st]), 4) for i, st in enumerate([crisis, choppy, calm])}
        dominant = max(probs, key=probs.get)
        return {**probs, "dominant": dominant, "crisis_prob": probs["crisis"], "n_obs": int(len(X))}
    except Exception as e:
        logger.debug("regime_hmm fit failed: %s", e)
        return None


def hmm_size_multiplier(closes) -> float:
    """Continuous book multiplier from the HMM: scale down smoothly as crisis probability
    rises, up in calm bull. multiplier = 1.0 − 0.6·P(crisis) + 0.2·P(calm_bull), in
    [0.4, 1.2]. Fails soft to 1.0 (callers still have regime_size_multiplier)."""
    r = regime_probabilities(closes)
    if not r:
        return 1.0
    m = 1.0 - 0.6 * r["crisis"] + 0.2 * r["calm_bull"]
    return float(max(0.4, min(1.2, m)))


__all__ = ["regime_probabilities", "hmm_size_multiplier"]
