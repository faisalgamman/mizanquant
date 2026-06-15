"""Cointegrated-pair scanner for halal pairs trading (Phase 3A).

Wires the previously-orphaned ``app.services.cointegration`` toolkit into a
production scanner.  Pairs are formed **within the same sector only** (per
user decision) — economically meaningful relationships, fewer spurious
cointegrations, and far less compute than a full O(N²) universe sweep.

Pipeline per sector:
  1. Pull the halal universe (universe.get_universe_symbols) and group by
     sector (universe.get_symbol_info[...]["sector"]).
  2. Correlation pre-filter on daily returns (PAIRS_CORRELATION_MIN) to cut
     the candidate set before the expensive Engle–Granger test.
  3. cointegration_report() on each surviving candidate; keep only pairs that
     pass the combined gate (EG p-value + tradable OU half-life).
  4. Rank by p-value ascending, truncate to PAIRS_MAX_PAIRS.

Results are cached (memory + JSON) with a TTL because cointegration
relationships are stable over weeks — no need to re-run the heavy scan every
trading cycle.

Environment
-----------
PAIRS_PVALUE_MAX        Max Engle–Granger p-value to accept (default 0.05).
PAIRS_MAX_HALF_LIFE     Max OU half-life in bars to accept (default 30).
PAIRS_CORRELATION_MIN   Min |corr| of returns to survive pre-filter (0.7).
PAIRS_MAX_PAIRS         Max cointegrated pairs returned (default 20).
PAIRS_SCAN_TTL          Cache TTL in seconds (default 86400 = 1 day).
PAIRS_SCAN_LOOKBACK     History window for the scan (default "1y").
PAIRS_MIN_SECTOR_SIZE   Minimum symbols in a sector to bother scanning (3).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

logger = logging.getLogger("screener")

# ── Config (env-driven) ──────────────────────────────────────────────────────

PAIRS_PVALUE_MAX: float = float(os.environ.get("PAIRS_PVALUE_MAX", "0.05"))
PAIRS_MAX_HALF_LIFE: float = float(os.environ.get("PAIRS_MAX_HALF_LIFE", "30"))
PAIRS_CORRELATION_MIN: float = float(os.environ.get("PAIRS_CORRELATION_MIN", "0.7"))
PAIRS_MAX_PAIRS: int = int(os.environ.get("PAIRS_MAX_PAIRS", "20"))
PAIRS_SCAN_TTL: float = float(os.environ.get("PAIRS_SCAN_TTL", "86400"))
PAIRS_SCAN_LOOKBACK: str = os.environ.get("PAIRS_SCAN_LOOKBACK", "1y")
PAIRS_MIN_SECTOR_SIZE: int = int(os.environ.get("PAIRS_MIN_SECTOR_SIZE", "3"))
# Cost cap on the live-FMP step of sector resolution. FMP's free tier is only
# 250 req/day and the MONTHLY screener depends on that same budget, so the pairs
# scan must NOT burst it (a 400-symbol burst once exhausted the daily quota and
# forced the screener onto its slow yfinance fallback). Default tiny: lean on the
# free sources — DB Universe.sector + FMPCache (the screener populates both daily)
# + the durable map below — and let the few still-missing names heal a handful per
# day. Override via env only for a deliberate one-off backfill.
PAIRS_SECTOR_FMP_CAP: int = int(os.environ.get("PAIRS_SECTOR_FMP_CAP", "25"))

_CACHE_PATH = Path(os.environ.get("PAIRS_SCAN_CACHE", "pairs_scan_cache.json"))
# Resolved symbol→sector map persists to the durable volume (CACHE_DIR=/data on
# Railway) so the one-time sector lookup survives restarts and never re-pays.
_SECTOR_CACHE_PATH = Path(
    os.environ.get("CACHE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
) / "pairs_sector_map.json"

# Module-level cache
_cache_pairs: list["PairReport"] | None = None
_cache_ts: float = 0.0

# Diagnostics from the most recent *real* scan (not cache hits). Surfaced by the
# API so "zero pairs" is explainable: is the stats engine present, how many
# candidates were tested, and how close did the best pair get to the gates.
_last_scan_stats: dict = {}


def coint_engine_available() -> bool:
    """True iff statsmodels (the Engle–Granger test) is importable. When false,
    every p-value is forced to 1.0 and the scanner can NEVER find a pair."""
    try:
        from app.services.cointegration import _HAS_COINT
        return bool(_HAS_COINT)
    except Exception:
        return False


def last_scan_stats() -> dict:
    """Funnel + gates from the last real scan (empty until one has run)."""
    return dict(_last_scan_stats)


# ── Data object ───────────────────────────────────────────────────────────────

@dataclass
class PairReport:
    """A cointegrated pair discovered by the scanner."""
    y_symbol: str
    x_symbol: str
    sector: str
    pvalue: float
    hedge_ratio: float
    intercept: float
    half_life: float
    zscore: float

    @property
    def pair_key(self) -> str:
        return f"{self.y_symbol}/{self.x_symbol}"

    def as_dict(self) -> dict:
        return {
            "pair": self.pair_key,
            "y_symbol": self.y_symbol,
            "x_symbol": self.x_symbol,
            "sector": self.sector,
            "pvalue": round(self.pvalue, 4),
            "hedge_ratio": round(self.hedge_ratio, 4),
            "intercept": round(self.intercept, 4),
            "half_life_bars": round(self.half_life, 2),
            "zscore": round(self.zscore, 3),
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _close_series(symbol: str, lookback: str):
    """Fetch the close-price Series for *symbol* (DatetimeIndex), or None."""
    try:
        from app.services.market_data import fetch as fetch_market_data

        df = fetch_market_data(symbol, period=lookback)
        if df is None or len(df) < 60:
            return None
        col_map = {c.lower(): c for c in df.columns}
        close_col = col_map.get("close") or col_map.get("adjclose")
        if not close_col:
            return None
        s = df[close_col].dropna()
        s.name = symbol
        return s if len(s) >= 60 else None
    except Exception as exc:
        logger.debug("pairs_scanner: close fetch failed for %s: %s", symbol, exc)
        return None


def _load_sector_cache() -> dict[str, str]:
    try:
        if _SECTOR_CACHE_PATH.exists():
            return json.loads(_SECTOR_CACHE_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_sector_cache(smap: dict[str, str]) -> None:
    try:
        _SECTOR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SECTOR_CACHE_PATH.write_text(json.dumps(smap))
    except Exception as exc:
        logger.debug("pairs_scanner: sector cache save failed: %s", exc)


def _resolve_sectors(symbols: list[str]) -> dict[str, str]:
    """symbol(UPPER) → sector, resolved through a cost-aware cascade:
    DB Universe (batched) → durable cache → FMPCache profiles → live FMP (capped).

    The naive per-symbol DB lookup returns nothing when ``Universe.sector`` is
    unpopulated (fresh/unseeded DB) — which silently zeroed the whole scan. This
    cascade mirrors the proven backtest sector-map resolver and persists results
    so the heavy lookup is one-time. Returns only non-empty sectors.
    """
    ups = [s.upper() for s in symbols if s]
    smap: dict[str, str] = {}

    # 1) DB Universe — single batched query (no N+1)
    try:
        from app.services.market_context_bundle import get_symbol_sectors
        smap.update({k.upper(): v for k, v in (get_symbol_sectors(ups) or {}).items() if v})
    except Exception as exc:
        logger.debug("pairs_scanner: DB sector map failed: %s", exc)

    # 2) durable cache (CACHE_DIR) — free, survives restarts
    cached = _load_sector_cache()
    for s in ups:
        if s not in smap and cached.get(s):
            smap[s] = cached[s]

    # 3) FMPCache profiles from prior screening — free (the screener fetches these daily)
    missing = [s for s in ups if s not in smap]
    if missing:
        try:
            from app.db.database import SessionLocal
            from app.db.models import FMPCache
            db = SessionLocal()
            try:
                for s in missing:
                    row = db.query(FMPCache).filter(FMPCache.cache_key == f"profile_{s}").first()
                    if row and row.data and row.data.get("sector"):
                        smap[s] = row.data["sector"]
            finally:
                db.close()
        except Exception as exc:
            logger.debug("pairs_scanner: FMPCache sector lookup failed: %s", exc)
        missing = [s for s in ups if s not in smap]

    # 4) live FMP — costs API credits, so capped; results cached below for next time
    if missing:
        try:
            from app.services.fmp_client import fmp_client
            for s in missing[:PAIRS_SECTOR_FMP_CAP]:
                try:
                    prof = fmp_client.get_profile(s)
                    if prof and prof.get("sector"):
                        smap[s] = prof["sector"]
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("pairs_scanner: live FMP sector lookup failed: %s", exc)

    if smap:
        _save_sector_cache({**cached, **smap})
    return smap


def _group_by_sector() -> dict[str, list[str]]:
    """Return {sector: [symbols...]} for the active halal universe."""
    from app.db.database import SessionLocal
    from app.services.universe import get_universe_symbols

    db = SessionLocal()
    try:
        symbols = get_universe_symbols(db)
    finally:
        db.close()

    smap = _resolve_sectors(symbols)
    sectors: dict[str, list[str]] = {}
    for sym in symbols:
        sector = smap.get(sym.upper()) or "UNKNOWN"
        sectors.setdefault(sector, []).append(sym)

    # Drop the UNKNOWN bucket and tiny sectors (can't form meaningful pairs)
    return {
        sec: syms
        for sec, syms in sectors.items()
        if sec != "UNKNOWN" and len(syms) >= PAIRS_MIN_SECTOR_SIZE
    }


def _returns_correlation(s_a, s_b) -> float:
    """|Pearson correlation| of aligned daily returns, or 0.0 on failure."""
    try:
        import pandas as pd

        joined = pd.concat([s_a, s_b], axis=1, join="inner").dropna()
        if len(joined) < 60:
            return 0.0
        rets = joined.pct_change().dropna()
        if len(rets) < 40:
            return 0.0
        corr = rets.iloc[:, 0].corr(rets.iloc[:, 1])
        return abs(float(corr)) if corr is not None and math.isfinite(corr) else 0.0
    except Exception:
        return 0.0


# ── Cache I/O ─────────────────────────────────────────────────────────────────

def _save_cache(pairs: list[PairReport]) -> None:
    try:
        _CACHE_PATH.write_text(
            json.dumps({"ts": time.time(), "pairs": [p.as_dict() for p in pairs]}, indent=2)
        )
    except Exception as exc:
        logger.debug("pairs_scanner: cache save failed: %s", exc)


def _load_disk_cache() -> list[PairReport] | None:
    try:
        if not _CACHE_PATH.exists():
            return None
        data = json.loads(_CACHE_PATH.read_text())
        if time.time() - float(data.get("ts", 0)) > PAIRS_SCAN_TTL:
            return None
        pairs = []
        for d in data.get("pairs", []):
            pairs.append(PairReport(
                y_symbol=d["y_symbol"], x_symbol=d["x_symbol"], sector=d["sector"],
                pvalue=d["pvalue"], hedge_ratio=d["hedge_ratio"],
                intercept=d.get("intercept", 0.0), half_life=d["half_life_bars"],
                zscore=d["zscore"],
            ))
        return pairs
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def find_cointegrated_pairs(
    max_pairs: int | None = None,
    force_refresh: bool = False,
) -> list[PairReport]:
    """Scan the halal universe (within-sector) for cointegrated pairs.

    Cached for PAIRS_SCAN_TTL seconds. Pass force_refresh=True to rescan.
    """
    global _cache_pairs, _cache_ts

    max_pairs = max_pairs if max_pairs is not None else PAIRS_MAX_PAIRS

    # Memory cache
    if not force_refresh and _cache_pairs is not None and (time.time() - _cache_ts) < PAIRS_SCAN_TTL:
        return _cache_pairs[:max_pairs]

    # Disk cache
    if not force_refresh:
        disk = _load_disk_cache()
        if disk is not None:
            _cache_pairs = disk
            _cache_ts = time.time()
            return disk[:max_pairs]

    from app.services.cointegration import cointegration_report

    global _last_scan_stats
    sectors = _group_by_sector()
    n_symbols_in_sectors = sum(len(v) for v in sectors.values())
    logger.info("pairs_scanner: scanning %d sectors for cointegrated pairs", len(sectors))

    results: list[PairReport] = []
    # Scan funnel — so a "zero pairs" outcome is explainable rather than mysterious.
    n_symbols_with_data = 0
    n_candidate_pairs = 0          # within-sector combinations considered
    n_passed_corr = 0             # survived the |corr| pre-filter
    n_eg_tested = 0               # Engle–Granger actually run
    n_passed_pvalue = 0           # p ≤ threshold (ignoring half-life)
    best_pvalue = 1.0
    best_pair = None

    for sector, symbols in sectors.items():
        # Fetch each symbol's closes once for this sector
        closes: dict[str, object] = {}
        for sym in symbols:
            s = _close_series(sym, PAIRS_SCAN_LOOKBACK)
            if s is not None:
                closes[sym] = s
        avail = sorted(closes.keys())
        n_symbols_with_data += len(avail)
        if len(avail) < 2:
            continue

        for a, b in combinations(avail, 2):
            n_candidate_pairs += 1
            # Correlation pre-filter (cheap) before the EG test (expensive)
            if _returns_correlation(closes[a], closes[b]) < PAIRS_CORRELATION_MIN:
                continue
            n_passed_corr += 1
            try:
                import pandas as pd
                joined = pd.concat([closes[a], closes[b]], axis=1, join="inner").dropna()
                if len(joined) < 60:
                    continue
                y_vals = joined.iloc[:, 0].values
                x_vals = joined.iloc[:, 1].values
                rep = cointegration_report(
                    y_vals, x_vals,
                    pvalue_threshold=PAIRS_PVALUE_MAX,
                    max_half_life_bars=PAIRS_MAX_HALF_LIFE,
                )
                n_eg_tested += 1
                if rep.pvalue < best_pvalue:
                    best_pvalue = rep.pvalue
                    best_pair = f"{a}/{b}"
                if rep.pvalue <= PAIRS_PVALUE_MAX:
                    n_passed_pvalue += 1
                if rep.is_cointegrated:
                    results.append(PairReport(
                        y_symbol=a, x_symbol=b, sector=sector,
                        pvalue=rep.pvalue, hedge_ratio=rep.hedge_ratio,
                        intercept=rep.intercept, half_life=rep.half_life,
                        zscore=rep.zscore,
                    ))
            except Exception as exc:
                logger.debug("pairs_scanner: report failed for %s/%s: %s", a, b, exc)

    # Rank by p-value ascending (strongest cointegration first)
    results.sort(key=lambda p: p.pvalue)

    _cache_pairs = results
    _cache_ts = time.time()
    _save_cache(results)

    _last_scan_stats = {
        "ts": time.time(),
        "statsmodels_available": coint_engine_available(),
        "sectors": len(sectors),
        "symbols_in_sectors": n_symbols_in_sectors,
        "symbols_with_data": n_symbols_with_data,
        "candidate_pairs": n_candidate_pairs,
        "passed_correlation": n_passed_corr,
        "eg_tested": n_eg_tested,
        "passed_pvalue": n_passed_pvalue,
        "cointegrated": len(results),
        "best_pvalue": round(best_pvalue, 4),
        "best_pair": best_pair,
        "gates": {
            "pvalue_max": PAIRS_PVALUE_MAX,
            "max_half_life_bars": PAIRS_MAX_HALF_LIFE,
            "correlation_min": PAIRS_CORRELATION_MIN,
        },
    }

    logger.info(
        "pairs_scanner: found %d cointegrated pairs "
        "(statsmodels=%s, %d candidates, %d passed corr, %d EG-tested, best p=%.3f)",
        len(results), _last_scan_stats["statsmodels_available"],
        n_candidate_pairs, n_passed_corr, n_eg_tested, best_pvalue,
    )
    return results[:max_pairs]


def clear_cache() -> None:
    """Reset the in-memory + disk cache (used in tests)."""
    global _cache_pairs, _cache_ts
    _cache_pairs = None
    _cache_ts = 0.0
    try:
        if _CACHE_PATH.exists():
            _CACHE_PATH.unlink()
    except Exception:
        pass


__all__ = [
    "PairReport",
    "find_cointegrated_pairs",
    "clear_cache",
    "last_scan_stats",
    "coint_engine_available",
]
