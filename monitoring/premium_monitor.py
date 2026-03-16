"""
Premium Monitor — Real-time WebSocket watcher for active trades.

Runs in a background thread. Catches SL/target hits between the 3-minute
polling cycles via Kite WebSocket (KiteTicker).

Shadow mode (Phase A): Logs detections but does NOT update DB or send alerts.
Live mode (Phase B): Calls exit_callback on SL/target detection.
"""

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable

from core.logger import get_logger

log = get_logger("premium_monitor")


@dataclass
class ActiveTrade:
    """Represents an active trade being monitored."""
    trade_id: int
    tracker_type: str  # "iron_pulse", "selling", "dessert", "momentum"
    strike: int
    option_type: str  # "CE" or "PE"
    instrument_token: int
    entry_premium: float
    sl_premium: float
    target_premium: float
    is_selling: bool = False


class PremiumMonitor:
    """
    Real-time premium monitoring via Kite WebSocket.

    Subscribes to instrument tokens for active trades and checks
    every tick for SL/target hits.
    """

    def __init__(self, socketio=None, shadow_mode: bool = True):
        self._socketio = socketio
        self._shadow_mode = shadow_mode
        self._exit_callback: Optional[Callable] = None

        # Trade tracking
        self._token_to_trades: Dict[int, List[ActiveTrade]] = {}
        self._all_trades: Dict[int, ActiveTrade] = {}  # trade_id -> ActiveTrade
        self._trade_gtt_ids: Dict[int, int] = {}  # trade_id -> GTT trigger ID

        # WebSocket
        self._ticker = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False

        # Kite client (for GTT polling)
        self._kite = None

        # Instrument map (set externally or via scan)
        self._instrument_map = None

        # LTP cache — updated on every WebSocket tick
        self._latest_ltp: Dict[int, float] = {}

        # Depth cache — updated on every MODE_FULL tick
        self._latest_depth: Dict[int, dict] = {}

        # Tick counter for health diagnostics
        self._tick_count: int = 0

        # Core strike tokens — always subscribed for orderflow collection
        # (ATM, ATM±100 — both CE and PE = 6 strikes)
        self._core_tokens: Dict[int, dict] = {}  # token -> {strike, option_type}
        self._core_spot: float = 0.0  # spot price for core depth snapshots

    def set_exit_callback(self, callback: Callable):
        """Set the callback for SL/target hit detection.

        callback receives a dict:
            {"trade_id": int, "tracker_type": str, "action": "WON"|"LOST",
             "exit_premium": float, "reason": str}
        """
        self._exit_callback = callback

    def is_monitoring(self, trade_id: int) -> bool:
        """Check if a trade is being monitored via WebSocket."""
        return trade_id in self._all_trades

    def register_trade(self, trade: ActiveTrade):
        """Register a trade for real-time monitoring."""
        token = trade.instrument_token
        if token not in self._token_to_trades:
            self._token_to_trades[token] = []
        self._token_to_trades[token].append(trade)
        self._all_trades[trade.trade_id] = trade

        # Subscribe token on WebSocket if running
        if self._ticker and self._running:
            try:
                self._ticker.subscribe([token])
                self._ticker.set_mode(self._ticker.MODE_FULL, [token])
            except Exception as e:
                log.error("Failed to subscribe token", token=token, error=str(e))

        log.info("Trade registered for monitoring",
                 trade_id=trade.trade_id,
                 tracker=trade.tracker_type,
                 strike=trade.strike,
                 type=trade.option_type,
                 token=token,
                 shadow=self._shadow_mode)

    def unregister_trade(self, trade_id: int):
        """Remove a trade from monitoring."""
        trade = self._all_trades.pop(trade_id, None)
        if not trade:
            return

        token = trade.instrument_token
        if token in self._token_to_trades:
            self._token_to_trades[token] = [
                t for t in self._token_to_trades[token]
                if t.trade_id != trade_id
            ]
            # Unsubscribe if no more trades on this token
            if not self._token_to_trades[token]:
                del self._token_to_trades[token]
                if self._ticker and self._running:
                    try:
                        self._ticker.unsubscribe([token])
                    except Exception:
                        pass

        self._trade_gtt_ids.pop(trade_id, None)
        log.info("Trade unregistered", trade_id=trade_id)

    def start(self):
        """Start the WebSocket connection in a background thread."""
        if self._running:
            return

        from kite.auth import load_token
        api_key = os.environ.get('KITE_API_KEY', '')
        access_token = load_token()

        if not api_key or not access_token:
            log.error("Cannot start premium monitor: missing Kite credentials")
            return

        try:
            from kiteconnect import KiteConnect, KiteTicker
            self._kite = KiteConnect(api_key=api_key)
            self._kite.set_access_token(access_token)

            self._ticker = KiteTicker(api_key, access_token)
            self._ticker.on_ticks = self._on_ticks
            self._ticker.on_connect = self._on_connect
            self._ticker.on_close = self._on_close
            self._ticker.on_error = self._on_error
            self._ticker.on_reconnect = self._on_reconnect

            self._running = True
            self._ws_thread = threading.Thread(
                target=self._ticker.connect,
                kwargs={"threaded": True},
                daemon=True,
                name="premium-monitor-ws"
            )
            self._ws_thread.start()

            log.info("Premium monitor started",
                     shadow=self._shadow_mode,
                     tokens=len(self._token_to_trades))

        except Exception as e:
            log.error("Failed to start premium monitor", error=str(e))
            self._running = False

    def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass
            self._ticker = None
        log.info("Premium monitor stopped")

    def _on_connect(self, ws, response):
        """WebSocket connected — subscribe to all tracked + core tokens."""
        tokens = list(self._token_to_trades.keys())
        core_tokens = list(self._core_tokens.keys())
        all_tokens = list(set(tokens + core_tokens))
        if all_tokens:
            ws.subscribe(all_tokens)
            ws.set_mode(ws.MODE_FULL, all_tokens)
            log.info("WebSocket connected, subscribed",
                     trade_tokens=len(tokens), core_tokens=len(core_tokens))
        else:
            log.info("WebSocket connected, no tokens to subscribe")

    def _on_ticks(self, ws, ticks):
        """Process incoming ticks."""
        for tick in ticks:
            token = tick.get('instrument_token')
            ltp = tick.get('last_price')
            if token and ltp is not None:
                self._on_tick_received(token, ltp)
            # Cache depth data if available (MODE_FULL)
            depth = tick.get('depth')
            if token and depth:
                self._latest_depth[token] = depth

    def _on_close(self, ws, code, reason):
        """WebSocket closed."""
        log.warning("WebSocket closed", code=code, reason=reason)

    def _on_error(self, ws, code, reason):
        """WebSocket error."""
        log.error("WebSocket error", code=code, reason=reason)

    def _on_reconnect(self, ws, attempts_count):
        """WebSocket reconnecting — re-subscribe all tokens."""
        log.warning("WebSocket reconnecting", attempt=attempts_count,
                    tokens_to_resubscribe=len(self._token_to_trades))

    def _on_tick_received(self, token: int, current_premium: float):
        """Process a single tick for a token."""
        self._tick_count += 1
        if self._tick_count % 500 == 0:
            log.info("WebSocket health", ticks=self._tick_count,
                     tokens=len(self._token_to_trades),
                     trades=len(self._all_trades))
        self._latest_ltp[token] = current_premium
        trades = self._token_to_trades.get(token, [])

        for trade in list(trades):  # Copy to avoid modification during iteration
            result = self._check_exit(trade, current_premium)
            if result:
                if self._shadow_mode:
                    log.info("SHADOW: Would exit trade",
                             trade_id=trade.trade_id,
                             tracker=trade.tracker_type,
                             action=result["action"],
                             premium=f"{current_premium:.2f}",
                             entry=f"{trade.entry_premium:.2f}",
                             reason=result["reason"])
                else:
                    log.info("EXIT DETECTED",
                             trade_id=trade.trade_id,
                             tracker=trade.tracker_type,
                             action=result["action"],
                             premium=f"{current_premium:.2f}",
                             reason=result["reason"])
                    if self._exit_callback:
                        self._exit_callback(result)

    def _check_exit(self, trade: ActiveTrade, current_premium: float) -> Optional[dict]:
        """
        Check if current premium triggers SL or target.

        For BUYING trades:
            SL: premium <= sl_premium → LOST
            Target: premium >= target_premium → WON

        For SELLING trades (inverted):
            SL: premium >= sl_premium → LOST (premium rose = loss for seller)
            Target: premium <= target_premium → WON (premium fell = profit for seller)

        Returns:
            Dict with exit info or None if no exit.
        """
        if trade.is_selling:
            # Selling: premium rising is bad, falling is good
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
            elif current_premium <= trade.target_premium:
                pnl = ((trade.entry_premium - current_premium) / trade.entry_premium) * 100
                return {
                    "trade_id": trade.trade_id,
                    "tracker_type": trade.tracker_type,
                    "action": "WON",
                    "exit_premium": current_premium,
                    "pnl_pct": pnl,
                    "reason": f"Target hit: premium fell to {current_premium:.2f} (T: {trade.target_premium:.2f})",
                }
        else:
            # Buying: premium falling is bad, rising is good
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
            elif current_premium >= trade.target_premium:
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

    def get_live_pnl(self) -> dict:
        """Return current P&L for all active trades using cached WebSocket LTP.

        Returns dict keyed by tracker_type with current_premium, pnl_pct, etc.
        Empty dict if no trades or no LTP data.
        """
        if not self._all_trades or not self._latest_ltp:
            return {}

        result = {}
        for trade in self._all_trades.values():
            ltp = self._latest_ltp.get(trade.instrument_token)
            if ltp is None:
                continue

            if trade.is_selling:
                pnl_pct = ((trade.entry_premium - ltp) / trade.entry_premium) * 100
            else:
                pnl_pct = ((ltp - trade.entry_premium) / trade.entry_premium) * 100

            pnl_points = ltp - trade.entry_premium
            if trade.is_selling:
                pnl_points = trade.entry_premium - ltp

            result[trade.tracker_type] = {
                "current_premium": round(ltp, 2),
                "pnl_pct": round(pnl_pct, 2),
                "pnl_points": round(pnl_points, 2),
                "strike": trade.strike,
                "option_type": trade.option_type,
                "entry_premium": trade.entry_premium,
            }

        return result

    def update_core_strikes(self, spot_price: float):
        """Subscribe to 6 core strikes (ATM, ATM±100 CE+PE) for orderflow collection.

        Called every 3-min cycle by the scheduler after spot price is known.
        Only re-subscribes if ATM has changed.

        # TODO: Future orderflow strategy — use core-strike OBI (bid/ask imbalance)
        # across 6 strikes to detect institutional positioning shifts.
        # Need 30+ full-session days of clean data before validating.
        # Key research: contrarian OBI signal showed 56.4% accuracy at 9-min horizon.
        """
        if not self._instrument_map or not self._ticker or not self._running:
            return
        if spot_price <= 0:
            return

        step = 50  # NIFTY strike interval
        atm = round(spot_price / step) * step
        strikes = [atm - 100, atm, atm + 100]
        option_types = ["CE", "PE"]

        expiry = self._instrument_map.get_current_expiry()
        if not expiry:
            return

        new_tokens: Dict[int, dict] = {}
        for s in strikes:
            for ot in option_types:
                inst = self._instrument_map.get_option_instrument(s, ot, expiry)
                if inst:
                    token = inst.get("instrument_token", 0)
                    if token:
                        new_tokens[token] = {"strike": s, "option_type": ot}

        # Diff: find tokens to subscribe/unsubscribe
        old_keys = set(self._core_tokens.keys())
        new_keys = set(new_tokens.keys())
        to_sub = new_keys - old_keys
        to_unsub = old_keys - new_keys

        # Don't unsub tokens that are also used by active trades
        trade_tokens = set(self._token_to_trades.keys())
        to_unsub -= trade_tokens

        if to_sub or to_unsub:
            try:
                if to_unsub:
                    self._ticker.unsubscribe(list(to_unsub))
                if to_sub:
                    self._ticker.subscribe(list(to_sub))
                    self._ticker.set_mode(self._ticker.MODE_FULL, list(to_sub))
                log.debug("Core strikes updated",
                          subscribed=len(to_sub), unsubscribed=len(to_unsub),
                          total_core=len(new_tokens))
            except Exception as e:
                log.error("Failed to update core strikes", error=str(e))

        self._core_tokens = new_tokens
        self._core_spot = spot_price

    def get_core_depth_snapshot(self) -> list:
        """Return depth snapshots for all core strike tokens with spot_price."""
        snapshots = []
        for token, info in self._core_tokens.items():
            depth = self._latest_depth.get(token)
            if not depth:
                continue
            buy_levels = depth.get('buy', [])
            sell_levels = depth.get('sell', [])
            total_bid = sum(l.get('quantity', 0) for l in buy_levels)
            total_ask = sum(l.get('quantity', 0) for l in sell_levels)
            obi = round(total_bid / total_ask, 3) if total_ask > 0 else 0.0
            snapshots.append({
                "instrument_token": token,
                "strike": info["strike"],
                "option_type": info["option_type"],
                "spot_price": self._core_spot,
                "total_bid_qty": total_bid,
                "total_ask_qty": total_ask,
                "bid_ask_imbalance": obi,
                "best_bid_price": buy_levels[0]['price'] if buy_levels else 0,
                "best_bid_qty": buy_levels[0]['quantity'] if buy_levels else 0,
                "best_bid_orders": buy_levels[0].get('orders', 0) if buy_levels else 0,
                "best_ask_price": sell_levels[0]['price'] if sell_levels else 0,
                "best_ask_qty": sell_levels[0]['quantity'] if sell_levels else 0,
                "best_ask_orders": sell_levels[0].get('orders', 0) if sell_levels else 0,
                "depth_json": json.dumps(depth),
            })
        return snapshots

    def get_depth_snapshot(self) -> list:
        """Return depth snapshots for all monitored trades with cached WebSocket depth."""
        snapshots = []
        for trade in self._all_trades.values():
            depth = self._latest_depth.get(trade.instrument_token)
            if not depth:
                continue
            buy_levels = depth.get('buy', [])
            sell_levels = depth.get('sell', [])
            total_bid = sum(l.get('quantity', 0) for l in buy_levels)
            total_ask = sum(l.get('quantity', 0) for l in sell_levels)
            obi = round(total_bid / total_ask, 3) if total_ask > 0 else 0.0
            snapshots.append({
                "instrument_token": trade.instrument_token,
                "strike": trade.strike,
                "option_type": trade.option_type,
                "total_bid_qty": total_bid,
                "total_ask_qty": total_ask,
                "bid_ask_imbalance": obi,
                "best_bid_price": buy_levels[0]['price'] if buy_levels else 0,
                "best_bid_qty": buy_levels[0]['quantity'] if buy_levels else 0,
                "best_bid_orders": buy_levels[0].get('orders', 0) if buy_levels else 0,
                "best_ask_price": sell_levels[0]['price'] if sell_levels else 0,
                "best_ask_qty": sell_levels[0]['quantity'] if sell_levels else 0,
                "best_ask_orders": sell_levels[0].get('orders', 0) if sell_levels else 0,
                "depth_json": json.dumps(depth),
            })
        return snapshots

    def scan_existing_trades(self, strategies: dict | None = None):
        """
        On startup, pick up ACTIVE trades from all tracker tables.
        Requires self._instrument_map to be set.

        Parameters
        ----------
        strategies : dict | None
            Mapping of name -> strategy instance (BaseTracker subclass).
            When provided, iterates over strategies to find active trades.
        """
        if not self._instrument_map:
            log.warning("Cannot scan trades: no instrument map")
            return

        expiry = self._instrument_map.get_current_expiry()
        if not expiry:
            log.warning("Cannot scan trades: no current expiry")
            return

        if not strategies:
            log.warning("Cannot scan trades: no strategies provided")
            return

        count = 0
        for name, strategy in strategies.items():
            try:
                setup = strategy.get_active()
                if not setup:
                    continue
                # Iron Pulse uses PENDING->ACTIVE lifecycle; only monitor ACTIVE
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
                log.error("Error scanning trades", strategy=name, error=str(e))

        log.info("Scanned existing trades", found=count)

    def _db_trade_to_active(self, setup: dict, tracker_type: str,
                            expiry: str, is_selling: bool) -> Optional[ActiveTrade]:
        """Convert a DB trade row to an ActiveTrade object."""
        strike = setup.get('strike')
        option_type = setup.get('option_type')

        if not strike or not option_type:
            return None

        inst = self._instrument_map.get_option_instrument(strike, option_type, expiry)
        if not inst:
            log.warning("No instrument found for trade",
                        strike=strike, type=option_type, expiry=expiry)
            return None

        # Get SL/target premiums — field names differ by tracker
        sl_premium = setup.get('sl_premium', 0)
        if is_selling:
            target_premium = setup.get('target2_premium') or setup.get('target_premium', 0)
        else:
            target_premium = setup.get('target1_premium') or setup.get('target_premium', 0)

        return ActiveTrade(
            trade_id=setup['id'],
            tracker_type=tracker_type,
            strike=strike,
            option_type=option_type,
            instrument_token=inst['instrument_token'],
            entry_premium=setup.get('entry_premium', 0),
            sl_premium=sl_premium,
            target_premium=target_premium,
            is_selling=is_selling,
        )

    def poll_gtt_status(self):
        """
        Check GTT trigger status for active trades.
        Detects externally-closed trades (e.g. Kite GTT triggered between snapshots).
        """
        if not self._kite or not self._trade_gtt_ids:
            return

        for trade_id, gtt_id in list(self._trade_gtt_ids.items()):
            try:
                gtt = self._kite.get_gtt(gtt_id)
                if gtt and gtt.get('status') == 'triggered':
                    trade = self._all_trades.get(trade_id)
                    if not trade:
                        continue

                    # Extract exit price from GTT orders
                    orders = gtt.get('orders', [])
                    exit_price = 0.0
                    if orders:
                        result = orders[0].get('result', {})
                        exit_price = result.get('price', 0.0)

                    result_info = {
                        "trade_id": trade_id,
                        "tracker_type": trade.tracker_type,
                        "action": "GTT_TRIGGERED",
                        "exit_premium": exit_price,
                        "pnl_pct": 0.0,
                        "reason": f"GTT {gtt_id} triggered externally",
                    }

                    if self._shadow_mode:
                        log.info("SHADOW: GTT triggered externally",
                                 trade_id=trade_id, gtt_id=gtt_id,
                                 exit_price=exit_price)
                    else:
                        log.info("GTT triggered externally",
                                 trade_id=trade_id, gtt_id=gtt_id,
                                 exit_price=exit_price)
                        if self._exit_callback:
                            self._exit_callback(result_info)

            except Exception as e:
                log.error("Error polling GTT", gtt_id=gtt_id, error=str(e))

    def get_status(self) -> dict:
        """Get monitor status for dashboard."""
        return {
            "shadow_mode": self._shadow_mode,
            "active_trades": len(self._all_trades),
            "tokens_subscribed": len(self._token_to_trades),
            "ws_connected": self._running and self._ticker is not None,
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
