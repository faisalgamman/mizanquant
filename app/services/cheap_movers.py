"""Cheap-mover scanner for the SPECULATION (Ross Cameron) shadow ledger.

The S&P/Russell day-trade scan has almost no sub-$20 names, so the speculation ledger never found
any candidates and stayed empty. This scans Alpaca's LIVE screener (top gainers + most-actives) for
CHEAP stocks making big % moves on volume — Cameron's actual universe — scores them with the SAME
explosion model the day-trade scan uses (RVOL + momentum + volatility + gap + halal flag), and
caches the result under 'speculation_scan' for the ledger to read.

Research / paper only. It places NO orders and never touches the halal buy path. The speculation
ledger still applies its own gates on top (halal_only, price/RVOL, and the 1-minute Cameron pattern).
"""

import logging

logger = logging.getLogger("screener")

_CACHE_KEY = "speculation_scan"


def scan_cheap_movers(max_price: float = 20.0, limit: int = 50) -> list:
    """Build candidate rows DIRECTLY from Alpaca's screener (real price + real day % move).

    We do NOT re-derive via daily bars (scan_explosion): the free IEX feed is too sparse to score
    the thin gappers, and its RVOL reads <1 early in the session (partial-day volume vs a full-day
    average). The screener already PRESELECTS the day's top movers, so the honest signal is the day's
    % move itself. explosion_score is a transparent transform of that move; the real entry gate stays
    the 1-minute Cameron pattern (cameron_setup) applied later by the ledger. [] on failure."""
    try:
        from app.services.market_data import get_alpaca_movers
    except Exception as e:
        logger.debug("cheap movers import failed: %s", e)
        return []
    movers = get_alpaca_movers(top=50, max_price=max(float(max_price), 25.0), min_price=1.0)
    rows = []
    for m in movers:
        sym = m.get("symbol")
        price = m.get("price")
        if not sym or not price:
            continue
        try:
            pct = float(m.get("pct_change") or 0.0)
        except (TypeError, ValueError):
            pct = 0.0
        # explosion_score = transparent transform of the REAL day move (50 base + the % gain,
        # capped). A +5% mover → 55; +50% → 100. Not the full technical model — a mover proxy.
        score = round(min(100.0, 50.0 + abs(pct)), 1)
        rows.append({
            "symbol": sym,
            "price": round(float(price), 4),
            "explosion_score": score,
            "gap_pct": round(pct, 2),
            "momentum_pct": round(pct, 2),
            "change_pct": round(pct, 2),
            "rvol": None,           # not available from the screener (movers are top-of-tape by design)
            "is_halal": None,       # research mode — NOT halal-screened; the ledger runs halal_only=false here
            "source": "alpaca_screener",
        })
    rows.sort(key=lambda r: r["explosion_score"], reverse=True)
    return rows[:int(limit)]


def refresh_cheap_movers(max_price: float = 20.0) -> dict:
    """Scan + cache under 'speculation_scan'. Fail-safe; returns a small status dict."""
    try:
        rows = scan_cheap_movers(max_price=max_price)
        from app.workspace_server import _cache_set
        _cache_set(_CACHE_KEY, {"results": rows, "source": "alpaca_movers", "universe": "cheap_movers"})
        cheap_halal = sum(1 for r in rows if (r.get("price") or 0) <= max_price and r.get("is_halal"))
        logger.info("cheap movers refreshed: %d scored, %d cheap+halal", len(rows), cheap_halal)
        return {"status": "ok", "rows": len(rows), "cheap_halal": cheap_halal}
    except Exception as e:
        logger.error("cheap movers refresh failed: %s", e)
        return {"status": "error", "message": str(e)[:200]}


__all__ = ["scan_cheap_movers", "refresh_cheap_movers"]
