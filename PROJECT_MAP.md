# PROJECT_MAP — openbb-trading

_Last updated: 2026-05-07_

---

## TECH_STACK

| Layer | Technology | Version (latest stable) | Notes |
|-------|-----------|------------------------|-------|
| **Runtime** | Python 3.11-slim (Docker) | 3.11.11 | Deployed on Railway Hobby |
| **Web** | FastAPI + Uvicorn | 0.136.1 / 0.46.0 | Async HTTP, lifespan mgmt |
| **DB** | SQLAlchemy 2.0 | 2.0.49 | SQLite (dev) / PostgreSQL (Railway) |
| **Broker** | Alpaca Markets (paper) | — | 4 accounts: default + A/B/C |
| **Broker** | Interactive Brokers (ib_insync) | 0.9.86 | Adapter complete, inert (no keys) |
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

> **⚠️ Risk:** `requirements.txt` is **unpinned** — no version constraints. Builds are non-reproducible. Pin to `>=` with tested versions.

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
│  │  HANA    │  │  marem   │  │  mazem   │  │ Alpaca ── IBKR (inert)│ │
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
├── halal_screener.py          [4833 lines] ← Monolith entry point, all endpoints
├── app/
│   ├── config.py              [Domain: Configuration] Settings + StrategyConfig
│   ├── exceptions.py          [Domain: Shared] Exception hierarchy (7 types)
│   ├── core/config.py         [Domain: Configuration] Nested AppCfg + calibration
│   ├── db/                    [Domain: Persistence] SQLAlchemy models (9 tables)
│   ├── background/            [Domain: Caching] DB-backed cache manager
│   ├── services/
│   │   ├── broker/            [Domain: Broker Abstraction] Protocol + 2 adapters
│   │   ├── guards/            [Domain: Risk] 14 individual guard modules
│   │   ├── *.py (20 files)    [Domain: Core Business Logic] Trading, screening,
│   │   │                        technical analysis, regime, ML, Telegram, etc.
│   │   └── ...
│   └── routers/               [⚠️ Empty! Endpoints in halal_screener.py]
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
- ✅ **Cache layer** — DB-backed, survives restarts
- ✅ **RL agents isolated** — separate package, no leakage into trading engine

### Risks to Address
- ⚠️ **`halal_screener.py` at ~4833 lines** — violates Single Responsibility. Endpoints should live in `app/routers/`. Benefits of splitting: isolated error handling, per-router middleware, independent testability.
- ⚠️ **`app/routers/__init__.py` is empty** — the package skeleton exists but is unused. Either populate it or remove it.
- ⚠️ **`requirements.txt` unpinned** — every `pip install` may produce different dependency trees.
- ⚠️ **USX Pro V4 filter (`usx_pro_filter.py`) exists but is not wired** into any endpoint or strategy.
- ⚠️ **Copilot (`copilot.py`) duplicates FastAPI setup** — should merge into main app or document as separate deployment.

---

## ORPHANS & PENDING

| Item | Status | Location | Action Required |
|------|--------|----------|----------------|
| `app/routers/` | Empty skeleton | `app/routers/__init__.py` | Populate or remove |
| USX Pro V4 filter | Module complete, not wired | `app/services/usx_pro_filter.py` | Connect to screener endpoints |
| IBKR adapter | Implemented, inert | `app/services/broker/ibkr_adapter.py` | Needs IBKR account + keys |
| Strategy D (pairs) | Deferred | (Phase 5 cointegration exists) | See `docs/out_of_scope.md` |
| HMM regime detector | Deferred | — | See `docs/out_of_scope.md`, item 2 |
| Walk-forward optimization | Deferred | — | See `docs/out_of_scope.md`, item 3 |
| Cross-strategy Kelly | Deferred | — | See `docs/out_of_scope.md`, item 8 |
| `copilot.py` | Standalone, not in Dockerfile | `copilot.py` | Either integrate or document |
| `requirements.txt` | Unpinned | `requirements.txt` | Pin versions for reproducibility |
| `keep_alive.py` | Loose utility | `keep_alive.py` | Review if still needed (Railway has built-in health checks) |
| `russell1000_halal.py` | Standalone listing | `russell1000_halal.py` | Could move to `app/data/` or `calibration/` |

---

## MILESTONES (Verifiable Goals)

| # | Milestone | Success Criteria | Dependencies |
|---|-----------|-----------------|--------------|
| **M1** | Pin dependencies | `pip freeze > requirements-locked.txt` passes; Docker build is deterministic | None |
| **M2** | Refactor halal_screener into routers | `/screener`, `/analyze`, `/ml`, `/rl`, `/admin` endpoints moved to `app/routers/`; all existing tests pass | M1 |
| **M3** | Wire USX Pro V4 | `/usx` endpoint uses `usx_pro_filter` module; outputs match documented spec | None |
| **M4** | Merge copilot into main app | `/agent/chat` available on main FastAPI without separate `copilot.py`; Claude integration tests pass | M2 |
| **M5** | Clean orphans | Remove `app/routers/__init__.py` if unused, integrate `keep_alive.py` into app lifespan, relocate `russell1000_halal.py` | M2 |
| **M6** | Activate IBKR (gate: user decision) | IBKR adapter receives real credentials; `factory.get_broker("IBKR")` returns working instance; `tests/test_ibkr_adapter.py` passes | External: IBKR account funding |
