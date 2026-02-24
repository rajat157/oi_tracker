import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SSEEvent:
    """Server-Sent Event payload."""

    event: str
    data: str
    id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def encode(self) -> str:
        lines = []
        if self.id:
            lines.append(f"id: {self.id}")
        lines.append(f"event: {self.event}")
        lines.append(f"data: {self.data}")
        lines.append("")
        return "\n".join(lines) + "\n"


class EventBus:
    """Broadcast SSE events to all subscribers via asyncio.Queue."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[SSEEvent]] = []

    async def publish(self, event: SSEEvent) -> None:
        dead: list[asyncio.Queue[SSEEvent]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(queue)
        for q in dead:
            self._subscribers.remove(q)

    async def subscribe(self) -> AsyncGenerator[SSEEvent]:
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
