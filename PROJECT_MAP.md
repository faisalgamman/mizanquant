# PROJECT_MAP — MizanQuant

_Last updated: 2026-06-06 (Architectural review refresh: versions reconciled to requirements.txt, orphans registered) (M1-M16 complete, V1 API deployed, Options 1-3 complete)_

> **Signal Expansion (Options 1+2+3):** ✅ Gate Toggle UI, ✅ Universe expanded to 660 symbols, ✅ Momentum Burst strategy added.

---

## TECH_STACK

| Layer | Technology | Version (latest stable) | Notes |
|-------|-----------|------------------------|-------|
| **Runtime** | Python 3.11-slim (Docker) | 3.11.x | Deployed on Railway Hobby |
| **Web** | FastAPI + Uvicorn | >=0.128.8 / >=0.40.0,<0.41.0 | Async HTTP, lifespan mgmt (uvicorn capped by openbb-core 1.6.x) |
| **DB** | SQLAlchemy 2.0 | >=2.0.48 | SQLite (dev, aiosqlite) / PostgreSQL (Railway, asyncpg) |
| **Broker** | Alpaca Markets (paper) | — | 4 accounts: default + A/B/C |
| **Broker** | Interactive Brokers (ib_insync) | >=0.9.86 | Adapter active (IB Gateway Docker on Railway, port 4002, paper) |
| **ML** | PyTorch (CPU) | >=2.7.0 | LSTM, Transformer models |
| **ML** | scikit-learn | >=1.8.0 | Ensemble, preprocessing |
| **ML** | XGBoost | >=3.2.0 | Stacking ensemble member |
| **ML** | statsmodels | >=0.14.6 | Cointegration, ADF, Hurst |
| **RL** | Custom (Double DQN, PG, ES) | — | In openbb_forecast/agents/ |
| **Data** | yfinance + Alpaca IEX + Tiingo | yfinance >=1.2.0 | Multi-source with fallback chain |
| **AI Agent** | Anthropic Claude Sonnet 4-6 | >=0.86.0 | Bilingual trading copilot |
| **Notify** | Telegram Bot API (httpx) | httpx >=0.28.1 | Async queue-based dispatcher |
| **Config** | pydantic-settings | >=2.13.1 | Nested, env-based, fail-fast |
| **CI** | GitHub Actions | — | ruff, pytest (80% cov), gitleaks |
| **Forecast Ext** | OpenBB core + providers | openbb-core ==1.6.9 | FRED/FMP/Tiingo pinned ==1.6.0 |

> **✅ Versions reconciled to `requirements.txt` (2026-06-06).** Earlier table listed
> aspirational/incorrect pins (e.g. PyTorch 2.11.0, FastAPI 0.136.1) — now mirrors actual constraints.

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
├── halal_screener.py          [~5006 lines] ← Router/function provider (NOT deployable)
├── app/workspace_server.py     [~7514 lines] ← CANONICAL entry point (railway.json → Dockerfile)
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
| **M15** | Wavelet denoising + XGBoost signal classifier + Fama-French factors | app/services/wavelet_denoise.py, app/services/signal_classifier.py, app/services/factor_exposure.py, app/services/trading_engine.py | ✅ Done |

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
| `gap_go` archetype | Stub (Phase 5) | `signal_archetypes.py:19` | Needs intraday data; explicitly deferred |
| Forecast Consensus (0-30) | Placeholder | `workspace_server.py:3440` | RSI-based proxy, not a real forecast model |
| `trade_plan.calculate_position_size()` | Deprecated | `trade_plan.py:87` | Live path is `risk_manager.calculate_position_size`; remove stale callers |
| shadow-mode sizing flags | Code present, OFF | `config.py` (`KALMAN_/GARCH_/WAVELET_/SENTIMENT_/FACTOR_/MR_/PORTFOLIO_COV_/XGB_SIGNAL_GATE_…_LIVE`) | Intentional — pending validation before going LIVE |

---

## RECENT ADDITIONS (Signal Expansion — Options 1+2+3)

| # | Feature | Files | Status |
|---|---------|-------|--------|
| **O3** | Gate Toggle UI — dynamic min_gate/strong_gate via slider + override + Telegram alert | `app/services/gate_settings.py` (new), `app/services/scoring.py` (mod), `app/routers/dashboard.py` (mod), `app/static/dashboard.html` (mod) | ✅ Done |
| **O1** | Universe expansion: S&P 500 + Russell 1000 → 660 halal symbols, JSON loader with fallback | `scripts/build_halal_universe.py` (new), `data/halal_universe_v2.json` (new), `app/services/universe.py` (mod) | ✅ Done |
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
| **M15** | Wavelet + XGBoost + Fama-French | wavelet_denoise.py — edge-preserving denoising, signal_classifier.py — buy-signal probability gate, factor_exposure.py — Fama-French 5-factor betas | ✅ Done |
| **M16** | Strategy B Reactivation (trading specialization) | halal_screener.py — full mean-reversion consensus restored (7 tools: regime router, stationarity gate, Bollinger, RSI, volume-price, stochastic, OBV); signals_advisor.py — strategy B re-enabled in _strategy_runners | ✅ Done |
| **M16** | Strategy B (Mean Reversion) re-enabled — 3-strategy specialization | halal_screener.py (restored run_consensus_reversion), app/services/signals_advisor.py (re-enabled B runner) | ✅ Done |


## Recent Changes (2026-06-05 — Architectural Review)

| Milestone | Change | Status |
|-----------|--------|--------|
| **A1** | Entry point resolved: nixpacks.toml → `python app/workspace_server.py` | ✅ |
| **A2** | halal_screener.py made non-deployable (removed `__main__` + uvicorn) | ✅ |
| **A3** | All `alpaca_get_account()` calls pass `strategy_id` (3 fixed) | ✅ |
| **A4** | OHLCV batch cache with 5-min TTL added to usx_pro_filter | ✅ |
| **A5** | Phase 1 extraction: 6 dashboard endpoints → `app/api/dashboard_api.py` | ✅ |
| **A6** | Orphans cleaned: scripts moved, reports archived, 1.9GB checkpoints pruned | ✅ |
| **A7** | Scoring boundary documented in conviction_engine.py (swing ≠ composite) | ✅ |
| **A8** | Archetype detectors verified behind OFF flags with validation_harness gate | ✅ |

**Files modified:** `nixpacks.toml`, `halal_screener.py`, `workspace_server.py`, `usx_pro_filter.py`,
`pipeline_orchestrator.py`, `conviction_engine.py`, `PROJECT_MAP.md`
**Files created:** `app/api/dashboard_api.py`
**Files moved:** `_run_consensus.py`, `_run_scan.py` → `scripts/`; 4 reports → `docs/`


## Recent Changes (2026-06-06 — Architectural Review v2: hardening)

| Milestone | Change | Success criterion | Status |
|-----------|--------|-------------------|--------|
| **M-A** | PROJECT_MAP versions reconciled to `requirements.txt`; real orphans registered | No fictional versions; 4 orphans logged | ✅ |
| **M-B** | Safety-net tests for the critical, previously-untested logic | 12 new cases green; no regression | ✅ |
| **M-C** | Vectorise the scoring hot path (`get_score`) in both backtest engines | Byte-identical to scalar; ≥3× faster | ✅ (49× on 5k rows) |
| **M-D** | Consolidate the 5 identical shadow-sizing layers in `execute_buy` | Golden-master byte-identical (qty + payload + diagnostics) | ✅ |
| **M-E** | First safe extraction from the `halal_screener` monolith behind a re-export facade | Data byte-identical to HEAD; contract preserved; no regression | ✅ (increment 1) |
| **W1** | Advisory weekly swing-picks report (manual execution, Option-A plan + validation status) | 10 tests green; read-only, no orders; no regression | ✅ |

**M-B coverage added:**
- `tests/test_forward_pf.py` (+7): SELL-side exit, stop-precedence-over-time, exact-touch
  boundary, guard clauses for `_simulate_fixed_exit`.
- `tests/test_backtest_service.py` (new): locks the Chan no-look-ahead invariant —
  signal on `close[i]` fills at `open[i+1]`.
- `tests/test_risk_manager_sizing.py` (new): locks the position-cap invariant on the
  LIVE sizing path (`risk_manager.calculate_position_size`).

**M-C performance:**
- `technical.score_series(df)` (new): column-wise scorer replacing
  `df.apply(get_score, axis=1)` in `backtest_service.py` and `halal_screener.py`.
  49× faster on 5k rows; `tests/test_score_series.py` proves byte-identical output
  across 25 fuzzed seeds + branch-boundary rows. `get_score` (scalar) is retained as
  the reference oracle.
- **Deliberately NOT vectorised (Simplicity First):** the `iterrows`/`range(len(df))`
  loops in `workspace_server.py` (≤25–500-row holder/price/upgrade tables),
  `routers/screener.py` (`tail(200)`) and `chart_generator.py` (per-bar matplotlib
  draw). These are bounded DataFrame→JSON serialisation / plotting on network-bound
  endpoints — vectorising risks changing live output for negligible CPU gain.

**M-D refactor (scoped to the recommended, lowest-risk option):**
- `trading_engine._apply_shadow_qty(...)` (new helper): single source of truth for
  the 5 byte-identical shadow-sizing layers (Wavelet, Kalman, MR, Sentiment, Factor).
  The 5 inline branch blocks now call it. GARCH, portfolio-cov, and the XGB *gate*
  were intentionally left inline — each differs (extra default keys / unconditional
  compute / block-vs-multiply), so folding them in would change recorded diagnostics.
- `tests/test_trading_engine_golden.py` (new): golden-master pinning the final `qty`,
  the submitted order payload, and every shadow diagnostic key — proven green before
  AND after the refactor (shadow path + live-apply path).
- The full 4-way decomposition from the original plan was **declined** after reading
  the code: its early-returns would need sentinel plumbing through the live order path
  (higher risk, modest gain). Recorded here for transparency.

**M-E decomposition (increment 1 — lowest-risk, behind a re-export facade):**
- `app/data/halal_exclusions.py` (new): pure exclusion tables `HARAM_EXCLUDE` (144) +
  `SP500_DELISTED_HALAL` (27), extracted verbatim (byte-identical to HEAD).
- `app/api/request_validators.py` (new): the HTTP-layer `validate_symbol/validate_date/
  validate_range` (raise `HTTPException(400)`). Distinct from `app.services.validators`
  (the structured pre-trade variant) — kept separate by design.
- `halal_screener` re-exports all moved names under their original spelling, so the wide
  public contract (`from halal_screener import …` and `hs.<name>`) is unchanged.
  Orphaned `import re` removed. `tests/test_halal_extraction.py` pins the contract.
- **Deferred (deliberately):** deeper extraction of the consensus/ML runners and the
  shared cache/rate-limiter state. The monolith's contract is wide and partly *dynamic*
  (`hs.<attr>`) and several clusters mutate shared module globals — extraction there is
  high-risk for low net gain (the re-export facade means the public surface doesn't
  shrink). `_VERIFIED_HARAM` stays put: it is runtime state, not data.

> **Baseline note:** 10 pre-existing suite failures (calibrate_thresholds, config,
> dashboard_endpoints, ibkr_config, market_data, scheduler_hooks, trading_engine_entry)
> exist on the clean tree under the local Python 3.14 interpreter (project targets 3.11/3.12).
> They are unrelated to this review and were confirmed present before any change.
