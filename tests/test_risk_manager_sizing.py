"""Lock the position-cap invariant of the LIVE sizing path.

`risk_manager.calculate_position_size` is what actually sizes real orders (the
`trade_plan` variant is deprecated). The safety property that must never
regress: a position is capped by ``min(equity * max_position_pct,
max_notional_per_order)`` even when the pure risk-based size is far larger.
"""
from __future__ import annotations

from app.core.config import app_cfg
from app.services.risk_manager import calculate_position_size


def _max_position_value(equity: float) -> float:
    return min(
        equity * (app_cfg.risk.max_position_pct / 100.0),
        app_cfg.execution.max_notional_per_order_usd,
    )


def test_position_cap_binds_when_risk_size_is_larger():
    # Tiny risk-per-share (0.10) makes the risk-based size huge, so the
    # max-position cap is the binding constraint.
    price, stop = 100.0, 99.9
    equity, cash = 100_000.0, 10_000_000.0
    res = calculate_position_size(price=price, stop_loss=stop,
                                  available_cash=cash, total_equity=equity)

    cap_value = _max_position_value(equity)
    assert res["qty"] == int(cap_value / price)        # cap, not the risk size
    assert res["position_value"] <= cap_value + 1e-6   # never exceeds the cap


def test_invalid_inputs_return_zero():
    for price, stop in [(0.0, 95.0), (100.0, 0.0), (100.0, 100.0), (100.0, 110.0)]:
        res = calculate_position_size(price=price, stop_loss=stop,
                                      available_cash=1_000_000.0, total_equity=100_000.0)
        assert res["qty"] == 0


def test_cash_constraint_can_bind():
    # Cash too small to buy even one share at the cap -> blocked.
    res = calculate_position_size(price=100.0, stop_loss=98.0,
                                  available_cash=50.0, total_equity=100_000.0)
    assert res["qty"] == 0
