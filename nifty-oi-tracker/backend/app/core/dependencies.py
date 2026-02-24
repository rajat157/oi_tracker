from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.events import EventBus

# Singleton event bus
_event_bus = EventBus()


async def get_db() -> AsyncGenerator[AsyncSession]:
    async for session in get_session():
        yield session


def get_event_bus() -> EventBus:
    return _event_bus


# Type aliases for Depends
DbSession = Depends(get_db)
