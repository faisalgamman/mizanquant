#!/usr/bin/env python3
"""Rebrand 5 legacy HTML pages to MizanQuant Design System."""
import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "app" / "static"

NEW_ROOT = """:root {
  --bg-root:        #0c0c10;
  --bg-surface:     #14141a;
  --bg-raised:      #1c1c26;
  --bg-overlay:     #242430;
  --bg:             #0c0c10;
  --surface:        #14141a;
  --card:           #1c1c26;
  --bg-card:        #1c1c26;
  --bg-hover:       #242430;
  --surface-raised: #1c1c26;
  --border-subtle:  rgba(255, 255, 255, 0.04);
  --border-default: rgba(255, 255, 255, 0.06);
  --border-strong:  rgba(255, 255, 255, 0.10);
  --border:         rgba(255, 255, 255, 0.06);
  --border-light:   rgba(255, 255, 255, 0.04);
  --border-hi:      rgba(255, 255, 255, 0.10);
  --text-primary:   #e4e4ec;
  --text-secondary: #9494a0;
  --text-muted:     #5c5c6a;
  --text-disabled:  #3c3c48;
  --text:           #e4e4ec;
  --txt:            #e4e4ec;
  --txt2:           #9494a0;
  --txt3:           #5c5c6a;
  --muted:          #5c5c6a;
  --accent:         #c8963e;
  --accent-hover:   #d4a853;
  --accent-dim:     rgba(200, 150, 62, 0.10);
  --accent-glow:    rgba(200, 150, 62, 0.18);
  --positive:       #4ade80;
  --positive-dim:   rgba(74, 222, 128, 0.10);
  --negative:       #f87171;
  --negative-dim:   rgba(248, 113, 113, 0.10);
  --warning:        #fbbf24;
  --warning-dim:    rgba(251, 191, 36, 0.10);
  --info:           #60a5fa;
  --info-dim:       rgba(96, 165, 250, 0.10);
  --green:          #4ade80;
  --green-dim:      rgba(74, 222, 128, 0.10);
  --red:            #f87171;
  --red-dim:        rgba(248, 113, 113, 0.10);
  --yellow:         #fbbf24;
  --amber:          #fbbf24;
  --amber-dim:      rgba(251, 191, 36, 0.10);
  --orange:         #fb923c;
  --gold:           #c8963e;
  --accent-green:   #4ade80;
  --accent-red:     #f87171;
  --accent-orange:  #fb923c;
  --accent-purple:  #a78bfa;
  --accent2:        #a78bfa;
  --fam-transformer: #eab308;
  --fam-rnn:         #a855f7;
  --fam-lstm:        #3b82f6;
  --fam-cnn:         #06b6d4;
  --fam-ensemble:    #22c55e;
  --fam-classic:     #64748b;
  --font-display: 'DM Sans', system-ui, -apple-system, sans-serif;
  --font-body:    system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-mono:    'JetBrains Mono', 'SF Mono', 'Fira Code', ui-monospace, monospace;
  --font-arabic:  'Cairo', 'Tahoma', 'Geeza Pro', system-ui, sans-serif;
  --mono:         'JetBrains Mono', 'SF Mono', 'Fira Code', ui-monospace, monospace;
  --text-xs:   0.625rem;
  --text-sm:   0.75rem;
  --text-base: 0.8125rem;
  --text-md:   0.9375rem;
  --text-lg:   1.125rem;
  --text-xl:   1.375rem;
  --text-2xl:  1.625rem;
  --radius-sm: 3px;
  --radius-md: 5px;
  --radius-lg: 8px;
  --radius-xl: 12px;
  --radius:    5px;
  --r:         5px;
  --ease-out:        cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in-out:     cubic-bezier(0.65, 0, 0.35, 1);
  --duration-fast:   120ms;
  --duration-normal: 200ms;
  --duration-slow:   350ms;
  --sidebar-w:  56px;
  --cmd-h:      48px;
  --market-h:   36px;
  --status-h:   28px;
}"""

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700'
    '&family=JetBrains+Mono:wght@400;500;600;700&family=Cairo:wght@400;600;700&display=swap" rel="stylesheet">\n'
    '<link rel="stylesheet" href="/static/fontawesome/all.min.css">\n'
    '<link rel="stylesheet" href="/static/design-tokens.css">'
)

STRIP = '<script src="/static/ecosystem-strip.js" defer></script>'


def rebrand(path: Path) -> bool:
    html = path.read_text(encoding="utf-8")
    orig = html

    # Remove old font/css links
    for pat in [
        r'<link[^>]*fonts\.googleapis\.com[^>]*/>\n?',
        r'<link[^>]*fonts\.gstatic\.com[^>]*/>\n?',
        r'<link[^>]*preconnect[^>]*googleapis[^>]*/>\n?',
        r'<link[^>]*preconnect[^>]*gstatic[^>]*/>\n?',
        r'<link[^>]*fontawesome[^>]*/>\n?',
        r'<link[^>]*design-tokens[^>]*/>\n?',
    ]:
        html = re.sub(pat, '', html)

    # Inject new font+css links after <meta charset>
    html = re.sub(r'(<meta charset[^>]*>)', r'\1\n' + FONT_LINKS, html, count=1)

    # Replace :root { ... } block
    html = re.sub(r':root\s*\{.*?\}', NEW_ROOT, html, count=1, flags=re.DOTALL)

    # Fix body font-family
    html = re.sub(r'font-family:\s*["\']?Inter["\']?[^;]*;', 'font-family: var(--font-body);', html)
    html = re.sub(r"font:\s*\d+px/[\d.]+ ['\"]?Inter['\"]?[^;]*;", 'font: 13px/1.5 var(--font-body);', html)
    html = re.sub(
        r"font-family:\s*-apple-system,\s*BlinkMacSystemFont,\s*'Segoe UI',\s*Roboto,\s*sans-serif",
        'font-family: var(--font-body)', html
    )

    # Replace hard-coded blue accent
    for blue in ('#3b82f6', '#58a6ff', '#4f8ef7', '#388bfd'):
        html = html.replace(blue, 'var(--accent)')
    html = html.replace('#4090e0', 'var(--accent-hover)')
    html = html.replace('#3a7be0', 'var(--accent-hover)')
    html = re.sub(r'var\(--accent\)[0-9a-fA-F]{2}', 'var(--accent-dim)', html)

    # Replace shimmer with pulse
    PULSE = "@keyframes pulse {\n  0%, 100% { opacity: 1; }\n  50% { opacity: 0.35; }\n}"
    html = re.sub(r'@keyframes\s+shimmer\s*\{[^}]*\}', PULSE, html, flags=re.DOTALL)
    html = html.replace('animation: shimmer', 'animation: pulse')

    # Add ecosystem strip before </body>
    if STRIP not in html:
        html = html.replace('</body>', STRIP + '\n</body>')

    if html != orig:
        path.write_text(html, encoding="utf-8")
        return True
    return False


for page in ["halal-screener.html", "ai-assistant.html", "forecast-panel.html", "alerts.html", "analysis-lab.html"]:
    p = STATIC / page
    if not p.exists():
        print(f"  SKIP     {page}")
        continue
    changed = rebrand(p)
    print(f"  {'UPDATED' if changed else 'NO CHANGE':8s}  {page}")

print("Done.")
