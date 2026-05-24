# Halal Screener — UI Kit

A hi-fi click-thru recreation of the MizanQuant **Halal Screener** surface, rebuilt in the unified post-rebrand palette (warm gold on warm near-black, DM Sans + JetBrains Mono, Cairo for Arabic). Replaces the legacy blue/green screen at `mizanquant/app/static/halal-screener.html`.

## What's here

| File | Component |
| --- | --- |
| `index.html` | Composed entry — open this |
| `App.jsx` | Top-level state + mock universe + filter logic |
| `shared.jsx` | Mock halal universe + AAOIFI screen helpers |
| `ScreenerHeader.jsx` | Title + Run Smart Scan button + last-scanned timestamp |
| `GateBadges.jsx` | Market gate row: regime, min/strong gates, RISK-ON state |
| `SummaryCards.jsx` | Six-cell summary strip: scanned · halal · qualified · watch · results · min gate |
| `AAOIFIScreens.jsx` | Four AAOIFI screen visualizations: debt · interest income · cash · haram industries |
| `SectorFilters.jsx` | Dynamic sector chips with halal-pass counts |
| `ComplianceTable.jsx` | Per-symbol AAOIFI verdict table with score bars and 4-screen pill |
| `DetailPanel.jsx` | Side panel that opens on row click — full screen breakdown for a symbol |

## What interacts

- **Click "Run Smart Scan"** → progress bar fills 0 → 100% over ~2.4s, then table renders
- **Sector chip** → filters the table to that sector
- **Score / signal / halal filter** → updates the table and result count
- **Click a table row** → side panel slides in with full AAOIFI screen breakdown
- **Sort column** → toggles ascending / descending

## AAOIFI screens shown

| # | Screen | Threshold |
| --- | --- | --- |
| 1 | Debt / Market Cap | < 33% |
| 2 | Interest Income / Revenue | < 5% |
| 3 | Cash + Receivables / Market Cap | < 33% |
| 4 | Haram industries | excluded list (alcohol, gambling, conventional banking, pork, defense, adult, tobacco) |

A symbol passes if and only if **all four** screens pass. Failure on any single screen flips the verdict to **Haram**.

## What it mirrors in production

- `mizanquant/app/static/halal-screener.html` — the screen UI (legacy palette, this kit replaces it)
- `mizanquant/app/services/halal_screening.py` — the AAOIFI screening logic
- `mizanquant/app/services/groq_assistant.py` — the LLM analyst tooling

This is a UI kit. All values are mocked. Real wiring lives in `/api/stock/smart-screener` and `/api/halal-status` per the FastAPI app.
