#!/usr/bin/env python3
"""Build expanded halal universe from S&P 500 + Russell 1000 lists.

Output: data/halal_universe_v2.json — consumed by app/services/universe.py.

Usage:
    python scripts/build_halal_universe.py [--aaoidfi]

Flags:
    --aaoidfi   Run AAOIFI screening via FMP API (slow, needs FMP key).
                Without this flag the script only merges existing lists.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("build_halal_universe")

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_OUTPUT = _PROJECT / "data" / "halal_universe_v2.json"

_HARAM_EXCLUDE = {
    "BAC", "C", "COF", "CFG", "FITB", "GS", "HBAN", "JPM", "KEY", "MS", "MTB",
    "PNC", "RF", "SCHW", "STT", "TFC", "USB", "WFC", "BK", "BEN", "BLK", "BX",
    "CBOE", "CME", "ICE", "IVZ", "KKR", "MSCI", "NDAQ", "SPGI", "TROW",
    "ACGL", "AFL", "AIG", "AIZ", "ALL", "AJG", "BRO", "CB", "CI", "CINF",
    "CNC", "COR", "EG", "ELV", "ERIE", "GL", "HIG", "HUM", "L", "MET",
    "PFG", "PGR", "PRU", "RJF", "TRV", "UNH", "WRB", "WTW",
    "BF.B", "STZ", "TAP", "MO", "PM", "SAM",
    "WYNN", "LVS", "MGM", "CCL", "NCLH", "RCL",
    "LMT", "NOC", "GD", "RTX", "HII", "LHX", "BA",
    "FOX", "FOXA", "NFLX", "DIS", "WBD", "LYV",
    "AEE", "AEP", "AES", "ATO", "CEG", "CMS", "CNP", "D", "DTE", "DUK", "ED",
    "EIX", "ES", "ETR", "EVRG", "EXC", "FE", "LNT", "NEE", "NI", "NRG", "PCG",
    "PEG", "PPL", "SO", "SRE", "VST", "WEC", "XEL",
    "AMT", "ARE", "AVB", "BXP", "CCI", "CPT", "DLR", "DOC", "EQIX", "EQR",
    "ESS", "EXR", "FRT", "HST", "INVH", "IRM", "KIM", "MAA", "O", "PLD",
    "PSA", "REG", "SBAC", "SPG", "UDR", "VICI", "VTR", "WELL",
    "AXP", "SYF", "CPAY",
}


def _load_sp500() -> list[str]:
    """Load S&P 500 halal list from universe.py."""
    sys.path.insert(0, str(_PROJECT))
    from app.services.universe import HALAL_STOCKS_FALLBACK
    return list(HALAL_STOCKS_FALLBACK)


def _load_russell1000() -> list[str]:
    """Load Russell 1000 halal list from app/data/russell1000_halal.py."""
    sys.path.insert(0, str(_PROJECT))
    from app.data.russell1000_halal import RUSSELL_1000_HALAL
    return list(RUSSELL_1000_HALAL)


def _run_aaoidfi(symbols: list[str]) -> dict[str, str]:
    """Run AAOIFI screening via FMP API.

    Returns dict of {symbol: "PASS"/"FAIL"/"DOUBTFUL"}.
    """
    from app.services.halal_screening import get_halal_status
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(get_halal_status, sym): sym for sym in symbols}
        for fut in as_completed(fut_map):
            sym = fut_map[fut]
            try:
                status = fut.result()
                results[sym] = "PASS" if status.get("halal") else "FAIL"
            except Exception as exc:
                logger.warning("AAOIFI screening failed for %s: %s", sym, exc)
                results[sym] = "DOUBTFUL"
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Build expanded halal universe")
    parser.add_argument("--aaoidfi", action="store_true", help="Run AAOIFI screening via FMP")
    args = parser.parse_args()

    sp500 = _load_sp500()
    russell = _load_russell1000()

    merged = list(dict.fromkeys(sp500 + russell))
    merged = sorted(s for s in merged if s not in _HARAM_EXCLUDE)

    logger.info("S&P 500 halal:     %d symbols", len(sp500))
    logger.info("Russell 1000 halal: %d symbols", len(russell))
    logger.info("Merged + dedup:     %d symbols", len(merged))

    screening: dict[str, str] | None = None
    if args.aaoidfi:
        logger.info("Running AAOIFI screening on %d symbols ...", len(merged))
        screening = _run_aaoidfi(merged)
        passed = [s for s, v in screening.items() if v == "PASS"]
        logger.info("AAOIFI passed: %d, failed: %d, doubtful: %d",
                     len(passed),
                     sum(1 for v in screening.values() if v == "FAIL"),
                     sum(1 for v in screening.values() if v == "DOUBTFUL"))

    payload = {
        "source": "S&P 500 + Russell 1000 merged",
        "total_count": len(merged),
        "symbols": sorted(merged),
        "screening": screening,
        "version": 2,
    }

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(payload, indent=2))
    logger.info("Written %s (%d symbols)", _OUTPUT, len(merged))


if __name__ == "__main__":
    main()
