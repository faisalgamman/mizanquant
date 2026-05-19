"""Tests for order_validator.py — 20+ tests."""
from __future__ import annotations

import pytest
from app.services.order_state import OrderRecord, OrderState
from app.services.order_validator import (
    validate_symbol,
    validate_side,
    validate_qty,
    validate_order_type,
    validate_time_in_force,
    validate_prices,
    validate_bracket_prices,
    validate_size_limits,
    validate_blacklist,
    fast_validate,
    validate_order,
    ValidationResult,
)

# ── Helpers ─────────────────────────────────────────────────────

def _order(**kw):
    defaults = {
        "order_id": "",
        "client_order_id": "test-001",
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "market",
        "time_in_force": "day",
        "qty": 10,
        "status": OrderState.CREATED,
    }
    return OrderRecord(**(defaults | kw))

# ── validate_symbol ─────────────────────────────────────────────

def test_symbol_missing():
    assert validate_symbol(_order(symbol="")) == "SYMBOL_MISSING"

def test_symbol_too_long():
    assert validate_symbol(_order(symbol="ABCDEFGHIJKLM")) == "SYMBOL_TOO_LONG"

def test_symbol_malformed_numeric():
    assert validate_symbol(_order(symbol="AAP123")) == "SYMBOL_MALFORMED"

def test_symbol_malformed_lowercase():
    # Lowercase is auto-normalized to uppercase — valid
    assert validate_symbol(_order(symbol="aapl")) is None

def test_symbol_malformed_special():
    # Symbols with special characters other than dots are invalid
    assert validate_symbol(_order(symbol="AAP-L")) == "SYMBOL_MALFORMED"

def test_symbol_ok():
    assert validate_symbol(_order(symbol="AAPL")) is None
    assert validate_symbol(_order(symbol="BRK.B")) is None

# ── validate_side ───────────────────────────────────────────────

def test_side_buy_ok():
    assert validate_side(_order(side="buy")) is None

def test_side_sell_ok():
    assert validate_side(_order(side="sell")) is None

def test_side_invalid():
    assert validate_side(_order(side="short")) == "SIDE_INVALID:short"

# ── validate_qty ────────────────────────────────────────────────

def test_qty_zero():
    assert validate_qty(_order(qty=0)) == "QTY_INVALID:0"

def test_qty_negative():
    assert validate_qty(_order(qty=-5)) == "QTY_INVALID:-5"

def test_qty_too_large():
    assert validate_qty(_order(qty=2_000_000)) == "QTY_TOO_LARGE:2000000"

def test_qty_ok():
    assert validate_qty(_order(qty=100)) is None

# ── validate_order_type ─────────────────────────────────────────

def test_order_type_market_ok():
    assert validate_order_type(_order(order_type="market")) is None

def test_order_type_invalid():
    r = validate_order_type(_order(order_type="foobar"))
    assert r and "ORDER_TYPE_INVALID" in r

# ── validate_time_in_force ──────────────────────────────────────

def test_tif_day_ok():
    assert validate_time_in_force(_order(time_in_force="day")) is None

def test_tif_invalid():
    r = validate_time_in_force(_order(time_in_force="forever"))
    assert r and "TIF_INVALID" in r

# ── validate_prices ─────────────────────────────────────────────

def test_limit_price_missing():
    r = validate_prices(_order(order_type="limit", limit_price=None))
    assert r == "LIMIT_PRICE_MISSING"

def test_limit_price_negative():
    r = validate_prices(_order(order_type="limit", limit_price=-5.0))
    assert r == "LIMIT_PRICE_NEGATIVE:-5.0"

def test_prices_ok_for_market():
    assert validate_prices(_order(order_type="market")) is None

# ── validate_bracket_prices ─────────────────────────────────────

def test_bracket_sl_above_tp_buy():
    r = validate_bracket_prices(_order(
        order_class="bracket", side="buy",
        stop_loss_price=110.0, take_profit_price=100.0,
    ))
    assert r and "SL_ABOVE_TP_BUY" in r

def test_bracket_prices_ok():
    assert validate_bracket_prices(_order(
        order_class="bracket", side="buy",
        stop_loss_price=95.0, take_profit_price=110.0,
    )) is None

def test_bracket_no_prices_ok():
    assert validate_bracket_prices(_order(order_class="bracket")) is None

# ── validate_blacklist ──────────────────────────────────────────

def test_blacklist_vxx():
    r = validate_blacklist(_order(symbol="VXX"))
    assert r and "BLACKLISTED" in r

# ── fast_validate / composite ───────────────────────────────────

def test_fast_validate_passes_for_good_order():
    result = fast_validate(_order(
        symbol="MSFT", side="buy", qty=50,
        order_type="market", time_in_force="gtc",
    ))
    assert result.passed
    assert result.reason() == "VALIDATION_PASSED"

def test_fast_validate_catches_multiple_errors():
    result = fast_validate(_order(
        symbol="", side="short", qty=0, order_type="bad",
    ))
    assert not result.passed
    assert len(result.failures) >= 3

def test_validate_order_with_extra_validators():
    def extra(o):
        if o.symbol == "TSLA":
            return "NO_TSLA"
        return None

    result = validate_order(_order(symbol="TSLA"), extra_validators=[extra])
    assert not result.passed
    assert any("NO_TSLA" in f for f in result.failures)

def test_validation_result_truthiness():
    r = ValidationResult()
    assert bool(r)
    r.add_failure("X", "bad")
    assert not bool(r)


