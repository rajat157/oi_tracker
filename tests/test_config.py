"""Tests for config.py — verify types, values, and defaults."""

import os
from datetime import time

import pytest

from config import (
    MarketConfig, AlertConfig, IronPulseConfig, SellingConfig,
    DessertConfig, MomentumConfig, PulseRiderConfig, ScalperConfig,
)


class TestMarketConfig:
    def test_defaults(self):
        cfg = MarketConfig()
        assert cfg.MARKET_OPEN == time(9, 15)
        assert cfg.MARKET_CLOSE == time(15, 30)
        assert cfg.NIFTY_STEP == 50
        assert isinstance(cfg.NIFTY_LOT_SIZE, int)

    def test_frozen(self):
        cfg = MarketConfig()
        with pytest.raises(AttributeError):
            cfg.NIFTY_STEP = 100


class TestAlertConfig:
    def test_defaults(self):
        cfg = AlertConfig()
        assert cfg.COOLDOWN == 300
        assert isinstance(cfg.SELLING_ALERT_CHAT_IDS, list)
        assert isinstance(cfg.SELLING_ALERT_EXTRA_CHAT_IDS, list)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        cfg = AlertConfig()
        assert cfg.CHAT_ID == "12345"


class TestIronPulseConfig:
    def test_defaults(self):
        cfg = IronPulseConfig()
        assert cfg.TIME_START == time(11, 0)
        assert cfg.TIME_END == time(14, 0)
        assert cfg.FORCE_CLOSE_TIME == time(15, 20)
        assert cfg.SL_PCT == 20.0
        assert cfg.TARGET_PCT == 22.0
        assert cfg.MIN_CONFIDENCE == 65.0
        assert cfg.TRAILING_SL_PCT == 15.0
        assert cfg.MIN_PREMIUM_PCT == 0.20


class TestSellingConfig:
    def test_defaults(self):
        cfg = SellingConfig()
        assert cfg.TIME_START == time(11, 0)
        assert cfg.SL_PCT == 25.0
        assert cfg.TARGET1_PCT == 25.0
        assert cfg.TARGET2_PCT == 50.0
        assert cfg.OTM_OFFSET == 1
        assert cfg.MIN_PREMIUM == 5.0


class TestDessertConfig:
    def test_defaults(self):
        cfg = DessertConfig()
        assert cfg.TIME_START == time(9, 30)
        assert cfg.SL_PCT == 25.0
        assert cfg.TARGET_PCT == 50.0
        assert cfg.CONTRA_SNIPER == "Contra Sniper"
        assert cfg.PHANTOM_PUT == "Phantom PUT"


class TestMomentumConfig:
    def test_defaults(self):
        cfg = MomentumConfig()
        assert cfg.TIME_START == time(12, 0)
        assert cfg.MIN_CONFIDENCE == 85.0
        assert cfg.STRATEGY_NAME == "Momentum"
        assert "Bears Winning" in cfg.BEARISH_VERDICTS
        assert "Bulls Winning" in cfg.BULLISH_VERDICTS


class TestPulseRiderConfig:
    def test_defaults(self):
        cfg = PulseRiderConfig()
        assert cfg.TIME_START == time(9, 30)
        assert cfg.SL_PCT == 15.0
        assert cfg.TARGET_PCT == 15.0
        assert cfg.CHC_LOOKBACK == 3
        assert cfg.CHOPPY_LOOKBACK == 10
        assert cfg.STRATEGY_NAME == "Price Action"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("PA_PLACE_ORDER", "true")
        monkeypatch.setenv("PA_LOTS", "3")
        cfg = PulseRiderConfig()
        assert cfg.PLACE_ORDER is True
        assert cfg.LOTS == 3


class TestScalperConfig:
    def test_defaults(self):
        cfg = ScalperConfig()
        assert cfg.TIME_START == time(9, 30)
        assert cfg.TIME_END == time(14, 30)
        assert cfg.MAX_TRADES_PER_DAY == 5
        assert cfg.COOLDOWN_MINUTES == 6
        assert cfg.FORCE_CLOSE_TIME == time(15, 15)
        assert cfg.MIN_PREMIUM == 50.0
        assert cfg.FALLBACK_SL_PCT == 8.0
        assert cfg.MAX_SL_PCT == 15.0
