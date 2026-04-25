"""Momentum quality gate (Chan Ch.5).

Time-series momentum is profitable on assets whose past returns
*statistically* predict future returns. The classic failure mode is
trading short-term ROC on a series that drifted up by chance — the
t-statistic of the mean return is small, the trend is not real, and
the trade is noise.

This module provides three checks that together separate real momentum
from spurious drift:

1. **t-statistic of mean returns** — `t = μ / (σ/√n)`. Chan §5.1: a
   significant trend has |t| ≥ 1.5–2.0. Below 1.5 the apparent move is
   indistinguishable from noise on the available sample.
2. **12-1 momentum** (Jegadeesh & Titman, refined Chan §5.2) — past 12
   months of return *excluding* the most recent ~21 trading days. The
   recent month tends to mean-revert and contaminates pure momentum
   signal. We scale to whatever lookback is available.
3. **Hurst exponent ≥ 0.55** (reusing Phase 3 R/S) — confirms positive
   autocorrelation in returns, the structural fingerprint of trend.

The combined `momentum_quality_score` returns a verdict: trending,
neutral, or mean-reverting. Mean-reverting verdict on a momentum
strategy is a hard signal to skip the trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from app.services.stationarity import hurst_exponent


@dataclass
class MomentumReport:
    tstat: float
    mom_12_1: float
    hurst: float
    verdict: str  # "trending" | "neutral" | "mean_reverting"
    reason: str

    def as_dict(self) -> dict:
        return {
            "tstat": round(self.tstat, 3),
            "mom_12_1_pct": round(self.mom_12_1 * 100, 2),
            "hurst": round(self.hurst, 4),
            "verdict": self.verdict,
            "reason": self.reason,
        }


def tstat_returns(returns: Iterable[float]) -> float:
    """t-statistic of the mean return. Returns 0 on degenerate input
    rather than raising; callers interpret 0 as "no signal"."""
    arr = np.asarray(list(returns), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 5:
        return 0.0
    mu = float(arr.mean())
    sd = float(arr.std(ddof=1))
    if sd <= 0:
        return 0.0
    se = sd / np.sqrt(n)
    return float(mu / se)


def momentum_12_1(
    closes: Iterable[float],
    lookback: int = 252,
    skip: int = 21,
) -> float:
    """Past return over `lookback` bars excluding the most recent
    `skip` bars. With 252 trading days and skip=21 this is the textbook
    12-1 month signal. Falls back proportionally on shorter histories;
    returns 0.0 if there is not enough data to compute either window.
    """
    arr = np.asarray(list(closes), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < skip + 5:
        return 0.0
    effective_lookback = min(lookback, n - 1)
    if effective_lookback <= skip:
        return 0.0
    start = arr[-effective_lookback]
    end = arr[-skip - 1]
    if start <= 0:
        return 0.0
    return float(end / start - 1.0)


def momentum_quality_score(
    closes: Iterable[float],
    tstat_threshold: float = 1.5,
    mom_threshold: float = 0.0,
    hurst_threshold: float = 0.55,
) -> MomentumReport:
    """Combined momentum-quality gate.

    Verdicts:
    - "trending"        — all three checks agree on positive momentum
    - "mean_reverting"  — Hurst < 0.45 (anti-momentum signal)
    - "neutral"         — anything else

    Only "trending" should permit a momentum BUY. "mean_reverting"
    should oppose it; "neutral" is the absence of evidence either way.
    """
    arr = np.asarray(list(closes), dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 30:
        return MomentumReport(
            tstat=0.0,
            mom_12_1=0.0,
            hurst=0.5,
            verdict="neutral",
            reason="insufficient history",
        )

    rets = np.diff(np.log(arr))
    t = tstat_returns(rets)
    mom = momentum_12_1(arr)
    h = hurst_exponent(arr)

    if h < 0.45:
        return MomentumReport(
            tstat=t,
            mom_12_1=mom,
            hurst=h,
            verdict="mean_reverting",
            reason=f"Hurst={h:.3f}<0.45 — series mean-reverts, momentum signal anti-correlated",
        )

    failures: list[str] = []
    if t < tstat_threshold:
        failures.append(f"t={t:.2f}<{tstat_threshold}")
    if mom <= mom_threshold:
        failures.append(f"mom_12_1={mom*100:.1f}%<={mom_threshold*100:.1f}%")
    if h < hurst_threshold:
        failures.append(f"Hurst={h:.3f}<{hurst_threshold}")

    if not failures:
        return MomentumReport(
            tstat=t,
            mom_12_1=mom,
            hurst=h,
            verdict="trending",
            reason=f"all gates passed (t={t:.2f}, mom={mom*100:.1f}%, H={h:.3f})",
        )
    return MomentumReport(
        tstat=t,
        mom_12_1=mom,
        hurst=h,
        verdict="neutral",
        reason="; ".join(failures),
    )


__all__ = [
    "MomentumReport",
    "tstat_returns",
    "momentum_12_1",
    "momentum_quality_score",
]
