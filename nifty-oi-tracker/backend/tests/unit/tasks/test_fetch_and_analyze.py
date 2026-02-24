"""Tests for fetch_and_analyze task."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.fetch_and_analyze import fetch_and_analyze

IST = timezone(timedelta(hours=5, minutes=30))


def _make_services(market_open=True):
    """Build a mock services dict for testing."""
    scheduler = MagicMock()
    scheduler.is_market_open.return_value = market_open

    market_data = AsyncMock()
    market_data.fetch_option_chain = AsyncMock(return_value={
        "spot_price": 23530.0,
        "expiry_dates": ["2026-03-26"],
        "current_expiry": "2026-03-26",
        "strikes": {
            23500: {
                "ce_oi": 500000, "ce_oi_change": 1000, "ce_volume": 10000,
                "ce_iv": 15.0, "ce_ltp": 120.0,
                "pe_oi": 400000, "pe_oi_change": -500, "pe_volume": 8000,
                "pe_iv": 14.0, "pe_ltp": 80.0,
            },
        },
    })
    market_data.fetch_india_vix = AsyncMock(return_value=14.5)
    market_data.fetch_futures_data = AsyncMock(return_value={
        "future_price": 23580.0, "future_oi": 1200000,
        "basis": 50.0, "basis_pct": 0.21,
    })

    alert = AsyncMock()
    event_bus = AsyncMock()

    # Mock session factory
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock()

    class MockFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    premium_monitor = MagicMock()
    instruments = MagicMock()
    instruments.get_instrument_token.return_value = 1234

    return {
        "scheduler": scheduler,
        "market_data": market_data,
        "alert": alert,
        "event_bus": event_bus,
        "session_factory": MockFactory(),
        "premium_monitor": premium_monitor,
        "instruments": instruments,
        "strategies": {},  # No strategies for basic test
    }


class TestFetchAndAnalyze:
    @pytest.mark.asyncio
    async def test_skips_when_market_closed(self):
        services = _make_services(market_open=False)
        result = await fetch_and_analyze(services)
        assert result is None
        services["market_data"].fetch_option_chain.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_option_chain(self):
        services = _make_services(market_open=True)
        services["market_data"].fetch_option_chain = AsyncMock(return_value=None)
        result = await fetch_and_analyze(services)
        assert result is None

    @pytest.mark.asyncio
    async def test_full_cycle_publishes_event(self):
        services = _make_services(market_open=True)

        with patch("app.services.analysis_service.AnalysisService") as MockAS, \
             patch("app.services.trade_service.TradeService") as MockTS:
            mock_as = AsyncMock()
            mock_as.get_prev_verdict = AsyncMock(return_value=None)
            mock_as.save_snapshots = AsyncMock()
            mock_as.save_analysis = AsyncMock(return_value=1)
            MockAS.return_value = mock_as

            mock_ts = AsyncMock()
            mock_ts.get_active_trade = AsyncMock(return_value=None)
            mock_ts.has_traded_today = AsyncMock(return_value=True)
            MockTS.return_value = mock_ts

            result = await fetch_and_analyze(services)

        assert result is not None
        assert "verdict" in result
        assert "spot_price" in result
        services["event_bus"].publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_market_data_error(self):
        services = _make_services(market_open=True)
        services["market_data"].fetch_option_chain = AsyncMock(
            side_effect=Exception("API error")
        )
        result = await fetch_and_analyze(services)
        assert result is None
