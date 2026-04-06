"""LivePnlBroadcaster — tick consumer that caches LTP for active trades.

Replaces the live P&L responsibility of `premium_monitor.py`. The scheduler
still owns the 5s emit cadence; this consumer just provides the payload
via `get_pnl_payload()` whenever the scheduler asks.

The broadcaster caches LTP per token but does not own any subscriptions —
ExitMonitor already keeps the active trade tokens subscribed, so this
consumer is passive (`get_required_tokens()` returns an empty set).
"""

from __future__ import annotations

from typing import Dict, Set

from core.logger import get_logger
from monitoring.tick_hub import TickConsumer

log = get_logger("live_pnl_broadcaster")


class LivePnlBroadcaster(TickConsumer):
    """Caches the latest LTP per token; computes P&L for scheduler broadcast."""

    def __init__(self, exit_monitor=None):
        self._exit_monitor = exit_monitor
        self._latest_ltp: Dict[int, float] = {}

    def set_exit_monitor(self, exit_monitor) -> None:
        self._exit_monitor = exit_monitor

    # ------------------------------------------------------------------
    # TickConsumer interface
    # ------------------------------------------------------------------

    def on_tick(self, token: int, tick: dict) -> None:
        ltp = tick.get("last_price")
        if ltp is not None:
            self._latest_ltp[token] = ltp

    def get_required_tokens(self) -> Set[int]:
        # Piggybacks on ExitMonitor's subscriptions; no independent subs needed.
        return set()

    # ------------------------------------------------------------------
    # P&L payload for the 5s SocketIO emit
    # ------------------------------------------------------------------

    def get_pnl_payload(self) -> dict:
        """Build the dict the scheduler emits as the `pnl_update` event.

        Shape (unchanged from premium_monitor):
            {
              "<tracker_type>": {
                  "current_premium": float,
                  "pnl_pct": float,
                  "pnl_points": float,
                  "strike": int,
                  "option_type": "CE" | "PE",
                  "entry_premium": float,
              },
              ...
            }
        """
        if self._exit_monitor is None:
            return {}
        trades = getattr(self._exit_monitor, "_all_trades", {}) or {}
        if not trades or not self._latest_ltp:
            return {}

        result: dict = {}
        for trade in trades.values():
            ltp = self._latest_ltp.get(trade.instrument_token)
            if ltp is None:
                continue

            if trade.is_selling:
                pnl_pct = ((trade.entry_premium - ltp) / trade.entry_premium) * 100
                pnl_points = trade.entry_premium - ltp
            else:
                pnl_pct = ((ltp - trade.entry_premium) / trade.entry_premium) * 100
                pnl_points = ltp - trade.entry_premium

            result[trade.tracker_type] = {
                "current_premium": round(ltp, 2),
                "pnl_pct": round(pnl_pct, 2),
                "pnl_points": round(pnl_points, 2),
                "strike": trade.strike,
                "option_type": trade.option_type,
                "entry_premium": trade.entry_premium,
            }
        return result

    def get_ltp(self, token: int) -> float:
        """Return the latest cached LTP for a token, or 0.0 if unknown."""
        return self._latest_ltp.get(token, 0.0)
