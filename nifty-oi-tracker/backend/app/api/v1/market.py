from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from app.core.constants import MARKET_CLOSE, MARKET_OPEN
from app.core.dependencies import get_scheduler_service

router = APIRouter(prefix="/market", tags=["market"])

IST = timezone(timedelta(hours=5, minutes=30))


@router.get("/status")
async def get_market_status(request: Request) -> dict:
    """Market open/close status with current IST time."""
    scheduler = request.app.state.scheduler if hasattr(request.app.state, "scheduler") else None
    is_open = scheduler.is_market_open() if scheduler else False
    now_ist = datetime.now(IST)
    return {
        "is_open": is_open,
        "market_open": MARKET_OPEN.isoformat(),
        "market_close": MARKET_CLOSE.isoformat(),
        "server_time": now_ist.isoformat(),
    }


@router.post("/refresh")
async def trigger_refresh(request: Request) -> dict:
    """Trigger manual data fetch."""
    scheduler = request.app.state.scheduler if hasattr(request.app.state, "scheduler") else None
    if scheduler:
        await scheduler.trigger_now()
        return {"message": "Refresh triggered"}
    return {"message": "Scheduler not initialized"}
