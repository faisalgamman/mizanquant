"""Tests for app.services.validators (M1 hardening)."""
from __future__ import annotations

import pytest

from app.services.validators import (
    ValidationResult,
    make_client_order_id,
    validate_entry,
    validate_symbol,
)


class TestSymbol:
    @pytest.mark.parametrize("sym", ["AAPL", "MSFT", "BRK.B", "F", "GOOGL"])
    def test_valid_symbols(self, sym):
        assert validate_symbol(sym).ok

    @pytest.mark.parametrize("sym", ["", None, "   ", "aapl123", "TOO.LONG.X", "12345", "GOOGLE"])
    def test_bad_syntax(self, sym):
        r = validate_symbol(sym)
        assert not r.ok
        assert r.code in {"SYMBOL_EMPTY", "SYMBOL_MALFORMED"}

    @pytest.mark.parametrize("sym", ["FB", "TWTR", "ATVI"])
    def test_delisted_blocked(self, sym):
        r = validate_symbol(sym)
        assert not r.ok
        assert r.code == "SYMBOL_DELISTED"


class TestEntry:
    def test_happy_path(self, valid_entry_kwargs):
        assert validate_entry(**valid_entry_kwargs).ok

    def test_price_must_be_positive(self, valid_entry_kwargs):
        valid_entry_kwargs["price"] = 0
        r = validate_entry(**valid_entry_kwargs)
        assert not r.ok and r.code == "PRICE_INVALID"

    def test_stop_above_price_rejected(self, valid_entry_kwargs):
        valid_entry_kwargs["stop_loss"] = valid_entry_kwargs["price"] + 1
        r = validate_entry(**valid_entry_kwargs)
        assert not r.ok and r.code == "SL_ABOVE_PRICE"

    def test_tp_below_price_rejected(self, valid_entry_kwargs):
        valid_entry_kwargs["take_profit"] = valid_entry_kwargs["price"] - 1
        r = validate_entry(**valid_entry_kwargs)
        assert not r.ok and r.code == "TP_BELOW_PRICE"

    def test_rr_too_low_rejected(self, valid_entry_kwargs):
        # Risk = 5, reward < 2.5 -> rejected
        valid_entry_kwargs["stop_loss"] = 175.0
        valid_entry_kwargs["take_profit"] = 181.0  # reward = 1 vs risk 5
        r = validate_entry(**valid_entry_kwargs)
        assert not r.ok and r.code == "RR_TOO_LOW"

    def test_notional_floor(self, valid_entry_kwargs):
        valid_entry_kwargs["qty"] = 0  # qty invalid
        r = validate_entry(**valid_entry_kwargs)
        assert not r.ok and r.code == "QTY_INVALID"

    def test_notional_cap(self, valid_entry_kwargs):
        r = validate_entry(**valid_entry_kwargs, max_notional_usd=100)
        assert not r.ok and r.code == "NOTIONAL_TOO_LARGE"

    def test_small_notional_rejected(self, valid_entry_kwargs):
        # 1 share at 180 -> 180 > 50 floor, fine. Lower the floor then flip.
        r = validate_entry(**valid_entry_kwargs, min_notional_usd=10_000)
        assert not r.ok and r.code == "NOTIONAL_TOO_SMALL"

    def test_invalid_side(self, valid_entry_kwargs):
        valid_entry_kwargs["side"] = "hodl"
        r = validate_entry(**valid_entry_kwargs)
        assert not r.ok and r.code == "SIDE_INVALID"

    def test_bool_protocol(self):
        assert bool(ValidationResult.success()) is True
        assert bool(ValidationResult.fail("X", "x")) is False


class TestClientOrderId:
    def test_length_bounded(self):
        for _ in range(50):
            coid = make_client_order_id("A", "AAPL", "buy")
            assert 1 <= len(coid) <= 48

    def test_uniqueness(self):
        ids = {make_client_order_id("A", "AAPL", "buy") for _ in range(1000)}
        assert len(ids) == 1000  # 6-hex random tail -> collisions vanishingly rare

    def test_prefix_encodes_strategy_and_symbol(self):
        coid = make_client_order_id("B", "MSFT", "buy")
        assert coid.startswith("B-MSFT-B-")

    def test_null_strategy_ok(self):
        coid = make_client_order_id(None, "AAPL", "sell")
        assert coid.startswith("X-AAPL-S-")
