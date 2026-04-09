"""
IntradayHunter Strategy Backtester (Phase B v0)

Replays 1-minute historical data for NIFTY/BANKNIFTY/SENSEX
and simulates the IntradayHunter strategy day-by-day per
docs/strategy_research/STRATEGY_RESEARCH.md.

This is a STAGED implementation:
  v0 (this file): data loading, day-by-day loop, regime classification, stub
                  signal detection. Verifies the loop works on real data.
  v1: entry triggers E1-E4 (findings_v4.md §4 Phase C)
  v2: filter rules R32/R34/R36/R39/R44
  v3: position management + Black-Scholes premium model + staged entry
  v4: exit decision tree (Phase F) + loss limit
  v5: parameter sweeps + walk-forward validation

Usage:
    uv run python scripts/backtest_intraday_hunter.py
    uv run python scripts/backtest_intraday_hunter.py --start 2025-09-01 --end 2026-04-08
    uv run python scripts/backtest_intraday_hunter.py --days 5  # smoke test
"""

import argparse
import csv
import itertools
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.connection import DB_PATH
from kite.iv import black_scholes_price

# ── Constants from findings_v4.md ─────────────────────────────────────────

# Trading window (R44 + time-of-day rules from §5)
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)

# IntradayHunter is a morning trader (entries 9:30-12:00, exits before ~12:30)
ENTRY_WINDOW_START = time(9, 30)
ENTRY_WINDOW_END = time(12, 0)
TIME_BASED_EXIT_CUTOFF = time(12, 30)  # Phase F time exit (R20)

# Position sizing — ONE lot per index per current NSE/BSE lot sizes.
# (Trader videos show larger scaled quantities, but for backtest we use 1 lot
#  to keep absolute P&L easy to interpret as "what 1 lot each would yield".)
POSITION_QTY = {
    "NIFTY":     65,   # 1 lot = 65 (per project memory)
    "BANKNIFTY": 30,   # 1 lot = 30
    "SENSEX":    20,   # 1 lot = 20
}

# Round numbers — only 1000s and 500s (R18, dTLpnuOGu-o explicit)
ROUND_NUMBER_INCREMENT = {
    "NIFTY": 500,
    "BANKNIFTY": 500,
    "SENSEX": 1000,
}

# Strike spacing for ATM option lookup
STRIKE_SPACING = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
}

# Option-pricing assumptions
RISK_FREE_RATE = 0.07
DEFAULT_IV = 0.13   # used as fallback if VIX-based IV is disabled
EXPIRY_DOW = {
    "NIFTY":     1,  # Tuesday (weekly NIFTY expiry per CLAUDE.md)
    "BANKNIFTY": 2,  # Wednesday weekly
    "SENSEX":    1,  # Tuesday weekly
}

# Default backtest period (matches the v4 findings video review window)
DEFAULT_START = "2024-01-01"
DEFAULT_END = "2026-04-08"


# ── Configuration: every parameter the strategy can be tuned on ──────────

@dataclass
class BacktestConfig:
    """All tunable backtest parameters in one place.

    Defaults are the v1 baseline; sweeps override individual fields.
    Filters can be toggled on/off independently to A/B test their value.
    """
    # ── E1 (rejection after directional run) ──
    e1_run_length: int = 5
    e1_retracement_max_pct: float = 0.4
    e1_min_retr_candles: int = 1
    e1_max_retr_candles: int = 3

    # ── E2 (gap counter-trap) ──
    # When today gaps significantly and the prior swing is NOT broken in
    # the first wait_minutes, fire AGAINST the gap (it's a trap).
    e2_enabled: bool = True
    e2_min_gap_pct: float = 0.20         # require >= 0.20% gap
    e2_wait_minutes: int = 15            # wait 15 minutes for confirmation
    e2_swing_lookback_days: int = 1      # use yesterday's hi/lo as the level

    # ── E3 (trend continuation / slow drift) ──
    # When yesterday was directional + today gaps the same way + price stays
    # on the gap side after the wait period, ride the trend.
    # This catches "slow grinding" days like 2025-12-22 where E1 (no run)
    # and E2 (gap held, swing broken) both stay silent.
    e3_enabled: bool = True
    e3_min_yclose_pct: float = 0.10       # |yesterday move| >= 0.10%
    e3_min_gap_pct: float = 0.10          # |gap| >= 0.10% (small but present)
    e3_wait_minutes: int = 20             # wait 20 minutes after open
    e3_max_current_pct: float = 0.60      # don't fire if already too far (> 0.60% from open)

    # ── Multi-index requirement ──
    multi_index_min: int = 2  # 2-of-3 minimum (3 = strict consensus)

    # ── SL / Target on option premium ──
    sl_pct: float = 0.20    # 20% of premium as hard SL
    tgt_pct: float = 0.45   # 45% of premium as first target (sweep-optimal)

    # ── R33: Trend Conflict Filter (gap-respect) ──
    # OFF: blocks too many good signals; net negative on alignment.
    enable_trend_conflict: bool = False
    trend_conflict_min_pct: float = 0.15

    # ── R45: One Direction Per Day ──
    # Don't flip from BUY to SELL (or vice versa) within the same day.
    # Reduces revenge-trading.
    enable_one_dir_per_day: bool = True

    # ── Cooldown after a closed trade ──
    enable_cooldown: bool = True
    cooldown_after_win_min: int = 60    # walk-forward optimal (was 90)
    cooldown_after_loss_min: int = 30   # 30 minutes after a losing trade

    # ── R34: Opening Zone Filter ──
    enable_opening_zone: bool = False  # OFF: little observed effect per sweep
    opening_zone_pct: float = 0.0020

    # ── Gap-Breakout Filter (TESTED, REJECTED) ──
    # Idea: open above yesterday's high → block SELLs; below yesterday's low → block BUYs
    # Result: blocked too many good trend-continuation signals; alignment 19→17
    # Kept disabled in defaults but the helper is preserved for reference.
    enable_gap_breakout_block: bool = False

    # ── R28: Consecutive-loss circuit breaker ──
    # Trader's stated rule: "after 3 losses take 1 day off". In practice
    # he violates it (e.g., 2025-12-22 was traded after a 3-loss streak).
    # Setting threshold to 4 better matches observed behavior.
    enable_loss_circuit: bool = True
    consecutive_loss_skip: int = 4

    # ── Day-Bias filter (continuous score, soft-block) ──
    # Computes a continuous score in [-1, 1] from 5 weighted inputs:
    #   1. Yesterday's day-move    (weight 0.20)
    #   2. Today's gap             (weight 0.30)
    #   3. Intraday move from open (weight 0.30)
    #   4. HDFC % move from open   (weight 0.10)
    #   5. KOTAK % move from open  (weight 0.10)
    # Each input is normalised to [-1, 1] using its threshold then weighted.
    # The signal is REJECTED only when |score| >= cfg.day_bias_block_score
    # AND the score sign is opposite to the signal direction.
    # This is a "soft veto" — only blocks on STRONG bias.
    enable_day_bias: bool = True
    day_bias_block_score: float = 0.60  # walk-forward optimal (was 0.50)
    day_bias_input_threshold_pct: float = 0.30  # 0.30% normalises to ±1.0 per input

    # ── R29: HDFC + KOTAK constituent confluence (per kOC9UPLfQ7g) ──
    # HDFC Bank and Kotak Mahindra Bank are the top-2 weights in BANKNIFTY
    # (~30%+ combined). When BN moves but the constituents don't agree,
    # it's a fake-out. Use them to GATE the BN component of a signal:
    # if HDFC + KOTAK don't confirm the direction, skip the BN entry
    # (NIFTY + SENSEX entries still happen).
    enable_constituent_confluence: bool = True
    constituent_min_pct: float = 0.15  # walk-forward optimal (was 0.20)
    # New: when HDFC and KOTAK strongly disagree WITH EACH OTHER (e.g.,
    # HDFC up + KOTAK down), skip just the BN position. Catches days where
    # BN is a fake-out (one constituent dragging it).
    enable_constituent_internal_split: bool = True
    constituent_internal_split_pct: float = 0.30  # |HDFC - KOTAK| >= 0.30%
    # When True, rejects the ENTIRE signal (all 3 indices) on constituent divergence.
    # When False, only skips the BN component.
    constituent_reject_all: bool = True

    # ── VIX-based IV (more realistic premium model) ──
    enable_vix_iv: bool = True
    bn_iv_scale: float = 1.30            # BN tends to be 30% more vol than NF
    sx_iv_scale: float = 1.20

    # ── Trading window ──
    # R6+R7 (findings_v4.md §5): "wait 2-5 candles, never enter on the first
    # momentum candle". Entries before 09:35 are 45% WR (random); entries
    # 09:35-10:00 are 60-80% WR. So we hold the window open from 09:35.
    entry_start: time = field(default_factory=lambda: time(9, 35))
    entry_end: time = field(default_factory=lambda: time(11, 30))
    time_exit: time = field(default_factory=lambda: time(12, 30))

    # ── Daily limits ──
    # NB: with 1-lot sizing, daily P&L is ~25-40x smaller than scaled qty.
    # Adjust loss limit proportionally.
    max_trades_per_day: int = 3
    daily_loss_limit: float = 3_000


# A global default config (replaced when running sweeps)
CFG = BacktestConfig()


# ── Documented R3+R4 days for ground-truth validation ────────────────────

# 23 days from findings_v4.md §6+§7 + RESUME_PROMPT.md.
# Outcome: 'W' = trader made profit, 'L' = trader took loss.
# Days marked '?' in earlier notes are normalized to 'W' (default-positive
# since the videos describe profitable trades unless explicitly a loss).
DOCUMENTED_DAYS: List[Tuple[str, str]] = [
    ("2024-12-11", "W"), ("2024-12-12", "W"), ("2024-12-13", "W"),  # Dec 13 = the "4.8L profit" day
    ("2025-08-22", "W"), ("2025-09-03", "W"), ("2025-09-12", "W"),
    ("2025-09-22", "W"), ("2025-09-29", "W"), ("2025-10-06", "W"),
    ("2025-10-15", "W"), ("2025-10-23", "L"), ("2025-10-30", "W"),
    ("2025-11-04", "L"),
    ("2025-12-22", "W"), ("2026-01-14", "W"), ("2026-01-21", "L"),
    ("2026-01-28", "W"), ("2026-02-09", "W"), ("2026-02-12", "W"),
    ("2026-02-16", "W"), ("2026-03-19", "W"), ("2026-03-23", "W"),
    ("2026-03-25", "W"),
]

# Walk-forward split: cutoff 2025-12-01 → TRAIN = 12 days, TEST = 11 days
WF_TRAIN_CUTOFF = "2025-12-01"
TRAIN_DAYS = [(d, w) for (d, w) in DOCUMENTED_DAYS if d < WF_TRAIN_CUTOFF]
TEST_DAYS = [(d, w) for (d, w) in DOCUMENTED_DAYS if d >= WF_TRAIN_CUTOFF]


# ── Data structures ───────────────────────────────────────────────────────

@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def is_green(self) -> bool:
        return self.close >= self.open

    @property
    def range_pts(self) -> float:
        return self.high - self.low


@dataclass
class Position:
    """Open option position (one of 3 per trade — BN/SX/NF)."""
    index: str
    direction: str            # 'CALL' or 'PUT'
    strike: float
    entry_ts: datetime
    entry_premium: float
    qty: int
    sl_premium: float
    target_premium: float
    notes: str = ""


@dataclass
class Trade:
    """Closed trade record (one row per index×day; a multi-index entry = 3 rows)."""
    day: date
    index: str
    direction: str
    strike: float
    entry_ts: datetime
    exit_ts: datetime
    entry_premium: float
    exit_premium: float
    qty: int
    pnl: float
    exit_reason: str
    notes: str = ""


@dataclass
class DayState:
    """Per-day state for the simulation loop. Reset at SESSION_START each day."""
    day: date
    trades_count: int = 0
    closed_trades: List[Trade] = field(default_factory=list)
    open_positions: List[Position] = field(default_factory=list)
    daily_pnl: float = 0.0
    loss_limit_hit: bool = False
    consecutive_losses: int = 0  # carries from prior days; reset on a winning day


# ── Data loading ──────────────────────────────────────────────────────────

def load_candles(
    conn: sqlite3.Connection,
    label: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> Dict[date, List[Candle]]:
    """Load candles grouped by trading day.

    Returns: {date: [Candle, ...]} sorted by timestamp.
    Days with no data are simply absent from the dict.
    """
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM instrument_history "
        "WHERE label = ? AND interval = ? "
        "  AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC",
        (
            label,
            interval,
            start.strftime("%Y-%m-%d %H:%M:%S"),
            end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    ).fetchall()

    by_day: Dict[date, List[Candle]] = defaultdict(list)
    for ts_str, o, h, lo, c, v in rows:
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        by_day[ts.date()].append(Candle(ts=ts, open=o, high=h, low=lo, close=c, volume=v))
    return dict(by_day)


def load_all_indices(
    conn: sqlite3.Connection,
    start: datetime,
    end: datetime,
    include_constituents: bool = True,
) -> Dict[str, Dict[date, List[Candle]]]:
    """Load 1-min candles for the 3 primary indices + (optionally) constituents.

    Returns: {label: {date: [Candle, ...]}}
    Constituents (HDFC, KOTAK) are loaded for the R29 confluence filter.
    """
    indices = {}
    labels = ["NIFTY", "BANKNIFTY", "SENSEX"]
    if include_constituents:
        labels += ["HDFCBANK", "KOTAKBANK"]
    for label in labels:
        print(f"  Loading {label} 1min...")
        by_day = load_candles(conn, label, "1min", start, end)
        n_days = len(by_day)
        n_candles = sum(len(v) for v in by_day.values())
        print(f"    {n_days:>4} days, {n_candles:>8,} candles")
        indices[label] = by_day
    return indices


# ── Regime classification (R3 §3 — uses prior day's move) ────────────────

def classify_regime(
    nifty_today: List[Candle],
    nifty_yesterday: Optional[List[Candle]],
) -> str:
    """Classify the day's regime using yesterday's move + today's gap.

    Per findings_v4.md §3, the regimes are:
        BULLISH       — yesterday strong up; buyers in market
        BEARISH       — yesterday strong down; sellers in profit
        SIDEWAYS_CHOP — yesterday sideways or 1up/1dn pattern
        FLAT          — minimal opening move
    """
    if not nifty_yesterday or not nifty_today:
        return "UNKNOWN"

    y_open = nifty_yesterday[0].open
    y_close = nifty_yesterday[-1].close
    y_move = y_close - y_open
    y_move_pct = (y_move / y_open) * 100

    today_open = nifty_today[0].open
    gap = today_open - y_close
    gap_pct = (gap / y_close) * 100

    if y_move_pct >= 0.5:
        regime = "BULLISH"
    elif y_move_pct <= -0.5:
        regime = "BEARISH"
    else:
        regime = "SIDEWAYS_CHOP"

    return f"{regime}|gap={gap_pct:+.2f}%"


# ── Helper functions ──────────────────────────────────────────────────────

def nearest_round_number(price: float, increment: int) -> float:
    return round(price / increment) * increment


def atm_strike(spot: float, spacing: int) -> float:
    return round(spot / spacing) * spacing


def days_to_next_expiry(d: date, dow: int) -> int:
    """Days until the next expiry weekday (Mon=0..Sun=6).

    Returns at least 1 (we never have 0 days to expiry mid-day).
    """
    today_dow = d.weekday()
    diff = (dow - today_dow) % 7
    return diff if diff != 0 else 7


def model_premium(
    spot: float,
    strike: float,
    option_type: str,
    days_to_expiry: int,
    iv: float = DEFAULT_IV,
) -> float:
    """Black-Scholes ATM option premium estimate."""
    t_years = days_to_expiry / 365.0
    return black_scholes_price(
        spot=spot, strike=strike, t=t_years,
        r=RISK_FREE_RATE, sigma=iv, option_type=option_type,
    )


# ── Signal detection (E1 — rejection after momentum) ─────────────────────

def detect_e1(
    minute_idx: int,
    candles: List[Candle],
    cfg: BacktestConfig,
) -> Optional[str]:
    """E1: small retracement after a directional run. Returns 'BUY'/'SELL'/None."""
    min_lookback = cfg.e1_run_length + cfg.e1_max_retr_candles
    if minute_idx < min_lookback:
        return None

    for retr_len in range(cfg.e1_min_retr_candles, cfg.e1_max_retr_candles + 1):
        run_end = minute_idx - retr_len
        run_start = run_end - cfg.e1_run_length
        if run_start < 0:
            continue

        run = candles[run_start:run_end]
        retr = candles[run_end:minute_idx]

        all_green = all(c.is_green for c in run)
        all_red = all(not c.is_green for c in run)
        if not (all_green or all_red):
            continue

        run_direction = "BUY" if all_green else "SELL"
        run_pts = abs(run[-1].close - run[0].open)
        if run_pts <= 0:
            continue

        retr_pts = abs(retr[-1].close - run[-1].close)
        retr_direction_correct = (
            (run_direction == "BUY" and retr[-1].close < run[-1].close)
            or (run_direction == "SELL" and retr[-1].close > run[-1].close)
        )
        if not retr_direction_correct:
            continue

        if retr_pts / run_pts > cfg.e1_retracement_max_pct:
            continue

        return run_direction

    return None


def detect_signals(
    minute_idx: int,
    nifty: List[Candle],
    bn: List[Candle],
    sx: List[Candle],
    state: DayState,
    cfg: BacktestConfig,
    nifty_yesterday: Optional[List[Candle]] = None,
) -> List[dict]:
    """Run all entry-trigger detectors and return matching signals.

    v2: E1 + E2 + multi-index N-of-3 confirmation.
    """
    signals = []

    # E1 on each of the 3 indices
    for label, candles in (("NIFTY", nifty), ("BANKNIFTY", bn), ("SENSEX", sx)):
        side = detect_e1(minute_idx, candles, cfg)
        if side:
            signals.append({
                "trigger": "E1",
                "primary_index": label,
                "direction": side,
                "entry_idx": minute_idx,
            })

    # E2 on NIFTY only (gap counter-trap is a market-wide signal)
    e2_side = detect_e2(minute_idx, nifty, nifty_yesterday, cfg)
    if e2_side:
        signals.append({
            "trigger": "E2",
            "primary_index": "NIFTY",
            "direction": e2_side,
            "entry_idx": minute_idx,
        })

    # E3 on NIFTY (trend continuation / slow drift)
    e3_side = detect_e3(minute_idx, nifty, nifty_yesterday, cfg)
    if e3_side:
        signals.append({
            "trigger": "E3",
            "primary_index": "NIFTY",
            "direction": e3_side,
            "entry_idx": minute_idx,
        })

    # Multi-index confirmation: count by direction
    by_side: Dict[str, List[str]] = {"BUY": [], "SELL": []}
    for s in signals:
        if s["trigger"] == "E1":
            by_side[s["direction"]].append(s["primary_index"])

    confirmed: List[dict] = []

    # E2 fires unconditionally (its premise is the gap-trap, no multi-index gate)
    confirmed.extend(s for s in signals if s["trigger"] == "E2")
    # E3 fires unconditionally (its premise is multi-day context, no multi-index gate)
    confirmed.extend(s for s in signals if s["trigger"] == "E3")

    # E1 needs cfg.multi_index_min agreeing indices
    for side, indices in by_side.items():
        if len(indices) >= cfg.multi_index_min:
            confirmed.append({
                "trigger": "E1",
                "direction": side,
                "agreeing": indices,
                "entry_idx": minute_idx,
            })
    return confirmed


# ── Position simulation ───────────────────────────────────────────────────

def _iv_for_index(label: str, cfg: BacktestConfig, vix_iv: Optional[float]) -> float:
    """Resolve IV for a given index. Uses VIX-based IV if cfg.enable_vix_iv,
    else falls back to DEFAULT_IV. BN/SX scaled by cfg.bn_iv_scale / sx_iv_scale.
    """
    base = (vix_iv if (cfg.enable_vix_iv and vix_iv) else DEFAULT_IV)
    if label == "BANKNIFTY":
        return base * cfg.bn_iv_scale
    if label == "SENSEX":
        return base * cfg.sx_iv_scale
    return base


def open_positions(
    signal: dict,
    nifty: Candle,
    bn: Candle,
    sx: Candle,
    today: date,
    cfg: BacktestConfig,
    vix_iv: Optional[float] = None,
    constituent_ok: bool = True,
) -> List[Position]:
    """Open one option position per index for a confirmed multi-index signal.

    If `constituent_ok` is False (R29 confluence failed), skip the BANKNIFTY
    component and only enter NIFTY + SENSEX. This reflects HDFC + KOTAK
    failing to confirm the BN direction.
    """
    positions: List[Position] = []
    direction = signal["direction"]
    otype = "CE" if direction == "BUY" else "PE"

    snapshots = {
        "NIFTY": nifty,
        "BANKNIFTY": bn,
        "SENSEX": sx,
    }

    for label, candle in snapshots.items():
        # R29: skip BN if constituent confluence failed
        if label == "BANKNIFTY" and not constituent_ok:
            continue
        spot = candle.close
        strike = atm_strike(spot, STRIKE_SPACING[label])
        dte = days_to_next_expiry(today, EXPIRY_DOW[label])
        iv = _iv_for_index(label, cfg, vix_iv)
        premium = model_premium(spot, strike, otype, dte, iv=iv)
        if premium <= 0:
            continue
        sl = premium * (1 - cfg.sl_pct)
        target = premium * (1 + cfg.tgt_pct)

        bn_skip = " bn_skip=R29" if (label == "BANKNIFTY" and not constituent_ok) else ""
        positions.append(Position(
            index=label,
            direction=direction,
            strike=strike,
            entry_ts=candle.ts,
            entry_premium=premium,
            qty=POSITION_QTY[label],
            sl_premium=sl,
            target_premium=target,
            notes=f"trigger={signal['trigger']} dte={dte} iv={iv:.3f}{bn_skip}",
        ))
    return positions


def check_exits(
    state: DayState,
    nifty: Candle,
    bn: Candle,
    sx: Candle,
    force_close: bool = False,
    cfg: Optional[BacktestConfig] = None,
    vix_iv: Optional[float] = None,
) -> List[Trade]:
    """Check exit conditions for all open positions and return closed trades."""
    cfg = cfg or CFG
    if not state.open_positions:
        return []

    snapshots = {"NIFTY": nifty, "BANKNIFTY": bn, "SENSEX": sx}
    closed: List[Trade] = []
    still_open: List[Position] = []
    time_exit = cfg.time_exit

    for pos in state.open_positions:
        candle = snapshots.get(pos.index)
        if candle is None:
            still_open.append(pos)
            continue

        spot = candle.close
        otype = "CE" if pos.direction == "BUY" else "PE"
        dte = days_to_next_expiry(candle.ts.date(), EXPIRY_DOW[pos.index])
        iv = _iv_for_index(pos.index, cfg, vix_iv)
        cur_premium = model_premium(spot, pos.strike, otype, dte, iv=iv)

        exit_reason = None
        if cur_premium <= pos.sl_premium:
            exit_reason = "SL_HIT"
        elif cur_premium >= pos.target_premium:
            exit_reason = "TGT_HIT"
        elif candle.ts.time() >= time_exit:
            exit_reason = "TIME_EXIT"
        elif force_close:
            exit_reason = "EOD_FORCE"

        if exit_reason:
            pnl = (cur_premium - pos.entry_premium) * pos.qty
            closed.append(Trade(
                day=pos.entry_ts.date(),
                index=pos.index,
                direction=pos.direction,
                strike=pos.strike,
                entry_ts=pos.entry_ts,
                exit_ts=candle.ts,
                entry_premium=pos.entry_premium,
                exit_premium=cur_premium,
                qty=pos.qty,
                pnl=pnl,
                exit_reason=exit_reason,
                notes=pos.notes,
            ))
        else:
            still_open.append(pos)

    state.open_positions = still_open
    return closed


# ── The main day loop ────────────────────────────────────────────────────

# ── Filter helpers (R33, R34, R45, cooldown) ─────────────────────────────

def compute_gap_pct(today: List[Candle], yesterday: Optional[List[Candle]]) -> float:
    if not yesterday or not today:
        return 0.0
    return (today[0].open - yesterday[-1].close) / yesterday[-1].close * 100


def compute_first_hour_pct(today: List[Candle], minutes: int = 60) -> float:
    """% move from session open to N minutes in (using close)."""
    if not today:
        return 0.0
    end_idx = min(minutes, len(today)) - 1
    if end_idx <= 0:
        return 0.0
    return (today[end_idx].close - today[0].open) / today[0].open * 100


def compute_current_move_pct(today: List[Candle], minute_idx: int) -> float:
    """% move from session open to the current minute (uses min(15, idx) for stability)."""
    if not today or minute_idx <= 0:
        return 0.0
    # Use the close 15 minutes ago (or earliest available) for stability:
    # this dampens noise from the very first 1-min candles.
    lookback_idx = max(0, minute_idx - 15)
    base = today[lookback_idx].close
    if base <= 0:
        return 0.0
    return (today[minute_idx].close - today[0].open) / today[0].open * 100


def filter_trend_conflict(
    direction: str,
    gap_pct: float,
    current_move_pct: float,
    cfg: BacktestConfig,
) -> bool:
    """R33: Trade WITH the day's dominant force; block against it.

    Logic — pick the dominant force, allow only signals aligned with it:

        Case A: gap intact (gap_pct and current_move_pct same sign,
                or current_move very small) → trade WITH the gap.
                Reject signals against the gap direction.

        Case B: gap reversed (current_move_pct opposite the gap and
                exceeds threshold) → market is "reversing the trap".
                Trade WITH the reversal.
                Reject signals in the original gap direction.

        Case C: gap is small (|gap_pct| < threshold) → no constraint.

    Returns True if signal is OK, False if it should be rejected.
    """
    if not cfg.enable_trend_conflict:
        return True
    threshold = cfg.trend_conflict_min_pct
    if abs(gap_pct) < threshold:
        return True  # Gap too small to matter

    gap_up = gap_pct > 0
    reversed_strongly = (
        (gap_up and current_move_pct <= -threshold)
        or (not gap_up and current_move_pct >= threshold)
    )

    if reversed_strongly:
        # Reversal is dominant: trade WITH the reversal
        if gap_up and direction == "BUY":
            return False  # gap was up, now reversed down → don't BUY
        if (not gap_up) and direction == "SELL":
            return False  # gap was down, now reversed up → don't SELL
    else:
        # Gap intact: trade WITH the gap
        if gap_up and direction == "SELL":
            return False
        if (not gap_up) and direction == "BUY":
            return False
    return True


def filter_opening_zone(
    spot: float,
    index: str,
    cfg: BacktestConfig,
) -> bool:
    """R34: Reject if spot is too close to a 500/1000 round number.

    Returns True if OK to enter, False if too close to a level.
    """
    if not cfg.enable_opening_zone:
        return True
    increment = ROUND_NUMBER_INCREMENT[index]
    nearest = round(spot / increment) * increment
    distance_pct = abs(spot - nearest) / spot
    return distance_pct >= cfg.opening_zone_pct


def filter_cooldown(
    state: "DayState",
    now: datetime,
    cfg: BacktestConfig,
) -> bool:
    """Cooldown after the most recent closed trade.

    Returns True if OK to enter, False if still in cooldown.
    """
    if not cfg.enable_cooldown or not state.closed_trades:
        return True
    last = state.closed_trades[-1]
    elapsed_min = (now - last.exit_ts).total_seconds() / 60
    if last.pnl > 0:
        return elapsed_min >= cfg.cooldown_after_win_min
    elif last.pnl < 0:
        return elapsed_min >= cfg.cooldown_after_loss_min
    return True


def filter_one_dir_per_day(
    state: "DayState",
    direction: str,
    cfg: BacktestConfig,
) -> bool:
    """R45: Don't flip BUY <-> SELL within the same day."""
    if not cfg.enable_one_dir_per_day or not state.closed_trades:
        return True
    last_dir = state.closed_trades[0].direction  # first trade locks direction
    return direction == last_dir


def gap_breakout_block(
    direction: str,
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    cfg: BacktestConfig,
) -> bool:
    """Returns False if signal should be BLOCKED.

    Blocks counter-trend signals when today's open is outside yesterday's
    high/low range (= a clear gap breakout).
    """
    if not cfg.enable_gap_breakout_block or not yesterday or not today:
        return True
    today_open = today[0].open
    y_high = max(c.high for c in yesterday)
    y_low = min(c.low for c in yesterday)
    if today_open > y_high and direction == "SELL":
        return False  # bullish gap-breakout → block SELL
    if today_open < y_low and direction == "BUY":
        return False  # bearish gap-breakdown → block BUY
    return True


def constituent_internal_split(
    minute_idx: int,
    hdfc_today: List[Candle],
    kotak_today: List[Candle],
    cfg: BacktestConfig,
) -> bool:
    """Check if HDFC + KOTAK STRONGLY DISAGREE with each other since open.

    Returns True when |HDFC_move - KOTAK_move| >= cfg.constituent_internal_split_pct.
    Used to gate the BN component only — when constituents fight each other
    BN tends to be a fake-out drag.
    """
    if not cfg.enable_constituent_internal_split:
        return False
    if not hdfc_today or not kotak_today:
        return False
    end_idx = min(minute_idx, len(hdfc_today) - 1, len(kotak_today) - 1)
    if end_idx <= 0:
        return False
    h = (hdfc_today[end_idx].close - hdfc_today[0].open) / hdfc_today[0].open * 100
    k = (kotak_today[end_idx].close - kotak_today[0].open) / kotak_today[0].open * 100
    return abs(h - k) >= cfg.constituent_internal_split_pct


def constituent_confluence_check(
    direction: str,
    minute_idx: int,
    hdfc_today: List[Candle],
    kotak_today: List[Candle],
    cfg: BacktestConfig,
) -> bool:
    """R29: HDFC + KOTAK should not be DIVERGENT from a BANKNIFTY signal.

    Logic: measure each constituent's % move FROM TODAY'S OPEN.
    If AT LEAST ONE of HDFC or KOTAK is moving in the signal direction
    (above the divergence threshold in the OPPOSITE sign), block. Otherwise
    allow.

    This is a "veto on strong divergence" rather than a "require strong
    agreement" — it only blocks when constituents are clearly fighting the BN
    direction, not when they're merely flat.

    Returns True if BN entry is OK, False if it should be skipped.
    """
    if not cfg.enable_constituent_confluence:
        return True
    if not hdfc_today or not kotak_today:
        return True  # data missing → don't block

    end_idx = min(minute_idx, len(hdfc_today) - 1, len(kotak_today) - 1)
    if end_idx <= 0:
        return True

    def move_from_open(candles: List[Candle]) -> float:
        a = candles[0].open
        b = candles[end_idx].close
        return (b - a) / a * 100 if a > 0 else 0.0

    h = move_from_open(hdfc_today)
    k = move_from_open(kotak_today)
    threshold = cfg.constituent_min_pct  # divergence threshold

    # Veto only when BOTH constituents diverge from the signal direction.
    # This catches the case where the WHOLE banking sector contradicts a
    # BN signal — typically because the signal was mistimed or wrong.
    if direction == "BUY":
        if h <= -threshold and k <= -threshold:
            return False
    else:  # SELL
        if h >= threshold and k >= threshold:
            return False
    return True


def compute_day_bias_score(
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    minute_idx: int,
    hdfc_today: List[Candle],
    kotak_today: List[Candle],
    cfg: BacktestConfig,
) -> float:
    """Composite day-bias score in [-1, 1]: -1 strong sell, 0 neutral, +1 strong buy.

    Weighted blend of 5 inputs:
        1. Yesterday's day-move      (weight 0.20)
        2. Today's gap from yclose   (weight 0.30)
        3. Intraday move from open   (weight 0.30)
        4. HDFC % move from open     (weight 0.10)
        5. KOTAK % move from open    (weight 0.10)

    Each input is normalised to [-1, 1] by clipping at ±cfg.day_bias_input_threshold_pct.
    """
    if not yesterday or not today:
        return 0.0

    threshold = cfg.day_bias_input_threshold_pct

    def clip(pct: float) -> float:
        return max(-1.0, min(1.0, pct / threshold))

    # 1. Yesterday's day move
    y_open = yesterday[0].open
    y_close = yesterday[-1].close
    y_move = (y_close - y_open) / y_open * 100 if y_open > 0 else 0.0

    # 2. Today's gap
    gap = (today[0].open - y_close) / y_close * 100 if y_close > 0 else 0.0

    # 3. Intraday move from open
    cur_idx = min(minute_idx, len(today) - 1)
    if cur_idx <= 0:
        intraday = 0.0
    else:
        intraday = (today[cur_idx].close - today[0].open) / today[0].open * 100

    # 4 & 5. Constituent moves from open
    h_move = 0.0
    k_move = 0.0
    if hdfc_today:
        h_idx = min(minute_idx, len(hdfc_today) - 1)
        if h_idx > 0:
            h_move = (hdfc_today[h_idx].close - hdfc_today[0].open) / hdfc_today[0].open * 100
    if kotak_today:
        k_idx = min(minute_idx, len(kotak_today) - 1)
        if k_idx > 0:
            k_move = (kotak_today[k_idx].close - kotak_today[0].open) / kotak_today[0].open * 100

    score = (
        0.20 * clip(y_move)
      + 0.30 * clip(gap)
      + 0.30 * clip(intraday)
      + 0.10 * clip(h_move)
      + 0.10 * clip(k_move)
    )
    return max(-1.0, min(1.0, score))


def filter_day_bias(
    direction: str,
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    minute_idx: int,
    hdfc_today: List[Candle],
    kotak_today: List[Candle],
    cfg: BacktestConfig,
) -> bool:
    """Soft veto: only reject when |score| >= block_threshold AND opposite to signal."""
    if not cfg.enable_day_bias:
        return True
    score = compute_day_bias_score(
        today, yesterday, minute_idx, hdfc_today, kotak_today, cfg
    )
    threshold = cfg.day_bias_block_score
    if direction == "BUY" and score <= -threshold:
        return False
    if direction == "SELL" and score >= threshold:
        return False
    return True


# ── E3 detection (trend continuation / slow drift) ──────────────────────

def detect_e3(
    minute_idx: int,
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    cfg: BacktestConfig,
) -> Optional[str]:
    """E3: Slow trend continuation.

    Catches days where:
        - Yesterday was directional in some direction (>= cfg.e3_min_yclose_pct)
        - Today gapped the SAME direction (>= cfg.e3_min_gap_pct)
        - Price has been holding above (BUY) / below (SELL) today's open after
          cfg.e3_wait_minutes
        - Current move from open hasn't run too far (still within
          cfg.e3_max_current_pct of open)
        - Price is still on the gap side of yesterday's close

    This is the "follow the slow grind" signal that fires on days like
    2025-12-22 where no directional run + no gap-trap exists, but the
    multi-day context says clearly bullish/bearish.

    Returns 'BUY' / 'SELL' / None.
    """
    if not cfg.e3_enabled or not yesterday:
        return None
    if minute_idx < cfg.e3_wait_minutes:
        return None

    y_open = yesterday[0].open
    y_close = yesterday[-1].close
    y_move_pct = (y_close - y_open) / y_open * 100
    if abs(y_move_pct) < cfg.e3_min_yclose_pct:
        return None  # yesterday flat → no directional bias

    today_open = today[0].open
    gap_pct = (today_open - y_close) / y_close * 100
    if abs(gap_pct) < cfg.e3_min_gap_pct:
        return None  # gap too small

    # Direction: gap and yesterday must agree
    bullish = y_move_pct > 0 and gap_pct > 0
    bearish = y_move_pct < 0 and gap_pct < 0
    if not (bullish or bearish):
        return None

    cur = today[minute_idx]
    cur_move_pct = (cur.close - today_open) / today_open * 100

    # Don't fire if price already ran too far (we're late)
    if abs(cur_move_pct) > cfg.e3_max_current_pct:
        return None

    if bullish:
        # Need price still above today's open AND above yesterday's close
        if cur.close > today_open and cur.close > y_close:
            return "BUY"
    else:
        if cur.close < today_open and cur.close < y_close:
            return "SELL"
    return None


# ── E2 detection (gap counter-trap) ──────────────────────────────────────

def detect_e2(
    minute_idx: int,
    today: List[Candle],
    yesterday: Optional[List[Candle]],
    cfg: BacktestConfig,
) -> Optional[str]:
    """E2: Gap counter-trap.

    When today gaps significantly AND after wait_minutes the prior swing
    high/low has NOT been broken, fire AGAINST the gap (the gap-traders
    are trapped). Direction = opposite of the gap.

    Logic:
        gap-down + price hasn't broken yesterday's low after 15 min -> BUY
        gap-up   + price hasn't broken yesterday's high after 15 min -> SELL
    """
    if not cfg.e2_enabled or not yesterday:
        return None
    if minute_idx < cfg.e2_wait_minutes:
        return None  # Need wait_minutes of post-open data

    gap_pct = compute_gap_pct(today, yesterday)
    if abs(gap_pct) < cfg.e2_min_gap_pct:
        return None  # Gap too small to trap

    y_high = max(c.high for c in yesterday)
    y_low = min(c.low for c in yesterday)

    # Check the post-open window: did price break the prior swing?
    window = today[: minute_idx + 1]
    window_high = max(c.high for c in window)
    window_low = min(c.low for c in window)

    if gap_pct < 0:
        # Gap-down: did we break yesterday's low? If NO, panic sellers trapped -> BUY
        if window_low > y_low:
            return "BUY"
    else:
        # Gap-up: did we break yesterday's high? If NO, fresh buyers trapped -> SELL
        if window_high < y_high:
            return "SELL"
    return None


# ── The main day loop (cfg-driven) ───────────────────────────────────────

def replay_day(
    day: date,
    nifty: List[Candle],
    bn: List[Candle],
    sx: List[Candle],
    nifty_yesterday: Optional[List[Candle]],
    consecutive_losses_in: int = 0,
    cfg: Optional[BacktestConfig] = None,
    vix_iv: Optional[float] = None,
    hdfc: Optional[List[Candle]] = None,
    kotak: Optional[List[Candle]] = None,
) -> DayState:
    """Replay one trading day minute-by-minute, applying all configured filters."""
    cfg = cfg or CFG
    state = DayState(day=day, consecutive_losses=consecutive_losses_in)

    if not nifty or not bn or not sx:
        return state

    n_minutes = min(len(nifty), len(bn), len(sx))

    # R28: 3-loss circuit breaker
    if cfg.enable_loss_circuit and consecutive_losses_in >= cfg.consecutive_loss_skip:
        return state

    # Pre-compute gap (used by R33 trend conflict filter)
    gap_pct = compute_gap_pct(nifty, nifty_yesterday)

    entry_start = cfg.entry_start
    entry_end = cfg.entry_end
    time_exit = cfg.time_exit

    # Pre-compute the day-bias score ONCE at the first eligible entry minute.
    # This freezes the bias so the filter doesn't flip-flop minute-by-minute.
    frozen_bias_score: Optional[float] = None  # set the first time we hit entry_start

    for minute_idx in range(n_minutes):
        nifty_c = nifty[minute_idx]
        bn_c = bn[minute_idx]
        sx_c = sx[minute_idx]
        now = nifty_c.ts.time()

        if now < SESSION_START or now > SESSION_END:
            continue

        # Freeze the day-bias score at the first eligible entry minute
        if (
            cfg.enable_day_bias
            and frozen_bias_score is None
            and entry_start <= now <= entry_end
        ):
            frozen_bias_score = compute_day_bias_score(
                nifty, nifty_yesterday, minute_idx, hdfc or [], kotak or [], cfg
            )

        # Entry pass — only inside the morning entry window
        if (
            entry_start <= now <= entry_end
            and state.trades_count < cfg.max_trades_per_day
            and not state.loss_limit_hit
            and not state.open_positions
            and filter_cooldown(state, nifty_c.ts, cfg)
        ):
            signals = detect_signals(
                minute_idx, nifty, bn, sx, state, cfg, nifty_yesterday
            )
            # Compute current intraday trend (always active, not gated on 60 min)
            current_move = compute_current_move_pct(nifty, minute_idx)
            for sig in signals:
                # Day-bias filter (frozen score; hard veto on opposite direction)
                if cfg.enable_day_bias and frozen_bias_score is not None:
                    threshold = cfg.day_bias_block_score
                    if sig["direction"] == "BUY" and frozen_bias_score <= -threshold:
                        continue
                    if sig["direction"] == "SELL" and frozen_bias_score >= threshold:
                        continue
                # R33: trend conflict (uses gap + current intraday move)
                if not filter_trend_conflict(sig["direction"], gap_pct, current_move, cfg):
                    continue
                # R45: one direction per day
                if not filter_one_dir_per_day(state, sig["direction"], cfg):
                    continue
                # R34: opening zone (check NIFTY spot only — proxy for all 3)
                if not filter_opening_zone(nifty_c.close, "NIFTY", cfg):
                    continue
                # Gap-breakout block: today open outside yesterday's range
                if not gap_breakout_block(sig["direction"], nifty, nifty_yesterday, cfg):
                    continue
                # R29: HDFC + KOTAK constituent confluence
                constituent_ok = constituent_confluence_check(
                    sig["direction"], minute_idx, hdfc or [], kotak or [], cfg
                )
                if not constituent_ok and cfg.constituent_reject_all:
                    continue  # whole signal rejected
                # New: HDFC + KOTAK internally split → skip BN component only
                if constituent_internal_split(minute_idx, hdfc or [], kotak or [], cfg):
                    constituent_ok = False  # forces open_positions to skip BN
                # All filters passed → take the trade
                positions = open_positions(
                    sig, nifty_c, bn_c, sx_c, day, cfg, vix_iv,
                    constituent_ok=constituent_ok,
                )
                if positions:
                    state.open_positions.extend(positions)
                    state.trades_count += 1
                    break  # only one signal per minute

        # Exit pass
        closed = check_exits(state, nifty_c, bn_c, sx_c, cfg=cfg, vix_iv=vix_iv)
        for trade in closed:
            state.closed_trades.append(trade)
            state.daily_pnl += trade.pnl
            if state.daily_pnl <= -cfg.daily_loss_limit:
                state.loss_limit_hit = True

    # Force-close any positions still open at EOD
    if state.open_positions:
        last_n, last_b, last_s = nifty[-1], bn[-1], sx[-1]
        closed = check_exits(state, last_n, last_b, last_s, force_close=True, cfg=cfg, vix_iv=vix_iv)
        for trade in closed:
            state.closed_trades.append(trade)
            state.daily_pnl += trade.pnl

    return state


# ── Reporting (v0 — minimal) ──────────────────────────────────────────────

def report_summary(all_states: List[DayState], out_csv: Optional[str] = None):
    n_days = len(all_states)
    n_traded = sum(1 for s in all_states if s.trades_count)
    total_trade_groups = sum(s.trades_count for s in all_states)  # 1 group = 3 indices
    total_individual = sum(len(s.closed_trades) for s in all_states)
    total_pnl = sum(s.daily_pnl for s in all_states)

    wins = losses = breakeven = 0
    gross_win = gross_loss = 0.0
    for s in all_states:
        for t in s.closed_trades:
            if t.pnl > 0:
                wins += 1
                gross_win += t.pnl
            elif t.pnl < 0:
                losses += 1
                gross_loss += abs(t.pnl)
            else:
                breakeven += 1

    pf = gross_win / gross_loss if gross_loss else float("inf")

    # Daily winners/losers
    winning_days = sum(1 for s in all_states if s.daily_pnl > 0)
    losing_days = sum(1 for s in all_states if s.daily_pnl < 0)

    # Max drawdown (per-day cumulative)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for s in all_states:
        cum += s.daily_pnl
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Exit reason breakdown
    exit_reasons: Dict[str, int] = defaultdict(int)
    for s in all_states:
        for t in s.closed_trades:
            exit_reasons[t.exit_reason] += 1

    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Days replayed:           {n_days}")
    print(f"  Days with trades:        {n_traded}")
    print(f"  Trade groups (signals):  {total_trade_groups}")
    print(f"  Individual positions:    {total_individual}  (3 per signal: BN+SX+NF)")
    print(f"  Total P&L (Rs):          {total_pnl:>12,.0f}")
    print(f"  Winning days:            {winning_days}")
    print(f"  Losing days:             {losing_days}")
    if total_individual:
        wr = wins / total_individual * 100
        print(f"  Position win rate:       {wr:.1f}% ({wins}W / {losses}L / {breakeven}BE)")
        print(f"  Profit factor:           {pf:.2f}")
        print(f"  Avg win:                 Rs {gross_win/wins if wins else 0:,.0f}")
        print(f"  Avg loss:                Rs {gross_loss/losses if losses else 0:,.0f}")
        print(f"  Max drawdown:            Rs {max_dd:,.0f}")
    print(f"  Exit reasons:            {dict(exit_reasons)}")

    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "day", "index", "direction", "strike", "entry_ts", "exit_ts",
                "entry_premium", "exit_premium", "qty", "pnl", "exit_reason", "notes",
            ])
            for s in all_states:
                for t in s.closed_trades:
                    writer.writerow([
                        t.day, t.index, t.direction, t.strike,
                        t.entry_ts, t.exit_ts,
                        f"{t.entry_premium:.2f}", f"{t.exit_premium:.2f}",
                        t.qty, f"{t.pnl:.0f}", t.exit_reason, t.notes,
                    ])
        print(f"\n  Per-trade CSV: {out_csv}")


# ── Main ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default=DEFAULT_START, help="YYYY-MM-DD")
    p.add_argument("--end", default=DEFAULT_END, help="YYYY-MM-DD")
    p.add_argument("--days", type=int, default=0, help="Limit to first N days (smoke test).")
    p.add_argument("--csv", default=None, help="Output CSV path for per-trade detail.")
    p.add_argument("--quiet", action="store_true", help="Suppress per-day output.")
    p.add_argument(
        "--mode",
        choices=["run", "validate", "sweep", "walkforward"],
        default="run",
        help="run = single backtest, validate = run + score against video days, "
             "sweep = grid search, walkforward = train/test split",
    )
    return p.parse_args()


# ── VIX loader (for dynamic IV) ──────────────────────────────────────────

def load_vix_per_day(conn: sqlite3.Connection) -> Dict[date, float]:
    """Load average VIX per day from vix_history (3-min granularity).

    Returns a dict {date: average_vix_as_iv_decimal}. e.g. VIX 14.0 -> 0.14.
    """
    rows = conn.execute(
        "SELECT substr(timestamp, 1, 10) as d, AVG(close) FROM vix_history GROUP BY d"
    ).fetchall()
    return {datetime.strptime(d, "%Y-%m-%d").date(): float(v) / 100.0 for d, v in rows}


# ── Run a full backtest (returns list of DayState) ───────────────────────

def run_backtest(
    indices: Dict[str, Dict[date, List[Candle]]],
    common_days: List[date],
    cfg: BacktestConfig,
    vix_by_day: Optional[Dict[date, float]] = None,
    verbose: bool = False,
) -> List[DayState]:
    all_states: List[DayState] = []
    consecutive_losses = 0
    hdfc_data = indices.get("HDFCBANK", {})
    kotak_data = indices.get("KOTAKBANK", {})
    for i, day in enumerate(common_days):
        nifty_today = indices["NIFTY"].get(day, [])
        bn_today = indices["BANKNIFTY"].get(day, [])
        sx_today = indices["SENSEX"].get(day, [])
        hdfc_today = hdfc_data.get(day, [])
        kotak_today = kotak_data.get(day, [])

        prior_day = common_days[i - 1] if i > 0 else None
        nifty_yesterday = indices["NIFTY"].get(prior_day) if prior_day else None

        vix_iv = vix_by_day.get(day) if vix_by_day else None

        state = replay_day(
            day, nifty_today, bn_today, sx_today, nifty_yesterday,
            consecutive_losses_in=consecutive_losses, cfg=cfg, vix_iv=vix_iv,
            hdfc=hdfc_today, kotak=kotak_today,
        )
        all_states.append(state)

        if state.daily_pnl < 0:
            consecutive_losses += 1
        elif state.daily_pnl > 0:
            consecutive_losses = 0
        else:
            consecutive_losses = 0  # rest day = reset

        if verbose and (state.trades_count or False):
            regime = classify_regime(nifty_today, nifty_yesterday)
            print(
                f"  {day} | {regime:<35} | "
                f"trades={state.trades_count} | pnl={state.daily_pnl:>+10,.0f}"
            )
    return all_states


# ── Validation against documented R3+R4 days ─────────────────────────────

def validate_against_documented(
    states: List[DayState],
    verbose: bool = False,
    day_set: Optional[List[Tuple[str, str]]] = None,
    label: str = "ALL",
) -> dict:
    """Score the backtest output against the 23 documented video days.

    "Aligned" means the backtest agrees with the trader's video outcome:
        WIN day  + backtest WIN          → ALIGNED  (+1)
        WIN day  + backtest LOSS         → MISS     (-1, false positive on direction)
        WIN day  + no signal             → MISS     (-0.5, missed opportunity)
        LOSS day + backtest LOSS         → ALIGNED  (+1, correctly hit a loss)
        LOSS day + backtest WIN          → DIVERGE  (0, backtest "did better" than human)
        LOSS day + no signal             → ALIGNED  (+1, correctly skipped a loss day)

    The smart-skip rule (loss day + no signal = aligned) reflects the trader's
    own R28 + R45 wisdom: "if you can't read the day, sit it out."
    """
    docs = day_set if day_set is not None else DOCUMENTED_DAYS
    per_day = {s.day.isoformat(): s for s in states}
    total = len(docs)
    aligned = 0
    matched = 0
    total_pnl = 0.0
    diffs = []
    for d, expected in docs:
        s = per_day.get(d)
        has_trade = s is not None and s.trades_count > 0
        actual_w = bool(s and s.daily_pnl > 0)
        expected_w = expected == "W"

        if not has_trade:
            # No signal
            if expected_w:
                status = "MISS_W"  # missed an opportunity
                score_pts = -0.5
            else:
                status = "OK_SKIP"  # smart skip of a loss day
                aligned += 1
                score_pts = 1.0
            diffs.append((d, expected, "(no sig)", 0.0, status))
            continue

        matched += 1
        total_pnl += s.daily_pnl
        if actual_w == expected_w:
            aligned += 1
            status = "OK"
        elif not expected_w and actual_w:
            status = "DIVERGE"  # backtest beat human (won on a loss day)
        else:
            status = "MISS"  # wrong direction

        diffs.append((
            d, expected,
            "WIN" if actual_w else "LOSS",
            s.daily_pnl,
            status,
        ))

    if verbose:
        print(f"\n  {'Date':<12} {'Expected':<10} {'Backtest':<10} {'P&L':>13}  Status")
        print("  " + "-" * 65)
        for d, exp, res, pnl, status in diffs:
            if pnl == 0.0:
                print(f"  {d:<12} {exp:<10} {res:<10} {'':>13}  {status}")
            else:
                print(f"  {d:<12} {exp:<10} {res:<10} Rs{pnl:>+11,.0f}  {status}")

    return {
        "documented": total,
        "matched": matched,
        "aligned": aligned,
        "no_signal": sum(1 for d in diffs if d[2] == "(no sig)"),
        "match_pct": (aligned / total * 100),
        "coverage_pct": (matched / total * 100),
        "pnl_on_docs": total_pnl,
        "diffs": diffs,
    }


# ── Parameter sweep ──────────────────────────────────────────────────────

def compute_summary(states: List[DayState]) -> dict:
    """Pull WR, PF, MaxDD, total P&L, and the user's "keep" trio."""
    total_pnl = sum(s.daily_pnl for s in states)
    wins = losses = 0
    gw = gl = 0.0
    for s in states:
        for t in s.closed_trades:
            if t.pnl > 0:
                wins += 1
                gw += t.pnl
            elif t.pnl < 0:
                losses += 1
                gl += abs(t.pnl)
    n = wins + losses
    wr = wins / n * 100 if n else 0
    pf = gw / gl if gl else float("inf")
    cum = peak = mdd = 0.0
    for s in states:
        cum += s.daily_pnl
        if cum > peak:
            peak = cum
        if peak - cum > mdd:
            mdd = peak - cum
    return {
        "total_pnl": total_pnl, "wr": wr, "pf": pf, "max_dd": mdd,
        "n_positions": n, "wins": wins, "losses": losses,
    }


def parameter_sweep(
    indices: Dict[str, Dict[date, List[Candle]]],
    common_days: List[date],
    vix_by_day: Dict[date, float],
    grid: Dict[str, list],
    base_cfg: Optional[BacktestConfig] = None,
    score_day_set: Optional[List[Tuple[str, str]]] = None,
) -> List[Tuple[BacktestConfig, dict, dict]]:
    """Grid search over `grid`. Returns sorted (cfg, validation, summary) list.

    Score = correct_matches - 0.3 * no_signal_count + (0.0001 * total_pnl)
            (rewards correct direction, lightly penalises misses, and uses
             P&L as a tie-breaker)

    `score_day_set` lets you score against TRAIN_DAYS only (walk-forward).
    """
    base_cfg = base_cfg or BacktestConfig()
    keys = list(grid.keys())
    combinations = list(itertools.product(*[grid[k] for k in keys]))
    print(f"\nSweeping {len(combinations)} combinations across {len(keys)} parameters...")
    print(f"Parameters: {keys}")
    results = []
    for i, combo in enumerate(combinations, 1):
        overrides = dict(zip(keys, combo))
        cfg = replace(base_cfg, **overrides)
        states = run_backtest(indices, common_days, cfg, vix_by_day, verbose=False)
        v = validate_against_documented(states, verbose=False, day_set=score_day_set)
        # Compute backtest summary inline
        total_pnl = sum(s.daily_pnl for s in states)
        wins = sum(1 for s in states for t in s.closed_trades if t.pnl > 0)
        losses = sum(1 for s in states for t in s.closed_trades if t.pnl < 0)
        n_pos = wins + losses
        wr = wins / n_pos * 100 if n_pos else 0
        gw = sum(t.pnl for s in states for t in s.closed_trades if t.pnl > 0)
        gl = sum(abs(t.pnl) for s in states for t in s.closed_trades if t.pnl < 0)
        pf = gw / gl if gl else float("inf")
        # Score: heavy weight on alignment, light bonus from total P&L (tie-breaker)
        # Max DD
        cum = peak = mdd = 0.0
        for s in states:
            cum += s.daily_pnl
            if cum > peak:
                peak = cum
            if peak - cum > mdd:
                mdd = peak - cum
        score = v["aligned"] * 2 + (total_pnl / 1_000_000)
        summary = {
            "total_pnl": total_pnl, "wr": wr, "pf": pf, "max_dd": mdd,
            "n_positions": n_pos, "score": score,
        }
        results.append((cfg, v, summary))
        if i % 5 == 0 or i == len(combinations):
            print(
                f"  [{i:>3}/{len(combinations)}] best so far: "
                f"score={max(r[2]['score'] for r in results):.2f}"
            )

    results.sort(key=lambda r: r[2]["score"], reverse=True)
    return results


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    print("=" * 70)
    print(f"  IntradayHunter Backtester (v2)  --  mode={args.mode}")
    print("=" * 70)
    print(f"  Period:    {args.start} -> {args.end}")
    print(f"  DB:        {DB_PATH}")
    print()

    print("Loading instrument history...")
    conn = sqlite3.connect(DB_PATH)
    indices = load_all_indices(conn, start_dt, end_dt)

    print("Loading VIX history...")
    vix_by_day = load_vix_per_day(conn)
    print(f"  VIX days: {len(vix_by_day)}")

    common_days = sorted(
        set(indices["NIFTY"].keys())
        & set(indices["BANKNIFTY"].keys())
        & set(indices["SENSEX"].keys())
    )
    print(f"\n  Trading days with all 3 indices: {len(common_days)}")
    if not common_days:
        print("  No common days. Did you run fetch_instrument_history.py?")
        conn.close()
        return

    if args.days > 0:
        common_days = common_days[: args.days]
        print(f"  (Smoke test: replaying first {len(common_days)} day(s))")

    if args.mode == "walkforward":
        # Walk-forward validation:
        #   1. Sweep on TRAIN_DAYS (Dec 2024 → Nov 2025)
        #   2. Take best config
        #   3. Validate on TEST_DAYS (Dec 2025 → Apr 2026)
        print("\n" + "=" * 70)
        print(f"  WALK-FORWARD VALIDATION (cutoff: {WF_TRAIN_CUTOFF})")
        print(f"  Train days: {len(TRAIN_DAYS)}  |  Test days: {len(TEST_DAYS)}")
        print("=" * 70)

        grid = {
            "day_bias_block_score":    [0.40, 0.50, 0.60],
            "constituent_min_pct":     [0.15, 0.20],
            "cooldown_after_win_min":  [60, 90, 120],
            "tgt_pct":                 [0.35, 0.45, 0.55],
        }
        # 3*2*3*3 = 54 combinations × ~5 sec each ≈ 4-5 min
        results = parameter_sweep(
            indices, common_days, vix_by_day, grid,
            score_day_set=TRAIN_DAYS,
        )
        print("\n" + "=" * 100)
        print("  TOP 5 CONFIGS BY TRAIN SCORE")
        print("=" * 100)
        for i, (cfg, v, s) in enumerate(results[:5], 1):
            print(
                f"  {i}: train={v['aligned']}/{len(TRAIN_DAYS)} ({v['match_pct']:.0f}%) "
                f"PF={s['pf']:.2f} WR={s['wr']:.1f}% MDD={s['max_dd']:,.0f} "
                f"PnL={s['total_pnl']:,.0f}"
            )
            print(f"      cfg: bias={cfg.day_bias_block_score} cstn={cfg.constituent_min_pct} "
                  f"cdwin={cfg.cooldown_after_win_min} tgt={cfg.tgt_pct}")

        # Validate top 3 configs on TEST set
        print("\n" + "=" * 100)
        print("  TOP 3 CONFIGS — TEST PERFORMANCE")
        print("=" * 100)
        for i, (cfg, train_v, train_s) in enumerate(results[:3], 1):
            test_states = run_backtest(indices, common_days, cfg, vix_by_day, verbose=False)
            test_v = validate_against_documented(test_states, day_set=TEST_DAYS)
            test_s = compute_summary(test_states)
            print(f"\n  Config #{i}:")
            print(f"    TRAIN: {train_v['aligned']}/{len(TRAIN_DAYS)} aligned, "
                  f"PF={train_s['pf']:.2f} WR={train_s['wr']:.1f}% MDD={train_s['max_dd']:,.0f}")
            print(f"    TEST:  {test_v['aligned']}/{len(TEST_DAYS)} aligned, "
                  f"PF={test_s['pf']:.2f} WR={test_s['wr']:.1f}% MDD={test_s['max_dd']:,.0f} "
                  f"PnL={test_s['total_pnl']:,.0f}")
            print(f"    Full backtest PnL: Rs {test_s['total_pnl']:,.0f}")

        # Best config detail validation
        best_cfg = results[0][0]
        print("\n" + "=" * 70)
        print("  BEST WALK-FORWARD CONFIG — full validation")
        print("=" * 70)
        full_states = run_backtest(indices, common_days, best_cfg, vix_by_day, verbose=False)
        validate_against_documented(full_states, verbose=True)
        print(compute_summary(full_states))

    elif args.mode == "sweep":
        # Focused sweep grid (96 combinations × ~5s each ≈ 8 min)
        grid = {
            "enable_trend_conflict":   [True, False],
            "enable_one_dir_per_day":  [True, False],
            "enable_opening_zone":     [True, False],
            "enable_cooldown":         [True, False],
            "multi_index_min":         [2, 3],
            "tgt_pct":                 [0.25, 0.35, 0.45],
        }
        results = parameter_sweep(indices, common_days, vix_by_day, grid)
        print("\n" + "=" * 100)
        print("  TOP 10 CONFIGURATIONS BY SCORE")
        print("=" * 100)
        print(f"  {'#':>3} {'Score':>8} {'Match%':>7} {'Cov%':>6} {'PnL(L)':>10} {'PF':>5} {'WR%':>5}  Filters")
        for i, (cfg, v, s) in enumerate(results[:10], 1):
            f_str = (
                f"trend={'Y' if cfg.enable_trend_conflict else 'N'} "
                f"1dir={'Y' if cfg.enable_one_dir_per_day else 'N'} "
                f"open={'Y' if cfg.enable_opening_zone else 'N'} "
                f"cd={cfg.cooldown_after_win_min} "
                f"sl={cfg.sl_pct} tgt={cfg.tgt_pct} mi={cfg.multi_index_min}"
            )
            print(
                f"  {i:>3} {s['score']:>8.2f} {v['match_pct']:>6.1f}% "
                f"{v['coverage_pct']:>5.1f}% Rs{s['total_pnl']/100000:>+8.1f}L "
                f"{s['pf']:>5.2f} {s['wr']:>5.1f}%  {f_str}"
            )
        # Validate the best config in detail
        best_cfg, best_v, best_s = results[0]
        print("\n" + "=" * 70)
        print("  BEST CONFIG -- per-day validation against documented videos")
        print("=" * 70)
        validate_against_documented(
            run_backtest(indices, common_days, best_cfg, vix_by_day),
            verbose=True,
        )

    else:
        # Single-run mode (default or --mode validate)
        cfg = CFG  # use defaults
        print()
        print("Replaying days...")
        states = run_backtest(indices, common_days, cfg, vix_by_day, verbose=not args.quiet)
        report_summary(states, out_csv=args.csv)
        if args.mode == "validate":
            print("\n" + "=" * 70)
            print("  VALIDATION vs documented R3+R4 video outcomes")
            print("=" * 70)
            v = validate_against_documented(states, verbose=True)
            print()
            print(f"  Coverage (had a trade):  {v['matched']}/{v['documented']} ({v['coverage_pct']:.1f}%)")
            print(f"  Aligned with video:      {v['aligned']}/{v['documented']} ({v['match_pct']:.1f}%)")
            print(f"  P&L on documented days:  Rs {v['pnl_on_docs']:+,.0f}")

    conn.close()


if __name__ == "__main__":
    main()
