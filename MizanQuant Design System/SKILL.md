---
name: mizanquant-design
description: Use this skill to generate well-branded interfaces and assets for MizanQuant — a halal-compliant quantitative trading & AI forecasting terminal — either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

# MizanQuant Design Skill

MizanQuant ("ميزان كوانت") is a single-user pro-grade trading workstation sitting at the intersection of **Sharia-compliant investing** and **algorithmic forecasting**. The brand vocabulary is **warm gold on warm near-black**, **dense terminal-style layouts**, **DM Sans + JetBrains Mono**, and **Font Awesome 6 Solid** iconography. No marketing tone, no decorative imagery, no shimmer, no bounces — this is a Bloomberg-feel pro tool.

## How to use

1. **Read `README.md` first.** It contains:
   - Surfaces map (which production HTMLs/JSXs map to which features)
   - Content Fundamentals (voice, casing, person, emoji policy, copy specimens)
   - Visual Foundations (backgrounds, accent, semantic colors, motion, layout rules)
   - Iconography (Font Awesome glyph vocabulary)
   - File index of everything in this skill

2. **Pull tokens from `colors_and_type.css`.** Every color, font, radius, motion, and spacing constant lives there. Drop it into a new HTML file via `<link rel="stylesheet">` and reference variables by name. **Do not invent new colors** — if you need a new shade, lift one from the existing ramp or ask the user.

3. **Look at preview cards in `preview/`** for live examples of every token in use — buttons, badges, signal cards, type specimens, etc. These are 700px-wide HTML snippets you can copy-paste from.

5. **Reach for public-facing surfaces** when you need marketing or investor materials:
   - **`landing/index.html`** — premium single-page institutional landing with a **Terminal Access Lock** modal (demo password `mizan2026`) and cinematic unlock that routes to the live UI kit. Hero (bilingual), trust strip, 5-layer architecture loop, performance metrics, AAOIFI compliance, audience cards (SWF / Sharia AMs / family offices), CTA. Targets Gulf/MENA capital.
   - **`slides/index.html`** — 6-slide investor briefing deck on `deck-stage` (1920×1080). Title · Architecture · Halal Compliance specimen · Strategy Backtest Comparison · Risk Controls · Closing.
   - **`one-pager/index.html`** — A4 portrait, print-ready tear-sheet. Densifies the landing page into a one-page LP handout with the architecture loop, AAOIFI badge, KPI strip, audience tiles, and institutional disclosure footer. Print directly from Chrome with default margins.
   - **`export/export-deck.py`** — standalone `python-pptx` script that exports the HTML deck as a **native, editable** `.pptx`. All shapes are real PowerPoint primitives — text boxes, rectangles, ovals, lines. Run with `python export/export-deck.py [--out path.pptx]`.
   - **`onboarding/index.html`** — 4-step clickable institutional onboarding prototype. NDA execution (canvas signature pad + 3 ack toggles) → Institutional KYC (entity profile + AAOIFI/Sharia disclosure + document uploads) → Sharia board review (5 reviewers, auto-advancing) → Credentials issuance (Partner ID, username, temp password, API key, TOTP QR, downloads). The final step links into `ui_kits/mizanquant_terminal/`.
   - **`email-signature/index.html`** — bilingual HTML email signature with live preview + form-driven personalisation + one-click HTML copy. Two variants: **dark** (primary, institutional vibe) and **light** (fallback for default-light Gmail). Raw paste-ready files at `email-signature/signature-{dark,light}.html`. **NOTE:** the logo URL inside the signature points to a placeholder GitHub Pages path — host `assets/logo-monogram.svg` on a stable public URL and update `LOGO_URL` before distribution.
   - **`report/index.html`** — bilingual end-of-day report template. Designed to mirror the format of `mizanquant/reports/daily_report_*.md` but rendered in hi-fi with system-health strip, performance KPIs, top-signals table, trades + AAOIFI screen diff, pipeline timeline, risk snapshot, model leaderboard, and a Telegram-ready Arabic blurb with **one-click plain-text copy** (~580 chars, fits Telegram caption limit). Sized for ~1080px web view, but also print-ready (A4 portrait via `@page` rule).

5. **Use brand assets from `assets/`**: `logo-monogram.svg` (mark only, 64×64), `logo-wordmark.svg` (horizontal lockup, 280×80), `logo-favicon.svg` (32×32). These are gold-tinted SVGs; they read on any dark background.

## When creating artifacts

- **HTML mocks / slides / prototypes** → copy `assets/` and reference `colors_and_type.css` directly. Build new screens by composing patterns from `preview/` and `ui_kits/`. Use Font Awesome from CDN: `<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">`.

- **Production code** → become an expert in the rules. The codebase is FastAPI + vanilla JS for the dashboards (`mizanquant/app/static/*.html`) and React + Vite + Tailwind v4 for the strategies-app (`mizanquant/strategies-app/`). When in doubt, mimic `mizanquant/app/static/dashboard.html` — it is the post-rebrand canonical example.

## Hard rules — do not break

- **No marketing tone.** Never use exclamation marks, "🎉", "🚀", or "Let's…". Read internal docs in `mizanquant/reports/` and `mizanquant/docs/` for the actual voice.
- **No shimmer.** Loading states use `@keyframes pulse` (opacity 1 → 0.35) only. The dashboard's source explicitly calls shimmer out as an anti-pattern.
- **No emoji in UI chrome.** Telegram alerts (🕐, 🤖) and AI prompts are the only exceptions — both backend-only.
- **No box-shadow on cards.** Elevation comes from the background ramp + 6% white hairlines. Toast/modal overlay is the single shadow exception (`0 8px 24px rgba(0,0,0,0.4)`).
- **No invented icons.** Use Font Awesome 6 Solid. The canonical glyph vocabulary is in `README.md` → Iconography.
- **All numbers are mono + tabular.** Wrap every numeric value in `font-family: var(--font-mono); font-feature-settings: "tnum" 1;`.
- **Halal compliance is load-bearing.** When showing a stock/signal/sector, flag halal status explicitly — never hide it, never assume.

## If the user invokes this skill alone

Ask what they want to build (a new screen for the product? a deck for investors? a redesign of an existing tab? a one-off marketing page?), gather the brief, then act as an expert designer and output HTML artifacts or production code as appropriate. Default to HTML mockups against `colors_and_type.css` unless told otherwise.

## File index

```
.
├── README.md                          ← read first
├── SKILL.md                           ← this file
├── colors_and_type.css                ← tokens (import this)
├── assets/                            ← logos (copy these)
│   ├── logo-monogram.svg
│   ├── logo-wordmark.svg
│   └── logo-favicon.svg
├── preview/                           ← per-token cards for reference
│   └── [29 HTML cards]
└── ui_kits/
    ├── mizanquant_terminal/           ← Overview dashboard
    ├── risk_desk/                     ← VaR · Kelly · blocks · stress
    └── halal_screener/                ← AAOIFI compliance · sector filters

landing/index.html                 ← gated institutional landing (Terminal Access Lock)
one-pager/index.html               ← A4 print-ready tear-sheet
slides/index.html                  ← 6-slide investor briefing deck
slides/deck-stage.js               ← deck shell (scaling, nav, print-to-PDF)
export/export-deck.py              ← python-pptx native editable PPTX exporter
export/README.md                   ← exporter usage notes
onboarding/index.html              ← 4-step clickable onboarding (NDA → KYC → Sharia → Credentials)
email-signature/index.html         ← live preview + customize + paste-ready (dark + light)
email-signature/signature-dark.html ← raw paste-ready HTML
email-signature/signature-light.html ← raw paste-ready HTML
report/index.html                  ← bilingual EOD report + Telegram blurb (copy to clipboard)
```
