"""Abstract base class for all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class TradingStrategy(ABC):
    """
    Base class for all trading strategies.

    Each strategy implements its own entry/exit logic as pure methods
    that receive market data and return decisions. Database operations
    are delegated to the TradeService.
    """

    @abstractmethod
    def should_enter(self, analysis: dict, strikes_data: dict, **kwargs) -> dict | None:
        """
        Evaluate whether a new trade should be opened.

        Args:
            analysis: Current OI analysis result.
            strikes_data: Current strike-level data (premiums, OI, IV).

        Returns:
            Trade params dict if entry conditions met, None otherwise.
        """

    @abstractmethod
    def check_exit(
        self,
        trade: dict,
        current_premium: float,
        now: datetime,
    ) -> dict | None:
        """
        Evaluate whether an active trade should be closed or updated.

        Args:
            trade: The active trade record (as dict).
            current_premium: Current LTP for the trade's strike/option_type.
            now: Current datetime.

        Returns:
            Dict with exit info (status, exit_premium, reason, pnl) or None to stay.
        """

    @abstractmethod
    def should_force_close(self, trade: dict, now: datetime) -> bool:
        """Return True if the trade must be force-closed (EOD / time rules)."""

    def compute_pnl_pct(self, entry: float, exit_prem: float, is_selling: bool = False) -> float:
        """Compute P&L percentage. Selling inverts the direction."""
        if entry <= 0:
            return 0.0
        if is_selling:
            return ((entry - exit_prem) / entry) * 100
        return ((exit_prem - entry) / entry) * 100
