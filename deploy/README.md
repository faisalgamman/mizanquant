# Deploying the mizanquant rebrand + new Overview

This kit does **two** things to your existing mizanquant repo:

1. **Rebrand** — Telegram alerts and the AI agent now say *ميزان كوانت*, the sidebar shows the new mīm-trace logo, the page title and favicon are mizanquant.
2. **Replace the Overview tab** — your current dashboard is preserved at `/static/dashboard-legacy.html` (all tabs intact), and `/static/dashboard.html` is now a redesigned Overview matching the UI Kit, wired to your real `/api/v1/*` endpoints.

## What's in here

| File | Purpose |
| ----- | ----- |
| `apply-rebrand.py` | One script does it all. Idempotent. |
| `static/overview-v2.html` | The new Overview page (drop-in for dashboard.html). |
| `static/overview-v2.js` | Vanilla JS that wires it to your `/api/v1/*` endpoints. |
| `static/logo-monogram.svg` | Mīm-trace mark, transparent. 64×64. |
| `static/logo-wordmark.svg` | Horizontal lockup. 280×80. |
| `static/logo-favicon.svg` | Simplified favicon. 32×32. |

## What the new Overview shows

A faithful implementation of the design system:

- **3-column workflow** — Scan (Market strip · Regime · Signals table) · Analyze (sticky middle column) · Trade (Portfolio · Positions · Paper · Pipeline · Guards · Schedule)
- **Lower row** — Models leaderboard · Sectors heatmap with halal flagging · AI Consensus
- **Click any signal** → Analyze panel fetches `/api/v1/scoring/weighted`, `/api/v1/trade/plan`, `/api/v1/halal/check` in parallel and renders score breakdown + trade plan
- **"Send to paper trade"** → POSTs to `/api/v1/paper/execute`
- **"Run AI consensus"** → calls `/consensus` and renders the 14-tool vote
- **"Run pipeline"** → calls `/api/v1/pipeline/run?dry_run=true&strategy=ABC`
- **Auto-refreshes every 30s**

The sidebar links to the other tabs (Forecast Lab, Trading Lab, Backtest, Risk Desk, etc.) point at `/static/dashboard-legacy.html#tab-…` — your existing app is **fully preserved**.

## How to apply

```bash
# Clone (or cd into) your mizanquant repo
git clone https://github.com/faisalgamman/mizanquant.git
cd mizanquant

# Drop this whole `deploy/` folder into the repo root, then:
python deploy/apply-rebrand.py
```

You'll see something like:

```
=== mizanquant rebrand ===

→ app/static/dashboard.html
  · already rebranded (marker present)

→ app/services/telegram_alert.py
  ✓ _TELEGRAM_PHOTO_API = "https://…
  ✓ {_utc_label()} UTC  ×5
  ✓ 🕐 {datetime.now(timezone.utc).strftime…  ×3
✓ app/services/telegram_alert.py  (9 changes, backup → telegram_alert.py.bak)

→ app/ai_agent.py
  ✓ "أنت محلل مالي إسلامي خبير…
  ✓ "أنت مستشار استثماري…
  ✓ 🤖 تم إعداد هذا التقرير…
  ✓ أنا **المحلل الذكي** لمنصة OpenBB Forecast.
✓ app/ai_agent.py  (4 changes, backup → ai_agent.py.bak)

→ app/static/dashboard.html  (replace with new Overview)
  ✓ Preserved original as dashboard-legacy.html
  ✓ Installed new mizanquant Overview as dashboard.html

→ app/static/  (logo SVGs + overview-v2.js)
  ✓ logo-favicon.svg
  ✓ logo-monogram.svg
  ✓ logo-wordmark.svg
  ✓ overview-v2.js

=== Done · 14 patch(es) applied ===
```

## Verify locally

```bash
python -c "from halal_screener import app"
uvicorn halal_screener:app --reload
```

- `http://localhost:8000/static/dashboard.html` → new mizanquant Overview
- `http://localhost:8000/static/dashboard-legacy.html` → full legacy app

## Ship to Railway

```bash
git add app/ deploy/
git commit -m "rebrand: mizanquant identity + new Overview"
git push
```

## Rolling back

```bash
# Restore the original dashboard
mv app/static/dashboard-legacy.html        app/static/dashboard.html
rm app/static/overview-v2.js

# Restore Telegram + AI from backups
mv app/services/telegram_alert.py.bak      app/services/telegram_alert.py
mv app/ai_agent.py.bak                     app/ai_agent.py

# Remove logos (optional)
rm app/static/logo-{monogram,wordmark,favicon}.svg
```

## Notes / caveats

- **Models leaderboard** in the lower row tries `/api/v1/models/leaderboard` first; if that endpoint doesn't exist, it falls back to a static list of the model families your codebase ships (Sharpe shows "—" for unknown values). Add a backend endpoint with the same name when convenient and it'll pick up live data.
- **AI Consensus** is a heavy endpoint (`/consensus`) — it loads only when you click "Run AI consensus" in the Analyze panel, never automatically.
- **Top signals** come from `/api/v1/overview` (`top_signals` array from `ConsensusLog`) merged with `/screener` for the long table.
- **Paper trade button** sends a $1k position by default — adjust in the JS (`Math.floor(1000 / entry)`) if you want a different sizing rule.
