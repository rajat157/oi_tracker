"""Lightweight publish-subscribe event bus for decoupling components."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Dict, List

from core.logger import get_logger

log = get_logger("events", db_enabled=False)


class EventType(str, Enum):
    """Known event types in the system."""
    TRADE_CREATED = "TRADE_CREATED"
    TRADE_EXITED = "TRADE_EXITED"
    TRADE_UPDATED = "TRADE_UPDATED"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    T1_HIT = "T1_HIT"


class EventBus:
    """Simple synchronous event bus.

    Subscribers receive (event_type, data). An exception in one
    subscriber is logged but does not prevent others from running.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str | EventType, callback: Callable) -> None:
        """Register *callback* for *event_type*."""
        key = str(event_type)
        self._subscribers.setdefault(key, []).append(callback)

    def publish(self, event_type: str | EventType, data: Any = None) -> None:
        """Notify all subscribers of *event_type* with *data*."""
        key = str(event_type)
        for cb in self._subscribers.get(key, []):
            try:
                cb(event_type, data)
            except Exception:
                log.error("EventBus subscriber failed", subscriber=str(cb), event=key)

    def clear(self) -> None:
        """Remove all subscriptions (useful in tests)."""
        self._subscribers.clear()

    @property
    def subscriber_count(self) -> int:
        return sum(len(v) for v in self._subscribers.values())


# Global singleton — importable from anywhere
event_bus = EventBus()
