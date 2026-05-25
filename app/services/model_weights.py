"""Model / signal-source weighting from the live track record (Phase 2).

Closes the feedback loop that was previously missing: `signal_tracker.get_accuracy_report`
already computes per-source win rate / profit factor, but NOTHING consumed it. This
module turns that report into:

  1. `get_source_weights()` — a {source: weight} map the Conviction Engine uses to
     gently up/down-weight confirmation layers by their TRAILING accuracy.
  2. `get_overall_health()` — the OVERALL win-rate / profit-factor / sample used by
     the adaptive-gates governor (Phase 5).

Safety properties (small noisy samples must not whipsaw real trading):
  * Sources with < `min_sample` evaluated signals get NEUTRAL weight (1.0).
  * Weights are clamped to [_W_MIN, _W_MAX] = [0.80, 1.20] — the same band the
    engine clamps acc_factor to, so a single bad month can only nudge, not dominate.
  * Results are cached briefly; this is read-only and never mutates scores itself.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("screener")

# Weight band — matches conviction_engine acc_factor clamp.
_W_MIN, _W_MAX = 0.80, 1.20
# Win rate that maps to neutral weight 1.0. Above → reward, below → penalize.
_WR_NEUTRAL = 55.0
# Sensitivity: how many win-rate points map to one unit of weight.
_WR_SPAN = 50.0

# Map signal_tracker signal_type/source labels → conviction confirmation layers.
# Unknown sources are keyed by their lowercased name (still usable downstream).
_SOURCE_TO_LAYER = {
    "swing": "technical", "momentum": "technical", "breakout": "technical",
    "usx_pro": "technical", "usx pro": "technical",
    "fundamental": "fundamental",
    "forecast": "ml_real", "consensus": "consensus", "ensemble": "ml_real",
    "rotation": "sector", "sector": "sector",
}

_cache: dict = {"weights": None, "health": None, "ts": 0.0}
_CACHE_TTL = 900  # 15 min


def _clamp(x: float, lo: float = _W_MIN, hi: float = _W_MAX) -> float:
    return max(lo, min(hi, x))


def _weight_from_win_rate(win_rate: float) -> float:
    return _clamp(1.0 + (win_rate - _WR_NEUTRAL) / _WR_SPAN)


def get_source_weights(period_days: int = 30, min_sample: int = 10,
                       use_cache: bool = True) -> dict:
    """Return {confirmation_layer: weight} from the trailing accuracy report.

    Layers with no qualifying source simply won't appear (caller treats a
    missing layer as neutral 1.0).
    """
    if use_cache and _cache["weights"] is not None and time.time() - _cache["ts"] < _CACHE_TTL:
        return _cache["weights"]

    weights: dict[str, float] = {}
    try:
        from app.services.signal_tracker import get_accuracy_report
        rows = get_accuracy_report(period_days=period_days)
        # Collapse multiple sources that map to the same layer by averaging.
        agg: dict[str, list[float]] = {}
        for row in rows or []:
            src = (row.get("Source") or "").strip()
            if not src or src.upper() == "OVERALL":
                continue
            total = row.get("Total Signals", 0) or 0
            if total < min_sample:
                continue
            wr = row.get("Win Rate %")
            if wr is None:
                continue
            layer = _SOURCE_TO_LAYER.get(src.lower(), src.lower())
            agg.setdefault(layer, []).append(_weight_from_win_rate(float(wr)))
        for layer, ws in agg.items():
            weights[layer] = round(sum(ws) / len(ws), 3)
    except Exception as exc:
        logger.debug("get_source_weights failed: %s", exc)

    _cache["weights"] = weights
    _cache["ts"] = time.time()
    return weights


def get_overall_health(period_days: int = 30) -> dict:
    """Return {win_rate_pct, profit_factor, avg_return_pct, sample} from OVERALL.

    Used by the adaptive-gates governor. Returns sample=0 when no matured signals,
    which the governor treats as "insufficient data — do not adjust".
    """
    try:
        from app.services.signal_tracker import get_accuracy_report
        rows = get_accuracy_report(period_days=period_days)
        for row in rows or []:
            if (row.get("Source") or "").upper() == "OVERALL":
                return {
                    "win_rate_pct": float(row.get("Win Rate %", 0) or 0),
                    "profit_factor": float(row.get("Profit Factor", 0) or 0),
                    "avg_return_pct": float(row.get("Avg Return %", 0) or 0),
                    "sample": int(row.get("Total Signals", 0) or 0),
                }
    except Exception as exc:
        logger.debug("get_overall_health failed: %s", exc)
    return {"win_rate_pct": 0.0, "profit_factor": 0.0, "avg_return_pct": 0.0, "sample": 0}


# ── Adaptive-gates governor (Phase 5) ───────────────────────────────────────────
# Tightens the screener's min/strong gates when the trailing track record is weak,
# with hysteresis + a minimum sample so a small noisy month can't whipsaw gates.
_gov_state = {"tightened": False}
_GOV_WR_FLOOR = 55.0     # tighten once 30d win rate drops below this
_GOV_WR_RELEASE = 60.0   # release only after it recovers above this (hysteresis band)
_GOV_MIN_SAMPLE = 20     # need this many matured signals before trusting the read
_GOV_DELTA = 5           # gate points added while tightened


def adaptive_gate_delta(period_days: int = 30) -> tuple[int, dict]:
    """Return (delta, diag): gate points to ADD to min/strong gates.

    Hysteresis: enter the tightened state when win rate < floor; leave it only
    when win rate recovers above the higher release band. Below the minimum
    sample we never tighten (insufficient evidence). The caller decides whether
    to apply this at all (gated by ADAPTIVE_GATES_LIVE).
    """
    health = get_overall_health(period_days=period_days)
    wr, sample = health["win_rate_pct"], health["sample"]
    if sample < _GOV_MIN_SAMPLE:
        return 0, {"applied": 0, "reason": "insufficient_sample",
                   "win_rate_pct": wr, "sample": sample, "tightened": _gov_state["tightened"]}
    if _gov_state["tightened"]:
        if wr >= _GOV_WR_RELEASE:
            _gov_state["tightened"] = False
    elif wr < _GOV_WR_FLOOR:
        _gov_state["tightened"] = True
    delta = _GOV_DELTA if _gov_state["tightened"] else 0
    return delta, {"applied": delta, "tightened": _gov_state["tightened"],
                   "win_rate_pct": wr, "sample": sample}


__all__ = ["get_source_weights", "get_overall_health", "adaptive_gate_delta"]
