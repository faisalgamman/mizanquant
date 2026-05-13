from __future__ import annotations

from fastapi import HTTPException

from app.config import settings
from app.utils.validation import validate_symbol, validate_date, validate_range


def require_api_key(api_key: str | None):
    if not settings.API_KEY:
        raise HTTPException(status_code=503, detail="Operator API key not configured")
    if api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
