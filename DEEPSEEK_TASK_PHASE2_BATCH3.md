# DeepSeek Task — Phase 2 / Batch 3: Mock-Target Repoints (Diagnosed Handoff)

> **Cold-start: you have no memory of prior work. This is self-contained.**
> A reviewer (Claude) diagnosed each failure. Apply the EXACT fix per item. Do NOT re-diagnose.
> Repo: `C:\Users\TECH VALLEY\mizanquant`. Test cmd: `python -m pytest <nodeid> -q -p no:cacheprovider`.

## ⛔ HARD RULES (violation = rejected)
1. **NEVER weaken/delete an assertion.** Keep every `assert ...` exactly as is.
2. **NEVER `xfail`/`skip`.**
3. Fix **only the mock target** (the `monkeypatch.setattr` path). The endpoints work in
   production; the tests just patch the WRONG symbol (stale pre-refactor API).
4. Touch ONLY `tests/test_dashboard_endpoints.py` and `tests/test_admin_endpoints.py`.
5. Run each test after editing; report outputs. Do NOT commit — Claude reviews + commits.

## ROOT CAUSE (common to all items)
These handlers were refactored to fetch data via the **broker factory** and `app.services.*`,
but the tests still patch the old `halal_screener` (`hs.*`) names → the patch misses, real
services run, assertions fail. Repoint each patch to the module the handler actually imports from.

---

### Item 1 — `test_dashboard_endpoints.py::test_halal_check`
Handler `app/api/v1/trading.py::v1_halal_check` does `from app.services.halal_screening import verify_halal`.
**Change:** replace `monkeypatch.setattr(hs, "verify_halal", ...)` with
`monkeypatch.setattr("app.services.halal_screening.verify_halal", lambda s: (True, "Verified halal"))`.
Leave the `validate_symbol` patch and all assertions unchanged.

### Item 2 — `test_dashboard_endpoints.py::test_halal_universe`
Handler `app/api/v1/trading.py::v1_halal_universe` uses `from app.services.universe import get_universe_symbols`.
**Change:** replace `monkeypatch.setattr(hs, "_universe_symbols", lambda: ["AAPL", "MSFT"])` with
`monkeypatch.setattr("app.services.universe.get_universe_symbols", lambda: ["AAPL", "MSFT"])`.
Keep the `assert ... == 2` (or whatever the count assertion is) unchanged.

### Item 3 — `test_dashboard_endpoints.py::test_trading_summary_no_account` and `test_trading_summary_with_account`
Handler `app/api/v1/trading.py::v1_trading_summary` does (inside the function):
`from app.services.broker.factory import get_broker; broker = get_broker(strategy_id=sid)`
then `broker.get_account(strategy_id=sid)` and `broker.get_positions(strategy_id=sid)`.
The tests patch `hs.alpaca_get_account` / `hs.alpaca_get_positions` — wrong target.
**Change:** add this fake-broker helper at the top of the test module (once):
```python
class _FakeBroker:
    name = "fake"
    def __init__(self, account=None, positions=None):
        self._a = account; self._p = positions or []
    def get_account(self, strategy_id=None): return self._a
    def get_positions(self, strategy_id=None): return self._p
```
Then in each test, REMOVE the two `hs.alpaca_get_*` patches and instead:
- no_account: `monkeypatch.setattr("app.services.broker.factory.get_broker", lambda strategy_id=None: _FakeBroker(account=None, positions=[]))`
- with_account: `monkeypatch.setattr("app.services.broker.factory.get_broker", lambda strategy_id=None: _FakeBroker(account=mock_account, positions=mock_positions))`
Keep `mock_account` / `mock_positions` and ALL assertions unchanged.

### Item 4 — `test_admin_endpoints.py::test_trading_status_returns_broker_diagnostics`
Handler `app/routers/portfolio.py::trading_status` does `from app.services.broker.factory import get_broker`
then `broker.get_account(strategy_id=sid)`; returns `broker_connected=False` when account is None.
The test patches `hs.alpaca_get_account` — wrong. It expects `broker_connected is False`.
**Change:** add the same `_FakeBroker` helper (or reuse if shared) and replace the
`hs.alpaca_get_account`/`hs.alpaca_get_last_error` patches with:
`monkeypatch.setattr("app.services.broker.factory.get_broker", lambda strategy_id=None: _FakeBroker(account=None))`
Keep the `broker_connected is False` assertion. (Note: `broker_reason`/`broker_status_code` assertions
may need the handler's real path — if they still fail AFTER this change, STOP and report; do not edit them.)

---

## OUT OF SCOPE — RESERVED FOR CLAUDE (do NOT touch)
`test_guards_recent_empty` (async-session mock — complex), `test_sectors_endpoint` (real prod NaN bug),
`test_calibrate_thresholds` (2), `test_config::test_empty_whitelist_flagged`, `test_ibkr_config::test_invalid_port_4004_rejected`,
`test_trade_plan::test_basic_sizing`, `test_market_data::test_pagination_resumes_on_429`,
`tests/chaos/test_kill_mid_order.py`, and the flaky set (`test_risk_manager::test_happy_path`,
`test_scoring` ×3 — these pass in isolation; Claude adds an isolation fixture).

## ACCEPTANCE GATE (Claude verifies)
1. `python -m pytest tests/test_dashboard_endpoints.py::test_halal_check tests/test_dashboard_endpoints.py::test_halal_universe tests/test_dashboard_endpoints.py::test_trading_summary_no_account tests/test_dashboard_endpoints.py::test_trading_summary_with_account tests/test_admin_endpoints.py::test_trading_status_returns_broker_diagnostics -q` → green (or any still-red one reported, not force-passed).
2. `git diff` on the two test files shows ONLY repointed `monkeypatch.setattr` targets + the `_FakeBroker` helper — **zero assertion changes**.
3. Full suite: **19 → ~14 failures**, zero new failures (Claude runs baseline diff).
