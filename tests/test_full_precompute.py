"""Tests for run_full_precompute() — the on-demand / off-session full rescan.

Covers the single-flight guard and the idle→running→done state walk, with the heavy
EDGAR/scan calls monkeypatched so the test stays fast and offline.
"""

import sys
import types

from app.services import scheduler as S


def _stub_heavy(monkeypatch):
    """Patch the heavy calls run_full_precompute makes so it returns instantly/offline."""
    import app.services.universe as U
    import app.services.halal_screening as H
    monkeypatch.setattr(U, "build_halal_candidates", lambda: ["AAA", "BBB"], raising=False)
    monkeypatch.setattr(H, "warm_fundamentals_cache",
                        lambda cands, **kw: {"refreshed": len(cands)}, raising=False)
    monkeypatch.setattr(U, "sync_verified_halal_to_universe", lambda: 2, raising=False)
    # The composite step imports app.workspace_server + halal_screener lazily — inject light
    # fakes so the unit test never pulls in the full app.
    fake_ws = types.ModuleType("app.workspace_server")
    fake_ws._SMART_UNIVERSE = ["AAA", "BBB"]
    fake_ws._refresh_smart_universe = lambda: None
    fake_ws._run_screener_bg = lambda syms: None
    monkeypatch.setitem(sys.modules, "app.workspace_server", fake_ws)
    fake_hs = types.ModuleType("halal_screener")
    fake_hs._db_symbols = object()
    monkeypatch.setitem(sys.modules, "halal_screener", fake_hs)


def test_full_precompute_walks_to_done(monkeypatch):
    _stub_heavy(monkeypatch)
    S._FULL_PRECOMPUTE_STATE.update(status="idle", phase=None, error=None)
    out = S.run_full_precompute(triggered_by="user")
    assert out["status"] == "done"
    assert out["active_halal"] == 2
    st = S.get_full_precompute_state()
    assert st["status"] == "done" and st["phase"] == "done"
    assert st["triggered_by"] == "user"
    assert st["result"] == {"refreshed": 2}
    assert st["error"] is None
    assert st["finished_at"] is not None


def test_technical_only_skips_fundamentals(monkeypatch):
    # In technical_only mode the heavy weekly steps must NOT run — wire them to blow up
    # so the test fails loudly if they're called; the composite step still runs.
    import app.services.universe as U
    import app.services.halal_screening as H

    def _boom(*a, **k):
        raise AssertionError("heavy weekly step ran in technical_only mode")

    monkeypatch.setattr(U, "build_halal_candidates", _boom, raising=False)
    monkeypatch.setattr(H, "warm_fundamentals_cache", _boom, raising=False)
    monkeypatch.setattr(U, "sync_verified_halal_to_universe", _boom, raising=False)
    import sys, types
    fake_ws = types.ModuleType("app.workspace_server")
    fake_ws._SMART_UNIVERSE = ["AAA"]
    fake_ws._refresh_smart_universe = lambda: None
    fake_ws._run_screener_bg = lambda syms: None
    monkeypatch.setitem(sys.modules, "app.workspace_server", fake_ws)
    fake_hs = types.ModuleType("halal_screener")
    fake_hs._db_symbols = object()
    monkeypatch.setitem(sys.modules, "halal_screener", fake_hs)

    S._FULL_PRECOMPUTE_STATE.update(status="idle", phase=None, error=None)
    out = S.run_full_precompute(triggered_by="user", technical_only=True)
    assert out["status"] == "done"
    assert out["mode"] == "technical"
    assert S.get_full_precompute_state()["mode"] == "technical"


def test_full_precompute_single_flight(monkeypatch):
    _stub_heavy(monkeypatch)
    # Hold the lock to simulate a run already in progress → a second call must bail out.
    assert S._FULL_PRECOMPUTE_LOCK.acquire(blocking=False)
    try:
        assert S.run_full_precompute(triggered_by="user") == {"status": "already_running"}
    finally:
        S._FULL_PRECOMPUTE_LOCK.release()


def test_full_precompute_error_is_captured_and_lock_released(monkeypatch):
    _stub_heavy(monkeypatch)
    import app.services.universe as U

    def boom():
        raise RuntimeError("edgar down")

    monkeypatch.setattr(U, "build_halal_candidates", boom, raising=False)
    S._FULL_PRECOMPUTE_STATE.update(status="idle", phase=None, error=None)
    out = S.run_full_precompute(triggered_by="user")
    assert out["status"] == "error"
    assert "edgar down" in out["error"]
    st = S.get_full_precompute_state()
    assert st["status"] == "error" and st["phase"] == "error"
    # The lock must be released even on error → a subsequent run can acquire it.
    assert S._FULL_PRECOMPUTE_LOCK.acquire(blocking=False)
    S._FULL_PRECOMPUTE_LOCK.release()
