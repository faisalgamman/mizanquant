#!/usr/bin/env python3
"""
mizanquant rebrand — applies the new identity to app/static/dashboard.html.

Run from the root of the mizanquant repo:
    python deploy/apply-rebrand.py

What it does:
  1. Backs up app/static/dashboard.html → dashboard.html.bak
  2. Replaces <title>OpenBB Forecast — Trading Dashboard</title>
     with mizanquant title + favicon link
  3. Replaces the sidebar logo (fa-chart-line) with the inline mīm-trace SVG
  4. Replaces "USX PRO" / "Trading Suite" with "mizanquant" / "Halal Trading Suite"
  5. Copies logo SVGs into app/static/

Idempotent — running twice is safe.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Resolve paths from the script location so it doesn't matter where you cd.
REPO_ROOT  = Path(__file__).resolve().parent.parent
DASHBOARD  = REPO_ROOT / "app" / "static" / "dashboard.html"
STATIC_DIR = REPO_ROOT / "app" / "static"
ASSETS_SRC = Path(__file__).resolve().parent / "static"

# ─────────────────────────────────────────────────────────────────────
# Replacements — exact-match strings, applied in order.
# ─────────────────────────────────────────────────────────────────────
REPLACEMENTS = [
    # 1. Title + favicon
    (
        '<title>OpenBB Forecast — Trading Dashboard</title>',
        '<title>mizanquant — Halal Trading Suite</title>\n'
        '<link rel="icon" type="image/svg+xml" href="/static/logo-favicon.svg">'
    ),
    # 2. Sidebar logo — FA icon → inline mīm-trace SVG
    (
        '<div class="logo"><i class="fas fa-chart-line"></i></div>',
        '<div class="logo">'
        '<svg viewBox="0 0 64 64" width="20" height="20" aria-hidden="true">'
        '<circle cx="18" cy="34" r="9.5" fill="none" stroke="currentColor" stroke-width="3.2"/>'
        '<path d="M27 40 L36 30 L44 36 L54 22" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="54" cy="22" r="3" fill="currentColor"/>'
        '</svg>'
        '</div>'
    ),
    # 3. Sidebar wordmark
    (
        '<div class="sidebar-brand-text"><h2>USX PRO</h2><span>Trading Suite</span></div>',
        '<div class="sidebar-brand-text"><h2>mizanquant</h2><span>Halal Trading Suite</span></div>'
    ),
]


def main() -> int:
    if not DASHBOARD.exists():
        print(f"✗ Dashboard not found: {DASHBOARD}", file=sys.stderr)
        return 1

    text = DASHBOARD.read_text(encoding="utf-8")

    # Back up before mutating.
    backup = DASHBOARD.with_suffix(".html.bak")
    if not backup.exists():
        shutil.copy(DASHBOARD, backup)
        print(f"✓ Backup → {backup.relative_to(REPO_ROOT)}")

    changed = 0
    for needle, replacement in REPLACEMENTS:
        if needle in text:
            text = text.replace(needle, replacement, 1)
            changed += 1
            print(f"✓ Replaced: {needle[:70].splitlines()[0]}…")
        elif replacement.split('"')[0] in text or "mizanquant" in text:
            # Already applied — skip silently.
            pass
        else:
            print(f"⚠ Skipped (not found): {needle[:80]}…", file=sys.stderr)

    DASHBOARD.write_text(text, encoding="utf-8")
    print(f"✓ Wrote {DASHBOARD.relative_to(REPO_ROOT)} ({changed} change(s))")

    # Copy SVGs into app/static/
    if ASSETS_SRC.exists():
        for svg in ASSETS_SRC.glob("*.svg"):
            dest = STATIC_DIR / svg.name
            shutil.copy(svg, dest)
            print(f"✓ Copied {svg.name} → {dest.relative_to(REPO_ROOT)}")
    else:
        print(f"⚠ {ASSETS_SRC} missing — skipping SVG copy", file=sys.stderr)

    print("\nDone. Verify locally, then:")
    print("  git add app/static/ deploy/")
    print('  git commit -m "rebrand: mizanquant identity"')
    print("  git push")
    print("\nRailway will auto-deploy if connected to this branch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
