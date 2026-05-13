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


def validate_range(val, name: str, min_v, max_v):
    if val < min_v or val > max_v:
        raise HTTPException(status_code=400, detail=f"{name} must be between {min_v} and {max_v}, got {val}")
    return val
