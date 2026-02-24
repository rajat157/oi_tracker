"""Tests for all 4 trading strategies — pure logic, no DB."""

from datetime import datetime, time
from unittest.mock import patch

import pytest

from app.strategies.iron_pulse import IronPulseStrategy
from app.strategies.selling import SellingStrategy
from app.strategies.dessert import DessertStrategy
from app.strategies.momentum import MomentumStrategy


# ── Helpers ──────────────────────────────────────────────────

def _make_analysis(verdict="Slightly Bullish", confidence=72, **overrides):
    defaults = {
        "verdict": verdict,
        "signal_confidence": confidence,
        "spot_price": 22500,
        "iv_skew": -0.5,
        "vix": 14.0,
        "max_pain": 22400,
        "trade_setup": {
            "direction": "BUY_CALL",
            "strike": 22500,
            "option_type": "CE",
            "moneyness": "ATM",
            "entry_premium": 150.0,
            "sl_premium": 120.0,
            "target1_premium": 183.0,
            "target2_premium": 216.0,
            "risk_pct": 20.0,
            "iv_at_strike": 14.0,
        },
    }
    defaults.update(overrides)
    return defaults


def _make_strikes_data():
    return {
        22450: {"ce_ltp": 200, "pe_ltp": 100, "ce_iv": 14, "pe_iv": 15},
        22500: {"ce_ltp": 150, "pe_ltp": 150, "ce_iv": 14, "pe_iv": 14.5},
        22550: {"ce_ltp": 100, "pe_ltp": 200, "ce_iv": 13.5, "pe_iv": 14},
    }


def _mock_time(h, m=0):
    """Patch datetime.now() to return a specific time."""
    return datetime(2026, 2, 24, h, m)


# ══════════════════════════════════════════════════════════════
# IRON PULSE
# ══════════════════════════════════════════════════════════════

class TestIronPulse:
    strategy = IronPulseStrategy()

    @patch("app.strategies.iron_pulse.datetime")
    def test_entry_valid(self, mock_dt):
        mock_dt.now.return_value = _mock_time(12, 0)
        result = self.strategy.should_enter(
            _make_analysis(), _make_strikes_data(),
            already_traded_today=False, has_active_trade=False,
            expiry_date="2026-02-26",
        )
        assert result is not None
        assert result["direction"] == "BUY_CALL"
        assert result["status"] == "PENDING"

    @patch("app.strategies.iron_pulse.datetime")
    def test_entry_rejected_already_traded(self, mock_dt):
        mock_dt.now.return_value = _mock_time(12, 0)
        result = self.strategy.should_enter(
            _make_analysis(), _make_strikes_data(),
            already_traded_today=True,
        )
        assert result is None

    @patch("app.strategies.iron_pulse.datetime")
    def test_entry_rejected_wrong_verdict(self, mock_dt):
        mock_dt.now.return_value = _mock_time(12, 0)
        result = self.strategy.should_enter(
            _make_analysis(verdict="Bulls Winning"), _make_strikes_data(),
        )
        assert result is None

    @patch("app.strategies.iron_pulse.datetime")
    def test_entry_rejected_low_confidence(self, mock_dt):
        mock_dt.now.return_value = _mock_time(12, 0)
        result = self.strategy.should_enter(
            _make_analysis(confidence=50), _make_strikes_data(),
        )
        assert result is None

    @patch("app.strategies.iron_pulse.datetime")
    def test_entry_rejected_outside_hours(self, mock_dt):
        mock_dt.now.return_value = _mock_time(10, 0)
        result = self.strategy.should_enter(
            _make_analysis(), _make_strikes_data(),
        )
        assert result is None

    def test_check_exit_sl_hit(self):
        trade = {
            "status": "ACTIVE",
            "entry_premium": 150,
            "sl_premium": 120,
            "target1_premium": 183,
            "activation_premium": 150,
            "t1_hit": False,
            "peak_premium": 155,
            "max_premium_reached": 155,
            "min_premium_reached": 140,
        }
        result = self.strategy.check_exit(trade, 118, datetime(2026, 2, 24, 13, 0))
        assert result["status"] == "LOST"
        assert result["hit_sl"] is True
        assert result["profit_loss_pct"] < 0

    def test_check_exit_t1_hit(self):
        trade = {
            "status": "ACTIVE",
            "entry_premium": 150,
            "sl_premium": 120,
            "target1_premium": 183,
            "activation_premium": 150,
            "t1_hit": False,
            "peak_premium": 170,
            "max_premium_reached": 170,
            "min_premium_reached": 145,
        }
        result = self.strategy.check_exit(trade, 185, datetime(2026, 2, 24, 13, 0))
        assert result["status"] == "ACTIVE"
        assert result["t1_hit"] is True
        assert result.get("_event") == "T1_HIT"

    def test_check_exit_trailing_sl_after_t1(self):
        trade = {
            "status": "ACTIVE",
            "entry_premium": 150,
            "sl_premium": 120,
            "target1_premium": 183,
            "activation_premium": 150,
            "t1_hit": True,
            "peak_premium": 200,
            "max_premium_reached": 200,
            "min_premium_reached": 145,
        }
        # Trailing SL = 200 * (1 - 0.15) = 170
        result = self.strategy.check_exit(trade, 168, datetime(2026, 2, 24, 13, 30))
        assert result["status"] == "WON"  # Still in profit (168 > 150 entry)
        assert result["exit_reason"] == "TRAILING_SL"

    def test_force_close(self):
        trade = {"status": "ACTIVE", "entry_premium": 150, "activation_premium": 150}
        assert self.strategy.should_force_close(trade, datetime(2026, 2, 24, 15, 20))
        result = self.strategy.force_close(trade, 160, datetime(2026, 2, 24, 15, 20))
        assert result["status"] == "WON"
        assert result["exit_reason"] == "EOD"

    def test_pending_activation(self):
        trade = {"entry_premium": 150}
        result = self.strategy.check_pending_activation(trade, 155, datetime(2026, 2, 24, 12, 0))
        assert result["status"] == "ACTIVE"

    def test_pending_dont_chase(self):
        trade = {"entry_premium": 150}
        # Premium moved 20% above entry — don't chase
        result = self.strategy.check_pending_activation(trade, 180, datetime(2026, 2, 24, 12, 0))
        assert result is None


# ══════════════════════════════════════════════════════════════
# SELLING
# ══════════════════════════════════════════════════════════════

class TestSelling:
    strategy = SellingStrategy()

    @patch("app.strategies.selling.datetime")
    def test_entry_bullish(self, mock_dt):
        mock_dt.now.return_value = _mock_time(12, 0)
        result = self.strategy.should_enter(
            _make_analysis(verdict="Slightly Bullish"), _make_strikes_data(),
        )
        assert result is not None
        assert result["direction"] == "SELL_PUT"

    @patch("app.strategies.selling.datetime")
    def test_entry_bearish(self, mock_dt):
        mock_dt.now.return_value = _mock_time(12, 0)
        result = self.strategy.should_enter(
            _make_analysis(verdict="Slightly Bearish"), _make_strikes_data(),
        )
        assert result is not None
        assert result["direction"] == "SELL_CALL"

    def test_check_exit_sl_premium_rises(self):
        """For sellers, SL = premium RISES."""
        trade = {
            "status": "ACTIVE",
            "entry_premium": 100,
            "sl_premium": 125,  # +25%
            "target_premium": 75,
            "target2_premium": 50,
            "t1_hit": False,
            "max_premium_reached": 110,
            "min_premium_reached": 95,
        }
        result = self.strategy.check_exit(trade, 130, datetime(2026, 2, 24, 13, 0))
        assert result["status"] == "LOST"
        assert result["profit_loss_pct"] < 0

    def test_check_exit_t2_target(self):
        """T2 = premium drops 50% → auto-exit WON."""
        trade = {
            "status": "ACTIVE",
            "entry_premium": 100,
            "sl_premium": 125,
            "target_premium": 75,
            "target2_premium": 50,
            "t1_hit": True,
            "max_premium_reached": 105,
            "min_premium_reached": 60,
        }
        result = self.strategy.check_exit(trade, 48, datetime(2026, 2, 24, 14, 0))
        assert result["status"] == "WON"
        assert result["exit_reason"] == "TARGET2"
        assert result["profit_loss_pct"] > 0

    def test_check_exit_t1_notify_stays_active(self):
        """T1 hit just records, stays ACTIVE."""
        trade = {
            "status": "ACTIVE",
            "entry_premium": 100,
            "sl_premium": 125,
            "target_premium": 75,
            "target2_premium": 50,
            "t1_hit": False,
            "max_premium_reached": 100,
            "min_premium_reached": 80,
        }
        result = self.strategy.check_exit(trade, 74, datetime(2026, 2, 24, 13, 0))
        assert result["status"] == "ACTIVE"
        assert result.get("t1_hit") is True

    def test_force_close_eod(self):
        trade = {"status": "ACTIVE", "entry_premium": 100}
        result = self.strategy.force_close(trade, 80, datetime(2026, 2, 24, 15, 20))
        assert result["status"] == "WON"
        assert result["exit_reason"] == "EOD"


# ══════════════════════════════════════════════════════════════
# DESSERT
# ══════════════════════════════════════════════════════════════

class TestDessert:
    strategy = DessertStrategy()

    @patch("app.strategies.dessert.datetime")
    def test_contra_sniper_entry(self, mock_dt):
        mock_dt.now.return_value = _mock_time(10, 0)
        analysis = _make_analysis(
            verdict="Slightly Bullish",
            iv_skew=0.5,
            max_pain=22600,  # ATM (22500) < max pain (22600)
        )
        result = self.strategy.should_enter(
            analysis, _make_strikes_data(),
            spot_move_30m=0.1,
        )
        assert result is not None
        assert result["strategy_name"] == "Contra Sniper"
        assert result["direction"] == "BUY_PUT"

    @patch("app.strategies.dessert.datetime")
    def test_phantom_put_entry(self, mock_dt):
        mock_dt.now.return_value = _mock_time(10, 0)
        analysis = _make_analysis(
            verdict="Neutral",  # Not bullish → Contra won't trigger
            confidence=40,
            iv_skew=-1.0,
        )
        result = self.strategy.should_enter(
            analysis, _make_strikes_data(),
            spot_move_30m=0.10,
        )
        assert result is not None
        assert result["strategy_name"] == "Phantom PUT"

    @patch("app.strategies.dessert.datetime")
    def test_no_entry_conditions_unmet(self, mock_dt):
        mock_dt.now.return_value = _mock_time(10, 0)
        analysis = _make_analysis(verdict="Neutral", confidence=80, iv_skew=2.0)
        result = self.strategy.should_enter(analysis, _make_strikes_data())
        assert result is None

    def test_check_exit_sl(self):
        trade = {
            "status": "ACTIVE",
            "entry_premium": 150,
            "sl_premium": 112.5,  # -25%
            "target_premium": 225,  # +50%
            "max_premium_reached": 155,
            "min_premium_reached": 140,
        }
        result = self.strategy.check_exit(trade, 110, datetime(2026, 2, 24, 12, 0))
        assert result["status"] == "LOST"

    def test_check_exit_target(self):
        trade = {
            "status": "ACTIVE",
            "entry_premium": 150,
            "sl_premium": 112.5,
            "target_premium": 225,
            "max_premium_reached": 200,
            "min_premium_reached": 145,
        }
        result = self.strategy.check_exit(trade, 230, datetime(2026, 2, 24, 12, 0))
        assert result["status"] == "WON"
        assert result["profit_loss_pct"] > 0


# ══════════════════════════════════════════════════════════════
# MOMENTUM
# ══════════════════════════════════════════════════════════════

class TestMomentum:
    strategy = MomentumStrategy()

    @patch("app.strategies.momentum.datetime")
    def test_entry_bearish_confirmed(self, mock_dt):
        mock_dt.now.return_value = _mock_time(13, 0)
        analysis = _make_analysis(
            verdict="Bears Winning",
            confidence=90,
            analysis_blob={"confirmation_status": "CONFIRMED"},
        )
        analysis["trade_setup"] = None  # Momentum doesn't use trade_setup
        result = self.strategy.should_enter(analysis, _make_strikes_data())
        assert result is not None
        assert result["direction"] == "BUY_PUT"
        assert result["confirmation_status"] == "CONFIRMED"

    @patch("app.strategies.momentum.datetime")
    def test_entry_rejected_low_confidence(self, mock_dt):
        mock_dt.now.return_value = _mock_time(13, 0)
        analysis = _make_analysis(
            verdict="Bears Winning",
            confidence=70,
            analysis_blob={"confirmation_status": "CONFIRMED"},
        )
        result = self.strategy.should_enter(analysis, _make_strikes_data())
        assert result is None

    @patch("app.strategies.momentum.datetime")
    def test_entry_rejected_not_confirmed(self, mock_dt):
        mock_dt.now.return_value = _mock_time(13, 0)
        analysis = _make_analysis(
            verdict="Bears Winning",
            confidence=90,
            analysis_blob={"confirmation_status": "CONFLICT"},
        )
        result = self.strategy.should_enter(analysis, _make_strikes_data())
        assert result is None

    @patch("app.strategies.momentum.datetime")
    def test_entry_rejected_wrong_verdict(self, mock_dt):
        mock_dt.now.return_value = _mock_time(13, 0)
        analysis = _make_analysis(
            verdict="Slightly Bullish",
            confidence=90,
            analysis_blob={"confirmation_status": "CONFIRMED"},
        )
        result = self.strategy.should_enter(analysis, _make_strikes_data())
        assert result is None

    def test_check_exit_target(self):
        trade = {
            "status": "ACTIVE",
            "entry_premium": 150,
            "sl_premium": 112.5,
            "target_premium": 225,
            "max_premium_reached": 200,
            "min_premium_reached": 145,
        }
        result = self.strategy.check_exit(trade, 230, datetime(2026, 2, 24, 13, 30))
        assert result["status"] == "WON"

    def test_force_close_loss(self):
        trade = {"status": "ACTIVE", "entry_premium": 150}
        result = self.strategy.force_close(trade, 130, datetime(2026, 2, 24, 15, 20))
        assert result["status"] == "LOST"
        assert result["exit_reason"] == "EOD"
