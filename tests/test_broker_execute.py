"""Tests for IBKR paper broker execution — halal gate, offline, success paths."""
from __future__ import annotations

import asyncio

from app.api.v1.paper import BrokerExecuteRequest


# ---------------------------------------------------------------------------
# Fake manager whose submit_bracket returns canned dicts
# ---------------------------------------------------------------------------

class FakeMgr:
    def __init__(self, result):
        self.result = result

    def submit_bracket(self, **kwargs):
        return self.result


MGR_OFFLINE = FakeMgr({"success": False, "reason": "Broker returned empty response", "order_id": ""})
MGR_REJECTED = FakeMgr({"success": False, "reason": "Order rejected: insufficient margin", "order_id": ""})
MGR_OK = FakeMgr({"success": True, "order_id": "TEST-123", "status": "submitted"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call(body, *, db=None, monkeypatch=None, verify_halal_fn=None, mgr=None):
    """Shortcut to call the async endpoint function directly."""
    if monkeypatch is not None:
        if verify_halal_fn is not None:
            monkeypatch.setattr("app.services.halal_screening.verify_halal", verify_halal_fn)
        if mgr is not None:
            monkeypatch.setattr(
                "app.services.order_manager.get_order_manager",
                lambda strategy_id=None: mgr,
            )
    from app.api.v1.paper import v1_broker_execute
    return asyncio.run(v1_broker_execute(body, db=db))


def _req(**kw):
    defaults = {"symbol": "AAPL", "side": "buy", "entry_price": 100.0,
                "stop_loss": 85.0, "take_profit": 130.0, "shares": 10}
    return BrokerExecuteRequest(**{**defaults, **kw})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_halal_block_rejects(monkeypatch):
    """Halal fail -> success=False, reason='halal_blocked', manager NOT called."""
    called = []
    mgr = FakeMgr({"success": True, "order_id": "SHOULD-NOT-APPEAR"})
    orig = mgr.submit_bracket

    def spy(**kw):
        called.append(1)
        return orig(**kw)

    mgr.submit_bracket = spy

    out = _call(_req(), db=None, monkeypatch=monkeypatch,
                verify_halal_fn=lambda s: (False, "Excluded sector"), mgr=mgr)
    assert out["success"] is False
    assert out["reason"] == "halal_blocked"
    assert "Excluded sector" in out["detail"]
    assert len(called) == 0  # manager never touched


def test_broker_offline_no_db_row(monkeypatch):
    """Broker returns empty response -> broker_offline, no TradeHistory write."""
    out = _call(_req(), db=None, monkeypatch=monkeypatch,
                verify_halal_fn=lambda s: (True, "ok"), mgr=MGR_OFFLINE)
    assert out["success"] is False
    assert out["reason"] == "broker_offline"


def test_broker_rejected_no_db_row(monkeypatch):
    """Broker rejects order -> success=False with reason, no DB row."""
    out = _call(_req(), db=None, monkeypatch=monkeypatch,
                verify_halal_fn=lambda s: (True, "ok"), mgr=MGR_REJECTED)
    assert out["success"] is False
    assert "insufficient margin" in out.get("detail", "")
    # reason should be the rejection reason (not broker_offline)
    assert "offline" not in out.get("reason", "")


def test_success_returns_broker_order_id(monkeypatch):
    """Happy path: broker accepts -> success=True, broker_order_id returned."""
    out = _call(_req(), db=None, monkeypatch=monkeypatch,
                verify_halal_fn=lambda s: (True, "ok"), mgr=MGR_OK)
    assert out["success"] is True
    assert out["broker_order_id"] == "TEST-123"
    assert out["broker"] == "ibkr"
    assert out["status"] == "submitted"


def test_success_without_db_skips_row(monkeypatch):
    """When db=None, success still returns True (DB row just skipped)."""
    out = _call(_req(), db=None, monkeypatch=monkeypatch,
                verify_halal_fn=lambda s: (True, "ok"), mgr=MGR_OK)
    assert out["success"] is True
    assert out.get("db_id") is None
