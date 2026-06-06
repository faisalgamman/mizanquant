"""CLI: generate the weekly swing-picks report (advisory, manual execution).

Usage:
    python scripts/weekly_picks.py --account 10000
    python scripts/weekly_picks.py --account 25000 --top 10 --funnel ready --out picks.md

Read-only: runs the existing halal funnel + Option-A enrichment and prints a
table. It NEVER places an order or sends Telegram. Confirm price/liquidity on the
broker before acting on any line.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `app.*` / `halal_screener` importable when run as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.weekly_report import build_weekly_report, format_report  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MizanQuant weekly swing-picks (advisory).")
    parser.add_argument("--account", type=float, required=True,
                        help="Account equity in USD (for position sizing).")
    parser.add_argument("--top", type=int, default=15, help="Max picks to show.")
    parser.add_argument("--min-confidence", type=float, default=45.0,
                        help="Minimum AI consensus confidence %% to include.")
    parser.add_argument("--funnel", choices=("pipeline", "ready"), default="pipeline",
                        help="pipeline = full funnel incl. USX V4 (default); ready = lighter.")
    parser.add_argument("--out", type=str, default=None,
                        help="Optional path to also write the report (e.g. picks.md).")
    args = parser.parse_args(argv)

    # Windows consoles default to cp1252; force UTF-8 so the report prints cleanly.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    report = build_weekly_report(
        args.account, top=args.top, min_confidence=args.min_confidence, funnel=args.funnel,
    )
    text = format_report(report)
    print(text)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n[written] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
