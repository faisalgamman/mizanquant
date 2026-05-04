# Daily Trading Report — 2026-05-04

**Generated:** 2026-05-04 UTC  
**System Version:** (from local codebase)  
**Environment:** Railway (Production)

---

## System Health

**Status: UNAVAILABLE — API Unreachable (403 Forbidden)**

All Railway endpoints at `https://openbb-trading-production.up.railway.app` returned HTTP 403 Forbidden. This is consistent with the 2026-04-28 report. The application enforces `X-API-Key` authentication on all routes; the agent's WebFetch tool cannot attach custom headers, so all live API calls are blocked.

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

Static analysis of local files. Compared against the 2026-04-28 report.

**No new fixes detected since 2026-04-28. All previously OPEN issues remain OPEN.**

---

### 3.1 `halal_screener.py`

**`analyze()` Hardcodes `"halal": "Yes"` (LOW — unresolved)**  
- Line 451: `"halal": "Yes"` is hardcoded in every response from `analyze()`, even though `_analyze_one()` (line 456–463) gates on `verify_halal()` first. The value is functionally correct but misleads API consumers who may assume no live check occurred.  
- **Fix:** Replace with `"halal": reason` using the string returned by `verify_halal()`, e.g. `"Verified (AAOIFI)"`.

**Stale Comment — "8GB RAM tier" (LOW — unresolved)**  
- Line 1572: `# === ML MODELS (re-enabled with 8GB RAM tier) ===`  
- Railway free tier is 512 MB RAM. The comment is incorrect and could cause a developer to accidentally re-enable ML models, triggering OOM kills.  
- **Fix:** Change to `# ML models disabled — Railway free tier (512 MB RAM limit)`.

**`PSKY` Unknown Ticker (LOW — unresolved)**  
- Line 225: `PSKY` in `_SP500_ALL`. Not a recognised S&P 500 component; causes a silent `DataFetchError` on every screening run.  
- **Fix:** Remove from `_SP500_ALL`.

**`XYZ` Unverified Ticker (LOW — NEW)**  
- Line 234: `XYZ` appears in `_SP500_ALL`. As of the knowledge cutoff this is not a standard S&P 500 component. If it represents a recently listed company, add a comment confirming the company name and index inclusion date. If it is a placeholder or test symbol, remove it.  
- **Fix:** Verify against the current S&P 500 constituent list and either annotate or remove.

---

### 3.2 `app/services/market_data.py`

**Rate Limiting: Correct**  
- Semaphore (max 3 concurrent) + `_alpaca_lock` minimum-interval enforcement (0.25 s) is solid.  
- Exponential backoff on 429 via `_backoff_next()` (capped at 30 s) is appropriate for the daily-bar path.  
- 5-minute in-memory TTL cache (60 s during market hours) prevents redundant fetches.

**`fetch_alpaca_intraday()` — No Max Retry Bound on 429 (LOW — unresolved)**  
- Lines 318–326: after a 429 response, `page_retry` is incremented and the loop `continue`s with no upper-bound check. Under sustained throttling the function will backoff-loop indefinitely until the httpx 30 s client timeout fires. The daily-bar path has an explicit `if page_retry >= max_retries: break` guard; the intraday path should match.  
- **Fix:**
  ```python
  page_retry += 1
  if page_retry >= 3:
      logger.error(f"Intraday 429 max retries for {symbol}")
      break  # give up this page
  ```

---

### 3.3 `app/services/halal_screening.py`

**AAOIFI Thresholds — Correct**  
| Screen | Threshold | Status |
|--------|-----------|--------|
| Debt / Market Cap | < 33% | Correct |
| Interest Income / Revenue | < 5% | Correct |
| Haram sector/industry | Excluded list | Correct |
| Liquidity / Market Cap | < 33% | Correct |

**`packaged_foods` / `entertainment` Comment Contradicts Behavior (MEDIUM — unresolved)**  
- Lines 72–80: `HARAM_INDUSTRIES` contains `"packaged foods"` and `"entertainment"` with inline comments reading "flagged for manual review, not auto-disqualified."  
- In practice, `haram_industry_flag = True` → `haram_pass = False` → `is_halal = False`. These industries **are** auto-disqualified.  
- **Fix (option A):** Move them to a separate `_REVIEW_INDUSTRIES` set and handle with a `needs_review` flag instead of failing the screen.  
- **Fix (option B):** Correct the comment to read `# auto-disqualified — pending manual review`.

**`batch_screen()` FMP Daily Limit Margin (LOW — unresolved)**  
- `max_per_run=80` × 3 FMP calls = 240 API calls per run, against a 250/day free-tier limit. A single transient retry burst exhausts the buffer.  
- **Recommendation:** Lower to `max_per_run=75` (225 calls, 25-call margin) or add a call counter with early abort when remaining quota < 10.

---

### 3.4 `app/services/signal_tracker.py`

**Accuracy Calculations: Correct**  
- Win rate, profit factor, and avg win/loss are mathematically sound.  
- SELL signal direction normalisation (line 61–62: `ret_pct = -price_chg if is_sell else price_chg`) is correctly applied.  
- Division-by-zero guard on profit factor is in place.

**`lookback_days=5` Default Hardcoded (LOW — unresolved)**  
- `check_signal_outcomes()` defaults to `lookback_days=5` in the function signature. This is not driven by `settings`, making it hard to adjust for different trade horizons (e.g., swing trades held 10+ days) without a code change.  
- **Fix:** Add `SIGNAL_LOOKBACK_DAYS: int = 5` to `settings`/`app_cfg` and use `settings.SIGNAL_LOOKBACK_DAYS` as the default.

---

### 3.5 `app/services/telegram_alert.py`

**Error Handling: Good**  
- All public alert functions catch exceptions and log without re-raising — Telegram failures never propagate to the main flow.  
- Chart-to-text fallback in `alert_signal_with_chart()` (lines 285–289) is solid.  
- `TELEGRAM_BUY_ONLY` filter is correctly applied to both text and photo paths.

**No Confirmed Retry on Transient Send Failures (LOW — unresolved)**  
- `send_message()` delegates to `queued_send_message` (notify.py). If that layer does not retry, a transient Telegram API hiccup silently drops the alert.  
- **Recommendation:** Verify `notify.py`'s queue implements at least 1 retry with 2 s backoff on HTTP 5xx / network error.

**`_sent_signals` Dedup Resets on Restart (LOW — unresolved)**  
- In-memory dict is lost on any container restart (deploy, OOM kill) → potential duplicate alerts within the 30-minute window post-restart.  
- **Recommendation:** Accept as a known limitation and document it in a comment, or persist the dedup set to the database.

**`alert_consensus()` Silent HOLD Return (LOW — unresolved)**  
- Lines 157–158: returns `False` for HOLD/NEUTRAL with no log entry, making it invisible in traces when debugging why an expected alert was not sent.  
- **Fix:** Add `logger.debug(f"Skipping HOLD/NEUTRAL consensus for {symbol}")` before the return.

---

## Summary of Code Issues

| Priority | Status | File | Issue |
|----------|--------|------|-------|
| HIGH | **FIXED** (prior) | `halal_screener.py` | `validate_symbol()` rejected `BRK.B` |
| HIGH | **FIXED** (prior) | `halal_screening.py` | `_fmp_rate_limit()` not thread-safe |
| MEDIUM | **FIXED** (prior) | `signal_tracker.py` | SELL signal outcomes computed in wrong direction |
| MEDIUM | OPEN | `halal_screening.py` | `packaged_foods`/`entertainment` comment contradicts auto-disqualify behavior |
| LOW | OPEN | `halal_screener.py` | Line 1572 comment references "8GB RAM tier" — Railway free tier is 512 MB |
| LOW | OPEN | `halal_screener.py` | `analyze()` hardcodes `"halal": "Yes"` — misleading for API consumers |
| LOW | OPEN | `halal_screener.py` | `PSKY` is not a valid S&P 500 ticker — silent fetch failures each run |
| LOW | **NEW** | `halal_screener.py` | `XYZ` in `_SP500_ALL` — unverified S&P 500 component, possibly placeholder |
| LOW | OPEN | `market_data.py` | `fetch_alpaca_intraday()` has no max retry count on 429 (infinite loop risk) |
| LOW | OPEN | `signal_tracker.py` | `lookback_days=5` default not driven by `settings` |
| LOW | OPEN | `telegram_alert.py` | No confirmed retry on send failure — silent drops on transient network errors |
| LOW | OPEN | `telegram_alert.py` | `_sent_signals` dedup resets on server restart |
| LOW | OPEN | `telegram_alert.py` | `alert_consensus()` returns `False` for HOLD with no log entry |

**13 total tracked issues: 3 FIXED, 1 NEW (LOW), 9 OPEN (1 MEDIUM, 8 LOW)**

---

## Progress Since 2026-04-28

| Bug | Prior Status | Current Status |
|-----|-------------|----------------|
| `packaged_foods`/`entertainment` comment | OPEN (MEDIUM) | OPEN |
| Line 1572 "8GB RAM tier" comment | OPEN (LOW) | OPEN |
| `analyze()` hardcodes `"halal": "Yes"` | OPEN (LOW) | OPEN |
| `PSKY` invalid ticker | OPEN (LOW) | OPEN |
| `XYZ` unverified ticker | Not tracked | **NEW (LOW)** |
| `fetch_alpaca_intraday()` no max retry | OPEN (LOW) | OPEN |
| `lookback_days=5` not in settings | OPEN (LOW) | OPEN |
| Telegram no retry on send failure | OPEN (LOW) | OPEN |
| `_sent_signals` dedup resets on restart | OPEN (LOW) | OPEN |
| `alert_consensus()` silent HOLD return | OPEN (LOW) | OPEN |

0 fixes since 2026-04-28. 1 new LOW issue identified (`XYZ` ticker).

---

## Notes

- Railway API returned HTTP 403 Forbidden on all endpoints for the third consecutive report (2026-04-20, 2026-04-27, 2026-04-28, 2026-05-04). The daily agent cannot pass `X-API-Key` via WebFetch. To resolve, consider one of:
  1. Add a public read-only health/status endpoint that does not require authentication.
  2. Run the daily agent from within the Railway environment (where it can use localhost or internal networking).
  3. Pass the API key through an environment variable and invoke via a curl-based approach with the Bash tool (requires adding the Railway host to the tool allowlist).
- The MEDIUM open issue (`packaged_foods`/`entertainment` auto-disqualification) is the highest-priority remaining code fix and has been open since at least 2026-04-20. Recommend addressing in the next PR.

---

*Report generated: 2026-05-04 UTC*
