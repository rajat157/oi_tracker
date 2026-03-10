"""Tests for strategies/selling.py — SellingStrategy (full logic, no legacy)."""

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from strategies.selling import SellingStrategy
from core.events import EventBus, EventType


@pytest.fixture
def repo():
    r = MagicMock()
    r.get_todays_trades.return_value = []
    r.get_active.return_value = None
    return r


@pytest.fixture
def strategy(repo):
    return SellingStrategy(trade_repo=repo)


def _analysis(verdict="Slightly Bullish", confidence=70.0, **kw):
    d = {"verdict": verdict, "signal_confidence": confidence,
         "spot_price": 24500.0}
    d.update(kw)
    return d


class TestSellingStrategy:
    def test_tracker_type(self, strategy):
        assert strategy.tracker_type == "selling"
        assert strategy.is_selling is True
        assert strategy.table_name == "sell_trade_setups"

    def test_get_active_delegates(self, strategy, repo):
        repo.get_active.return_value = None
        result = strategy.get_active()
        repo.get_active.assert_called_with("sell_trade_setups")
        assert result is None

    def test_get_active_found(self, strategy, repo):
        trade = {"id": 1, "status": "ACTIVE", "strike": 24500}
        repo.get_active.return_value = trade
        result = strategy.get_active()
        assert result["id"] == 1


class TestShouldCreate:
    def test_valid_signal(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is True

    def test_rejects_non_slightly(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis(verdict="Bulls Winning")) is False

    def test_rejects_low_confidence(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis(confidence=50.0)) is False

    def test_rejects_already_traded(self, strategy, repo):
        repo.get_todays_trades.return_value = [{"id": 1}]
        assert strategy.should_create(_analysis()) is False

    def test_rejects_active_exists(self, strategy, repo):
        repo.get_active.return_value = {"id": 1}
        assert strategy.should_create(_analysis()) is False

    def test_rejects_outside_window(self, strategy):
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False


class TestOtmStrike:
    def test_sell_put_otm(self):
        assert SellingStrategy.get_otm_strike(24500.0, "SELL_PUT") == 24450

    def test_sell_call_otm(self):
        assert SellingStrategy.get_otm_strike(24500.0, "SELL_CALL") == 24550


class TestCreateTrade:
    def test_creates_sell_put(self, repo):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))

        strategy = SellingStrategy(trade_repo=repo, bus=bus)
        repo.insert_trade.return_value = 7

        strikes = {24450: {"pe_ltp": 80.0, "pe_iv": 15.0}}
        analysis = _analysis(verdict="Slightly Bullish")

        trade_id = strategy.create_trade(None, analysis, strikes)

        assert trade_id == 7
        call_kw = repo.insert_trade.call_args[1]
        assert call_kw["direction"] == "SELL_PUT"
        assert call_kw["strike"] == 24450
        assert call_kw["option_type"] == "PE"
        assert call_kw["sl_premium"] == 100.0  # 80 * 1.25
        assert call_kw["target_premium"] == 60.0  # 80 * 0.75 (T1)
        assert call_kw["target2_premium"] == 40.0  # 80 * 0.50 (T2)
        assert len(received) == 1

    def test_creates_sell_call(self, repo):
        strategy = SellingStrategy(trade_repo=repo)
        repo.insert_trade.return_value = 8

        strikes = {24550: {"ce_ltp": 90.0, "ce_iv": 14.0}}
        analysis = _analysis(verdict="Slightly Bearish")

        trade_id = strategy.create_trade(None, analysis, strikes)

        assert trade_id == 8
        call_kw = repo.insert_trade.call_args[1]
        assert call_kw["direction"] == "SELL_CALL"
        assert call_kw["option_type"] == "CE"

    def test_skips_low_premium(self, strategy, repo):
        strikes = {24450: {"pe_ltp": 2.0}}
        result = strategy.create_trade(None, _analysis(), strikes)
        assert result is None
        repo.insert_trade.assert_not_called()


class TestCheckAndUpdate:
    def _trade(self, **overrides):
        t = {
            "id": 1, "strike": 24450, "option_type": "PE",
            "direction": "SELL_PUT",
            "entry_premium": 80.0, "sl_premium": 100.0,
            "target_premium": 60.0, "target2_premium": 40.0,
            "max_premium_reached": 80.0, "min_premium_reached": 80.0,
            "t1_hit": 0,
        }
        t.update(overrides)
        return t

    def test_sl_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        result = strategy.check_and_update({24450: {"pe_ltp": 105.0}})
        assert result["action"] == "LOST"
        assert result["reason"] == "SL"
        assert result["pnl"] < 0

    def test_t2_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        result = strategy.check_and_update({24450: {"pe_ltp": 35.0}})
        assert result["action"] == "WON"
        assert result["reason"] == "TARGET2"
        assert result["pnl"] > 0

    def test_t1_notification(self, strategy, repo):
        """T1 hit should notify but NOT exit."""
        bus = EventBus()
        t1_events = []
        bus.subscribe(EventType.T1_HIT, lambda et, d: t1_events.append(d))
        strategy = SellingStrategy(trade_repo=repo, bus=bus)

        repo.get_active.return_value = self._trade()
        mock_now = datetime(2025, 1, 1, 13, 0)
        # Premium dropped to T1 (60) but not T2 (40)
        with patch("strategies.selling.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = mock_now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24450: {"pe_ltp": 55.0}})
        # Should NOT exit — T1 is notify-only
        assert result is None
        # But T1 event should have fired
        assert len(t1_events) == 1

    def test_eod_exit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        with patch("core.base_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 15, 25)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24450: {"pe_ltp": 70.0}})
        assert result is not None
        assert result["reason"] == "EOD"

    def test_no_active(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.check_and_update({}) is None


class TestGetStats:
    def test_delegates_to_repo(self, strategy, repo):
        repo.get_stats.return_value = {"total": 10, "wins": 8}
        stats = strategy.get_stats()
        assert stats["total"] == 10
        repo.get_stats.assert_called_once_with("sell_trade_setups", 30)

    def test_no_repo(self):
        s = SellingStrategy()
        assert s.get_stats()["total"] == 0
