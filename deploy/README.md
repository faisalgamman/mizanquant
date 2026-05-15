# Deploying the mizanquant rebrand to Railway

This kit applies the **mizanquant** brand identity to your trading dashboard, Telegram alerts, and the bilingual AI analyst — then ships to Railway.

## What's in here

| File | Purpose |
| ----- | ----- |
| `apply-rebrand.py` | Idempotent Python script that patches three files in your repo. |
| `static/logo-monogram.svg` | Mīm-trace mark, transparent background. 64×64. |
| `static/logo-wordmark.svg` | Horizontal lockup: monogram + `mizanquant` wordmark. 280×80. |
| `static/logo-favicon.svg` | Simplified favicon, 32×32, optimized for 16/32 px. |

## What the script touches

| File | Change |
| ----- | ----- |
| `app/static/dashboard.html` | Page `<title>` → *mizanquant — Halal Trading Suite*. Favicon link added. Sidebar logo → inline mīm-trace SVG. Sidebar wordmark → `mizanquant / Halal Trading Suite`. |
| `app/services/telegram_alert.py` | Inserts `_BRAND_LINE = "ميزان كوانت · mizanquant"` constant. Prefixes every alert timestamp footer with it — so each notification ends with `ميزان كوانت · mizanquant\n2026-05-15 14:32 UTC`. |
| `app/ai_agent.py` | Both Arabic system prompts now introduce the model as *المحلل المالي في منصة ميزان كوانت*. Template report footer + "hello" reply rebranded from *OpenBB Forecast* to *ميزان كوانت*. |
| `app/static/logo-*.svg` | The three logo files copied in. |

Each touched file gets a `.bak` backup the first time.

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
  ✓ <title>OpenBB Forecast — Trading Dashboard</title>…
  ✓ <div class="logo"><i class="fas fa-chart-line"></i></div>…
  ✓ <div class="sidebar-brand-text"><h2>USX PRO</h2>…
✓ app/static/dashboard.html  (3 change(s), backup → dashboard.html.bak)

→ app/services/telegram_alert.py
  ✓ _TELEGRAM_PHOTO_API = "…"…
  ✓ {_utc_label()} UTC…
  ✓ 🕐 {datetime.now(timezone.utc)…
✓ app/services/telegram_alert.py  (3 change(s), backup → telegram_alert.py.bak)

→ app/ai_agent.py
  ✓ "أنت محلل مالي إسلامي خبير. مهمتك هي تحليل بيانات الفرص الاستثمارية "…
  ✓ "أنت مستشار استثماري إسلامي خبير. أجب على أسئلة المستثمرين "…
  ✓ 🤖 تم إعداد هذا التقرير بواسطة **المحلل الذكي** | OpenBB Forecast…
  ✓ أنا **المحلل الذكي** لمنصة OpenBB Forecast.…
✓ app/ai_agent.py  (4 change(s), backup → ai_agent.py.bak)

→ app/static/  (logo SVGs)
  ✓ logo-favicon.svg
  ✓ logo-monogram.svg
  ✓ logo-wordmark.svg

=== Done · 10 patch(es) applied ===
```

## Verify locally

```bash
# Smoke test — make sure imports still parse
python -c "from halal_screener import app"

# Run the app however you normally do
uvicorn halal_screener:app --reload
```

Visit `http://localhost:8000/static/dashboard.html` — sidebar should show the new mīm-trace mark and the `mizanquant / Halal Trading Suite` wordmark.

If you have a Telegram test setup, fire a buy-signal alert and you should see the new footer.

## Ship to Railway

```bash
git add app/ deploy/
git commit -m "rebrand: mizanquant identity"
git push
```

If your Railway project is connected to this branch, it auto-deploys. Otherwise:

```bash
# One-time: npm i -g @railway/cli && railway login
railway up
```

## Rolling back

```bash
mv app/static/dashboard.html.bak              app/static/dashboard.html
mv app/services/telegram_alert.py.bak         app/services/telegram_alert.py
mv app/ai_agent.py.bak                        app/ai_agent.py
rm app/static/logo-{monogram,wordmark,favicon}.svg
```

## Optional next steps

- **Rename the Telegram bot** — `@BotFather` → `/setname` → `mizanquant signals`.
- **Rename the Railway project** — open the project on railway.app, settings, rename to `mizanquant`.
- **Update the GitHub repo description** — *"Halal algorithmic trading suite — mizan (the scale of justice), quantified."*
- **Set the AI agent's tagline in Telegram** — `@BotFather` → `/setdescription`.
