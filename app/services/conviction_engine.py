"""Conviction Engine — the shared, side-effect-free core of stock selection.

This module is deliberately PURE: every function takes plain dicts / numbers and
returns plain dicts. It performs NO I/O (no DB, no HTTP, no market_data fetch).
That is what lets the validation harness and the live screener call the EXACT
same scoring code — guaranteeing that what we backtest is what we trade.

What it produces for a screener row (deep-picks `_enrich_one`):
  - context_adjusted_score : composite re-weighted by market regime + sector
                             rotation (reuses _QUADRANT_BOOST / _MACRO_FACTOR).
  - confirmations / count  : which independent layers agree (technical,
                             fundamental, real-ML, sector tailwind, consensus).
  - conviction_score       : 0-100 calibrated "how sure are we" — composite
                             scaled by confirmation breadth and (optional)
                             trailing model accuracy.
  - rank_value             : risk-adjusted rank = conviction × upside / ATR.

NONE of these mutate the live `swing_score` / `composite_score`. The caller
decides whether to RANK by them, gated behind the SELECTION_CONDITIONING_LIVE /
CONVICTION_SIZING_LIVE flags. Hard gates remain a floor the engine never bypasses.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("screener")

# Reuse the exact rotation/macro multipliers the shadow conditioning already uses,
# so live ranking and the shadow `context_adjusted_score` never diverge.
try:
    from app.services.market_context_bundle import (
        _QUADRANT_BOOST, _MACRO_FACTOR, _sector_to_rank_map,
    )
except Exception:  # pragma: no cover — defensive fallback if import graph changes
    _QUADRANT_BOOST = {"leading": 1.10, "improving": 1.05, "weakening": 0.97,
                       "lagging": 0.92, "unknown": 1.0}
    _MACRO_FACTOR = {"risk_on": 1.05, "neutral": 1.0, "risk_off": 0.90}

    def _sector_to_rank_map(bundle: dict) -> dict:
        out: dict = {}
        for _etf, info in (bundle.get("sector_ranks") or {}).items():
            name = (info.get("sector") or "").strip().lower()
            if name:
                out[name] = info
        return out


# ── Sector-name normalizer ───────────────────────────────────────────────────────
# FMP and other data vendors use different taxonomy than the SPDR ETF labels used
# by pit_bundle / market_context_bundle.  Without normalisation, stocks in
# "Healthcare", "Financial Services", "Consumer Cyclical" etc. all fall through to
# "unknown" (no sector boost applied) even though XLV / XLF / XLY cover them.
#
# Tested against the halal universe: these five mismatches account for ~55-60 % of
# the "unknown" bucket — fixing them brings unknown_pct below 25 %.
_SECTOR_ALIASES: dict[str, str] = {
    "healthcare":            "health care",           # FMP → SPDR XLV
    "financial services":    "financials",            # FMP → SPDR XLF
    "consumer cyclical":     "consumer discretionary",# FMP → SPDR XLY
    "basic materials":       "materials",             # FMP → SPDR XLB
    "consumer defensive":    "consumer staples",      # FMP → SPDR XLP
    # Less common but still seen in halal screeners
    "information technology":"technology",            # some indices use this
    "telecom services":      "communication services",
    "communication":         "communication services",
}


# ═══════════════════════════════════════════════════════════════════════
# SCORING SYSTEM BOUNDARIES (A7 — Architectural Decision 2026-06-05)
# ═══════════════════════════════════════════════════════════════════════
# TWO independent scoring systems serve different domains:
#
#   swing_score (signals_advisor, scheduler, pipeline_orchestrator):
#     → Fast per-symbol qualifier for 7x daily signal slots.
#     → Outputs: BUY / STRONG BUY with price + stop + target.
#     → Domain: immediate trading decisions (10:30-16:30 ET).
#
#   composite_score / deep-picks (workspace_server, conviction_engine):
#     → Multi-factor deep analysis via CompositeBridge (tech + fund + consensus).
#     → Outputs: scored picks with conviction, context, archetype enrichments.
#     → Domain: research-quality ranking, NOT a parallel signal source.
#
# They may produce DIFFERENT top-pick lists. This is EXPECTED — they
# score different things at different cadences for different consumers.
# DO NOT merge them. Keep explicit domain boundaries.
# ═══════════════════════════════════════════════════════════════════════

def _normalize_sector(sector: str) -> str:
    """Map vendor sector names to the SPDR ETF taxonomy before quadrant lookup.

    Case-insensitive.  Returns the canonical lowercase SPDR name if an alias
    exists, otherwise the original string lowercased.
    """
    s = (sector or "").strip().lower()
    return _SECTOR_ALIASES.get(s, s)


# ── Pre-registered configuration (honest n_trials accounting) ───────────────────
# These thresholds/coefficients are design choices. For the Deflated Sharpe Ratio
# in the validation harness, n_trials must reflect HOW MANY variants were
# considered across the project — NOT 1. We pre-register a single ENHANCED config
# and report this honest trial count so DSR is not inflated.
N_TRIALS_REGISTERED = 12

# Multi-confirmation: STRONG BUY requires composite >= STRONG_BUY_COMPOSITE AND
# at least STRONG_BUY_MIN_CONFIRMATIONS independent layers agreeing.
STRONG_BUY_COMPOSITE = 72
STRONG_BUY_MIN_CONFIRMATIONS = 3

# Confirmation thresholds (each is one independent vote).
_TECH_STRONG = 20        # score_tech (0-30) — technically strong
_MOM_STRONG = 20         # momentum_score (0-30) fallback
_FUND_GRADES = ("A", "B")
_TAILWIND_QUADRANTS = ("leading", "improving")

# Conviction calibration: at 0 confirmations conviction = 0.70·composite; each
# confirmation adds 0.06·composite up to ~1.0·composite at 5 confirmations. This
# deliberately DISCOUNTS unconfirmed high composites (the false-positive risk).
_CONV_BASE = 0.70
_CONV_PER_CONFIRM = 0.06

# Risk-adjusted ranking: rank_value = (conviction/100) · upside% / (ATR% · k).
_ATR_K = 1.0
_ATR_FLOOR = 0.5         # never divide by a near-zero ATR
_UPSIDE_FLOOR = 0.1      # minimum positive upside proxy

# Cap on the context multiplier so swing×boost×macro can't compound unboundedly.
_CONTEXT_MULT_MIN, _CONTEXT_MULT_MAX = 0.80, 1.20


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def context_multiplier(sector: Optional[str], bundle: dict) -> tuple[float, str, Optional[int]]:
    """Return (multiplier, quadrant, sector_rank) for a stock's sector.

    multiplier = sector_boost(quadrant) × macro_factor(risk_posture), clamped.

    Sector names are normalised through _normalize_sector() before lookup so
    FMP taxonomy ("Healthcare", "Consumer Cyclical" …) maps correctly to the
    SPDR ETF labels stored in the bundle ("Health Care", "Consumer Discretionary" …).
    """
    posture = (bundle or {}).get("risk_posture", "neutral")
    macro = _MACRO_FACTOR.get(posture, 1.0)
    sect_map = _sector_to_rank_map(bundle or {})
    info = sect_map.get(_normalize_sector(sector or ""))
    quad = info["quadrant"] if info else "unknown"
    boost = _QUADRANT_BOOST.get(quad, 1.0)
    mult = _clamp(boost * macro, _CONTEXT_MULT_MIN, _CONTEXT_MULT_MAX)
    rank = info.get("rank") if info else None
    return mult, quad, rank


def detect_confirmations(row: dict, quadrant: str) -> list[str]:
    """Return the list of independent layers that confirm a BUY for this row.

    Layers (each at most one vote):
      technical    — score_tech strong, or momentum_score strong as fallback
      fundamental  — fundamental grade A/B
      ml_real      — REAL ML forecast (not the RSI proxy) points up
      consensus    — per-symbol consensus majority buy (only when present)

    NOTE — the sector-rotation tailwind ("leading"/"improving" RRG quadrant) was
    REMOVED as a confirmation layer.  The validation harness proved it is
    ANTI-predictive for this halal universe: ranking by sector-conditioned score
    lost ~1% per trade at both 5- and 20-day horizons (delta -1.00% / -0.93%,
    ENHANCED DSR collapsed 0.97→0.15).  A "leading" sector is momentum-extended
    and mean-reverts, so counting it as a positive vote made the STRONG BUY gate
    too permissive.  The gate now relies on the four genuinely predictive,
    independent layers below (requires ≥3 of 4 → a stricter, cleaner bar).
    `quadrant` is still accepted for signature stability and informational output.
    """
    confirms: list[str] = []

    tech = row.get("score_tech")
    if tech is None:
        tech = row.get("momentum_score", 0)
    if (tech or 0) >= _TECH_STRONG or (row.get("momentum_score", 0) >= _MOM_STRONG):
        confirms.append("technical")

    if (row.get("f_grade") in _FUND_GRADES):
        confirms.append("fundamental")

    # Real ML forecast: only top-N candidates get true model runs; their
    # forecast_details carries model_direction + models. The RSI proxy does not.
    fd = row.get("forecast_details") or {}
    if fd.get("models") and fd.get("model_direction") == "up":
        confirms.append("ml_real")

    # (sector tailwind removed — see docstring: anti-predictive in backtest)

    # Consensus is optional — counted only when the row already carries it
    # (e.g. attached upstream). Never fetched here (keeps the engine pure).
    votes_buy = row.get("consensus_votes_buy")
    votes_sell = row.get("consensus_votes_sell")
    verdict = (row.get("consensus_verdict") or "").upper()
    if verdict in ("BUY", "STRONG BUY") or (
        votes_buy is not None and votes_sell is not None and votes_buy > votes_sell
    ):
        confirms.append("consensus")

    return confirms


def compute_conviction(
    row: dict,
    bundle: dict | None = None,
    accuracy_weights: dict | None = None,
) -> dict:
    """Compute the conviction layer for a single screener row (pure function).

    Args:
        row: a screener/deep-picks row. Reads composite_score, score_tech,
             momentum_score, f_grade, forecast_details, sector, atr_pct,
             analyst_upside, is_halal (all optional — degrades gracefully).
        bundle: MarketContextBundle (risk_posture + sector_ranks). For the
             backtest this MUST be the point-in-time bundle for the as_of date.
        accuracy_weights: optional {source: weight} from model_weights; scales
             conviction by the trailing accuracy of contributing layers.

    Returns a dict of additive fields (never mutates `row`).
    """
    bundle = bundle or {}
    composite = float(row.get("composite_score") or 0)
    sector = row.get("sector")

    mult, quadrant, sector_rank = context_multiplier(sector, bundle)
    context_adjusted = round(composite * mult, 1)

    confirms = detect_confirmations(row, quadrant)
    n_confirm = len(confirms)

    # Accuracy factor: average weight of confirming sources, clamped near 1.0 so
    # a noisy 30d sample can only gently tilt conviction (Phase-2 feedback loop).
    acc_factor = 1.0
    if accuracy_weights:
        ws = [accuracy_weights.get(c) for c in confirms if accuracy_weights.get(c) is not None]
        if ws:
            acc_factor = _clamp(sum(ws) / len(ws), 0.80, 1.20)

    conviction = composite * (_CONV_BASE + _CONV_PER_CONFIRM * n_confirm) * acc_factor
    conviction_score = round(_clamp(conviction, 0.0, 100.0), 1)

    # Risk-adjusted rank. Upside: prefer analyst upside when real & positive,
    # else a conservative proxy from composite. Downside: ATR%.
    upside = row.get("analyst_upside")
    if not (isinstance(upside, (int, float)) and upside > 0):
        upside = (composite - 50.0) / 5.0   # composite 70 → 4% proxy
    upside = max(float(upside), _UPSIDE_FLOOR)
    atr_pct = row.get("atr_pct")
    atr_pct = float(atr_pct) if isinstance(atr_pct, (int, float)) and atr_pct > 0 else 3.0
    rank_value = round((conviction_score / 100.0) * upside / max(atr_pct * _ATR_K, _ATR_FLOOR), 4)

    # Multi-confirmation STRONG BUY gate (false-positive reduction).
    is_halal = bool(row.get("is_halal"))
    strong_ok = (
        composite >= STRONG_BUY_COMPOSITE
        and is_halal
        and n_confirm >= STRONG_BUY_MIN_CONFIRMATIONS
    )

    return {
        "context_adjusted_score": context_adjusted,
        "context_multiplier": round(mult, 3),
        "rotation_quadrant": quadrant,
        "sector_rank": sector_rank,
        "risk_posture": bundle.get("risk_posture", "neutral"),
        "confirmations": confirms,
        "confirmation_count": n_confirm,
        "conviction_score": conviction_score,
        "rank_value": rank_value,
        "strong_buy_qualified": strong_ok,
    }


def apply_strong_buy_gate(signal_composite: str, conv: dict) -> str:
    """Demote STRONG BUY → BUY when multi-confirmation is not satisfied.

    Only ever DOWNGRADES; never upgrades. Leaves BUY/WATCH/AVOID untouched.
    """
    if signal_composite == "STRONG BUY" and not conv.get("strong_buy_qualified", False):
        return "BUY"
    return signal_composite


def suggested_position_size(
    conviction_score: float,
    static_risk_pct: float,
    pnl_history: Optional[list] = None,
) -> dict:
    """Risk fraction suggestion for a candidate.

    With enough realised trade returns (pnl_history) we use continuous Kelly
    (kelly.py) as a CEILING-respecting estimate; otherwise we scale the static
    risk budget by conviction. Kelly can only ever reduce size, never expand it.
    """
    base = static_risk_pct * _clamp(conviction_score / 100.0, 0.0, 1.0)
    if pnl_history:
        try:
            from app.services.kelly import kelly_from_db_pnls
            eff, diag = kelly_from_db_pnls(pnl_history, static_risk_pct=static_risk_pct)
            # Blend: take the more conservative of Kelly and conviction-scaled.
            size = round(min(eff, max(base, 0.0)) if diag.get("kelly_used") else base, 4)
            return {"suggested_risk_pct": size, "kelly": diag}
        except Exception as exc:
            logger.debug("kelly sizing failed: %s", exc)
    return {"suggested_risk_pct": round(base, 4), "kelly": {"kelly_used": False}}


__all__ = [
    "compute_conviction", "apply_strong_buy_gate", "suggested_position_size",
    "context_multiplier", "detect_confirmations", "_normalize_sector",
    "N_TRIALS_REGISTERED", "STRONG_BUY_COMPOSITE", "STRONG_BUY_MIN_CONFIRMATIONS",
]
