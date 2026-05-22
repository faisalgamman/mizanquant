"""Router for Investors Insights features."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.services.investors_service import investors_service

router = APIRouter(prefix="/api/investors", tags=["investors"])


@router.get("/goat", summary="Get G.O.A.T Investors")
def get_goat_investors() -> dict[str, Any]:
    """Retrieve the list of famous investors and their portfolio info."""
    try:
        data = investors_service.get_goat_investors()
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/forecasts", summary="Get Analyst Forecasts")
def get_analyst_forecasts() -> dict[str, Any]:
    """Retrieve recent analyst stock recommendations and price targets."""
    try:
        data = investors_service.get_analyst_forecasts()
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hot-picks", summary="Get Hot Investors Picks")
def get_hot_picks() -> dict[str, Any]:
    """Retrieve stocks heavily bought by institutional investors."""
    try:
        data = investors_service.get_hot_picks()
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
