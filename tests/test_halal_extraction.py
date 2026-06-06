"""Lock the M-E extraction contract for halal_screener.

The pure data tables and HTTP validators were moved out of the monolith into
``app.data.halal_exclusions`` and ``app.api.request_validators``. This test
asserts (1) the extracted modules carry the expected data/behaviour and (2)
``halal_screener`` still re-exports every moved name under its original spelling,
so the wide public contract (`from halal_screener import ...` / `hs.<name>`) is
unbroken.
"""
from __future__ import annotations

import pytest

from app.data.halal_exclusions import HARAM_EXCLUDE, SP500_DELISTED_HALAL
from app.api.request_validators import validate_symbol, validate_date, validate_range


def test_exclusion_tables_intact():
    assert isinstance(HARAM_EXCLUDE, set) and len(HARAM_EXCLUDE) == 144
    assert "JPM" in HARAM_EXCLUDE and "MO" in HARAM_EXCLUDE      # bank + tobacco
    assert isinstance(SP500_DELISTED_HALAL, list) and len(SP500_DELISTED_HALAL) == 27
    assert "ATVI" in SP500_DELISTED_HALAL                         # survivorship name


def test_validators_behaviour():
    from fastapi import HTTPException
    assert validate_symbol(" aapl ") == "AAPL"
    assert validate_date("2024-01-31") == "2024-01-31"
    assert validate_range(5, "x", 1, 10) == 5
    with pytest.raises(HTTPException):
        validate_symbol("not_a_symbol!")
    with pytest.raises(HTTPException):
        validate_date("31-01-2024")
    with pytest.raises(HTTPException):
        validate_range(99, "x", 1, 10)


def test_halal_screener_reexports_preserve_contract():
    import halal_screener as hs
    # Same objects, re-exported under the original private/public names.
    assert hs._HARAM_EXCLUDE is HARAM_EXCLUDE
    assert hs._SP500_DELISTED_HALAL is SP500_DELISTED_HALAL
    assert hs.validate_symbol is validate_symbol
    assert hs.validate_range is validate_range
    assert hs.validate_date is validate_date
    # Names other modules import by-name still resolve off the monolith.
    from halal_screener import (  # noqa: F401
        _HARAM_EXCLUDE, _SP500_DELISTED_HALAL, validate_symbol as vs, validate_range as vr,
    )
