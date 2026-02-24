from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/latest")
async def get_latest_analysis(db: AsyncSession = Depends(get_db)) -> dict:
    """Full dashboard payload: verdict, active trades, chart history, zones."""
    return {"message": "Not implemented yet"}


@router.get("/history")
async def get_analysis_history(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Historical analysis for charts."""
    return {"data": [], "count": 0}
