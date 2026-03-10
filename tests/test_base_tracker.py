"""Tests for core/base_tracker.py — ABC and shared helpers."""

from datetime import datetime, time
from typing import Dict, Optional
from unittest.mock import MagicMock

import pytest

from core.base_tracker import BaseTracker
from core.events import EventBus, EventType


class DummyTracker(BaseTracker):
    """Minimal concrete subclass for testing shared helpers."""
    tracker_type = "dummy"
    table_name = "dummy_trades"
    time_start = time(11, 0)
    time_end = time(14, 0)
    force_close_time = time(15, 20)
    max_trades_per_day = 1

    def should_create(self, analysis, **kwargs):
        return True

    def create_trade(self, signal, analysis, strikes_data, **kwargs):
        return 1

    def check_and_update(self, strikes_data, **kwargs):
        return None

    def get_active(self):
        return None

    def get_stats(self, lookback_days=30):
        return {"total": 0}


class TestBaseTrackerABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            BaseTracker()

    def test_concrete_subclass_works(self):
        t = DummyTracker()
        assert t.tracker_type == "dummy"


class TestTimeWindow:
    def test_in_window(self):
        t = DummyTracker()
        dt = datetime(2025, 1, 1, 12, 30)
        assert t.is_in_time_window(dt) is True

    def test_before_window(self):
        t = DummyTracker()
        dt = datetime(2025, 1, 1, 10, 0)
        assert t.is_in_time_window(dt) is False

    def test_after_window(self):
        t = DummyTracker()
        dt = datetime(2025, 1, 1, 14, 30)
        assert t.is_in_time_window(dt) is False

    def test_at_boundary(self):
        t = DummyTracker()
        assert t.is_in_time_window(datetime(2025, 1, 1, 11, 0)) is True
        assert t.is_in_time_window(datetime(2025, 1, 1, 14, 0)) is True


class TestForceClose:
    def test_not_past(self):
        t = DummyTracker()
        assert t.is_past_force_close(datetime(2025, 1, 1, 14, 0)) is False

    def test_past(self):
        t = DummyTracker()
        assert t.is_past_force_close(datetime(2025, 1, 1, 15, 21)) is True

    def test_exactly_at(self):
        t = DummyTracker()
        assert t.is_past_force_close(datetime(2025, 1, 1, 15, 20)) is True


class TestCalculatePnl:
    def test_buying_profit(self):
        assert BaseTracker.calculate_pnl(100.0, 122.0) == 22.0

    def test_buying_loss(self):
        assert BaseTracker.calculate_pnl(100.0, 80.0) == -20.0

    def test_selling_profit(self):
        # Selling profits when premium drops
        assert BaseTracker.calculate_pnl(100.0, 75.0, is_selling=True) == 25.0

    def test_selling_loss(self):
        # Selling loses when premium rises
        assert BaseTracker.calculate_pnl(100.0, 125.0, is_selling=True) == -25.0

    def test_zero_entry(self):
        assert BaseTracker.calculate_pnl(0.0, 50.0) == 0.0


class TestGetCurrentPremium:
    def test_ce(self):
        strikes = {"24500": {"ce_ltp": 150.0, "pe_ltp": 80.0}}
        assert DummyTracker.get_current_premium(strikes, 24500, "CE") == 150.0

    def test_pe(self):
        strikes = {"24500": {"ce_ltp": 150.0, "pe_ltp": 80.0}}
        assert DummyTracker.get_current_premium(strikes, 24500, "PE") == 80.0

    def test_int_key(self):
        strikes = {24500: {"ce_ltp": 150.0, "pe_ltp": 80.0}}
        assert DummyTracker.get_current_premium(strikes, 24500, "CE") == 150.0

    def test_missing_strike(self):
        assert DummyTracker.get_current_premium({}, 99999, "CE") is None


class TestAlreadyTradedToday:
    def test_no_repo(self):
        t = DummyTracker()
        assert t.already_traded_today() is False

    def test_under_limit(self):
        repo = MagicMock()
        repo.get_todays_trades.return_value = []
        t = DummyTracker(trade_repo=repo)
        assert t.already_traded_today() is False

    def test_at_limit(self):
        repo = MagicMock()
        repo.get_todays_trades.return_value = [{"id": 1}]
        t = DummyTracker(trade_repo=repo)
        assert t.already_traded_today() is True


class TestEventPublishing:
    def test_publish_injects_tracker_type(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))

        t = DummyTracker(bus=bus)
        t._publish(EventType.TRADE_CREATED, {"id": 42})

        assert received[0]["tracker_type"] == "dummy"
        assert received[0]["id"] == 42


class TestForceExit:
    def test_force_exit_updates_db_and_publishes(self):
        bus = EventBus()
        repo = MagicMock()
        received = []
        bus.subscribe(EventType.TRADE_EXITED, lambda et, d: received.append(d))

        t = DummyTracker(trade_repo=repo, bus=bus)
        t.force_exit(trade_id=99, exit_premium=180.0, reason="WS_SL", pnl_pct=-20.5)

        # DB should be updated
        repo.update_trade.assert_called_once()
        call_args = repo.update_trade.call_args
        assert call_args[0][0] == "dummy_trades"  # table_name
        assert call_args[0][1] == 99  # trade_id
        assert call_args[1]["status"] == "LOST"
        assert call_args[1]["exit_premium"] == 180.0
        assert call_args[1]["exit_reason"] == "WS_SL"
        assert call_args[1]["profit_loss_pct"] == -20.5

        # Event should be published
        assert len(received) == 1
        assert received[0]["trade_id"] == 99
        assert received[0]["action"] == "LOST"
        assert received[0]["tracker_type"] == "dummy"

    def test_force_exit_winning_trade(self):
        bus = EventBus()
        repo = MagicMock()
        received = []
        bus.subscribe(EventType.TRADE_EXITED, lambda et, d: received.append(d))

        t = DummyTracker(trade_repo=repo, bus=bus)
        t.force_exit(trade_id=50, exit_premium=200.0, reason="WS_TARGET", pnl_pct=22.0)

        call_args = repo.update_trade.call_args
        assert call_args[1]["status"] == "WON"
        assert received[0]["action"] == "WON"

    def test_force_exit_no_repo(self):
        t = DummyTracker()
        # Should not raise even without trade_repo
        t.force_exit(trade_id=1, exit_premium=100.0, reason="test", pnl_pct=-10.0)
