# DeepSeek — Parallel Dev Handoff (runs alongside Claude)

> **Cold-start: you (DeepSeek) have no memory of prior work. Self-contained.**
> Claude diagnosed each item. Apply EXACTLY what is specified. Run the test after each.
> **Do NOT commit** — Claude reviews (baseline-diff, no weakened assertions) and commits.
> Repo: `C:\Users\TECH VALLEY\mizanquant`. Test: `python -m pytest <nodeid> -q -p no:cacheprovider`.

## ⛔ HARD RULES
1. NEVER weaken/delete an assertion. 2. NEVER xfail/skip. 3. Fix only the mock/interface named.
4. Touch ONLY the files listed per item. 5. If an item misbehaves vs this spec, STOP and report.
6. Your files do not overlap Claude's — work freely in parallel.

---

## ITEM 1 — `test_client_order_id_attached_on_retry` (mock interface drift)
**File:** `tests/test_trading_engine_entry.py` (only this test's `DummyClient`).
**Root cause:** `trading_engine._submit_order` now calls `client.request(method, url, headers=, json=, params=)`
(trading_engine.py:79), but the test's `DummyClient` only implements `.post()` / `.get()` →
`AttributeError: 'DummyClient' object has no attribute 'request'`.
**Fix:** add a `request` method to `DummyClient` that dispatches on `method` (keep `post`/`get` if you like, but
`request` is what's called). The 422-duplicate path must still return `fake_resp_duplicate`, and the follow-up
GET must still return the `existing_order` response:
```python
    def request(self, method, url, headers=None, json=None, params=None):
        if str(method).upper() == "POST":
            return fake_resp_duplicate
        return types.SimpleNamespace(status_code=200, json=lambda: existing_order)
```
Keep every `assert` unchanged (the test verifies a duplicate-coid 422 makes us FETCH the existing order, not
re-submit — a real-money idempotency guarantee).
**Verify:** `python -m pytest tests/test_trading_engine_entry.py::test_client_order_id_attached_on_retry -q` → green.

---

## ITEM 2 — `test_dashboard_endpoints.py::test_guards_recent_empty` (wrong session mock)
**File:** `tests/test_dashboard_endpoints.py` (only this test).
**Root cause:** handler `app/api/v1/guards.py::v1_guards_recent` reads via an ASYNC session injected by
`Depends(get_async_db)`. The test patches the SYNC `app.db.database.SessionLocal` → patch misses → the real
GuardLog rows leak → `assert [...] == []` fails.
**Fix:** use a FastAPI dependency override instead of the SessionLocal monkeypatch. Replace the
`monkeypatch.setattr("app.db.database.SessionLocal", ...)` line (and the local `MockSession` if now unused) with:
```python
    from app.db.async_database import get_async_db

    class _FakeResult:
        def scalars(self): return self
        def all(self): return []
    class _FakeAsyncDB:
        async def execute(self, stmt): return _FakeResult()
    async def _fake_get_async_db():
        yield _FakeAsyncDB()

    hs.app.dependency_overrides[get_async_db] = _fake_get_async_db
    try:
        resp = client.get("/api/guards/recent?limit=5")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        hs.app.dependency_overrides.pop(get_async_db, None)
```
Keep the `== []` assertion. (`hs` is the already-imported `halal_screener` in this test module.)
**Verify:** `python -m pytest tests/test_dashboard_endpoints.py::test_guards_recent_empty -q` → green.

---

## ITEM 3 — `.gitignore` (stop tracking runtime artifacts)
**File:** `.gitignore` (append; do not remove existing lines).
Append these patterns:
```
# Runtime / cache artifacts (not source)
app/data/yfinance_cache/
models/_performance/
scripts/_*.json
.graph_context
```
Do NOT `git rm` anything and do NOT touch `run_with_telegram.ps1` (contains a secret — Claude/user handle it).
**Verify:** `git status --short` no longer lists those untracked paths.

---

## ITEM 4 — D3 display (`Confirmations: N/4`) — **WAIT for Claude's signal**
Do NOT start until Claude tells you the `confirmation_count` / `confirmations` field is attached to the signal
row. When signalled, Claude will hand you the exact field names + the precise display lines for Telegram
(`signals_advisor._format_signal`) and deep-picks. Informational only — **no gating logic**.

---

## Acceptance gate (Claude verifies each before commit)
- Items 1-2: named test green; `git diff` on the test file shows only the mock/interface change — **no assertion edits**.
- Item 3: `.gitignore` append only; artifacts disappear from `git status`.
- Full suite: each item drops one failure with **zero new failures** (Claude runs baseline-diff).
- Report exact command outputs back to Claude. Do NOT commit.
