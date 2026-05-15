# Deploying the mizanquant rebrand to Railway

This folder contains everything you need to apply the **mizanquant** brand identity to your existing trading dashboard and ship it to Railway.

## What's in here

| File | Purpose |
| ----- | ----- |
| `apply-rebrand.py` | Idempotent Python script that patches `app/static/dashboard.html` in your repo. |
| `static/logo-monogram.svg` | Mīm-trace mark, transparent background. 64×64. |
| `static/logo-wordmark.svg` | Horizontal lockup: monogram + `mizanquant` wordmark. 280×80. |
| `static/logo-favicon.svg` | Simplified favicon, 32×32, optimized for 16/32 px. |

## How to apply it

```bash
# Clone (or cd into) your mizanquant repo
git clone https://github.com/faisalgamman/mizanquant.git
cd mizanquant

# Drop this whole `deploy/` folder into the repo root, then run:
python deploy/apply-rebrand.py
```

The script will:

1. **Back up** `app/static/dashboard.html` → `dashboard.html.bak`
2. **Replace** the page `<title>`, add a favicon `<link>`, and update the sidebar logo + wordmark
3. **Copy** the three SVGs into `app/static/`

It's idempotent — running it twice is a no-op.

## Verify locally

```bash
# From the repo root, however you normally run the app
uvicorn halal_screener:app --reload
```

Visit `http://localhost:8000/static/dashboard.html` — the sidebar should show the new mīm-trace mark and the `mizanquant / Halal Trading Suite` wordmark.

## Ship to Railway

```bash
git add app/static/ deploy/
git commit -m "rebrand: mizanquant identity"
git push
```

Railway will auto-deploy if your project is connected to this branch. Otherwise:

```bash
# Install once: npm i -g @railway/cli && railway login
railway up
```

## If you want to roll back

```bash
mv app/static/dashboard.html.bak app/static/dashboard.html
rm app/static/logo-*.svg
git checkout -- app/static/
```

## What else to update (optional)

The rebrand script touches only the **dashboard chrome**. If you also want to:

- **Change the project name in Railway** — open the project settings on railway.app and rename it.
- **Rename the Telegram bot** — talk to `@BotFather`, `/setname`, `mizanquant signals`.
- **Update Telegram alert headers** — in `app/services/telegram_alert.py`, the `alert_strong_signal` template uses `[BUY]` etc. — no brand text to swap, but you can add a `mizanquant · halal trading` footer line.
- **Update the AI agent's Arabic system prompt** — `app/ai_agent.py` has the system prompt for the Islamic analyst persona. You can append a single line introducing it as ميزان كوانت.
