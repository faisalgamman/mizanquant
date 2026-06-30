"""Cointegration toolkit for pairs trading (Chan Ch.4).

Two non-stationary series y_t and x_t are *cointegrated* when there
exists a linear combination `spread_t = y_t - β·x_t - α` that is
stationary. Cointegration is the structural prerequisite for a pairs
mean-reversion trade: each leg can drift forever, but the spread must
revert. Without it, the "pair" is just two correlated random walks and
the trade is doomed.

Workflow (Chan §4.1–4.3):

1. Fit the hedge ratio β via OLS of y on x (and intercept α).
2. Engle–Granger test: ADF on the residual spread. Low p-value (≤ 0.05)
   ⇒ reject unit root in residuals ⇒ cointegrated.
3. Compute the rolling z-score of the spread; trades are placed when
   |z| ≥ entry_z and unwound when |z| ≤ exit_z.
4. The spread's OU half-life (reused from Phase 3) tells you the
   expected holding period. Strategies need it short enough that the
   trade closes before the cointegration relationship breaks down.

This module exposes the tools without wiring them to a live strategy —
the codebase doesn't currently run pairs trades. The toolkit is ready
for a future Strategy D, and for ad-hoc analysis via the consensus
agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

try:
    from statsmodels.tsa.stattools import coint as _coint
    _HAS_COINT = True
except Exception:  # pragma: no cover
    _HAS_COINT = False

from app.services.stationarity import half_life_ou


@dataclass
class CointegrationReport:
    pvalue: float
    hedge_ratio: float
    intercept: float
    zscore: float
    half_life: float
    is_cointegrated: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "pvalue": round(self.pvalue, 4),
            "hedge_ratio": round(self.hedge_ratio, 4),
            "intercept": round(self.intercept, 4),
            "zscore": round(self.zscore, 3),
            "half_life_bars": round(self.half_life, 2),
            "is_cointegrated": self.is_cointegrated,
            "reason": self.reason,
        }


def hedge_ratio(y: Iterable[float], x: Iterable[float]) -> tuple[float, float]:
    """OLS hedge ratio (β) and intercept (α) from `y = α + β·x + ε`.

    Returns (0.0, 0.0) on degenerate input rather than raising. Callers
    interpret a zero hedge ratio as "no relationship".
    """
    y_arr = np.asarray(list(y), dtype=float)
    x_arr = np.asarray(list(x), dtype=float)
    mask = np.isfinite(y_arr) & np.isfinite(x_arr)
    y_arr = y_arr[mask]
    x_arr = x_arr[mask]
    if len(y_arr) < 30 or np.ptp(x_arr) == 0:
        return 0.0, 0.0
    A = np.column_stack([np.ones_like(x_arr), x_arr])
    try:
        beta, *_ = np.linalg.lstsq(A, y_arr, rcond=None)
    except np.linalg.LinAlgError:
        return 0.0, 0.0
    return float(beta[1]), float(beta[0])


def spread_series(y: Iterable[float], x: Iterable[float]) -> np.ndarray:
    """Compute spread_t = y_t - β·x_t - α using OLS β/α."""
    y_arr = np.asarray(list(y), dtype=float)
    x_arr = np.asarray(list(x), dtype=float)
    beta, alpha = hedge_ratio(y_arr, x_arr)
    return y_arr - beta * x_arr - alpha


def engle_granger_pvalue(y: Iterable[float], x: Iterable[float]) -> float:
    """Engle–Granger cointegration p-value. Returns 1.0 when the test
    cannot be run (constant series, too few samples, statsmodels
    missing)."""
    y_arr = np.asarray(list(y), dtype=float)
    x_arr = np.asarray(list(x), dtype=float)
    mask = np.isfinite(y_arr) & np.isfinite(x_arr)
    y_arr = y_arr[mask]
    x_arr = x_arr[mask]
    if len(y_arr) < 30 or np.ptp(y_arr) == 0 or np.ptp(x_arr) == 0:
        return 1.0
    if not _HAS_COINT:
        return 1.0
    try:
        _, pvalue, _ = _coint(y_arr, x_arr)
        return float(pvalue)
    except Exception:
        return 1.0


def adf_pvalue(series: Iterable[float]) -> float:
    """Augmented Dickey–Fuller p-value for a SINGLE series (unit-root test).

    Low p (≤ ~0.05) ⇒ reject the unit root ⇒ the series is stationary
    (mean-reverting). Used by the out-of-sample check to ask "is the test-window
    spread still stationary?". Returns 1.0 when the test can't run (too few
    points, constant series, or statsmodels missing) — i.e. "no evidence of
    stationarity"."""
    arr = np.asarray(list(series), dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 20 or np.ptp(arr) == 0:
        return 1.0
    if not _HAS_COINT:  # adfuller ships in the same statsmodels module as coint
        return 1.0
    try:
        from statsmodels.tsa.stattools import adfuller
        return float(adfuller(arr, autolag="AIC")[1])
    except Exception:
        return 1.0


def spread_zscore(spread: Iterable[float], lookback: int = 60) -> float:
    """Z-score of the latest spread value vs the trailing window."""
    arr = np.asarray(list(spread), dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < max(10, lookback // 4):
        return 0.0
    window = arr[-lookback:] if len(arr) >= lookback else arr
    mu = float(window.mean())
    sd = float(window.std(ddof=1))
    if sd <= 0:
        return 0.0
    return float((arr[-1] - mu) / sd)


def cointegration_report(
    y: Iterable[float],
    x: Iterable[float],
    pvalue_threshold: float = 0.05,
    max_half_life_bars: float = 30.0,
    zscore_lookback: int = 60,
) -> CointegrationReport:
    """Combined gate. Cointegrated iff ADF on residuals rejects unit
    root AND the spread half-life is short enough to trade."""
    y_arr = np.asarray(list(y), dtype=float)
    x_arr = np.asarray(list(x), dtype=float)
    p = engle_granger_pvalue(y_arr, x_arr)
    beta, alpha = hedge_ratio(y_arr, x_arr)
    spread = y_arr - beta * x_arr - alpha
    z = spread_zscore(spread, lookback=zscore_lookback)
    hl = half_life_ou(spread)

    reasons: list[str] = []
    if p > pvalue_threshold:
        reasons.append(f"EG p={p:.3f}>{pvalue_threshold}")
    if hl > max_half_life_bars or not np.isfinite(hl):
        reasons.append(f"half-life={hl:.1f}>{max_half_life_bars}")
    if beta == 0.0:
        reasons.append("hedge ratio undefined")

    is_coint = len(reasons) == 0
    reason = "cointegrated; tradable spread" if is_coint else "; ".join(reasons)
    return CointegrationReport(
        pvalue=p,
        hedge_ratio=beta,
        intercept=alpha,
        zscore=z,
        half_life=hl,
        is_cointegrated=is_coint,
        reason=reason,
    )


def validate_oos(
    y: Iterable[float],
    x: Iterable[float],
    train_frac: float = 0.7,
    oos_pvalue_max: float = 0.15,
    min_test: int = 40,
) -> dict:
    """Out-of-sample cointegration check — the guard against overfit/spurious pairs.

    A pair that's only cointegrated in-sample (because we tested many candidates
    and this one looked good by chance) will FAIL here. We:

      1. fit the hedge ratio β/α on the TRAIN split only,
      2. form the spread on the held-out TEST split with those *train* β/α
         (no re-fit — that's the whole point: does the learned relationship still
         hold on data it never saw?),
      3. ADF-test that test-window spread for stationarity.

    The OOS p-value threshold is intentionally looser than the in-sample EG gate
    (default 0.15 vs 0.05): the test window is shorter, so the ADF test has less
    power — we still demand evidence of stationarity, just don't punish the shorter
    sample (empirically 0.15 cleanly separates planted pairs from spurious random
    walks). Returns a dict; ``passed`` is None when there isn't enough data to
    judge (caller decides — the scanner fails OPEN in that case)."""
    y_arr = np.asarray(list(y), dtype=float)
    x_arr = np.asarray(list(x), dtype=float)
    mask = np.isfinite(y_arr) & np.isfinite(x_arr)
    y_arr = y_arr[mask]
    x_arr = x_arr[mask]
    n = len(y_arr)
    n_train = int(n * train_frac)
    n_test = n - n_train
    if n_train < 60 or n_test < min_test:
        return {"passed": None, "oos_pvalue": None, "oos_half_life": None,
                "train_n": n_train, "test_n": n_test,
                "reason": f"insufficient data for OOS (train={n_train}, test={n_test})"}

    y_tr, x_tr = y_arr[:n_train], x_arr[:n_train]
    y_te, x_te = y_arr[n_train:], x_arr[n_train:]
    beta, alpha = hedge_ratio(y_tr, x_tr)
    if beta == 0.0:
        return {"passed": False, "oos_pvalue": 1.0, "oos_half_life": None,
                "train_n": n_train, "test_n": n_test,
                "reason": "train hedge ratio undefined"}

    test_spread = y_te - beta * x_te - alpha
    p_oos = adf_pvalue(test_spread)
    hl_oos = half_life_ou(test_spread)
    passed = p_oos <= oos_pvalue_max
    reason = ("OOS spread stationary" if passed
              else f"OOS spread not stationary (ADF p={p_oos:.3f} > {oos_pvalue_max})")
    return {"passed": bool(passed), "oos_pvalue": float(p_oos),
            "oos_half_life": float(hl_oos) if np.isfinite(hl_oos) else None,
            "train_n": n_train, "test_n": n_test, "reason": reason}


__all__ = [
    "CointegrationReport",
    "hedge_ratio",
    "spread_series",
    "engle_granger_pvalue",
    "adf_pvalue",
    "spread_zscore",
    "cointegration_report",
    "validate_oos",
]
