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
    SELLING_ALERT_BOT_TOKEN: str = field(
        default_factory=lambda: os.getenv("SELLING_ALERT_BOT_TOKEN", "")
    )
    SELLING_ALERT_CHAT_IDS: list = field(
        default_factory=lambda: [
            x.strip()
            for x in os.getenv("SELLING_ALERT_CHAT_IDS", "7011095516").split(",")
        ]
    )
    SELLING_ALERT_EXTRA_CHAT_IDS: list = field(
        default_factory=lambda: [
            x.strip()
            for x in os.getenv("SELLING_ALERT_EXTRA_CHAT_IDS", "").split(",")
            if x.strip()
        ]
    )
    COOLDOWN: int = 300


# ---------------------------------------------------------------------------
# Iron Pulse (trade_tracker.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IronPulseConfig:
    """Iron Pulse buying strategy — 1:1 RR, bread & butter."""
    TIME_START: time = time(11, 0)
    TIME_END: time = time(14, 0)
    FORCE_CLOSE_TIME: time = time(15, 20)
    MARKET_CLOSE: time = time(15, 25)
    SETUP_START: time = time(9, 30)
    SETUP_END: time = time(15, 15)
    SL_PCT: float = 20.0
    TARGET_PCT: float = 22.0
    MIN_CONFIDENCE: float = 65.0
    TRAILING_SL_PCT: float = 15.0
    MIN_PREMIUM_PCT: float = 0.20


# ---------------------------------------------------------------------------
# Selling (selling_tracker.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SellingConfig:
    """Options selling strategy — dual T1/T2 targets."""
    TIME_START: time = time(11, 0)
    TIME_END: time = time(14, 0)
    FORCE_CLOSE_TIME: time = time(15, 20)
    MIN_CONFIDENCE: float = 65.0
    SL_PCT: float = 25.0
    TARGET1_PCT: float = 25.0
    TARGET2_PCT: float = 50.0
    OTM_OFFSET: int = 1
    MIN_PREMIUM: float = 5.0


# ---------------------------------------------------------------------------
# Dessert (dessert_tracker.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DessertConfig:
    """Dessert premium 1:2 RR — Contra Sniper + Phantom PUT."""
    TIME_START: time = time(9, 30)
    TIME_END: time = time(14, 0)
    FORCE_CLOSE_TIME: time = time(15, 20)
    SL_PCT: float = 25.0
    TARGET_PCT: float = 50.0
    MIN_PREMIUM: float = 5.0
    CONTRA_SNIPER: str = "Contra Sniper"
    PHANTOM_PUT: str = "Phantom PUT"


# ---------------------------------------------------------------------------
# Momentum (momentum_tracker.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MomentumConfig:
    """Premium momentum reversal strategy."""
    TIME_START: time = time(12, 0)
    TIME_END: time = time(14, 0)
    FORCE_CLOSE_TIME: time = time(15, 20)
    SL_PCT: float = 25.0
    TARGET_PCT: float = 50.0
    MIN_PREMIUM: float = 5.0
    MIN_CONFIDENCE: float = 85.0
    STRATEGY_NAME: str = "Momentum"
    BEARISH_VERDICTS: tuple = ("Bears Winning", "Bears Strongly Winning")
    BULLISH_VERDICTS: tuple = ("Bulls Winning", "Bulls Strongly Winning")


# ---------------------------------------------------------------------------
# PulseRider / Price Action (pa_tracker.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PulseRiderConfig:
    """Price Action CHC-3 strategy — PulseRider."""
    TIME_START: time = time(9, 30)
    TIME_END: time = time(14, 0)
    FORCE_CLOSE_TIME: time = time(15, 20)
    SL_PCT: float = 15.0
    TARGET_PCT: float = 15.0
    MIN_PREMIUM: float = 5.0
    MAX_PREMIUM: float = 200.0
    CHC_LOOKBACK: int = 3
    CHOPPY_LOOKBACK: int = 10
    CHOPPY_THRESHOLD: float = 0.15
    VIX_WARN_THRESHOLD: float = 18.0
    STRATEGY_NAME: str = "Price Action"
    PLACE_ORDER: bool = field(
        default_factory=lambda: os.getenv("PA_PLACE_ORDER", "false").lower() == "true"
    )
    LOTS: int = field(default_factory=lambda: int(os.getenv("PA_LOTS", "1")))


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
