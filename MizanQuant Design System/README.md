# MizanQuant Design System

> *ميزان كوانت* — a halal-compliant quantitative trading & AI forecasting terminal.

MizanQuant is a single-user pro-grade trading workstation. The product wraps a Python/FastAPI backend (model registry, halal screener, paper-trade book, IBKR/Alpaca brokers, AAOIFI compliance gates, Telegram alerts, multi-LLM AI agent) in a dark, dense, terminal-style web UI. The brand sits at the unusual intersection of **Sharia-compliant investing** and **algorithmic forecasting** — the name *mizan* is Arabic for "balance/scale," referencing both the AAOIFI screening gates and the model-ensemble consensus that drives every signal.

The codebase ships a recent rebrand (see `deploy/README.md`, dated late-April 2026): warm-gold accent on a warm near-black canvas, the **mīm-trace** logo (a circle "lens" + ascending dot-to-dot chart arrow), and a unified DM Sans / JetBrains Mono type pairing. Older screens still in `app/static/` (halal-screener, ai-assistant, forecast-panel) carry pre-rebrand blue/green palettes — those are legacy and will phase out; the system below reflects the post-rebrand canon.

## Surfaces

| Surface | Path in source repo | What it is |
| --- | --- | --- |
| **Overview Dashboard** | `app/static/dashboard.html` (+ `deploy/static/overview-v2.html`) | The flagship screen. 3-column workflow: Scan · Analyze · Trade, plus Models / Sectors / Consensus row. The canonical example of the design system in use. |
| **Strategies App** | `strategies-app/` (React + Vite + Tailwind v4) | The React-based satellite app, mounted at `/static/strategies-app/`. Performance cards, equity curve, drawdown, strategy comparison, positions. |
| **Macro · FRED** | `app/static/macro.html` | Macro indicator dashboard. |
| **Risk Desk** | `app/static/risk-desk.html` | VaR, drawdown alerts, exposure caps, kill-switch. |
| **Forecast Lab** | `app/static/forecast-panel.html` | Model training & calibration suite (legacy palette). |
| **Halal Screener** | `app/static/halal-screener.html` | AAOIFI four-screen pass/fail (legacy palette). |
| **AI Assistant** | `app/static/ai-assistant.html` | LLM-powered Arabic investment reports + chat + 14-model consensus (legacy palette). |

## Sources

This design system was built from a single source: a read-only mount of the production codebase at `mizanquant/` (FastAPI backend + Vite/React frontend). No Figma or external brand guide was provided; all tokens and patterns were lifted directly from CSS variables, inline styles, and component source. The most-authoritative files were:

- `mizanquant/app/static/dashboard.html` — canonical post-rebrand tokens
- `mizanquant/strategies-app/src/index.css` — Tailwind v4 `@theme` definitions
- `mizanquant/deploy/static/{logo-monogram,logo-wordmark,logo-favicon}.svg` — the brand mark
- `mizanquant/deploy/README.md` — rebrand intent & rollout plan
- `mizanquant/app/services/telegram_alert.py`, `mizanquant/app/ai_agent.py` — Arabic copy + voice
- `mizanquant/reports/daily_report_2026-05-04.md` — internal voice / engineering language

---

## Content Fundamentals

**Voice — bilingual, technical, dry.** English handles all UI chrome, table headers, error messages and developer-facing copy. Arabic (RTL, *ميزان كوانت*) is reserved for the AI Investment Reports, Telegram alerts, and the AI assistant prompts — a deliberate choice that positions the product for Gulf/MENA pro retail. Never machine-translate between the two; they are written separately, by hand, for different reading contexts.

**Tone — professional, never breezy.** Reads like a Bloomberg Terminal or quantitative research note, not a fintech consumer app. No exclamation marks. No marketing hyperbole. No "🎉" or "🚀". Numbers and abbreviations carry the meaning; sentences are short.

- ✅ "Auto-trade ON · BUY @ 81 score · halal · risk-on"
- ✅ "13 total tracked issues: 3 FIXED, 1 NEW (LOW), 9 OPEN"
- ✅ "Pipeline running — dry-run · strategy ABC · stage 4/6"
- ❌ "Hey! Looks like the market is moving! 🚀"
- ❌ "We've found some great opportunities for you today."

**Casing — sentence case for prose; UPPER for status & verdicts.** Page titles and headings are sentence case (*"Risk desk"*, not *"Risk Desk"*). Table column headers are eyebrow-style: `UPPERCASE` with 0.5px letter-spacing. Trading verdicts (`BUY` / `STRONG BUY` / `WAIT` / `AVOID` / `SELL`) and regime states (`BULL` / `BEAR` / `NEUTRAL`) are always all-caps, treated as enum values, never lowercased even mid-sentence.

**Person — implicit third / system.** The UI rarely addresses "you" and never uses "we." It states what *is*: "Connected", "Live", "5 positions open", "Auto-trade enabled". Buttons describe the action, not the conversation: "Run pipeline" not "Let's run the pipeline"; "Send to paper trade" not "Send this to paper trading for me".

**Emoji — no.** The codebase contains zero decorative emoji in UI chrome. The two narrow exceptions, both backend-facing: (1) Telegram alerts use a leading 🕐 timestamp + 🤖 byline because the channel needs visual scanning at speed; (2) AI report prompt scaffolding includes 🤖 as a literal "I am the AI analyst" marker. Treat both as out-of-bounds for the UI itself.

**Unicode — used as iconography only when it carries meaning.** Triangular signal arrows (`▲`, `▼`, `◆`) appear in `StrategyCards.tsx` as buy/sell/hold markers; the AAOIFI compliance pass uses a literal checkmark glyph. Don't reach for unicode decoratively — if it's a symbol the user has to recognize, use a Font Awesome glyph instead (see Iconography).

**Numbers — always tabular.** Every number in the product is rendered in JetBrains Mono with `font-feature-settings: "tnum" 1;` and `font-variant-numeric: tabular-nums`. Currency uses `$` prefix and two decimals by default. Percentages always carry a sign (`+1.2%`, `-0.4%`). Large currency values are abbreviated to thousands with `K` suffix (`$12.4K`) only in tight summary cards — full precision everywhere else.

**Copy specimens — direct from source:**

- Brand line: *ميزان كوانت · mizanquant* (`telegram_alert.py:42`)
- Sidebar tagline: *Halal Trading Suite*
- Status footer: *Broker · Alpaca · Auto-trade ON · Regime BULL · Pipeline idle · Kill-switch armed*
- Empty state: *Select a signal to analyze*
- AI agent system prompt opener (Arabic): *"أنت محلل مالي إسلامي خبير…"* — "You are an expert Islamic financial analyst…"

---

## Visual Foundations

**Backgrounds — warm near-black, four-tier ramp.** Root canvas is `#0c0c10` (a hair warmer than neutral charcoal — that warmth is intentional, it pairs with the gold accent). Three elevated surfaces layer up: `#14141a` (main workspace), `#1c1c26` (cards), `#242430` (modal/overlay). No full-bleed imagery, no gradients, no textures, no hand-drawn illustration, no repeating patterns. The product is a dashboard — the background's job is to disappear.

**Accent — warm gold, used sparingly.** `#c8963e` is the only accent. It appears in: the brand mark, primary CTAs (`Run pipeline`, `Send to paper trade`), active nav state, focus rings, links, and the `::selection` highlight. Hover lifts to `#d4a853`. A dimmed variant `rgba(200,150,62,0.10)` is the universal "active" background tint (sidebar item, selected signal card, primary button base). **Never** use gold for state communication — that's what the semantic ramp is for.

**Semantic colors — directional and load-bearing.** Green (`#4ade80`) is always positive (buy, profit, system OK, halal-pass). Red (`#f87171`) is negative (sell, loss, error, halal-fail). Amber (`#fbbf24`) is wait/warning/caution. Blue-info (`#60a5fa`) is metric-neutral. Each ships paired with a `-dim` variant at ~10% alpha for background washes — `b-green` (green text on green-10% background) is a constant pattern in badges.

**Type — DM Sans display, system body, JetBrains Mono numerals.** DM Sans (variable, weights 400/500/600/700) carries every brand surface and UI title. System font (`system-ui, -apple-system, 'Segoe UI', sans-serif`) carries body copy — it's intentional that body looks native, terminal-like, not "designed". JetBrains Mono handles every numeric value, ticker symbol, price, timestamp, and code block. Mixing the three within a single line is the rule, not the exception: `Sharpe 1.84` is DM Sans `Sharpe` + JetBrains Mono `1.84`. Type scale is a tight minor third (×1.2) — UI runs SMALL (13px body, 22px max for page titles) because density is a feature.

**Spacing — 4px base.** All paddings, margins, and gaps come from a 4-step scale: 4, 6, 8, 10, 12, 16, 20, 24, 32, 40. The two most common values are 12px (card padding) and 6px (gap between sibling tiles). Pages have 16–24px outer gutter. Vertical rhythm is loose at the page level (24px between major sections) and tight inside cards (3–4px between rows).

**Borders — white-on-dark hairlines, three weights.** All borders are RGBA white at 4%, 6%, or 10% alpha. Default is 6%. 4% is for inner dividers (e.g. between rows inside a card). 10% is for stronger separations (selected state, focus). Border-color is the *only* thing used to communicate selection on most cards — there is no colored fill, just a `border-color: var(--accent)` swap.

**Radii — 3 / 5 / 8 / 12.** 5px is the default. 3px for badges and small tags. 8px for cards and modals. 12px for hero panels and overlays. Pills (regime chip, halal flag) override with `border-radius: 12px` capsule.

**Shadows — barely there.** There is exactly one elevation pattern in the codebase: a subtle drop-shadow on toasts and modal overlays (`box-shadow: 0 8px 24px rgba(0,0,0,0.4)`). Inline cards do NOT use shadows for elevation — they use the background ramp + the border-light hairline. This is critical to the dense, flat aesthetic.

**Hover / press states — restrained.** Hover almost always means: (a) lift `color` from `--text-secondary` to `--text-primary`, OR (b) lift `background` to the next surface in the ramp. The signal cards do a `translateY(-1px)` lift, but that's the only place. Buttons darken/lighten by ~10%. No scale-down on press. No glow rings. No big transforms.

**Selection — accent-bordered, accent-dim filled.** When a user picks a signal card or table row, the border becomes `var(--accent)` and the background becomes `var(--accent-dim)`. That's it — no glow, no checkmark, no badge.

**Transparency & blur — rare.** Used only for the modal overlay scrim (`background: rgba(0,0,0,0.5)`) and the dim-variants of accent/semantic colors. No `backdrop-filter`. No glass-morphism.

**Loading states — pulse, not shimmer.** A canonical comment in `dashboard.html` reads: *"No shimmer — DESIGN.md anti-pattern. Use pulse only."* `@keyframes pulse` cycles opacity 1 → 0.35 → 1 on a 1.5s ease-in-out. The skeleton class in older screens used shimmer; remove it on touch.

**Layout rules — fixed shell, scrolling workspace.** Sidebar (left, 56px collapsed / 200px hover-expanded), command bar (top, 48px), market-context strip (top, 36px), status bar (bottom, 28px) are all `position: fixed`. Everything else lives in the main area with normal flow. The three-column workflow grid uses `grid-template-columns: 2.5fr 1.8fr 1.8fr` — middle "Analyze" column is `position: sticky` so it stays in view while the long right "Trade" column scrolls.

**Iconography vibe — Font Awesome solid, 13–16px, current-color.** No custom illustration. No 3D, no isometric, no gradient-filled glyphs. Icons are part of the UI grammar — adjacent to labels in nav, prefixed to empty-state messages, indicating sort direction. See Iconography.

**Imagery — none.** This is a data product. There are no photographs, hero images, illustrations, or stock graphics anywhere in the source. The closest things are: SVG sparklines (data), conic/ring SVGs for score gauges (data), and the SVG logos (brand). Adding decorative imagery is off-brand.

**Animation — short, ease-out, mostly state transitions.** The motion vocabulary is intentionally tiny:
- 120ms (`--duration-fast`) for hover color/background swaps
- 200ms (`--duration-normal`) for sidebar expand, panel open
- 350ms (`--duration-slow`) for the slide-in side panel
- ease-out (`cubic-bezier(0.16, 1, 0.3, 1)`) for entrances
- pulse (1.5s ease-in-out) for live/loading dots
- spin (0.8s linear) for loading spinners

No bounces, no springs, no parallax, no scroll-jacking. Status dots that should pulse get a `.pulse` class — everything else is a step function.

**Color vibe of overall product — cool-warm split.** The canvas is warm (the dark tones have a faint brown/amber undertone, not blue-gray). The data viz is cool-coded (green/red/amber/blue). The accent is warm gold. The deliberate mix — warm chrome, cool data — is what gives the product its "premium pro" feel without leaning on flashy color.

---

## Iconography

**System: Font Awesome 6 Free Solid.** Bundled in the codebase at `app/static/fontawesome/` (CSS + webfonts). Loaded via `<link rel="stylesheet" href="/static/fontawesome/all.min.css">`. Every glyph is referenced by class — `<i class="fas fa-th-large"></i>`, `<i class="fas fa-brain"></i>`, etc. Color is always `currentColor` so they inherit from the surrounding text.

For artifacts created outside the production build (slides, mocks, prototypes), load Font Awesome from CDN to match exactly:

```html
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">
```

**Canonical glyph vocabulary** — these icons recur across the product and form the visual lexicon. Reuse them; don't invent new mappings:

| Concept | Class | Notes |
| --- | --- | --- |
| Overview / dashboard | `fa-th-large` | Sidebar root |
| Forecast Lab | `fa-brain` | ML / AI |
| Trading Lab | `fa-robot` | Algo trading |
| Backtest history | `fa-history` | |
| Risk Desk | `fa-shield-alt` / `fa-shield-halved` | Defensive theme |
| Charts | `fa-chart-bar`, `fa-chart-line`, `fa-chart-area` | |
| Research | `fa-search-dollar` | |
| Halal Screener | `fa-mosque` | Religious-compliance, no other use of this glyph |
| AI Assistant | `fa-robot` (shares icon with Trading Lab — context disambiguates) |
| Refresh | `fa-sync-alt` | |
| Search | `fa-search` | |
| Run / play | `fa-play` | Used on pipeline-run button |
| Vote / consensus | `fa-vote-yea` | |

**Logos & marks.** Three SVGs in `assets/`:
- `logo-monogram.svg` (64×64) — the **mīm-trace**: a ring (lens / scale weight / Arabic letter mīm) connected by a chart-line path to an ascending dot. Use anywhere the brand needs to appear standalone.
- `logo-wordmark.svg` (280×80) — horizontal lockup: mark + `mizan`*`quant`* + "HALAL TRADING SUITE" tagline.
- `logo-favicon.svg` (32×32) — simplified for favicon use.

All three are pure SVG with `currentColor`-friendly strokes (defined in oklch, semantically the green accent in the *deploy/* assets — they pre-date the gold rebrand and should be re-tinted to gold `#c8963e` for production usage on dark backgrounds; both variants are valid).

**Sparklines, gauges, heatmaps — inline SVG, hand-rolled.** The dashboard generates these on the fly in `overview-v2.js` (functions `sparkSvg`, `ringSvg`, etc). Stroke widths are `1.2px` (sparkline) to `3px` (score ring). All sized to fit their container — never absolute pixels.

**No icon font beyond Font Awesome. No emoji. No image-based icons.** A single SVG file in `strategies-app/public/icons.svg` contains the default Vite social icons (Bluesky, Discord, GitHub, X) — these are unused in the product and should be ignored.

---

## File index

```
/
├── README.md                    ← you are here
├── SKILL.md                     ← agent skill manifest (for Claude Code)
├── colors_and_type.css          ← single source of truth for tokens
│
├── assets/
│   ├── logo-monogram.svg
│   ├── logo-wordmark.svg
│   └── logo-favicon.svg
│
├── preview/                     ← Design System tab cards
│   ├── color-primary.html
│   ├── color-semantic.html
│   ├── color-text.html
│   ├── color-borders.html
│   ├── type-display.html
│   ├── type-mono.html
│   ├── type-scale.html
│   ├── type-eyebrow.html
│   ├── spacing-radii.html
│   ├── spacing-elevation.html
│   ├── components-buttons.html
│   ├── components-badges.html
│   ├── components-table.html
│   ├── components-card-signal.html
│   ├── components-card-metric.html
│   ├── components-input.html
│   ├── components-sparkline-ring.html
│   ├── components-nav.html
│   ├── brand-monogram.html
│   ├── brand-wordmark.html
│   └── brand-iconography.html
│
└── ui_kits/
    ├── mizanquant_terminal/      ← flagship Overview dashboard
    │   ├── README.md
    │   ├── index.html
    │   ├── styles.css
    │   ├── shared.jsx
    │   ├── Sidebar.jsx
    │   ├── CommandBar.jsx
    │   ├── ScanColumn.jsx
    │   ├── AnalyzeColumn.jsx
    │   ├── TradeColumn.jsx
    │   ├── LowerRow.jsx
    │   ├── StatusBar.jsx
    │   └── App.jsx
    ├── risk_desk/                ← VaR · Kelly · system blocks · stress tests
    │   ├── README.md
    │   ├── index.html
    │   ├── styles.css
    │   ├── shared.jsx
    │   ├── RiskHeader.jsx
    │   ├── VaRMetrics.jsx
    │   ├── KellySizing.jsx
    │   ├── BlockToggles.jsx
    │   ├── PositionRisk.jsx
    │   ├── StressTests.jsx
    │   ├── RiskAlerts.jsx
    │   └── App.jsx
    └── halal_screener/           ← AAOIFI 4-screen compliance + sector filters
        ├── README.md
        ├── index.html
        ├── styles.css
        ├── shared.jsx
        ├── ScreenerHeader.jsx
        ├── GateBadges.jsx
        ├── AAOIFIScreens.jsx
        ├── SectorFilters.jsx
        ├── ComplianceTable.jsx
        ├── DetailPanel.jsx
        └── App.jsx
```
