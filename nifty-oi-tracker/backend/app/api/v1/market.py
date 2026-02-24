from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.constants import MARKET_CLOSE, MARKET_OPEN

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/status")
async def get_market_status() -> dict:
    """Market open/close status with current time."""
    now = datetime.now(timezone.utc)
    # IST = UTC+5:30, simplified check
    ist_hour = (now.hour + 5) % 24 + (1 if now.minute >= 30 else 0)
    return {
        "is_open": False,  # Placeholder — proper IST logic in service
        "market_open": MARKET_OPEN.isoformat(),
        "market_close": MARKET_CLOSE.isoformat(),
        "server_time": now.isoformat(),
    }


@router.post("/refresh")
async def trigger_refresh() -> dict:
    """Trigger manual data fetch."""
    return {"message": "Refresh triggered"}
