"""Repository for analysis results (analysis_history table)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from db.base_repo import BaseRepository


class AnalysisRepository(BaseRepository):
    """CRUD for the analysis_history table."""

    def save_analysis(self, timestamp: datetime, spot_price: float,
                      atm_strike: int, total_call_oi: int, total_put_oi: int,
                      call_oi_change: int, put_oi_change: int, verdict: str,
                      expiry_date: str, **kwargs) -> None:
        """Persist an analysis result (delegates to legacy)."""
        from db.legacy import save_analysis as _legacy
        _legacy(timestamp, spot_price, atm_strike, total_call_oi, total_put_oi,
                call_oi_change, put_oi_change, verdict, expiry_date, **kwargs)

    def get_latest(self) -> Optional[dict]:
        from db.legacy import get_latest_analysis as _legacy
        return _legacy()

    def get_history(self, limit: int = 50, date: Optional[str] = None) -> list:
        from db.legacy import get_analysis_history as _legacy
        return _legacy(limit=limit, date=date)

    def get_previous_verdict(self, today_only: bool = True) -> Optional[str]:
        from db.legacy import get_previous_verdict as _legacy
        return _legacy(today_only=today_only)

    def get_previous_smoothed_score(self) -> Optional[float]:
        from db.legacy import get_previous_smoothed_score as _legacy
        return _legacy()

    def get_recent_price_trend(self, lookback_minutes: int = 9) -> list:
        from db.legacy import get_recent_price_trend as _legacy
        return _legacy(lookback_minutes=lookback_minutes)

    def get_recent_oi_changes(self, lookback: int = 3) -> list:
        from db.legacy import get_recent_oi_changes as _legacy
        return _legacy(lookback=lookback)
