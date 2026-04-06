"""OrderflowCollector — tick consumer that caches MODE_FULL depth snapshots.

Replaces the depth-collection responsibility of `premium_monitor.py`. The
scheduler calls `collect_snapshots()` every 10s to drain cached depth into
the `orderflow_depth` table.

Core strike management (ATM-100, ATM, ATM+100 × CE/PE = 6 strikes) is
preserved from premium_monitor, with one change: subscriptions are now
requested from TickHub (reference-counted) rather than driven directly on
a KiteTicker.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Set

from core.logger import get_logger
from monitoring.tick_hub import TickConsumer

log = get_logger("orderflow_collector")

NIFTY_STEP = 50


class OrderflowCollector(TickConsumer):
    """Caches depth per token; exposes snapshot collection for the 10s job."""

    def __init__(self, tick_hub=None):
        self._tick_hub = tick_hub
        self._latest_depth: Dict[int, dict] = {}
        self._core_tokens: Dict[int, dict] = {}   # token -> {strike, option_type}
        self._core_spot: float = 0.0
        self._instrument_map = None

    def set_tick_hub(self, tick_hub) -> None:
        self._tick_hub = tick_hub

    # ------------------------------------------------------------------
    # TickConsumer interface
    # ------------------------------------------------------------------

    def on_tick(self, token: int, tick: dict) -> None:
        depth = tick.get("depth")
        if depth:
            self._latest_depth[token] = depth

    def get_required_tokens(self) -> Set[int]:
        return set(self._core_tokens.keys())

    # ------------------------------------------------------------------
    # Core strike management
    # ------------------------------------------------------------------

    def update_core_strikes(
        self,
        spot_price: float,
        active_trade_tokens: Optional[Set[int]] = None,
    ) -> None:
        """Re-derive the 6 core strikes from current spot. Subscribes the
        new tokens via TickHub and releases stale ones (except those still
        held by active trades).
        """
        if not self._instrument_map:
            return
        if spot_price <= 0:
            return

        atm = round(spot_price / NIFTY_STEP) * NIFTY_STEP
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
                    tok = inst.get("instrument_token", 0)
                    if tok:
                        new_tokens[tok] = {"strike": s, "option_type": ot}

        old_keys = set(self._core_tokens.keys())
        new_keys = set(new_tokens.keys())
        to_sub = new_keys - old_keys
        to_unsub = old_keys - new_keys

        # Never release a token still held by an active trade — ExitMonitor
        # owns that ref count, but we still skip the release_subscription call
        # to avoid double-decrementing our own count.
        trade_tokens = active_trade_tokens or set()
        to_unsub -= trade_tokens

        if self._tick_hub is not None:
            if to_sub:
                try:
                    self._tick_hub.request_subscription(list(to_sub))
                except Exception as e:
                    log.error("TickHub.request_subscription failed",
                              tokens=list(to_sub), error=str(e))
            if to_unsub:
                try:
                    self._tick_hub.release_subscription(list(to_unsub))
                except Exception as e:
                    log.error("TickHub.release_subscription failed",
                              tokens=list(to_unsub), error=str(e))

        self._core_tokens = new_tokens
        self._core_spot = spot_price

        if to_sub or to_unsub:
            log.debug("Core strikes updated",
                      subscribed=len(to_sub), unsubscribed=len(to_unsub),
                      total_core=len(new_tokens))

    # ------------------------------------------------------------------
    # Snapshot collection (scheduler 10s job)
    # ------------------------------------------------------------------

    def collect_snapshots(
        self,
        active_trades_by_token: Optional[Dict[int, object]] = None,
    ) -> List[dict]:
        """Return depth snapshots for both core strikes and active trades.

        Dedup by token (active trade + core strike overlap — active wins).
        """
        snapshots: List[dict] = []
        seen: Set[int] = set()

        # Active trades first
        if active_trades_by_token:
            for token, trade in active_trades_by_token.items():
                if token in seen:
                    continue
                depth = self._latest_depth.get(token)
                if not depth:
                    continue
                record = self._build_depth_record(
                    token,
                    strike=getattr(trade, "strike", 0),
                    option_type=getattr(trade, "option_type", ""),
                    spot_price=0.0,  # scheduler can fill or leave 0
                    depth=depth,
                )
                snapshots.append(record)
                seen.add(token)

        # Core strikes
        for token, info in self._core_tokens.items():
            if token in seen:
                continue
            depth = self._latest_depth.get(token)
            if not depth:
                continue
            record = self._build_depth_record(
                token,
                strike=info["strike"],
                option_type=info["option_type"],
                spot_price=self._core_spot,
                depth=depth,
            )
            snapshots.append(record)
            seen.add(token)

        return snapshots

    @staticmethod
    def _build_depth_record(
        token: int, strike: int, option_type: str,
        spot_price: float, depth: dict,
    ) -> dict:
        buy_levels = depth.get("buy", []) or []
        sell_levels = depth.get("sell", []) or []
        total_bid = sum(l.get("quantity", 0) for l in buy_levels)
        total_ask = sum(l.get("quantity", 0) for l in sell_levels)
        obi = round(total_bid / total_ask, 3) if total_ask > 0 else 0.0
        return {
            "instrument_token": token,
            "strike": strike,
            "option_type": option_type,
            "spot_price": spot_price,
            "total_bid_qty": total_bid,
            "total_ask_qty": total_ask,
            "bid_ask_imbalance": obi,
            "best_bid_price": buy_levels[0]["price"] if buy_levels else 0,
            "best_bid_qty": buy_levels[0]["quantity"] if buy_levels else 0,
            "best_bid_orders": buy_levels[0].get("orders", 0) if buy_levels else 0,
            "best_ask_price": sell_levels[0]["price"] if sell_levels else 0,
            "best_ask_qty": sell_levels[0]["quantity"] if sell_levels else 0,
            "best_ask_orders": sell_levels[0].get("orders", 0) if sell_levels else 0,
            "depth_json": json.dumps(depth),
        }
