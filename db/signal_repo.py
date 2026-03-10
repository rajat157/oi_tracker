"""Repository for signal outcomes and accuracy tracking."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from db.base_repo import BaseRepository


class SignalRepository(BaseRepository):
    """CRUD for signal_outcomes and related accuracy tables."""

    def save_signal_outcome(self, signal_timestamp: datetime, verdict: str,
                            strength: str, combined_score: float,
                            entry_price: float, **kwargs) -> int:
        """Persist a new signal outcome (delegates to legacy)."""
        from database import save_signal_outcome as _legacy
        return _legacy(signal_timestamp, verdict, strength, combined_score,
                       entry_price, **kwargs)

    def update_signal_outcome(self, signal_id: int, outcome_timestamp: datetime,
                              actual_exit_price: float, hit_target: bool,
                              hit_sl: bool, profit_loss_pct: float,
                              was_correct: bool) -> None:
        from database import update_signal_outcome as _legacy
        _legacy(signal_id, outcome_timestamp, actual_exit_price, hit_target,
                hit_sl, profit_loss_pct, was_correct)

    def get_pending_signals(self) -> list:
        from database import get_pending_signals as _legacy
        return _legacy()

    def get_signal_accuracy(self, lookback_days: int = 30) -> dict:
        from database import get_signal_accuracy as _legacy
        return _legacy(lookback_days=lookback_days)
