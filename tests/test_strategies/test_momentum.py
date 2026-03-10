"""Tests for strategies/momentum.py — MomentumStrategy (no legacy imports)."""

import json
from datetime import datetime, time
from unittest.mock import patch, MagicMock

import pytest

from strategies.momentum import MomentumStrategy
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
    return MomentumStrategy(trade_repo=repo)


def _make_analysis(verdict="Bulls Winning", confidence=90.0,
                   confirmation="CONFIRMED", **extras):
    aj = json.dumps({"confirmation_status": confirmation, "combined_score": 42.0})
    d = {
        "verdict": verdict,
        "signal_confidence": confidence,
        "spot_price": 24500.0,
        "iv_skew": 0.5,
        "vix": 12.0,
        "analysis_json": aj,
    }
    d.update(extras)
    return d


class TestShouldCreate:
    def test_bullish_confirmed(self, strategy):
        now = datetime(2025, 1, 1, 12, 30)
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(_make_analysis())
        assert result == "BUY_CALL"

    def test_bearish_confirmed(self, strategy):
        now = datetime(2025, 1, 1, 13, 0)
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(
                _make_analysis(verdict="Bears Winning"))
        assert result == "BUY_PUT"

    def test_low_confidence_rejected(self, strategy):
        now = datetime(2025, 1, 1, 12, 30)
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(_make_analysis(confidence=70.0))
        assert result is None

    def test_not_confirmed_rejected(self, strategy):
        now = datetime(2025, 1, 1, 12, 30)
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(
                _make_analysis(confirmation="NEUTRAL"))
        assert result is None

    def test_already_traded_today(self, strategy, repo):
        repo.get_todays_trades.return_value = [{"id": 1}]
        result = strategy.evaluate(_make_analysis())
        assert result is None

    def test_slightly_verdicts_rejected(self, strategy):
        now = datetime(2025, 1, 1, 12, 30)
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(
                _make_analysis(verdict="Slightly Bullish"))
        assert result is None


class TestGetConfirmation:
    def test_from_json_string(self):
        analysis = {"analysis_json": json.dumps({"confirmation_status": "CONFIRMED"})}
        assert MomentumStrategy._get_confirmation(analysis) == "CONFIRMED"

    def test_from_dict(self):
        analysis = {"analysis_json": {"confirmation_status": "REVERSAL_ALERT"}}
        assert MomentumStrategy._get_confirmation(analysis) == "REVERSAL_ALERT"

    def test_fallback_to_direct(self):
        analysis = {"confirmation_status": "NEUTRAL", "analysis_json": ""}
        assert MomentumStrategy._get_confirmation(analysis) == "NEUTRAL"

    def test_empty(self):
        assert MomentumStrategy._get_confirmation({}) == ""


class TestCheckAndUpdate:
    def test_sl_hit(self, strategy, repo):
        trade = {
            "id": 1, "strike": 24500, "option_type": "CE",
            "entry_premium": 100.0, "sl_premium": 75.0, "target_premium": 150.0,
            "max_premium_reached": 100.0, "min_premium_reached": 100.0,
        }
        repo.get_active.return_value = trade
        strikes = {24500: {"ce_ltp": 70.0}}

        result = strategy.check_and_update(strikes)

        assert result["action"] == "LOST"
        assert result["reason"] == "SL"
        assert result["pnl"] < 0
        # Verify DB was updated
        assert repo.update_trade.call_count == 2  # tracking + resolve

    def test_target_hit(self, strategy, repo):
        trade = {
            "id": 2, "strike": 24500, "option_type": "PE",
            "entry_premium": 100.0, "sl_premium": 75.0, "target_premium": 150.0,
            "max_premium_reached": 100.0, "min_premium_reached": 100.0,
        }
        repo.get_active.return_value = trade
        strikes = {24500: {"pe_ltp": 155.0}}

        result = strategy.check_and_update(strikes)

        assert result["action"] == "WON"
        assert result["reason"] == "TARGET"

    def test_no_active_trade(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.check_and_update({}) is None


class TestCreateTrade:
    def test_creates_and_publishes(self, repo):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))

        strategy = MomentumStrategy(trade_repo=repo, bus=bus)
        repo.insert_trade.return_value = 42

        strikes = {24500: {"ce_ltp": 150.0, "pe_ltp": 80.0}}
        analysis = _make_analysis()

        trade_id = strategy.create_trade("BUY_CALL", analysis, strikes)

        assert trade_id == 42
        repo.insert_trade.assert_called_once()
        assert len(received) == 1
        assert received[0]["trade_id"] == 42
        assert received[0]["tracker_type"] == "momentum"

    def test_skips_low_premium(self, strategy, repo):
        strikes = {24500: {"ce_ltp": 2.0}}
        result = strategy.create_trade("BUY_CALL", _make_analysis(), strikes)
        assert result is None
        repo.insert_trade.assert_not_called()


class TestGetStats:
    def test_delegates_to_repo(self, strategy, repo):
        repo.get_stats.return_value = {"total": 5, "wins": 3}
        stats = strategy.get_stats()
        assert stats["total"] == 5
        repo.get_stats.assert_called_once_with("momentum_trades", 30)

    def test_no_repo_returns_empty(self):
        strategy = MomentumStrategy()
        stats = strategy.get_stats()
        assert stats["total"] == 0
