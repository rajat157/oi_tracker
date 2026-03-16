"""
Backtest: Rally Catcher V2 — Optimized
=======================================
Improvements over V1:
  - Signal B (Reversal) DROPPED entirely — 30.9% WR was a drag
  - Signal A (Strong Momentum) — Stricter: acceleration + premium confirmation + OI
  - Signal C (Compression Breakout) — Tuned: wider consolidation window, VIX bias
  - Signal D (OI Trap Breakout) — NEW: detect trapped OI then trade the squeeze
  - Exit logic overhauled: wider target, tighter SL, 2-stage trailing, momentum exit
  - Fewer trades: max 5/day, 9-min cooldown, Rs 80 minimum premium
  - Parameter sweep: target x SL x time-exit grid search

Data: oi_tracker.db, 3-min candles, 32 trading days (2026-01-30 to 2026-03-16)

Usage:
    cd D:/Projects/oi_tracker && uv run python scripts/backtest_rally_v2.py
"""

import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from itertools import product
from pathlib import Path
from typing import Optional, List, Dict, Tuple

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = Path(__file__).resolve().parent.parent / "oi_tracker.db"

# ── Constants ────────────────────────────────────────────────────────
NIFTY_GAP = 50
ITM_DEPTH = 2           # 2 strikes ITM (100 pts)
LOT_SIZE = 65
BROKERAGE_TOTAL = 72    # Rs per round-trip

# Entry timing
ENTRY_START = time(9, 30)
ENTRY_END = time(14, 30)
FORCE_EXIT_TIME = time(15, 15)

# Position management
MAX_TRADES_PER_DAY = 5
COOLDOWN_MINUTES = 9
MAX_OPEN_POSITIONS = 1

# Exit parameters (defaults — also swept)
TARGET_PCT = 0.18       # +18% premium
STOP_LOSS_PCT = -0.07   # -7% premium
TIME_EXIT_MINUTES = 24  # exit flat trades after 24 min
TIME_EXIT_DEAD_ZONE = (-0.03, 0.03)  # only time-exit if P&L between -3% and +3%

# Trailing stop — 2 stages
TRAIL_1_TRIGGER = 0.10  # after +10%, move SL to +4%
TRAIL_1_LOCK = 0.04
TRAIL_2_TRIGGER = 0.14  # after +14%, move SL to +9%
TRAIL_2_LOCK = 0.09

# Momentum exit: if premium drops 5% from peak in any 6-min window
MOMENTUM_DROP_PCT = 0.05
MOMENTUM_WINDOW_CANDLES = 2  # 6 min = 2 candles

# Signal A — Strong Momentum (stricter)
SIG_A_MOVE_2C = 25       # pts in 2 candles (6 min) — was 20
SIG_A_MOVE_3C = 35       # pts in 3 candles (9 min) — was 25
SIG_A_PREMIUM_MOVE = 0.05  # +5% premium move in 2 candles
SIG_A_MIN_OI_CHANGE = 0    # OI on winning side must be positive

# Signal C — Compression Breakout (tuned)
SIG_C_RANGE_PTS = 25         # max range (was 20)
SIG_C_CONSOLIDATION_CANDLES = 6   # 18 min (was 5 / 15 min)
SIG_C_BREAKOUT_PTS = 12      # breakout beyond range (was 10)
SIG_C_VIX_THRESHOLD = 0.03   # VIX change threshold for bias

# Signal D — OI Trap Breakout (NEW)
SIG_D_OI_THRESHOLD = 50_000  # min OI change on trapped side in 5 candles (15 min)
SIG_D_OI_LOOKBACK = 5        # candles (15 min)
SIG_D_SPOT_MOVE = 15         # pts against trapped side in 2 candles (6 min)

# Premium filter
MIN_PREMIUM = 80
MAX_PREMIUM = 500


# ── Data Classes ─────────────────────────────────────────────────────
@dataclass
class Candle:
    timestamp: datetime
    spot_price: float
    vix: float
    futures_basis: float
    atm_strike: int
    strikes: Dict[int, dict] = field(default_factory=dict)


@dataclass
class Trade:
    entry_time: datetime
    exit_time: Optional[datetime]
    signal_type: str          # 'A', 'C', 'D'
    direction: str            # 'CE' or 'PE'
    strike: int
    entry_premium: float
    exit_premium: float
    entry_spot: float
    exit_spot: float
    pnl_pts: float
    pnl_rs: float
    exit_reason: str
    date: str


@dataclass
class OpenPosition:
    entry_time: datetime
    signal_type: str
    direction: str
    strike: int
    entry_premium: float
    entry_spot: float
    highest_premium: float
    trail_stage: int = 0       # 0=none, 1=trail1, 2=trail2
    premium_history: list = field(default_factory=list)  # last N premiums for momentum exit


# ── Database Loading ─────────────────────────────────────────────────
def load_all_data() -> Dict[str, List[Candle]]:
    """Load all data from DB, grouped by trading day."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    print("Loading analysis_history...")
    cur = conn.execute("""
        SELECT timestamp, spot_price, vix, futures_basis, atm_strike
        FROM analysis_history
        ORDER BY timestamp
    """)
    analysis_rows = cur.fetchall()
    print(f"  {len(analysis_rows)} analysis records loaded")

    analysis_map = {}
    for row in analysis_rows:
        ts = row['timestamp']
        analysis_map[ts] = {
            'spot_price': row['spot_price'],
            'vix': row['vix'],
            'futures_basis': row['futures_basis'],
            'atm_strike': row['atm_strike'],
        }

    print("Loading oi_snapshots...")
    cur = conn.execute("""
        SELECT timestamp, strike_price, ce_ltp, pe_ltp, ce_oi, pe_oi,
               ce_oi_change, pe_oi_change, ce_volume, pe_volume, spot_price
        FROM oi_snapshots
        ORDER BY timestamp, strike_price
    """)
    snapshot_rows = cur.fetchall()
    print(f"  {len(snapshot_rows)} snapshot records loaded")
    conn.close()

    snapshots_by_ts: Dict[str, list] = defaultdict(list)
    for row in snapshot_rows:
        snapshots_by_ts[row['timestamp']].append(row)

    days: Dict[str, List[Candle]] = defaultdict(list)
    timestamps = sorted(analysis_map.keys())

    for ts in timestamps:
        dt = datetime.fromisoformat(ts)
        date_str = dt.strftime('%Y-%m-%d')
        a = analysis_map[ts]

        candle = Candle(
            timestamp=dt,
            spot_price=a['spot_price'],
            vix=a['vix'],
            futures_basis=a['futures_basis'],
            atm_strike=a['atm_strike'],
        )

        if ts in snapshots_by_ts:
            for srow in snapshots_by_ts[ts]:
                candle.strikes[srow['strike_price']] = {
                    'ce_oi': srow['ce_oi'],
                    'pe_oi': srow['pe_oi'],
                    'ce_oi_change': srow['ce_oi_change'],
                    'pe_oi_change': srow['pe_oi_change'],
                    'ce_ltp': srow['ce_ltp'],
                    'pe_ltp': srow['pe_ltp'],
                    'ce_volume': srow['ce_volume'],
                    'pe_volume': srow['pe_volume'],
                }

        days[date_str].append(candle)

    for day_candles in days.values():
        day_candles.sort(key=lambda c: c.timestamp)

    print(f"  {len(days)} trading days loaded")
    return days


# ── Strike & Premium Helpers ─────────────────────────────────────────
def get_atm_strike(spot_price: float) -> int:
    return int(round(spot_price / NIFTY_GAP) * NIFTY_GAP)


def get_trade_strike(spot_price: float, direction: str) -> int:
    atm = get_atm_strike(spot_price)
    if direction == 'CE':
        return atm - ITM_DEPTH * NIFTY_GAP
    else:
        return atm + ITM_DEPTH * NIFTY_GAP


def get_premium(candle: Candle, strike: int, direction: str) -> Optional[float]:
    if strike not in candle.strikes:
        return None
    data = candle.strikes[strike]
    ltp = data['ce_ltp'] if direction == 'CE' else data['pe_ltp']
    if ltp is None or ltp <= 0:
        return None
    return ltp


def get_oi_buildup(candle: Candle, direction: str, around_atm: int, depth: int = 3) -> int:
    """Get OI change buildup around ATM for a direction."""
    total = 0
    for offset in range(-depth, depth + 1):
        strike = around_atm + offset * NIFTY_GAP
        if strike in candle.strikes:
            key = 'ce_oi_change' if direction == 'CE' else 'pe_oi_change'
            total += candle.strikes[strike].get(key, 0) or 0
    return total


def get_total_oi_change(candle: Candle, direction: str) -> int:
    """Get total OI change across ALL strikes for CE or PE."""
    total = 0
    for strike_data in candle.strikes.values():
        key = 'ce_oi_change' if direction == 'CE' else 'pe_oi_change'
        total += strike_data.get(key, 0) or 0
    return total


def get_net_oi_change_near_atm(candle: Candle, direction: str, around_atm: int, depth: int = 5) -> int:
    """Get net OI change near ATM (wider range for Signal D)."""
    total = 0
    for offset in range(-depth, depth + 1):
        strike = around_atm + offset * NIFTY_GAP
        if strike in candle.strikes:
            key = 'ce_oi_change' if direction == 'CE' else 'pe_oi_change'
            total += candle.strikes[strike].get(key, 0) or 0
    return total


# ── Signal Detection ─────────────────────────────────────────────────
def detect_signal_a(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal A — Strong Momentum (stricter than V1):
    - Spot moved 25+ pts in last 6 min AND 35+ pts in last 9 min
    - Acceleration: last-3-min move > previous-3-min move
    - Premium moved +5% in last 6 min
    - OI on winning side is positive (fresh positions)
    """
    if idx < 3:
        return None

    current = candles[idx]
    prev1 = candles[idx - 1]
    prev2 = candles[idx - 2]
    prev3 = candles[idx - 3]

    # 2-candle move (6 min)
    move_2c = current.spot_price - prev2.spot_price
    if abs(move_2c) < SIG_A_MOVE_2C:
        return None

    # 3-candle move (9 min)
    move_3c = current.spot_price - prev3.spot_price
    if abs(move_3c) < SIG_A_MOVE_3C:
        return None

    # Direction must be consistent
    if (move_2c > 0) != (move_3c > 0):
        return None

    direction = 'CE' if move_2c > 0 else 'PE'

    # Acceleration: last candle move > previous candle move (in the direction)
    last_1c_move = current.spot_price - prev1.spot_price
    prev_1c_move = prev1.spot_price - prev2.spot_price
    if direction == 'CE':
        if last_1c_move <= prev_1c_move:
            return None  # Not accelerating
    else:
        if last_1c_move >= prev_1c_move:
            return None  # Not accelerating (for downside, last should be more negative)

    # Premium confirmation: option premium moved +5% in last 2 candles
    strike = get_trade_strike(current.spot_price, direction)
    curr_prem = get_premium(current, strike, direction)
    if curr_prem is None:
        return None
    # Try the same strike on prev2 (even though ATM might have shifted)
    prev_prem = get_premium(prev2, strike, direction)
    if prev_prem is None or prev_prem <= 0:
        # Try the strike from prev2's ATM perspective
        alt_strike = get_trade_strike(prev2.spot_price, direction)
        prev_prem = get_premium(prev2, alt_strike, direction)
        if prev_prem is None or prev_prem <= 0:
            return None

    prem_move = (curr_prem - prev_prem) / prev_prem
    if prem_move < SIG_A_PREMIUM_MOVE:
        return None

    # OI confirmation: OI change on winning side is positive
    atm = get_atm_strike(current.spot_price)
    if direction == 'CE':
        # Bullish: CE OI change (call buyers / writers adding) should be positive
        oi_change = get_oi_buildup(current, 'CE', atm)
    else:
        # Bearish: PE OI change should be positive
        oi_change = get_oi_buildup(current, 'PE', atm)

    if oi_change <= SIG_A_MIN_OI_CHANGE:
        return None

    return direction


def detect_signal_c(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal C — Compression Breakout (tuned):
    - Range in last 18 min (6 candles) is <= 25 pts
    - Breakout: spot moves 12+ pts beyond range
    - OI change in breakout direction is above average
    - VIX bias: if VIX rising during compression, favor UP; if falling, favor DOWN
    """
    if idx < SIG_C_CONSOLIDATION_CANDLES + 1:
        return None

    current = candles[idx]

    # Consolidation range: candles [idx-7] to [idx-1] (6 candles)
    lookback_start = idx - SIG_C_CONSOLIDATION_CANDLES - 1
    lookback_end = idx - 1

    prices = [candles[j].spot_price for j in range(lookback_start, lookback_end + 1)]
    range_high = max(prices)
    range_low = min(prices)
    range_width = range_high - range_low

    if range_width > SIG_C_RANGE_PTS:
        return None  # Not in compression

    # Breakout detection
    if current.spot_price > range_high + SIG_C_BREAKOUT_PTS:
        direction = 'CE'
    elif current.spot_price < range_low - SIG_C_BREAKOUT_PTS:
        direction = 'PE'
    else:
        return None

    # OI confirmation: total OI change in breakout direction above average
    atm = get_atm_strike(current.spot_price)
    if direction == 'CE':
        # Upside breakout: PE writers trapped → PE OI buildup is confirmation
        oi_change = get_oi_buildup(current, 'PE', atm)
        if oi_change <= 0:
            return None
    else:
        # Downside breakout: CE writers trapped → CE OI buildup
        oi_change = get_oi_buildup(current, 'CE', atm)
        if oi_change <= 0:
            return None

    # Compare against average OI change over lookback
    avg_oi = 0
    count = 0
    for j in range(lookback_start, lookback_end + 1):
        c = candles[j]
        c_atm = get_atm_strike(c.spot_price)
        if direction == 'CE':
            oi_val = get_oi_buildup(c, 'PE', c_atm)
        else:
            oi_val = get_oi_buildup(c, 'CE', c_atm)
        avg_oi += oi_val
        count += 1
    avg_oi = avg_oi / count if count > 0 else 0

    if oi_change < avg_oi:
        return None  # OI change not above average

    # VIX bias: check VIX trend during compression
    vix_start = candles[lookback_start].vix
    vix_end = candles[lookback_end].vix
    if vix_start > 0 and vix_end > 0:
        vix_change = vix_end - vix_start
        if vix_change > SIG_C_VIX_THRESHOLD:
            # VIX rising → favor UP breakouts, penalize DOWN
            if direction == 'PE':
                return None  # VIX rising = fear → downside breakout less reliable
        elif vix_change < -SIG_C_VIX_THRESHOLD:
            # VIX falling → favor DOWN breakouts, penalize UP
            if direction == 'CE':
                return None  # VIX falling = calm → upside breakout less reliable

    return direction


def detect_signal_d(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal D — OI Trap Breakout (NEW):
    - Significant OI built on one side in last 15 min (5 candles)
    - Then spot moves sharply AGAINST those positions
    - e.g., large PE OI addition → spot goes UP (PE writers trapped / PE buyers squeezed)
    """
    if idx < SIG_D_OI_LOOKBACK + 2:
        return None

    current = candles[idx]
    atm = get_atm_strike(current.spot_price)

    # Check OI buildup in the lookback window
    # We look at the OI change values on recent candles
    # Sum OI changes over the lookback window
    pe_oi_total = 0
    ce_oi_total = 0
    for j in range(idx - SIG_D_OI_LOOKBACK, idx):
        c = candles[j]
        c_atm = get_atm_strike(c.spot_price)
        pe_oi_total += get_net_oi_change_near_atm(c, 'PE', c_atm)
        ce_oi_total += get_net_oi_change_near_atm(c, 'CE', c_atm)

    # Spot move in last 2 candles
    prev2 = candles[idx - 2]
    spot_move = current.spot_price - prev2.spot_price

    # Case 1: Large PE OI buildup → spot moves UP (PE trapped)
    if pe_oi_total >= SIG_D_OI_THRESHOLD and spot_move >= SIG_D_SPOT_MOVE:
        direction = 'CE'
        return direction

    # Case 2: Large CE OI buildup → spot moves DOWN (CE trapped)
    if ce_oi_total >= SIG_D_OI_THRESHOLD and spot_move <= -SIG_D_SPOT_MOVE:
        direction = 'PE'
        return direction

    return None


# ── Position Management ──────────────────────────────────────────────
def check_exit(position: OpenPosition, candle: Candle,
               target_pct: float = TARGET_PCT,
               sl_pct: float = STOP_LOSS_PCT,
               time_exit_min: int = TIME_EXIT_MINUTES) -> Optional[Tuple[float, str]]:
    """
    Check if position should be exited.
    Returns (exit_premium, reason) or None.
    """
    premium = get_premium(candle, position.strike, position.direction)
    if premium is None:
        return None

    pct_change = (premium - position.entry_premium) / position.entry_premium

    # Update highest premium
    if premium > position.highest_premium:
        position.highest_premium = premium

    # Track premium history for momentum exit
    position.premium_history.append(premium)

    # Check target
    if pct_change >= target_pct:
        return (premium, 'TARGET')

    # Check stop loss
    if pct_change <= sl_pct:
        return (premium, 'SL')

    # Check trailing stop — stage 2 first (tighter)
    if position.trail_stage < 2 and pct_change >= TRAIL_2_TRIGGER:
        position.trail_stage = 2
    elif position.trail_stage < 1 and pct_change >= TRAIL_1_TRIGGER:
        position.trail_stage = 1

    if position.trail_stage == 2:
        trail_level = position.entry_premium * (1 + TRAIL_2_LOCK)
        if premium <= trail_level:
            return (premium, 'TRAIL_SL2')
    elif position.trail_stage == 1:
        trail_level = position.entry_premium * (1 + TRAIL_1_LOCK)
        if premium <= trail_level:
            return (premium, 'TRAIL_SL1')

    # Momentum exit: premium dropped 5% from peak within 2 candles
    if len(position.premium_history) >= MOMENTUM_WINDOW_CANDLES + 1:
        recent_peak = max(position.premium_history[-(MOMENTUM_WINDOW_CANDLES + 1):])
        if recent_peak > 0:
            drop_from_peak = (recent_peak - premium) / recent_peak
            if drop_from_peak >= MOMENTUM_DROP_PCT and pct_change > 0:
                # Only momentum-exit if we're still in profit (avoid double-counting with SL)
                return (premium, 'MOMENTUM')

    # Time-based exit: after N minutes if P&L is flat
    elapsed = (candle.timestamp - position.entry_time).total_seconds() / 60
    if elapsed >= time_exit_min:
        if TIME_EXIT_DEAD_ZONE[0] <= pct_change <= TIME_EXIT_DEAD_ZONE[1]:
            return (premium, 'TIME')

    # EOD
    if candle.timestamp.time() >= FORCE_EXIT_TIME:
        return (premium, 'EOD')

    return None


# ── Backtest Engine (parameterized for sweep) ────────────────────────
def run_single_backtest(
    days: Dict[str, List[Candle]],
    sorted_dates: List[str],
    target_pct: float = TARGET_PCT,
    sl_pct: float = STOP_LOSS_PCT,
    time_exit_min: int = TIME_EXIT_MINUTES,
    verbose: bool = False,
) -> List[Trade]:
    """Run a single backtest with the given parameters. Returns list of trades."""
    all_trades: List[Trade] = []

    for date_str in sorted_dates:
        candles = days[date_str]
        if len(candles) < 10:
            continue

        position: Optional[OpenPosition] = None
        day_trades = 0
        last_exit_time: Optional[datetime] = None

        for idx, candle in enumerate(candles):
            ct = candle.timestamp.time()

            # ── Check exits first ──
            if position is not None:
                # Force exit at EOD
                if ct >= FORCE_EXIT_TIME:
                    prem = get_premium(candle, position.strike, position.direction)
                    if prem is not None:
                        exit_prem = prem
                        reason = 'EOD'
                    else:
                        exit_prem = position.entry_premium * 0.95
                        reason = 'EOD_NO_PRICE'

                    pnl_pts = exit_prem - position.entry_premium
                    pnl_rs = pnl_pts * LOT_SIZE - BROKERAGE_TOTAL

                    all_trades.append(Trade(
                        entry_time=position.entry_time, exit_time=candle.timestamp,
                        signal_type=position.signal_type, direction=position.direction,
                        strike=position.strike, entry_premium=position.entry_premium,
                        exit_premium=exit_prem, entry_spot=position.entry_spot,
                        exit_spot=candle.spot_price, pnl_pts=pnl_pts, pnl_rs=pnl_rs,
                        exit_reason=reason, date=date_str,
                    ))
                    last_exit_time = candle.timestamp
                    position = None
                    continue

                result = check_exit(position, candle, target_pct, sl_pct, time_exit_min)
                if result is not None:
                    exit_prem, reason = result
                    pnl_pts = exit_prem - position.entry_premium
                    pnl_rs = pnl_pts * LOT_SIZE - BROKERAGE_TOTAL

                    all_trades.append(Trade(
                        entry_time=position.entry_time, exit_time=candle.timestamp,
                        signal_type=position.signal_type, direction=position.direction,
                        strike=position.strike, entry_premium=position.entry_premium,
                        exit_premium=exit_prem, entry_spot=position.entry_spot,
                        exit_spot=candle.spot_price, pnl_pts=pnl_pts, pnl_rs=pnl_rs,
                        exit_reason=reason, date=date_str,
                    ))
                    last_exit_time = candle.timestamp
                    position = None

            # ── Check entries ──
            if position is not None:
                continue

            if ct < ENTRY_START or ct > ENTRY_END:
                continue

            if day_trades >= MAX_TRADES_PER_DAY:
                continue

            # Cooldown
            if last_exit_time is not None:
                elapsed = (candle.timestamp - last_exit_time).total_seconds() / 60
                if elapsed < COOLDOWN_MINUTES:
                    continue

            # Try signals: A, C, D (no B)
            signal_type = None
            direction = None

            dir_a = detect_signal_a(candles, idx)
            if dir_a:
                signal_type = 'A'
                direction = dir_a

            if signal_type is None:
                dir_c = detect_signal_c(candles, idx)
                if dir_c:
                    signal_type = 'C'
                    direction = dir_c

            if signal_type is None:
                dir_d = detect_signal_d(candles, idx)
                if dir_d:
                    signal_type = 'D'
                    direction = dir_d

            if signal_type is None:
                continue

            # Get strike and premium
            strike = get_trade_strike(candle.spot_price, direction)
            premium = get_premium(candle, strike, direction)

            if premium is None:
                continue
            if premium < MIN_PREMIUM or premium > MAX_PREMIUM:
                continue

            # Open position
            position = OpenPosition(
                entry_time=candle.timestamp,
                signal_type=signal_type,
                direction=direction,
                strike=strike,
                entry_premium=premium,
                entry_spot=candle.spot_price,
                highest_premium=premium,
            )
            day_trades += 1

        # End of day: force-close any open
        if position is not None and candles:
            last_candle = candles[-1]
            prem = get_premium(last_candle, position.strike, position.direction)
            if prem is None:
                prem = position.entry_premium * 0.95
            pnl_pts = prem - position.entry_premium
            pnl_rs = pnl_pts * LOT_SIZE - BROKERAGE_TOTAL

            all_trades.append(Trade(
                entry_time=position.entry_time, exit_time=last_candle.timestamp,
                signal_type=position.signal_type, direction=position.direction,
                strike=position.strike, entry_premium=position.entry_premium,
                exit_premium=prem, entry_spot=position.entry_spot,
                exit_spot=last_candle.spot_price, pnl_pts=pnl_pts, pnl_rs=pnl_rs,
                exit_reason='FORCE_CLOSE', date=date_str,
            ))

    return all_trades


# ── Reporting ────────────────────────────────────────────────────────
def print_full_report(trades: List[Trade], all_dates: List[str]):
    print()
    print("=" * 100)
    print("  RALLY CATCHER V2 — DETAILED BACKTEST REPORT")
    print("=" * 100)

    if not trades:
        print("\n  NO TRADES GENERATED. Check signal parameters.")
        return

    total = len(trades)
    wins = [t for t in trades if t.pnl_rs > 0]
    losses = [t for t in trades if t.pnl_rs <= 0]
    total_pnl = sum(t.pnl_rs for t in trades)
    avg_pnl = total_pnl / total

    gross_pnl_before = sum(t.pnl_pts * LOT_SIZE for t in trades)
    total_brokerage = total * BROKERAGE_TOTAL

    gross_wins = sum(t.pnl_rs for t in wins)
    gross_losses = abs(sum(t.pnl_rs for t in losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    avg_winner = sum(t.pnl_rs for t in wins) / len(wins) if wins else 0
    avg_loser = sum(t.pnl_rs for t in losses) / len(losses) if losses else 0

    best_trade = max(trades, key=lambda t: t.pnl_rs)
    worst_trade = min(trades, key=lambda t: t.pnl_rs)

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    max_dd_pct = 0
    for t in sorted(trades, key=lambda t: t.entry_time):
        cumulative += t.pnl_rs
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
            if peak > 0:
                max_dd_pct = dd / peak * 100

    # Hold times
    hold_times = []
    for t in trades:
        if t.exit_time:
            hold_times.append((t.exit_time - t.entry_time).total_seconds() / 60)
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0

    wr = len(wins) / total * 100

    print(f"""
{'─' * 100}
  SUMMARY STATISTICS
{'─' * 100}
  Total Trades:        {total}
  Wins:                {len(wins)} ({wr:.1f}%)
  Losses:              {len(losses)} ({100-wr:.1f}%)
  Win Rate:            {wr:.1f}%

  P&L Before Brokerage: Rs {gross_pnl_before:+,.0f}
  Total Brokerage:       Rs {total_brokerage:,.0f} ({total} trades x Rs {BROKERAGE_TOTAL})
  P&L After Brokerage:   Rs {total_pnl:+,.0f}
  Average P&L/Trade:     Rs {avg_pnl:+,.0f}
  Profit Factor:         {profit_factor:.2f}

  Gross Wins:          Rs {gross_wins:+,.0f}
  Gross Losses:        Rs {-gross_losses:,.0f}

  Avg Winner:          Rs {avg_winner:+,.0f}
  Avg Loser:           Rs {avg_loser:+,.0f}
  Win/Loss Ratio:      {abs(avg_winner/avg_loser):.2f}x{'' if avg_loser == 0 else ''}

  Best Trade:          Rs {best_trade.pnl_rs:+,.0f} ({best_trade.signal_type}-{best_trade.direction} on {best_trade.date} at {best_trade.entry_time.strftime('%H:%M')})
  Worst Trade:         Rs {worst_trade.pnl_rs:+,.0f} ({worst_trade.signal_type}-{worst_trade.direction} on {worst_trade.date} at {worst_trade.entry_time.strftime('%H:%M')})

  Max Drawdown:        Rs {max_dd:,.0f} ({max_dd_pct:.1f}%)
  Avg Hold Time:       {avg_hold:.1f} min
""")

    # ── By Signal Type ──
    print(f"{'─' * 100}")
    print(f"  SIGNAL ANALYSIS (A=Strong Momentum, C=Compression Breakout, D=OI Trap)")
    print(f"{'─' * 100}")
    print(f"  {'Signal':<12} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12} {'Avg Win':>10} {'Avg Loss':>10} {'PF':>6}")
    print(f"  {'─'*12} {'─'*7} {'─'*6} {'─'*7} {'─'*7} {'─'*10} {'─'*12} {'─'*10} {'─'*10} {'─'*6}")

    for sig in ['A', 'C', 'D']:
        sig_trades = [t for t in trades if t.signal_type == sig]
        if not sig_trades:
            print(f"  Signal {sig:<5} {'0':>7}")
            continue
        sw = [t for t in sig_trades if t.pnl_rs > 0]
        sl = [t for t in sig_trades if t.pnl_rs <= 0]
        swr = len(sw) / len(sig_trades) * 100
        savg = sum(t.pnl_rs for t in sig_trades) / len(sig_trades)
        stotal = sum(t.pnl_rs for t in sig_trades)
        sawg = sum(t.pnl_rs for t in sw) / len(sw) if sw else 0
        salg = sum(t.pnl_rs for t in sl) / len(sl) if sl else 0
        gw = sum(t.pnl_rs for t in sw)
        gl = abs(sum(t.pnl_rs for t in sl))
        pf = gw / gl if gl > 0 else float('inf')
        print(f"  Signal {sig:<5} {len(sig_trades):>7} {len(sw):>6} {len(sl):>7} {swr:>6.1f}% {savg:>+10,.0f} {stotal:>+12,.0f} {sawg:>+10,.0f} {salg:>+10,.0f} {pf:>6.2f}")

    print(f"  {'─'*12} {'─'*7} {'─'*6} {'─'*7} {'─'*7} {'─'*10} {'─'*12} {'─'*10} {'─'*10} {'─'*6}")
    print(f"  {'TOTAL':<12} {total:>7} {len(wins):>6} {len(losses):>7} {wr:>6.1f}% {avg_pnl:>+10,.0f} {total_pnl:>+12,.0f} {avg_winner:>+10,.0f} {avg_loser:>+10,.0f} {profit_factor:>6.2f}")

    # ── Exit Reason Breakdown ──
    exit_reasons = defaultdict(list)
    for t in trades:
        exit_reasons[t.exit_reason].append(t)

    print(f"\n{'─' * 100}")
    print(f"  EXIT REASON BREAKDOWN")
    print(f"{'─' * 100}")
    print(f"  {'Reason':<15} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12}")
    print(f"  {'─'*15} {'─'*7} {'─'*6} {'─'*7} {'─'*10} {'─'*12}")
    for reason in sorted(exit_reasons.keys()):
        rtrades = exit_reasons[reason]
        rwins = sum(1 for t in rtrades if t.pnl_rs > 0)
        rwr = rwins / len(rtrades) * 100
        ravg = sum(t.pnl_rs for t in rtrades) / len(rtrades)
        rtotal = sum(t.pnl_rs for t in rtrades)
        print(f"  {reason:<15} {len(rtrades):>7} {rwins:>6} {rwr:>6.1f}% {ravg:>+10,.0f} {rtotal:>+12,.0f}")

    # ── Direction Analysis ──
    print(f"\n{'─' * 100}")
    print(f"  DIRECTION ANALYSIS (CE vs PE)")
    print(f"{'─' * 100}")
    print(f"  {'Direction':<10} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12}")
    print(f"  {'─'*10} {'─'*7} {'─'*6} {'─'*7} {'─'*10} {'─'*12}")
    for d in ['CE', 'PE']:
        dt = [t for t in trades if t.direction == d]
        if not dt:
            continue
        dw = sum(1 for t in dt if t.pnl_rs > 0)
        dwr = dw / len(dt) * 100
        davg = sum(t.pnl_rs for t in dt) / len(dt)
        dtotal = sum(t.pnl_rs for t in dt)
        print(f"  {d:<10} {len(dt):>7} {dw:>6} {dwr:>6.1f}% {davg:>+10,.0f} {dtotal:>+12,.0f}")

    # ── Daily Breakdown ──
    print(f"\n{'─' * 100}")
    print(f"  DAILY BREAKDOWN")
    print(f"{'─' * 100}")
    print(f"  {'Date':<12} {'Trades':>7} {'W':>4} {'L':>4} {'Gross P&L':>12} {'Brokerage':>10} {'Net P&L':>12} {'Cumulative':>12}")
    print(f"  {'─'*12} {'─'*7} {'─'*4} {'─'*4} {'─'*12} {'─'*10} {'─'*12} {'─'*12}")

    trades_by_day = defaultdict(list)
    for t in trades:
        trades_by_day[t.date].append(t)

    cumulative = 0
    daily_pnls = []
    max_consec_losses = 0
    curr_losses = 0
    max_consec_wins = 0
    curr_wins = 0
    largest_winning_day = ("", float('-inf'))
    largest_losing_day = ("", float('inf'))

    for date_str in sorted(all_dates):
        dt = trades_by_day.get(date_str, [])
        if not dt:
            continue
        dw = sum(1 for t in dt if t.pnl_rs > 0)
        dl = sum(1 for t in dt if t.pnl_rs <= 0)
        gross = sum(t.pnl_pts * LOT_SIZE for t in dt)
        brok = len(dt) * BROKERAGE_TOTAL
        net = gross - brok
        cumulative += net
        daily_pnls.append(net)

        if net > largest_winning_day[1]:
            largest_winning_day = (date_str, net)
        if net < largest_losing_day[1]:
            largest_losing_day = (date_str, net)

        if net > 0:
            curr_wins += 1
            curr_losses = 0
            max_consec_wins = max(max_consec_wins, curr_wins)
        else:
            curr_losses += 1
            curr_wins = 0
            max_consec_losses = max(max_consec_losses, curr_losses)

        print(f"  {date_str:<12} {len(dt):>7} {dw:>4} {dl:>4} {gross:>+12,.0f} {brok:>10,.0f} {net:>+12,.0f} {cumulative:>+12,.0f}")

    # ── Monthly Summary ──
    print(f"\n{'─' * 100}")
    print(f"  MONTHLY SUMMARY")
    print(f"{'─' * 100}")
    print(f"  {'Month':<10} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Net P&L':>12} {'Avg/Trade':>12}")
    print(f"  {'─'*10} {'─'*7} {'─'*6} {'─'*7} {'─'*12} {'─'*12}")

    trades_by_month = defaultdict(list)
    for t in trades:
        trades_by_month[t.date[:7]].append(t)

    for month in sorted(trades_by_month.keys()):
        mt = trades_by_month[month]
        mw = sum(1 for t in mt if t.pnl_rs > 0)
        mwr = mw / len(mt) * 100
        mpnl = sum(t.pnl_rs for t in mt)
        mavg = mpnl / len(mt)
        print(f"  {month:<10} {len(mt):>7} {mw:>6} {mwr:>6.1f}% {mpnl:>+12,.0f} {mavg:>+12,.0f}")

    # ── Time Analysis ──
    print(f"\n{'─' * 100}")
    print(f"  TIME ANALYSIS (by hour of entry)")
    print(f"{'─' * 100}")
    print(f"  {'Hour':<8} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12}")
    print(f"  {'─'*8} {'─'*7} {'─'*6} {'─'*7} {'─'*10} {'─'*12}")

    trades_by_hour = defaultdict(list)
    for t in trades:
        trades_by_hour[t.entry_time.hour].append(t)

    for hour in sorted(trades_by_hour.keys()):
        ht = trades_by_hour[hour]
        hw = sum(1 for t in ht if t.pnl_rs > 0)
        hwr = hw / len(ht) * 100
        havg = sum(t.pnl_rs for t in ht) / len(ht)
        htotal = sum(t.pnl_rs for t in ht)
        print(f"  {hour:02d}:00   {len(ht):>7} {hw:>6} {hwr:>6.1f}% {havg:>+10,.0f} {htotal:>+12,.0f}")

    # ── Risk Metrics ──
    trading_days_count = len(trades_by_day)
    avg_trades_per_day = total / trading_days_count if trading_days_count > 0 else 0
    daily_pnl_mean = sum(daily_pnls) / len(daily_pnls) if daily_pnls else 0
    daily_pnl_std = (sum((p - daily_pnl_mean)**2 for p in daily_pnls) / len(daily_pnls)) ** 0.5 if daily_pnls else 0
    sharpe = daily_pnl_mean / daily_pnl_std if daily_pnl_std > 0 else 0

    # Consecutive trade wins/losses
    max_ct_wins = 0
    max_ct_losses = 0
    cw = 0
    cl = 0
    for t in sorted(trades, key=lambda x: x.entry_time):
        if t.pnl_rs > 0:
            cw += 1
            cl = 0
            max_ct_wins = max(max_ct_wins, cw)
        else:
            cl += 1
            cw = 0
            max_ct_losses = max(max_ct_losses, cl)

    print(f"""
{'─' * 100}
  RISK METRICS
{'─' * 100}
  Max Drawdown:              Rs {max_dd:,.0f} ({max_dd_pct:.1f}%)
  Avg Trades/Day:            {avg_trades_per_day:.1f}
  Trading Days with Trades:  {trading_days_count}

  Largest Winning Day:       {largest_winning_day[0]}  Rs {largest_winning_day[1]:+,.0f}
  Largest Losing Day:        {largest_losing_day[0]}  Rs {largest_losing_day[1]:+,.0f}

  Max Consec Winning Days:   {max_consec_wins}
  Max Consec Losing Days:    {max_consec_losses}
  Max Consec Winning Trades: {max_ct_wins}
  Max Consec Losing Trades:  {max_ct_losses}

  Avg Daily P&L:             Rs {daily_pnl_mean:+,.0f}
  Std Dev Daily P&L:         Rs {daily_pnl_std:,.0f}
  Sharpe Ratio (daily):      {sharpe:.2f}

  Avg Hold Time:             {avg_hold:.1f} min
""")

    # ── Signal-wise Exit Reasons ──
    print(f"{'─' * 100}")
    print(f"  SIGNAL-WISE EXIT REASONS")
    print(f"{'─' * 100}")

    for sig in ['A', 'C', 'D']:
        sig_trades = [t for t in trades if t.signal_type == sig]
        if not sig_trades:
            continue
        print(f"\n  Signal {sig} ({len(sig_trades)} trades):")
        sig_exits = defaultdict(list)
        for t in sig_trades:
            sig_exits[t.exit_reason].append(t)
        print(f"    {'Reason':<15} {'Count':>6} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12}")
        print(f"    {'─'*15} {'─'*6} {'─'*7} {'─'*10} {'─'*12}")
        for reason in sorted(sig_exits.keys()):
            rt = sig_exits[reason]
            rw = sum(1 for t in rt if t.pnl_rs > 0)
            rwr = rw / len(rt) * 100
            ravg = sum(t.pnl_rs for t in rt) / len(rt)
            rtot = sum(t.pnl_rs for t in rt)
            print(f"    {reason:<15} {len(rt):>6} {rwr:>6.1f}% {ravg:>+10,.0f} {rtot:>+12,.0f}")

    # ── ALL Trades (detailed log) ──
    sorted_trades = sorted(trades, key=lambda x: x.entry_time)
    n_show = len(sorted_trades)
    print(f"\n{'─' * 100}")
    print(f"  TRADE LOG (ALL {total} trades)")
    print(f"{'─' * 100}")
    print(f"  {'#':>3} {'Date':<12} {'Entry':>6} {'Exit':>6} {'Sig':>3} {'Dir':>3} {'Strike':>7} {'EntPrem':>8} {'ExPrem':>8} {'Chg%':>7} {'P&L Rs':>10} {'Hold':>5} {'Reason':<12}")
    print(f"  {'─'*3} {'─'*12} {'─'*6} {'─'*6} {'─'*3} {'─'*3} {'─'*7} {'─'*8} {'─'*8} {'─'*7} {'─'*10} {'─'*5} {'─'*12}")
    for i, t in enumerate(sorted_trades, 1):
        exit_t = t.exit_time.strftime('%H:%M') if t.exit_time else '  -- '
        chg = (t.exit_premium - t.entry_premium) / t.entry_premium * 100
        hold = (t.exit_time - t.entry_time).total_seconds() / 60 if t.exit_time else 0
        print(f"  {i:>3} {t.date:<12} {t.entry_time.strftime('%H:%M'):>6} {exit_t:>6} {t.signal_type:>3} {t.direction:>3} {t.strike:>7} {t.entry_premium:>8.1f} {t.exit_premium:>8.1f} {chg:>+6.1f}% {t.pnl_rs:>+10,.0f} {hold:>5.0f}m {t.exit_reason:<12}")

    # ── Final Verdict ──
    print(f"\n{'=' * 100}")
    print(f"  FINAL VERDICT")
    print(f"{'=' * 100}")
    if total_pnl > 0:
        print(f"  PROFITABLE: Rs {total_pnl:+,.0f} over {trading_days_count} trading days")
        print(f"  Avg Rs {daily_pnl_mean:+,.0f}/day = ~Rs {daily_pnl_mean * 22:+,.0f}/month (est. 22 trading days)")
    else:
        print(f"  UNPROFITABLE: Rs {total_pnl:+,.0f} over {trading_days_count} trading days")
        print(f"  Avg Rs {daily_pnl_mean:+,.0f}/day")

    print(f"  Win Rate: {wr:.1f}% | Profit Factor: {profit_factor:.2f} | Sharpe: {sharpe:.2f}")
    print(f"  Brokerage Impact: Rs {total_brokerage:,.0f} ({total_brokerage/abs(gross_pnl_before)*100:.1f}% of gross P&L)" if gross_pnl_before != 0 else "")

    if profit_factor >= 1.5 and wr >= 45:
        print(f"\n  >> Strategy looks VIABLE for live trading")
    elif profit_factor >= 1.2:
        print(f"\n  >> Strategy is PROMISING but needs more data")
    elif profit_factor >= 1.0:
        print(f"\n  >> Strategy is MARGINAL — needs refinement")
    else:
        print(f"\n  >> Strategy is LOSING — do NOT deploy")

    print(f"{'=' * 100}")


# ── Parameter Sweep ──────────────────────────────────────────────────
def run_parameter_sweep(days: Dict[str, List[Candle]], sorted_dates: List[str]):
    print()
    print("=" * 100)
    print("  PARAMETER SWEEP")
    print("=" * 100)

    targets = [0.12, 0.15, 0.18, 0.20, 0.25]
    stop_losses = [-0.06, -0.07, -0.08, -0.10]
    time_exits = [18, 24, 30]

    results = []
    total_combos = len(targets) * len(stop_losses) * len(time_exits)
    print(f"  Running {total_combos} parameter combinations...")
    print()

    combo_num = 0
    for tgt, sl, te in product(targets, stop_losses, time_exits):
        combo_num += 1
        if combo_num % 10 == 0:
            print(f"    ... {combo_num}/{total_combos}")

        trades = run_single_backtest(days, sorted_dates, tgt, sl, te)

        if not trades:
            continue

        total_t = len(trades)
        wins_t = sum(1 for t in trades if t.pnl_rs > 0)
        total_pnl = sum(t.pnl_rs for t in trades)
        wr = wins_t / total_t * 100

        # Daily stats for Sharpe
        daily = defaultdict(float)
        for t in trades:
            daily[t.date] += t.pnl_rs
        daily_vals = list(daily.values())
        if len(daily_vals) > 1:
            mean_d = sum(daily_vals) / len(daily_vals)
            std_d = (sum((v - mean_d)**2 for v in daily_vals) / len(daily_vals)) ** 0.5
            sharpe = mean_d / std_d if std_d > 0 else 0
        else:
            sharpe = 0

        # Profit factor
        gw = sum(t.pnl_rs for t in trades if t.pnl_rs > 0)
        gl = abs(sum(t.pnl_rs for t in trades if t.pnl_rs <= 0))
        pf = gw / gl if gl > 0 else float('inf')

        # Max DD
        cum = 0
        pk = 0
        mdd = 0
        for t in sorted(trades, key=lambda x: x.entry_time):
            cum += t.pnl_rs
            if cum > pk:
                pk = cum
            dd = pk - cum
            if dd > mdd:
                mdd = dd

        results.append({
            'target': tgt,
            'sl': sl,
            'time_exit': te,
            'trades': total_t,
            'wr': wr,
            'pnl': total_pnl,
            'sharpe': sharpe,
            'pf': pf,
            'max_dd': mdd,
        })

    # Sort by P&L
    results_by_pnl = sorted(results, key=lambda r: r['pnl'], reverse=True)
    results_by_sharpe = sorted(results, key=lambda r: r['sharpe'], reverse=True)

    print(f"\n{'─' * 100}")
    print(f"  TOP 10 BY NET P&L")
    print(f"{'─' * 100}")
    print(f"  {'#':>3} {'Target':>8} {'SL':>8} {'TimeEx':>8} {'Trades':>7} {'WR%':>7} {'Net P&L':>12} {'Sharpe':>8} {'PF':>6} {'MaxDD':>10}")
    print(f"  {'─'*3} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*7} {'─'*12} {'─'*8} {'─'*6} {'─'*10}")
    for i, r in enumerate(results_by_pnl[:10], 1):
        print(f"  {i:>3} {r['target']:>7.0%} {r['sl']:>7.0%} {r['time_exit']:>6}m {r['trades']:>7} {r['wr']:>6.1f}% {r['pnl']:>+12,.0f} {r['sharpe']:>8.2f} {r['pf']:>6.2f} {r['max_dd']:>10,.0f}")

    print(f"\n{'─' * 100}")
    print(f"  TOP 10 BY SHARPE RATIO")
    print(f"{'─' * 100}")
    print(f"  {'#':>3} {'Target':>8} {'SL':>8} {'TimeEx':>8} {'Trades':>7} {'WR%':>7} {'Net P&L':>12} {'Sharpe':>8} {'PF':>6} {'MaxDD':>10}")
    print(f"  {'─'*3} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*7} {'─'*12} {'─'*8} {'─'*6} {'─'*10}")
    for i, r in enumerate(results_by_sharpe[:10], 1):
        print(f"  {i:>3} {r['target']:>7.0%} {r['sl']:>7.0%} {r['time_exit']:>6}m {r['trades']:>7} {r['wr']:>6.1f}% {r['pnl']:>+12,.0f} {r['sharpe']:>8.2f} {r['pf']:>6.2f} {r['max_dd']:>10,.0f}")

    # Best overall (weighted: 50% P&L rank + 50% Sharpe rank)
    for r in results:
        pnl_rank = next(i for i, x in enumerate(results_by_pnl) if x is r) + 1
        sharpe_rank = next(i for i, x in enumerate(results_by_sharpe) if x is r) + 1
        r['combined_rank'] = 0.5 * pnl_rank + 0.5 * sharpe_rank

    results_by_combined = sorted(results, key=lambda r: r['combined_rank'])

    print(f"\n{'─' * 100}")
    print(f"  TOP 10 COMBINED (50% P&L rank + 50% Sharpe rank)")
    print(f"{'─' * 100}")
    print(f"  {'#':>3} {'Target':>8} {'SL':>8} {'TimeEx':>8} {'Trades':>7} {'WR%':>7} {'Net P&L':>12} {'Sharpe':>8} {'PF':>6} {'MaxDD':>10}")
    print(f"  {'─'*3} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*7} {'─'*12} {'─'*8} {'─'*6} {'─'*10}")
    for i, r in enumerate(results_by_combined[:10], 1):
        print(f"  {i:>3} {r['target']:>7.0%} {r['sl']:>7.0%} {r['time_exit']:>6}m {r['trades']:>7} {r['wr']:>6.1f}% {r['pnl']:>+12,.0f} {r['sharpe']:>8.2f} {r['pf']:>6.2f} {r['max_dd']:>10,.0f}")

    print(f"\n{'=' * 100}")
    print(f"  OPTIMAL PARAMETERS (by combined rank):")
    best = results_by_combined[0]
    print(f"  Target: {best['target']:.0%}  |  SL: {best['sl']:.0%}  |  Time Exit: {best['time_exit']}m")
    print(f"  {best['trades']} trades | {best['wr']:.1f}% WR | Rs {best['pnl']:+,.0f} P&L | {best['sharpe']:.2f} Sharpe | {best['pf']:.2f} PF")
    print(f"{'=' * 100}")


# ── Main ─────────────────────────────────────────────────────────────
def main():
    import sys

    # Use sweep-winning params by default, or original defaults with --default flag
    use_sweep_winner = "--default" not in sys.argv

    if use_sweep_winner:
        # SWEEP WINNER: -10% SL, 18% target, 30m time exit
        run_sl = -0.10
        run_target = 0.18
        run_time_exit = 30
        label = "SWEEP WINNER"
    else:
        run_sl = STOP_LOSS_PCT
        run_target = TARGET_PCT
        run_time_exit = TIME_EXIT_MINUTES
        label = "DEFAULT"

    print("=" * 110)
    print(f"  RALLY CATCHER V2 — {label} CONFIGURATION")
    print(f"  Signals: A (Strong Momentum) + C (Compression Breakout) + D (OI Trap)")
    print(f"  Exits: +{run_target:.0%} target, {run_sl:.0%} SL, 2-stage trailing, momentum exit, {run_time_exit}m time exit")
    print(f"  32 Trading Days | 3-Min Candles | Rs 72 Brokerage | Rs 80 Min Premium")
    print("=" * 110)
    print()

    days = load_all_data()
    sorted_dates = sorted(days.keys())

    print(f"\nProcessing {len(sorted_dates)} trading days: {sorted_dates[0]} to {sorted_dates[-1]}")
    print("-" * 110)

    # ── Run main backtest with specified params ──
    trades = run_single_backtest(days, sorted_dates, run_target, run_sl, run_time_exit, verbose=True)

    # Print daily summary during run
    trades_by_day = defaultdict(list)
    for t in trades:
        trades_by_day[t.date].append(t)

    cumulative = 0
    for date_str in sorted_dates:
        dt = trades_by_day.get(date_str, [])
        if not dt:
            continue
        dw = sum(1 for t in dt if t.pnl_rs > 0)
        dl = sum(1 for t in dt if t.pnl_rs <= 0)
        dpnl = sum(t.pnl_rs for t in dt)
        cumulative += dpnl
        print(f"  {date_str}  |  Trades: {len(dt):2d}  |  W: {dw}  L: {dl}  |  P&L: Rs {dpnl:+,.0f}  |  Cum: Rs {cumulative:+,.0f}")

    # ── Full Report ──
    print_full_report(trades, sorted_dates)

    # ── Robustness: First half vs Second half ──
    mid = len(sorted_dates) // 2
    first_half_dates = set(sorted_dates[:mid])
    second_half_dates = set(sorted_dates[mid:])
    first_half_trades = [t for t in trades if t.date in first_half_dates]
    second_half_trades = [t for t in trades if t.date in second_half_dates]

    print(f"\n{'=' * 110}")
    print(f"  ROBUSTNESS CHECK — First Half vs Second Half")
    print(f"{'=' * 110}")
    for label_h, ht, dates_h in [
        ("FIRST HALF", first_half_trades, sorted_dates[:mid]),
        ("SECOND HALF", second_half_trades, sorted_dates[mid:]),
    ]:
        hw = sum(1 for t in ht if t.pnl_rs > 0)
        hwr = hw / len(ht) * 100 if ht else 0
        hpnl = sum(t.pnl_rs for t in ht)
        hgw = sum(t.pnl_rs for t in ht if t.pnl_rs > 0)
        hgl = abs(sum(t.pnl_rs for t in ht if t.pnl_rs <= 0))
        hpf = hgw / hgl if hgl > 0 else 0
        cum_h = 0
        pk_h = 0
        mdd_h = 0
        for t in sorted(ht, key=lambda x: x.entry_time):
            cum_h += t.pnl_rs
            if cum_h > pk_h:
                pk_h = cum_h
            dd = pk_h - cum_h
            if dd > mdd_h:
                mdd_h = dd
        print(f"\n  {label_h} ({dates_h[0]} to {dates_h[-1]}, {len(dates_h)} days):")
        print(f"    Trades: {len(ht)} | Wins: {hw} ({hwr:.1f}%) | Net P&L: Rs {hpnl:+,.0f} | PF: {hpf:.2f} | Max DD: Rs {mdd_h:,.0f}")

    print(f"\n{'=' * 110}")

    # ── Skip sweep if using winner params (already found) ──
    if "--sweep" in sys.argv:
        run_parameter_sweep(days, sorted_dates)


if __name__ == '__main__':
    main()
