"""Tests for strategies/dessert.py — DessertStrategy (no legacy imports)."""

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from strategies.dessert import DessertStrategy
from core.events import EventBus, EventType


@pytest.fixture
def repo():
    """Mock TradeRepository."""
    r = MagicMock()
    r.get_todays_trades.return_value = []
    r.get_active.return_value = None
    return r


@pytest.fixture
def strategy(repo):
    """Create strategy with mock repo."""
    return DessertStrategy(trade_repo=repo)


class TestContraSniper:
    def test_triggers(self):
        analysis = {
            "verdict": "Bulls Winning", "iv_skew": 0.5,
            "spot_price": 24400.0, "max_pain": 24500,
        }
        assert DessertStrategy._check_contra_sniper(analysis) is True

    def test_rejects_non_bullish(self):
        analysis = {"verdict": "Bears Winning", "iv_skew": 0.5,
                     "spot_price": 24400.0, "max_pain": 24500}
        assert DessertStrategy._check_contra_sniper(analysis) is False

    def test_rejects_high_iv_skew(self):
        analysis = {"verdict": "Slightly Bullish", "iv_skew": 1.5,
                     "spot_price": 24400.0, "max_pain": 24500}
        assert DessertStrategy._check_contra_sniper(analysis) is False

    def test_rejects_above_max_pain(self):
        analysis = {"verdict": "Bulls Winning", "iv_skew": 0.5,
                     "spot_price": 24600.0, "max_pain": 24500}
        assert DessertStrategy._check_contra_sniper(analysis) is False


class TestPhantomPut:
    def test_triggers(self):
        analysis = {"signal_confidence": 40.0, "iv_skew": -0.5}
        assert DessertStrategy._check_phantom_put(analysis, 0.1) is True

    def test_rejects_high_confidence(self):
        analysis = {"signal_confidence": 60.0, "iv_skew": -0.5}
        assert DessertStrategy._check_phantom_put(analysis, 0.1) is False

    def test_rejects_positive_iv_skew(self):
        analysis = {"signal_confidence": 40.0, "iv_skew": 0.5}
        assert DessertStrategy._check_phantom_put(analysis, 0.1) is False

    def test_rejects_no_spot_move(self):
        analysis = {"signal_confidence": 40.0, "iv_skew": -0.5}
        assert DessertStrategy._check_phantom_put(analysis, None) is False
        assert DessertStrategy._check_phantom_put(analysis, 0.01) is False


class TestEvaluate:
    def test_contra_sniper_priority(self, strategy):
        analysis = {
            "verdict": "Bulls Winning", "iv_skew": 0.5,
            "spot_price": 24400.0, "max_pain": 24500,
            "signal_confidence": 40.0,
        }
        with patch("core.base_tracker.datetime") as mock_dt, \
             patch.object(DessertStrategy, "_get_spot_move_30m", return_value=0.2):
            mock_dt.now.return_value = datetime(2025, 1, 1, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(analysis)
        assert result == "Contra Sniper"

    def test_already_traded_today(self, strategy, repo):
        repo.get_todays_trades.return_value = [{"id": 1}]
        assert strategy.evaluate({}) is None


class TestCheckAndUpdate:
    def test_sl_hit(self, strategy, repo):
        trade = {
            "id": 1, "strike": 24500, "option_type": "PE",
            "entry_premium": 100.0, "sl_premium": 75.0,
            "target_premium": 150.0, "strategy_name": "Phantom PUT",
            "max_premium_reached": 100.0, "min_premium_reached": 100.0,
        }
        repo.get_active.return_value = trade
        result = strategy.check_and_update({24500: {"pe_ltp": 70.0}})
        assert result["action"] == "LOST"

    def test_target_hit(self, strategy, repo):
        trade = {
            "id": 2, "strike": 24500, "option_type": "PE",
            "entry_premium": 100.0, "sl_premium": 75.0,
            "target_premium": 150.0, "strategy_name": "Contra Sniper",
            "max_premium_reached": 100.0, "min_premium_reached": 100.0,
        }
        repo.get_active.return_value = trade
        result = strategy.check_and_update({24500: {"pe_ltp": 160.0}})
        assert result["action"] == "WON"

    def test_no_active_trade(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.check_and_update({}) is None


class TestCreateTrade:
    def test_creates_and_publishes(self, repo):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))

        strategy = DessertStrategy(trade_repo=repo, bus=bus)
        repo.insert_trade.return_value = 10
        repo._fetch_all.return_value = []  # no spot history

        strikes = {24500: {"pe_ltp": 120.0}}
        analysis = {
            "spot_price": 24480.0, "verdict": "Bulls Winning",
            "signal_confidence": 40.0, "iv_skew": 0.5, "vix": 12.0,
            "max_pain": 24500,
        }

        trade_id = strategy.create_trade("Contra Sniper", analysis, strikes)

        assert trade_id == 10
        repo.insert_trade.assert_called_once()
        assert len(received) == 1
        assert received[0]["trade_id"] == 10

    def test_skips_low_premium(self, strategy, repo):
        strikes = {24500: {"pe_ltp": 2.0}}
        analysis = {"spot_price": 24480.0, "verdict": "x", "signal_confidence": 0}
        result = strategy.create_trade("Contra Sniper", analysis, strikes)
        assert result is None
        repo.insert_trade.assert_not_called()


class TestGetStats:
    def test_delegates_to_repo(self, strategy, repo):
        repo.get_stats.return_value = {"total": 3, "wins": 2}
        stats = strategy.get_stats()
        assert stats["total"] == 3
        repo.get_stats.assert_called_once_with("dessert_trades", 30)

    def test_no_repo_returns_empty(self):
        strategy = DessertStrategy()
        stats = strategy.get_stats()
        assert stats["total"] == 0
