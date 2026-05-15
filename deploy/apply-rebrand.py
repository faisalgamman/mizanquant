#!/usr/bin/env python3
"""
mizanquant rebrand — applies the new identity across the whole product.

Run from the root of the mizanquant repo:
    python deploy/apply-rebrand.py

Patches three files:
  • app/static/dashboard.html       — page title, favicon, sidebar logo + wordmark
  • app/services/telegram_alert.py  — brand footer on every alert (5+ call sites)
  • app/ai_agent.py                 — AI analyst introduces itself as ميزان كوانت

Also copies logo SVGs into app/static/.

Idempotent — each file carries a marker (`mizanquant` / `_BRAND_LINE` / `ميزان`);
if present, that file is skipped silently. Safe to re-run.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "app" / "static"
ASSETS_SRC = Path(__file__).resolve().parent / "static"


# ─────────────────────────────────────────────────────────────────────
# Per-file patch spec
#
# Each entry:
#   marker       — string whose presence means "already rebranded; skip"
#   replacements — list of (needle, replacement, all)
#                  `all=True` does a global replace; `all=False` does one.
# ─────────────────────────────────────────────────────────────────────
PATCHES = [

    # ════════════════════════════════════════════════════════════════
    # 1. DASHBOARD UI
    # ════════════════════════════════════════════════════════════════
    {
        "path": "app/static/dashboard.html",
        "marker": "<h2>mizanquant</h2>",
        "replacements": [
            (
                "<title>OpenBB Forecast — Trading Dashboard</title>",
                "<title>mizanquant — Halal Trading Suite</title>\n"
                '<link rel="icon" type="image/svg+xml" href="/static/logo-favicon.svg">',
                False,
            ),
            (
                '<div class="logo"><i class="fas fa-chart-line"></i></div>',
                '<div class="logo">'
                '<svg viewBox="0 0 64 64" width="20" height="20" aria-hidden="true">'
                '<circle cx="18" cy="34" r="9.5" fill="none" stroke="currentColor" stroke-width="3.2"/>'
                '<path d="M27 40 L36 30 L44 36 L54 22" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>'
                '<circle cx="54" cy="22" r="3" fill="currentColor"/>'
                '</svg>'
                '</div>',
                False,
            ),
            (
                '<div class="sidebar-brand-text"><h2>USX PRO</h2><span>Trading Suite</span></div>',
                '<div class="sidebar-brand-text"><h2>mizanquant</h2><span>Halal Trading Suite</span></div>',
                False,
            ),
        ],
    },

    # ════════════════════════════════════════════════════════════════
    # 2. TELEGRAM ALERTS — brand footer on every message
    # ════════════════════════════════════════════════════════════════
    {
        "path": "app/services/telegram_alert.py",
        "marker": "_BRAND_LINE",
        "replacements": [
            # Insert the _BRAND_LINE constant right after the two URL constants.
            # NOTE: we don't include the trailing newline in the needle (the
            # file may use CRLF — Windows-origin sources do). The original line
            # ending is preserved automatically after the match.
            (
                '_TELEGRAM_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"',
                '_TELEGRAM_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"\n'
                '\n'
                '# Brand footer — included on every alert message.\n'
                '_BRAND_LINE = "ميزان كوانت · mizanquant"',
                False,
            ),
            # Standard UTC-stamped footer pattern — appears 5× across the file.
            (
                '{_utc_label()} UTC',
                '{_BRAND_LINE}\\n{_utc_label()} UTC',
                True,
            ),
            # ET-suffixed pattern — appears 3× (alert_qualified_signal,
            # alert_market_block, and one daily-summary). Two of the three are
            # inside multi-line concatenated f-strings WITHOUT a leading \n,
            # so we match the bare ❛́ prefix and add the brand line inside
            # the same f-string.
            (
                "🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M ET')}",
                "{_BRAND_LINE}\\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M ET')}",
                True,
            ),
        ],
    },

    # ════════════════════════════════════════════════════════════════
    # 3. AI AGENT — introduce as ميزان كوانت
    # ════════════════════════════════════════════════════════════════
    {
        "path": "app/ai_agent.py",
        "marker": "ميزان كوانت",
        "replacements": [
            (
                '"أنت محلل مالي إسلامي خبير. مهمتك هي تحليل بيانات الفرص الاستثمارية "',
                '"أنت المحلل المالي الإسلامي في منصة \\"ميزان كوانت\\" (mizanquant) — '
                'منصة تداول خوارزمي وفق ضوابط الشريعة الإسلامية. '
                'مهمتك هي تحليل بيانات الفرص الاستثمارية "',
                False,
            ),
            (
                '"أنت مستشار استثماري إسلامي خبير. أجب على أسئلة المستثمرين "',
                '"أنت مستشار \\"ميزان كوانت\\" (mizanquant) للاستثمار وفق الشريعة الإسلامية. '
                'أجب على أسئلة المستثمرين "',
                False,
            ),
            (
                "🤖 تم إعداد هذا التقرير بواسطة **المحلل الذكي** | OpenBB Forecast",
                "🤖 تم إعداد هذا التقرير بواسطة **المحلل الذكي** | "
                "**ميزان كوانت** (mizanquant)",
                False,
            ),
            (
                "أنا **المحلل الذكي** لمنصة OpenBB Forecast.",
                "أنا **المحلل الذكي** لمنصة **ميزان كوانت** (mizanquant) — "
                "تداول خوارزمي وفق الشريعة.",
                False,
            ),
        ],
    },
]


def patch_file(rel_path: str, marker: str, replacements: list) -> int:
    """Apply replacements to one file. Returns the count of changes made."""
    path = REPO_ROOT / rel_path
    if not path.exists():
        print(f"⚠ Skipped (not found): {rel_path}", file=sys.stderr)
        return 0

    text = path.read_text(encoding="utf-8")

    # Idempotency guard.
    if marker in text:
        print(f"· {rel_path}  already rebranded (marker found: {marker!r:.40}…)")
        return 0

    # Back up before mutating.
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        shutil.copy(path, backup)

    changed = 0
    for needle, replacement, do_all in replacements:
        if needle not in text:
            print(f"  ⚠ needle not found: {needle[:60]!r:.60}…", file=sys.stderr)
            continue
        count = text.count(needle) if do_all else 1
        text = text.replace(needle, replacement, -1 if do_all else 1)
        changed += count
        suffix = f" ×{count}" if do_all and count > 1 else ""
        print(f"  ✓ {needle[:60].splitlines()[0]}{suffix}")

    path.write_text(text, encoding="utf-8")
    print(f"✓ {rel_path}  ({changed} change(s), backup → {backup.name})")
    return changed


def main() -> int:
    print("=== mizanquant rebrand ===\n")

    total = 0
    for spec in PATCHES:
        print(f"→ {spec['path']}")
        total += patch_file(spec["path"], spec["marker"], spec["replacements"])
        print()

    # Copy SVGs into app/static/
    if ASSETS_SRC.exists() and STATIC_DIR.exists():
        print("→ app/static/  (logo SVGs)")
        for svg in ASSETS_SRC.glob("*.svg"):
            dest = STATIC_DIR / svg.name
            shutil.copy(svg, dest)
            print(f"  ✓ {svg.name}")
    elif not STATIC_DIR.exists():
        print(f"⚠ {STATIC_DIR} missing — skipped SVG copy", file=sys.stderr)

    print(f"\n=== Done · {total} patch(es) applied ===")
    if total:
        print("\nNext:")
        print("  python -c \"from halal_screener import app\"   # smoke-test imports")
        print("  git add app/ deploy/")
        print('  git commit -m "rebrand: mizanquant identity"')
        print("  git push                                       # Railway auto-deploys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
