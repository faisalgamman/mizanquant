# DeepSeek Task — Phase 2: Green the Test Suite (Diagnosed Handoff)

> **You (DeepSeek) have no memory of prior sessions. This document is self-contained.**
> A reviewer (Claude) has already diagnosed each failure's ROOT CAUSE. Your job is the
> **mechanical application** of the exact fix specified per item. Do NOT re-diagnose, do NOT
> improvise, do NOT touch anything not listed here.

## Repository
`C:\Users\TECH VALLEY\mizanquant` — Python/FastAPI halal trading platform.
Canonical app: `app/workspace_server.py`. Run tests: `python -m pytest <file> -q -p no:cacheprovider`.

## Baseline (measured)
Full suite = 668 tests. **29 pre-existing failures** (this Phase 2 closes them). Target after your work
on Batch 1: the listed tests pass, **zero new failures**, **zero assertions weakened**.

---

## ⛔ HARD RULES (violating any = task rejected in review)
1. **NEVER weaken or delete a test assertion** to make it pass. No changing `assert x == 3` to `== 660`.
2. **NEVER add `@pytest.mark.xfail` / `skip`** to dodge a failure.
3. **Fix the ROOT CAUSE specified** — either (a) a real production bug in app code, or (b) a wrong
   mock/patch target in the test, or (c) a stale expectation from a verified rename.
4. **Touch ONLY the files/lines named per item.** Do not reformat, refactor, or "improve" adjacent code.
5. After each item: run its test and confirm green. After all items: run the full Batch-1 set together.
6. If an item does not behave as described here, **STOP and report back** — do not guess.

---

## BATCH 1 — Fully diagnosed, execute these exactly

### Item 1 — PROD BUG: `claude_tools.py` uses `TOOL_SCHEMAS` before it is defined
**File:** `app/services/claude_tools.py`
**Symptom:** line ~30 `DEEPSEEK_TOOL_SCHEMAS = _to_openai_tools(TOOL_SCHEMAS)` runs at import, but
`TOOL_SCHEMAS` (Anthropic format) is defined LATER in the file → `NameError: name 'TOOL_SCHEMAS' is not defined`.
**Fix:** MOVE the single line `DEEPSEEK_TOOL_SCHEMAS = _to_openai_tools(TOOL_SCHEMAS)` to AFTER the
`TOOL_SCHEMAS = [...]` definition block (place it immediately after that list closes). Change nothing else.
**Verify:** `python -c "import app.services.claude_tools"` → no error. Fixes the import path used by
`tests/test_admin_endpoints.py::test_agent_health_no_longer_exposes_key_prefix`.

### Item 2 — PROD BUG: naive `datetime.now()` in `ml_pipeline.py`
**File:** `app/services/ml_pipeline.py`, lines 723 and 947.
**Symptom:** `tests/test_no_naive_datetimes.py` scans service code for `datetime.now()` without tz and flags these.
**Fix:** ensure `timezone` is imported (`from datetime import datetime, timezone`), then:
- line 723: `datetime.now()` → `datetime.now(timezone.utc)`
- line 947: `datetime.now()` → `datetime.now(timezone.utc)`
**Verify:** `python -m pytest tests/test_no_naive_datetimes.py -q` → green.

### Item 3 — STALE TEST (verified rename `trading_app` → `mizanquant`)
**File:** `tests/test_scheduler_hooks.py`, lines ~25 and ~43.
**Root cause:** test asserts the script/db name equals `'trading_app'`, but the project was renamed to
`'mizanquant'`. The production value `'mizanquant'` is CORRECT; the test expectation is stale.
**Fix:** update the two assertions' expected string from `'trading_app'` to `'mizanquant'`. (This is the ONE
permitted expectation change — because production is correct and the test is provably outdated.)
**Verify:** `python -m pytest tests/test_scheduler_hooks.py -q` → green.

### Item 4 — MOCK TARGET MISMATCH: dashboard universe tests (2 of the 7-test bucket)
**File:** `tests/test_dashboard_endpoints.py` — `test_symbols_universe` (~line 27), `test_symbols_search` (~line 38).
**Root cause:** the tests do `monkeypatch.setattr(hs, "_universe_symbols", ...)`, but the endpoint
`/api/symbols/universe` is served by `app/api/v1/system.py::v1_symbols_universe`, which loads the universe via
the helper **`_fetch_universe`** in `app/api/v1/system.py` — NOT `hs._universe_symbols`. So the patch misses and
the real 660-symbol universe leaks through.
**Fix:** repoint the patch to the real target. Replace
`monkeypatch.setattr(hs, "_universe_symbols", lambda: [...])`
with `monkeypatch.setattr("app.api.v1.system._fetch_universe", lambda: ["AAPL","MSFT","GOOGL"])`
(use the SAME symbol list already in each test; keep the `assert ... == 3` / search assertions unchanged).
**Verify:** both tests green; assertions untouched.

### Item 5 — GUARD ORDER: `test_rejects_invalid_stop` never reaches validation
**File:** `tests/test_trading_engine_entry.py`, `test_rejects_invalid_stop` (~line 50).
**Root cause:** the test expects a `'VALIDATION'` rejection, but the **market-hours guard fires first**
("Market is closed…") because `is_market_open` is not mocked, so execution never reaches stop validation.
**Fix:** inside the test, mock market hours open before calling the entry function, matching the pattern used
elsewhere in the suite: `with patch.object(<module>, "is_market_open", return_value=True):` (use the same
import path the other passing tests in this file use). Keep the `assert 'VALIDATION' in reason` assertion.
**Verify:** test green.

### Item 6 — FIXTURE: shadow_scorer model promotion blocked by real quality gate
**File:** `tests/test_shadow_scorer.py` — `test_evaluate_no_production_promotes_staging`, `test_evaluate_promotes_after_outperformance`.
**Root cause:** promotion calls `app/services/model_registry.py` quality gate, which rejects the mock model
(`Sharpe 0.00 < 1.0; Acc 0.000 < 0.55`) because the test's fake model has no real metrics.
**Fix:** in these tests, make the staging model carry passing metrics (Sharpe ≥ 1.0, Acc ≥ 0.55) OR mock the
quality-gate function so promotion logic under test is exercised without the metric gate. Do NOT weaken the
test's promotion assertions. (Confirm the exact gate function name in `model_registry.py:119` and patch it.)
**Verify:** both tests green.

---

## OUT OF SCOPE — RESERVED FOR CLAUDE (do NOT touch — may be real behavior bugs)
These need judgment (real-bug vs test); Claude diagnoses them in a second handoff. **Leave them failing.**
- `tests/test_calibrate_thresholds.py` (2) — `assert 50 == 45` / `assert False is True` (threshold logic; possibly
  the deliberate `min_confidence 30→45` change — needs verification, not a blind test edit).
- `tests/test_dashboard_endpoints.py` — `test_trading_summary_no_account`, `test_trading_summary_with_account`,
  `test_guards_recent_empty`, `test_halal_check`, `test_halal_universe` (each a distinct mock target — Claude maps them).
- `tests/test_admin_endpoints.py` — `test_trading_history_requires_key…`, `test_trading_status_returns_broker_diagnostics`
  (401 from a pydantic `OperatorAPIKey … call .rebuild()` forward-ref issue — possible real auth bug).
- `tests/test_trading_performance.py::test_trading_performance_serializes_no_loss_case` (same 401/auth root).
- `tests/test_config.py::test_empty_whitelist_flagged`, `tests/test_ibkr_config.py::test_invalid_port_4004_rejected`
  (`assert 4004 == 4002`), `tests/test_trade_plan.py::test_basic_sizing` (`assert 50 == 200`),
  `tests/chaos/test_kill_mid_order.py` (`assert 7 == 10`), `tests/test_market_data.py::test_pagination_resumes_on_429`,
  `tests/test_trading_engine_entry.py::test_client_order_id_attached_on_retry` — each may be a real prod change.

---

## ACCEPTANCE GATE (what Claude will verify before accepting your Batch 1)
1. `python -m pytest tests/test_no_naive_datetimes.py tests/test_scheduler_hooks.py tests/test_shadow_scorer.py tests/test_dashboard_endpoints.py::test_symbols_universe tests/test_dashboard_endpoints.py::test_symbols_search tests/test_trading_engine_entry.py::test_rejects_invalid_stop tests/test_admin_endpoints.py::test_agent_health_no_longer_exposes_key_prefix -q` → **all green**.
2. `git diff` on the four `tests/` files shows ONLY repointed mock targets / the one verified rename — **no assertion values changed** (Claude greps the diff for weakened asserts).
3. `python -c "import app.services.claude_tools, app.services.ml_pipeline"` → no error.
4. Full suite shows **29 → ~21 failures** (8 closed by Batch 1), **zero new failures** (Claude runs baseline diff).
5. Report back the exact command outputs. Do NOT commit — Claude commits after review.
