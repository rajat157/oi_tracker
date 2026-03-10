"""Tests for core/trade.py — enums, signals, results, active trades."""

from datetime import datetime

import pytest

from core.trade import (
    TradeStatus, TradeDirection, TradeSignal, TradeResult, ActiveTrade,
)


class TestTradeStatus:
    def test_values(self):
        assert TradeStatus.PENDING == "PENDING"
        assert TradeStatus.ACTIVE == "ACTIVE"
        assert TradeStatus.WON == "WON"
        assert TradeStatus.LOST == "LOST"
        assert TradeStatus.CANCELLED == "CANCELLED"
        assert TradeStatus.EXPIRED == "EXPIRED"

    def test_string_comparison(self):
        assert TradeStatus.ACTIVE == "ACTIVE"
        assert "WON" == TradeStatus.WON


class TestTradeDirection:
    def test_option_type(self):
        assert TradeDirection.BUY_CALL.option_type == "CE"
        assert TradeDirection.BUY_PUT.option_type == "PE"
        assert TradeDirection.SELL_CALL.option_type == "CE"
        assert TradeDirection.SELL_PUT.option_type == "PE"

    def test_is_buying(self):
        assert TradeDirection.BUY_CALL.is_buying is True
        assert TradeDirection.BUY_PUT.is_buying is True
        assert TradeDirection.SELL_CALL.is_buying is False
        assert TradeDirection.SELL_PUT.is_buying is False


class TestTradeSignal:
    def _make_signal(self, **overrides):
        defaults = dict(
            direction="BUY_CALL", strike=24500, option_type="CE",
            entry_premium=150.0, sl_premium=120.0, target_premium=183.0,
            confidence=72.0, verdict="Slightly Bullish", spot_price=24480.0,
        )
        defaults.update(overrides)
        return TradeSignal(**defaults)

    def test_round_trip_dict(self):
        sig = self._make_signal()
        d = sig.to_dict()
        assert d["strike"] == 24500
        assert d["direction"] == "BUY_CALL"
        restored = TradeSignal.from_dict(d)
        assert restored.strike == sig.strike
        assert restored.entry_premium == sig.entry_premium

    def test_from_dict_ignores_extra_keys(self):
        d = dict(
            direction="BUY_PUT", strike=24600, option_type="PE",
            entry_premium=100.0, sl_premium=75.0, target_premium=150.0,
            confidence=80.0, verdict="Bearish", spot_price=24550.0,
            unknown_key="ignored",
        )
        sig = TradeSignal.from_dict(d)
        assert sig.strike == 24600

    def test_timestamp_default(self):
        sig = self._make_signal()
        assert isinstance(sig.timestamp, datetime)


class TestTradeResult:
    def test_to_dict(self):
        r = TradeResult(
            action="WON", pnl=22.5, reason="TARGET_HIT",
            exit_premium=183.0, trade_id=42, tracker_type="iron_pulse",
        )
        d = r.to_dict()
        assert d["pnl"] == 22.5
        assert d["tracker_type"] == "iron_pulse"

    def test_from_dict(self):
        d = dict(action="LOST", pnl=-20.0, reason="SL_HIT",
                 exit_premium=120.0, trade_id=7, tracker_type="selling")
        r = TradeResult.from_dict(d)
        assert r.action == "LOST"


class TestActiveTrade:
    def test_from_db_row(self):
        row = {
            "id": 99, "strike": 24500, "option_type": "CE",
            "entry_premium": 150.0, "sl_premium": 120.0,
            "target_premium": 183.0,
        }
        at = ActiveTrade.from_db_row(row, tracker_type="iron_pulse", instrument_token=12345)
        assert at.trade_id == 99
        assert at.tracker_type == "iron_pulse"
        assert at.instrument_token == 12345
        assert at.is_selling is False

    def test_from_db_row_selling(self):
        row = {
            "id": 5, "strike": 24700, "option_type": "PE",
            "entry_premium": 80.0, "sl_premium": 100.0,
            "target_premium": 60.0,
        }
        at = ActiveTrade.from_db_row(row, tracker_type="selling",
                                      instrument_token=999, is_selling=True)
        assert at.is_selling is True

    def test_to_dict(self):
        at = ActiveTrade(
            trade_id=1, tracker_type="dessert", strike=24500,
            option_type="PE", instrument_token=555,
            entry_premium=100.0, sl_premium=75.0, target_premium=150.0,
        )
        d = at.to_dict()
        assert d["trade_id"] == 1
        assert d["tracker_type"] == "dessert"
