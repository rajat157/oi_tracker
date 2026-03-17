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
        self._instrument_map = instrument_map
        self._active_gtts: Dict[int, int] = {}      # trade_id -> gtt_trigger_id
        self._active_orders: Dict[int, str] = {}     # trade_id -> order_id
        self._trade_symbols: Dict[int, str] = {}     # trade_id -> tradingsymbol
        self._lock = threading.Lock()

        if self._enabled:
            log.info("OrderExecutor LIVE mode",
                     lots=self._lots, qty=self._quantity, product=self._product)
        else:
            log.info("OrderExecutor paper mode (LIVE_TRADING_ENABLED=false)")

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
    ) -> OrderResult:
        """Place entry order + GTT OCO for SL/target.

        Called by strategy.create_trade() AFTER DB insert succeeds.
        If not enabled, returns paper result. If order fails, logs error
        but does NOT roll back the DB trade.
        """
        if not self._enabled:
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

        # Step 2: Place GTT OCO for SL + target
        log.info("Placing GTT OCO", trade_id=trade_id, symbol=symbol,
                 sl=sl, target=target)

        gtt_result = place_gtt_oco(
            trading_symbol=symbol,
            entry_price=entry,
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

        # Step 3: Store in internal mapping
        with self._lock:
            self._active_orders[trade_id] = order_id
            self._trade_symbols[trade_id] = symbol
            if gtt_trigger_id:
                self._active_gtts[trade_id] = gtt_trigger_id

        # Step 4: Update trade DB with order tracking
        self._update_trade_order_info(trade_id, order_id, gtt_trigger_id)

        log.info("Entry + GTT placed",
                 trade_id=trade_id, order_id=order_id,
                 gtt_trigger_id=gtt_trigger_id)

        return OrderResult(
            success=True,
            order_id=order_id,
            gtt_trigger_id=gtt_trigger_id,
            is_paper=False,
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

        log.error("GTT modify failed", trade_id=trade_id,
                  gtt_id=gtt_id, result=result)
        return OrderResult(success=False, error=result.get("message", ""),
                           is_paper=False)

    def cancel_exit_orders(self, trade_id: int) -> OrderResult:
        """Cancel GTT OCO for a trade (cleanup on exit).

        Idempotent: safe to call even if no GTT exists.
        """
        if not self._enabled:
            return OrderResult(success=True, is_paper=True)

        with self._lock:
            gtt_id = self._active_gtts.pop(trade_id, None)
            self._active_orders.pop(trade_id, None)
            self._trade_symbols.pop(trade_id, None)

        if not gtt_id:
            return OrderResult(success=True, is_paper=False)

        from kite.broker import delete_gtt

        log.info("Cancelling GTT", trade_id=trade_id, gtt_id=gtt_id)

        result = delete_gtt(gtt_id)
        if result.get("status") == "success":
            return OrderResult(success=True, gtt_trigger_id=gtt_id, is_paper=False)

        log.error("GTT cancel failed", trade_id=trade_id,
                  gtt_id=gtt_id, result=result)
        return OrderResult(success=False, error=result.get("message", ""),
                           is_paper=False)

    def place_exit(
        self,
        trade_id: int,
        strike: int,
        option_type: str,
    ) -> OrderResult:
        """Place market sell order for time-based exits (EOD, MAX_TIME, TIME_FLAT).

        Cancels GTT first, then places market sell.
        """
        if not self._enabled:
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

    def _update_trade_order_info(self, trade_id: int, order_id: str,
                                  gtt_trigger_id: int) -> None:
        """Update trade DB row with order tracking IDs."""
        try:
            from db.trade_repo import TradeRepository
            repo = TradeRepository()
            # Try scalp_trades first, then rr_trades
            for table in ("scalp_trades", "rr_trades"):
                try:
                    repo.update_trade(table, trade_id,
                                      order_id=order_id,
                                      gtt_trigger_id=gtt_trigger_id)
                    return
                except Exception:
                    continue
        except Exception as e:
            log.error("Failed to update trade order info",
                      trade_id=trade_id, error=str(e))

    def _migrate_schema(self) -> None:
        """Add order tracking columns to existing trade tables (idempotent)."""
        try:
            from db.connection import get_connection
            with get_connection() as conn:
                for table in ("scalp_trades", "rr_trades"):
                    for col, col_type in [("order_id", "TEXT"),
                                           ("gtt_trigger_id", "INTEGER")]:
                        try:
                            conn.execute(
                                f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                        except Exception:
                            pass  # Column already exists
        except Exception as e:
            log.error("Schema migration failed", error=str(e))
