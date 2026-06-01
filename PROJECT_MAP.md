# PROJECT_MAP — openbb-trading

_Last updated: 2026-06-01 (Cleanup + Kalman + Bayesian Sharpe (SHAP)) (M1-M13 complete, V1 API deployed, Options 1-3 complete)_

> **Signal Expansion (Options 1+2+3):** ✅ Gate Toggle UI, ✅ Universe expanded to 680 symbols, ✅ Momentum Burst strategy added.

---

## TECH_STACK

| Layer | Technology | Version (latest stable) | Notes |
|-------|-----------|------------------------|-------|
| **Runtime** | Python 3.11-slim (Docker) | 3.11.11 | Deployed on Railway Hobby |
| **Web** | FastAPI + Uvicorn | 0.136.1 / 0.46.0 | Async HTTP, lifespan mgmt |
| **DB** | SQLAlchemy 2.0 | 2.0.49 | SQLite (dev) / PostgreSQL (Railway) |
| **Broker** | Alpaca Markets (paper) | — | 4 accounts: default + A/B/C |
| **Broker** | Interactive Brokers (ib_insync) | 0.9.86 | Adapter active (IB Gateway Docker on Railway, port 4002, paper) |
| **ML** | PyTorch (CPU) | 2.11.0 | LSTM, Transformer models |
| **ML** | scikit-learn | 1.8.0 | Ensemble, preprocessing |
| **ML** | XGBoost | 3.2.0 | Stacking ensemble member |
| **ML** | statsmodels | 0.14.6 | Cointegration, ADF, Hurst |
| **RL** | Custom (Double DQN, PG, ES) | — | In openbb_forecast/agents/ |
| **Data** | yfinance + Alpaca IEX | — | Dual-source with fallback |
| **AI Agent** | Anthropic Claude Sonnet 4-6 | 0.100.0 | Bilingual trading copilot |
| **Notify** | Telegram Bot API (httpx) | — | Async queue-based dispatcher |
| **Config** | pydantic-settings | 2.14.0 | Nested, env-based, fail-fast |
| **CI** | GitHub Actions | — | ruff, pytest (80% cov), gitleaks |
| **Forecast Ext** | OpenBB (openbb_forecast) | 0.1.0 | Poetry package, extensions |

> **✅ Fixed:** `requirements.txt` now pinned to `>=` with tested stable versions.

---

## SYSTEM_FLOW

```
                              ┌─────────────────┐
                              │   .env config    │
                              │  pydantic-settings│
                              └────────┬────────┘
                                       │
┌──────────────────────────────────────┴──────────────────────────────────┐
│                         FastAPI (halal_screener.py)                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ /screener│  │ /analyze │  │ /ml/*    │  │ /rl/*    │  │ /agent/* │ │
│  │ /usx     │  │ /bcf     │  │ /backtest│  │ /mc      │  │ /admin/* │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘ │
└───────┼──────────────┼────────────┼──────────────┼──────────────┼───────┘
        │              │            │              │              │
        ▼              ▼            ▼              ▼              ▼
┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐
│  Halal       │ │Technical │ │openbb_   │ │Risk Mgr  │ │Claude Agent    │
│  Screening   │ │Analysis  │ │forecast  │ │+ Guards  │ │(claude_agent)  │
│  (FMP+yf)    │ │(custom)  │ │(ML/RL/MC)│ │(14 mods) │ │                │
└──────┬───────┘ └──────────┘ └────┬─────┘ └────┬─────┘ └────────────────┘
       │                           │            │
       ▼                           ▼            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Trading Engine (trading_engine.py)                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐ │
│  │Strategy A│  │Strategy B│  │Strategy C│  │ Broker Abstraction   │ │
│  │  HANA    │  │  marem   │  │  mazem   │  │ Alpaca ── IBKR (active)│ │
│  │ trend-fw │  │mean-rev  │  │ ML-drive │  │ factory → adapter    │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────────────────┘ │
└───────┼──────────────┼────────────┼──────────────────────────────────┘
        │              │            │
        ▼              ▼            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Background Scheduler (scheduler.py)               │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌─────────┐ │
│  │Pre-  │ │30-min│ │Post- │ │Night-│ │Fill  │ │Calibr│ │Retrain  │ │
│  │Market│ │Scan  │ │Market│ │ly    │ │Watch │ │-ate  │ │Models   │ │
│  │9:00  │ │(×5)  │ │16:15 │ │Train │ │5s    │ │Weekly│ │(cron)   │ │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └─────────┘ │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│                   Notification Pipeline                        │
│  notify.py (async queue) → Telegram Bot (httpx)               │
│  trade_logger.py → JSONL rotating file (50MB×10)              │
│  DB: SignalHistory, TradeHistory, GuardLog, ConsensusLog      │
└───────────────────────────────────────────────────────────────┘
```

### Data Flow (Runtime)

1. **Scheduler** triggers scan → calls halal screening + technical analysis + ML forecasts
2. Each strategy generates signals → consensus voting (momentum/reversion/ML profiles)
3. Trade signals pass through **Guards Pipeline** (14 modules, sequential):
   - market_hours → regime_bear_freeze → vix_halt → account_status → ...
   - All results logged to `GuardLog` table
4. **Risk Manager** sizes position (Kelly-aware, regime-adaptive)
5. **Trading Engine** submits bracket order via broker adapter (Alpaca)
6. **Fill Watcher** polls order status every 5s → updates DB on fill
7. **Signal Tracker** scores outcome accuracy on close

---

## ARCHITECTURE

### Domain Boundaries (Domain-Driven)

```
openbb-trading/
├── halal_screener.py          [~4308 lines → ~3880 lines] ← Entry point, includes routers
├── app/
│   ├── config.py              [Domain: Configuration] Settings + StrategyConfig
│   ├── exceptions.py          [Domain: Shared] Exception hierarchy (7 types)
│   ├── core/config.py         [Domain: Configuration] Nested AppCfg + calibration
│   ├── db/                    [Domain: Persistence] SQLAlchemy models (9 tables)
│   ├── background/            [Domain: Caching] DB-backed cache manager
│   ├── services/
│   │   ├── broker/            [Domain: Broker Abstraction] Protocol + 2 adapters
│   │   ├── guards/            [Domain: Risk] 14 individual guard modules
│   │   ├── gate_settings.py   [Domain: Config] In-memory gate toggle UI overrides
│   │   ├── *.py (21 files)    [Domain: Core Business Logic] Trading, screening,
│   │   │                        technical analysis, regime, ML, Telegram, etc.
│   │   └── ...
│   └── routers/               [✅ Populated] 5 modules: screener, forecast, consensus, portfolio (+/api/ibkr/ping), admin
├── app/api/                   [✅ NEW] V1 API endpoints
│   ├── deps.py                Auth + validation dependencies
│   └── v1/
│       ├── __init__.py        Combines all V1 routers under /api/v1/
│       ├── system.py          System status, health, scheduler, block, symbols
│       ├── market.py          Market context, status, sector performance
│       ├── trading.py         Trading summary, controls, history, halal, backtest
│       ├── pipeline.py        Pipeline status + manual run
│       ├── guards.py          Recent guard activity + daily summary
│       ├── scoring.py         Weighted score + trade plan
│       ├── watchlist.py       Watchlist CRUD
│       └── overview.py        Aggregator endpoint (1 call replaces 7 separate endpoints)
├── openbb_forecast/           [Domain: ML/RL/Simulation] OpenBB extension
│   ├── agents/                RL: DQN, PG, ES
│   ├── models/                LSTM, Transformer, Ensemble
│   ├── backtesting/           Walk-forward engine
│   ├── risk/                  Metrics (Sharpe, VaR, CVaR)
│   ├── data/                  Preprocessing, time guard
│   └── simulation/            Monte Carlo GBM
├── scripts/                   [Domain: CLI/DevOps] Preflight, train, backtest, calibrate
├── calibration/               [Domain: Config] Tuned threshold snapshots
├── tests/                     [Domain: QA] 35+ test files + chaos suite
└── docs/                      [Domain: Documentation] Runbook, out-of-scope register
```

### Strengths (Surgical Architecture Check)
- ✅ **Broker abstraction** via Protocol class — minimal interface, 7 methods
- ✅ **Guard system** modular — 14 independent files, one concern each
- ✅ **No micro-fragmentation** — files are meaningful units
- ✅ **`out_of_scope.md`** — explicit scope control, 10 deferred items
- ✅ **Fail-fast config** — `validate_for_live()` called at boot
- ✅ **IBKR diagnostic endpoint** — `GET /api/ibkr/ping` tests TWS connectivity via socket
- ✅ **Cache layer** — DB-backed, survives restarts
- ✅ **RL agents isolated** — separate package, no leakage into trading engine

### Risks Addressed (M1-M4)
- ✅ ~~`halal_screener.py` at ~4833 lines~~ → Refactored into 5 router modules (~3880 lines remaining). All endpoints preserved, all tests pass.
- ✅ ~~`app/routers/__init__.py` is empty~~ → Populated with 5 router modules: screener, forecast, consensus, portfolio, admin.
- ✅ ~~`requirements.txt` unpinned~~ → Pinned to `>=` with tested stable versions.
- ✅ ~~USX Pro V4 filter not wired~~ → Integrated as Stage 1 of 3-stage signals pipeline (commit `42059e6`).
- ✅ ~~Copilot duplicates FastAPI setup~~ → Merged into main app as `POST /v1/query` streaming endpoint; `copilot.py` removed.

---

## RECENT ADDITIONS

| # | Feature | Files | Status |
|---|---------|-------|--------|
| **M8** | GARCH(1,1) volatility model with shadow-mode sizing | app/services/garch_volatility.py (new), app/services/trading_engine.py (additive) | ✅ Done |
| **M9** | Portfolio covariance-based capital allocation | app/services/portfolio_optimizer.py (new), app/services/trading_engine.py (additive) | ✅ Done |
| **M10** | Mean-reversion OU utility with quality scoring | app/services/mean_reversion_util.py (new), app/services/trading_engine.py (additive) | ✅ Done |
| **M11** | News sentiment engine — VADER + analyst consensus | app/services/sentiment_engine.py (new), app/services/trading_engine.py (additive), app/workspace_server.py (delegated) | ✅ Done |
| **M12** | Strategy simulation sandbox with full sizing cascade | app/services/strategy_simulator.py (new), app/workspace_server.py (endpoint) | ✅ Done |
| **M13** | Model explainability — ensemble attribution + SHAP-ready | app/services/model_explainer.py (new), app/workspace_server.py (endpoint) | ✅ Done |
| **M14** | Kalman filter denoising + Bayesian Sharpe ratio | app/services/kalman_filter.py, app/services/bayesian_sharpe.py, app/services/backtest_qc.py (new) | ✅ Done |

---

## ORPHANS & PENDING

| Item | Status | Location | Action Required |
|------|--------|----------|----------------|
| Strategy D (pairs) | Deferred | (Phase 5 cointegration exists) | See `docs/out_of_scope.md` |
| HMM regime detector | Deferred | — | See `docs/out_of_scope.md`, item 2 |
| Walk-forward optimization | Deferred | — | See `docs/out_of_scope.md`, item 3 |
| Cross-strategy Kelly | Deferred | — | See `docs/out_of_scope.md`, item 8 |
| `keep_alive.py` | Moved to `app/services/keep_alive.py` | Reviewed — still needed for Railway health checks | ✅ Done |
| `russell1000_halal.py` | Moved to `app/data/russell1000_halal.py` | Reviewed — correctly placed in `app/data/` | ✅ Done |

---

## RECENT ADDITIONS (Signal Expansion — Options 1+2+3)

| # | Feature | Files | Status |
|---|---------|-------|--------|
| **O3** | Gate Toggle UI — dynamic min_gate/strong_gate via slider + override + Telegram alert | `app/services/gate_settings.py` (new), `app/services/scoring.py` (mod), `app/routers/dashboard.py` (mod), `app/static/dashboard.html` (mod) | ✅ Done |
| **O1** | Universe expansion: S&P 500 + Russell 1000 → 680 halal symbols, JSON loader with fallback | `scripts/build_halal_universe.py` (new), `data/halal_universe_v2.json` (new), `app/services/universe.py` (mod) | ✅ Done |
| **O2** | Momentum Burst Strategy: 8-condition daily breakout detector + backtest + registration | `app/strategies/momentum_burst.py` (new), `app/strategies/__init__.py` (mod), `app/strategies/backtest.py` (mod), `app/routers/dashboard.py` (mod) | ✅ Done |

---

## MILESTONES (Verifiable Goals)

| # | Milestone | Success Criteria | Status |
|---|-----------|-----------------|--------|
| **M1** | Pin dependencies | `requirements.txt` pinned to `>=` stable versions | ✅ Done (commit `c852437`) |
| **M2** | Refactor halal_screener into routers | 5 router modules in `app/routers/`; all 178 tests pass | ✅ Done (commit `c852437`) |
| **M3** | Wire USX Pro V4 | 3-stage signals pipeline (USX → AI → Telegram) | ✅ Done (commit `42059e6`) |
| **M4** | Merge copilot into main app | `POST /v1/query` streaming endpoint on main FastAPI; `copilot.py` removed | ✅ Done (commit pending) |
| **M5** | Clean orphans | `keep_alive.py` → `app/services/`, `russell1000_halal.py` → `app/data/` | ✅ Done |
| **M6** | Activate IBKR | IB Gateway Docker on Railway (paper, port 4002), `BROKER_TYPE=ibkr`, `/api/ibkr/ping` → `ok` | ✅ Done |
| **M8** | GARCH(1,1) volatility model | app/services/garch_volatility.py, GARCH integration in execute_buy() | ✅ Done |
| **M7** | OpenBB Workspace Integration | Custom backend at `app/workspace_server.py` serving 40 widgets (19 models + 19 agents + MC + metrics) with OpenBB Platform extension registration | ✅ Done |
| **M9** | Portfolio Covariance Matrix | app/services/portfolio_optimizer.py — Kelly-optimal portfolio weights via Cov^-1 x mu, shadow-mode integration in execute_buy() | ✅ Done |
| **M10** | Mean Reversion OU Utility | app/services/mean_reversion_util.py — OU parameter estimation, mr_quality_score(), shadow-mode integration in execute_buy() | ✅ Done |
| **M11** | News Sentiment Engine | app/services/sentiment_engine.py — VADER news + analyst consensus scoring, shadow-mode integration in execute_buy() | ✅ Done |
| **M12** | Strategy Simulation Sandbox | app/services/strategy_simulator.py — historical what-if with full sizing cascade (GARCH/Cov/MR/Sentiment), /api/simulate/strategy endpoint | ✅ Done |
| **M13** | Model Explainability (SHAP) | app/services/model_explainer.py — ensemble attribution now, SHAP TreeExplainer ready for future XGBoost/sklearn models | ✅ Done |
| **M14** | Kalman Filter + Bayesian Sharpe | app/services/kalman_filter.py — signal denoising, app/services/bayesian_sharpe.py — full posterior with credible intervals | ✅ Done |
