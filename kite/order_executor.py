"""Unified order execution service for all trading strategies.

Sits between strategy lifecycle events and the Kite broker API.
Composition-based: injected into strategies, not inherited.

When LIVE_TRADING_ENABLED=false (default), all methods are no-ops
that return success with is_paper=True. Existing paper-trade behavior
is completely preserved.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Dict, Optional

from config import LiveTradingConfig, MarketConfig
from core.logger import get_logger

log = get_logger("order_executor")

_live_cfg = LiveTradingConfig()


@dataclass
class OrderResult:
    """Result of an order execution attempt."""
    success: bool
    order_id: str = ""
    gtt_trigger_id: int = 0
    error: str = ""
    is_paper: bool = True
    actual_fill_price: float = 0.0
    corrected_sl: float = 0.0
    corrected_target: float = 0.0
    gtt_already_triggered: bool = False


class OrderExecutor:
    """Unified order execution service for all strategies.

    Thread-safe for WebSocket callbacks. When live trading is disabled,
    all methods are no-ops returning is_paper=True.
    """

    def __init__(self, instrument_map=None):
        self._enabled = _live_cfg.ENABLED
        self._lots = _live_cfg.LOTS
        self._quantity = _live_cfg.quantity
        self._product = _live_cfg.PRODUCT
        # Per-strategy override: comma-separated list of tracker_types allowed to trade live
        # Empty string = all strategies live when ENABLED=true
        _strats = _live_cfg.STRATEGIES.strip()
        self._live_strategies: set = set(s.strip() for s in _strats.split(",") if s.strip()) if _strats else set()
        self._instrument_map = instrument_map
        self._active_gtts: Dict[int, int] = {}      # trade_id -> gtt_trigger_id
        self._active_orders: Dict[int, str] = {}     # trade_id -> order_id
        self._trade_symbols: Dict[int, str] = {}     # trade_id -> tradingsymbol
        self._lock = threading.Lock()

        if self._enabled:
            if self._live_strategies:
                log.info("OrderExecutor LIVE mode (selective)",
                         strategies=self._live_strategies,
                         lots=self._lots, qty=self._quantity)
            else:
                log.info("OrderExecutor LIVE mode (all strategies)",
                         lots=self._lots, qty=self._quantity, product=self._product)
        else:
            log.info("OrderExecutor paper mode (LIVE_TRADING_ENABLED=false)")

    def is_strategy_live(self, tracker_type: str) -> bool:
        """Check if a specific strategy is enabled for live trading."""
        if not self._enabled:
            return False
        if not self._live_strategies:
            return True  # empty = all strategies live
        return tracker_type in self._live_strategies

    def set_instrument_map(self, instrument_map) -> None:
        """Set instrument map (called after Kite auth in scheduler.start)."""
        self._instrument_map = instrument_map

    @property
    def is_live(self) -> bool:
        """True if live trading is enabled and Kite is authenticated."""
        if not self._enabled:
            return False
        from kite.broker import is_authenticated
        return is_authenticated()

    # ------------------------------------------------------------------
    # Core lifecycle methods
    # ------------------------------------------------------------------

    def place_entry(
        self,
        trade_id: int,
        strike: int,
        option_type: str,
        entry_premium: float,
        sl_premium: float,
        target_premium: float,
        order_type: str = "MARKET",
        tracker_type: str = "",
        table_name: str = "",
    ) -> OrderResult:
        """Place entry order + GTT OCO for SL/target.

        Called by strategy.create_trade() AFTER DB insert succeeds.
        If not enabled (globally or for this strategy), returns paper result.
        """
        if not self.is_strategy_live(tracker_type):
            return OrderResult(success=True, is_paper=True)

        from kite.broker import is_authenticated, place_order, place_gtt_oco

        if not is_authenticated():
            log.error("Live trading enabled but Kite not authenticated",
                      trade_id=trade_id)
            return OrderResult(success=False, error="Not authenticated", is_paper=False)

        # Round all prices to 0.05 ticks
        entry = self.round_to_tick(entry_premium, "nearest")
        sl = self.round_to_tick(sl_premium, "down")
        target = self.round_to_tick(target_premium, "up")

        # Resolve trading symbol
        symbol = self._resolve_symbol(strike, option_type)
        if not symbol:
            log.error("Cannot resolve trading symbol",
                      trade_id=trade_id, strike=strike, type=option_type)
            return OrderResult(success=False, error="Symbol not found", is_paper=False)

        # Step 1: Place entry order
        log.info("Placing entry order", trade_id=trade_id, symbol=symbol,
                 order_type=order_type, entry=entry, qty=self._quantity)

        entry_result = place_order(
            trading_symbol=symbol,
            transaction_type="BUY",
            quantity=self._quantity,
            price=entry if order_type == "LIMIT" else 0,
            order_type=order_type,
            product=self._product,
        )

        if entry_result.get("status") != "success":
            log.error("Entry order failed, continuing as paper trade",
                      trade_id=trade_id, result=entry_result)
            return OrderResult(
                success=False,
                error=entry_result.get("message", "Order failed"),
                is_paper=False,
            )

        order_id = entry_result["data"]["order_id"]

        # Step 2: Query actual fill price for MARKET orders
        actual_fill = entry
        if order_type == "MARKET":
            actual_fill = self._query_fill_price(order_id, expected=entry)

        # Step 3: Recompute SL/target preserving percentage ratios
        entry_for_gtt = entry
        if actual_fill != entry and actual_fill > 0 and entry > 0:
            sl_pct = (entry - sl) / entry
            tgt_pct = (target - entry) / entry
            sl = self.round_to_tick(actual_fill * (1 - sl_pct), "down")
            target = self.round_to_tick(actual_fill * (1 + tgt_pct), "up")
            entry_for_gtt = actual_fill
            log.info("Fill price correction", trade_id=trade_id,
                     expected=entry, actual=actual_fill, new_sl=sl, new_target=target)

        # Step 4: Place GTT OCO for SL + target
        log.info("Placing GTT OCO", trade_id=trade_id, symbol=symbol,
                 sl=sl, target=target)

        gtt_result = place_gtt_oco(
            trading_symbol=symbol,
            entry_price=entry_for_gtt,
            sl_price=sl,
            target_price=target,
            quantity=self._quantity,
            product=self._product,
        )

        gtt_trigger_id = 0
        if gtt_result.get("status") == "success":
            gtt_trigger_id = gtt_result["data"]["trigger_id"]
        else:
            log.error("GTT placement failed — entry order is LIVE, manual monitoring needed",
                      trade_id=trade_id, order_id=order_id, result=gtt_result)

        # Step 5: Store in internal mapping
        with self._lock:
            self._active_orders[trade_id] = order_id
            self._trade_symbols[trade_id] = symbol
            if gtt_trigger_id:
                self._active_gtts[trade_id] = gtt_trigger_id

        # Step 6: Update trade DB with order tracking + corrected prices
        self._update_trade_order_info(
            trade_id, order_id, gtt_trigger_id,
            actual_fill_price=actual_fill,
            corrected_sl=sl if actual_fill != entry else 0,
            corrected_target=target if actual_fill != entry else 0,
            table_name=table_name,
        )

        log.info("Entry + GTT placed",
                 trade_id=trade_id, order_id=order_id,
                 gtt_trigger_id=gtt_trigger_id, fill=actual_fill)

        return OrderResult(
            success=True,
            order_id=order_id,
            gtt_trigger_id=gtt_trigger_id,
            is_paper=False,
            actual_fill_price=actual_fill,
            corrected_sl=sl if actual_fill != entry else 0,
            corrected_target=target if actual_fill != entry else 0,
        )

    def modify_sl(
        self,
        trade_id: int,
        new_sl_premium: float,
        current_premium: float,
        target_premium: float,
    ) -> OrderResult:
        """Modify GTT to update SL (trailing stop).

        Called by strategy.check_and_update() when trail stage advances.
        """
        if not self._enabled:
            return OrderResult(success=True, is_paper=True)

        with self._lock:
            gtt_id = self._active_gtts.get(trade_id)
            symbol = self._trade_symbols.get(trade_id)

        if not gtt_id or not symbol:
            log.debug("No active GTT to modify", trade_id=trade_id)
            return OrderResult(success=True, is_paper=True)

        from kite.broker import modify_gtt

        new_sl = self.round_to_tick(new_sl_premium, "down")
        target = self.round_to_tick(target_premium, "up")

        log.info("Modifying GTT SL", trade_id=trade_id,
                 gtt_id=gtt_id, new_sl=new_sl)

        result = modify_gtt(
            trigger_id=gtt_id,
            trading_symbol=symbol,
            current_price=current_premium,
            new_sl_price=new_sl,
            target_price=target,
            quantity=self._quantity,
            product=self._product,
        )

        if result.get("status") == "success":
            return OrderResult(success=True, gtt_trigger_id=gtt_id, is_paper=False)

        # Check if GTT already triggered (position already exited by Kite)
        error_msg = result.get("message", "")
        if "already triggered" in error_msg.lower():
            log.info("GTT already triggered during modify — position already exited",
                     trade_id=trade_id, gtt_id=gtt_id)
            # Clean up internal state
            with self._lock:
                self._active_gtts.pop(trade_id, None)
                self._active_orders.pop(trade_id, None)
                self._trade_symbols.pop(trade_id, None)
            return OrderResult(success=False, error=error_msg,
                               is_paper=False, gtt_already_triggered=True)

        log.error("GTT modify failed", trade_id=trade_id,
                  gtt_id=gtt_id, result=result)
        return OrderResult(success=False, error=error_msg,
                           is_paper=False)

    def cancel_exit_orders(self, trade_id: int) -> OrderResult:
        """Cancel GTT OCO for a trade (cleanup on exit).

        Checks GTT status on Kite first:
        - If 'triggered': GTT already sold the position — safe to clean up.
        - If 'active': GTT hasn't fired yet — we must place a market sell
          BEFORE cancelling, otherwise the position is left unguarded.
        - If already gone (error): treat as no-op.

        Idempotent: safe to call even if no GTT exists.
        """
        if not self._enabled:
            return OrderResult(success=True, is_paper=True)

        with self._lock:
            gtt_id = self._active_gtts.pop(trade_id, None)
            order_id = self._active_orders.pop(trade_id, None)
            symbol = self._trade_symbols.pop(trade_id, None)

        if not gtt_id:
            return OrderResult(success=True, is_paper=False)

        from kite.broker import get_gtt_status, delete_gtt, place_order, is_authenticated

        # Step 1: Check GTT status on Kite
        gtt_check = get_gtt_status(gtt_id)
        gtt_status = gtt_check.get("gtt_status", "unknown")

        log.info("GTT status check", trade_id=trade_id,
                 gtt_id=gtt_id, gtt_status=gtt_status)

        if gtt_status == "triggered":
            # GTT already fired and sold — position is closed, just clean up
            log.info("GTT already triggered, position sold",
                     trade_id=trade_id, gtt_id=gtt_id)
            return OrderResult(success=True, gtt_trigger_id=gtt_id, is_paper=False)

        if gtt_status == "active":
            # GTT hasn't fired yet — cancel it, then sell at market
            log.info("GTT still active, cancelling and placing market sell",
                     trade_id=trade_id, gtt_id=gtt_id)

            delete_result = delete_gtt(gtt_id)
            if delete_result.get("status") != "success":
                log.error("GTT cancel failed while active",
                          trade_id=trade_id, gtt_id=gtt_id, result=delete_result)

            # Place market sell to close the position
            if symbol and is_authenticated():
                sell_result = place_order(
                    trading_symbol=symbol,
                    transaction_type="SELL",
                    quantity=self._quantity,
                    order_type="MARKET",
                    product=self._product,
                )
                if sell_result.get("status") == "success":
                    log.info("Market sell placed after GTT cancel",
                             trade_id=trade_id,
                             order_id=sell_result["data"]["order_id"])
                else:
                    log.error("Market sell failed after GTT cancel — POSITION MAY BE OPEN",
                              trade_id=trade_id, result=sell_result)
            else:
                log.error("Cannot place market sell — no symbol or not authenticated",
                          trade_id=trade_id)

            return OrderResult(success=True, gtt_trigger_id=gtt_id, is_paper=False)

        # GTT in other state (cancelled, expired, disabled, error querying)
        # Try to delete anyway, ignore errors
        log.info("GTT in non-active state, attempting cleanup",
                 trade_id=trade_id, gtt_id=gtt_id, gtt_status=gtt_status)
        delete_gtt(gtt_id)
        return OrderResult(success=True, gtt_trigger_id=gtt_id, is_paper=False)

    def place_exit(
        self,
        trade_id: int,
        strike: int,
        option_type: str,
        tracker_type: str = "",
    ) -> OrderResult:
        """Place market sell order for time-based exits (EOD, MAX_TIME, TIME_FLAT).

        Cancels GTT first, then places market sell.
        """
        if not self.is_strategy_live(tracker_type):
            return OrderResult(success=True, is_paper=True)

        # Cancel GTT first
        self.cancel_exit_orders(trade_id)

        from kite.broker import is_authenticated, place_order

        if not is_authenticated():
            log.error("Cannot place exit — Kite not authenticated",
                      trade_id=trade_id)
            return OrderResult(success=False, error="Not authenticated", is_paper=False)

        symbol = self._resolve_symbol(strike, option_type)
        if not symbol:
            log.error("Cannot resolve symbol for exit",
                      trade_id=trade_id, strike=strike, type=option_type)
            return OrderResult(success=False, error="Symbol not found", is_paper=False)

        log.info("Placing market exit order", trade_id=trade_id, symbol=symbol)

        result = place_order(
            trading_symbol=symbol,
            transaction_type="SELL",
            quantity=self._quantity,
            order_type="MARKET",
            product=self._product,
        )

        if result.get("status") == "success":
            order_id = result["data"]["order_id"]
            log.info("Exit order placed", trade_id=trade_id, order_id=order_id)
            return OrderResult(success=True, order_id=order_id, is_paper=False)

        log.error("Exit order failed", trade_id=trade_id, result=result)
        return OrderResult(success=False, error=result.get("message", ""),
                           is_paper=False)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def round_to_tick(price: float, direction: str = "nearest") -> float:
        """Round to NSE option tick size (0.05).

        direction: 'up' for targets, 'down' for SL, 'nearest' for entry.
        """
        if direction == "up":
            return round(math.ceil(price * 20) / 20, 2)
        elif direction == "down":
            return round(math.floor(price * 20) / 20, 2)
        return round(round(price * 20) / 20, 2)

    def get_order_info(self, trade_id: int) -> dict:
        """Return stored order_id and gtt_trigger_id for a trade."""
        with self._lock:
            return {
                "order_id": self._active_orders.get(trade_id, ""),
                "gtt_trigger_id": self._active_gtts.get(trade_id, 0),
                "symbol": self._trade_symbols.get(trade_id, ""),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_symbol(self, strike: int, option_type: str) -> Optional[str]:
        """Resolve Kite tradingsymbol from strike + option_type."""
        if not self._instrument_map:
            return None
        try:
            expiry = self._instrument_map.get_current_expiry()
            if not expiry:
                return None
            inst = self._instrument_map.get_option_instrument(
                strike, option_type, expiry)
            if inst:
                return inst.get("tradingsymbol") or inst.get("trading_symbol")
        except Exception as e:
            log.error("Symbol resolution failed", error=str(e),
                      strike=strike, type=option_type)
        return None

    def _query_fill_price(self, order_id: str, expected: float,
                          max_retries: int = 3, delay: float = 0.5) -> float:
        """Query Kite API for actual fill price of a MARKET order.

        Retries up to max_retries times since MARKET orders fill
        near-instantly but status propagation may have slight delay.
        Returns actual fill price, or expected price on failure.
        """
        import time as time_mod
        from kite.broker import get_order_status

        for attempt in range(max_retries):
            result = get_order_status(order_id)
            if result.get("status") == "success":
                avg_price = result.get("average_price", 0)
                order_status = result.get("order_status", "")
                if order_status == "COMPLETE" and avg_price > 0:
                    return self.round_to_tick(avg_price, "nearest")
                if order_status in ("REJECTED", "CANCELLED"):
                    log.warning("Order rejected/cancelled", order_id=order_id,
                                status=order_status)
                    return expected
            if attempt < max_retries - 1:
                time_mod.sleep(delay)

        log.warning("Fill price query exhausted retries, using expected",
                    order_id=order_id, expected=expected)
        return expected

    def _update_trade_order_info(self, trade_id: int, order_id: str,
                                  gtt_trigger_id: int,
                                  actual_fill_price: float = 0.0,
                                  corrected_sl: float = 0.0,
                                  corrected_target: float = 0.0,
                                  table_name: str = "") -> None:
        """Update trade DB row with order tracking IDs and fill price."""
        try:
            from db.trade_repo import TradeRepository
            repo = TradeRepository()
            updates = dict(order_id=order_id, gtt_trigger_id=gtt_trigger_id)
            if actual_fill_price > 0:
                updates["actual_fill_price"] = actual_fill_price
                updates["entry_premium"] = actual_fill_price
            if corrected_sl > 0:
                updates["sl_premium"] = corrected_sl
            if corrected_target > 0:
                updates["target_premium"] = corrected_target

            if table_name:
                repo.update_trade(table_name, trade_id, **updates)
            else:
                # Fallback: try rr_trades
                repo.update_trade("rr_trades", trade_id, **updates)
        except Exception as e:
            log.error("Failed to update trade order info",
                      trade_id=trade_id, error=str(e))

    def _migrate_schema(self) -> None:
        """Add order tracking columns to existing trade tables (idempotent)."""
        try:
            from db.connection import get_connection
            with get_connection() as conn:
                for table in ("rr_trades",):
                    for col, col_type in [("order_id", "TEXT"),
                                           ("gtt_trigger_id", "INTEGER"),
                                           ("actual_fill_price", "REAL"),
                                           ("is_paper", "INTEGER DEFAULT 0")]:
                        try:
                            conn.execute(
                                f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                        except Exception:
                            pass  # Column already exists
        except Exception as e:
            log.error("Schema migration failed", error=str(e))
