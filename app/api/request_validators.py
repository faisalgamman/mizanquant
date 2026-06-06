"""HTTP request-input validators for the screener API (pure, no app state).

Extracted verbatim from the ``halal_screener`` monolith (M-E). These raise
``fastapi.HTTPException(400)`` on bad input and return the cleaned value
otherwise. ``halal_screener`` re-exports them so existing router imports
(``from halal_screener import validate_symbol, validate_range, ...``) are
unchanged.

NOTE: this ``validate_symbol`` is the HTTP-layer string/raise variant. It is
intentionally distinct from ``app.services.validators.validate_symbol``, which
returns a structured ``ValidationResult`` for the pre-trade order path.
"""
from __future__ import annotations

import re

from fastapi import HTTPException


def validate_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if not re.match(r'^[A-Z]{1,6}(\.[A-Z])?$', s):
        raise HTTPException(status_code=400, detail=f"Invalid symbol format: {symbol}")
    return s


def validate_date(d: str) -> str:
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
        raise HTTPException(status_code=400, detail=f"Invalid date format: {d}. Use YYYY-MM-DD")
    return d


def validate_range(val, name, min_v, max_v):
    if val < min_v or val > max_v:
        raise HTTPException(status_code=400, detail=f"{name} must be between {min_v} and {max_v}, got {val}")
    return val
