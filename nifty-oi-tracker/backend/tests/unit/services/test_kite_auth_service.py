"""Tests for KiteAuthService."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.settings import Setting
from app.services.kite_auth_service import KiteAuthService


@pytest.fixture
async def kite_engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def kite_session_factory(kite_engine):
    return async_sessionmaker(kite_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def auth_service(kite_session_factory):
    return KiteAuthService(session_factory=kite_session_factory)


class TestKiteAuthService:
    @pytest.mark.asyncio
    async def test_get_access_token_none_when_no_token(self, auth_service):
        token = await auth_service.get_access_token()
        assert token is None

    @pytest.mark.asyncio
    async def test_save_and_get_access_token(self, auth_service):
        await auth_service.save_access_token("test_token_123")
        token = await auth_service.get_access_token()
        assert token == "test_token_123"

    @pytest.mark.asyncio
    async def test_is_authenticated_false(self, auth_service):
        assert await auth_service.is_authenticated() is False

    @pytest.mark.asyncio
    async def test_is_authenticated_true(self, auth_service):
        await auth_service.save_access_token("abc")
        assert await auth_service.is_authenticated() is True

    def test_get_login_url(self, auth_service):
        url = auth_service.get_login_url()
        assert "kite.zerodha.com/connect/login" in url
        assert "api_key=" in url

    @pytest.mark.asyncio
    async def test_exchange_token(self, auth_service):
        with patch("kiteconnect.KiteConnect") as MockKite:
            mock_kite = MagicMock()
            mock_kite.generate_session.return_value = {"access_token": "exchanged_token"}
            MockKite.return_value = mock_kite

            token = await auth_service.exchange_token("request_abc")
            assert token == "exchanged_token"

            # Also saved to DB
            saved = await auth_service.get_access_token()
            assert saved == "exchanged_token"

    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self, auth_service):
        await auth_service.save_access_token("first")
        await auth_service.save_access_token("second")
        token = await auth_service.get_access_token()
        assert token == "second"
