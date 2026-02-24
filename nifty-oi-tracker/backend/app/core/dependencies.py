from collections.abc import AsyncGenerator

from fastapi import Depends, Request
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


# ── Service providers (read from app.state) ────────────

def get_kite_auth(request: Request):
    return request.app.state.kite_auth


def get_alert_service(request: Request):
    return request.app.state.alert


def get_scheduler_service(request: Request):
    return request.app.state.scheduler


def get_session_factory(request: Request):
    return request.app.state.session_factory


# Type aliases for Depends
DbSession = Depends(get_db)
