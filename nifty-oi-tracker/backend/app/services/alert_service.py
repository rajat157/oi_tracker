"""Telegram alert system — async dual-bot notifications."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.services.logging_service import get_logger

log = get_logger("alerts")

ALERT_COOLDOWN_SECONDS = 300  # 5 minutes


class AlertService:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10)
        self._main_token = settings.telegram_bot_token
        self._main_chat_id = settings.telegram_chat_id
        self._selling_token = settings.selling_alert_bot_token
        self._selling_chat_ids = [
            c.strip()
            for c in settings.selling_alert_chat_ids.split(",")
            if c.strip()
        ]
        self._selling_extra_ids = [
            c.strip()
            for c in settings.selling_alert_extra_chat_ids.split(",")
            if c.strip()
        ]
        self._last_alerts: dict[str, datetime] = {}

    async def close(self) -> None:
        await self._client.aclose()

    # ── Core send ──────────────────────────────────────

    async def send_telegram(
        self, message: str, chat_id: str | None = None, parse_mode: str = "HTML"
    ) -> bool:
        """Send a single Telegram message via the main bot."""
        if not self._main_token:
            log.warning("Telegram bot token not configured")
            return False
        target = chat_id or self._main_chat_id
        return await self._send(self._main_token, target, message, parse_mode)

    async def send_selling_alert(self, message: str) -> bool:
        """Send to main bot chat IDs + external users via selling bot."""
        success = True
        # Main bot to selling chat IDs
        for cid in self._selling_chat_ids:
            if not await self._send(self._main_token, cid, message, "HTML"):
                success = False
        # Selling bot to external users
        if self._selling_token:
            for cid in self._selling_extra_ids:
                if not await self._send(self._selling_token, cid, message, "HTML"):
                    success = False
        return success

    # ── Formatted alerts ───────────────────────────────

    async def send_alert(self, alert_type: str, message: str) -> bool:
        """Send alert with cooldown check."""
        if not self._check_cooldown(alert_type):
            return False
        return await self.send_telegram(message)

    async def send_trade_entry_alert(
        self, strategy: str, trade_data: dict[str, Any]
    ) -> bool:
        """Formatted trade entry alert."""
        alert_type = f"ENTRY_{strategy}"
        if not self._check_cooldown(alert_type):
            return False

        direction = trade_data.get("direction", "")
        strike = trade_data.get("strike", 0)
        option_type = trade_data.get("option_type", "CE")
        entry = trade_data.get("entry_premium", 0)
        sl = trade_data.get("sl_premium", 0)
        target = trade_data.get("target_premium", 0)
        confidence = trade_data.get("signal_confidence", 0)
        verdict = trade_data.get("verdict", "")

        now = datetime.now(timezone.utc)
        message = (
            f"<b>TRADE ENTRY — {strategy.upper()}</b>\n\n"
            f"<b>Direction:</b> <code>{direction}</code>\n"
            f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL:</b> <code>Rs {sl:.2f}</code>\n"
            f"<b>Target:</b> <code>Rs {target:.2f}</code>\n\n"
            f"<b>Verdict:</b> {verdict}\n"
            f"<b>Confidence:</b> {confidence:.0f}%\n\n"
            f"<i>{now.strftime('%H:%M:%S')} UTC</i>"
        )

        ok = await self.send_telegram(message)
        # Also send selling alerts to external users
        if strategy == "selling":
            await self.send_selling_alert(message)
        return ok

    async def send_trade_exit_alert(
        self, strategy: str, trade_data: dict[str, Any], exit_info: dict[str, Any]
    ) -> bool:
        """Formatted trade exit alert."""
        alert_type = f"EXIT_{strategy}"
        if not self._check_cooldown(alert_type):
            return False

        strike = trade_data.get("strike", 0)
        option_type = trade_data.get("option_type", "CE")
        entry = trade_data.get("entry_premium", 0)
        exit_premium = exit_info.get("exit_premium", 0)
        pnl_pct = exit_info.get("pnl_pct", 0)
        reason = exit_info.get("reason", "")
        action = exit_info.get("action", "")

        emoji = "W" if action == "WON" else "L"
        now = datetime.now(timezone.utc)
        message = (
            f"<b>TRADE EXIT — {strategy.upper()} [{emoji}]</b>\n\n"
            f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl_pct:+.1f}%</code>\n"
            f"<b>Reason:</b> {reason}\n\n"
            f"<i>{now.strftime('%H:%M:%S')} UTC</i>"
        )

        ok = await self.send_telegram(message)
        if strategy == "selling":
            await self.send_selling_alert(message)
        return ok

    # ── Internal ───────────────────────────────────────

    async def _send(
        self, bot_token: str, chat_id: str, message: str, parse_mode: str
    ) -> bool:
        if not bot_token:
            return False
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            log.error("Telegram API error", status=resp.status_code, chat_id=chat_id)
            return False
        except Exception as e:
            log.error("Failed to send Telegram alert", error=str(e))
            return False

    def _check_cooldown(self, alert_type: str) -> bool:
        now = datetime.now(timezone.utc)
        last = self._last_alerts.get(alert_type)
        if last:
            elapsed = (now - last).total_seconds()
            if elapsed < ALERT_COOLDOWN_SECONDS:
                return False
        self._last_alerts[alert_type] = now
        return True
