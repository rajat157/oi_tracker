"""Repository for OI snapshot data (oi_snapshots table)."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from db.base_repo import BaseRepository


class SnapshotRepository(BaseRepository):
    """CRUD for the oi_snapshots table."""

    def save_snapshot(self, timestamp: datetime, spot_price: float,
                      strikes_data: dict, expiry_date: str) -> None:
        """Persist a full option-chain snapshot (delegates to legacy for now)."""
        from database import save_snapshot as _legacy
        _legacy(timestamp, spot_price, strikes_data, expiry_date)

    def get_latest(self) -> Optional[dict]:
        """Return the most recent snapshot metadata."""
        from database import get_latest_snapshot as _legacy
        return _legacy()

    def get_strikes_for_timestamp(self, timestamp: str) -> dict:
        """Return strike-level data for a given timestamp string."""
        from database import get_strikes_for_timestamp as _legacy
        return _legacy(timestamp)

    def get_previous_strikes_data(self) -> Optional[dict]:
        """Return strike data from the previous snapshot."""
        from database import get_previous_strikes_data as _legacy
        return _legacy()
