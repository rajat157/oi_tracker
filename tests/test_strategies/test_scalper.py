"""Tests for strategies/scalper.py — ScalperStrategy (full logic, no legacy)."""

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from strategies.scalper import ScalperStrategy
from core.events import EventBus, EventType


@pytest.fixture
def repo():
    r = MagicMock()
    r.get_todays_trades.return_value = []
    r.get_active.return_value = None
    return r


@pytest.fixture
def strategy(repo):
    return ScalperStrategy(trade_repo=repo)


def _analysis(**kw):
    d = {"spot_price": 24500.0, "verdict": "Slightly Bullish",
         "signal_confidence": 70, "vix": 12.0}
    d.update(kw)
    return d


class TestScalperStrategy:
    def test_tracker_type(self, strategy):
        assert strategy.tracker_type == "scalper"
        assert strategy.max_trades_per_day == 5
        assert strategy.is_selling is False

    def test_get_active_delegates(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.get_active() is None
        repo.get_active.assert_called_with("scalp_trades")


class TestShouldCreate:
    def test_valid(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt, \
             patch("strategies.scalper.datetime") as mock_sdt:
            now = datetime(2025, 1, 1, 10, 30)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_sdt.now.return_value = now
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is True

    def test_rejects_before_945(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt, \
             patch("strategies.scalper.datetime") as mock_sdt:
            now = datetime(2025, 1, 1, 9, 40)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_sdt.now.return_value = now
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_rejects_max_trades(self, strategy, repo):
        repo.get_todays_trades.return_value = [{"id": i} for i in range(5)]
        with patch("strategies.scalper.datetime") as mock_sdt:
            mock_sdt.now.return_value = datetime(2025, 1, 1, 10, 30)
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_cooldown_active(self, strategy, repo):
        repo.get_todays_trades.return_value = [{
            "id": 1, "resolved_at": "2025-01-01T10:28:00",
        }]
        with patch("core.base_tracker.datetime") as mock_dt, \
             patch("strategies.scalper.datetime") as mock_sdt:
            now = datetime(2025, 1, 1, 10, 30)  # only 2 min after resolve
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_sdt.now.return_value = now
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_sdt.fromisoformat = datetime.fromisoformat
            assert strategy.should_create(_analysis()) is False

    def test_rejects_no_spot(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt, \
             patch("strategies.scalper.datetime") as mock_sdt:
            now = datetime(2025, 1, 1, 10, 30)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_sdt.now.return_value = now
            mock_sdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis(spot_price=0)) is False


class TestCreateTrade:
    def _signal(self, **overrides):
        s = {
            "action": "BUY", "option_type": "CE", "strike": 24400,
            "entry_premium": 200.0, "sl_premium": 180.0,
            "target_premium": 220.0, "confidence": 75,
            "reasoning": "VWAP breakout confirmed",
            "_candles": [{"iv": 15.0}], "_vwap": 198.5,
        }
        s.update(overrides)
        return s

    def test_creates_trade(self, repo):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))

        strategy = ScalperStrategy(trade_repo=repo, bus=bus)
        repo.insert_trade.return_value = 99

        trade_id = strategy.create_trade(self._signal(), _analysis(), {})

        assert trade_id == 99
        repo.insert_trade.assert_called_once()
        call_kw = repo.insert_trade.call_args[1]
        assert call_kw["direction"] == "BUY_CE"
        assert call_kw["strike"] == 24400
        assert call_kw["agent_reasoning"] == "VWAP breakout confirmed"
        assert len(received) == 1

    def test_skips_low_confidence(self, strategy, repo):
        result = strategy.create_trade(
            self._signal(confidence=40), _analysis(), {})
        assert result is None
        repo.insert_trade.assert_not_called()

    def test_skips_low_premium(self, strategy, repo):
        result = strategy.create_trade(
            self._signal(entry_premium=10.0), _analysis(), {})
        assert result is None

    def test_caps_sl(self, strategy, repo):
        repo.insert_trade.return_value = 1
        repo.get_todays_trades.return_value = []
        # SL is 30% away — should be capped to MAX_SL_PCT (15%)
        strategy.create_trade(
            self._signal(entry_premium=200.0, sl_premium=140.0),
            _analysis(), {})
        call_kw = repo.insert_trade.call_args[1]
        assert call_kw["sl_premium"] == 170.0  # 200 * (1 - 0.15)


class TestCheckAndUpdate:
    def _trade(self, **overrides):
        t = {
            "id": 1, "strike": 24400, "option_type": "CE",
            "direction": "BUY_CE", "created_at": "2025-01-01T10:00:00",
            "entry_premium": 200.0, "sl_premium": 180.0,
            "target_premium": 220.0, "trade_number": 1,
            "max_premium_reached": 200.0, "min_premium_reached": 200.0,
        }
        t.update(overrides)
        return t

    def test_sl_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        result = strategy.check_and_update({24400: {"ce_ltp": 175.0}})
        assert result["action"] == "LOST"
        assert result["reason"] == "SL"

    def test_target_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        result = strategy.check_and_update({24400: {"ce_ltp": 225.0}})
        assert result["action"] == "WON"
        assert result["reason"] == "TARGET"

    def test_no_active(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.check_and_update({}) is None


class TestGetStats:
    def test_delegates_to_repo(self, strategy, repo):
        repo.get_stats.return_value = {"total": 20, "wins": 10}
        stats = strategy.get_stats()
        assert stats["total"] == 20
        repo.get_stats.assert_called_once_with("scalp_trades", 30)
