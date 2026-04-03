# Daily Trading Report — 2026-04-03

**Generated:** 2026-04-03 07:23 UTC  
**System Version:** 17.0.0  
**Environment:** Railway (Production)

---

## System Health

**Status: UNAVAILABLE — API Unreachable**

The Railway deployment at `https://openbb-trading-production.up.railway.app` could not be reached during this run. The outbound proxy returned HTTP 403 (host_not_allowed), indicating the production API URL is blocked from this execution environment.

All Part 1 and Part 2 data (stock screening, signal accuracy, Telegram summary) could **not** be collected. Code analysis (Part 3) was completed successfully from local files.

---

## Part 1: Live Stock Screening

> **UNAVAILABLE** — `/api/v1/screen_stocks` and `/api/v1/screening_report` could not be reached.

**Top 10 Stocks by Screening Score:** N/A (API unreachable)  
**Halal Compliance Flags:** N/A

---

## Part 2: Signal Accuracy

> **UNAVAILABLE** — All `/api/v1/signals/*` endpoints could not be reached.

### 7-Day Signal Accuracy
| Metric | Value |
|--------|-------|
| Win Rate | N/A |
| Profit Factor | N/A |
| Total Signals | N/A |

### 30-Day Signal Accuracy
| Metric | Value |
|--------|-------|
| Win Rate | N/A |
| Profit Factor | N/A |
| Total Signals | N/A |

### Top Performing Signal Sources
N/A

### Worst Performing Signal Sources
N/A

### Last 10 Signals with Outcomes
N/A

---

## Part 3: Code Analysis

### 3.1 `halal_screener.py`

**Symbol Validation Inconsistency (Bug)**
- `validate_symbol()` (line 68–72) uses regex `^[A-Z]{1,6}$`, which **rejects dot-notation symbols** like `BRK.B`.
- However, `BRK.B` is present in `_SP500_ALL` (line 127) and will be included in `HALAL_STOCKS`.
- Any API endpoint using `validate_symbol()` called with `BRK.B` will return HTTP 400.
- The `_validate_symbol()` function in `market_data.py` correctly supports `BRK.B` via `^[A-Z]{1,6}(\.[A-Z])?$`.
- **Fix:** Align `validate_symbol()` regex to match the one in `market_data.py`.

**`analyze()` Hardcodes `"halal": "Yes"` (Misleading)**
- `analyze()` (line 266) always returns `"halal": "Yes"` for every stock, regardless of AAOIFI screening.
- This is because `run_screener()` iterates only over `HALAL_STOCKS` (pre-filtered list), so it implies these are candidates, not confirmed-halal.
- However, the UI/API consumers may interpret the field literally.
- **Recommendation:** Return `"halal": "Candidate"` or populate from `get_halal_status()` cache when available, to avoid misleading display.

**`PSKY` Ticker in Stock Universe**
- `PSKY` appears in `_SP500_ALL` (line 151) but is not a recognized S&P 500 component. This may cause consistent data-fetch failures without surfacing any error to the operator.

**Memory Management**
- `MODEL_CACHE_TTL` is correctly capped at 5 minutes for Railway's free tier (line 182). Good defensive coding.
- `_model_semaphore` (line 49) correctly prevents concurrent model training to avoid OOM.

**CORS**
- `allow_origins=["*"]` (line 56) accepts requests from any domain. Acceptable for a public screener widget but note that authentication/authorization is not enforced at the CORS layer.

---

### 3.2 `app/services/market_data.py`

**Rate Limiting: Well Implemented**
- Semaphore (max 3 concurrent Alpaca requests) + `_alpaca_lock` for minimum interval enforcement is correct.
- Exponential backoff on HTTP 429 (delays: 2s, 4s, 8s across 3 attempts) is appropriate for the Alpaca IEX free tier (200 req/min).
- 5-minute in-memory cache (`_DATA_CACHE_TTL = 300`) prevents redundant fetches during a screener run.

**Pagination Loop Logic (Minor)**
- In `fetch_alpaca()` (lines 142–182), the `while True` / `for attempt` structure is technically correct but reads confusingly. The `else` clause on the `while` (line 177) is never reached in normal operation. No functional bug, but reduces readability.

**Symbol Validation**
- `_validate_symbol()` uses `^[A-Z]{1,6}(\.[A-Z])?$` — correctly handles `BRK.B`.
- `_BAD_SYMBOLS` set ("LIY", "LIYY", "") is appropriate for known-bad tickers.

**Minimum Data Requirement**
- Both `fetch_alpaca()` and `fetch_yf()` return `None` if fewer than 200 bars are available (lines 176, 216). This is a safe guard that prevents analysis on insufficient history, but could silently drop valid tickers with short listing histories (e.g., recent IPOs).

---

### 3.3 `app/services/halal_screening.py`

**AAOIFI Screening Thresholds**
- Debt Screen: Total Debt / Market Cap < **33%** ✓
- Interest Income Screen: Interest Income / Revenue < **5%** ✓
- Haram Revenue: Sector/industry exclusion list ✓
- Liquidity Screen: (Cash + Interest-Bearing Securities) / Market Cap < **33%** ✓

All four thresholds match AAOIFI Sharia Standard No. 21.

**`_fmp_rate_limit()` is NOT Thread-Safe (Bug)**
- `_fmp_rate_limit()` (lines 63–69) reads/writes `_fmp_last_call` without a lock.
- The Alpaca equivalent correctly uses `threading.Lock()`. FMP's rate limiter lacks this.
- Under `ThreadPoolExecutor` (used in `run_screener()`), multiple threads could simultaneously pass the check, resulting in burst requests that exceed the FMP 250/day limit.
- **Fix:** Add a `threading.Lock()` around `_fmp_rate_limit()`, mirroring the Alpaca implementation.

**`packaged_foods` / `entertainment` Flagging**
- These industries are in `HARAM_INDUSTRIES` with a comment "flagged for manual review, not auto-disqualified" — but `haram_industry_flag` is still set to `True` for them (line 132), which causes `haram_pass = False` and `is_halal = False`.
- Comment says "not auto-disqualified" but the code does auto-disqualify them. This is a discrepancy between intent (comment) and behavior (code).
- **Clarify:** Either remove these from `HARAM_INDUSTRIES` and add a separate `REVIEW_INDUSTRIES` set, or update the comment to reflect auto-disqualification.

**Database Session Handling**
- `screen_and_store()` uses `try/finally` to close the session — correct pattern.
- `get_halal_status()` reads `row.details` after `db.close()` (line 267). If `details` is a lazy-loaded relationship, this would raise a `DetachedInstanceError`. Worth verifying that `details` is a plain JSON column (not a relationship).

**`batch_screen()` Daily Limit**
- `max_per_run=80` → 240 FMP API calls (3 per symbol) vs. 250/day free limit. Safe margin is only 10 calls — any unexpected profile/balance-sheet/income retries could tip over the limit.

---

### 3.4 `app/services/signal_tracker.py`

**Accuracy Calculations: Correct**
- Win rate, avg win/loss, and profit factor calculations are mathematically sound.
- Profit factor guard `if buy_losses and sum(buy_losses) != 0` (lines 115, 135) correctly avoids division by zero.

**Outcome Direction Assumption (Minor)**
- `check_signal_outcomes()` computes return as `(current_price - signal.price) / signal.price * 100` regardless of signal direction (BUY or SELL).
- For SELL signals, a negative return should be counted as a WIN, but the current formula treats positive returns as wins for all signals.
- `get_accuracy_report()` partially corrects this by filtering on `"BUY" in signal`, but SELL signal accuracy is never computed.
- **Recommendation:** Record signal direction in `outcome_return_pct` computation, or add a `direction`-aware win/loss classification.

**Signal Maturity Window**
- `check_signal_outcomes()` uses a 5-day lookback (`lookback_days=5`) to determine if a signal is "mature enough" to evaluate. This is reasonable for swing trades but may be too short for position trades. Consider making this configurable via `settings`.

**No Outcome Staleness Handling**
- Once `outcome_price` is set, it is never updated. If a signal was checked at 5 days but the trade horizon is 20 days, the recorded outcome may not reflect the actual trade result. Acceptable for a paper-trading baseline but worth noting.

---

### 3.5 `app/services/telegram_alert.py`

**Error Handling: Good**
- `send_message()` and `send_photo()` both catch all exceptions and log errors without raising, ensuring Telegram failures never crash the main flow.
- Fallback from chart-with-photo to text-only in `alert_signal_with_chart()` (lines 284–290) is a solid resilience pattern.

**No Retry on Send Failure**
- `send_message()` makes a single HTTP POST attempt. Telegram's API is generally reliable, but transient network issues (especially on Railway) could cause silent failures.
- **Recommendation:** Add 1–2 retries with short delay (e.g., 2s) before giving up, similar to the Alpaca rate-limit retry pattern already in the codebase.

**`_sent_signals` In-Memory Dedup**
- The deduplication dict `_sent_signals` (line 37) resets on every server restart. If Railway restarts the container (which happens on deploys or OOM), duplicate alerts could be sent immediately after restart.
- **Recommendation:** Consider persisting the sent-signal log to the database, or accept this as a known limitation.

**`alert_consensus()` Silent Return for HOLD**
- `alert_consensus()` returns `False` for HOLD/NEUTRAL signals (line 159) without any log message. This is fine by design but adds opacity — consider a `logger.debug()` for traceability.

**Caption Length**
- `send_photo()` correctly truncates captions to 1024 characters (Telegram limit). Good.

**`parse_mode` Not Set**
- `send_message()` does not set `parse_mode` in the payload, so Markdown/HTML formatting in message text (like `**bold**` or `<b>`) will be sent as literal characters. Intentional for plain-text alerts, but worth documenting.

---

## Summary of Code Issues

| Priority | File | Issue |
|----------|------|-------|
| HIGH | `halal_screener.py` | `validate_symbol()` rejects `BRK.B` — breaks all endpoints for dot-notation tickers |
| HIGH | `halal_screening.py` | `_fmp_rate_limit()` not thread-safe — may burst through 250/day FMP limit |
| MEDIUM | `halal_screening.py` | `packaged_foods`/`entertainment` comment says "not auto-disqualified" but code does auto-disqualify |
| MEDIUM | `signal_tracker.py` | SELL signal outcomes calculated wrong direction (treated as loss when price rises) |
| LOW | `halal_screener.py` | `analyze()` hardcodes `"halal": "Yes"` — misleading for un-screened symbols |
| LOW | `halal_screener.py` | `PSKY` is not a valid S&P 500 ticker — causes silent fetch failures |
| LOW | `telegram_alert.py` | No retry on send failure — silent drops on transient network issues |
| LOW | `telegram_alert.py` | `_sent_signals` dedup resets on server restart — potential duplicate alerts |

---

## Notes

- Railway API was **unreachable** from this execution environment (proxy 403). No live data was collected.
- All code findings are based on static analysis of local files only.
- Re-run with direct network access to collect live screening and signal data.

---

*Report generated: 2026-04-03 07:23 UTC*
