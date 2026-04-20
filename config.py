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


# ---------------------------------------------------------------------------
# IntradayHunter — multi-index trap-theory strategy (Phase B v5 backtested)
# Backtest: 19/23 documented days aligned, PF 1.25, WR 47.1%, MDD Rs 58K
# at 1-lot sizing across BN+NF+SX. See scripts/backtest_intraday_hunter.py.
# ---------------------------------------------------------------------------

def _parse_live_indices(raw: str) -> frozenset:
    """Parse 'NIFTY,SENSEX' env var into a frozenset of upper-case index labels.

    Defaults to empty set if env var is unset (= all paper).
    Allowed values: NIFTY, BANKNIFTY, SENSEX.
    """
    if not raw:
        return frozenset()
    return frozenset(s.strip().upper() for s in raw.split(",") if s.strip())


@dataclass(frozen=True)
class IntradayHunterConfig:
    """IntradayHunter — multi-index BN+NF+SX trap-theory strategy.

    All defaults match scripts/backtest_intraday_hunter.py v5 final config.
    Values were locked in via walk-forward validation + ceiling-breaking
    experiments. See docs/strategy_research/STRATEGY_RESEARCH.md §6 for the
    full backtest history.

    ── Env vars (overrides) ──
        INTRADAY_HUNTER_ENABLED   : 'true' to register the strategy with the
                                    scheduler (default: false → strategy is
                                    built but not active)
        IH_LIVE_INDICES           : comma-separated list of indices to trade
                                    LIVE (real broker orders). Indices NOT in
                                    this list trade as paper. Empty = all paper.
                                    Example: 'NIFTY,SENSEX' (BN stays paper)
        IH_LOTS                   : multiplier on the per-index 1-lot quantity
                                    (default: 1)
    """
    # ── Master switch ──
    ENABLED: bool = field(
        default_factory=lambda: os.getenv("INTRADAY_HUNTER_ENABLED", "false").lower() == "true"
    )
    LIVE_INDICES: frozenset = field(
        default_factory=lambda: _parse_live_indices(os.getenv("IH_LIVE_INDICES", ""))
    )
    LOTS: int = field(
        default_factory=lambda: int(os.getenv("IH_LOTS", "1"))
    )

    # ── Trading window (R6+R7: wait 2-5 candles, never enter on first candle) ──
    TIME_START: time = time(9, 35)        # 09:35 sweet spot (vs 09:30 = random WR)
    TIME_END: time = time(11, 30)         # morning-only trader
    TIME_EXIT: time = time(12, 30)        # Phase F time exit
    FORCE_CLOSE_TIME: time = time(15, 15)

    # ── Daily limits ──
    MAX_TRADES_PER_DAY: int = 3
    DAILY_LOSS_LIMIT_RS: float = 3000.0   # 1-lot scaled (~50K at trader's qty)
    # R28 — "after X losing days, take a day off". Psychological rule for
    # discretionary traders that doesn't apply to automated systems (no
    # revenge-trading impulse). Set to 0 to disable entirely.
    # Rule is additionally guarded against counting trades whose notes
    # contain "R28_EXCLUDED" — lets us exclude losses from bug-era days.
    CONSECUTIVE_LOSS_SKIP: int = 0        # disabled by default
    COOLDOWN_AFTER_WIN_MIN: int = 60      # walk-forward optimal (was 90)
    COOLDOWN_AFTER_LOSS_MIN: int = 30

    # ── Per-index lot sizes (1 lot each, multiplied by LOTS env var) ──
    NIFTY_LOT_QTY: int = 65               # 1 lot = 65
    BANKNIFTY_LOT_QTY: int = 30           # 1 lot = 30
    SENSEX_LOT_QTY: int = 20              # 1 lot = 20

    @property
    def NIFTY_QTY(self) -> int:
        return self.NIFTY_LOT_QTY * self.LOTS

    @property
    def BANKNIFTY_QTY(self) -> int:
        return self.BANKNIFTY_LOT_QTY * self.LOTS

    @property
    def SENSEX_QTY(self) -> int:
        return self.SENSEX_LOT_QTY * self.LOTS

    @property
    def MAX_GROUPS_PER_DAY(self) -> int:
        """Alias for MAX_TRADES_PER_DAY used by story_state()."""
        return self.MAX_TRADES_PER_DAY

    def is_index_live(self, index_label: str) -> bool:
        """Returns True if this index should place real broker orders.

        An index trades LIVE only when:
            1. The master LiveTradingConfig.ENABLED is True (handled outside)
            2. The index is in IH_LIVE_INDICES env var
        """
        return index_label.upper() in self.LIVE_INDICES

    # ── Premium pricing (Black-Scholes via kite/iv.py) ──
    RISK_FREE_RATE: float = 0.07
    DEFAULT_IV: float = 0.13
    BN_IV_SCALE: float = 1.30             # BANKNIFTY ~30% more vol than NIFTY
    SX_IV_SCALE: float = 1.20             # SENSEX ~20% more vol
    SL_PCT: float = 0.20                  # 20% premium SL
    TGT_PCT: float = 0.45                 # 45% premium target (sweep optimal)

    # ── E1 (rejection after directional run) ──
    E1_RUN_LENGTH: int = 5
    E1_RETRACEMENT_MAX_PCT: float = 0.4
    E1_MIN_RETR_CANDLES: int = 1
    E1_MAX_RETR_CANDLES: int = 3
    MULTI_INDEX_MIN: int = 2              # 2-of-3 must agree

    # ── E2 (gap counter-trap) ──
    E2_ENABLED: bool = True
    E2_MIN_GAP_PCT: float = 0.20
    E2_WAIT_MINUTES: int = 15

    # ── E3 (trend continuation / slow drift) ──
    E3_ENABLED: bool = True
    E3_MIN_YCLOSE_PCT: float = 0.10
    E3_MIN_GAP_PCT: float = 0.10
    E3_WAIT_MINUTES: int = 20
    E3_MAX_CURRENT_PCT: float = 0.60

    # ── R29 HDFC + KOTAK constituent confluence ──
    ENABLE_CONSTITUENT_CONFLUENCE: bool = True
    CONSTITUENT_MIN_PCT: float = 0.15
    CONSTITUENT_REJECT_ALL: bool = True
    # Internal split: HDFC vs KOTAK disagreement → skip BN component only
    ENABLE_CONSTITUENT_INTERNAL_SPLIT: bool = True
    CONSTITUENT_INTERNAL_SPLIT_PCT: float = 0.30

    # ── Day-bias filter (continuous score, frozen at first eligible minute) ──
    ENABLE_DAY_BIAS: bool = True
    DAY_BIAS_BLOCK_SCORE: float = 0.60     # walk-forward optimal
    DAY_BIAS_INPUT_THRESHOLD_PCT: float = 0.30

    # ── R45 one-direction-per-day ──
    ENABLE_ONE_DIR_PER_DAY: bool = True

    # ── Other (kept OFF per backtest) ──
    ENABLE_TREND_CONFLICT: bool = False    # Too strict
    ENABLE_OPENING_ZONE: bool = False      # No measurable benefit
    ENABLE_GAP_BREAKOUT_BLOCK: bool = False  # Tested, rejected

    # ── Claude agent (signal confirmation + active monitoring) ──
    AGENT_ENABLED: bool = field(
        default_factory=lambda: os.getenv("IH_AGENT_ENABLED", "true").lower() == "true"
    )
    AGENT_MONITOR_THROTTLE_SEC: int = 180   # min seconds between monitor calls per position
    # [V5] Agent memory + cooldown
    AGENT_HISTORY_LENGTH: int = 5           # last N decisions passed to confirm_signal
    AGENT_REJECTION_COOLDOWN_SEC: int = 300  # don't re-ask agent for same direction within 5 min of rejection

    # ── [V1] E0 — gap-rejection-recovery (early-entry trigger) ──
    # The trader's favorite gap-day setup. Yesterday directional + today's
    # first candle moves sharply against yesterday + 2-3 recovery candles
    # back in yesterday's direction. Enters in yesterday's direction.
    # Backtest gain over 2.3 years: +Rs 11,918 (PF 1.25 → 1.26).
    ENABLE_E0: bool = field(
        default_factory=lambda: os.getenv("IH_ENABLE_E0", "true").lower() == "true"
    )
    E0_MIN_YDAY_PCT: float = 0.30            # yesterday |move| >= 0.30%
    E0_MIN_INITIAL_PCT: float = 0.20         # first candle move against yesterday >= 0.20%
    E0_MIN_RECOVERY_PCT: float = 0.10        # recovery in yesterday direction >= 0.10%
    E0_ENTRY_START: time = time(9, 17)       # earlier than normal TIME_START
    E0_MAX_MINUTE: int = 10                  # must fire by minute 10 (~09:25)

    # ── [V2] Multi-day regime in day_bias_score ──
    # Adds yesterday's close-position-within-range as a new weighted input.
    # Close near high (>= 80%) = bullish; close near low (<= 20%) = bearish.
    # Backtest gain over 2.3 years: +Rs 7,030 (free improvement).
    ENABLE_MULTI_DAY_REGIME: bool = field(
        default_factory=lambda: os.getenv("IH_ENABLE_MULTI_DAY_REGIME", "true").lower() == "true"
    )
