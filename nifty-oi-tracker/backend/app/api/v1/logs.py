from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
async def get_logs(
    level: str | None = None,
    component: str | None = None,
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Filtered system logs."""
    return {"data": [], "count": 0}
