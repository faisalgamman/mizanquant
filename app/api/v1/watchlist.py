from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger("screener")

router = APIRouter(tags=["v1-watchlist"])


@router.get("/watchlist")
async def v1_get_watchlist():
    from app.services.watchlist_service import get_watchlist
    return {"symbols": get_watchlist()}


@router.put("/watchlist")
async def v1_set_watchlist(body: dict):
    from app.services.watchlist_service import set_watchlist
    symbols = body.get("symbols", [])
    saved = set_watchlist(symbols)
    return {"symbols": saved, "count": len(saved)}


@router.post("/watchlist/add/{symbol}")
async def v1_add_to_watchlist(symbol: str):
    from app.services.watchlist_service import add_symbol
    updated = add_symbol(symbol)
    return {"symbols": updated, "count": len(updated)}


@router.delete("/watchlist/remove/{symbol}")
async def v1_remove_from_watchlist(symbol: str):
    from app.services.watchlist_service import remove_symbol
    updated = remove_symbol(symbol)
    return {"symbols": updated, "count": len(updated)}
