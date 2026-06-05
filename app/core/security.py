"""Shared API-key security scheme.

Single source of truth for the operator API-key dependency. Living in a leaf
module (no app-level imports) lets routers import `OperatorAPIKey` at module
scope without the circular import against `halal_screener`. That module-level
availability is what lets FastAPI resolve the `OperatorAPIKey` annotation under
`from __future__ import annotations` (otherwise it stays an unresolved
ForwardRef and every guarded route fails with a pydantic "not fully defined"
error → 401).
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Security
from fastapi.security import APIKeyHeader

operator_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
OperatorAPIKey = Annotated[Optional[str], Security(operator_api_key_header)]
