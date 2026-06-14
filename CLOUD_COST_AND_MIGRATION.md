# Cloud cost reduction + migration runbook

## 1. Where the money goes (your May 11 – Jun 11 bill)
| Item | Cost | Note |
|---|---|---|
| **Memory** | **$47.00** | ~70% of the bill — the real target |
| CPU | $20.09 | the heavy 657-symbol FMP scans + repeated cold re-scans |
| Egress | $0.36 | negligible |
| Volume | $0.00 | negligible |
| **Total bill** | **$62.48** | single replica, capped 2 GB RAM / 2 vCPU (`railway.json`) |

**Honest caveat:** this period was inflated by a lot of redeploys + cold scans (incl. today's
fixes). A stable month with the changes below will read lower than $62. Memory billing is on
*actual* resident usage, so trimming the always-resident footprint is the lever.

## 2. Phase 1 — cut Railway cost (do these; ~halve the bill)

### 2a. DONE in code (commit `6b626d8`)
- **Removed `torch`** from `requirements.txt`. It was imported at startup
  ([workspace_server.py:851](app/workspace_server.py:851)) → ~300–500 MB resident RAM + ~2 GB image,
  but only powered the secondary DL forecast endpoints (`/api/forecast/{model}`). All torch imports
  are guarded (`_HAS_TORCH`) → fall back to ARIMA. The core **Monte-Carlo engine (risers + Analyze
  forecast) is pure numpy** and is untouched — verified with torch simulated absent.
- **Removed `mlflow`** — only loaded when `MLFLOW_TRACKING_URI` is set (never in serving) → image/disk only.
- **Removed `xgboost`** (commit `ddd2933`) — guarded by `_HAS_XGB`; only powers the secondary
  trade-history classifier (`if not _HAS_XGB: return None`). Live signals effectively unchanged (the AI
  sub-score is already discounted as ~coin-flip).
- **`matplotlib`/`mplfinance` checked, kept:** NOT used by the main screen (both `/api/stock/chart` and
  `/api/chart/rotation` return JSON; charts are client-side SVG) and already lazy-loaded (only via Telegram
  chart images) → zero startup RAM. Drop them only in the lean build (§5) if you don't need Telegram chart images.
- Trade-off: the 18+ DL forecast models now fall back to ARIMA. Acceptable — the system measures ML at
  ~coin-flip. To re-enable later: add `torch` back (CPU wheel) per the Dockerfile note.

### 2b. OWNER action — 1 env var (biggest CPU lever)
Set in Railway → Variables: **`SCREENER_FUNNEL_TOP_N=150`**
→ the expensive FMP analysis runs only on the top-150 technically-ranked symbols instead of all 657
→ cold scans drop ~12 min → ~3–4 min, FMP usage drops ~75%, and CPU + scan-peak memory fall. The code
path already exists ([workspace_server.py `_FUNNEL_TOP_N`](app/workspace_server.py:802)) and is tested;
it's off by default. Reversible (set back to `0` for the full scan).

### 2c. Optional further cuts (evaluate AFTER measuring 2a+2b)
- **`xgboost`** (~50–100 MB): guarded by `_HAS_XGB` (verified app imports fine without it). Removing it
  makes the live "AI*" sub-score fall back — small real impact (already discounted), but it touches live
  signal output, so decide deliberately.
- **Lazy-import `matplotlib`/`mplfinance`** (~100 MB) if they load at startup — only import inside the
  chart-generating functions.
- Reduce scheduler churn / keep `SCREENER_CACHE_TTL` high (already 3600s).

### 2d. How to confirm the savings
Watch the Railway **Usage** page for 3–5 days after the rebuild. Expect Memory to drop materially
(no torch resident) and CPU to drop once the funnel is on. Target: **$62 → ~$30–40** (lower with the funnel).
**Re-measure one full billing cycle before deciding to migrate.**

## 3. Phase 2 — migration (only if still over budget after Phase 1)

### 3a. Honest comparison
| Option | ~Monthly | Effort | Reliability | Notes |
|---|---|---|---|---|
| **Railway (optimized)** | ~$30–40 | none (done) | managed, high | git-push deploy, managed PG, volume |
| **Render** | ~$7 web + ~$7 PG + IB svc | medium | managed | closest like-for-like; private IB service costs extra |
| **Fly.io** | ~$5–15 | medium–high | self-ish | volumes + machines; usage-based |
| **Hetzner VPS (CX22)** | ~€5 | **high** | self-managed | cheapest; you own everything |
| **DigitalOcean droplet** | ~$6–12 | high | self-managed | similar to Hetzner |

For ~$4,000 of real money, managed reliability has value. A $5 VPS is cheapest **only if** you accept the
operational burden below.

### 3b. What has to move (current architecture)
1. **The FastAPI app** (this repo, Dockerfile) — easy anywhere that runs a container.
2. **PostgreSQL** (Railway managed PG; `asyncpg`/`psycopg2` in requirements) — move to managed PG or a
   Postgres container; export/import the DB.
3. **Persistent volume `/data`** (`CACHE_DIR=/data/.cache`, paper ledger, `smart_screener_lastgood`) —
   needs a persistent disk on the target. Copy the contents at cutover.
4. **The IB Gateway sidecar** (`ibgateway/` — separate service `gway`, Java IB Gateway + auto-restart
   `AUTO_RESTART_TIME=11:59 PM`) — **the hard part** (see 3c).
5. **Secrets/env** (rotated set: DeepSeek, Telegram, Alpaca, FMP, API_KEY, DASHBOARD_SECRET, dashboard
   password, IBKR creds, `AUTO_TRADE_ENABLED=false`, `CACHE_DIR`, etc.) — recreate on the target.
6. **Scheduler** (`apscheduler` in-process — already runs inside the app, no external cron needed). ✅
7. **Deploy method** — replace git-push-to-deploy with: a deploy script / `docker compose up -d --build`,
   or the host's CI.

### 3c. The IB Gateway problem (read before choosing a VPS)
`ibgateway/Dockerfile` runs the **Java IB Gateway** (interactive, needs IBC for headless login, daily
restart, and handles IBKR 2FA). On Railway it's a managed second service. On a VPS you must:
- Run the IBC + IB Gateway container yourself (VNC/headless), persist its settings.
- Reproduce the daily auto-restart (cron/systemd timer ≈ the current `AUTO_RESTART_TIME`).
- Handle IBKR login/2FA on a headless box (the usual pain point).
- The app reaches it over the private network (`ib_insync` → gateway host:port).
This is ~70% of the migration effort. If you don't want to own this, **Render** (managed services) or
**staying on optimized Railway** is the pragmatic choice.

### 3d. Cutover runbook (target: single small VPS + Docker Compose — cheapest path)
1. Provision the VPS (Hetzner CX22 / DO, ≥2 GB RAM, Docker installed). Attach a volume for `/data`.
2. Write a `docker-compose.yml`: services = `app` (this Dockerfile), `postgres` (with a named volume),
   `ibgateway` (from `ibgateway/Dockerfile`). Wire the app→gateway and app→postgres over the compose network.
3. Recreate all env/secrets in a `.env` (chmod 600). Set `CACHE_DIR=/data/.cache`,
   `AUTO_TRADE_ENABLED=false` for the first run.
4. **Export Railway Postgres** → import into the new PG (`pg_dump | pg_restore`). Copy the `/data`
   volume contents (paper ledger + caches).
5. `docker compose up -d --build`. Bring the IB Gateway up first; complete IBKR login/2FA; confirm
   `ib_insync` connects (broker health = IBKR ✓ in the footer).
6. Smoke test on the new host: `/health`, `/terminal` (login), `/buys`, `/api/risers`, `/agent/chat`,
   the Monthly scanner completes, a paper trade records. Keep `AUTO_TRADE_ENABLED=false`.
7. Put a reverse proxy (Caddy/nginx) + TLS in front; point DNS.
8. **Run both in parallel for a few days** (Railway still live) before cancelling Railway.
9. Add a deploy script (`git pull && docker compose up -d --build`) to replace push-to-deploy.

### 3e. Rollback
Keep the Railway service paused (not deleted) for one billing cycle. If the VPS misbehaves (esp. the IB
gateway), repoint DNS back to Railway. Don't delete Railway PG until the new DB has run clean for a week.

### 3f. Honest recommendation
Do **Phase 1** now (it's mostly done — just add the funnel env var) and **measure one cycle**. If the
optimized bill is ~$30, the VPS saves maybe ~$20/mo but costs you the IB-gateway ops burden and reliability
risk on a real-money system — usually not worth it. If you still want out, **Render** is the lowest-effort
move; a **VPS** is cheapest only if you're comfortable owning the IB gateway.

## 4. Verify (Phase 1)
- Build succeeds without torch (Railway rebuild of `6b626d8`).
- App boots: log shows `torch not installed — DL models will fall back to ARIMA` (expected, not an error).
- `/api/risers` returns stocks, Analyze forecast renders (Monte-Carlo intact), `/agent/chat` works.
- DL endpoints `/api/forecast/{model}` return an ARIMA fallback, not a 500.
- Railway Usage memory trends down over several days.

## 5. The LEAN "main-screen-only" build (your chosen migration target)
You want to migrate only what the main dashboard (`/terminal`) actually uses. Here is the exact surface,
derived from every `fetch()` in the terminal JSX.

### 5a. KEEP — the 24 endpoints the main screen calls
| Group | Endpoints |
|---|---|
| Screeners | `/buys` · `/api/screener/deep-picks` · `/api/screener/watch` · `/api/stock/smart-screener` |
| Market | `/api/v1/overview` · `/api/market/indicators` · `/api/market/news` |
| Analyze | `/api/v1/scoring/weighted` · `/api/v1/trade/plan` · `/api/v1/forecast/` · `/api/v1/consensus/` · `/api/risers` |
| Broker/portfolio | `/api/v1/broker/health` · `/api/v1/broker/close` · `/api/v1/broker/execute` |
| Paper | `/api/v1/paper/trades` · `/paper_validation/status` |
| Intel/chart | `/signals/accuracy` · `/api/risk/status` · `/api/chart/rotation` · `/api/stock/chart` · `/api/v1/models/leaderboard` |
| Agent | `/agent/chat` |

### 5b. STRIP — heavy, NOT on the main screen
- DL forecast endpoints: `/api/forecast/{model}` + `/api/chart/forecast/{model}` (already neutralized — torch gone).
- ML training/research modules: `ml_pipeline.py`, `mlflow_tracker.py`, `optimizer.py`, `model_explainer.py`,
  `smart_ensemble.py`, `signal_classifier.py` (xgboost).
- Telegram chart **images** (`chart_generator.py`, `signals_chart.py`) → drop `matplotlib` + `mplfinance`
  (Telegram TEXT alerts still work). Assorted admin/research routes.

### 5c. Lean `requirements.txt` (starting point — verify each import before removing)
- **Keep:** fastapi, uvicorn, yfinance, pandas, numpy, statsmodels (ARIMA), requests, httpx,
  pydantic-settings, python-dotenv, sqlalchemy + asyncpg/psycopg2 (or aiosqlite), python-multipart,
  ib_insync (broker), apscheduler (scheduler), anthropic + openai + groq (agent), redis/slowapi (if used),
  openbb-core + openbb-fred + openbb-fmp + openbb-tiingo (market data), the vendored `openbb_forecast`
  (Monte-Carlo, numpy-only).
- **Already removed:** torch, mlflow, xgboost.
- **Droppable for lean:** matplotlib, mplfinance (Telegram chart images). **Verify before removing:**
  scikit-learn, nltk, sentry-sdk, prometheus-client, mlflow, psutil — grep the serving path; drop any not used.

### 5d. The catch — the main screen STILL needs (cannot strip)
The dashboard executes/closes trades and shows the live portfolio + paper ledger, so the lean build STILL
requires: **(1) the IBKR gateway** (`ibgateway/` — the hard migration piece), **(2) Postgres** (paper ledger,
signals accuracy, agent decisions, leaderboard), **(3) the `/data` volume** (caches, `smart_screener_lastgood`,
ledger). So "main-screen-only" removes the ML weight, not the broker/DB/volume — plan §3 still applies for those.

### 5e. Net effect
A lean image (no torch/mlflow/xgboost ± matplotlib) is ~2.5 GB smaller and materially lower RAM — it fits
comfortably in ~1 GB, which is what makes a ~€5 VPS or a small Render/Fly instance viable. Do §2 (mostly done)
+ measure; if you still migrate, build from the §5a KEEP list and carry the §5d infra.
