"""Tests for strategies/rr_strategy.py — RRStrategy (Rally Rider)."""

from datetime import datetime, time
from unittest.mock import patch, MagicMock

import pytest

from strategies.rr_strategy import RRStrategy, RREngine_round_to_tick
from core.events import EventBus, EventType


@pytest.fixture
def repo():
    r = MagicMock()
    r.get_todays_trades.return_value = []
    r.get_active.return_value = None
    return r


@pytest.fixture
def strategy(repo):
    return RRStrategy(trade_repo=repo)


def _analysis(**kw):
    d = {"spot_price": 24500.0, "verdict": "Slightly Bullish",
         "signal_confidence": 70, "vix": 12.0}
    d.update(kw)
    return d


class TestRRStrategy:
    def test_tracker_type(self, strategy):
        assert strategy.tracker_type == "rally_rider"
        assert strategy.table_name == "rr_trades"
        assert strategy.max_trades_per_day == 3
        assert strategy.is_selling is False

    def test_get_active_delegates(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.get_active() is None
        repo.get_active.assert_called_with("rr_trades")


class TestShouldCreate:
    def _setup_engine(self, strategy):
        """Replace strategy engine with a mock configured for NORMAL regime."""
        eng = MagicMock()
        eng.classify_regime.return_value = "NORMAL"
        eng.get_regime_params.return_value = {
            "signals": {"MC", "MOM"}, "sl_pts": 40, "tgt_pts": 20, "max_hold": 35,
            "direction": "BOTH", "time_start": time(9, 45),
            "time_end": time(14, 15),
            "cooldown": 8, "max_trades": 3,
        }
        strategy._engine = eng
        return eng

    def test_valid(self, strategy, repo):
        self._setup_engine(strategy)
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            now = datetime(2025, 1, 1, 11, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is True

    def test_rejects_outside_regime_time(self, strategy, repo):
        self._setup_engine(strategy)
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            now = datetime(2025, 1, 1, 9, 0)  # before 9:45
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_rejects_max_trades(self, strategy, repo):
        self._setup_engine(strategy)
        repo.get_todays_trades.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            now = datetime(2025, 1, 1, 11, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_rejects_cooldown(self, strategy, repo):
        self._setup_engine(strategy)
        # Last trade resolved 2 minutes ago, cooldown is 8
        repo.get_todays_trades.return_value = [
            {"id": 1, "resolved_at": "2025-01-01T10:58:00"}
        ]
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            now = datetime(2025, 1, 1, 11, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            assert strategy.should_create(_analysis()) is False

    def test_rejects_no_spot(self, strategy, repo):
        self._setup_engine(strategy)
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            now = datetime(2025, 1, 1, 11, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis(spot_price=0)) is False

    def test_rejects_active_trade(self, strategy, repo):
        self._setup_engine(strategy)
        repo.get_active.return_value = {"id": 1}
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            now = datetime(2025, 1, 1, 11, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False


class TestCreateTrade:
    def _signal(self, **overrides):
        s = {
            "action": "BUY_CE", "option_type": "CE", "strike": 24400,
            "entry_premium": 200.05, "sl_premium": 180.10,
            "target_premium": 220.15, "confidence": 75,
            "reasoning": "Strong VWAP reclaim with momentum",
            "signal_type": "MC", "regime": "NORMAL",
            "signal_data": {"rally_pts": 35.0, "pullback_pct": 0.4,
                            "regime": "NORMAL", "max_hold": 35,
                            "weekly_trend": "UP"},
        }
        s.update(overrides)
        return s

    def test_creates_trade(self, repo):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))

        strategy = RRStrategy(trade_repo=repo, bus=bus)
        repo.insert_trade.return_value = 42

        trade_id = strategy.create_trade(self._signal(), _analysis(), {})

        assert trade_id == 42
        repo.insert_trade.assert_called_once()
        call_kw = repo.insert_trade.call_args[1]
        assert call_kw["direction"] == "BUY_CE"
        assert call_kw["strike"] == 24400
        assert call_kw["signal_type"] == "MC"
        assert call_kw["regime"] == "NORMAL"
        assert call_kw["agent_confidence"] == 75
        assert call_kw["trail_stage"] == 0
        assert len(received) == 1

    def test_skips_low_confidence(self, strategy, repo):
        result = strategy.create_trade(
            self._signal(confidence=40), _analysis(), {})
        assert result is None
        repo.insert_trade.assert_not_called()

    def test_skips_low_premium(self, strategy, repo):
        result = strategy.create_trade(
            self._signal(entry_premium=50.0), _analysis(), {})
        assert result is None
        repo.insert_trade.assert_not_called()

    def test_skips_high_premium(self, strategy, repo):
        result = strategy.create_trade(
            self._signal(entry_premium=600.0), _analysis(), {})
        assert result is None
        repo.insert_trade.assert_not_called()

    def test_tick_rounding_in_signal(self):
        """Verify round_to_tick works as used in evaluate_signal."""
        assert RREngine_round_to_tick(200.03) == 200.05
        assert RREngine_round_to_tick(200.07) == 200.05
        assert RREngine_round_to_tick(200.12) == 200.10


class TestCheckAndUpdate:
    def _trade(self, **overrides):
        t = {
            "id": 1, "strike": 24400, "option_type": "CE",
            "direction": "BUY_CE", "created_at": "2025-01-01T10:00:00",
            "entry_premium": 200.0, "sl_premium": 170.0,
            "target_premium": 220.0, "trade_number": 1,
            "max_premium_reached": 200.0, "min_premium_reached": 200.0,
            "trail_stage": 0, "regime": "NORMAL",
            "signal_data_json": '{"max_hold": 35}',
        }
        t.update(overrides)
        return t

    def test_no_sl_exit_in_check_and_update(self, strategy, repo):
        """SL is handled by PremiumMonitor, not check_and_update."""
        repo.get_active.return_value = self._trade(
            created_at="2025-01-01T10:00:00", target_premium=280.0)
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 10, 5)  # 5 min in
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            # Premium below SL — check_and_update should NOT exit
            result = strategy.check_and_update({24400: {"ce_ltp": 165.0}})
            assert result is None

    def test_target_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        result = strategy.check_and_update({24400: {"ce_ltp": 225.0}})
        assert result["action"] == "WON"
        assert result["reason"] == "TARGET"

    def test_no_mechanical_trailing_stops(self, strategy, repo):
        """Mechanical trailing stops removed — Claude manages soft SL."""
        repo.get_active.return_value = self._trade(
            created_at="2025-01-01T10:00:00", target_premium=280.0)
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 10, 5)  # 5 min in
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            # Premium at +15% — should NOT trigger any trail stage update
            result = strategy.check_and_update({24400: {"ce_ltp": 230.0}})
            assert result is None
            # Verify no trail_stage or sl_premium updates in DB
            updates = repo.update_trade.call_args_list
            for call in updates:
                kw = call[1] if len(call) > 1 else call.kwargs
                assert "trail_stage" not in kw
                assert "sl_premium" not in kw

    def test_time_flat_exit(self, strategy, repo):
        # Trade created 40 minutes ago, premium flat (max_hold=35)
        repo.get_active.return_value = self._trade(
            created_at="2025-01-01T10:00:00",
            target_premium=280.0,
            signal_data_json='{"max_hold": 35}',
        )
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 10, 40)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            result = strategy.check_and_update({24400: {"ce_ltp": 201.0}})
            # +0.5% < 3% dead zone, 40min > 35min max_hold
            assert result is not None
            assert result["reason"] == "TIME_FLAT"

    def test_eod_exit(self, strategy, repo):
        # Created 10 min ago (so MAX_TIME won't trigger), now past force close 15:15
        repo.get_active.return_value = self._trade(
            created_at="2025-01-01T15:10:00",
            target_premium=280.0,
        )
        now = datetime(2025, 1, 1, 15, 20)
        with patch("strategies.rr_strategy.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bdt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_bdt.now.return_value = now
            mock_bdt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24400: {"ce_ltp": 205.0}})
            assert result is not None
            assert result["reason"] == "EOD"

    def test_max_time_exit(self, strategy, repo):
        repo.get_active.return_value = self._trade(
            created_at="2025-01-01T09:30:00",
            target_premium=280.0,
        )
        with patch("strategies.rr_strategy.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 10, 20)  # 50 min
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            result = strategy.check_and_update({24400: {"ce_ltp": 210.0}})
            assert result is not None
            assert result["reason"] == "MAX_TIME"

    def test_no_active(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.check_and_update({}) is None


class TestIntegration:
    """Integration tests — real objects, no mocks. Catches rename/import issues."""

    def test_premium_engine_instantiates(self):
        from strategies.premium_engine import PremiumEngine
        pe = PremiumEngine()
        strikes = pe.get_itm_strikes(23000.0)
        assert "ce_strike" in strikes
        assert "pe_strike" in strikes

    def test_premium_engine_build_chart_callable(self):
        from strategies.premium_engine import PremiumEngine
        pe = PremiumEngine()
        assert callable(pe.build_premium_chart_from_ohlc)
        assert callable(pe.format_chart_for_prompt)
        assert callable(pe.format_nifty_ohlc_for_prompt)
        assert callable(pe.compute_vwap)

    def test_rr_strategy_premium_engine_wired(self):
        strategy = RRStrategy(trade_repo=MagicMock())
        pe = strategy.premium_engine
        assert pe is not None
        strikes = pe.get_itm_strikes(23000.0)
        assert strikes["ce_strike"] == 22900

    def test_rr_strategy_engine_wired(self):
        strategy = RRStrategy(trade_repo=MagicMock())
        eng = strategy.engine
        assert eng is not None
        strike = eng.get_rr_strike(23000.0, "CE")
        assert strike == 22900

    def test_rr_strategy_agent_wired(self):
        strategy = RRStrategy(trade_repo=MagicMock())
        agent = strategy.agent
        assert agent is not None
        assert callable(agent.build_prompt)
        assert callable(agent.monitor_active_trade)

    def test_kite_fetcher_propagates_to_engine(self):
        """RRStrategy(kite_fetcher=X) → engine._fetcher is X."""
        fake_fetcher = MagicMock()
        strategy = RRStrategy(trade_repo=MagicMock(), kite_fetcher=fake_fetcher)
        assert strategy.engine._fetcher is fake_fetcher

    def test_engine_default_no_fetcher(self):
        """When no kite_fetcher passed, engine._fetcher is None (PMOM/NMOM disabled)."""
        strategy = RRStrategy(trade_repo=MagicMock())
        assert strategy.engine._fetcher is None

    def test_order_executor_methods_exist(self):
        from kite.order_executor import OrderExecutor
        oe = OrderExecutor()
        assert callable(oe.place_entry)
        assert callable(oe.modify_sl)
        assert callable(oe.cancel_exit_orders)
        assert callable(oe.place_exit)
        assert callable(oe.round_to_tick)
        assert callable(oe.is_strategy_live)

    def test_trade_monitor_importable(self):
        from strategies.trade_monitor import (
            build_monitor_prompt, validate_monitor_response, MONITOR_SYSTEM_PROMPT
        )
        assert callable(build_monitor_prompt)
        assert callable(validate_monitor_response)
        assert "HOLD" in MONITOR_SYSTEM_PROMPT


class TestGetStats:
    def test_delegates_to_repo(self, strategy, repo):
        repo.get_stats.return_value = {"total": 5, "wins": 3}
        stats = strategy.get_stats()
        assert stats["total"] == 5
        repo.get_stats.assert_called_once_with("rr_trades", 30)

    def test_no_repo(self):
        strategy = RRStrategy(trade_repo=None)
        stats = strategy.get_stats()
        assert stats["total"] == 0
