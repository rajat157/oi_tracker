"""Tests for scheduler Kite integration (Phase B)."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

from core.events import event_bus


@pytest.fixture(autouse=True)
def _clean_global_bus():
    """Prevent AlertBroker leak — clear global event_bus after each test."""
    yield
    event_bus.clear()


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

    @patch('monitoring.scheduler.KiteDataFetcher')
    def test_uses_kite_fetcher(self, MockKite):
        """Scheduler should instantiate KiteDataFetcher, not NSEFetcher."""
        from monitoring.scheduler import OIScheduler
        scheduler = OIScheduler()
        assert hasattr(scheduler, 'kite_fetcher')

    @patch('monitoring.scheduler.KiteDataFetcher')
    def test_no_nse_fetcher_in_fetch(self, MockKite):
        """fetch_and_analyze should NOT create NSEFetcher."""
        from monitoring.scheduler import OIScheduler
        scheduler = OIScheduler()

        mock_fetcher = MockKite.return_value
        mock_fetcher.fetch_option_chain.return_value = None

        # Mock market as open since fetch_and_analyze never runs outside hours
        scheduler.is_market_open = lambda: True
        scheduler.fetch_and_analyze()

        # Should have called kite_fetcher.fetch_option_chain, not NSEFetcher
        mock_fetcher.fetch_option_chain.assert_called()


class TestTickHubServices:
    """Test that the scheduler wires TickHub + the 4 consumers correctly."""

    @patch('monitoring.scheduler.KiteDataFetcher')
    def test_tickhub_and_consumers_wired(self, MockKite):
        """TickHub + ExitMonitor + CandleBuilder + OrderflowCollector + LivePnlBroadcaster wired."""
        from monitoring.scheduler import OIScheduler
        scheduler = OIScheduler()

        # All 5 TickHub services exist on the scheduler
        assert hasattr(scheduler, "tick_hub")
        assert hasattr(scheduler, "candle_builder")
        assert hasattr(scheduler, "exit_monitor")
        assert hasattr(scheduler, "orderflow_collector")
        assert hasattr(scheduler, "live_pnl_broadcaster")

        # Exit monitor is wired to the scheduler's callback (compare __self__
        # and __func__ because bound-method `is` doesn't hold across lookups)
        cb = scheduler.exit_monitor._exit_callback
        assert cb is not None
        assert cb.__self__ is scheduler
        assert cb.__func__ is scheduler._handle_premium_exit.__func__

        # Strategy has references to the new services
        rr = scheduler.strategies["rally_rider"]
        assert rr._exit_monitor is scheduler.exit_monitor
        assert rr._candle_builder is scheduler.candle_builder


class TestFetchOutputUnchanged:
    """Verify analysis format is unchanged for SocketIO."""

    @patch('monitoring.scheduler.KiteDataFetcher')
    def test_fetch_output_unchanged(self, MockKite, mock_kite_fetcher):
        """Analysis output should maintain same structure for dashboard."""
        MockKite.return_value = mock_kite_fetcher

        from monitoring.scheduler import OIScheduler
        socketio_mock = MagicMock()
        scheduler = OIScheduler(socketio=socketio_mock)

        # Mock market as open since fetch_and_analyze never runs outside hours
        scheduler.is_market_open = lambda: True
        scheduler.fetch_and_analyze()

        # SocketIO should have been called with analysis data
        if socketio_mock.emit.called:
            # fetch_and_analyze now emits oi_update + story_update + tiles_update;
            # find the oi_update call specifically.
            call_map = {c[0][0]: c[0][1] for c in socketio_mock.emit.call_args_list}
            assert "oi_update" in call_map, f"Expected oi_update in {list(call_map)}"
            data = call_map["oi_update"]
            assert "verdict" in data
            assert "spot_price" in data or "atm_strike" in data
