"""Centralized configuration classes for all strategies and system settings.

All strategy constants extracted from individual tracker files into typed config classes.
"""

import os
from datetime import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Market-wide
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketConfig:
    """Market-wide timing and instrument constants."""
    MARKET_OPEN: time = time(9, 15)
    MARKET_CLOSE: time = time(15, 30)
    NIFTY_STEP: int = 50
    NIFTY_LOT_SIZE: int = int(os.getenv("NIFTY_LOT_SIZE", "65"))


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AlertConfig:
    """Telegram alert configuration."""
    BOT_TOKEN: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    CHAT_ID: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "7011095516"))
    COOLDOWN: int = 300


# ---------------------------------------------------------------------------
# Scalper (scalper_tracker.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScalperConfig:
    """Claude-powered FNO scalper agent."""
    TIME_START: time = time(9, 30)
    TIME_END: time = time(14, 30)
    FORCE_CLOSE_TIME: time = time(15, 15)
    MAX_TRADES_PER_DAY: int = 5
    COOLDOWN_MINUTES: int = 6
    MIN_PREMIUM: float = 50.0
    MAX_PREMIUM: float = 500.0
    MIN_AGENT_CONFIDENCE: int = 60
    FALLBACK_SL_PCT: float = 8.0
    FALLBACK_TARGET_PCT: float = 10.0
    MAX_SL_PCT: float = 15.0
    PLACE_ORDER: bool = field(
        default_factory=lambda: os.getenv("SCALP_PLACE_ORDER", "false").lower() == "true"
    )
    LOTS: int = field(default_factory=lambda: int(os.getenv("SCALP_LOTS", "1")))
