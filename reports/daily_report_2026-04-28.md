# Daily Trading Report — 2026-04-28

**Generated:** 2026-04-28 UTC  
**System Version:** (from local codebase)  
**Environment:** Railway (Production)

---

## System Health

**Status: UNAVAILABLE — API Unreachable (403 Forbidden)**

All Railway endpoints at `https://openbb-trading-production.up.railway.app` returned HTTP 403 Forbidden via WebFetch. The previous report (2026-04-20) was blocked by proxy allowlist (`Host not in allowlist`); today the host resolves but access is denied at the application layer (likely a Railway IP restriction or API key enforcement on all routes).

Parts 1 and 2 (live screening, signal accuracy, Telegram summary) could **not** be collected. Part 3 (code analysis) was completed from local files.

---

## Part 1: Live Stock Screening

> **UNAVAILABLE** — `/api/v1/screen_stocks` and `/api/v1/screening_report` returned HTTP 403.

### Top 10 Stocks by Screening Score
N/A — API unreachable.

### Halal Compliance Flags
N/A — API unreachable.

---

## Part 2: Signal Accuracy

> **UNAVAILABLE** — All `/api/v1/signals/*` endpoints returned HTTP 403. Telegram daily summary endpoint also returned 403.

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

Static analysis of local files. Compared against the 2026-04-20 report.

**Notable update: Both HIGH priority bugs from 2026-04-20 have been resolved in the current codebase.**

---

### 3.1 `halal_screener.py`

**`validate_symbol()` BRK.B Regex — FIXED (was HIGH)**
- Previous report flagged that `validate_symbol()` used `^[A-Z]{1,6}$`, rejecting `BRK.B`.
- Current code at line 120 now uses `^[A-Z]{1,6}(\.[A-Z])?$`, matching the implementation in `market_data.py:139`.
- `BRK.B` will no longer return HTTP 400. **Resolved.**

**Stale Comment: "8GB RAM tier" (LOW — unresolved)**
- Line 1572: `# === ML MODELS (re-enabled with 8GB RAM tier) ===`
- Railway free tier provides 512 MB RAM. This comment is incorrect and misleading — a reader could mistakenly believe ML models are safe to load into memory.
- **Fix:** Update comment to reflect actual environment: `# ML models disabled — Railway free tier (512 MB RAM)`

**`analyze()` Hardcodes `"halal": "Yes"` (LOW — unresolved)**
- Line 451: every stock processed by `analyze()` returns `"halal": "Yes"` regardless of verification source.
- The upstream `_analyze_one()` does call `verify_halal()` first, so the value is functionally correct, but it misleads API consumers who may assume no live check occurred.
- **Recommendation:** Populate from `verify_halal()` reason string, e.g. `"halal": "Verified (AAOIFI)"`.

**`PSKY` Unknown Ticker (LOW — unresolved)**
- Line 225: `PSKY` appears in `_SP500_ALL`. It is not a standard S&P 500 component and causes silent fetch failures on every run.
- **Fix:** Remove `PSKY` from `_SP500_ALL`. If intentional, add a comment explaining its source.

---

### 3.2 `app/services/market_data.py`

**Rate Limiting: Correct**
- Semaphore (max 3 concurrent) + `_alpaca_lock` minimum-interval enforcement is solid.
- Exponential backoff on 429 using `_backoff_next(attempt)` (capped at 30 s) is appropriate.
- 5-minute in-memory TTL cache prevents redundant fetches during market hours (downgraded to 60 s when market is open).

**`fetch_alpaca_intraday()` — No Max Retry Bound on 429 (LOW — unresolved)**
- The intraday path (lines ~311–320) increments `page_retry` and uses `_backoff_next()` for exponential backoff on 429, but has **no upper bound** (`if page_retry >= max_retries: break` is absent).
- Under sustained 429 throttling the loop will back off exponentially (capped at 30 s per attempt) and retry indefinitely until the host connection times out (httpx 30 s client timeout).
- The daily-bar path has an explicit `max_retries = 3` guard. The intraday path should match.
- **Fix:** Add `if page_retry >= 3: break` after the 429 increment, consistent with the daily-bar path.

---

### 3.3 `app/services/halal_screening.py`

**`_fmp_rate_limit()` Thread-Safety — FIXED (was HIGH)**
- Previous report flagged that `_fmp_last_call` was updated without a lock.
- Current code (lines 82–95) correctly uses `_fmp_lock = threading.Lock()` wrapping the read-modify-write, mirroring the Alpaca implementation.
- **Resolved.**

**AAOIFI Thresholds — Correct**
| Screen | Threshold | Status |
|--------|-----------|--------|
| Debt / Market Cap | < 33% | ✓ Correct |
| Interest Income / Revenue | < 5% | ✓ Correct |
| Haram sector/industry | Excluded list | ✓ Correct |
| Liquidity / Market Cap | < 33% | ✓ Correct |

**`packaged_foods` / `entertainment` Comment vs. Behavior (MEDIUM — unresolved)**
- `HARAM_INDUSTRIES` (lines 72–80) includes `"packaged foods"` and `"entertainment"` with inline comments reading "flagged for manual review, not auto-disqualified."
- The code sets `haram_industry_flag = True` for these, which flows to `haram_pass = False` → `is_halal = False`. They **are** auto-disqualified, contrary to the comment.
- **Fix:** Either move these to a separate `_REVIEW_INDUSTRIES` set (and handle separately), or correct the comments to read "auto-disqualified pending manual review."

**`batch_screen()` FMP Daily Limit Margin (LOW)**
- `max_per_run=80` × 3 FMP calls = 240 calls vs. 250/day limit. The 10-call safety margin is consumed by a single retry burst on a transient failure.
- **Recommendation:** Lower to `max_per_run=75` (225 calls, 25-call margin) or add a call counter with early abort.

---

### 3.4 `app/services/signal_tracker.py`

**SELL Signal Outcome Direction — FIXED (was MEDIUM)**
- Previous report flagged that SELL wins were calculated as losses.
- Current code at line 61–62:
  ```python
  is_sell = "SELL" in sig_text and "BUY" not in sig_text
  ret_pct = -price_chg if is_sell else price_chg
  ```
  SELL signals are now correctly direction-normalised (price drop = profit = positive `ret_pct`).
- `get_accuracy_report()` now also includes SELL signals in the `actionable` list (lines 112–115).
- **Resolved.**

**`lookback_days=5` Default Hardcoded (LOW — unresolved)**
- `check_signal_outcomes()` defaults to `lookback_days=5`. The calling endpoint at line 3782 exposes this as a query parameter, but the default is not driven by `settings`, making it hard to adjust without code changes for different trade horizons (e.g., swing trades held 10+ days).
- **Recommendation:** Add `SIGNAL_LOOKBACK_DAYS: int = 5` to `settings` and use it as the default.

**Accuracy Calculations: Correct**
- Win rate, profit factor, avg win/loss are mathematically sound.
- Division-by-zero guard on profit factor is in place.

---

### 3.5 `app/services/telegram_alert.py`

**Error Handling: Good**
- All send functions catch exceptions and log without re-raising — Telegram failures never crash the main flow.
- Chart-to-text fallback in `alert_signal_with_chart()` is solid.

**No Retry on Transient Send Failures (LOW — unresolved)**
- `send_message()` delegates to `queued_send_message` (notify.py). Whether that layer retries is outside this file's scope, but if it does not, a transient Telegram API hiccup silently drops the alert.
- **Recommendation:** Verify that `notify.py`'s send queue implements at least 1 retry with 2 s backoff.

**`_sent_signals` Dedup Resets on Restart (LOW — unresolved)**
- In-memory dict resets on any container restart (deploys, OOM kills) → potential duplicate alerts within the 30-minute dedup window post-restart.
- **Recommendation:** Accept as a known limitation and document it, or persist to the DB.

**`alert_consensus()` Silent HOLD Return (LOW — unresolved)**
- Lines 117–118: returns `False` for HOLD/NEUTRAL with no log entry, making it invisible in traces.
- **Fix:** Add `logger.debug(f"Skipping HOLD consensus for {symbol}")` before the return.

---

## Summary of Code Issues

| Priority | Status | File | Issue |
|----------|--------|------|-------|
| HIGH | **FIXED** | `halal_screener.py` | `validate_symbol()` rejected `BRK.B` — now uses correct regex |
| HIGH | **FIXED** | `halal_screening.py` | `_fmp_rate_limit()` was not thread-safe — lock added |
| MEDIUM | **FIXED** | `signal_tracker.py` | SELL signal outcomes were computed in wrong direction |
| MEDIUM | OPEN | `halal_screening.py` | `packaged_foods`/`entertainment` comment contradicts behavior |
| LOW | OPEN | `halal_screener.py` | Line 1572 comment references "8GB RAM tier" — Railway free tier is 512 MB |
| LOW | OPEN | `halal_screener.py` | `analyze()` hardcodes `"halal": "Yes"` — misleading for API consumers |
| LOW | OPEN | `halal_screener.py` | `PSKY` is not a valid S&P 500 ticker — silent fetch failures each run |
| LOW | OPEN | `market_data.py` | `fetch_alpaca_intraday()` has no max retry count on 429 (infinite loop risk) |
| LOW | OPEN | `signal_tracker.py` | `lookback_days=5` default not driven by `settings` |
| LOW | OPEN | `telegram_alert.py` | No confirmed retry on send failure — silent drops on transient network errors |
| LOW | OPEN | `telegram_alert.py` | `_sent_signals` dedup resets on server restart |
| LOW | OPEN | `telegram_alert.py` | `alert_consensus()` returns `False` for HOLD with no log entry |

---

## Progress Since 2026-04-20

| Bug | Prior Status | Current Status |
|-----|-------------|----------------|
| `validate_symbol()` BRK.B regex | OPEN (HIGH) | **FIXED** |
| `_fmp_rate_limit()` thread safety | OPEN (HIGH) | **FIXED** |
| SELL signal outcome direction | OPEN (MEDIUM) | **FIXED** |
| `packaged_foods`/`entertainment` comment | OPEN (MEDIUM) | OPEN |
| All LOW items (×7) | OPEN | OPEN |

3 of 10 tracked issues resolved. Both HIGH priority items cleared.

---

## Notes

- Railway API returned HTTP 403 Forbidden on all endpoints — different failure mode from the 2026-04-20 proxy allowlist block. Possible causes: Railway firewall rules, application-level auth enforcement, or deployment issue. Recommend checking Railway dashboard logs.
- No API data could be collected for Parts 1 or 2. Re-run with direct network access or from within the Railway environment to collect live data.
- All code findings are from static analysis of local files only.

---

*Report generated: 2026-04-28 UTC*
