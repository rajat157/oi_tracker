"""TickHub — single Kite WebSocket connection, dispatches ticks to consumers.

This is the foundation of the live-data architecture. One KiteTicker connection
is shared across multiple TickConsumer implementations (CandleBuilder,
ExitMonitor, OrderflowCollector, LivePnlBroadcaster). Subscriptions are
reference-counted so two consumers watching the same token result in exactly
one ticker subscription; when one consumer releases a token, it only actually
unsubscribes when the ref count drops to zero.

Threading:
- KiteTicker runs on its own daemon thread (`kite-tick-hub`).
- `on_ticks` fires on that thread. The hub dispatches ticks synchronously to
  every registered consumer. Consumers must do minimal work inline.
- Mutations to the subscription table use `threading.RLock`.

Reconnection:
- On (re)connect, `_on_connect` re-subscribes the full union of all tokens
  with a positive ref count and calls `consumer.on_connect()` so consumers
  can trigger gap backfills.
- Exceptions inside one consumer are caught and logged — a buggy consumer
  must NOT kill the tick stream for the others.
"""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Dict, List, Optional, Set

from core.logger import get_logger

log = get_logger("tick_hub")


class TickConsumer(ABC):
    """Base class for every tick consumer wired into TickHub."""

    @abstractmethod
    def on_tick(self, token: int, tick: dict) -> None:
        """Receive a single tick. Runs on the WebSocket thread — be fast."""

    def on_connect(self) -> None:
        """Called after each (re)connection so the consumer can react
        (e.g., backfill gaps). Default: no-op."""

    def get_required_tokens(self) -> Set[int]:
        """Tokens this consumer wants subscribed when the hub (re)connects.

        Used by TickHub to rebuild the subscription set on reconnect.
        Override if your consumer tracks its own tokens (e.g., active trades).
        """
        return set()


class TickHub:
    """Owns one KiteTicker; reference-counts tokens across consumers."""

    def __init__(self, api_key: str = "", access_token: str = ""):
        self._api_key = api_key or os.environ.get("KITE_API_KEY", "")
        self._access_token = access_token
        self._consumers: List[TickConsumer] = []
        self._ticker = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.RLock()
        self._token_refs: Dict[int, int] = defaultdict(int)
        self._tick_count: int = 0

    # ------------------------------------------------------------------
    # Consumer registry
    # ------------------------------------------------------------------

    def add_consumer(self, consumer: TickConsumer) -> None:
        """Register a tick consumer. Safe to call before or after start."""
        with self._lock:
            if consumer not in self._consumers:
                self._consumers.append(consumer)
                log.info("Consumer registered",
                         consumer=consumer.__class__.__name__)

    # ------------------------------------------------------------------
    # Subscription management (reference counted)
    # ------------------------------------------------------------------

    def request_subscription(self, tokens: List[int]) -> None:
        """Increment ref count for each token; subscribe new ones on the ticker."""
        if not tokens:
            return
        newly_added: List[int] = []
        with self._lock:
            for token in tokens:
                if self._token_refs[token] == 0:
                    newly_added.append(token)
                self._token_refs[token] += 1
            if newly_added and self._ticker is not None and self._running:
                try:
                    self._ticker.subscribe(newly_added)
                    self._ticker.set_mode(self._ticker.MODE_FULL, newly_added)
                    log.info("Subscribed tokens",
                             count=len(newly_added), tokens=newly_added)
                except Exception as e:
                    log.error("Subscribe failed",
                              tokens=newly_added, error=str(e))

    def release_subscription(self, tokens: List[int]) -> None:
        """Decrement ref count for each token; unsubscribe when count hits 0."""
        if not tokens:
            return
        to_unsub: List[int] = []
        with self._lock:
            for token in tokens:
                if self._token_refs.get(token, 0) <= 0:
                    continue
                self._token_refs[token] -= 1
                if self._token_refs[token] == 0:
                    del self._token_refs[token]
                    to_unsub.append(token)
            if to_unsub and self._ticker is not None and self._running:
                try:
                    self._ticker.unsubscribe(to_unsub)
                    log.info("Unsubscribed tokens",
                             count=len(to_unsub), tokens=to_unsub)
                except Exception as e:
                    log.error("Unsubscribe failed",
                              tokens=to_unsub, error=str(e))

    def get_subscribed_tokens(self) -> Set[int]:
        """Snapshot of currently-subscribed tokens (ref count > 0)."""
        with self._lock:
            return {t for t, c in self._token_refs.items() if c > 0}

    def get_ref_count(self, token: int) -> int:
        """Read the current ref count for a token (for tests / diagnostics)."""
        with self._lock:
            return self._token_refs.get(token, 0)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the KiteTicker on a daemon thread."""
        if self._running:
            log.warning("TickHub already running")
            return

        if not self._access_token:
            from kite.auth import load_token
            self._access_token = load_token()

        if not self._api_key or not self._access_token:
            log.error("TickHub: missing Kite credentials — not starting")
            return

        try:
            from kiteconnect import KiteTicker
            self._ticker = KiteTicker(self._api_key, self._access_token)
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
                name="kite-tick-hub",
            )
            self._ws_thread.start()

            log.info("TickHub started",
                     consumers=len(self._consumers),
                     tokens=len(self._token_refs))
        except Exception as e:
            log.error("TickHub start failed", error=str(e))
            self._running = False

    def stop(self) -> None:
        """Close the ticker; no more ticks will be dispatched after this."""
        self._running = False
        if self._ticker is not None:
            try:
                self._ticker.close()
            except Exception:
                pass
            self._ticker = None
        log.info("TickHub stopped")

    # ------------------------------------------------------------------
    # KiteTicker callbacks (all run on the WS thread)
    # ------------------------------------------------------------------

    def _on_connect(self, ws, response) -> None:
        """Re-subscribe the full token union + notify consumers."""
        # Pull the latest required tokens from each consumer (handles
        # consumers that added tokens before the hub was running).
        self._refresh_token_refs_from_consumers()

        with self._lock:
            tokens = list(self._token_refs.keys())

        if tokens:
            try:
                ws.subscribe(tokens)
                ws.set_mode(ws.MODE_FULL, tokens)
                log.info("TickHub connected, subscribed", count=len(tokens))
            except Exception as e:
                log.error("Subscribe on connect failed", error=str(e))
        else:
            log.info("TickHub connected, no tokens to subscribe")

        # Notify consumers so they can trigger gap backfills, etc.
        for consumer in list(self._consumers):
            try:
                consumer.on_connect()
            except Exception as e:
                log.error("Consumer on_connect error",
                          consumer=consumer.__class__.__name__, error=str(e))

    def _on_ticks(self, ws, ticks) -> None:
        """Dispatch every tick to every consumer."""
        for tick in ticks:
            token = tick.get("instrument_token")
            if token is None:
                continue
            self._tick_count += 1
            for consumer in self._consumers:
                try:
                    consumer.on_tick(token, tick)
                except Exception as e:
                    log.error("Consumer on_tick error",
                              consumer=consumer.__class__.__name__,
                              token=token, error=str(e))

        # Health log every 500 ticks
        if self._tick_count and self._tick_count % 500 == 0:
            with self._lock:
                log.info("TickHub health",
                         ticks=self._tick_count,
                         tokens=len(self._token_refs),
                         consumers=len(self._consumers))

    def _on_close(self, ws, code, reason) -> None:
        log.warning("TickHub WebSocket closed", code=code, reason=reason)

    def _on_error(self, ws, code, reason) -> None:
        log.error("TickHub WebSocket error", code=code, reason=reason)

    def _on_reconnect(self, ws, attempts) -> None:
        log.info("TickHub reconnecting", attempts=attempts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_token_refs_from_consumers(self) -> None:
        """Pull the full required-token set from every consumer into the hub.

        This is called on (re)connect so that a consumer that added tokens
        BEFORE the hub's ticker was running still ends up subscribed. We
        count each distinct (consumer, token) pair once; a token requested
        by two consumers will have ref count 2.
        """
        union_refs: Dict[int, int] = defaultdict(int)
        for consumer in list(self._consumers):
            try:
                needed = consumer.get_required_tokens() or set()
                for token in needed:
                    union_refs[token] += 1
            except Exception as e:
                log.error("get_required_tokens failed",
                          consumer=consumer.__class__.__name__, error=str(e))
        with self._lock:
            # Merge: keep manual request_subscription counts but ensure every
            # consumer-required token has at least ref count 1 from that path.
            for token, count in union_refs.items():
                if self._token_refs.get(token, 0) < count:
                    self._token_refs[token] = max(self._token_refs.get(token, 0), count)
