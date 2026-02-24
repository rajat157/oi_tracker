from fastapi import APIRouter

from app.api.v1.analysis import router as analysis_router
from app.api.v1.events import router as events_router
from app.api.v1.kite import router as kite_router
from app.api.v1.logs import router as logs_router
from app.api.v1.market import router as market_router
from app.api.v1.trades import router as trades_router

api_v1_router = APIRouter()

api_v1_router.include_router(analysis_router)
api_v1_router.include_router(trades_router)
api_v1_router.include_router(market_router)
api_v1_router.include_router(events_router)
api_v1_router.include_router(kite_router)
api_v1_router.include_router(logs_router)
