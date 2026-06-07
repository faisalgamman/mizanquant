"""Periodic DJIM FINANCIAL re-screen of the curated halal universe.

The curated universe (``data/halal_universe_v2.json``) only guarantees the SECTOR
screen (no banks / alcohol / gambling / …). It does NOT guarantee the DJIM
FINANCIAL ratios, which drift over time as companies take on debt. A name can sit
in a halal sector yet breach the debt screen — e.g. Clorox (CLX) at ~39% debt /
market cap > the 33% limit. ``verify_halal`` historically passed every curated
name without checking those ratios, so such drift went undetected.

This module recomputes the three market-cap-based financial screens for every
universe symbol and reports the failures, so they can be pruned:

    debt screen       : total_debt / avg_mcap_24m          < 33%
    liquidity screen  : (cash + short-term inv) / avg_mcap_24m < 33%
    receivables screen : net_receivables / avg_mcap_24m     < 33%

Delegates to ``screen_symbol`` in halal_screening.py so logic stays in one place.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("screener")

DEBT_MAX_PCT = 33.0
LIQUIDITY_MAX_PCT = 33.0

_UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "data" / "halal_universe_v2.json"
_EXCLUDED_PATH = Path(__file__).resolve().parents[2] / "data" / "halal_excluded_by_ratio.json"


def screen_financials_djim(symbol: str) -> dict | None:
    """Run full DJIM 3-ratio screen for one symbol via screen_symbol.

    Returns {debt_ratio, liquidity_ratio, receivable_ratio,
             debt_pass, liquidity_pass, receivable_pass, passes, avg_mcap, mcap_basis}
    or None when data is unavailable.
    Delegates to the canonical screen_symbol so logic stays in one place.
    """
    try:
        from app.services.halal_screening import screen_symbol
        r = screen_symbol(symbol)
    except Exception as e:
        logger.debug("screen_financials_djim %s: %s", symbol, e)
        return None
    if r is None:
        return None
    passes = r.get("debt_pass", False) and r.get("liquidity_pass", False) and r.get("receivable_pass", True)
    return {
        "debt_ratio": r.get("debt_ratio", 999.0),
        "liquidity_ratio": r.get("liquidity_ratio", 999.0),
        "receivable_ratio": r.get("receivable_ratio", 0.0),
        "debt_pass": r.get("debt_pass", False),
        "liquidity_pass": r.get("liquidity_pass", False),
        "receivable_pass": r.get("receivable_pass", True),
        "passes": passes,
        "avg_mcap": r.get("avg_market_cap", r.get("market_cap", 0)),
        "mcap_basis": r.get("mcap_basis", "spot_fallback"),
    }


# Keep old name as alias for backward-compat with existing tests
screen_financials_yf = screen_financials_djim


def _fail_reason(r: dict) -> str:
    parts = []
    if not r.get("debt_pass", True):
        parts.append(f"debt {r['debt_ratio']}% > {DEBT_MAX_PCT:.0f}%")
    if not r.get("liquidity_pass", True):
        parts.append(f"liquidity {r['liquidity_ratio']}% > {LIQUIDITY_MAX_PCT:.0f}%")
    if not r.get("receivable_pass", True):
        parts.append(f"receivables {r.get('receivable_ratio', 0)}% > 33%")
    return "; ".join(parts) or "ratio breach"


def rescreen_universe(symbols: list[str], sleep: float = 0.2, _screen_fn=None) -> dict:
    """Re-screen the financial ratios for ``symbols``.

    Returns {checked, passed, failed:[{symbol,debt_ratio,liquidity_ratio,reason}],
    unknown:[symbols]}. ``_screen_fn`` is injectable for tests; defaults to
    ``screen_financials_yf``. Unknown (no data) names are NOT failed — they are
    reported separately so the caller decides policy (we do not fabricate a pass).
    """
    screen = _screen_fn or screen_financials_yf
    failed: list[dict] = []
    unknown: list[str] = []
    passed = 0
    for i, sym in enumerate(symbols, 1):
        r = screen(sym)
        if r is None:
            unknown.append(sym)
        elif r["passes"]:
            passed += 1
        else:
            failed.append({
                "symbol": sym,
                "debt_ratio": r["debt_ratio"],
                "liquidity_ratio": r["liquidity_ratio"],
                "receivable_ratio": r.get("receivable_ratio", 0.0),
                "reason": _fail_reason(r),
            })
        if sleep and i % 1 == 0:
            time.sleep(sleep)
    return {
        "checked": len(symbols),
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
    }


def load_universe(path: Path = _UNIVERSE_PATH) -> list[str]:
    data = json.loads(path.read_text())
    return list(data.get("symbols", []))


def apply_rescreen(result: dict, dry_run: bool = True, path: Path = _UNIVERSE_PATH) -> dict:
    """Prune the failed names from the universe JSON (financial-ratio breaches).

    Writes the excluded names + reasons to ``halal_excluded_by_ratio.json`` for an
    audit trail. ``unknown`` (no-data) names are KEPT (not pruned) — absence of
    data is not proof of non-compliance. dry_run=True by default: returns the
    planned change without writing.
    """
    failed_syms = {f["symbol"] for f in result.get("failed", [])}
    data = json.loads(path.read_text())
    before = list(data.get("symbols", []))
    after = [s for s in before if s not in failed_syms]
    plan = {
        "removed": [s for s in before if s in failed_syms],
        "before_count": len(before),
        "after_count": len(after),
        "dry_run": dry_run,
    }
    if dry_run:
        return plan
    data["symbols"] = after
    path.write_text(json.dumps(data, indent=2))
    _EXCLUDED_PATH.write_text(json.dumps(
        {"excluded": result.get("failed", []), "asof": time.strftime("%Y-%m-%d")}, indent=2,
    ))
    logger.info("halal rescreen: pruned %d names from universe (%d -> %d)",
                len(plan["removed"]), plan["before_count"], plan["after_count"])
    return plan
