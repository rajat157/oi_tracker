from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.models.system_log import SystemLog

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
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    conditions = [SystemLog.timestamp >= cutoff]
    if level:
        conditions.append(SystemLog.level == level.upper())
    if component:
        conditions.append(SystemLog.component == component)

    stmt = (
        select(SystemLog)
        .where(and_(*conditions))
        .order_by(SystemLog.id.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    data = [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "level": r.level,
            "component": r.component,
            "message": r.message,
            "details": r.details,
            "session_id": r.session_id,
        }
        for r in rows
    ]
    return {"data": data, "count": len(data)}
