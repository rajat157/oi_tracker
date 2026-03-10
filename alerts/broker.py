"""AlertBroker — subscribes to EventBus and routes alerts to TelegramChannel.

Strategies include a pre-formatted ``alert_message`` in their event data.
The broker extracts the message and sends it via the appropriate channel.
This fully decouples strategies from knowing about Telegram.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from alerts.telegram import TelegramChannel
from core.events import EventBus, EventType, event_bus as _default_bus

logger = logging.getLogger(__name__)


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

        # Selling strategy uses a separate bot + extra recipients
        self._selling_chat_ids = [
            x.strip()
            for x in os.getenv("SELLING_ALERT_CHAT_IDS",
                               os.getenv("TELEGRAM_CHAT_ID", "")).split(",")
            if x.strip()
        ]
        self._selling_extra_bot = os.getenv("SELLING_ALERT_BOT_TOKEN", "")
        self._selling_extra_ids = [
            x.strip()
            for x in os.getenv("SELLING_ALERT_EXTRA_CHAT_IDS", "").split(",")
            if x.strip()
        ]

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

        tracker_type = data.get("tracker_type", "")

        if tracker_type == "selling":
            self.channel.send_multi(
                message,
                chat_ids=self._selling_chat_ids,
                extra_bot_token=self._selling_extra_bot or None,
                extra_chat_ids=self._selling_extra_ids or None,
            )
        else:
            self.channel.send(message)
