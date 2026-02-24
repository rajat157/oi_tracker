"""Real-time WebSocket premium monitoring for active trades."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Callable

from app.services.logging_service import get_logger

log = get_logger("premium_mon")


@dataclass
class ActiveTrade:
    """Trade registered for real-time monitoring."""

    trade_id: int
    strategy: str  # iron_pulse, selling, dessert, momentum
    strike: int
    option_type: str
    instrument_token: int
    entry_premium: float
    sl_premium: float
    target_premium: float
    is_selling: bool = False


class PremiumMonitorService:
    """Monitors premiums via KiteTicker WebSocket in a background thread."""

    def __init__(self, shadow_mode: bool = True) -> None:
        self._shadow_mode = shadow_mode
        self._exit_callback: Callable | None = None
        self._token_to_trades: dict[int, list[ActiveTrade]] = {}
        self._all_trades: dict[int, ActiveTrade] = {}
        self._ticker = None
        self._ws_thread: threading.Thread | None = None
        self._running = False

    def set_exit_callback(self, callback: Callable) -> None:
        self._exit_callback = callback

    def register_trade(self, trade: ActiveTrade) -> None:
        token = trade.instrument_token
        if token not in self._token_to_trades:
            self._token_to_trades[token] = []
        self._token_to_trades[token].append(trade)
        self._all_trades[trade.trade_id] = trade

        if self._ticker and self._running:
            try:
                self._ticker.subscribe([token])
                self._ticker.set_mode(self._ticker.MODE_LTP, [token])
            except Exception as e:
                log.error("Failed to subscribe token", token=token, error=str(e))

        log.info(
            "Trade registered",
            trade_id=trade.trade_id,
            strategy=trade.strategy,
            strike=trade.strike,
            shadow=self._shadow_mode,
        )

    def unregister_trade(self, trade_id: int) -> None:
        trade = self._all_trades.pop(trade_id, None)
        if not trade:
            return
        token = trade.instrument_token
        if token in self._token_to_trades:
            self._token_to_trades[token] = [
                t for t in self._token_to_trades[token] if t.trade_id != trade_id
            ]
            if not self._token_to_trades[token]:
                del self._token_to_trades[token]
                if self._ticker and self._running:
                    try:
                        self._ticker.unsubscribe([token])
                    except Exception:
                        pass
        log.info("Trade unregistered", trade_id=trade_id)

    def start(self, api_key: str, access_token: str) -> None:
        if self._running:
            return
        if not api_key or not access_token:
            log.error("Cannot start: missing Kite credentials")
            return
        try:
            from kiteconnect import KiteTicker

            self._ticker = KiteTicker(api_key, access_token)
            self._ticker.on_ticks = self._on_ticks
            self._ticker.on_connect = self._on_connect
            self._ticker.on_close = self._on_close
            self._ticker.on_error = self._on_error

            self._running = True
            self._ws_thread = threading.Thread(
                target=self._ticker.connect,
                kwargs={"threaded": True},
                daemon=True,
                name="premium-monitor-ws",
            )
            self._ws_thread.start()
            log.info("Premium monitor started", shadow=self._shadow_mode)
        except Exception as e:
            log.error("Failed to start premium monitor", error=str(e))
            self._running = False

    def stop(self) -> None:
        self._running = False
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass
            self._ticker = None
        log.info("Premium monitor stopped")

    def get_status(self) -> dict:
        return {
            "shadow_mode": self._shadow_mode,
            "active_trades": len(self._all_trades),
            "tokens_subscribed": len(self._token_to_trades),
            "ws_connected": self._running and self._ticker is not None,
            "trades": [
                {
                    "trade_id": t.trade_id,
                    "strategy": t.strategy,
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

    # ── WebSocket callbacks ────────────────────────────

    def _on_connect(self, ws, response) -> None:
        tokens = list(self._token_to_trades.keys())
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_LTP, tokens)
        log.info("WebSocket connected", tokens=len(tokens))

    def _on_ticks(self, ws, ticks) -> None:
        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")
            if token and ltp is not None:
                self._check_trades(token, ltp)

    def _on_close(self, ws, code, reason) -> None:
        log.warning("WebSocket closed", code=code, reason=reason)

    def _on_error(self, ws, code, reason) -> None:
        log.error("WebSocket error", code=code, reason=reason)

    def _check_trades(self, token: int, current_premium: float) -> None:
        trades = self._token_to_trades.get(token, [])
        for trade in list(trades):
            result = self._check_exit(trade, current_premium)
            if result:
                if self._shadow_mode:
                    log.info(
                        "SHADOW: Would exit trade",
                        trade_id=trade.trade_id,
                        action=result["action"],
                        premium=f"{current_premium:.2f}",
                    )
                else:
                    log.info(
                        "EXIT DETECTED",
                        trade_id=trade.trade_id,
                        action=result["action"],
                        premium=f"{current_premium:.2f}",
                    )
                    if self._exit_callback:
                        self._exit_callback(result)

    @staticmethod
    def _check_exit(trade: ActiveTrade, current_premium: float) -> dict | None:
        if trade.is_selling:
            if current_premium >= trade.sl_premium:
                pnl = ((trade.entry_premium - current_premium) / trade.entry_premium) * 100
                return {
                    "trade_id": trade.trade_id,
                    "strategy": trade.strategy,
                    "action": "LOST",
                    "exit_premium": current_premium,
                    "pnl_pct": pnl,
                    "reason": f"SL hit: premium rose to {current_premium:.2f}",
                }
            elif current_premium <= trade.target_premium:
                pnl = ((trade.entry_premium - current_premium) / trade.entry_premium) * 100
                return {
                    "trade_id": trade.trade_id,
                    "strategy": trade.strategy,
                    "action": "WON",
                    "exit_premium": current_premium,
                    "pnl_pct": pnl,
                    "reason": f"Target hit: premium fell to {current_premium:.2f}",
                }
        else:
            if current_premium <= trade.sl_premium:
                pnl = ((current_premium - trade.entry_premium) / trade.entry_premium) * 100
                return {
                    "trade_id": trade.trade_id,
                    "strategy": trade.strategy,
                    "action": "LOST",
                    "exit_premium": current_premium,
                    "pnl_pct": pnl,
                    "reason": f"SL hit: premium fell to {current_premium:.2f}",
                }
            elif current_premium >= trade.target_premium:
                pnl = ((current_premium - trade.entry_premium) / trade.entry_premium) * 100
                return {
                    "trade_id": trade.trade_id,
                    "strategy": trade.strategy,
                    "action": "WON",
                    "exit_premium": current_premium,
                    "pnl_pct": pnl,
                    "reason": f"Target hit: premium rose to {current_premium:.2f}",
                }
        return None
