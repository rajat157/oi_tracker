from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from app.core.dependencies import get_event_bus
from app.core.events import EventBus

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def event_stream(event_bus: EventBus = Depends(get_event_bus)) -> EventSourceResponse:
    """SSE endpoint — streams analysis_update, trade_update, market_status events."""

    async def generate():
        async for event in event_bus.subscribe():
            yield {
                "event": event.event,
                "data": event.data,
                "id": event.id,
            }

    return EventSourceResponse(generate())
