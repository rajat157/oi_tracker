"""Repository for system logs."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from db.base_repo import BaseRepository


class LogRepository(BaseRepository):
    """CRUD for the system_logs table."""

    def save_log(self, timestamp: datetime, level: str, component: str,
                 message: str, details: str = None,
                 session_id: str = None) -> None:
        from db.legacy import save_log as _legacy
        _legacy(timestamp, level, component, message, details, session_id)

    def get_logs(self, level: str = None, component: str = None,
                 hours: int = 24, limit: int = 100,
                 offset: int = 0) -> Tuple[list, int]:
        from db.legacy import get_logs as _legacy
        return _legacy(level=level, component=component, hours=hours,
                       limit=limit, offset=offset)

    def purge(self, days: int = 7) -> None:
        from db.legacy import purge_old_logs as _legacy
        _legacy(days=days)
