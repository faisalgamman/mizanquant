"""MarketContextBundle — the reactive conditioning state shared across the platform.

This is the SPINE of the unified ecosystem. It composes the regime engine, live
FRED macro indicators, and sector rotation (RRG) into ONE versioned snapshot that
every layer reads:

    screener     → rotation sector boost + macro risk-off filter (shadow)
    risk desk    → regime-scaled position sizing (shadow)
    forecasting  → posture-conditioned predictions (shadow)
    MizanAI      → a single tool returns the whole reactive state
    dashboard    → ecosystem status strip (regime · posture · top sector · VIX)

A change in market regime/posture therefore cascades into all consumers.

SHADOW MODE: consumers read the bundle but only ACT on it (alter live order
sizing / selection) when ``settings.CONTEXT_CONDITIONING_LIVE`` is True. Until then
the conditioning is computed and logged for validation only.

Caching:
  - async path (HTTP endpoint)  → Redis key "context:bundle", TTL 900s
  - sync path (screener/risk in worker threads) → module-level TTL memo

A RegimeLog row is written whenever ``risk_posture`` flips, and the bundle
``version`` integer is bumped so dependent caches can invalidate reactively.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("screener")

# ── Sector ETFs used for the rotation map (SPDR sectors) ────────────────────────
_SECTOR_ETFS: dict[str, str] = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLP":  "Consumer Staples",
    "XLY":  "Consumer Discretionary",
    "XLU":  "Utilities",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLC":  "Communication Services",
}

# RRG quadrant → desirability score (leading is best). Used to rank sectors.
_QUADRANT_SCORE = {"leading": 4, "improving": 3, "weakening": 2, "lagging": 1, "unknown": 0}

# ── Version / posture-flip state (module-level; DB RegimeLog is source of truth) ─
_lock = threading.Lock()
_state: dict = {"version": 1, "posture": None}

# Sync memo for thread-based callers so the screener doesn't recompute per symbol.
_sync_cache: dict = {"data": None, "ts": 0.0}
_SYNC_TTL = 900  # 15 min, matches Redis TTL


# ── Posture derivation ──────────────────────────────────────────────────────────

def _derive_posture(
    macro: dict,
    vix: Optional[float],
    vix_pctile: Optional[float],
    regime: str,
) -> tuple[str, int, list[str]]:
    """Classify market into risk_on / neutral / risk_off from macro + VIX + regime.

    Returns (posture, risk_score, reasons). risk_score is the count of risk-off
    signals fired — surfaced for transparency in the bundle.

    Rule (graded, conservative):
      - risk_off  if >=2 risk-off signals, OR regime==BEAR, OR VIX>30
      - risk_on   if 0 signals and regime==BULL
      - neutral   otherwise
    Risk-off signals: inverted yield curve (t10y2y<0), HY spread>5%, VIX>27,
    VIX percentile>80.
    """
    reasons: list[str] = []
    signals = 0

    t10y2y = macro.get("t10y2y")
    hy = macro.get("hy_spread")
    if t10y2y is not None and t10y2y < 0:
        signals += 1
        reasons.append("yield curve inverted (10Y-2Y < 0)")
    if hy is not None and hy > 5.0:
        signals += 1
        reasons.append(f"HY credit spread {hy:.1f}% > 5%")
    if vix is not None and vix > 27:
        signals += 1
        reasons.append(f"VIX {vix:.0f} > 27")
    if vix_pctile is not None and vix_pctile > 80:
        signals += 1
        reasons.append(f"VIX percentile {vix_pctile:.0f} > 80")

    if signals >= 2 or regime == "BEAR" or (vix is not None and vix > 30):
        posture = "risk_off"
    elif signals == 0 and regime == "BULL":
        posture = "risk_on"
    else:
        posture = "neutral"

    if regime == "BEAR" and "regime BEAR" not in reasons:
        reasons.append("regime BEAR")
    return posture, signals, reasons


def _sector_ranks(rotation: list[dict]) -> dict:
    """Rank sectors from rotation data. Returns {ETF: {sector, quadrant, rank, ...}}.

    Rank 1 = strongest (leading + highest momentum). Used by the screener to boost
    stocks in sectors that are rotating up.
    """
    if not rotation:
        return {}
    scored = []
    for r in rotation:
        sym = r.get("symbol", "")
        quad = r.get("quadrant", "unknown")
        scored.append((
            sym,
            _QUADRANT_SCORE.get(quad, 0),
            r.get("rs_momentum") or 0.0,
            quad,
            r.get("rs_ratio"),
            r.get("rs_momentum"),
        ))
    # Highest quadrant score, then highest momentum first
    scored.sort(key=lambda x: (x[1], x[2]), reverse=True)
    ranks: dict = {}
    for i, (sym, _qs, _mom, quad, rs_ratio, rs_mom) in enumerate(scored, start=1):
        ranks[sym] = {
            "sector":      _SECTOR_ETFS.get(sym, sym),
            "quadrant":    quad,
            "rank":        i,
            "rs_ratio":    rs_ratio,
            "rs_momentum": rs_mom,
        }
    return ranks


# ── Composition ─────────────────────────────────────────────────────────────────

def compute_context_bundle() -> dict:
    """Compose the full reactive context from existing services (synchronous).

    Degrades gracefully: any failing sub-source is omitted / None, never raises.
    Writes a RegimeLog row + bumps version when risk_posture flips.
    """
    now = datetime.now(timezone.utc)

    # 1) Regime + VIX + gates (this already enriches with FRED macro internally)
    regime = "NEUTRAL"
    vix = vix_pctile = None
    status = None
    min_gate = strong_gate = None
    halt = False
    try:
        from app.services.market_context import get_market_status
        ms = get_market_status()
        regime = ms.get("regime", "NEUTRAL")
        vix = ms.get("vix")
        vix_pctile = ms.get("vix_pctile")
        status = ms.get("status")
        min_gate = ms.get("min_gate")
        strong_gate = ms.get("strong_gate")
        halt = bool(ms.get("halt_pipeline", False))
    except Exception as exc:
        logger.warning("context bundle: market_status failed: %s", exc)

    # 2) Canonical FRED macro dict
    macro: dict = {"t10y2y": None, "hy_spread": None, "fed_rate": None,
                   "cpi_yoy": None, "unemployment": None}
    try:
        from app.services.openbb_data import get_economic_indicators
        macro = get_economic_indicators() or macro
    except Exception as exc:
        logger.debug("context bundle: macro failed: %s", exc)

    # 3) Sector rotation (RRG)
    rotation: list[dict] = []
    try:
        from app.services.openbb_data import get_relative_rotation
        rotation = get_relative_rotation(list(_SECTOR_ETFS.keys()), benchmark="SPY") or []
    except Exception as exc:
        logger.debug("context bundle: rotation failed: %s", exc)

    ranks = _sector_ranks(rotation)
    top_sectors = [
        {"symbol": s, "sector": v["sector"], "quadrant": v["quadrant"]}
        for s, v in sorted(ranks.items(), key=lambda kv: kv[1]["rank"])[:3]
    ]

    # 4) Derive posture
    posture, risk_score, reasons = _derive_posture(macro, vix, vix_pctile, regime)

    # 5) Version / flip detection
    with _lock:
        prev = _state["posture"]
        if prev is not None and posture != prev:
            _state["version"] += 1
            _log_posture_flip(prev, posture, regime, vix, vix_pctile, macro)
        _state["posture"] = posture
        version = _state["version"]

    return {
        "version":      version,
        "computed_at":  now.isoformat(),
        "regime":       regime,
        "status":       status,
        "vix":          vix,
        "vix_pctile":   vix_pctile,
        "macro":        macro,
        "rotation":     rotation,
        "sector_ranks": ranks,
        "top_sectors":  top_sectors,
        "risk_posture": posture,
        "risk_score":   risk_score,
        "reasons":      reasons,
        "gates":        {"min_gate": min_gate, "strong_gate": strong_gate, "halt": halt},
        "source":       "MarketContextBundle",
    }


def _log_posture_flip(prev: str, new: str, regime: str, vix, vix_pctile, macro: dict) -> None:
    """Persist a RegimeLog row when risk_posture changes (audit trail)."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import RegimeLog
        db = SessionLocal()
        try:
            db.add(RegimeLog(
                state=f"{regime}/{new}",   # e.g. "BEAR/risk_off"
                vix=vix,
                vix_pctile=vix_pctile,
                yield_spread=macro.get("t10y2y"),
                changed=True,
            ))
            db.commit()
            logger.info("risk_posture flip: %s → %s (regime=%s)", prev, new, regime)
        finally:
            db.close()
    except Exception as exc:
        logger.debug("context bundle: RegimeLog write failed: %s", exc)


# ── Public accessors ──────────────────────────────────────────────────────────

async def get_context_bundle(force: bool = False) -> dict:
    """Async accessor (HTTP endpoint). Redis-cached for 900s."""
    import asyncio
    from app.services.redis_client import cached_or_compute

    async def _compute():
        return await asyncio.to_thread(compute_context_bundle)

    return await cached_or_compute(
        "context:bundle", 900, _compute, force_refresh=force, compute_timeout=30
    )


def get_context_bundle_sync(force: bool = False) -> dict:
    """Synchronous accessor for thread-based callers (screener, risk_manager).

    Uses a module-level TTL memo so a screener run over hundreds of symbols
    computes the bundle at most once per _SYNC_TTL window.
    """
    now = time.time()
    with _lock:
        if (not force and _sync_cache["data"] is not None
                and now - _sync_cache["ts"] < _SYNC_TTL):
            return _sync_cache["data"]
    data = compute_context_bundle()
    with _lock:
        _sync_cache["data"] = data
        _sync_cache["ts"] = now
    return data


def regime_risk_multiplier(posture: str) -> float:
    """Position-size multiplier by posture (used in shadow risk sizing).

    risk_off → 0.5 (halve size), neutral → 0.85, risk_on → 1.0.
    """
    return {"risk_off": 0.5, "neutral": 0.85, "risk_on": 1.0}.get(posture, 0.85)


# ── Shadow conditioning for the screener ────────────────────────────────────────
# Sector boost by RRG quadrant; macro factor by posture. Used to compute a
# context_adjusted_score WITHOUT touching the live swing_score (shadow mode).
_QUADRANT_BOOST = {"leading": 1.10, "improving": 1.05, "weakening": 0.97,
                   "lagging": 0.92, "unknown": 1.0}
_MACRO_FACTOR = {"risk_on": 1.05, "neutral": 1.0, "risk_off": 0.90}


def _sector_to_rank_map(bundle: dict) -> dict:
    """sector NAME (lowercased) → {quadrant, rank, ...} from bundle sector_ranks."""
    out: dict = {}
    for _etf, info in (bundle.get("sector_ranks") or {}).items():
        name = (info.get("sector") or "").strip().lower()
        if name:
            out[name] = info
    return out


def get_symbol_sectors(symbols: list[str]) -> dict:
    """symbol → sector name, from the Universe table (single query). {} on failure."""
    try:
        from app.db.database import SessionLocal
        from app.db.models import Universe
        db = SessionLocal()
        try:
            ups = [s.upper() for s in symbols if s]
            if not ups:
                return {}
            rows = (db.query(Universe.symbol, Universe.sector)
                      .filter(Universe.symbol.in_(ups)).all())
            return {row[0].upper(): row[1] for row in rows if row[1]}
        finally:
            db.close()
    except Exception:
        return {}


def apply_context_shadow(results: list[dict],
                         symbol_sectors: dict | None = None,
                         bundle: dict | None = None) -> list[dict]:
    """Attach SHADOW conditioning fields to screener results (additive only).

    Adds per row: rotation_quadrant, sector_rank, risk_posture,
    context_adjusted_score = swing_score × sector_boost × macro_factor.

    Does NOT modify swing_score / swing_signal (those drive live trades). When
    settings.CONTEXT_CONDITIONING_LIVE is True the caller may re-rank by
    context_adjusted_score; otherwise these fields are display/validation only.
    """
    try:
        if bundle is None:
            bundle = get_context_bundle_sync()
        posture = bundle.get("risk_posture", "neutral")
        macro_factor = _MACRO_FACTOR.get(posture, 1.0)
        sect_map = _sector_to_rank_map(bundle)
        symbol_sectors = symbol_sectors or {}
        for r in results:
            if not isinstance(r, dict):
                continue
            sym = (r.get("symbol") or "").upper()
            sector = (symbol_sectors.get(sym) or "").strip().lower()
            info = sect_map.get(sector)
            quad = info["quadrant"] if info else "unknown"
            boost = _QUADRANT_BOOST.get(quad, 1.0)
            base = r.get("swing_score") or 0
            r["rotation_quadrant"] = quad
            r["sector_rank"] = info["rank"] if info else None
            r["risk_posture"] = posture
            r["context_adjusted_score"] = round(base * boost * macro_factor, 1)
    except Exception as exc:
        logger.debug("apply_context_shadow failed: %s", exc)
    return results
