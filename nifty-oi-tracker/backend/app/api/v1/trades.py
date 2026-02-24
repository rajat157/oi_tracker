from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("/{strategy}")
async def get_trades(
    strategy: str,
    days: int = Query(30, ge=1, le=365),
    status: str | None = None,
    direction: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Unified trade history per strategy."""
    return {"strategy": strategy, "data": [], "count": 0}


@router.get("/{strategy}/stats")
async def get_trade_stats(
    strategy: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Win rate, avg P/L per strategy."""
    return {"strategy": strategy, "stats": {}}
