import asyncio

import pytest

from app.core.events import EventBus, SSEEvent


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe():
    bus = EventBus()
    received = []

    async def consumer():
        async for event in bus.subscribe():
            received.append(event)
            if len(received) >= 2:
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.01)

    await bus.publish(SSEEvent(event="test", data='{"a":1}'))
    await bus.publish(SSEEvent(event="test2", data='{"b":2}'))

    await asyncio.wait_for(task, timeout=1.0)

    assert len(received) == 2
    assert received[0].event == "test"
    assert received[1].event == "test2"


@pytest.mark.asyncio
async def test_event_bus_subscriber_count():
    bus = EventBus()
    assert bus.subscriber_count == 0

    async def dummy():
        async for _ in bus.subscribe():
            break

    task = asyncio.create_task(dummy())
    await asyncio.sleep(0.01)
    assert bus.subscriber_count == 1

    await bus.publish(SSEEvent(event="done", data="{}"))
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_sse_event_encode():
    event = SSEEvent(event="analysis_update", data='{"verdict":"Bullish"}', id="42")
    encoded = event.encode()
    assert "id: 42" in encoded
    assert "event: analysis_update" in encoded
    assert 'data: {"verdict":"Bullish"}' in encoded
