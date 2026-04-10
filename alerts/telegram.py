"""Thin wrapper around Telegram Bot API for sending messages."""

from __future__ import annotations

import os
from typing import List, Optional

import requests

from core.logger import get_logger

log = get_logger("telegram", db_enabled=False)


class TelegramChannel:
    """Send messages via a Telegram bot.

    Reads bot token and chat IDs from environment by default.
    All parameters can be overridden for testing.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        default_chat_id: Optional[str] = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.default_chat_id = default_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    def send(self, message: str, chat_id: Optional[str] = None,
             parse_mode: str = "HTML") -> bool:
        """Send *message* to one or more chats (comma-separated IDs)."""
        cid = chat_id or self.default_chat_id
        if not self.bot_token:
            log.warning("Telegram bot token not configured")
            return False
        ids = [c.strip() for c in cid.split(",") if c.strip()]
        if not ids:
            return False
        success = True
        for c in ids:
            if not self._post(self.bot_token, c, message, parse_mode):
                success = False
        return success

    def send_multi(self, message: str, chat_ids: List[str],
                   parse_mode: str = "HTML",
                   extra_bot_token: Optional[str] = None,
                   extra_chat_ids: Optional[List[str]] = None) -> bool:
        """Send *message* to multiple chats. Optionally also via a second bot."""
        success = True
        for cid in chat_ids:
            if not self._post(self.bot_token, cid, message, parse_mode):
                success = False

        if extra_bot_token and extra_chat_ids:
            for cid in extra_chat_ids:
                if not self._post(extra_bot_token, cid, message, parse_mode):
                    success = False
        return success

    @staticmethod
    def _post(token: str, chat_id: str, message: str,
              parse_mode: str) -> bool:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }, timeout=10)
            if resp.status_code == 200:
                log.info("Telegram alert sent", chat_id=chat_id)
                return True
            log.error("Telegram API error", status=resp.status_code, chat_id=chat_id)
            return False
        except Exception as e:
            log.error("Failed to send Telegram alert", error=str(e), chat_id=chat_id)
            return False
