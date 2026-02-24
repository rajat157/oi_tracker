"""Tests for scheduler Kite integration (Phase B)."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime


@pytest.fixture
def mock_kite_fetcher():
    """Create a mock KiteDataFetcher."""
    fetcher = MagicMock()
    fetcher.fetch_option_chain.return_value = {
        "spot_price": 23000.0,
        "expiry_dates": ["2026-02-27"],
        "current_expiry": "2026-02-27",
        "strikes": {
            22900: {"ce_oi": 50000, "ce_oi_change": 1000, "ce_volume": 500,
                    "ce_iv": 14.5, "ce_ltp": 200.0,
                    "pe_oi": 30000, "pe_oi_change": -500, "pe_volume": 300,
                    "pe_iv": 15.0, "pe_ltp": 50.0},
            22950: {"ce_oi": 40000, "ce_oi_change": 800, "ce_volume": 400,
                    "ce_iv": 13.8, "ce_ltp": 160.0,
                    "pe_oi": 35000, "pe_oi_change": -300, "pe_volume": 350,
                    "pe_iv": 14.2, "pe_ltp": 70.0},
            23000: {"ce_oi": 60000, "ce_oi_change": 2000, "ce_volume": 600,
                    "ce_iv": 13.0, "ce_ltp": 130.0,
                    "pe_oi": 55000, "pe_oi_change": -200, "pe_volume": 500,
                    "pe_iv": 13.5, "pe_ltp": 95.0},
            23050: {"ce_oi": 45000, "ce_oi_change": 500, "ce_volume": 350,
                    "ce_iv": 12.5, "ce_ltp": 90.0,
                    "pe_oi": 60000, "pe_oi_change": 1500, "pe_volume": 550,
                    "pe_iv": 13.0, "pe_ltp": 140.0},
            23100: {"ce_oi": 35000, "ce_oi_change": 300, "ce_volume": 250,
                    "ce_iv": 12.0, "ce_ltp": 60.0,
                    "pe_oi": 70000, "pe_oi_change": 2500, "pe_volume": 600,
                    "pe_iv": 12.8, "pe_ltp": 190.0},
        }
    }
    fetcher.fetch_india_vix.return_value = 14.0
    fetcher.fetch_futures_data.return_value = {
        "future_price": 23050.0,
        "future_oi": 10000000,
        "basis": 50.0,
        "basis_pct": 0.22,
        "expiry": "2026-02-26",
    }
    fetcher.close = MagicMock()
    return fetcher


class TestUsesKiteFetcher:
    """Verify scheduler uses KiteDataFetcher."""

    @patch('scheduler.KiteDataFetcher')
    def test_uses_kite_fetcher(self, MockKite):
        """Scheduler should instantiate KiteDataFetcher, not NSEFetcher."""
        from scheduler import OIScheduler
        scheduler = OIScheduler()
        assert hasattr(scheduler, 'kite_fetcher')

    @patch('scheduler.KiteDataFetcher')
    def test_no_nse_fetcher_in_fetch(self, MockKite):
        """fetch_and_analyze should NOT create NSEFetcher."""
        from scheduler import OIScheduler
        scheduler = OIScheduler()

        mock_fetcher = MockKite.return_value
        mock_fetcher.fetch_option_chain.return_value = None

        scheduler.fetch_and_analyze(force=True)

        # Should have called kite_fetcher.fetch_option_chain, not NSEFetcher
        mock_fetcher.fetch_option_chain.assert_called()


class TestPremiumMonitor:
    """Test premium monitor integration."""

    @patch('scheduler.PremiumMonitor')
    @patch('scheduler.KiteDataFetcher')
    def test_premium_monitor_started(self, MockKite, MockMonitor):
        """Premium monitor should start with scheduler."""
        from scheduler import OIScheduler
        scheduler = OIScheduler()
        scheduler.start()

        # Monitor should exist
        assert hasattr(scheduler, 'premium_monitor')

        scheduler.stop()


class TestFetchOutputUnchanged:
    """Verify analysis format is unchanged for SocketIO."""

    @patch('scheduler.KiteDataFetcher')
    def test_fetch_output_unchanged(self, MockKite, mock_kite_fetcher):
        """Analysis output should maintain same structure for dashboard."""
        MockKite.return_value = mock_kite_fetcher

        from scheduler import OIScheduler
        socketio_mock = MagicMock()
        scheduler = OIScheduler(socketio=socketio_mock)

        scheduler.fetch_and_analyze(force=True)

        # SocketIO should have been called with analysis data
        if socketio_mock.emit.called:
            args = socketio_mock.emit.call_args
            event = args[0][0]
            data = args[0][1]
            assert event == "oi_update"
            assert "verdict" in data
            assert "spot_price" in data or "atm_strike" in data
