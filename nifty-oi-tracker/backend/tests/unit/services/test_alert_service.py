"""Tests for AlertService."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from app.services.alert_service import AlertService, ALERT_COOLDOWN_SECONDS


@pytest.fixture
def alert_service():
    svc = AlertService()
    return svc


class TestAlertService:
    @pytest.mark.asyncio
    async def test_send_telegram_no_token(self, alert_service):
        alert_service._main_token = ""
        result = await alert_service.send_telegram("hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_telegram_success(self, alert_service):
        alert_service._main_token = "fake_token"
        alert_service._main_chat_id = "12345"

        mock_response = MagicMock()
        mock_response.status_code = 200
        alert_service._client.post = AsyncMock(return_value=mock_response)

        result = await alert_service.send_telegram("test message")
        assert result is True
        alert_service._client.post.assert_called_once()

        # Verify URL and payload
        call_args = alert_service._client.post.call_args
        assert "fake_token" in call_args[0][0]
        assert call_args[1]["json"]["chat_id"] == "12345"
        assert call_args[1]["json"]["text"] == "test message"

    @pytest.mark.asyncio
    async def test_send_telegram_api_error(self, alert_service):
        alert_service._main_token = "token"
        alert_service._main_chat_id = "123"

        mock_response = MagicMock()
        mock_response.status_code = 400
        alert_service._client.post = AsyncMock(return_value=mock_response)

        result = await alert_service.send_telegram("msg")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_telegram_network_error(self, alert_service):
        alert_service._main_token = "token"
        alert_service._main_chat_id = "123"
        alert_service._client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))

        result = await alert_service.send_telegram("msg")
        assert result is False

    def test_cooldown_first_call_passes(self, alert_service):
        assert alert_service._check_cooldown("TEST") is True

    def test_cooldown_second_call_blocked(self, alert_service):
        alert_service._check_cooldown("TEST")
        assert alert_service._check_cooldown("TEST") is False

    def test_cooldown_different_types_independent(self, alert_service):
        alert_service._check_cooldown("ENTRY")
        assert alert_service._check_cooldown("EXIT") is True

    def test_cooldown_expires(self, alert_service):
        alert_service._check_cooldown("TEST")
        # Manually set last alert to past
        alert_service._last_alerts["TEST"] = datetime.now(timezone.utc) - timedelta(
            seconds=ALERT_COOLDOWN_SECONDS + 1
        )
        assert alert_service._check_cooldown("TEST") is True

    @pytest.mark.asyncio
    async def test_send_alert_with_cooldown(self, alert_service):
        alert_service._main_token = "token"
        alert_service._main_chat_id = "123"
        mock_response = MagicMock()
        mock_response.status_code = 200
        alert_service._client.post = AsyncMock(return_value=mock_response)

        # First call passes
        result = await alert_service.send_alert("ENTRY", "msg")
        assert result is True
        # Second call blocked by cooldown
        result = await alert_service.send_alert("ENTRY", "msg")
        assert result is False

    @pytest.mark.asyncio
    async def test_selling_alert_dual_bot(self, alert_service):
        alert_service._main_token = "main_token"
        alert_service._selling_token = "sell_token"
        alert_service._selling_chat_ids = ["111"]
        alert_service._selling_extra_ids = ["222"]

        mock_response = MagicMock()
        mock_response.status_code = 200
        alert_service._client.post = AsyncMock(return_value=mock_response)

        result = await alert_service.send_selling_alert("sell msg")
        assert result is True
        assert alert_service._client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_trade_entry_alert_format(self, alert_service):
        alert_service._main_token = "token"
        alert_service._main_chat_id = "123"
        mock_response = MagicMock()
        mock_response.status_code = 200
        alert_service._client.post = AsyncMock(return_value=mock_response)

        trade = {
            "direction": "BUY_CALL",
            "strike": 23500,
            "option_type": "CE",
            "entry_premium": 150.0,
            "sl_premium": 120.0,
            "target_premium": 183.0,
            "signal_confidence": 72,
            "verdict": "Slightly Bullish",
        }

        result = await alert_service.send_trade_entry_alert("iron_pulse", trade)
        assert result is True

        msg = alert_service._client.post.call_args[1]["json"]["text"]
        assert "IRON_PULSE" in msg
        assert "23500 CE" in msg
        assert "BUY_CALL" in msg

    @pytest.mark.asyncio
    async def test_trade_exit_alert_format(self, alert_service):
        alert_service._main_token = "token"
        alert_service._main_chat_id = "123"
        mock_response = MagicMock()
        mock_response.status_code = 200
        alert_service._client.post = AsyncMock(return_value=mock_response)

        trade = {"strike": 23500, "option_type": "CE", "entry_premium": 150.0}
        exit_info = {
            "exit_premium": 183.0,
            "pnl_pct": 22.0,
            "reason": "Target hit",
            "action": "WON",
        }

        result = await alert_service.send_trade_exit_alert("iron_pulse", trade, exit_info)
        assert result is True

        msg = alert_service._client.post.call_args[1]["json"]["text"]
        assert "EXIT" in msg
        assert "+22.0%" in msg

    @pytest.mark.asyncio
    async def test_close(self, alert_service):
        alert_service._client = AsyncMock()
        alert_service._client.aclose = AsyncMock()
        await alert_service.close()
        alert_service._client.aclose.assert_called_once()
