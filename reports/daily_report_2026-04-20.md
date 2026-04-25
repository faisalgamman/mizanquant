# Daily Trading Report — 2026-04-20

**Generated:** 2026-04-20 UTC  
**System Version:** 17.0.0  
**Environment:** Railway (Production)

---

## System Health

**Status: UNAVAILABLE — API Unreachable**

The Railway deployment at `https://openbb-trading-production.up.railway.app` could not be reached. The outbound proxy returned `Host not in allowlist`, indicating the production API URL is blocked from this execution environment (same constraint as 2026-04-03).

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

All findings are from static analysis of local files. Compared against the 2026-04-03 report — **no issues from that report have been resolved**. One new stale-comment issue is noted below.

---

### 3.1 `halal_screener.py`

**BRK.B Symbol Validation (Bug — HIGH, unresolved since 2026-04-03)**
- `validate_symbol()` (line 69–73) uses regex `^[A-Z]{1,6}$`, which rejects `BRK.B`.
- `BRK.B` is present in `_SP500_ALL` and thus in `HALAL_STOCKS` — any endpoint calling `validate_symbol("BRK.B")` returns HTTP 400.
- `_validate_symbol()` in `market_data.py` correctly uses `^[A-Z]{1,6}(\.[A-Z])?$`.
- **Fix:** Align regex in `validate_symbol()` to `^[A-Z]{1,6}(\.[A-Z])?$`.

**`analyze()` Hardcodes `"halal": "Yes"` (LOW, unresolved)**
- `analyze()` line 388 unconditionally returns `"halal": "Yes"` for every stock it processes.
- The calling code (`_analyze_one`, line 393–400) runs `verify_halal()` first and only calls `analyze()` for confirmed-halal stocks — so the field is technically always accurate, but the hardcoded string is misleading to API consumers who may think no live check was done.
- **Recommendation:** Return `"halal": "Verified"` or populate from the `verify_halal()` reason string.

**Stale `MODEL_CACHE_TTL` Comment (NEW — LOW)**
- Line 232: comment reads `# 8GB RAM tier — can hold more models for faster consensus`.
- Railway free tier is 512 MB. The 8 GB comment is stale/misleading; `MODEL_CACHE_MAX = 20` is already constrained by `MODEL_CACHE_TTL = min(..., 1800)` (line 233), which mitigates actual OOM risk, but the comment should be corrected to avoid confusion.

**`PSKY` Unrecognized Ticker (LOW, unresolved)**
- `PSKY` in `_SP500_ALL` (line 151) is not a standard S&P 500 component. Causes silent per-run fetch failures without surfacing to the operator.

**`_HARAM_EXCLUDE` vs `_SP500_ALL` Deduplication**
- Several tickers appear in both `_SP500_ALL` and `_HARAM_EXCLUDE` (e.g., `AEE`, `AMT`, `AXP`). This is intentional — `HALAL_STOCKS` filters them out — but the duplication in `_SP500_ALL` is redundant. No functional bug.

---

### 3.2 `app/services/market_data.py`

**Rate Limiting: Well Implemented (no new issues)**
- Semaphore (max 3 concurrent) + `_alpaca_lock` for minimum interval is correct.
- Exponential backoff (2s → 4s → 8s) on HTTP 429 is appropriate for the IEX free tier.
- 5-minute in-memory cache (`_DATA_CACHE_TTL = 300`) prevents redundant fetches.

**`fetch_alpaca_intraday()` — Single 429 Retry (LOW, unresolved)**
- Intraday path (lines 258–261) retries only once on HTTP 429, sleeping a flat 2 seconds. The daily-bar path uses exponential backoff with 3 attempts. Should be unified for consistency.

**Minimum 200-Bar Requirement**
- Both `fetch_alpaca()` and `fetch_yf()` return `None` for fewer than 200 bars. Silently drops recent IPOs. Acceptable trade-off, worth documenting for ops awareness.

---

### 3.3 `app/services/halal_screening.py`

**AAOIFI Thresholds — Correct**
| Screen | Threshold | Status |
|--------|-----------|--------|
| Debt / Market Cap | < 33% | ✓ |
| Interest Income / Revenue | < 5% | ✓ |
| Haram sector/industry | Excluded list | ✓ |
| Liquidity / Market Cap | < 33% | ✓ |

All four thresholds match AAOIFI Sharia Standard No. 21.

**`_fmp_rate_limit()` Not Thread-Safe (HIGH, unresolved since 2026-04-03)**
- `_fmp_rate_limit()` (lines 82–88) reads/writes `_fmp_last_call` without a lock.
- Under `ThreadPoolExecutor`, multiple threads can bypass the rate check simultaneously, causing burst requests that exhaust the 250/day FMP free-tier limit.
- **Fix:** Add `threading.Lock()` mirroring the Alpaca implementation in `market_data.py`.

**`packaged_foods` / `entertainment` Comment vs. Behavior Discrepancy (MEDIUM, unresolved)**
- `HARAM_INDUSTRIES` includes `"packaged foods"` and `"entertainment"` with comments saying "flagged for manual review, not auto-disqualified."
- The code sets `haram_industry_flag = True` for these, which flows to `haram_pass = False` → `is_halal = False` — they ARE auto-disqualified.
- **Fix:** Either remove from `HARAM_INDUSTRIES` into a separate `REVIEW_INDUSTRIES` set, or correct the comments.

**`batch_screen()` Daily Limit Margin**
- `max_per_run=80` → 240 FMP calls vs. 250/day limit. 10-call safety margin is thin; retry bursts on failures could exceed it.

---

### 3.4 `app/services/signal_tracker.py`

**SELL Signal Win Direction (MEDIUM, unresolved since 2026-04-03)**
- `check_signal_outcomes()` computes `(current_price - entry_price) / entry_price * 100` for all signals regardless of direction.
- For SELL signals a price drop is a WIN, but the formula treats it as a loss.
- `get_accuracy_report()` filters on `"BUY" in signal`, so SELL accuracy is never computed at all — it simply vanishes from reporting.
- **Fix:** Add a `direction` field to the outcome computation, or skip SELL signal outcome tracking with an explicit log note.

**Signal Maturity Window Not Configurable**
- Hardcoded `lookback_days=5` in `check_signal_outcomes()`. Should be exposed via `settings` for different trade horizons.

**Accuracy Calculations: Correct**
- Win rate, profit factor, avg win/loss are mathematically sound.
- Division-by-zero guard on profit factor is in place.

---

### 3.5 `app/services/telegram_alert.py`

**Error Handling: Good**
- All send functions catch exceptions and log without raising — Telegram failures never crash the main flow.
- Chart-to-text fallback in `alert_signal_with_chart()` is solid.

**No Retry on Transient Failures (LOW, unresolved)**
- `send_message()` makes a single POST attempt. A 1–2 retry with 2s back-off would improve reliability on Railway's network.

**`_sent_signals` Dedup Resets on Restart (LOW, unresolved)**
- In-memory dict resets on any container restart (deploys, OOM kills) → potential duplicate alerts post-restart. Consider persisting to DB or accepting as known limitation.

**`alert_consensus()` Silent HOLD Return**
- Returns `False` for HOLD/NEUTRAL with no log entry. A `logger.debug()` would aid traceability.

---

## Summary of Code Issues

| Priority | Status | File | Issue |
|----------|--------|------|-------|
| HIGH | OPEN | `halal_screener.py` | `validate_symbol()` rejects `BRK.B` — HTTP 400 for dot-notation tickers |
| HIGH | OPEN | `halal_screening.py` | `_fmp_rate_limit()` not thread-safe — can burst through 250/day FMP limit |
| MEDIUM | OPEN | `halal_screening.py` | `packaged_foods`/`entertainment` comment says "not auto-disqualified" but code does disqualify |
| MEDIUM | OPEN | `signal_tracker.py` | SELL signal outcomes computed in wrong direction |
| LOW | NEW | `halal_screener.py` | `MODEL_CACHE_TTL` comment references "8GB RAM tier" — stale, Railway free tier is 512 MB |
| LOW | OPEN | `halal_screener.py` | `analyze()` hardcodes `"halal": "Yes"` — misleading for API consumers |
| LOW | OPEN | `halal_screener.py` | `PSKY` is not a valid S&P 500 ticker — silent fetch failures each run |
| LOW | OPEN | `market_data.py` | `fetch_alpaca_intraday()` uses flat 2s retry vs. exponential backoff in daily path |
| LOW | OPEN | `telegram_alert.py` | No retry on send failure — silent drops on transient network issues |
| LOW | OPEN | `telegram_alert.py` | `_sent_signals` dedup resets on server restart — potential duplicate alerts |

---

## Notes

- Railway API was **unreachable** from this execution environment (proxy host-not-allowed). Same constraint as 2026-04-03.
- **No issues from the 2026-04-03 report have been resolved** as of this run.
- The two HIGH priority bugs (`validate_symbol()` for `BRK.B` and thread-unsafe FMP rate limiter) remain the most impactful unresolved items.
- All findings are based on static analysis of local files only.
- Re-run with direct network access to collect live screening and signal data.

---

*Report generated: 2026-04-20 UTC*
