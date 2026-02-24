from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.schemas.common import StrategyName
from app.services.trade_service import TradeService

router = APIRouter(prefix="/trades", tags=["trades"])


def _parse_strategy(strategy: str) -> StrategyName:
    try:
        return StrategyName(strategy)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid strategy: {strategy}. Valid: {[s.value for s in StrategyName]}",
        )


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
    sname = _parse_strategy(strategy)
    svc = TradeService(db)
    data, total = await svc.get_trades(
        sname, days=days, status=status, direction=direction,
        limit=limit, offset=offset,
    )
    return {"strategy": strategy, "data": data, "count": total}


@router.get("/{strategy}/stats")
async def get_trade_stats(
    strategy: str,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Win rate, avg P/L per strategy."""
    sname = _parse_strategy(strategy)
    svc = TradeService(db)
    stats = await svc.get_stats(sname, days=days)
    return {"strategy": strategy, "stats": stats}
