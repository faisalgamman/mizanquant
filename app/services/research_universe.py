"""Research universe — the two-tier breadth expansion (RESEARCH ONLY, never traded).

The measured lesson: discovery power scales with √breadth, but a strategy must pass on the HALAL
slice to matter (the halal filter is heavily binding — edges that live on excluded sectors die).
So the factor panel now captures a wider TRADEABLE universe — the halal candidates first (panel
continuity) plus a liquid non-halal expansion (S&P 500 constituents) — and every snapshot row is
tagged ``halal`` 0/1 so every measurement can be read twice: full universe (discovery) and halal
slice (the verdict that counts).

The expansion names are NEVER bought, never enter any paper ledger or the screener — they exist
only as research rows in FactorSnapshot. Composition knobs (gradual, box-safe):
RESEARCH_EXPANSION (default on), RESEARCH_HALAL_N (250), RESEARCH_EXPANSION_N (150).
"""

from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger("screener")

# Liquid fallback expansion list (used when the S&P-500 fetch fails): deliberately includes the
# sectors the halal screen excludes (banks/insurance/exchanges/asset managers…) — that exclusion
# is exactly what makes them pure RESEARCH breadth. All are mega/large-cap, deep-liquidity names.
_STATIC_EXPANSION = [
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "BLK", "BX", "KKR", "APO", "USB", "PNC", "TFC",
    "COF", "BK", "STT", "AXP", "V", "MA", "PYPL", "FI", "FIS", "GPN", "ICE", "CME", "NDAQ", "CBOE",
    "SPGI", "MCO", "MSCI", "AIG", "MET", "PRU", "AFL", "ALL", "TRV", "CB", "PGR", "HIG", "CINF",
    "AJG", "AON", "MMC", "WTW", "BRO", "L", "GL", "BRK.B", "WRB", "RJF", "AMP", "TROW", "BEN",
    "IVZ", "NTRS", "FITB", "HBAN", "RF", "CFG", "KEY", "MTB", "SYF", "DFS", "ALLY",
    "SO", "DUK", "NEE", "AEP", "EXC", "SRE", "XEL", "ED", "PEG", "WEC", "ES", "EIX", "DTE", "PPL",
    "AEE", "CMS", "CNP", "FE", "NRG", "VST", "CEG",
    "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "PSA", "WELL", "DLR", "AVB", "EQR", "VTR", "ARE",
    "MGM", "LVS", "WYNN", "CZR", "DKNG", "MO", "PM", "STZ", "TAP", "BF.B", "DEO",
    "LMT", "RTX", "NOC", "GD", "BA", "LHX", "HII",
]


def _cache_path() -> str:
    base = os.environ.get("CACHE_DIR") or "/data"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return os.path.join(base, "sp500_constituents.json")


def _fetch_sp500() -> list[str]:
    """S&P-500 constituents (symbols) — fetched once and cached 30 days; [] on failure."""
    path = _cache_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("symbols") and (time.time() - float(data.get("at", 0))) < 30 * 86400:
            return list(data["symbols"])
    except Exception:
        pass
    symbols: list[str] = []
    try:
        import httpx
        url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
        with httpx.Client(timeout=15) as client:
            resp = client.get(url)
            resp.raise_for_status()
        lines = resp.text.splitlines()
        for line in lines[1:]:
            sym = line.split(",")[0].strip().upper()
            if sym and sym.isascii() and 1 <= len(sym) <= 6:
                symbols.append(sym)
        if symbols:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"at": time.time(), "symbols": symbols}, fh)
            logger.info("research_universe: fetched %d S&P500 constituents", len(symbols))
    except Exception as e:
        logger.debug("research_universe: S&P500 fetch failed (%s) — static fallback", e)
    return symbols


def get_halal_set() -> set[str]:
    """The CURRENT halal membership used to tag snapshot rows: the AAOIFI-passing basket when
    available, else the curated/expanded halal universe table. Never raises."""
    try:
        from app.workspace_server import _cache_get
        basket = _cache_get("halal_basket", max_age=86400 * 3) or {}
        rows = basket.get("results") or []
        if len(rows) >= 30:
            return {str(r.get("symbol", "")).upper() for r in rows if r.get("symbol")}
    except Exception:
        pass
    try:
        from app.services.universe import build_halal_candidates
        return {str(s).upper() for s in (build_halal_candidates() or [])}
    except Exception:
        return set()


def get_research_universe() -> list[str]:
    """The capture universe: top halal candidates FIRST (exact continuity with the existing
    panel head), then the liquid expansion. Composition via env; expansion can be disabled
    (RESEARCH_EXPANSION=false → behaves like the old halal-only capture)."""
    try:
        halal_n = int(os.environ.get("RESEARCH_HALAL_N", "250"))
        exp_n = int(os.environ.get("RESEARCH_EXPANSION_N", "150"))
    except (TypeError, ValueError):
        halal_n, exp_n = 250, 150
    try:
        from app.services.universe import build_halal_candidates
        halal = [str(s).upper() for s in (build_halal_candidates() or [])][:halal_n]
    except Exception as e:
        logger.debug("research_universe: halal candidates failed: %s", e)
        halal = []
    if os.environ.get("RESEARCH_EXPANSION", "true").strip().lower() not in ("true", "1", "yes", "on"):
        return halal
    expansion_src = _fetch_sp500() or list(_STATIC_EXPANSION)
    seen = set(halal)
    expansion = []
    for s in expansion_src:
        su = str(s).upper()
        if su and su not in seen:
            expansion.append(su)
            seen.add(su)
        if len(expansion) >= exp_n:
            break
    return halal + expansion


__all__ = ["get_research_universe", "get_halal_set"]
