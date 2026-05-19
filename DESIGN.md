# 🎨 DESIGN.md — Mizanquant

> **Format**: Google Stitch DESIGN.md (Impeccable-compatible)
> **Version**: 2.0.0 — "Terminal" redesign
> **Status**: Active · replacing v1 "Inter + Green" dashboard

---

## 1. PRODUCT — What we're designing

Mizanquant is a **Halal algorithmic trading platform**. It screens US equities for Shariah compliance, runs 18 deep-learning forecast models, executes paper-trades via IBKR, and presents a real-time dashboard for traders.

**Product surfaces covered by this spec:**
- `/dashboard` — Main trading overview (signals, portfolio, positions, pipeline)
- `/screener` — Halal stock screener
- `/consensus` — Model consensus view
- `/forecast/{model}` — Individual model drill-down
- Future: `/risk` desk, `/backtest` lab

---

## 2. AUDIENCE — Who sees this

| Persona | Primary need | Screen time |
|---|---|---|
| **Quant trader** (Faisal) | Scan signals → analyze → execute. Needs speed & data density. | 6–10 hrs/day |
| **Portfolio manager** | Monitor positions, P&L, risk guards. Reviews daily. | 1–2 hrs/day |
| **Researcher** | Explore model leaderboard, backtest, halal compliance. | Ad-hoc |

**Key insight**: The primary user spends all day in this dashboard. Eye strain matters. Data precision matters. Speed of scanning matters more than visual decoration.

---

## 3. BRAND — Personality & voice

| Dimension | Value |
|---|---|
| **Archetype** | The Sage + The Guardian |
| **Tone** | Precise, calm, trustworthy. Never flashy. Never "startup-y". |
| **Cultural context** | Islamic finance — values transparency, fairness, avoidance of speculation/gharar. Design should feel grounded, not gamified. |
| **Aspirational references** | Bloomberg Terminal (data density), Linear (precision), Coinbase (trust) |
| **Anti-references** | Robinhood (gamified), crypto exchanges (neon/flashy), generic SaaS (purple gradients + Inter) |

---

## 4. ANTI-REFERENCES — What we deliberately avoid

| Pattern | Why we avoid it |
|---|---|
| Inter font | #1 AI-design cliché. Overused to the point of invisibility. |
| Green-on-black "Matrix" aesthetic | Every trading app does this. Not distinctive. |
| Card-grid layouts | Hides data behind clicks. Trading needs visible data. |
| Purple/blue gradient accents | Crypto-exchange aesthetic. Wrong trust signal for Islamic finance. |
| Glassmorphism / frosted glass | Distracting, reduces legibility for data-dense UIs. |
| Skeleton loaders (shimmer) | Generic placeholder. Better: pulse dots or inline spinners. |
| Emoji in UI | Not professional for finance. |
| Fake metrics / decorative stats | Every number must be real and actionable. |

---

## 5. DESIGN TOKENS

### 5.1 Color

```css
:root {
  /* Backgrounds — warm near-black (not cold blue-black) */
  --bg-root:       #0c0c10;
  --bg-surface:    #14141a;
  --bg-raised:     #1c1c26;
  --bg-overlay:    #242430;

  /* Borders */
  --border-subtle:  rgba(255, 255, 255, 0.04);
  --border-default: rgba(255, 255, 255, 0.06);
  --border-strong:  rgba(255, 255, 255, 0.10);

  /* Text */
  --text-primary:    #e4e4ec;
  --text-secondary:  #9494a0;
  --text-muted:      #5c5c6a;
  --text-disabled:   #3c3c48;

  /* Accent — Warm Gold (Islamic geometric art reference) */
  --accent:          #c8963e;
  --accent-hover:    #d4a853;
  --accent-dim:      rgba(200, 150, 62, 0.10);
  --accent-glow:     rgba(200, 150, 62, 0.18);

  /* Semantic */
  --positive:        #4ade80;
  --positive-dim:    rgba(74, 222, 128, 0.10);
  --negative:        #f87171;
  --negative-dim:    rgba(248, 113, 113, 0.10);
  --warning:         #fbbf24;
  --warning-dim:     rgba(251, 191, 36, 0.10);
  --info:            #60a5fa;
  --info-dim:        rgba(96, 165, 250, 0.10);
}
```

### 5.2 Typography

```css
:root {
  --font-display: 'DM Sans', system-ui, -apple-system, sans-serif;
  --font-body:    system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-mono:    'JetBrains Mono', 'SF Mono', 'Fira Code', ui-monospace, monospace;

  /* Scale (minor third: 1.2) */
  --text-xs:   0.625rem;   /* 10px — labels, badges */
  --text-sm:   0.75rem;    /* 12px — secondary data */
  --text-base: 0.8125rem;  /* 13px — body, tables */
  --text-md:   0.9375rem;  /* 15px — section titles */
  --text-lg:   1.125rem;   /* 18px — panel headers */
  --text-xl:   1.375rem;   /* 22px — price display */
  --text-2xl:  1.625rem;   /* 26px — major metrics */
}
```

### 5.3 Spacing (4px base)

```
4, 8, 12, 16, 20, 24, 32, 40, 48, 64
```

### 5.4 Radii

```css
--radius-none: 0;
--radius-sm:   3px;   /* inline badges */
--radius-md:   5px;   /* buttons, inputs */
--radius-lg:   8px;   /* cards, panels */
--radius-xl:   12px;  /* modals */
```

### 5.5 Motion

```css
--ease-out:    cubic-bezier(0.16, 1, 0.3, 1);
--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
--duration-fast:    120ms;
--duration-normal:  200ms;
--duration-slow:    350ms;
```

---

## 6. COMPONENT SPECS

### 6.1 Data Row (table row)
- Hover: `background: var(--bg-raised);` + subtle left accent border
- Selected: `background: var(--accent-dim); border-left: 2px solid var(--accent)`
- Click target: full row, min-height 32px
- Monospace for all numeric columns (tabular-nums)

### 6.2 Status Indicator
- Small dot: 6px diameter
- Live pulse: ring animation (not skeleton shimmer)
- States: green (connected), amber (degraded), red (disconnected)

### 6.3 Command Palette (Ctrl+K)
- Dark overlay background
- Search input at top
- Results grouped by category
- Keyboard navigation (↑↓ Enter Esc)

### 6.4 Section Header
- Collapsible chevron on right
- Uppercase label, letter-spacing: 0.5px
- Subtle bottom border

### 6.5 Price Display
- Large monospace, tabular-nums
- Change percentage in colored badge
- Positive: green text, no background unless significant
- Negative: red text

---

## 7. LAYOUT

```
┌──────────────────────────────────────────────────────┐
│  NAV BAR: Mizanquant · Overview · [Search ⌘K] · ⚡   │  ← 44px
├──────────────────────────────────────────────────────┤
│  MARKET: SPY ▲0.4% · VIX 15.2 · Breadth 62% · ...   │  ← 32px
├──────────────────────────────────────────────────────┤
│                     MAIN GRID                         │
│  ┌──────────┐ ┌──────────────┐ ┌──────────────────┐  │
│  │ SIGNALS  │ │   ANALYZE    │ │   PORTFOLIO      │  │
│  │ (table)  │ │   (detail)   │ │   POSITIONS      │  │
│  │          │ │              │ │   PAPER          │  │
│  │          │ │              │ │   PIPELINE       │  │
│  │          │ │              │ │   GUARDS         │  │
│  └──────────┘ └──────────────┘ └──────────────────┘  │
├──────────────────────────────────────────────────────┤
│  MODELS (leaderboard) · SECTORS (heatmap) · NEWS     │
├──────────────────────────────────────────────────────┤
│  STATUS BAR: ⬤ Connected · IBKR Live · ET 14:32:05  │  ← 24px
└──────────────────────────────────────────────────────┘
```

---

## 8. INTERACTION RULES

- **Keyboard-first**: Tab between sections, ↑↓ within tables, Enter to select, Esc to close panels
- **Real-time updates**: Market bar refreshes every 5s, positions every 10s, without full-page reload
- **Loading states**: Inline data replacement (not full-page spinners). Previous data stays visible until new data arrives.
- **Empty states**: Show "No signals match filters" with clear reset action. Not decorative illustrations.
- **Error states**: Inline error banner at top, auto-dismiss after restore.

---

## 9. RESPONSIVE BREAKPOINTS

| Breakpoint | Behavior |
|---|---|
| 1600px+ | Full 3-column main grid + lower row |
| 1200–1600px | 3-column → 2-column (analyze panel becomes overlay) |
| 768–1200px | 2-column → single column, sections stacked |
| <768px | Single column, simplified tables, collapsible sections |
