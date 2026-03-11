"""Tests for config.py — verify types, values, and defaults."""

import os
from datetime import time

import pytest

from config import MarketConfig, AlertConfig, ScalperConfig


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

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        cfg = AlertConfig()
        assert cfg.CHAT_ID == "12345"


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
