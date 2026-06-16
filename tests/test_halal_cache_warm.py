"""Cache-first halal status + pre-market fundamentals warm-up.

These guard the fix for the daily pre-market stall: the screener must read the durable
ScreeningResult cache (warmed before market open) instead of fetching live fundamentals
and crawling on a blocked Yahoo. Covers: serve-fresh-from-cache, serve-STALE-on-refresh-
failure (never drop a symbol during an outage), and stalest-first bounded warm-up.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import app.services.halal_screening as hs


class _FakeQuery:
    def __init__(self, first=None, all_=None):
        self._first, self._all = first, all_ or []

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class _FakeSession:
    def __init__(self, first=None, all_=None):
        self._first, self._all = first, all_ or []

    def query(self, *a, **k):
        return _FakeQuery(self._first, self._all)

    def close(self):
        pass


def _row(symbol, age_days, details):
    return SimpleNamespace(
        symbol=symbol, last_screened=hs._utc_now() - timedelta(days=age_days),
        details=details, is_halal=details.get("is_halal", False),
        debt_ratio=0.0, interest_ratio=0.0, liquidity_ratio=0.0, sector="Tech",
    )


def test_fresh_cache_is_served_without_refetch(monkeypatch):
    details = {"symbol": "AAA", "is_halal": True, "screens_passed": 5,
               "screen_version": hs.HALAL_SCREEN_VERSION}
    monkeypatch.setattr(hs, "SessionLocal", lambda: _FakeSession(first=_row("AAA", 3, details)))
    # If a refresh is attempted the test fails loudly.
    monkeypatch.setattr(hs, "screen_and_store",
                        lambda s: (_ for _ in ()).throw(AssertionError("must not refetch a fresh row")))
    out = hs.get_halal_status("AAA")
    assert out == details
    assert "stale" not in out


def test_stale_plus_refresh_failure_serves_stale_flagged(monkeypatch):
    details = {"symbol": "BBB", "is_halal": True, "screens_passed": 5,
               "screen_version": hs.HALAL_SCREEN_VERSION}
    monkeypatch.setattr(hs, "SessionLocal", lambda: _FakeSession(first=_row("BBB", 20, details)))
    monkeypatch.setattr(hs.settings, "FMP_API_KEY", "x", raising=False)
    monkeypatch.setattr(hs, "screen_and_store", lambda s: None)  # live refresh fails
    out = hs.get_halal_status("BBB")
    assert out["is_halal"] is True          # last-known verdict preserved
    assert out["stale"] is True
    assert out["stale_days"] == 20          # never dropped, just flagged


def test_stale_plus_refresh_success_returns_fresh(monkeypatch):
    details = {"symbol": "CCC", "is_halal": False}
    monkeypatch.setattr(hs, "SessionLocal", lambda: _FakeSession(first=_row("CCC", 20, details)))
    monkeypatch.setattr(hs.settings, "FMP_API_KEY", "x", raising=False)
    monkeypatch.setattr(hs, "screen_and_store", lambda s: {"symbol": "CCC", "is_halal": True})
    out = hs.get_halal_status("CCC")
    assert out["is_halal"] is True
    assert "stale" not in out


def test_warm_refreshes_stalest_first_and_honors_cap(monkeypatch):
    now = hs._utc_now()
    rows = [("AAA", now - timedelta(days=30)),
            ("BBB", now - timedelta(days=2)),    # fresh → skipped
            ("CCC", now - timedelta(days=40))]
    monkeypatch.setattr(hs, "SessionLocal", lambda: _FakeSession(all_=rows))
    called = []
    monkeypatch.setattr(hs, "screen_and_store", lambda s: called.append(s) or {"is_halal": True})

    # DDD is missing entirely (most stale); cap to 2 refreshes.
    out = hs.warm_fundamentals_cache(["AAA", "BBB", "CCC", "DDD"], max_refresh=2)

    assert called == ["DDD", "CCC"]          # missing first, then oldest; BBB (fresh) skipped, AAA capped out
    assert out["refreshed"] == 2
    assert out["skipped_fresh"] == 1         # BBB
    assert out["considered"] == 3            # AAA, CCC, DDD
    assert out["cap"] == 2


def test_naive_last_screened_does_not_crash(monkeypatch):
    # The DB returns last_screened tz-NAIVE; _utc_now() is tz-AWARE. get_halal_status must
    # not raise "can't subtract offset-naive and offset-aware datetimes" (regression: this
    # bug made EVERY cached symbol fail the scan → treated as non-halal).
    details = {"symbol": "NAI", "is_halal": True, "screen_version": hs.HALAL_SCREEN_VERSION}
    row = SimpleNamespace(
        symbol="NAI", last_screened=datetime.utcnow() - timedelta(days=2),  # NAIVE
        details=details, is_halal=True, debt_ratio=0, interest_ratio=0,
        liquidity_ratio=0, sector="Tech",
    )
    monkeypatch.setattr(hs, "SessionLocal", lambda: _FakeSession(first=row))
    monkeypatch.setattr(hs, "screen_and_store",
                        lambda s: (_ for _ in ()).throw(AssertionError("should serve fresh cache, not refetch")))
    out = hs.get_halal_status("NAI")
    assert out == details   # fresh (2d < TTL), served from cache without a tz crash


def test_warm_counts_failures(monkeypatch):
    monkeypatch.setattr(hs, "SessionLocal", lambda: _FakeSession(all_=[]))
    monkeypatch.setattr(hs, "screen_and_store", lambda s: None)  # all fail
    out = hs.warm_fundamentals_cache(["AAA", "BBB"], max_refresh=10)
    assert out["failed"] == 2 and out["refreshed"] == 0
