"""Abstract base class for all trading strategy trackers.

Provides the shared lifecycle skeleton: time-window checks, P&L helpers,
daily-trade guards, and a standard interface that the scheduler can iterate.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, time
from typing import Any, Dict, List, Optional

from core.events import EventBus, EventType, event_bus as _default_bus

logger = logging.getLogger(__name__)


class BaseTracker(ABC):
    """Abstract base for every strategy tracker.

    Subclass attributes (set as class variables):
        tracker_type        — unique slug, e.g. "iron_pulse", "selling"
        table_name          — DB table holding trades
        time_start          — earliest signal creation time
        time_end            — latest signal creation time
        force_close_time    — hard exit time for open trades
        max_trades_per_day  — cap (1 for most, 5 for scalper)
        is_selling          — True flips P&L direction
        supports_pending    — True only for IronPulse (PENDING→ACTIVE flow)
    """

    # --- Subclass must set these ---
    tracker_type: str = ""
    table_name: str = ""
    time_start: time = time(9, 30)
    time_end: time = time(14, 0)
    force_close_time: time = time(15, 20)
    max_trades_per_day: int = 1
    is_selling: bool = False
    supports_pending: bool = False

    def __init__(self, trade_repo: Any = None, bus: EventBus | None = None) -> None:
        self.trade_repo = trade_repo
        self.bus = bus or _default_bus

    # ------------------------------------------------------------------
    # Abstract interface — each strategy implements these
    # ------------------------------------------------------------------

    @abstractmethod
    def should_create(self, analysis: dict, **kwargs) -> bool:
        """Return True if the strategy conditions are met for a new trade."""
        ...

    @abstractmethod
    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict, **kwargs) -> Optional[int]:
        """Create a new trade in the DB. Return trade_id or None."""
        ...

    @abstractmethod
    def check_and_update(self, strikes_data: dict, **kwargs) -> Optional[Dict]:
        """Check the active trade and update status. Return result dict or None."""
        ...

    @abstractmethod
    def get_active(self) -> Optional[Dict]:
        """Return the currently active trade row (dict) or None."""
        ...

    @abstractmethod
    def get_stats(self, lookback_days: int = 30) -> Dict:
        """Return strategy performance statistics."""
        ...

    # ------------------------------------------------------------------
    # Concrete shared helpers
    # ------------------------------------------------------------------

    def is_in_time_window(self, now: Optional[datetime] = None) -> bool:
        """Check whether *now* falls within the strategy's entry window."""
        now = now or datetime.now()
        return self.time_start <= now.time() <= self.time_end

    def is_past_force_close(self, now: Optional[datetime] = None) -> bool:
        """Check whether *now* is past the hard force-close time."""
        now = now or datetime.now()
        return now.time() >= self.force_close_time

    @staticmethod
    def calculate_pnl(entry: float, current: float, is_selling: bool = False) -> float:
        """Percentage P&L from entry to current premium.

        For buying: (current - entry) / entry * 100
        For selling: (entry - current) / entry * 100  (profit when premium falls)
        """
        if entry == 0:
            return 0.0
        if is_selling:
            return round((entry - current) / entry * 100, 2)
        return round((current - entry) / entry * 100, 2)

    def already_traded_today(self) -> bool:
        """Check if max daily trades reached. Requires trade_repo to be set."""
        if self.trade_repo is None:
            return False
        today = datetime.now().strftime("%Y-%m-%d")
        trades = self.trade_repo.get_todays_trades(self.table_name, today)
        return len(trades) >= self.max_trades_per_day

    @staticmethod
    def get_current_premium(strikes_data: dict, strike: int, option_type: str) -> Optional[float]:
        """Look up the current LTP for a strike/option_type in strikes_data."""
        strike_data = strikes_data.get(str(strike)) or strikes_data.get(strike)
        if not strike_data:
            return None
        key = "ce_ltp" if option_type == "CE" else "pe_ltp"
        return strike_data.get(key)

    def force_exit(self, trade_id: int, exit_premium: float,
                   reason: str, pnl_pct: float,
                   alert_message: str | None = None) -> None:
        """Force-exit a trade (used by PremiumMonitor WebSocket exits).

        Updates the DB row and publishes TRADE_EXITED.
        """
        if self.trade_repo is None:
            logger.warning("force_exit called but trade_repo is None")
            return
        now = datetime.now()
        status = "WON" if pnl_pct > 0 else "LOST"
        self.trade_repo.update_trade(
            self.table_name, trade_id,
            status=status,
            resolved_at=now,
            exit_premium=exit_premium,
            exit_reason=reason,
            profit_loss_pct=round(pnl_pct, 2),
        )
        event_data = {
            "trade_id": trade_id,
            "action": status,
            "pnl": round(pnl_pct, 2),
            "reason": reason,
            "exit_premium": exit_premium,
        }
        if alert_message:
            event_data["alert_message"] = alert_message
        self._publish(EventType.TRADE_EXITED, event_data)
        logger.info("force_exit: %s trade %d %s (%.2f%%) — %s",
                     self.tracker_type, trade_id, status, pnl_pct, reason)

    def _publish(self, event_type: EventType, data: dict) -> None:
        """Convenience wrapper to publish events with tracker_type injected."""
        data.setdefault("tracker_type", self.tracker_type)
        self.bus.publish(event_type, data)
