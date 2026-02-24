from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.schemas.common import StrategyName
from app.services.analysis_service import AnalysisService
from app.services.trade_service import TradeService

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/latest")
async def get_latest_analysis(db: AsyncSession = Depends(get_db)) -> dict:
    """Full dashboard payload: verdict, active trades, chart history."""
    analysis_svc = AnalysisService(db)
    trade_svc = TradeService(db)

    latest = await analysis_svc.get_latest()
    chart_history = await analysis_svc.get_history(limit=50)

    active_trades = {}
    trade_stats = {}
    for strategy in StrategyName:
        active_trades[strategy.value] = await trade_svc.get_active_trade(strategy)
        trade_stats[strategy.value] = await trade_svc.get_stats(strategy)

    return {
        "analysis": latest,
        "active_trades": active_trades,
        "trade_stats": trade_stats,
        "chart_history": chart_history,
    }


@router.get("/history")
async def get_analysis_history(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Historical analysis for charts."""
    svc = AnalysisService(db)
    data = await svc.get_history(limit=limit)
    return {"data": data, "count": len(data)}
