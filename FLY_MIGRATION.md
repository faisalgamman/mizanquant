# Migrating mizanquant: Railway → Fly.io

Move hosting off Railway to Fly.io (cheaper, predictable, managed). The app is already a single
Dockerized FastAPI service, so this is mostly **config + a data move**. IB Gateway is **deferred**
(the live IBKR portfolio panel goes offline until re-added; auto-trade is OFF, trading is manual).

Railway is NOT stopped by this — it keeps running (and billing) until **you delete it** at the end.

---

## 0. Prerequisites (your machine)
- Install flyctl: `iwr https://fly.io/install.ps1 -useb | iex` (Windows) → `fly auth login`.
- Have `pg_dump` / `pg_restore` (PostgreSQL client tools) installed.
- Create a free managed Postgres → copy its connection string as the NEW `DATABASE_URL`:
  - **Neon** (https://neon.tech) or **Supabase** (https://supabase.com) free tier.

## 1. Create the Fly app + volume (uses the committed `fly.toml`)
```
fly apps create mizanquant            # or `fly launch --no-deploy --copy-config` and keep fly.toml
fly volumes create data --size 3 --region iad
```

## 2. Set secrets (copy the EXACT values from Railway → Variables; never invent)
A missing key silently degrades FMP/Alpaca/DeepSeek — port ALL of these:
```
fly secrets set \
  DATABASE_URL="<NEW Neon/Supabase URL>" \
  ALPACA_API_KEY=... ALPACA_SECRET_KEY=... ALPACA_BASE_URL="https://paper-api.alpaca.markets" \
  ALPACA_DATA_KEY=... ALPACA_DATA_SECRET=... \
  ALPACA_API_KEY_A=... ALPACA_SECRET_KEY_A=... ALPACA_API_KEY_B=... ALPACA_SECRET_KEY_B=... \
  ALPACA_API_KEY_C=... ALPACA_SECRET_KEY_C=... \
  FMP_API_KEY=... FRED_API_KEY=... TIINGO_TOKEN=... \
  DEEPSEEK_API_KEY=... DEEPSEEK_MODEL="deepseek-chat" \
  ANTHROPIC_API_KEY=... CLAUDE_MODEL="claude-sonnet-4-6" \
  GROQ_API_KEY=... OPENROUTER_API_KEY=... AGENT_MODEL="" \
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... TELEGRAM_WEBHOOK_SECRET=... \
  API_KEY=... \
  EDGAR_USER_AGENT="mizanquant <your-email>" \
  AUTO_TRADE_ENABLED=false
```
- `CACHE_DIR=/data` and `PORT=8080` are already in `fly.toml` `[env]` — no secret needed.
- **IBKR (deferred):** do NOT set `IBKR_HOST/IBKR_PORT` → the broker adapter reports "IBKR
  unavailable" and the portfolio panel shows offline (expected). Add them back when you re-host
  IB Gateway (step 7).
- **Only set the `HALAL_* / GATE_* / DAYTRADE_*` knobs if you OVERRODE them on Railway** — they have
  safe code defaults otherwise. Keep `AUTO_TRADE_ENABLED=false` (real-money safety).

## 3. Migrate the database (Railway Postgres → new Postgres)
```
pg_dump "<RAILWAY_DATABASE_URL>" -Fc -f mizan.dump
pg_restore --no-owner --no-acl --clean --if-exists -d "<NEW_DATABASE_URL>" mizan.dump
```
The app's `init_db` then creates any missing tables + runs its lightweight column migrations on boot.

## 4. Deploy + verify
```
fly deploy
fly logs        # expect: "Scheduler started", "Database tables initialized."
fly open        # opens the URL
```
Check on the Fly URL:
- `GET /health` → 200.
- Terminal dashboard loads; Weekly/Monthly/Pairs/Explosion scanners populate; MizanAI agent answers;
  halal verdicts render; paper ledgers show the **migrated** counts (DB connected).
- `fly machine restart` → `/data` cache + DB persist (no first-scan stall, no data loss).
- Portfolio panel shows "broker offline" — expected (IB Gateway deferred).

## 5. Cutover (reversible) — and stopping the Railway bill
1. While testing, Railway stays live. **Avoid double-writes:** two live schedulers duplicate
   paper-ledger rows / Telegram alerts / the off-session precompute, and the DBs diverge. So once Fly
   is verified, put Railway in **standby** (Railway → service → scale to 0, or pause) so only ONE
   system writes.
2. Final DB sync at the moment of cutover: re-run step 3 (last `pg_dump` → restore).
3. Switch your usage/domain to the Fly URL. Keep Railway in standby ~3–7 days as rollback (scale it
   back up to revert).
4. When confident, **delete the Railway services** (app + `ibgateway` + the Railway Postgres addon).
   The bill stops only after deletion — running both = paying for both.

## 6. Trim to the terminal UI (AFTER Fly is verified — do NOT do this before)
Keep `app/static/terminal/*` + every backend route it calls. Remove the unused legacy pages a few at
a time, re-checking the terminal each time (no console errors):
`dashboard-legacy.html, dashboard-v2.html, dashboard.html, etf.html, rotation.html, weekly-picks.html,
screener.html, trading*.html, macro.html, risk-desk.html, investors.html, strategies.html,
forecast-panel.html, analysis-lab.html, halal-screener.html, ai-assistant.html, assistant.html,
alerts.html, backtest.html` (+ their dedicated page-routes). Doing this after cutover avoids deploying
a breaking trim to the still-live Railway app via push-to-main.

## 7. (Later) Re-add IB Gateway
Run the `ibgateway/` image as a SECOND Fly app on the private 6PN network, then set `IBKR_HOST` to its
`<app>.internal` name and `IBKR_PORT=4002`. This restores the live IBKR portfolio panel.
