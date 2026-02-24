"""Tests for Premium Monitor — real-time WebSocket trade watcher."""

import pytest
from unittest.mock import patch, MagicMock, call
from dataclasses import dataclass


@dataclass
class MockActiveTrade:
    """Mimics ActiveTrade for testing."""
    trade_id: int
    tracker_type: str  # "iron_pulse", "selling", "dessert", "momentum"
    strike: int
    option_type: str
    instrument_token: int
    entry_premium: float
    sl_premium: float
    target_premium: float
    is_selling: bool = False


class TestSLTargetDetection:
    """Test SL/target hit detection logic."""

    def test_buying_sl_hit(self):
        """Buying trade: premium drops below SL → LOST."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0,
            sl_premium=80.0,    # -20% SL
            target_premium=122.0,  # +22% target
            is_selling=False,
        )

        result = monitor._check_exit(trade, current_premium=75.0)
        assert result is not None
        assert result["action"] == "LOST"

    def test_buying_target_hit(self):
        """Buying trade: premium rises above target → WON."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0,
            sl_premium=80.0,
            target_premium=122.0,
            is_selling=False,
        )

        result = monitor._check_exit(trade, current_premium=130.0)
        assert result is not None
        assert result["action"] == "WON"

    def test_selling_sl_hit(self):
        """Selling trade: premium RISES above SL → LOST (inverted)."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=2, tracker_type="selling",
            strike=23000, option_type="PE",
            instrument_token=2000,
            entry_premium=100.0,
            sl_premium=125.0,     # +25% rise = SL for seller
            target_premium=50.0,  # -50% drop = target for seller
            is_selling=True,
        )

        result = monitor._check_exit(trade, current_premium=130.0)
        assert result is not None
        assert result["action"] == "LOST"

    def test_selling_target_hit(self):
        """Selling trade: premium DROPS below target → WON (inverted)."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=2, tracker_type="selling",
            strike=23000, option_type="PE",
            instrument_token=2000,
            entry_premium=100.0,
            sl_premium=125.0,
            target_premium=50.0,
            is_selling=True,
        )

        result = monitor._check_exit(trade, current_premium=45.0)
        assert result is not None
        assert result["action"] == "WON"

    def test_no_action_in_range(self):
        """Premium between SL and target → no exit."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0,
            sl_premium=80.0,
            target_premium=122.0,
            is_selling=False,
        )

        result = monitor._check_exit(trade, current_premium=100.0)
        assert result is None

    def test_selling_no_action_in_range(self):
        """Selling: premium between entry and SL → no exit."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=2, tracker_type="selling",
            strike=23000, option_type="PE",
            instrument_token=2000,
            entry_premium=100.0,
            sl_premium=125.0,
            target_premium=50.0,
            is_selling=True,
        )

        result = monitor._check_exit(trade, current_premium=90.0)
        assert result is None


class TestShadowMode:
    """Test shadow mode behavior."""

    def test_shadow_mode_logs_only(self):
        """In shadow mode, detection should log but NOT call exit_callback."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        callback = MagicMock()
        monitor.set_exit_callback(callback)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0, sl_premium=80.0,
            target_premium=122.0, is_selling=False,
        )
        monitor.register_trade(trade)

        # Simulate tick that hits SL
        monitor._on_tick_received(trade.instrument_token, 75.0)

        # Callback should NOT be called in shadow mode
        callback.assert_not_called()

    def test_live_mode_calls_callback(self):
        """In live mode, detection should call exit_callback."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=False)

        callback = MagicMock()
        monitor.set_exit_callback(callback)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0, sl_premium=80.0,
            target_premium=122.0, is_selling=False,
        )
        monitor.register_trade(trade)

        # Simulate tick that hits target
        monitor._on_tick_received(trade.instrument_token, 130.0)

        # Callback SHOULD be called in live mode
        callback.assert_called_once()
        call_args = callback.call_args[0][0]
        assert call_args["trade_id"] == 1
        assert call_args["action"] == "WON"


class TestRegistration:
    """Test trade registration and unregistration."""

    def test_register_subscribes_instrument(self):
        """Registering a trade should add it to active tracking."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0, sl_premium=80.0,
            target_premium=122.0, is_selling=False,
        )
        monitor.register_trade(trade)

        assert 1000 in monitor._token_to_trades
        assert len(monitor._token_to_trades[1000]) == 1

    def test_unregister_removes_trade(self):
        """Unregistering should remove trade from tracking."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0, sl_premium=80.0,
            target_premium=122.0, is_selling=False,
        )
        monitor.register_trade(trade)
        monitor.unregister_trade(trade_id=1)

        # Token mapping should be empty or removed
        trades_for_token = monitor._token_to_trades.get(1000, [])
        assert len(trades_for_token) == 0

    def test_multiple_trades_same_token(self):
        """Multiple trades on same instrument should coexist."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade1 = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0, sl_premium=80.0,
            target_premium=122.0, is_selling=False,
        )
        trade2 = MockActiveTrade(
            trade_id=2, tracker_type="dessert",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=90.0, sl_premium=67.5,
            target_premium=135.0, is_selling=False,
        )
        monitor.register_trade(trade1)
        monitor.register_trade(trade2)

        assert len(monitor._token_to_trades[1000]) == 2


class TestGTTPolling:
    """Test GTT status polling."""

    def test_gtt_triggered_detected(self):
        """GTT poll should detect externally-triggered GTT."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0, sl_premium=80.0,
            target_premium=122.0, is_selling=False,
        )
        monitor.register_trade(trade)

        # Store GTT trigger ID
        monitor._trade_gtt_ids[1] = 12345

        # Mock Kite API: GTT status is "triggered"
        mock_kite = MagicMock()
        mock_kite.get_gtt.return_value = {
            "id": 12345,
            "status": "triggered",
            "orders": [{"result": {"price": 78.0}}],
        }
        monitor._kite = mock_kite

        callback = MagicMock()
        monitor.set_exit_callback(callback)

        # Poll GTT status
        monitor.poll_gtt_status()

        # In shadow mode, should NOT call callback but should detect
        callback.assert_not_called()


class TestScanExistingTrades:
    """Test startup scan for active trades."""

    @patch('premium_monitor.get_active_trade_setup')
    @patch('premium_monitor.get_active_sell_setup')
    @patch('premium_monitor.get_active_dessert')
    @patch('premium_monitor.get_active_momentum')
    def test_scan_existing_active_trades(self, mock_momentum, mock_dessert,
                                          mock_sell, mock_buying):
        """On startup, should pick up ACTIVE trades from all trackers."""
        from premium_monitor import PremiumMonitor

        # Mock an active buying trade
        mock_buying.return_value = {
            'id': 1, 'status': 'ACTIVE',
            'strike': 23000, 'option_type': 'CE',
            'entry_premium': 100.0, 'sl_premium': 80.0,
            'target1_premium': 122.0, 'target2_premium': None,
        }
        mock_sell.return_value = None
        mock_dessert.return_value = None
        mock_momentum.return_value = None

        monitor = PremiumMonitor(shadow_mode=True)

        # Mock instrument map to provide token
        monitor._instrument_map = MagicMock()
        monitor._instrument_map.get_current_expiry.return_value = "2026-02-27"
        mock_inst = {'instrument_token': 5000, 'tradingsymbol': 'NIFTY2622723000CE'}
        monitor._instrument_map.get_option_instrument.return_value = mock_inst

        monitor.scan_existing_trades()

        # Should have registered the trade
        assert 5000 in monitor._token_to_trades


class TestGetStatus:
    """Test status reporting."""

    def test_get_status_empty(self):
        """Status with no trades."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)
        status = monitor.get_status()
        assert status["shadow_mode"] is True
        assert status["active_trades"] == 0

    def test_get_status_with_trades(self):
        """Status with registered trades."""
        from premium_monitor import PremiumMonitor
        monitor = PremiumMonitor(shadow_mode=True)

        trade = MockActiveTrade(
            trade_id=1, tracker_type="iron_pulse",
            strike=23000, option_type="CE",
            instrument_token=1000,
            entry_premium=100.0, sl_premium=80.0,
            target_premium=122.0, is_selling=False,
        )
        monitor.register_trade(trade)

        status = monitor.get_status()
        assert status["active_trades"] == 1
