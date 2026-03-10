"""Tests for strategies/iron_pulse.py — IronPulseStrategy.

Fully self-contained: mock trade_repo, no legacy trade_tracker imports.
"""

from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch

import pytest

from core.events import EventBus, EventType
from strategies.iron_pulse import IronPulseStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _analysis(**kw):
    d = {
        "spot_price": 24500.0,
        "verdict": "Slightly Bullish",
        "signal_confidence": 70,
        "trade_setup": {
            "direction": "BUY_CALL",
            "strike": 24500,
            "option_type": "CE",
            "moneyness": "ATM",
            "entry_premium": 150.0,
            "risk_pct": 20,
            "iv_at_strike": 12.0,
        },
        "expiry_date": "",  # empty by default — DTE filter exits early
        "confirmation_status": "CONFIRMED",
    }
    d.update(kw)
    return d


def _trade(**overrides):
    """Mock DB trade row for check_and_update tests."""
    t = {
        "id": 1,
        "strike": 24500,
        "option_type": "CE",
        "direction": "BUY_CALL",
        "moneyness": "ATM",
        "entry_premium": 150.0,
        "sl_premium": 120.0,         # -20%
        "target1_premium": 183.0,    # +22%
        "activation_premium": 150.0,
        "status": "ACTIVE",
        "t1_hit": 0,
        "peak_premium": 150.0,
        "max_premium_reached": 150.0,
        "min_premium_reached": 150.0,
        "trailing_sl": None,
    }
    t.update(overrides)
    return t


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo():
    r = MagicMock()
    r.get_todays_trades.return_value = []
    r.get_active.return_value = None
    r.get_active_or_pending.return_value = None
    r.get_last_resolved.return_value = None
    return r


@pytest.fixture
def strategy(repo):
    return IronPulseStrategy(trade_repo=repo)


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestAttributes:
    def test_tracker_type(self, strategy):
        assert strategy.tracker_type == "iron_pulse"

    def test_table_name(self, strategy):
        assert strategy.table_name == "trade_setups"

    def test_supports_pending(self, strategy):
        assert strategy.supports_pending is True

    def test_is_selling(self, strategy):
        assert strategy.is_selling is False

    def test_max_trades(self, strategy):
        assert strategy.max_trades_per_day == 1

    def test_time_window(self, strategy):
        assert strategy.time_start == time(11, 0)
        assert strategy.time_end == time(14, 0)
        assert strategy.force_close_time == time(15, 20)

    def test_init_table_called(self, repo):
        IronPulseStrategy(trade_repo=repo)
        repo.init_table.assert_called_once()

    def test_instance_state(self, strategy):
        assert strategy.entry_tolerance == 0.02
        assert strategy.cooldown_minutes == 12
        assert strategy.last_suggested_direction is None
        assert strategy.last_cancelled_time is None


# ---------------------------------------------------------------------------
# _is_valid_strategy_signal
# ---------------------------------------------------------------------------

class TestIsValidStrategySignal:
    def test_valid(self, strategy):
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy._is_valid_strategy_signal(_analysis()) is True

    def test_rejects_non_slightly(self, strategy):
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy._is_valid_strategy_signal(
                _analysis(verdict="Bulls Winning")) is False

    def test_rejects_low_confidence(self, strategy):
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy._is_valid_strategy_signal(
                _analysis(signal_confidence=50)) is False

    def test_rejects_outside_window(self, strategy):
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy._is_valid_strategy_signal(_analysis()) is False


# ---------------------------------------------------------------------------
# Guard checks
# ---------------------------------------------------------------------------

class TestGuardChecks:
    def test_already_traded_today(self, strategy, repo):
        repo.get_todays_trades.return_value = [{"id": 1}]
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_existing_active_blocks(self, strategy, repo):
        repo.get_active_or_pending.return_value = {"id": 1, "status": "ACTIVE"}
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_no_trade_setup_blocks(self, strategy, repo):
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(
                _analysis(trade_setup=None)) is False

    def test_cheap_premium_blocks(self, strategy, repo):
        """Premium < 0.20% of spot should be blocked."""
        cheap_setup = {
            "direction": "BUY_CALL", "strike": 24500, "option_type": "CE",
            "moneyness": "ATM", "entry_premium": 30.0,  # 30/24500 = 0.12%
            "risk_pct": 20, "iv_at_strike": 12.0,
        }
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(
                _analysis(trade_setup=cheap_setup)) is False

    def test_cooldown_after_resolution(self, strategy, repo):
        """Should block within 12 minutes of last resolved trade."""
        repo.get_last_resolved.return_value = {
            "resolved_at": (datetime(2025, 1, 1, 11, 55)).isoformat(),
        }
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            assert strategy.should_create(_analysis()) is False

    def test_no_cooldown_after_enough_time(self, strategy, repo):
        """Should allow after 12+ minutes since last resolved trade."""
        repo.get_last_resolved.return_value = {
            "resolved_at": (datetime(2025, 1, 1, 11, 30)).isoformat(),
        }
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            assert strategy.should_create(_analysis()) is True

    def test_cancellation_cooldown(self, strategy, repo):
        strategy.last_cancelled_time = datetime(2025, 1, 1, 11, 50)
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is False

    def test_direction_flip_cooldown(self, strategy, repo):
        strategy.last_suggested_direction = "BUY_PUT"
        strategy.last_suggestion_time = datetime(2025, 1, 1, 11, 50)
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # BUY_CALL != BUY_PUT and within 15 min
            assert strategy.should_create(_analysis()) is False

    def test_same_direction_no_cooldown(self, strategy, repo):
        strategy.last_suggested_direction = "BUY_CALL"
        strategy.last_suggestion_time = datetime(2025, 1, 1, 11, 55)
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is True

    def test_move_already_happened_bullish(self, strategy, repo):
        """Block when spot already up 0.8%+ for bullish signal."""
        price_history = [
            {"spot_price": 24000},
            {"spot_price": 24250},  # 1.04% move
        ]
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(
                _analysis(spot_price=24250),
                price_history=price_history) is False

    def test_bounce_blocks_put(self, strategy, repo):
        """Bounce from low blocks PUT entry."""
        price_history = [
            {"spot_price": 24500},
            {"spot_price": 24400},  # low
            {"spot_price": 24500},  # bounced 0.41%
        ]
        put_analysis = _analysis(
            verdict="Slightly Bearish",
            trade_setup={
                "direction": "BUY_PUT", "strike": 24500, "option_type": "PE",
                "moneyness": "ATM", "entry_premium": 150.0,
                "risk_pct": 20, "iv_at_strike": 12.0,
            })
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(
                put_analysis, price_history=price_history) is False

    def test_bounce_does_not_block_call(self, strategy, repo):
        """Bounce check only applies to bearish/PUT trades."""
        price_history = [
            {"spot_price": 24400},
            {"spot_price": 24300},
            {"spot_price": 24500},
        ]
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(
                _analysis(), price_history=price_history) is True


# ---------------------------------------------------------------------------
# DTE momentum filter
# ---------------------------------------------------------------------------

class TestDTEMomentumFilter:
    def _patch_dt(self, now=None):
        """Context manager patching datetime with strptime support."""
        p = patch("strategies.iron_pulse.datetime")
        mock_dt = p.start()
        mock_dt.now.return_value = now or datetime(2025, 1, 1, 12, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.strptime = datetime.strptime
        return p, mock_dt

    def test_blocks_misaligned_momentum(self, repo):
        """Block when DTE >= 3, IV > 10, momentum misaligned."""
        def mock_trend(minutes):
            return [
                {"spot_price": 24500},
                {"spot_price": 24400},  # falling = misaligned for BUY_CALL
            ]

        strategy = IronPulseStrategy(trade_repo=repo, price_trend_fn=mock_trend)
        analysis = _analysis(expiry_date="2025-01-06")
        p, _ = self._patch_dt()
        try:
            assert strategy._is_dte_momentum_filtered(analysis) is True
        finally:
            p.stop()

    def test_allows_near_expiry(self, repo):
        """DTE < 3 should skip filter."""
        def mock_trend(minutes):
            return [
                {"spot_price": 24500},
                {"spot_price": 24400},
            ]

        strategy = IronPulseStrategy(trade_repo=repo, price_trend_fn=mock_trend)
        analysis = _analysis(expiry_date="2025-01-02")
        p, _ = self._patch_dt()
        try:
            assert strategy._is_dte_momentum_filtered(analysis) is False
        finally:
            p.stop()

    def test_allows_low_iv(self, repo):
        """IV <= 10 should skip filter."""
        strategy = IronPulseStrategy(trade_repo=repo)
        low_iv_setup = _analysis(expiry_date="2025-01-06")
        low_iv_setup["trade_setup"]["iv_at_strike"] = 8.0
        p, _ = self._patch_dt()
        try:
            assert strategy._is_dte_momentum_filtered(low_iv_setup) is False
        finally:
            p.stop()

    def test_no_price_trend_fn_skips(self, strategy):
        """No price_trend_fn injected -> skip filter."""
        analysis = _analysis(expiry_date="2025-01-06")
        p, _ = self._patch_dt()
        try:
            assert strategy._is_dte_momentum_filtered(analysis) is False
        finally:
            p.stop()


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

class TestQualityScore:
    def test_max_score(self, strategy):
        analysis = _analysis(
            confirmation_status="CONFIRMED",
            signal_confidence=75,
            verdict="Bulls Winning",
            premium_momentum={"premium_momentum_score": 15},
        )
        analysis["trade_setup"]["moneyness"] = "ITM"
        analysis["trade_setup"]["risk_pct"] = 10
        score = strategy._calculate_quality_score(analysis)
        # CONFIRMED(2) + conf 60-85(2) + Winning(1) + ITM(1) + risk<=15(1) + PM(1) = 8
        assert score == 8

    def test_zero_score(self, strategy):
        analysis = _analysis(
            confirmation_status="CONFLICT",
            signal_confidence=40,
            verdict="Neutral",
        )
        assert strategy._calculate_quality_score(analysis) == 0


# ---------------------------------------------------------------------------
# should_create — full integration
# ---------------------------------------------------------------------------

class TestShouldCreate:
    def test_all_conditions_met(self, strategy, repo):
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(_analysis()) is True

    def test_bearish_signal(self, strategy, repo):
        put_analysis = _analysis(
            verdict="Slightly Bearish",
            trade_setup={
                "direction": "BUY_PUT", "strike": 24500, "option_type": "PE",
                "moneyness": "ATM", "entry_premium": 140.0,
                "risk_pct": 20, "iv_at_strike": 12.0,
            })
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(put_analysis) is True


# ---------------------------------------------------------------------------
# create_trade
# ---------------------------------------------------------------------------

class TestCreateTrade:
    def test_creates_pending_setup(self, repo):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))
        strategy = IronPulseStrategy(trade_repo=repo, bus=bus)

        repo.insert_trade.return_value = 42
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            trade_id = strategy.create_trade(
                True, _analysis(), {}, timestamp=datetime(2025, 1, 1, 12, 0))

        assert trade_id == 42
        repo.insert_trade.assert_called_once()
        kw = repo.insert_trade.call_args[1]
        assert kw["status"] == "PENDING"
        assert kw["direction"] == "BUY_CALL"
        assert kw["strike"] == 24500
        assert kw["entry_premium"] == 150.0
        # SL: -20% and Target: +22%
        assert kw["sl_premium"] == round(150.0 * 0.80, 2)
        assert kw["target1_premium"] == round(150.0 * 1.22, 2)
        # Event published
        assert len(received) == 1
        assert received[0]["direction"] == "BUY_CALL"

    def test_no_trade_setup_returns_none(self, strategy, repo):
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.create_trade(
                True, _analysis(trade_setup=None), {})
        assert result is None

    def test_updates_direction_tracking(self, strategy, repo):
        repo.insert_trade.return_value = 1
        ts = datetime(2025, 1, 1, 12, 0)
        with patch("strategies.iron_pulse.datetime") as mock_dt:
            mock_dt.now.return_value = ts
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            strategy.create_trade(True, _analysis(), {}, timestamp=ts)
        assert strategy.last_suggested_direction == "BUY_CALL"
        assert strategy.last_suggestion_time == ts


# ---------------------------------------------------------------------------
# check_and_update — PENDING activation
# ---------------------------------------------------------------------------

class TestPendingActivation:
    def test_activates_at_entry(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(status="PENDING")
        ts = datetime(2025, 1, 1, 12, 0)
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 150.0}}, timestamp=ts)
        assert result is not None
        assert result["new_status"] == "ACTIVE"
        assert result["activation_premium"] == 150.0

    def test_activates_within_10pct(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(status="PENDING")
        ts = datetime(2025, 1, 1, 12, 0)
        # 160 is within 10% of 150 entry
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 160.0}}, timestamp=ts)
        assert result is not None
        assert result["new_status"] == "ACTIVE"

    def test_does_not_activate_above_10pct(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(status="PENDING")
        ts = datetime(2025, 1, 1, 12, 0)
        # 170 is >10% above 150 entry
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 170.0}}, timestamp=ts)
        assert result is None
        # But tracking update happened
        repo.update_trade.assert_called()

    def test_no_setup_returns_none(self, strategy, repo):
        repo.get_active_or_pending.return_value = None
        assert strategy.check_and_update({}) is None


# ---------------------------------------------------------------------------
# check_and_update — ACTIVE Phase 1 (SL/T1)
# ---------------------------------------------------------------------------

class TestActivePhase1:
    def test_sl_hit(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 13, 0)
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 115.0}}, timestamp=ts)
        assert result["new_status"] == "LOST"
        assert result["profit_loss_pct"] < 0
        # Verify DB update
        call_kw = repo.update_trade.call_args[1]
        assert call_kw["status"] == "LOST"
        assert call_kw["hit_sl"] is True

    def test_t1_hit_does_not_close(self, strategy, repo):
        """T1 hit starts trailing — trade stays ACTIVE in DB."""
        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 13, 0)
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 185.0}}, timestamp=ts)
        assert result["new_status"] == "T1_HIT"
        assert result["profit_loss_pct"] > 0
        # DB should set t1_hit=True, NOT resolve (status not changed)
        call_kw = repo.update_trade.call_args[1]
        assert call_kw["t1_hit"] is True
        assert "status" not in call_kw  # stays ACTIVE implicitly

    def test_t1_publishes_event(self, repo):
        bus = EventBus()
        t1_events = []
        bus.subscribe(EventType.T1_HIT, lambda et, d: t1_events.append(d))
        strategy = IronPulseStrategy(trade_repo=repo, bus=bus)

        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 13, 0)
        strategy.check_and_update({24500: {"ce_ltp": 185.0}}, timestamp=ts)
        assert len(t1_events) == 1
        assert "alert_message" in t1_events[0]

    def test_no_exit_between_sl_and_t1(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 13, 0)
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 155.0}}, timestamp=ts)
        assert result is None
        # Tracking updated
        repo.update_trade.assert_called()

    def test_tracks_max_premium(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 13, 0)
        strategy.check_and_update(
            {24500: {"ce_ltp": 170.0}}, timestamp=ts)
        call_kw = repo.update_trade.call_args[1]
        assert call_kw["max_premium_reached"] == 170.0
        assert call_kw["min_premium_reached"] == 150.0

    def test_zero_premium_ignored(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade()
        assert strategy.check_and_update({24500: {"ce_ltp": 0}}) is None

    def test_pe_trade_uses_pe_ltp(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(
            option_type="PE", direction="BUY_PUT")
        ts = datetime(2025, 1, 1, 13, 0)
        result = strategy.check_and_update(
            {24500: {"pe_ltp": 115.0}}, timestamp=ts)
        assert result["new_status"] == "LOST"


# ---------------------------------------------------------------------------
# check_and_update — ACTIVE Phase 2 (Trailing SL)
# ---------------------------------------------------------------------------

class TestActivePhase2:
    def test_trailing_sl_hit(self, strategy, repo):
        """After T1, trailing SL hit should resolve the trade."""
        repo.get_active_or_pending.return_value = _trade(
            t1_hit=True, peak_premium=200.0)
        ts = datetime(2025, 1, 1, 13, 30)
        # Trailing SL = 200 * 0.85 = 170. Current 165 < 170 → hit
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 165.0}}, timestamp=ts)
        assert result is not None
        assert result["new_status"] in ("WON", "LOST")
        # P&L from activation (150) to 165 = +10% → WON
        assert result["profit_loss_pct"] > 0
        assert result["new_status"] == "WON"

    def test_trailing_sl_loss(self, strategy, repo):
        """After T1, if premium drops below activation, it's a LOST."""
        repo.get_active_or_pending.return_value = _trade(
            t1_hit=True, peak_premium=190.0, activation_premium=180.0)
        ts = datetime(2025, 1, 1, 13, 30)
        # Trailing SL = 190 * 0.85 = 161.5. Current 160 < 161.5 → hit
        # P&L from activation (180) to 160 = -11.1% → LOST
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 160.0}}, timestamp=ts)
        assert result["new_status"] == "LOST"
        assert result["profit_loss_pct"] < 0

    def test_trailing_continues_above_sl(self, strategy, repo):
        """After T1, premium above trailing SL should just track."""
        repo.get_active_or_pending.return_value = _trade(
            t1_hit=True, peak_premium=200.0)
        ts = datetime(2025, 1, 1, 13, 30)
        # Trailing SL = 200 * 0.85 = 170. Current 175 > 170 → no exit
        result = strategy.check_and_update(
            {24500: {"ce_ltp": 175.0}}, timestamp=ts)
        assert result is None
        # But tracking updated
        call_kw = repo.update_trade.call_args[1]
        assert call_kw["trailing_sl"] == 200.0 * 0.85

    def test_peak_updates_with_new_high(self, strategy, repo):
        """Peak premium should update when current exceeds it."""
        repo.get_active_or_pending.return_value = _trade(
            t1_hit=True, peak_premium=200.0)
        ts = datetime(2025, 1, 1, 13, 30)
        strategy.check_and_update(
            {24500: {"ce_ltp": 210.0}}, timestamp=ts)
        call_kw = repo.update_trade.call_args[1]
        assert call_kw["peak_premium"] == 210.0
        # Trailing SL recomputed from new peak
        assert call_kw["trailing_sl"] == 210.0 * 0.85


# ---------------------------------------------------------------------------
# Exit event publishing
# ---------------------------------------------------------------------------

class TestExitEvents:
    def test_sl_publishes_trade_exited(self, repo):
        bus = EventBus()
        exited = []
        bus.subscribe(EventType.TRADE_EXITED, lambda et, d: exited.append(d))
        strategy = IronPulseStrategy(trade_repo=repo, bus=bus)

        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 13, 0)
        strategy.check_and_update({24500: {"ce_ltp": 115.0}}, timestamp=ts)
        assert len(exited) == 1
        assert exited[0]["reason"] == "SL"
        assert "alert_message" in exited[0]


# ---------------------------------------------------------------------------
# expire_pending
# ---------------------------------------------------------------------------

class TestExpirePending:
    def test_expires_at_market_close(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(status="PENDING")
        ts = datetime(2025, 1, 1, 15, 26)
        result = strategy.expire_pending(ts)
        assert result is True
        call_kw = repo.update_trade.call_args[1]
        assert call_kw["status"] == "EXPIRED"

    def test_does_not_expire_before_close(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(status="PENDING")
        ts = datetime(2025, 1, 1, 14, 0)
        result = strategy.expire_pending(ts)
        assert result is False

    def test_does_not_expire_active(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(status="ACTIVE")
        ts = datetime(2025, 1, 1, 15, 26)
        result = strategy.expire_pending(ts)
        assert result is False

    def test_no_setup_returns_false(self, strategy, repo):
        repo.get_active_or_pending.return_value = None
        result = strategy.expire_pending(datetime(2025, 1, 1, 15, 26))
        assert result is False


# ---------------------------------------------------------------------------
# force_close
# ---------------------------------------------------------------------------

class TestForceClose:
    def test_force_closes_at_time(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 15, 21)
        result = strategy.force_close(ts, {24500: {"ce_ltp": 160.0}})
        assert result is True
        call_kw = repo.update_trade.call_args[1]
        assert call_kw["status"] == "WON"  # 160 > 150 entry

    def test_force_close_loss(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 15, 21)
        result = strategy.force_close(ts, {24500: {"ce_ltp": 140.0}})
        assert result is True
        call_kw = repo.update_trade.call_args[1]
        assert call_kw["status"] == "LOST"

    def test_does_not_close_before_time(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade()
        ts = datetime(2025, 1, 1, 14, 0)
        result = strategy.force_close(ts, {})
        assert result is False

    def test_does_not_close_pending(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(status="PENDING")
        ts = datetime(2025, 1, 1, 15, 21)
        result = strategy.force_close(ts, {})
        assert result is False


# ---------------------------------------------------------------------------
# get_active / get_pending / get_stats
# ---------------------------------------------------------------------------

class TestGettersAndStats:
    def test_get_active_delegates(self, strategy, repo):
        repo.get_active.return_value = {"id": 1}
        assert strategy.get_active() == {"id": 1}
        repo.get_active.assert_called_with("trade_setups")

    def test_get_active_no_repo(self):
        strategy = IronPulseStrategy()
        assert strategy.get_active() is None

    def test_get_pending_delegates(self, strategy, repo):
        repo.get_pending.return_value = {"id": 2, "status": "PENDING"}
        assert strategy.get_pending() == {"id": 2, "status": "PENDING"}

    def test_get_stats_delegates(self, strategy, repo):
        repo.get_stats.return_value = {"total": 20, "wins": 16}
        stats = strategy.get_stats()
        assert stats["total"] == 20
        repo.get_stats.assert_called_with("trade_setups", 30)

    def test_get_stats_no_repo(self):
        strategy = IronPulseStrategy()
        stats = strategy.get_stats()
        assert stats["total"] == 0


# ---------------------------------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------------------------------

class TestDashboardHelpers:
    def test_dashboard_stats(self, strategy, repo):
        repo.get_stats.return_value = {"total": 10, "wins": 8}
        repo.get_active_or_pending.return_value = {"id": 1}
        result = strategy.get_dashboard_stats()
        assert result["has_active_setup"] is True
        assert result["stats"]["total"] == 10

    def test_active_setup_with_pnl(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(
            activation_premium=150.0)
        result = strategy.get_active_setup_with_pnl(
            {24500: {"ce_ltp": 165.0}})
        assert result is not None
        assert result["current_premium"] == 165.0
        assert result["live_pnl_pct"] == 10.0  # (165-150)/150*100

    def test_pending_setup_pnl_from_entry(self, strategy, repo):
        repo.get_active_or_pending.return_value = _trade(
            status="PENDING", activation_premium=None)
        result = strategy.get_active_setup_with_pnl(
            {24500: {"ce_ltp": 160.0}})
        assert result is not None
        # Uses entry_premium (150) as base
        expected_pnl = round((160 - 150) / 150 * 100, 2)
        assert result["live_pnl_pct"] == expected_pnl

    def test_no_setup_returns_none(self, strategy, repo):
        repo.get_active_or_pending.return_value = None
        assert strategy.get_active_setup_with_pnl({}) is None


# ---------------------------------------------------------------------------
# Alert formatting
# ---------------------------------------------------------------------------

class TestAlertFormatting:
    def test_entry_alert(self):
        trade_setup = {
            "direction": "BUY_CALL", "strike": 24500, "option_type": "CE",
        }
        alert = IronPulseStrategy._format_entry_alert(
            trade_setup, 150.0, 120.0, 183.0, _analysis())
        assert "IRON PULSE" in alert
        assert "BUY CALL" in alert
        assert "24500 CE" in alert
        assert "150.00" in alert

    def test_exit_alert_won(self):
        setup = {"direction": "BUY_CALL", "strike": 24500,
                 "option_type": "CE", "entry_premium": 150.0}
        alert = IronPulseStrategy._format_exit_alert(setup, 183.0, "TARGET", 22.0)
        assert "WON" in alert

    def test_exit_alert_lost(self):
        setup = {"direction": "BUY_PUT", "strike": 24500,
                 "option_type": "PE", "entry_premium": 140.0}
        alert = IronPulseStrategy._format_exit_alert(setup, 112.0, "SL", -20.0)
        assert "LOST" in alert
        assert "Stop Loss" in alert

    def test_t1_alert(self):
        setup = {"direction": "BUY_CALL", "strike": 24500,
                 "option_type": "CE", "entry_premium": 150.0}
        alert = IronPulseStrategy._format_t1_alert(setup, 183.0, 22.0, 155.55)
        assert "T1 HIT" in alert
        assert "Trailing SL" in alert


# ---------------------------------------------------------------------------
# Expiry date parsing
# ---------------------------------------------------------------------------

class TestParseExpiryDate:
    def test_iso_format(self):
        result = IronPulseStrategy._parse_expiry_date("2025-01-07")
        assert result == datetime(2025, 1, 7)

    def test_nse_format(self):
        result = IronPulseStrategy._parse_expiry_date("07-Jan-2025")
        assert result == datetime(2025, 1, 7)

    def test_empty_string(self):
        assert IronPulseStrategy._parse_expiry_date("") is None

    def test_invalid_format(self):
        assert IronPulseStrategy._parse_expiry_date("garbage") is None


# ---------------------------------------------------------------------------
# cancel_on_direction_change — disabled
# ---------------------------------------------------------------------------

class TestCancelDisabled:
    def test_cancel_is_noop(self, strategy):
        """cancel_on_direction_change should be a no-op."""
        strategy.cancel_on_direction_change(_analysis())
        # No exception, no side effects
