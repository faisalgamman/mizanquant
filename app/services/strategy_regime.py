"""Per-asset strategy-regime classifier (Chan Ch.6).

The macro regime detector in `app/services/regime.py` answers "is the
*overall market* bullish, neutral, or bearish?" using SPY trend, VIX
percentile, and yield spread. That's necessary but not sufficient.

Chan §6.2 makes a sharper point: regime should also be classified
*per asset* and used to *route* trades. A given stock can be:

- **trending** — high Hurst, high ADX, ADF cannot reject unit root.
  Momentum strategies fit. Mean-reversion strategies will lose.
- **ranging** — low Hurst, low ADF p (spread is stationary), short OU
  half-life. Mean-reversion strategies fit. Momentum will lose.
- **noisy** — neither structure is strong enough to bet on. Skip both.

This module exposes `classify_strategy_regime(closes)` that returns one
of those three labels plus the underlying statistics. It reuses the
estimators built in Phase 3 (stationarity) and Phase 4 (momentum
quality), so the same numbers drive every gate in the system.

The classifier is intentionally conservative: ambiguous evidence falls
through to "noisy" rather than guessing. False classifications cost
trades; abstentions only cost opportunity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from app.services.momentum_quality import momentum_quality_score
from app.services.stationarity import stationarity_report


@dataclass
class StrategyRegime:
    label: str  # "trending" | "ranging" | "noisy"
    hurst: float
    adf_pvalue: float
    half_life: float
    tstat: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "hurst": round(self.hurst, 4),
            "adf_pvalue": round(self.adf_pvalue, 4),
            "half_life_bars": round(self.half_life, 2),
            "tstat": round(self.tstat, 3),
            "reason": self.reason,
        }


def classify_strategy_regime(
    closes: Iterable[float],
    trending_hurst: float = 0.55,
    ranging_hurst: float = 0.45,
    ranging_adf_p: float = 0.10,
    ranging_max_half_life: float = 30.0,
    min_tstat_for_trend: float = 1.0,
) -> StrategyRegime:
    """Classify per-asset regime as trending / ranging / noisy.

    Decision tree:
      Hurst ≥ trending_hurst AND |t| ≥ min_tstat_for_trend  → "trending"
      Hurst ≤ ranging_hurst AND ADF p ≤ ranging_adf_p
                              AND half-life ≤ ranging_max  → "ranging"
      otherwise                                            → "noisy"

    Each branch demands two independent confirmations to avoid pinning
    the entire decision on one noisy estimator.
    """
    arr = np.asarray(list(closes), dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 60:
        return StrategyRegime(
            label="noisy",
            hurst=0.5,
            adf_pvalue=1.0,
            half_life=float("inf"),
            tstat=0.0,
            reason="insufficient history",
        )

    stat_rep = stationarity_report(arr, max_half_life_bars=ranging_max_half_life)
    mom_rep = momentum_quality_score(arr)

    h = stat_rep.hurst
    p = stat_rep.adf_pvalue
    hl = stat_rep.half_life
    t = mom_rep.tstat

    if h >= trending_hurst and abs(t) >= min_tstat_for_trend:
        return StrategyRegime(
            label="trending",
            hurst=h,
            adf_pvalue=p,
            half_life=hl,
            tstat=t,
            reason=f"trend persistence (H={h:.2f}, |t|={abs(t):.2f})",
        )

    if (
        h <= ranging_hurst
        and p <= ranging_adf_p
        and np.isfinite(hl)
        and hl <= ranging_max_half_life
    ):
        return StrategyRegime(
            label="ranging",
            hurst=h,
            adf_pvalue=p,
            half_life=hl,
            tstat=t,
            reason=f"stationary (H={h:.2f}, ADF p={p:.2f}, t1/2={hl:.1f})",
        )

    return StrategyRegime(
        label="noisy",
        hurst=h,
        adf_pvalue=p,
        half_life=hl,
        tstat=t,
        reason=(
            f"ambiguous: H={h:.2f}, ADF p={p:.2f}, t1/2={hl:.1f}, "
            f"|t|={abs(t):.2f}"
        ),
    )


__all__ = ["StrategyRegime", "classify_strategy_regime"]
