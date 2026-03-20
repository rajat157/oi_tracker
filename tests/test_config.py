"""Tests for config.py — verify types, values, and defaults."""

import os
from datetime import time

import pytest

from config import MarketConfig, AlertConfig


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
