"""Tests for Transaction Cost Analysis (Phase 2C).

Verifies:
1. compute_tca: BUY fill > decision → positive realized_slippage_bps.
2. compute_tca: SELL fill < decision → positive realized_slippage_bps.
3. compute_tca: perfect fill (no slippage) → 0 bps.
4. slippage_vs_model_bps = realized − modeled when est_bps supplied.
5. slippage_vs_model_bps = None when est_bps not supplied.
6. Zero prices handled gracefully (returns None for bps).
7. record_tca_to_trade persists tca into signal_details.
8. record_tca_to_trade is idempotent (does not overwrite existing tca).
"""

from __future__ import annotations

import json

import pytest


# ── compute_tca unit tests ────────────────────────────────────────────────────

def test_buy_fill_above_decision_is_positive_slippage():
    from app.services.tca import compute_tca

    result = compute_tca("buy", decision_price=100.0, filled_avg_price=100.50, est_bps=5.0)
    # Filled above decision → paid more → positive slippage
    assert result["realized_slippage_bps"] > 0, (
        "BUY fill above decision should produce positive slippage"
    )


def test_buy_fill_below_decision_is_negative_slippage():
    from app.services.tca import compute_tca

    result = compute_tca("buy", decision_price=100.0, filled_avg_price=99.80, est_bps=5.0)
    # Filled below decision → paid less → negative slippage (lucky fill)
    assert result["realized_slippage_bps"] < 0


def test_sell_fill_below_decision_is_positive_slippage():
    from app.services.tca import compute_tca

    result = compute_tca("sell", decision_price=100.0, filled_avg_price=99.50)
    # SELL received less than decision → positive cost
    assert result["realized_slippage_bps"] > 0


def test_perfect_fill_zero_slippage():
    from app.services.tca import compute_tca

    result = compute_tca("buy", decision_price=150.0, filled_avg_price=150.0, est_bps=10.0)
    assert result["realized_slippage_bps"] == pytest.approx(0.0, abs=1e-6)


def test_slippage_vs_model_computed_when_est_bps_given():
    from app.services.tca import compute_tca

    result = compute_tca("buy", decision_price=100.0, filled_avg_price=100.30, est_bps=20.0)
    assert result["slippage_vs_model_bps"] is not None
    # realized ≈ 30 bps, modeled 20 → vs_model ≈ 10 bps
    assert abs(result["slippage_vs_model_bps"] - (result["realized_slippage_bps"] - 20.0)) < 0.1


def test_slippage_vs_model_none_when_no_est_bps():
    from app.services.tca import compute_tca

    result = compute_tca("buy", decision_price=100.0, filled_avg_price=100.30)
    assert result["slippage_vs_model_bps"] is None


def test_zero_prices_handled():
    from app.services.tca import compute_tca

    result = compute_tca("buy", decision_price=0, filled_avg_price=100.0)
    assert result["realized_slippage_bps"] is None

    result2 = compute_tca("buy", decision_price=100.0, filled_avg_price=0)
    assert result2["realized_slippage_bps"] is None


# ── record_tca_to_trade integration test ─────────────────────────────────────

class _FakeTrade:
    """Minimal mock of a TradeHistory ORM row for unit testing."""

    def __init__(self, side="buy", decision_price=200.0, filled_avg=201.0, est_bps=15.0):
        # Simulate signal_details stored as a Python dict
        self.signal_details = {
            "decision_price": decision_price,
            "execution_estimate": {"est_cost_bps": est_bps},
        }
        self.filled_avg_price = filled_avg
        self.entry_price = decision_price
        self.side = side
        self.order_id = "test-order-001"

    # Minimal __table__ stub so flag_modified path doesn't crash
    class __table__:
        class columns:
            class signal_details:
                class type:
                    pass


def test_record_tca_persists_into_signal_details():
    from app.services.tca import record_tca_to_trade

    trade = _FakeTrade(side="buy", decision_price=200.0, filled_avg=201.0, est_bps=15.0)
    record_tca_to_trade(trade, db=None)

    details = trade.signal_details
    if isinstance(details, str):
        details = json.loads(details)

    assert "tca" in details, "record_tca_to_trade must add 'tca' to signal_details"
    tca = details["tca"]
    assert tca["realized_slippage_bps"] is not None
    assert tca["realized_slippage_bps"] > 0   # paid more → positive
    assert tca["modeled_bps"] == 15.0


def test_record_tca_idempotent():
    """Second call should not overwrite existing tca."""
    from app.services.tca import record_tca_to_trade

    trade = _FakeTrade(side="buy", decision_price=200.0, filled_avg=201.0, est_bps=15.0)
    record_tca_to_trade(trade, db=None)

    # Inject a sentinel value
    details = trade.signal_details
    if isinstance(details, str):
        details = json.loads(details)
    details["tca"]["_sentinel"] = "first_call"
    trade.signal_details = details

    # Second call should be a no-op
    record_tca_to_trade(trade, db=None)

    after = trade.signal_details
    if isinstance(after, str):
        after = json.loads(after)
    assert after["tca"].get("_sentinel") == "first_call", (
        "record_tca_to_trade must not overwrite existing tca"
    )
