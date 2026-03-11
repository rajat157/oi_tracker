"""AlertBroker — subscribes to EventBus and routes alerts to TelegramChannel.

Strategies include a pre-formatted ``alert_message`` in their event data.
The broker extracts the message and sends it via the appropriate channel.
This fully decouples strategies from knowing about Telegram.
"""

from __future__ import annotations

from typing import Any, Optional

from alerts.telegram import TelegramChannel
from core.events import EventBus, EventType, event_bus as _default_bus
from core.logger import get_logger

log = get_logger("alert_broker", db_enabled=False)


class AlertBroker:
    """Central alert dispatcher wired to the EventBus.

    Usage::

        broker = AlertBroker()          # subscribes to global event_bus
        broker = AlertBroker(bus=my_bus) # subscribes to custom bus (for tests)
    """

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        channel: Optional[TelegramChannel] = None,
    ) -> None:
        self.channel = channel or TelegramChannel()

        bus = bus or _default_bus
        bus.subscribe(EventType.TRADE_CREATED, self._on_event)
        bus.subscribe(EventType.TRADE_EXITED, self._on_event)
        bus.subscribe(EventType.TRADE_UPDATED, self._on_event)
        bus.subscribe(EventType.T1_HIT, self._on_event)

    def _on_event(self, event_type: str, data: Any) -> None:
        """Generic handler — extract message and send."""
        if not isinstance(data, dict):
            return
        message = data.get("alert_message")
        if not message:
            return

        self.channel.send(message)
