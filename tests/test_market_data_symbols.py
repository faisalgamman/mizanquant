"""Pins the P4 yfinance symbol-handling fixes (from the 2026-06-07 prod log flood).

(1) Share-class formatting: yfinance wants BRK-B, not BRK.B — `_yf_symbol` maps
    the dot to a dash so the large-cap is fetchable instead of "delisted".
(2) Confirmed-delisted names are pre-seeded into the bad-symbol set so they are
    skipped BEFORE the 3-retry round-trip on every restart.
(3) A genuine, live ticker still validates normally (no over-blocking).
"""
from __future__ import annotations

from app.services.market_data import (
    _BAD_SYMBOLS,
    _KNOWN_DELISTED,
    _validate_symbol,
    _yf_symbol,
)


def test_yf_symbol_dot_to_dash():
    assert _yf_symbol("BRK.B") == "BRK-B"
    assert _yf_symbol("BF.B") == "BF-B"
    assert _yf_symbol("AAPL") == "AAPL"  # untouched when no dot


def test_known_delisted_are_blocked_pre_fetch():
    assert "XLNX" in _KNOWN_DELISTED
    assert _KNOWN_DELISTED <= _BAD_SYMBOLS  # seeded into the bad set
    assert _validate_symbol("XLNX") == ""   # blocked before any network call
    assert _validate_symbol("COUP") == ""


def test_live_tickers_not_over_blocked():
    # Names that merely failed transiently in the log must NOT be denylisted.
    for live in ("ZI", "SKX", "SAGE", "BPMC", "LEV"):
        assert live not in _KNOWN_DELISTED
        assert _validate_symbol(live) == live


def test_normal_symbol_validates():
    assert _validate_symbol("aapl") == "AAPL"      # upper-cased
    assert _validate_symbol("BRK.B") == "BRK.B"    # valid format kept (mapped only at fetch)
    assert _validate_symbol("") == ""
    assert _validate_symbol("TOOLONGSYM") == ""    # fails format check
