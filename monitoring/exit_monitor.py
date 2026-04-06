"""ExitMonitor — tick-level SL/target/soft-SL detection for active trades.

This is one of the four TickHub consumers that replace `premium_monitor.py`.
It preserves the exact exit-detection semantics that production has relied on:

- Hard SL (buy): `current <= sl_premium` → LOST
- Target  (buy): `current >= target_premium` → WON
- Soft SL (buy): `current <= soft_sl` → flag only; do not fire exit callback
- Hard SL (sell): `current >= sl_premium` → LOST (premium rose = loss)
- Target  (sell): `current <= target_premium` → WON (premium fell = profit)

Soft SL is Claude's trailing stop. It is intentionally NOT auto-enforced —
the strategy's `_call_trade_monitor` reads the breach state at the next
3-min cycle and decides whether to exit or widen the SL.

The interface is kept identical to premium_monitor so that RR strategy
only needs a kwarg rename. ActiveTrade dataclass moves here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

from core.logger import get_logger
from monitoring.tick_hub import TickConsumer

log = get_logger("exit_monitor")


@dataclass
class ActiveTrade:
    """Represents an active trade being monitored via WebSocket ticks."""
    trade_id: int
    tracker_type: str
    strike: int
    option_type: str  # "CE" or "PE"
    instrument_token: int
    entry_premium: float
    sl_premium: float       # hard SL (disaster protection)
    target_premium: float
    is_selling: bool = False
    soft_sl: float = 0.0               # Claude-managed trailing SL
    soft_sl_breached: bool = False     # set when premium drops below soft_sl
    soft_sl_breach_premium: float = 0.0


class ExitMonitor(TickConsumer):
    """Detects SL/target/soft-SL exits per tick. Mirrors premium_monitor."""

    def __init__(self, tick_hub=None, shadow_mode: bool = False):
        self._tick_hub = tick_hub
        self._shadow_mode = shadow_mode
        self._exit_callback: Optional[Callable] = None

        self._token_to_trades: Dict[int, List[ActiveTrade]] = {}
        self._all_trades: Dict[int, ActiveTrade] = {}   # trade_id -> ActiveTrade

        # Set externally by scheduler after instrument map is refreshed.
        self._instrument_map = None

    def set_tick_hub(self, tick_hub) -> None:
        self._tick_hub = tick_hub

    def set_exit_callback(self, callback: Callable) -> None:
        """Callback signature: callback(dict). See _check_exit for payload."""
        self._exit_callback = callback

    # ------------------------------------------------------------------
    # Trade registration
    # ------------------------------------------------------------------

    def is_monitoring(self, trade_id: int) -> bool:
        return trade_id in self._all_trades

    def register_trade(self, trade: ActiveTrade) -> None:
        """Start monitoring a trade. Subscribes its token via TickHub."""
        token = trade.instrument_token
        self._all_trades[trade.trade_id] = trade
        self._token_to_trades.setdefault(token, []).append(trade)

        if self._tick_hub is not None:
            try:
                self._tick_hub.request_subscription([token])
            except Exception as e:
                log.error("TickHub.request_subscription failed",
                          token=token, error=str(e))

        log.info("Trade registered for exit monitoring",
                 trade_id=trade.trade_id,
                 tracker=trade.tracker_type,
                 strike=trade.strike,
                 type=trade.option_type,
                 token=token,
                 shadow=self._shadow_mode)

    def unregister_trade(self, trade_id: int) -> None:
        """Stop monitoring a trade. Releases its token subscription."""
        trade = self._all_trades.pop(trade_id, None)
        if not trade:
            return

        token = trade.instrument_token
        remaining = [t for t in self._token_to_trades.get(token, [])
                     if t.trade_id != trade_id]
        if remaining:
            self._token_to_trades[token] = remaining
        else:
            self._token_to_trades.pop(token, None)
            if self._tick_hub is not None:
                try:
                    self._tick_hub.release_subscription([token])
                except Exception as e:
                    log.error("TickHub.release_subscription failed",
                              token=token, error=str(e))

        log.info("Trade unregistered from exit monitoring", trade_id=trade_id)

    def update_trade_sl(self, trade_id: int, new_sl: float) -> None:
        """Update the hard SL for a monitored trade (rare; used by trailing)."""
        trade = self._all_trades.get(trade_id)
        if trade:
            trade.sl_premium = new_sl
            log.info("Hard SL updated", trade_id=trade_id, new_sl=new_sl)

    def update_soft_sl(self, trade_id: int, new_soft_sl: float) -> None:
        """Update Claude's soft SL (internal trailing, not on exchange)."""
        trade = self._all_trades.get(trade_id)
        if trade:
            trade.soft_sl = new_soft_sl
            trade.soft_sl_breached = False
            trade.soft_sl_breach_premium = 0.0
            log.info("Soft SL updated", trade_id=trade_id, soft_sl=new_soft_sl)

    def get_soft_sl_status(self, trade_id: int) -> dict:
        trade = self._all_trades.get(trade_id)
        if not trade:
            return {}
        return {
            "soft_sl": trade.soft_sl,
            "soft_sl_breached": trade.soft_sl_breached,
            "soft_sl_breach_premium": trade.soft_sl_breach_premium,
        }

    # ------------------------------------------------------------------
    # TickConsumer interface
    # ------------------------------------------------------------------

    def on_tick(self, token: int, tick: dict) -> None:
        trades = self._token_to_trades.get(token)
        if not trades:
            return
        ltp = tick.get("last_price")
        if ltp is None:
            return

        for trade in list(trades):
            result = self._check_exit(trade, ltp)
            if result is None:
                continue
            if self._shadow_mode:
                log.info("SHADOW: Would exit trade",
                         trade_id=trade.trade_id,
                         tracker=trade.tracker_type,
                         action=result["action"],
                         premium=f"{ltp:.2f}",
                         entry=f"{trade.entry_premium:.2f}",
                         reason=result["reason"])
            else:
                log.info("EXIT DETECTED",
                         trade_id=trade.trade_id,
                         tracker=trade.tracker_type,
                         action=result["action"],
                         premium=f"{ltp:.2f}",
                         reason=result["reason"])
                if self._exit_callback is not None:
                    try:
                        self._exit_callback(result)
                    except Exception as e:
                        log.error("exit_callback failed",
                                  trade_id=trade.trade_id, error=str(e))

    def get_required_tokens(self) -> Set[int]:
        return set(self._token_to_trades.keys())

    # ------------------------------------------------------------------
    # Exit detection — preserved exactly from premium_monitor
    # ------------------------------------------------------------------

    def _check_exit(self, trade: ActiveTrade, current_premium: float) -> Optional[dict]:
        """Check if current premium triggers SL or target.

        BUYING trades:
            SL:     current <= sl_premium        → LOST
            Target: current >= target_premium    → WON
            Soft:   current <= soft_sl           → flag only, no exit

        SELLING trades (inverted):
            SL:     current >= sl_premium        → LOST
            Target: current <= target_premium    → WON
        """
        if trade.is_selling:
            if current_premium >= trade.sl_premium:
                pnl = ((trade.entry_premium - current_premium) / trade.entry_premium) * 100
                return {
                    "trade_id": trade.trade_id,
                    "tracker_type": trade.tracker_type,
                    "action": "LOST",
                    "exit_premium": current_premium,
                    "pnl_pct": pnl,
                    "reason": f"SL hit: premium rose to {current_premium:.2f} (SL: {trade.sl_premium:.2f})",
                }
            if current_premium <= trade.target_premium:
                pnl = ((trade.entry_premium - current_premium) / trade.entry_premium) * 100
                return {
                    "trade_id": trade.trade_id,
                    "tracker_type": trade.tracker_type,
                    "action": "WON",
                    "exit_premium": current_premium,
                    "pnl_pct": pnl,
                    "reason": f"Target hit: premium fell to {current_premium:.2f} (T: {trade.target_premium:.2f})",
                }
            return None

        # Buying
        if current_premium <= trade.sl_premium:
            pnl = ((current_premium - trade.entry_premium) / trade.entry_premium) * 100
            return {
                "trade_id": trade.trade_id,
                "tracker_type": trade.tracker_type,
                "action": "LOST",
                "exit_premium": current_premium,
                "pnl_pct": pnl,
                "reason": f"SL hit: premium fell to {current_premium:.2f} (SL: {trade.sl_premium:.2f})",
            }

        # Soft SL: flag only, do NOT return an exit
        if trade.soft_sl > 0 and current_premium <= trade.soft_sl:
            if not trade.soft_sl_breached:
                trade.soft_sl_breached = True
                trade.soft_sl_breach_premium = current_premium
            else:
                trade.soft_sl_breach_premium = min(
                    trade.soft_sl_breach_premium, current_premium)

        if current_premium >= trade.target_premium:
            pnl = ((current_premium - trade.entry_premium) / trade.entry_premium) * 100
            return {
                "trade_id": trade.trade_id,
                "tracker_type": trade.tracker_type,
                "action": "WON",
                "exit_premium": current_premium,
                "pnl_pct": pnl,
                "reason": f"Target hit: premium rose to {current_premium:.2f} (T: {trade.target_premium:.2f})",
            }

        return None

    # ------------------------------------------------------------------
    # Startup scan
    # ------------------------------------------------------------------

    def scan_existing_trades(self, strategies: dict) -> None:
        """Pick up ACTIVE trades from all trackers at startup."""
        if not self._instrument_map:
            log.warning("Cannot scan trades: no instrument map")
            return

        expiry = self._instrument_map.get_current_expiry()
        if not expiry:
            log.warning("Cannot scan trades: no current expiry")
            return

        count = 0
        for name, strategy in (strategies or {}).items():
            try:
                setup = strategy.get_active()
                if not setup:
                    continue
                if setup.get("status") not in ("ACTIVE",):
                    continue
                trade = self._db_trade_to_active(
                    setup, strategy.tracker_type, expiry,
                    is_selling=strategy.is_selling,
                )
                if trade:
                    self.register_trade(trade)
                    count += 1
            except Exception as e:
                log.error("Error scanning trades",
                          strategy=name, error=str(e))
        log.info("Scanned existing trades", found=count)

    def _db_trade_to_active(self, setup: dict, tracker_type: str,
                            expiry: str, is_selling: bool) -> Optional[ActiveTrade]:
        """Convert a DB trade row to an ActiveTrade."""
        strike = setup.get("strike")
        option_type = setup.get("option_type")
        if not strike or not option_type:
            return None

        inst = self._instrument_map.get_option_instrument(strike, option_type, expiry)
        if not inst:
            log.warning("No instrument found for trade",
                        strike=strike, type=option_type, expiry=expiry)
            return None

        sl_premium = setup.get("sl_premium", 0)
        if is_selling:
            target_premium = setup.get("target2_premium") or setup.get("target_premium", 0)
        else:
            target_premium = setup.get("target1_premium") or setup.get("target_premium", 0)

        return ActiveTrade(
            trade_id=setup["id"],
            tracker_type=tracker_type,
            strike=strike,
            option_type=option_type,
            instrument_token=inst["instrument_token"],
            entry_premium=setup.get("entry_premium", 0),
            sl_premium=sl_premium,
            target_premium=target_premium,
            is_selling=is_selling,
            soft_sl=setup.get("soft_sl_premium", 0) or 0,
        )

    def get_status(self) -> dict:
        return {
            "shadow_mode": self._shadow_mode,
            "active_trades": len(self._all_trades),
            "tokens_subscribed": len(self._token_to_trades),
            "trades": [
                {
                    "trade_id": t.trade_id,
                    "tracker": t.tracker_type,
                    "strike": t.strike,
                    "type": t.option_type,
                    "entry": t.entry_premium,
                    "sl": t.sl_premium,
                    "target": t.target_premium,
                    "is_selling": t.is_selling,
                }
                for t in self._all_trades.values()
            ],
        }
