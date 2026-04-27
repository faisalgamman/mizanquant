# Daily Halal Trading Report — 2026-04-27

**Generated:** 2026-04-27  
**Agent:** Claude (claude-sonnet-4-6)

---

## System Health

| Component | Status | Notes |
|-----------|--------|-------|
| Railway API | ❌ UNAVAILABLE | Host blocked by network allowlist during report generation |
| `/api/v1/health` | ❌ Unreachable | Could not connect |
| `/api/v1/screen_stocks` | ❌ Unreachable | Could not connect |
| `/api/v1/screening_report` | ❌ Unreachable | Could not connect |
| `/api/v1/signals/check_outcomes` | ❌ Unreachable | Could not connect |
| `/api/v1/signals/accuracy` | ❌ Unreachable | Could not connect |
| `/api/v1/signals/history` | ❌ Unreachable | Could not connect |
| `/api/v1/telegram/daily_summary` | ❌ Unreachable | Could not connect |

> **Note:** All Railway API endpoints returned "Host not in allowlist" — the agent's execution environment does not have outbound access to `openbb-trading-production.up.railway.app`. Live stock data, signal accuracy metrics, and Telegram notifications were not available for this report. Code analysis (Part 3) was completed in full.

---

## Top 10 Stocks by Screening Score

> **N/A — API Unavailable**

Live screening data could not be fetched. Refer to the previous report (`daily_report_2026-04-20.md`) or access the Railway dashboard directly at `https://openbb-trading-production.up.railway.app/api/v1/screen_stocks`.

---

## Halal Compliance Flags

> **N/A — API Unavailable**

AAOIFI compliance report could not be fetched. Review thresholds currently configured in code:

| Ratio | Threshold | Standard |
|-------|-----------|---------|
| Debt / Market Cap | < 33% | AAOIFI |
| Interest Income / Revenue | < 5% | AAOIFI |
| Liquid Assets / Market Cap | < 33% | AAOIFI |

---

## Signal Accuracy — 7-Day

> **N/A — API Unavailable**

---

## Signal Accuracy — 30-Day

> **N/A — API Unavailable**

---

## Top & Worst Performing Signal Sources

> **N/A — API Unavailable**

---

## Last 10 Signals with Outcomes

> **N/A — API Unavailable**

---

## Code Analysis

### Files Reviewed
1. `halal_screener.py` (4,709 lines)
2. `app/services/market_data.py` (412 lines)
3. `app/services/halal_screening.py` (486 lines)
4. `app/services/signal_tracker.py` (205 lines)
5. `app/services/telegram_alert.py` (319 lines)

---

### 🔴 Critical Issues (Risk of Wrong Trades / Data Loss)

**1. `signal_tracker.py` — Signal direction normalization is fragile**
- Lines 57–62: `is_sell = "SELL" in sig_text and "BUY" not in sig_text`
- If signal text contains both keywords (e.g. "BUY on oversold, SELL setup"), it silently defaults to BUY
- Outcome returns and win/loss classification will be wrong for such signals
- **Fix:** Use an explicit field for direction, not text parsing

**2. `halal_screening.py` — `abs()` on interest income masks sign**
- Line 282: `abs(interest_income) / revenue * 100`
- For financial companies, interest income is positive (they earn it); for others it's usually an expense (negative)
- Using `abs()` hides which direction the figure flows, potentially marking interest-earning firms as compliant
- **Fix:** Accept signed value and document the expected sign convention per data source

**3. `halal_screener.py` — `SEQ_LEN = 20` duplicated in 3 places**
- Lines 687, 723, 759: each ML function re-declares `SEQ_LEN = 20` locally
- A change in one location will silently diverge from the others
- **Fix:** Declare `ML_SEQUENCE_LENGTH = 20` as a module-level constant

---

### 🟠 High Issues (Data Integrity / API Reliability)

**4. `halal_screener.py` — `fold_scores` always empty (dead code)**
- Lines 695, 731: `fold_scores = []` is initialized but never populated
- `avg_mse = ... if fold_scores else 0.0` always returns `0.0`
- Either finish the cross-validation implementation or remove the dead code

**5. `market_data.py` — No validation of `start`/`end` date parameters**
- Lines 169–175: date strings passed directly to Alpaca without format validation
- A malformed date will cause a hard API failure with no informative error
- **Fix:** Validate ISO 8601 format before calling API

**6. `halal_screening.py` — FMP API migration comment is stale**
- Line 106: `# FMP migrated to /stable/ endpoints (Aug 2025)`
- Current date is April 2026 — if this migration is complete, remove the comment; if incomplete, it is a bug
- **Fix:** Verify current FMP endpoint and remove or update the comment

**7. `telegram_alert.py` — Message length not validated**
- Telegram enforces a 4,096 character limit per message
- Daily summaries listing many stocks could silently truncate or return a 400 error
- **Fix:** Truncate or paginate messages before sending

**8. `signal_tracker.py` — Silent failures in outcome checking**
- Line 70: `except Exception: logger.debug(...)` — exceptions are swallowed at debug level
- Signals can silently fail to record outcomes, inflating the "pending" count indefinitely
- **Fix:** Log at WARNING or ERROR, and consider a dead-letter mechanism

---

### 🟡 Medium Issues (Performance / Correctness)

**9. `halal_screening.py` — Batch screening doesn't track daily FMP API usage**
- FMP free tier: ~250 requests/day
- Each symbol requires ~3 FMP calls; `max_per_run=80` = ~240 calls per batch
- Multiple runs in the same day will silently exceed the daily limit
- **Fix:** Persist a daily call counter (Redis or DB) and enforce a hard cap

**10. `market_data.py` — `yfinance` fallback has no rate limiting**
- When Alpaca fails, code falls back to yfinance with no throttle
- Under sustained Alpaca outages, yfinance could burst and trigger bans
- **Fix:** Apply the same semaphore/min-interval pattern to yfinance calls

**11. `halal_screening.py` — Sector/industry matching case-sensitive mismatch risk**
- Haram industry strings include em-dashes (`"beverages—brewers"`) but API may return hyphens or spaces
- **Fix:** Normalize both sides to lowercase + ASCII punctuation before comparison

**12. `telegram_alert.py` — Deduplication key too coarse**
- Dedup key is `f"{symbol}_{verdict}"` with a 30-minute window
- Price movements of 5%+ within the window produce no re-alert
- **Fix:** Include price bucket or score range in the dedup key

**13. `signal_tracker.py` — Break-even trades classified as losses**
- Line 118: `losses = [r for r in act_returns if r <= 0]` — 0.0% return is a "loss"
- Distorts win rate and profit factor
- **Fix:** Add a `break_even` bucket (e.g. `abs(r) < 0.05%`)

---

### 🔵 Low Issues (Code Quality)

**14. `halal_screener.py` — Unicode smart quotes in comments**
- Line 38: Non-ASCII curly quotes could cause encoding issues on some systems
- **Fix:** Replace with standard ASCII apostrophes/quotes

**15. `telegram_alert.py` — Strategy IDs hardcoded**
- Line 266: `for sid in ("A", "B", "C")` — should iterate over `STRATEGY_CONFIGS.keys()`
- Adding a new strategy requires editing two separate locations

**16. `halal_screening.py` — `haram_revenue` field name is confusing**
- Line 309: `"haram_revenue": not haram_pass` — the boolean semantics are inverted
- Rename to `haram_disqualified` for clarity

---

### Hardcoded Values Summary

| File | Value | Location | Recommendation |
|------|-------|----------|----------------|
| `halal_screener.py` | `SEQ_LEN = 20` | Lines 687, 723, 759 | Module constant |
| `halal_screener.py` | `BACKTEST_COST_BPS = 20.0` | Line 480 | Move to settings |
| `halal_screener.py` | Score thresholds (75, 55, 35) | Lines 425–435 | Move to settings |
| `market_data.py` | `_ALPACA_MIN_INTERVAL = 0.25` | Line 108 | Acceptable, document unit |
| `market_data.py` | `max_retries = 3` | Line 188 | Move to settings |
| `halal_screening.py` | AAOIFI thresholds (33%, 5%) | Lines 6–9 | Move to settings |
| `halal_screening.py` | `max_per_run = 80` | Line 439 | Move to settings |
| `signal_tracker.py` | `lookback_days = 5` | Line 28 | Acceptable default |
| `telegram_alert.py` | `_DEDUP_WINDOW = 1800` | Line 47 | Move to settings |
| `telegram_alert.py` | Strategy IDs `("A","B","C")` | Line 266 | Use config keys |

---

### Rate Limiting Matrix

| Service | Concurrency Control | Min Interval | Retry Logic | Fallback |
|---------|--------------------|-----------   |-------------|----------|
| Alpaca | Semaphore(3) | 0.25s | 3x exp. backoff | yfinance |
| FMP | None | 0.5s sleep | 60s sleep on 429 | yfinance |
| Telegram | None | None | No retry | Text fallback |
| yfinance | None | None | None | None |

**Recommendation:** Standardize retry logic across all external services using a shared `retry_with_backoff()` utility.

---

### Top Improvement Recommendations (Priority Order)

1. **Fix signal direction parsing** — use an explicit `direction` enum, not text search
2. **Fix interest income sign convention** — document and enforce per data source
3. **Promote `SEQ_LEN` to module constant** — eliminate 3-way duplication
4. **Finish or remove `fold_scores` dead code** — misleading metric always returns 0
5. **Add FMP daily call counter** — prevent silent quota exhaustion on free tier
6. **Validate Telegram message length** — prevent silent truncation or 400 errors
7. **Upgrade signal outcome error logging** to WARNING level
8. **Add rate limiting to yfinance fallback**
9. **Normalize sector/industry strings** before haram matching
10. **Standardize retry logic** across Alpaca, FMP, and Telegram into one utility

---

*Report generated by Claude (claude-sonnet-4-6) — 2026-04-27*  
*Railway API: UNAVAILABLE (host not in agent network allowlist)*  
*Code analysis: COMPLETE (5 files reviewed)*
