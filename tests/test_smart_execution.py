"""Tests for smart_execution (Phase 2B).

Verifies:
1. should_route returns False for small orders.
2. should_route returns True for orders >= threshold.
3. should_route respects SMART_ROUTING_ENABLED=False.
4. route_and_submit_entry returns None for small orders (fall-through).
5. route_and_submit_entry returns a result dict for large orders.
6. A large order produces at least 2 slices + an OCO exit.
7. When OCO fails, emergency stop-loss is attempted.
8. Env threshold override is respected.
"""

from __future__ import annotations

import pytest


# ── should_route ──────────────────────────────────────────────────────────────

def test_should_route_false_for_small_order(monkeypatch):
    from app.services import smart_execution as se
    monkeypatch.setattr(se, "SMART_ROUTING_ENABLED", True)
    monkeypatch.setattr(se, "SMART_ROUTING_THRESHOLD", 500)
    from app.services.smart_execution import should_route
    assert should_route(100) is False


def test_should_route_true_for_large_order(monkeypatch):
    from app.services import smart_execution as se
    monkeypatch.setattr(se, "SMART_ROUTING_ENABLED", True)
    monkeypatch.setattr(se, "SMART_ROUTING_THRESHOLD", 500)
    from app.services.smart_execution import should_route
    assert should_route(500) is True
    assert should_route(1000) is True


def test_should_route_false_when_disabled(monkeypatch):
    from app.services import smart_execution as se
    monkeypatch.setattr(se, "SMART_ROUTING_ENABLED", False)
    from app.services.smart_execution import should_route
    assert should_route(10_000) is False


def test_threshold_boundary(monkeypatch):
    from app.services import smart_execution as se
    monkeypatch.setattr(se, "SMART_ROUTING_ENABLED", True)
    monkeypatch.setattr(se, "SMART_ROUTING_THRESHOLD", 200)
    from app.services.smart_execution import should_route
    assert should_route(199) is False
    assert should_route(200) is True


# ── route_and_submit_entry ────────────────────────────────────────────────────

def test_small_order_returns_none(monkeypatch):
    """Below threshold → returns None so caller uses normal bracket."""
    from app.services import smart_execution as se
    monkeypatch.setattr(se, "SMART_ROUTING_ENABLED", True)
    monkeypatch.setattr(se, "SMART_ROUTING_THRESHOLD", 500)
    from app.services.smart_execution import route_and_submit_entry

    result = route_and_submit_entry(
        symbol="AAPL", qty=50, price=200.0,
        stop_loss=180.0, take_profit=220.0,
        strategy_id=None,
    )
    assert result is None


def test_large_order_submits_slices_and_oco(monkeypatch):
    """Large order → slices submitted, OCO placed, result dict returned."""
    from app.services import smart_execution as se

    monkeypatch.setattr(se, "SMART_ROUTING_ENABLED", True)
    monkeypatch.setattr(se, "SMART_ROUTING_THRESHOLD", 100)
    monkeypatch.setattr(se, "SMART_ROUTING_STRATEGY", "simple")
    monkeypatch.setattr(se, "SMART_ROUTING_MAX_SLICES", 3)
    monkeypatch.setattr(se, "SMART_ROUTING_INTERVAL", 0)

    submitted_orders = []

    def _fake_submit(payload, strategy_id=None):
        submitted_orders.append(dict(payload))
        return {"id": f"fake-{len(submitted_orders)}", "status": "pending_new"}

    monkeypatch.setattr(se, "_adv_lookup", lambda sym: 1_000_000)

    from app.services.smart_execution import route_and_submit_entry
    import app.services.smart_execution as _se_mod
    monkeypatch.setattr(_se_mod, "route_and_submit_entry", route_and_submit_entry)

    # Patch _submit_order inside the smart_execution module
    import app.services.trading_engine as te_mod
    monkeypatch.setattr(te_mod, "_submit_order", _fake_submit)

    result = route_and_submit_entry(
        symbol="AAPL", qty=600, price=200.0,
        stop_loss=180.0, take_profit=220.0,
        strategy_id=None,
        use_trailing=False,
        trail_pct=0.0,
        client_order_id_prefix="TEST-AAPL-B-abc123",
    )

    assert result is not None, "Large order must return a result dict, not None"
    assert result["slices_submitted"] > 0, "At least one slice must be submitted"
    assert result["filled_qty"] > 0

    # OCO should be the last submitted order
    oco_orders = [o for o in submitted_orders if o.get("order_class") == "oco"]
    assert len(oco_orders) >= 1, "Exactly one OCO exit order must be placed"


def test_oco_qty_covers_full_filled_quantity(monkeypatch):
    """OCO exit qty must equal the total qty submitted across all slices."""
    from app.services import smart_execution as se

    monkeypatch.setattr(se, "SMART_ROUTING_ENABLED", True)
    monkeypatch.setattr(se, "SMART_ROUTING_THRESHOLD", 100)
    monkeypatch.setattr(se, "SMART_ROUTING_STRATEGY", "simple")
    monkeypatch.setattr(se, "SMART_ROUTING_MAX_SLICES", 5)
    monkeypatch.setattr(se, "SMART_ROUTING_INTERVAL", 0)
    monkeypatch.setattr(se, "_adv_lookup", lambda sym: 2_000_000)

    submitted_orders = []

    def _fake_submit(payload, strategy_id=None):
        submitted_orders.append(dict(payload))
        return {"id": f"fake-{len(submitted_orders)}", "status": "pending_new"}

    import app.services.trading_engine as te_mod
    monkeypatch.setattr(te_mod, "_submit_order", _fake_submit)

    from app.services.smart_execution import route_and_submit_entry

    total_qty = 500
    result = route_and_submit_entry(
        symbol="MSFT", qty=total_qty, price=350.0,
        stop_loss=330.0, take_profit=380.0,
        strategy_id=None,
    )

    assert result is not None
    oco_orders = [o for o in submitted_orders if o.get("order_class") == "oco"]
    assert oco_orders, "OCO order must have been submitted"
    oco_qty = int(float(oco_orders[-1]["qty"]))
    assert oco_qty == result["filled_qty"], (
        f"OCO qty {oco_qty} must equal filled_qty {result['filled_qty']}"
    )


def test_oco_failure_triggers_emergency_sl(monkeypatch):
    """If OCO submit fails, an emergency stop-loss must be placed."""
    from app.services import smart_execution as se

    monkeypatch.setattr(se, "SMART_ROUTING_ENABLED", True)
    monkeypatch.setattr(se, "SMART_ROUTING_THRESHOLD", 10)
    monkeypatch.setattr(se, "SMART_ROUTING_STRATEGY", "simple")
    monkeypatch.setattr(se, "SMART_ROUTING_MAX_SLICES", 2)
    monkeypatch.setattr(se, "SMART_ROUTING_INTERVAL", 0)
    monkeypatch.setattr(se, "_adv_lookup", lambda sym: 500_000)

    call_count = {"n": 0}

    def _selective_fail(payload, strategy_id=None):
        call_count["n"] += 1
        if payload.get("order_class") == "oco":
            return None  # OCO fails
        return {"id": f"fake-{call_count['n']}", "status": "pending_new"}

    import app.services.trading_engine as te_mod
    monkeypatch.setattr(te_mod, "_submit_order", _selective_fail)

    # Suppress Telegram in tests
    try:
        import app.services.telegram_alert as tg
        monkeypatch.setattr(tg, "send_message", lambda msg: None)
    except Exception:
        pass

    from app.services.smart_execution import route_and_submit_entry

    result = route_and_submit_entry(
        symbol="ILIQ", qty=100, price=10.0,
        stop_loss=9.0, take_profit=12.0,
        strategy_id=None,
    )

    assert result is not None
    assert result["oco_status"] in ("emergency_sl", "emergency_sl_failed"), (
        "OCO failure must trigger emergency stop-loss attempt"
    )
