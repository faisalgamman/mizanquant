# Development Plan - OpenBB Halal Trading System

## Completed

### Phase 1: Critical Deployment Fixes (DONE)
1. Removed hardcoded `sys.path.insert(0, "D:/Stock-Prediction-Models-master/openbb-forecast")` - broke Railway
2. Fixed Dockerfile: cleaned corrupted CMD line, uses `${PORT:-8000}` for Railway
3. Added 3 missing widget definitions: consensus, batch_consensus, pipeline
4. Imported `keep_alive.py` to prevent Railway sleep
5. Fixed `__main__` to use `os.environ.get("PORT")` and bind `0.0.0.0`
6. Bumped version to 11.1.0

### Phase 2: Bug Fixes (DONE)
1. Fixed VaR/CVaR edge case in `monte_carlo.py` - broke when all returns positive
2. Fixed VaR/CVaR edge case in `risk/metrics.py` `conditional_var()` - same issue
3. Fixed directional accuracy in `models/base.py` - was computing prediction error, not direction
4. Fixed log-return vs simple-return mismatch in `router.py` - was passing log returns to simple-return formula
5. Made `annualized_return()` in `risk/metrics.py` handle both log and simple returns
6. Fixed `"usx_filtered" in dir()` in pipeline - initialized variable before try/except
7. Fixed Double DQN docstring - said per-step but code does per-episode epsilon decay

### Phase 3: Performance Optimization (DONE)
1. Extracted `score_usx_single()` — consensus no longer scans all stocks for USX (3 min → instant)
2. Added `ThreadPoolExecutor` concurrent fetching to screener, USX screener, and pipeline quick filter (3-5x faster)
3. Added model caching for LSTM and Transformer with 1-hour TTL (repeat calls: minutes → <1s)
4. Added background job system for `/pipeline` and `/batch_consensus` — returns job_id, poll via `/job/{id}`
5. All sub-tools (`run_lstm`, `run_transformer`, `run_ensemble`, `run_monte_carlo`, `run_dqn`, `run_policy_gradient`) accept optional `df=` to avoid re-fetching
6. Rewrote `run_consensus` to pass shared `df` to all 9 tools — eliminates redundant yfinance calls
7. Bumped version to 11.2.0

---

### Phase 4: Production Hardening (DONE)
1. Added `with_timeout()` wrapper — all endpoints have asyncio timeout (120s default, 300s for consensus/pipeline)
2. Added `validate_symbol()`, `validate_date()`, `validate_range()` input validators with proper HTTPException (400)
3. All endpoints return proper HTTP status codes (400, 429, 504) via FastAPI `HTTPException`
4. Health check now verifies yfinance connectivity and openbb_forecast import — returns "degraded" if deps are down
5. Added in-memory rate limiter `check_rate_limit()` — expensive endpoints limited to 2 req/min
6. Bumped version to 11.4.0

---

## Remaining Phases

### Phase 5: Feature Enhancements

#### 5.1 - Sentiment analysis integration
**Notes:** `add_sentiment.py` already exists with NLTK-based sentiment analysis code. Integrate as a 10th voting tool in consensus.
**Files:** `halal_screener.py` - add sentiment function, update `run_consensus()` to include it
**Dependencies:** `nltk` (already in Dockerfile)

#### 5.2 - Portfolio tracking
**Notes:** `add_portfolio.py` has portfolio position tracking code. Add endpoints for tracking open positions, P&L, and portfolio allocation.
**Files:** New endpoints in `halal_screener.py`

#### 5.3 - Webhook notifications
**Notes:** Send alerts when new STRONG BUY signals appear. Could use a simple webhook to Telegram/Discord/Slack.
**Files:** New notification module

#### 5.4 - Historical consensus tracking
**Notes:** Store daily consensus results to track signal accuracy over time. Use SQLite or simple JSON file.
**Files:** New storage layer

#### 5.5 - OpenBB Pro dashboard templates
**Notes:** Create pre-configured dashboard layouts for common workflows (screening, analysis, monitoring).
**Files:** Dashboard configuration files
