"""Kite Connect authentication — token management via Settings table."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.settings import Setting


class KiteAuthService:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._api_key = settings.kite_api_key
        self._api_secret = settings.kite_api_secret

    # ── Read ───────────────────────────────────────────

    async def get_access_token(self) -> str | None:
        """Read today's access token from Settings table."""
        async with self._session_factory() as session:
            token_date = await self._get_setting(session, "kite_token_date")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if token_date != today:
                return None
            return await self._get_setting(session, "kite_access_token")

    async def is_authenticated(self) -> bool:
        try:
            token = await self.get_access_token()
            return token is not None and len(token) > 0
        except Exception:
            # Tables may not exist yet (before migrations)
            return False

    def get_login_url(self) -> str:
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={self._api_key}"

    # ── Write ──────────────────────────────────────────

    async def save_access_token(self, token: str) -> None:
        """Save access token + today's date to Settings table."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with self._session_factory() as session:
            await self._set_setting(session, "kite_access_token", token)
            await self._set_setting(session, "kite_token_date", today)
            await session.commit()

    async def validate_token(self, access_token: str) -> dict:
        """Validate an access token by calling kite.profile(). Returns profile on success."""
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=self._api_key)
        kite.set_access_token(access_token)

        def _profile():
            return kite.profile()

        return await asyncio.get_running_loop().run_in_executor(None, _profile)

    async def exchange_token(self, request_token: str) -> str:
        """Exchange request_token for access_token via Kite API."""
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=self._api_key)

        def _exchange():
            data = kite.generate_session(request_token, api_secret=self._api_secret)
            return data["access_token"]

        access_token = await asyncio.get_running_loop().run_in_executor(None, _exchange)
        await self.save_access_token(access_token)
        return access_token

    # ── Helpers ────────────────────────────────────────

    @staticmethod
    async def _get_setting(session: AsyncSession, key: str) -> str | None:
        stmt = select(Setting.value).where(Setting.key == key)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def _set_setting(session: AsyncSession, key: str, value: str) -> None:
        stmt = select(Setting).where(Setting.key == key)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            session.add(Setting(key=key, value=value))
        await session.flush()
