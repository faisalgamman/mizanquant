# DeepSeek — Remaining Phase 2 Fixes (Single Consolidated Handoff)

> **Cold-start: you (DeepSeek) have no memory of prior work. This file is self-contained and
> supersedes any earlier DEEPSEEK_TASK_PHASE2*.md.** A reviewer (Claude) diagnosed every item.
> Apply ONLY what is in **Section A**. Everything in **Section B is reserved for Claude — do not touch it.**
>
> Repo: `C:\Users\TECH VALLEY\mizanquant`. Test: `python -m pytest <nodeid> -q -p no:cacheprovider`.
> Suite status when this was written: **15 failing** (down from 29). Section A closes 5 of them.

## ⛔ HARD RULES (any violation = rejected in review)
1. **NEVER weaken/delete an assertion.** Keep every `assert ...` exactly as written.
2. **NEVER add `xfail` / `skip`.**
3. Fix **only the mock target** — the `monkeypatch.setattr(...)` path. These endpoints work in
   production; the tests patch the WRONG symbol (a stale, pre-refactor API name).
4. Touch ONLY `tests/test_dashboard_endpoints.py` and `tests/test_admin_endpoints.py`.
5. Run each test after editing; paste the output. **Do NOT commit** — Claude reviews + commits.
6. If any item does not behave as described, **STOP and report** — do not improvise.

## Root cause (common to all Section-A items)
The handlers were refactored to fetch data through the **broker factory**
(`app.services.broker.factory.get_broker(...).get_account()`) and `app.services.*` modules.
The tests still patch the old `halal_screener` (`hs.*`) names, so the patch misses, the real
services run, and the assertions fail. Repoint each patch to the module the handler imports from.
(`get_broker` / `verify_halal` / `get_universe_symbols` are imported *inside* the handler functions,
so patch them at their **source module** path — that is what an in-function import resolves to.)

---

# SECTION A — EXECUTE THESE (5 tests)

## A1 — `tests/test_dashboard_endpoints.py::test_halal_check`
Handler `app/api/v1/trading.py::v1_halal_check` does `from app.services.halal_screening import verify_halal`.
**Change:** replace
`monkeypatch.setattr(hs, "verify_halal", lambda s: (True, "Verified halal"))`
→ `monkeypatch.setattr("app.services.halal_screening.verify_halal", lambda s: (True, "Verified halal"))`
Leave the `validate_symbol` patch and every assertion unchanged.

## A2 — `tests/test_dashboard_endpoints.py::test_halal_universe`
Handler `app/api/v1/trading.py::v1_halal_universe` uses `from app.services.universe import get_universe_symbols`.
**Change:** replace
`monkeypatch.setattr(hs, "_universe_symbols", lambda: ["AAPL", "MSFT"])`
→ `monkeypatch.setattr("app.services.universe.get_universe_symbols", lambda: ["AAPL", "MSFT"])`
Keep the count assertion unchanged.

## A3 + A4 — `test_trading_summary_no_account` and `test_trading_summary_with_account` (same file)
Handler `app/api/v1/trading.py::v1_trading_summary` does (inside the function)
`from app.services.broker.factory import get_broker; broker = get_broker(strategy_id=sid)`
then `broker.get_account(strategy_id=sid)` / `broker.get_positions(strategy_id=sid)`.
Add this helper ONCE near the top of the test module:
```python
class _FakeBroker:
    name = "fake"
    def __init__(self, account=None, positions=None):
        self._a = account; self._p = positions or []
    def get_account(self, strategy_id=None): return self._a
    def get_positions(self, strategy_id=None): return self._p
```
Then in each test REMOVE the `hs.alpaca_get_account` / `hs.alpaca_get_positions` patches and add:
- no_account: `monkeypatch.setattr("app.services.broker.factory.get_broker", lambda strategy_id=None: _FakeBroker(account=None, positions=[]))`
- with_account: `monkeypatch.setattr("app.services.broker.factory.get_broker", lambda strategy_id=None: _FakeBroker(account=mock_account, positions=mock_positions))`
Keep `mock_account` / `mock_positions` and ALL assertions unchanged.

## A5 — `tests/test_admin_endpoints.py::test_trading_status_returns_broker_diagnostics`
Handler `app/routers/portfolio.py::trading_status` uses `from app.services.broker.factory import get_broker`
then `broker.get_account(strategy_id=sid)`; returns `broker_connected=False` when the account is None.
Add the same `_FakeBroker` helper (or reuse it) and replace the `hs.alpaca_get_account` /
`hs.alpaca_get_last_error` patches with:
`monkeypatch.setattr("app.services.broker.factory.get_broker", lambda strategy_id=None: _FakeBroker(account=None))`
Keep `assert body["broker_connected"] is False`. If `broker_reason` / `broker_status_code` assertions
still fail AFTER this change, **STOP and report** — do not edit them.

## Section-A acceptance gate (Claude verifies)
- `python -m pytest tests/test_dashboard_endpoints.py::test_halal_check tests/test_dashboard_endpoints.py::test_halal_universe tests/test_dashboard_endpoints.py::test_trading_summary_no_account tests/test_dashboard_endpoints.py::test_trading_summary_with_account tests/test_admin_endpoints.py::test_trading_status_returns_broker_diagnostics -q` → green (or report any still-red; never force-pass).
- `git diff` on the two test files shows ONLY repointed `monkeypatch.setattr` targets + the `_FakeBroker` helper. **Zero assertion changes.**

---

# SECTION B — RESERVED FOR CLAUDE (do NOT touch)
Each of these needs a judgment call — *is the test stale, or did production regress?* — that must not be
guessed. Several are likely real production bugs or **deliberate safety changes**; blindly "greening" them
would hide a real problem. Claude handles all of them.

| Test | Why reserved (Claude's preliminary finding) |
|------|---------------------------------------------|
| `test_config::test_empty_whitelist_flagged` | ⚠️ **SAFETY** — the live-trading whitelist gate was *disabled* in prod (config.py SF-3, ~line 321). The failing test may be correctly flagging removal of a safety check. Needs a human decision, not a test edit. |
| `test_ibkr_config::test_invalid_port_4004_rejected` | Stale: port 4004 is now a *valid* socat-relay port (ibkr_config VALID_PORTS). Assertion must be inverted — verify before changing. |
| `test_trade_plan::test_basic_sizing` (`50 vs 200`) | Position sizing returns 50 not 200 shares — likely a max-position-% cap now applies. Real-behavior question. |
| `test_calibrate_thresholds` (×2) | Threshold-acceptance logic (`50 vs 45`); possibly the deliberate min_confidence 30→45 change. |
| `test_market_data::test_pagination_resumes_on_429` | Pagination/429 retry returns `[]`; market_data was refactored (feed=iex removal). Real-logic vs stale mock. |
| `test_trading_engine_entry::test_client_order_id_attached_on_retry` | Duplicate-coid retry returns None; needs tracing the engine retry path. |
| `test_dashboard_endpoints::test_guards_recent_empty` | Async-session mock — handler uses an async SQLAlchemy session, not the patched sync `SessionLocal`. |
| `tests/chaos/test_kill_mid_order` (`7 vs 10`) | Idempotency/retry count — chaos invariant; needs careful analysis. |
| `test_risk_manager::TestEligibility::test_happy_path` | Order-dependent flaky (passes in isolation) — Claude is adding a state-isolation fixture. |

---

## After Section A
Hand back your test output. Claude runs a full-suite baseline diff (expect **15 → 10**, zero new failures),
verifies no assertions were weakened, then commits. Claude continues Section B in parallel.
