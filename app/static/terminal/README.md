# MizanQuant Terminal — UI Kit

A hi-fi click-thru recreation of the MizanQuant Overview dashboard. Reflects the post-rebrand canon (warm gold accent on warm near-black, DM Sans + JetBrains Mono, Font Awesome 6 Solid icons).

## What's here

| File | Component |
| --- | --- |
| `index.html` | Composed app shell + entry — open this to see the live UI |
| `App.jsx` | Top-level state, signal selection, mock data |
| `shared.jsx` | Reusable primitives: `Badge`, `Ring`, `Sparkline`, `Spark`, `mock data` |
| `Sidebar.jsx` | Left nav, 56px collapsed / 200px hover-expanded |
| `CommandBar.jsx` | Top command bar with search |
| `MarketStrip.jsx` | Market context strip (VIX, SPY, Breadth, Credit, Liq) |
| `ScanColumn.jsx` | Column 1: market cards, regime, signal hero trio, filterable table |
| `AnalyzeColumn.jsx` | Column 2: sticky analysis panel for the selected signal |
| `TradeColumn.jsx` | Column 3: portfolio, positions, paper, pipeline, guards, schedule |
| `LowerRow.jsx` | Models leaderboard, sectors heatmap, consensus |
| `StatusBar.jsx` | Bottom status footer |

## What interacts

- **Click a signal card or table row** → the Analyze panel fills with score breakdown + trade plan; the Consensus panel fills with 14-model votes
- **Run pipeline** → stages animate `done → running → pending` over a few seconds
- **Send to paper trade** → toast confirmation
- **Hover sidebar** → expands to 200px showing labels

## What's faked

This is a UI kit, not a working trading app. All data is static (`mockSignals`, `mockPortfolio`, etc. in `shared.jsx`). Real production wiring lives in `mizanquant/deploy/static/overview-v2.js` (vanilla JS) and `mizanquant/strategies-app/src/` (React). Use those as the source of truth for actual API contracts.

## Coverage gap

The lower-row "Consensus" panel here only animates on signal-select; the production button-triggered `/consensus` call (heavy LLM round-robin across 14 tools) is intentionally not modelled.
