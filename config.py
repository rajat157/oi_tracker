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
# Live Trading — unified order execution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveTradingConfig:
    """Live trading master switch and parameters.

    Controls whether strategies place real Kite orders or remain paper-only.
    Per-strategy override: LIVE_TRADING_STRATEGIES (comma-separated list).
    If empty, all strategies are live when ENABLED=true.
    """
    ENABLED: bool = field(
        default_factory=lambda: os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
    )
    STRATEGIES: str = field(
        default_factory=lambda: os.getenv("LIVE_TRADING_STRATEGIES", "")
    )
    LOTS: int = field(
        default_factory=lambda: int(os.getenv("LIVE_TRADING_LOTS", "1"))
    )
    PRODUCT: str = "NRML"

    @property
    def quantity(self) -> int:
        """Total quantity = lots * NIFTY lot size."""
        return self.LOTS * MarketConfig().NIFTY_LOT_SIZE


# ---------------------------------------------------------------------------
# RR (Rally Rider) — regime-adaptive, Claude-agent-powered rally catcher
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RRConfig:
    """Rally Rider — regime-adaptive, Claude-agent-powered rally catcher."""
    TIME_START: time = time(9, 30)       # widest window (regime narrows it)
    TIME_END: time = time(14, 30)
    FORCE_CLOSE_TIME: time = time(15, 15)
    MAX_TRADES_PER_DAY: int = 3
    COOLDOWN_MINUTES: int = 8
    MIN_PREMIUM: float = 80.0
    MAX_PREMIUM: float = 500.0
    MIN_AGENT_CONFIDENCE: int = 60
    TIME_EXIT_DEAD_PCT: float = 3.0
    MAX_DURATION_MIN: int = 45
    REGIME_LOOKBACK_DAYS: int = 5


# Regime -> per-regime params (SL/TGT in spot pts, converted to premium via delta)
RR_REGIME_PARAMS = {
    "HIGH_VOL_DOWN": {"signals": {"MC", "MOM", "PMOM", "NMOM"}, "sl_pts": 30, "tgt_pts": 40, "max_hold": 15,
                      "direction": "CE_ONLY", "time_start": time(10, 30), "time_end": time(14, 0),
                      "cooldown": 8, "max_trades": 2},
    "HIGH_VOL_UP":   {"signals": {"MC", "VWAP"}, "sl_pts": 25, "tgt_pts": 35, "max_hold": 35,
                      "direction": "PE_ONLY", "time_start": time(9, 45), "time_end": time(14, 15),
                      "cooldown": 8, "max_trades": 2},
    "LOW_VOL":       {"signals": {"MC"}, "sl_pts": 40, "tgt_pts": 25, "max_hold": 40,
                      "direction": "PE_ONLY", "time_start": time(10, 30), "time_end": time(14, 0),
                      "cooldown": 12, "max_trades": 1},
    "NORMAL":        {"signals": {"MC", "MOM", "PMOM", "NMOM"}, "sl_pts": 40, "tgt_pts": 20, "max_hold": 35,
                      "direction": "BOTH", "time_start": time(9, 45), "time_end": time(14, 15),
                      "cooldown": 8, "max_trades": 3},
    "TRENDING_DOWN": {"signals": {"MOM", "PMOM", "NMOM", "VWAP"}, "sl_pts": 40, "tgt_pts": 50, "max_hold": 30,
                      "direction": "PE_ONLY", "time_start": time(9, 30), "time_end": time(14, 30),
                      "cooldown": 6, "max_trades": 3},
    "TRENDING_UP":   {"signals": {"MC", "MOM", "PMOM", "NMOM"}, "sl_pts": 40, "tgt_pts": 50, "max_hold": 40,
                      "direction": "CE_ONLY", "time_start": time(9, 30), "time_end": time(14, 30),
                      "cooldown": 6, "max_trades": 3},
}
