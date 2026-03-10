"""Tests for strategies/pulse_rider.py — PulseRiderStrategy.

Fully self-contained: mock trade_repo, no legacy pa_tracker imports.
"""

from datetime import datetime, time
from unittest.mock import MagicMock, patch

import pytest

from core.events import EventBus, EventType
from strategies.pulse_rider import PulseRiderStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _analysis(**kw):
    d = {
        "spot_price": 24500.0,
        "verdict": "Slightly Bullish",
        "signal_confidence": 70,
        "iv_skew": 0.5,
        "vix": 12.0,
        "confirmation_status": "CONFIRMED",
    }
    d.update(kw)
    return d


def _strikes_data(atm=24500, ce_ltp=100.0, pe_ltp=95.0):
    """Build strikes_data dict for ATM strike."""
    return {atm: {"ce_ltp": ce_ltp, "pe_ltp": pe_ltp}}


def _build_premium_history(n, ce_start=90.0, pe_start=85.0,
                           ce_delta=2.0, pe_delta=0.0, spot=24500.0):
    """Build n premium history entries with linear CE/PE changes."""
    history = []
    for i in range(n):
        history.append({
            "ts": datetime(2025, 1, 1, 10, i * 3),
            "ce_ltp": ce_start + i * ce_delta,
            "pe_ltp": pe_start + i * pe_delta,
            "spot": spot,
        })
    return history


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo():
    """Mock TradeRepository."""
    r = MagicMock()
    r.get_todays_trades.return_value = []
    r.get_active.return_value = None
    return r


@pytest.fixture
def strategy(repo):
    """Create PulseRiderStrategy with mock repo."""
    return PulseRiderStrategy(trade_repo=repo)


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestAttributes:
    def test_tracker_type(self, strategy):
        assert strategy.tracker_type == "pulse_rider"

    def test_table_name(self, strategy):
        assert strategy.table_name == "pa_trades"

    def test_max_trades_per_day(self, strategy):
        assert strategy.max_trades_per_day == 1

    def test_is_selling(self, strategy):
        assert strategy.is_selling is False

    def test_time_window(self, strategy):
        assert strategy.time_start == time(9, 30)
        assert strategy.time_end == time(14, 0)
        assert strategy.force_close_time == time(15, 20)

    def test_init_table_called(self, repo):
        """init_table is called with PA_TRADES_DDL on construction."""
        PulseRiderStrategy(trade_repo=repo)
        repo.init_table.assert_called_once()

    def test_stateful_fields_initialized(self, strategy):
        assert strategy.atm_strike is None
        assert strategy.premium_history == []
        assert strategy._current_date is None


# ---------------------------------------------------------------------------
# Day reset
# ---------------------------------------------------------------------------

class TestResetDay:
    def test_reset_clears_state(self, strategy):
        strategy.atm_strike = 24500
        strategy.premium_history = [{"ce_ltp": 100}]
        strategy.reset_day()
        assert strategy.atm_strike is None
        assert strategy.premium_history == []


# ---------------------------------------------------------------------------
# ATM lock & premium recording
# ---------------------------------------------------------------------------

class TestATMLock:
    def test_lock_atm_strike(self, strategy):
        strategy._lock_atm_strike(24523.0)
        assert strategy.atm_strike == 24500  # rounded to nearest 50

    def test_lock_atm_strike_rounds_up(self, strategy):
        strategy._lock_atm_strike(24575.0)
        assert strategy.atm_strike == 24600

    def test_lock_atm_strike_exact(self, strategy):
        strategy._lock_atm_strike(24550.0)
        assert strategy.atm_strike == 24550


class TestRecordPremium:
    def test_records_when_atm_set(self, strategy):
        strategy.atm_strike = 24500
        strategy._record_premium(
            _strikes_data(24500, 100.0, 95.0),
            datetime(2025, 1, 1, 10, 0), 24500.0,
        )
        assert len(strategy.premium_history) == 1
        assert strategy.premium_history[0]["ce_ltp"] == 100.0
        assert strategy.premium_history[0]["pe_ltp"] == 95.0

    def test_skips_when_no_atm(self, strategy):
        strategy._record_premium(
            _strikes_data(), datetime(2025, 1, 1, 10, 0), 24500.0,
        )
        assert len(strategy.premium_history) == 0

    def test_skips_zero_premium(self, strategy):
        strategy.atm_strike = 24500
        strategy._record_premium(
            _strikes_data(24500, 0, 95.0),
            datetime(2025, 1, 1, 10, 0), 24500.0,
        )
        assert len(strategy.premium_history) == 0

    def test_skips_missing_strike(self, strategy):
        strategy.atm_strike = 24500
        strategy._record_premium(
            {24550: {"ce_ltp": 100, "pe_ltp": 90}},
            datetime(2025, 1, 1, 10, 0), 24500.0,
        )
        assert len(strategy.premium_history) == 0


# ---------------------------------------------------------------------------
# Momentum detection (CHC-3)
# ---------------------------------------------------------------------------

class TestDetectMomentum:
    def test_ce_rising_3_candles(self, strategy):
        # CE rising, PE flat => CE signal
        strategy.premium_history = _build_premium_history(
            4, ce_start=90, pe_start=85, ce_delta=2.0, pe_delta=0.0,
        )
        result = strategy._detect_momentum()
        assert result is not None
        assert result[0] == "CE"
        assert result[1] > 0

    def test_pe_rising_3_candles(self, strategy):
        # PE rising, CE flat => PE signal
        strategy.premium_history = _build_premium_history(
            4, ce_start=90, pe_start=85, ce_delta=0.0, pe_delta=2.0,
        )
        result = strategy._detect_momentum()
        assert result is not None
        assert result[0] == "PE"

    def test_both_rising_stronger_ce(self, strategy):
        # Both rising but CE has bigger % move
        strategy.premium_history = _build_premium_history(
            4, ce_start=90, pe_start=85, ce_delta=5.0, pe_delta=1.0,
        )
        result = strategy._detect_momentum()
        assert result is not None
        assert result[0] == "CE"

    def test_both_rising_stronger_pe(self, strategy):
        strategy.premium_history = _build_premium_history(
            4, ce_start=90, pe_start=85, ce_delta=1.0, pe_delta=5.0,
        )
        result = strategy._detect_momentum()
        assert result is not None
        assert result[0] == "PE"

    def test_none_when_no_trend(self, strategy):
        # Alternating — no 3-candle rising
        strategy.premium_history = [
            {"ts": datetime(2025, 1, 1, 10, 0), "ce_ltp": 90, "pe_ltp": 85, "spot": 24500},
            {"ts": datetime(2025, 1, 1, 10, 3), "ce_ltp": 92, "pe_ltp": 83, "spot": 24500},
            {"ts": datetime(2025, 1, 1, 10, 6), "ce_ltp": 91, "pe_ltp": 84, "spot": 24500},
            {"ts": datetime(2025, 1, 1, 10, 9), "ce_ltp": 93, "pe_ltp": 82, "spot": 24500},
        ]
        result = strategy._detect_momentum()
        assert result is None

    def test_none_when_insufficient_history(self, strategy):
        strategy.premium_history = _build_premium_history(2)
        assert strategy._detect_momentum() is None


# ---------------------------------------------------------------------------
# IV skew filter
# ---------------------------------------------------------------------------

class TestIVSkew:
    def test_ce_ok_below_threshold(self):
        assert PulseRiderStrategy._is_iv_skew_ok("CE", 0.5) is True

    def test_ce_blocked_above_threshold(self):
        assert PulseRiderStrategy._is_iv_skew_ok("CE", 1.5) is False

    def test_pe_ok_above_threshold(self):
        assert PulseRiderStrategy._is_iv_skew_ok("PE", -0.5) is True

    def test_pe_blocked_below_threshold(self):
        assert PulseRiderStrategy._is_iv_skew_ok("PE", -1.5) is False

    def test_ce_at_boundary(self):
        assert PulseRiderStrategy._is_iv_skew_ok("CE", 1.0) is True

    def test_pe_at_boundary(self):
        assert PulseRiderStrategy._is_iv_skew_ok("PE", -1.0) is True


# ---------------------------------------------------------------------------
# Choppy filter
# ---------------------------------------------------------------------------

class TestIsChoppy:
    def test_choppy_when_flat(self, strategy):
        # Very tight range → choppy
        strategy.premium_history = _build_premium_history(
            12, ce_start=90, pe_start=85, spot=24500,
        )
        # All spots identical → range = 0 < threshold
        assert strategy._is_choppy() is True

    def test_not_choppy_when_moving(self, strategy):
        history = []
        for i in range(12):
            history.append({
                "ts": datetime(2025, 1, 1, 10, i * 3),
                "ce_ltp": 90 + i, "pe_ltp": 85,
                "spot": 24400 + i * 20,  # 220 pt range on ~24500 ≈ 0.9%
            })
        strategy.premium_history = history
        assert strategy._is_choppy() is False

    def test_returns_false_insufficient_data(self, strategy):
        strategy.premium_history = _build_premium_history(3)
        assert strategy._is_choppy() is False


# ---------------------------------------------------------------------------
# evaluate() — full signal detection
# ---------------------------------------------------------------------------

class TestEvaluate:
    """Test the evaluate() method which orchestrates all filters."""

    def _setup_for_signal(self, strategy, side="CE"):
        """Pre-load strategy state so CHC-3 fires for the given side."""
        strategy.atm_strike = 24500
        strategy._current_date = datetime(2025, 1, 1).date()  # prevent reset
        if side == "CE":
            strategy.premium_history = _build_premium_history(
                4, ce_start=90, pe_start=85, ce_delta=3.0, pe_delta=0.0,
                spot=24500,
            )
        else:
            strategy.premium_history = _build_premium_history(
                4, ce_start=90, pe_start=85, ce_delta=0.0, pe_delta=3.0,
                spot=24500,
            )
        # Inject varying spots to avoid choppy filter
        for i, h in enumerate(strategy.premium_history):
            h["spot"] = 24400 + i * 50

    def test_returns_ce_on_valid_signal(self, strategy, repo):
        self._setup_for_signal(strategy, "CE")
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(
                _analysis(), _strikes_data(24500, 100.0, 95.0),
            )
        assert result == "CE"

    def test_returns_pe_on_valid_signal(self, strategy, repo):
        self._setup_for_signal(strategy, "PE")
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(
                _analysis(), _strikes_data(24500, 100.0, 95.0),
            )
        assert result == "PE"

    def test_rejects_outside_time_window(self, strategy, repo):
        self._setup_for_signal(strategy, "CE")
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 8, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(_analysis(), _strikes_data())
        assert result is None

    def test_rejects_already_traded_today(self, strategy, repo):
        self._setup_for_signal(strategy, "CE")
        repo.get_todays_trades.return_value = [{"id": 1}]
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(_analysis(), _strikes_data())
        assert result is None

    def test_rejects_active_trade(self, strategy, repo):
        self._setup_for_signal(strategy, "CE")
        repo.get_active.return_value = {"id": 1}
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(_analysis(), _strikes_data())
        assert result is None

    def test_rejects_zero_spot(self, strategy, repo):
        self._setup_for_signal(strategy, "CE")
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(
                _analysis(spot_price=0), _strikes_data(),
            )
        assert result is None

    def test_rejects_iv_skew_ce(self, strategy, repo):
        self._setup_for_signal(strategy, "CE")
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(
                _analysis(iv_skew=1.5), _strikes_data(24500, 100, 95),
            )
        assert result is None

    def test_rejects_conflict_confirmation(self, strategy, repo):
        self._setup_for_signal(strategy, "CE")
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(
                _analysis(confirmation_status="CONFLICT"),
                _strikes_data(24500, 100, 95),
            )
        assert result is None

    def test_insufficient_history_returns_none(self, strategy, repo):
        strategy.atm_strike = 24500
        strategy.premium_history = _build_premium_history(2)
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.evaluate(_analysis(), _strikes_data())
        assert result is None

    def test_day_reset_on_new_date(self, strategy, repo):
        strategy.atm_strike = 24500
        strategy.premium_history = [{"old": True}]
        strategy._current_date = datetime(2025, 1, 1).date()
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            # New day
            mock_dt.now.return_value = datetime(2025, 1, 2, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            strategy.evaluate(_analysis(), _strikes_data())
        # State should be reset
        assert strategy._current_date == datetime(2025, 1, 2).date()
        assert strategy.atm_strike is not None  # Re-locked from spot


# ---------------------------------------------------------------------------
# should_create() — wraps evaluate
# ---------------------------------------------------------------------------

class TestShouldCreate:
    def test_true_when_evaluate_returns_signal(self, strategy, repo):
        strategy.atm_strike = 24500
        strategy._current_date = datetime(2025, 1, 1).date()
        strategy.premium_history = _build_premium_history(
            4, ce_start=90, pe_start=85, ce_delta=3.0, pe_delta=0.0,
        )
        for i, h in enumerate(strategy.premium_history):
            h["spot"] = 24400 + i * 50
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(
                _analysis(), strikes_data=_strikes_data(24500, 100, 95),
            ) is True

    def test_false_when_evaluate_returns_none(self, strategy, repo):
        # No history → no signal
        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(
                _analysis(), strikes_data=_strikes_data(),
            ) is False


# ---------------------------------------------------------------------------
# create_trade()
# ---------------------------------------------------------------------------

class TestCreateTrade:
    def test_creates_ce_trade(self, repo):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))
        strategy = PulseRiderStrategy(trade_repo=repo, bus=bus)

        strategy.atm_strike = 24500
        # Need premium history for _detect_momentum in _create
        strategy.premium_history = _build_premium_history(
            4, ce_start=90, pe_start=85, ce_delta=3.0, pe_delta=0.0,
        )
        repo.insert_trade.return_value = 42

        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            trade_id = strategy.create_trade(
                "CE", _analysis(), _strikes_data(24500, 100.0, 95.0),
            )

        assert trade_id == 42
        repo.insert_trade.assert_called_once()
        kw = repo.insert_trade.call_args[1]
        assert kw["direction"] == "BUY_CALL"
        assert kw["option_type"] == "CE"
        assert kw["strike"] == 24500
        assert kw["entry_premium"] == 100.0
        assert kw["status"] == "ACTIVE"
        # SL and target: 15% each
        assert kw["sl_premium"] == round(100.0 * 0.85, 2)
        assert kw["target_premium"] == round(100.0 * 1.15, 2)
        # Event published
        assert len(received) == 1
        assert received[0]["direction"] == "BUY_CALL"

    def test_creates_pe_trade(self, repo):
        strategy = PulseRiderStrategy(trade_repo=repo)
        strategy.atm_strike = 24500
        strategy.premium_history = _build_premium_history(
            4, ce_start=90, pe_start=85, ce_delta=0.0, pe_delta=3.0,
        )
        repo.insert_trade.return_value = 43

        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            trade_id = strategy.create_trade(
                "PE", _analysis(), _strikes_data(24500, 100.0, 95.0),
            )

        assert trade_id == 43
        kw = repo.insert_trade.call_args[1]
        assert kw["direction"] == "BUY_PUT"
        assert kw["option_type"] == "PE"
        # PE premium used
        assert kw["entry_premium"] == 95.0

    def test_rejects_low_premium(self, repo):
        strategy = PulseRiderStrategy(trade_repo=repo)
        strategy.atm_strike = 24500
        strategy.premium_history = _build_premium_history(4)

        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            trade_id = strategy.create_trade(
                "CE", _analysis(), _strikes_data(24500, 3.0, 2.0),
            )

        assert trade_id is None
        repo.insert_trade.assert_not_called()

    def test_rejects_high_premium(self, repo):
        strategy = PulseRiderStrategy(trade_repo=repo)
        strategy.atm_strike = 24500
        strategy.premium_history = _build_premium_history(4)

        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            trade_id = strategy.create_trade(
                "CE", _analysis(), _strikes_data(24500, 250.0, 200.0),
            )

        assert trade_id is None
        repo.insert_trade.assert_not_called()

    def test_paper_trade_high_vix(self, repo):
        """High VIX should still create trade (paper mode) but skip Kite order."""
        strategy = PulseRiderStrategy(trade_repo=repo)
        strategy.atm_strike = 24500
        strategy.premium_history = _build_premium_history(
            4, ce_start=90, pe_start=85, ce_delta=3.0, pe_delta=0.0,
        )
        repo.insert_trade.return_value = 44

        with patch("strategies.pulse_rider.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 11, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            trade_id = strategy.create_trade(
                "CE", _analysis(vix=22.0),
                _strikes_data(24500, 100.0, 95.0),
            )

        assert trade_id == 44  # Trade still created


# ---------------------------------------------------------------------------
# check_and_update()
# ---------------------------------------------------------------------------

class TestCheckAndUpdate:
    def _trade(self, **overrides):
        t = {
            "id": 1,
            "strike": 24500,
            "option_type": "CE",
            "direction": "BUY_CALL",
            "entry_premium": 100.0,
            "sl_premium": 85.0,     # -15%
            "target_premium": 115.0, # +15%
            "max_premium_reached": 100.0,
            "min_premium_reached": 100.0,
        }
        t.update(overrides)
        return t

    def test_no_active_returns_none(self, strategy, repo):
        repo.get_active.return_value = None
        assert strategy.check_and_update({}) is None

    def test_sl_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 12, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24500: {"ce_ltp": 80.0}})
        assert result["action"] == "LOST"
        assert result["reason"] == "SL"
        assert result["pnl"] < 0

    def test_target_hit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 12, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24500: {"ce_ltp": 120.0}})
        assert result["action"] == "WON"
        assert result["reason"] == "TARGET"
        assert result["pnl"] > 0

    def test_eod_exit_profit(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 15, 25)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24500: {"ce_ltp": 105.0}})
        assert result["action"] == "WON"
        assert result["reason"] == "EOD"

    def test_eod_exit_loss(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 15, 25)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24500: {"ce_ltp": 95.0}})
        assert result["action"] == "LOST"
        assert result["reason"] == "EOD"

    def test_no_exit_in_range(self, strategy, repo):
        """Premium between SL and target — no exit."""
        repo.get_active.return_value = self._trade()
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 12, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24500: {"ce_ltp": 100.0}})
        assert result is None
        # But update_trade should be called (tracking)
        repo.update_trade.assert_called()

    def test_tracks_max_min_premium(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 12, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            strategy.check_and_update({24500: {"ce_ltp": 110.0}})
        # First update_trade call should track max
        call_kw = repo.update_trade.call_args_list[0][1]
        assert call_kw["max_premium_reached"] == 110.0
        assert call_kw["min_premium_reached"] == 100.0

    def test_pe_trade_uses_pe_ltp(self, strategy, repo):
        repo.get_active.return_value = self._trade(
            option_type="PE", direction="BUY_PUT",
            entry_premium=95.0, sl_premium=80.75, target_premium=109.25,
        )
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 12, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24500: {"pe_ltp": 110.0}})
        assert result["action"] == "WON"

    def test_zero_premium_ignored(self, strategy, repo):
        repo.get_active.return_value = self._trade()
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 12, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({24500: {"ce_ltp": 0}})
        assert result is None

    def test_exit_publishes_event(self, repo):
        bus = EventBus()
        exited = []
        bus.subscribe(EventType.TRADE_EXITED, lambda et, d: exited.append(d))
        strategy = PulseRiderStrategy(trade_repo=repo, bus=bus)

        repo.get_active.return_value = {
            "id": 1, "strike": 24500, "option_type": "CE",
            "direction": "BUY_CALL",
            "entry_premium": 100.0, "sl_premium": 85.0,
            "target_premium": 115.0,
            "max_premium_reached": 100.0, "min_premium_reached": 100.0,
        }
        with patch("strategies.pulse_rider.datetime") as mock_dt, \
             patch("core.base_tracker.datetime") as mock_bt:
            now = datetime(2025, 1, 1, 12, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_bt.now.return_value = now
            mock_bt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            strategy.check_and_update({24500: {"ce_ltp": 120.0}})

        assert len(exited) == 1
        assert exited[0]["reason"] == "TARGET"


# ---------------------------------------------------------------------------
# get_active / get_stats
# ---------------------------------------------------------------------------

class TestGetActiveAndStats:
    def test_get_active_delegates(self, strategy, repo):
        repo.get_active.return_value = {"id": 1}
        result = strategy.get_active()
        assert result == {"id": 1}
        repo.get_active.assert_called_with("pa_trades")

    def test_get_active_no_repo(self):
        strategy = PulseRiderStrategy()
        assert strategy.get_active() is None

    def test_get_stats_delegates(self, strategy, repo):
        repo.get_stats.return_value = {"total": 10, "wins": 7}
        stats = strategy.get_stats()
        assert stats["total"] == 10
        repo.get_stats.assert_called_with("pa_trades", 30)

    def test_get_stats_no_repo(self):
        strategy = PulseRiderStrategy()
        stats = strategy.get_stats()
        assert stats["total"] == 0
        assert stats["win_rate"] == 0

    def test_get_stats_custom_lookback(self, strategy, repo):
        repo.get_stats.return_value = {"total": 5}
        strategy.get_stats(lookback_days=7)
        repo.get_stats.assert_called_with("pa_trades", 7)


# ---------------------------------------------------------------------------
# Alert formatting
# ---------------------------------------------------------------------------

class TestAlertFormatting:
    def test_entry_alert_contains_key_info(self):
        alert = PulseRiderStrategy._format_entry_alert(
            "BUY_CALL", 24500, "CE", 100.0, 85.0, 115.0,
            24500.0, "Slightly Bullish", 70, 0.5, 12.0, 0.06,
        )
        assert "BUY_CALL" in alert
        assert "24500 CE" in alert
        assert "100.00" in alert

    def test_entry_alert_vix_warning(self):
        alert = PulseRiderStrategy._format_entry_alert(
            "BUY_PUT", 24500, "PE", 95.0, 80.75, 109.25,
            24500.0, "Slightly Bearish", 65, -0.3, 22.0, 0.05,
        )
        assert "PAPER TRADE" in alert
        assert "HIGH VIX" in alert

    def test_exit_alert_won(self):
        trade = {"strike": 24500, "option_type": "CE", "entry_premium": 100.0}
        alert = PulseRiderStrategy._format_exit_alert(trade, 115.0, "TARGET", 15.0)
        assert "WON" in alert
        assert "+15.00%" in alert

    def test_exit_alert_lost(self):
        trade = {"strike": 24500, "option_type": "PE", "entry_premium": 95.0}
        alert = PulseRiderStrategy._format_exit_alert(trade, 80.75, "SL", -15.0)
        assert "LOST" in alert
        assert "Stop Loss" in alert
