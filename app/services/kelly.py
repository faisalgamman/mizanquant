"""Continuous Kelly position sizing (Chan Ch.7).

For a strategy with per-trade percentage returns r_i, the Kelly-optimal
fraction of equity to risk per trade is

    f* = mean(r) / var(r)

(Eq. 7.5 in Ernest Chan, *Algorithmic Trading*, 2013.)

This is the *continuous* Kelly — it does not require trades to be binary
win/loss outcomes, and so is the right formula for trades that exit at
varying stop / take-profit levels. The previous implementation used the
binary form (`p - (1-p)/b`) which throws away most of the return
distribution.

Two practical adjustments matter (Chan §7.4):

1. **Fractional Kelly** — full Kelly maximizes long-run growth *if* μ
   and σ are known exactly. They are not. Half-Kelly gives ~75% of full
   Kelly's growth with ~50% of its drawdown — a much better tradeoff in
   practice. Default: 0.5.

2. **Sample-size shrinkage** — with only 20–50 trades, μ is very noisy
   and easily overstated. We shrink toward zero by `n / (n + n_prior)`,
   so a strategy with 20 trades and `n_prior=30` is sized at 40% of the
   raw Kelly estimate. This is the empirical-Bayes adjustment that
   Chan's "estimation error" warning is pointing at.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np


def continuous_kelly(
    pnls_pct: Iterable[float],
    fractional: float = 0.5,
    n_prior: int = 30,
    n_min: int = 10,
    max_kelly: float = 0.25,
) -> float:
    """Compute fractional Kelly with sample-size shrinkage.

    Parameters
    ----------
    pnls_pct : iterable of float
        Per-trade returns expressed as fractions of capital risked (or of
        position notional — pick one and be consistent). 0.05 = +5%.
    fractional : float, default 0.5
        Fraction of full Kelly to use (0.5 = half-Kelly).
    n_prior : int, default 30
        Pseudo-trade count used for shrinkage. Larger → more skeptical of
        small samples.
    n_min : int, default 10
        Below this trade count, return 0. Kelly is unreliable on tiny
        samples.
    max_kelly : float, default 0.25
        Hard ceiling on the returned fraction. Even with strong stats
        we never risk more than this fraction of equity per trade.

    Returns
    -------
    float in [0, max_kelly]. 0 means "do not size from Kelly — fall back
    to the static risk budget."
    """
    arr = np.asarray(list(pnls_pct), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < n_min:
        return 0.0

    mu = float(arr.mean())
    var = float(arr.var(ddof=1)) if n > 1 else 0.0
    if var <= 0 or mu <= 0:
        return 0.0

    f_full = mu / var
    f_shrunk = f_full * (n / (n + n_prior))
    f_used = fractional * f_shrunk
    return float(min(max(f_used, 0.0), max_kelly))


def kelly_from_db_pnls(
    pnls_pct: Iterable[float],
    static_risk_pct: float,
    fractional: float = 0.5,
    n_prior: int = 30,
    n_min: int = 10,
) -> tuple[float, dict]:
    """Wrapper that returns the effective per-trade risk fraction and a
    diagnostic dict.

    The effective risk is min(static_risk_pct, kelly_fraction). The
    static budget is therefore a hard ceiling — Kelly can only ever
    *reduce* sizing, never expand beyond what risk policy allows.
    """
    f_kelly = continuous_kelly(
        pnls_pct,
        fractional=fractional,
        n_prior=n_prior,
        n_min=n_min,
    )
    if f_kelly <= 0:
        return static_risk_pct, {
            "kelly_used": False,
            "kelly_fraction": 0.0,
            "n_trades": len(list(pnls_pct)) if not hasattr(pnls_pct, "__len__") else len(pnls_pct),
        }

    eff = min(static_risk_pct, f_kelly)
    return eff, {
        "kelly_used": eff < static_risk_pct,
        "kelly_fraction": round(f_kelly, 6),
        "static_risk_pct": round(static_risk_pct, 6),
        "effective_risk_pct": round(eff, 6),
    }


__all__ = ["continuous_kelly", "kelly_from_db_pnls"]
