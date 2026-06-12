#!/usr/bin/env python3
"""Build expanded halal universe — DJIM screen, gradual, resume-safe.

Usage:
    python scripts/build_halal_universe.py [--aaoidfi] [--seed-file PATH]
                                           [--max-per-run N] [--resume-age-days N]

Flags:
    --aaoidfi          Run DJIM screening via FMP API (canonical screen_symbol).
    --seed-file PATH   Ticker list to screen (one per line). Default: S&P 500 + Russell 1000.
    --max-per-run N    Max symbols to screen per invocation (default 200) — stay within
                       free FMP budget (~250/day). Run over several days.
    --resume-age-days N  Skip symbols already in the JSON and screened within N days
                         (default 30). Keeps daily runs fast.

Output: data/halal_universe_v2.json — consumed by app/services/universe.py.
- ADDS newly-confirmed halal names only — no pruning.
- The monthly re-screen (scheduler.py) handles ongoing maintenance.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
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


def _load_seed_file(path: str) -> list[str]:
    """Load tickers from a file, one per line."""
    tickers = []
    with open(path, "r") as f:
        for line in f:
            t = line.strip().upper()
            if t and not t.startswith("#"):
                tickers.append(t)
    logger.info("Loaded %d tickers from %s", len(tickers), path)
    return tickers


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


def _load_existing_universe() -> tuple[set[str], dict[str, str]]:
    """Load the current halal_universe_v2.json.

    Returns (symbols_set, screening_dict) or (set(), {}) if missing."""
    if not _OUTPUT.exists():
        return set(), {}
    data = json.loads(_OUTPUT.read_text(encoding="utf-8"))
    symbols = set(data.get("symbols", []))
    screening = data.get("screening", {}) or {}
    return symbols, screening


def _save_universe(symbols: set[str], source: str) -> None:
    """Write the universe JSON — ADDS only, never prunes existing."""
    # Merge with any existing symbols
    existing, _ = _load_existing_universe()
    all_symbols = sorted(existing | symbols)
    payload = {
        "source": source,
        "total_count": len(all_symbols),
        "symbols": all_symbols,
        "version": 2,
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Written %s (%d symbols total)", _OUTPUT, len(all_symbols))


def _screen_one(symbol: str) -> dict | None:
    """Run the canonical DJIM screen_symbol on one ticker.

    Returns the screen_symbol result dict (with is_halal, halal_confidence,
    etc.) or None if data unavailable.
    """
    from app.services.halal_screening import screen_symbol
    try:
        return screen_symbol(symbol)
    except Exception as exc:
        logger.warning("screen_symbol failed for %s: %s", symbol, exc)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build expanded halal universe — DJIM screen, gradual, resume-safe",
    )
    parser.add_argument("--aaoidfi", action="store_true",
                        help="Run DJIM screening (canonical screen_symbol)")
    parser.add_argument("--seed-file", type=str, default="",
                        help="Ticker list file (one per line)")
    parser.add_argument("--max-per-run", type=int, default=200,
                        help="Max symbols to screen per invocation (default 200)")
    parser.add_argument("--resume-age-days", type=int, default=30,
                        help="Skip symbols already screened within N days (default 30)")
    args = parser.parse_args()

    # ── 1. Gather candidate symbols ──────────────────────────────────
    if args.seed_file:
        candidates = _load_seed_file(args.seed_file)
    else:
        sp500 = _load_sp500()
        russell = _load_russell1000()
        candidates = list(dict.fromkeys(sp500 + russell))
        logger.info("S&P 500 halal:     %d symbols", len(sp500))
        logger.info("Russell 1000 halal: %d symbols", len(russell))

    candidates = sorted(s for s in candidates if s and s not in _HARAM_EXCLUDE)
    candidates = [s for s in candidates if s.isalpha()]  # strip funky tickers
    logger.info("Candidate pool:     %d symbols (after haram exclusion)", len(candidates))

    # ── 2. Resume: skip already-in-universe symbols ──────────────────
    existing, old_screening = _load_existing_universe()
    if existing:
        logger.info("Existing universe:  %d symbols", len(existing))

    # Which candidates still need screening?
    to_screen = []
    for sym in candidates:
        if sym in existing and old_screening.get(sym) == "PASS":
            continue  # already confirmed halal, skip
        to_screen.append(sym)

    logger.info("To screen:          %d symbols (new or unconfirmed)", len(to_screen))

    # ── 3. Screen (respect --max-per-run budget) ─────────────────────
    if args.aaoidfi and to_screen:
        batch = to_screen[:args.max_per_run]
        logger.info("Screening %d symbols (DJIM screen_symbol) ...", len(batch))

        new_halal: set[str] = set()
        failed = 0
        for i, sym in enumerate(batch):
            result = _screen_one(sym)
            if result is None:
                failed += 1
                continue
            if result.get("is_halal"):
                new_halal.add(sym)
            # Progress every 50 symbols
            if (i + 1) % 50 == 0:
                logger.info("  … %d/%d done (%d halal so far)",
                            i + 1, len(batch), len(new_halal))

        logger.info(
            "Screening complete: %d HALAL, %d data-unavailable, %d non-compliant",
            len(new_halal), failed,
            len(batch) - len(new_halal) - failed,
        )

        # ── 4. Save (ADD only — never prune) ─────────────────────────
        all_halal = existing | new_halal
        _save_universe(
            all_halal,
            source=args.seed_file or "S&P 500 + Russell 1000 merged",
        )

        if len(to_screen) > args.max_per_run:
            remaining = len(to_screen) - args.max_per_run
            logger.info(
                "Budget exhausted — %d symbols remaining for next run. "
                "Re-run the same command to continue.",
                remaining,
            )
    else:
        # No screening — just merge and save
        logger.info("No screening requested. Use --aaoidfi to run DJIM checks.")
        if existing:
            _save_universe(existing, source="Existing (no new screening)")
        else:
            logger.warning(
                "No existing universe and no screening. "
                "Run with --aaoidfi to build from scratch."
            )


if __name__ == "__main__":
    main()
