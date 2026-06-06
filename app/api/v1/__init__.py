from fastapi import APIRouter

from app.api.v1.system import router as system_router
from app.api.v1.market import router as market_router
from app.api.v1.trading import router as trading_router
from app.api.v1.pipeline import router as pipeline_router
from app.api.v1.guards import router as guards_router
from app.api.v1.scoring import router as scoring_router
from app.api.v1.watchlist import router as watchlist_router
from app.api.v1.overview import router as overview_router
from app.api.v1.paper import router as paper_router
from app.api.v1.forecast import router as forecast_router

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(system_router)
v1_router.include_router(market_router)
v1_router.include_router(trading_router)
v1_router.include_router(pipeline_router)
v1_router.include_router(guards_router)
v1_router.include_router(scoring_router)
v1_router.include_router(watchlist_router)
v1_router.include_router(overview_router)
v1_router.include_router(paper_router)
v1_router.include_router(forecast_router)
