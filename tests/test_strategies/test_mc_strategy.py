"""Tests for strategies/mc_strategy.py — MCStrategy (mechanical rally catcher)."""

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from strategies.mc_strategy import MCStrategy
from core.events import EventBus, EventType


@pytest.fixture
def repo():
    r = MagicMock()
    r.get_todays_trades.return_value = []
    r.get_active.return_value = None
    return r


@pytest.fixture
def strategy(repo):
    return MCStrategy(trade_repo=repo)


def _analysis(**kw):
    d = {"spot_price": 24500.0, "verdict": "Slightly Bullish",
         "signal_confidence": 70, "vix": 12.0}
    d.update(kw)
    return d


class TestMCStrategy:
    def test_tracker_type(self, strategy):
        assert strategy.tracker_type == "mc"
        assert strategy.table_name == "mc_trades"
        assert strategy.max_trades_per_day == 1
        assert strategy.is_selling is False

    def test_get_active_delegates(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.get_active() is None
        repo.get_active.assert_called_with("mc_trades")


class TestShouldCreate:
    def test_valid(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt, \
             patch("strategies.mc_strategy.datetime") as mock_sdt:
            now = datetime(2025, 1, 1, 11, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_sdt.now.return_value = now
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is True

    def test_rejects_before_10(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt, \
             patch("strategies.mc_strategy.datetime") as mock_sdt:
            now = datetime(2025, 1, 1, 9, 45)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_sdt.now.return_value = now
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_rejects_max_trades(self, strategy, repo):
        repo.get_todays_trades.return_value = [{"id": 1}]
        with patch("strategies.mc_strategy.datetime") as mock_sdt:
            mock_sdt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_rejects_no_spot(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt, \
             patch("strategies.mc_strategy.datetime") as mock_sdt:
            now = datetime(2025, 1, 1, 11, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_sdt.now.return_value = now
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis(spot_price=0)) is False


class TestCreateTrade:
    def _signal(self, **overrides):
        s = {
            "signal_type": "MC",
            "direction": "BUY_CE", "option_type": "CE", "strike": 24400,
            "entry_premium": 200.0, "sl_premium": 170.0,
            "target_premium": 216.0,
            "signal_data": {"rally_pts": 35.0, "pullback_pct": 0.4,
                            "weekly_trend": "UP", "day_open": 24450.0},
        }
        s.update(overrides)
        return s

    def test_creates_trade(self, repo):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))

        strategy = MCStrategy(trade_repo=repo, bus=bus)
        repo.insert_trade.return_value = 42

        trade_id = strategy.create_trade(self._signal(), _analysis(), {})

        assert trade_id == 42
        repo.insert_trade.assert_called_once()
        call_kw = repo.insert_trade.call_args[1]
        assert call_kw["direction"] == "BUY_CE"
        assert call_kw["strike"] == 24400
        assert call_kw["signal_type"] == "MC"
        assert call_kw["trail_stage"] == 0
        assert len(received) == 1

    def test_skips_low_premium(self, strategy, repo):
        result = strategy.create_trade(
            self._signal(entry_premium=50.0), _analysis(), {})
        assert result is None
        repo.insert_trade.assert_not_called()


class TestCheckAndUpdate:
    def _trade(self, **overrides):
        t = {
            "id": 1, "strike": 24400, "option_type": "CE",
            "direction": "BUY_CE", "created_at": "2025-01-01T10:00:00",
            "entry_premium": 200.0, "sl_premium": 170.0,
            "target_premium": 216.0, "trade_number": 1,
            "max_premium_reached": 200.0, "min_premium_reached": 200.0,
            "trail_stage": 0,
        }
        t.update(overrides)
        return t

    def test_sl_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        result = strategy.check_and_update({24400: {"ce_ltp": 165.0}})
        assert result["action"] == "LOST"
        assert result["reason"] == "SL"

    def test_target_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        result = strategy.check_and_update({24400: {"ce_ltp": 220.0}})
        assert result["action"] == "WON"
        assert result["reason"] == "TARGET"

    def test_trailing_stop_stage_1(self, strategy, repo):
        # Premium at +12% from entry → trail stage 1, SL moves to +4%
        repo.get_active.return_value = self._trade()
        result = strategy.check_and_update({24400: {"ce_ltp": 224.0}})
        # +12% > 10% trigger but < target → no exit, trail updated
        # Check update was called with trail_stage=1
        updates = repo.update_trade.call_args_list
        # First update call should have trail_stage
        found_trail = False
        for call in updates:
            kw = call[1] if len(call) > 1 else call.kwargs
            if kw.get("trail_stage") == 1:
                found_trail = True
                assert kw["sl_premium"] == 208.0  # 200 * 1.04
                break
        assert found_trail

    def test_no_active(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.check_and_update({}) is None


class TestGetStats:
    def test_delegates_to_repo(self, strategy, repo):
        repo.get_stats.return_value = {"total": 5, "wins": 3}
        stats = strategy.get_stats()
        assert stats["total"] == 5
        repo.get_stats.assert_called_once_with("mc_trades", 30)
