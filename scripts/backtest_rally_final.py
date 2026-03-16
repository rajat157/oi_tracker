"""
Backtest: Rally Catcher FINAL — Production-Ready
=================================================
Based on V2 parameter sweep winner: SL=-10%, Target=+18%, Time exit=30 min

Changes from V2:
  - Signal A DROPPED entirely (37.5% WR, PF 0.68 — net drag)
  - Signal C (Compression Breakout) — kept as-is (72.7% WR in V2)
  - Signal D (OI Trap) — added premium confirmation filter (+2% last candle)
  - Signal E (Trend Continuation After Pullback) — NEW
  - Exit: 2-stage trailing stop, momentum exit (6% in 6 min), conditional time exit
  - SL widened to -10% (was -7%), time exit to 30 min (was 24 min)

Data: oi_tracker.db, 3-min candles, 32 trading days (2026-01-30 to 2026-03-16)

Usage:
    cd D:/Projects/oi_tracker && uv run python scripts/backtest_rally_final.py
"""

import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
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

# Exit parameters — V2 sweep winner
TARGET_PCT = 0.18       # +18% premium
STOP_LOSS_PCT = -0.10   # -10% premium
TIME_EXIT_MINUTES = 30  # exit flat trades after 30 min
TIME_EXIT_DEAD_ZONE = (-0.04, 0.04)  # only time-exit if P&L between -4% and +4%

# Trailing stop — 2 stages
TRAIL_1_TRIGGER = 0.10  # after +10%, move SL to +4%
TRAIL_1_LOCK = 0.04
TRAIL_2_TRIGGER = 0.14  # after +14%, move SL to +9%
TRAIL_2_LOCK = 0.09

# Momentum exit: if premium drops 6% from peak in any 6-min window
MOMENTUM_DROP_PCT = 0.06
MOMENTUM_WINDOW_CANDLES = 2  # 6 min = 2 candles

# Signal C — Compression Breakout (unchanged from V2)
SIG_C_RANGE_PTS = 25         # max range
SIG_C_CONSOLIDATION_CANDLES = 6   # 18 min
SIG_C_BREAKOUT_PTS = 12      # breakout beyond range
SIG_C_VIX_THRESHOLD = 0.03   # VIX change threshold for bias

# Signal D — OI Trap Breakout (+ premium confirmation)
SIG_D_OI_THRESHOLD = 50_000  # min OI change on trapped side in 5 candles (15 min)
SIG_D_OI_LOOKBACK = 5        # candles (15 min)
SIG_D_SPOT_MOVE = 15         # pts against trapped side in 2 candles (6 min)
SIG_D_PREMIUM_CONFIRM = 0.02 # premium must have moved +2% in last candle

# Signal E — Trend Continuation After Pullback (NEW)
SIG_E_RALLY_PTS = 40         # min initial rally size (pts)
SIG_E_RALLY_LOOKBACK = 10    # candles (30 min) to find the rally
SIG_E_PULLBACK_MIN = 15      # min pullback depth (pts)
SIG_E_PULLBACK_MAX = 25      # max pullback depth (pts)
SIG_E_PULLBACK_LOOKBACK = 3  # candles (9 min) for pullback + resumption

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
    signal_type: str          # 'C', 'D', 'E'
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
    premium_history: list = field(default_factory=list)


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
def detect_signal_c(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal C -- Compression Breakout (unchanged from V2):
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
        # Upside breakout: PE writers trapped -> PE OI buildup is confirmation
        oi_change = get_oi_buildup(current, 'PE', atm)
        if oi_change <= 0:
            return None
    else:
        # Downside breakout: CE writers trapped -> CE OI buildup
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
            # VIX rising -> favor UP breakouts, penalize DOWN
            if direction == 'PE':
                return None
        elif vix_change < -SIG_C_VIX_THRESHOLD:
            # VIX falling -> favor DOWN breakouts, penalize UP
            if direction == 'CE':
                return None

    return direction


def detect_signal_d(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal D -- OI Trap Breakout (with premium confirmation):
    - Significant OI built on one side in last 15 min (5 candles)
    - Then spot moves sharply AGAINST those positions
    - ADDED: premium must have moved +2% in last candle (confirmation trap is unwinding)
    """
    if idx < SIG_D_OI_LOOKBACK + 2:
        return None

    current = candles[idx]
    prev1 = candles[idx - 1]
    atm = get_atm_strike(current.spot_price)

    # Check OI buildup in the lookback window
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

    direction = None

    # Case 1: Large PE OI buildup -> spot moves UP (PE trapped)
    if pe_oi_total >= SIG_D_OI_THRESHOLD and spot_move >= SIG_D_SPOT_MOVE:
        direction = 'CE'

    # Case 2: Large CE OI buildup -> spot moves DOWN (CE trapped)
    if ce_oi_total >= SIG_D_OI_THRESHOLD and spot_move <= -SIG_D_SPOT_MOVE:
        direction = 'PE'

    if direction is None:
        return None

    # Premium confirmation: premium must have increased by at least 2% in the last candle
    strike = get_trade_strike(current.spot_price, direction)
    curr_prem = get_premium(current, strike, direction)
    prev_prem = get_premium(prev1, strike, direction)

    if curr_prem is None or prev_prem is None or prev_prem <= 0:
        return None

    prem_change = (curr_prem - prev_prem) / prev_prem
    if prem_change < SIG_D_PREMIUM_CONFIRM:
        return None  # Premium not confirming the trap unwind

    return direction


def detect_signal_e(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal E -- Trend Continuation After Pullback (NEW):
    - In last 30 min (10 candles), there was a rally of 40+ pts
    - In last 9 min (3 candles), spot pulled back 15-25 pts
    - Current candle resumes in original direction (higher low for UP, lower high for DOWN)
    """
    if idx < SIG_E_RALLY_LOOKBACK + SIG_E_PULLBACK_LOOKBACK:
        return None

    current = candles[idx]

    # Find the rally in the last 30 min (look at candles [idx-13] to [idx-3])
    # We want the rally to have happened BEFORE the pullback window
    rally_start_idx = idx - SIG_E_RALLY_LOOKBACK - SIG_E_PULLBACK_LOOKBACK
    rally_end_idx = idx - SIG_E_PULLBACK_LOOKBACK

    if rally_start_idx < 0:
        return None

    # Get prices in the rally window
    rally_prices = [(j, candles[j].spot_price) for j in range(rally_start_idx, rally_end_idx + 1)]
    if len(rally_prices) < 2:
        return None

    # Find max rally within this window
    min_price_in_rally = min(p for _, p in rally_prices)
    max_price_in_rally = max(p for _, p in rally_prices)
    min_idx = next(j for j, p in rally_prices if p == min_price_in_rally)
    max_idx = next(j for j, p in rally_prices if p == max_price_in_rally)

    up_rally = max_price_in_rally - min_price_in_rally
    # Determine rally direction: min must come before max for UP rally, vice versa
    if min_idx < max_idx and up_rally >= SIG_E_RALLY_PTS:
        rally_direction = 'UP'
        rally_peak = max_price_in_rally
    elif max_idx < min_idx and up_rally >= SIG_E_RALLY_PTS:
        rally_direction = 'DOWN'
        rally_peak = min_price_in_rally
    else:
        return None

    # Now check pullback in the last 3 candles (9 min): [idx-2], [idx-1], [idx]
    pullback_candles = [candles[j] for j in range(idx - SIG_E_PULLBACK_LOOKBACK, idx + 1)]
    pullback_prices = [c.spot_price for c in pullback_candles]

    if rally_direction == 'UP':
        # After UP rally, pullback = peak - min of pullback candles
        pullback_low = min(pullback_prices[:-1])  # exclude current candle
        pullback_depth = rally_peak - pullback_low

        if pullback_depth < SIG_E_PULLBACK_MIN or pullback_depth > SIG_E_PULLBACK_MAX:
            return None

        # Resumption: current candle must be higher than the pullback low
        # AND higher than the previous candle (making higher low)
        if current.spot_price <= pullback_low:
            return None
        if current.spot_price <= candles[idx - 1].spot_price:
            return None  # Not resuming upward

        return 'CE'

    else:  # DOWN rally
        # After DOWN rally, pullback = max of pullback candles - valley
        pullback_high = max(pullback_prices[:-1])  # exclude current candle
        pullback_depth = pullback_high - rally_peak

        if pullback_depth < SIG_E_PULLBACK_MIN or pullback_depth > SIG_E_PULLBACK_MAX:
            return None

        # Resumption: current candle must be lower than the pullback high
        # AND lower than the previous candle (making lower high)
        if current.spot_price >= pullback_high:
            return None
        if current.spot_price >= candles[idx - 1].spot_price:
            return None  # Not resuming downward

        return 'PE'


# ── Position Management ──────────────────────────────────────────────
def check_exit(position: OpenPosition, candle: Candle) -> Optional[Tuple[float, str]]:
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
    if pct_change >= TARGET_PCT:
        return (premium, 'TARGET')

    # Check stop loss
    if pct_change <= STOP_LOSS_PCT:
        return (premium, 'SL')

    # Check trailing stop -- stage 2 first (tighter)
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

    # Momentum exit: premium dropped 6% from peak within 2 candles
    if len(position.premium_history) >= MOMENTUM_WINDOW_CANDLES + 1:
        recent_peak = max(position.premium_history[-(MOMENTUM_WINDOW_CANDLES + 1):])
        if recent_peak > 0:
            drop_from_peak = (recent_peak - premium) / recent_peak
            if drop_from_peak >= MOMENTUM_DROP_PCT and pct_change > 0:
                # Only momentum-exit if we're still in profit
                return (premium, 'MOMENTUM')

    # Time-based exit: after 30 min if P&L is flat (between -4% and +4%)
    elapsed = (candle.timestamp - position.entry_time).total_seconds() / 60
    if elapsed >= TIME_EXIT_MINUTES:
        if TIME_EXIT_DEAD_ZONE[0] <= pct_change <= TIME_EXIT_DEAD_ZONE[1]:
            return (premium, 'TIME')

    # EOD
    if candle.timestamp.time() >= FORCE_EXIT_TIME:
        return (premium, 'EOD')

    return None


# ── Backtest Engine ──────────────────────────────────────────────────
def run_backtest(days: Dict[str, List[Candle]], sorted_dates: List[str]) -> List[Trade]:
    """Run the backtest. Returns list of trades."""
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
                        reason = 'EOD'

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

                result = check_exit(position, candle)
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

            # Try signals: C, D, E (Signal A dropped)
            signal_type = None
            direction = None

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
                dir_e = detect_signal_e(candles, idx)
                if dir_e:
                    signal_type = 'E'
                    direction = dir_e

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
    """Print the complete professional backtest report."""

    # ================================================================
    #  EXECUTIVE SUMMARY
    # ================================================================
    print()
    print("=" * 110)
    print("=" * 110)
    print("  RALLY CATCHER FINAL -- PRODUCTION BACKTEST REPORT")
    print("=" * 110)
    print("=" * 110)

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
    sorted_trades = sorted(trades, key=lambda t: t.entry_time)
    cumulative = 0
    peak = 0
    max_dd = 0
    max_dd_pct = 0
    dd_start_date = ""
    dd_end_date = ""
    current_dd_start = ""
    for t in sorted_trades:
        cumulative += t.pnl_rs
        if cumulative > peak:
            peak = cumulative
            current_dd_start = t.date
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
            dd_start_date = current_dd_start
            dd_end_date = t.date
            if peak > 0:
                max_dd_pct = dd / peak * 100

    # Hold times
    hold_times = []
    for t in trades:
        if t.exit_time:
            hold_times.append((t.exit_time - t.entry_time).total_seconds() / 60)
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0

    wr = len(wins) / total * 100

    # Consecutive trades
    max_ct_wins = 0
    max_ct_losses = 0
    cw = 0
    cl = 0
    for t in sorted_trades:
        if t.pnl_rs > 0:
            cw += 1
            cl = 0
            max_ct_wins = max(max_ct_wins, cw)
        else:
            cl += 1
            cw = 0
            max_ct_losses = max(max_ct_losses, cl)

    # Daily P&L stats
    trades_by_day = defaultdict(list)
    for t in trades:
        trades_by_day[t.date].append(t)

    daily_pnls = []
    for date_str in sorted(all_dates):
        dt = trades_by_day.get(date_str, [])
        if dt:
            net = sum(t.pnl_rs for t in dt)
            daily_pnls.append(net)

    trading_days_count = len(trades_by_day)
    win_days = sum(1 for p in daily_pnls if p > 0)
    loss_days = sum(1 for p in daily_pnls if p <= 0)

    daily_pnl_mean = sum(daily_pnls) / len(daily_pnls) if daily_pnls else 0
    daily_pnl_std = (sum((p - daily_pnl_mean)**2 for p in daily_pnls) / len(daily_pnls)) ** 0.5 if daily_pnls else 0
    sharpe = daily_pnl_mean / daily_pnl_std if daily_pnl_std > 0 else 0

    # Calmar: annualized return / max DD
    annualized_return = daily_pnl_mean * 252  # approx trading days/year
    calmar = annualized_return / max_dd if max_dd > 0 else float('inf')

    # Drawdown duration
    cum = 0
    pk = 0
    in_dd = False
    dd_duration_days = 0
    max_dd_duration = 0
    current_dd_days = 0
    prev_date = None
    for date_str in sorted(all_dates):
        dt = trades_by_day.get(date_str, [])
        if not dt:
            continue
        net = sum(t.pnl_rs for t in dt)
        cum += net
        if cum > pk:
            pk = cum
            if current_dd_days > max_dd_duration:
                max_dd_duration = current_dd_days
            current_dd_days = 0
        else:
            current_dd_days += 1
    if current_dd_days > max_dd_duration:
        max_dd_duration = current_dd_days

    largest_winning_day = ("", float('-inf'))
    largest_losing_day = ("", float('inf'))
    for date_str in sorted(all_dates):
        dt = trades_by_day.get(date_str, [])
        if not dt:
            continue
        net = sum(t.pnl_rs for t in dt)
        if net > largest_winning_day[1]:
            largest_winning_day = (date_str, net)
        if net < largest_losing_day[1]:
            largest_losing_day = (date_str, net)

    # ── Print Executive Summary ──
    first_date = sorted_trades[0].date
    last_date = sorted_trades[-1].date

    print(f"""
+{'=' * 108}+
|  EXECUTIVE SUMMARY                                                                                         |
+{'=' * 108}+
|  Strategy:     Rally Catcher FINAL (Signals C + D + E)                                                     |
|  Period:       {first_date} to {last_date} ({trading_days_count} trading days with trades){'': <43}|
|  Parameters:   SL=-10%, Target=+18%, Time Exit=30min, 2-stage trailing, momentum exit                      |
|  Net P&L:      Rs {total_pnl:>+10,.0f}  |  Win Rate: {wr:.1f}%  |  Profit Factor: {profit_factor:.2f}  |  Sharpe: {sharpe:.2f}{'': <24}|
+{'=' * 108}+
""")

    # ================================================================
    #  TRADE STATISTICS
    # ================================================================
    wl_ratio = abs(avg_winner / avg_loser) if avg_loser != 0 else float('inf')
    expectancy = avg_pnl

    print(f"{'=' * 110}")
    print(f"  TRADE STATISTICS")
    print(f"{'=' * 110}")
    print(f"""
  Total Trades:            {total}
  Wins:                    {len(wins)} ({wr:.1f}%)
  Losses:                  {len(losses)} ({100-wr:.1f}%)
  Win Rate:                {wr:.1f}%

  P&L Before Brokerage:   Rs {gross_pnl_before:>+12,.0f}
  Total Brokerage:         Rs {total_brokerage:>12,.0f}  ({total} trades x Rs {BROKERAGE_TOTAL})
  Net P&L After Brokerage: Rs {total_pnl:>+12,.0f}

  Profit Factor:           {profit_factor:.2f}
  Expectancy/Trade:        Rs {expectancy:>+,.0f}

  Average Winner:          Rs {avg_winner:>+,.0f}
  Average Loser:           Rs {avg_loser:>+,.0f}
  Win/Loss Ratio:          {wl_ratio:.2f}x

  Max Consec Wins:         {max_ct_wins}
  Max Consec Losses:       {max_ct_losses}

  Average Holding Time:    {avg_hold:.1f} min

  Best Trade:              Rs {best_trade.pnl_rs:>+,.0f}  ({best_trade.signal_type}-{best_trade.direction} on {best_trade.date} at {best_trade.entry_time.strftime('%H:%M')})
  Worst Trade:             Rs {worst_trade.pnl_rs:>+,.0f}  ({worst_trade.signal_type}-{worst_trade.direction} on {worst_trade.date} at {worst_trade.entry_time.strftime('%H:%M')})
""")

    # ================================================================
    #  BY SIGNAL TYPE
    # ================================================================
    print(f"{'=' * 110}")
    print(f"  BY SIGNAL TYPE")
    print(f"  C = Compression Breakout  |  D = OI Trap  |  E = Trend Continuation After Pullback")
    print(f"{'=' * 110}")
    print(f"  {'Signal':<10} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12} {'PF':>6} {'Best':>10} {'Worst':>10}")
    print(f"  {'':=<10} {'':=<7} {'':=<6} {'':=<7} {'':=<7} {'':=<10} {'':=<12} {'':=<6} {'':=<10} {'':=<10}")

    for sig, sig_name in [('C', 'Compress'), ('D', 'OI Trap'), ('E', 'Pullback')]:
        sig_trades = [t for t in trades if t.signal_type == sig]
        if not sig_trades:
            print(f"  {sig_name:<10} {'0':>7}")
            continue
        sw = [t for t in sig_trades if t.pnl_rs > 0]
        sl_t = [t for t in sig_trades if t.pnl_rs <= 0]
        swr = len(sw) / len(sig_trades) * 100
        savg = sum(t.pnl_rs for t in sig_trades) / len(sig_trades)
        stotal = sum(t.pnl_rs for t in sig_trades)
        gw = sum(t.pnl_rs for t in sw)
        gl = abs(sum(t.pnl_rs for t in sl_t))
        pf = gw / gl if gl > 0 else float('inf')
        best_s = max(sig_trades, key=lambda t: t.pnl_rs)
        worst_s = min(sig_trades, key=lambda t: t.pnl_rs)
        print(f"  {sig_name:<10} {len(sig_trades):>7} {len(sw):>6} {len(sl_t):>7} {swr:>6.1f}% {savg:>+10,.0f} {stotal:>+12,.0f} {pf:>6.2f} {best_s.pnl_rs:>+10,.0f} {worst_s.pnl_rs:>+10,.0f}")

    print(f"  {'':=<10} {'':=<7} {'':=<6} {'':=<7} {'':=<7} {'':=<10} {'':=<12} {'':=<6} {'':=<10} {'':=<10}")
    print(f"  {'TOTAL':<10} {total:>7} {len(wins):>6} {len(losses):>7} {wr:>6.1f}% {avg_pnl:>+10,.0f} {total_pnl:>+12,.0f} {profit_factor:>6.2f} {best_trade.pnl_rs:>+10,.0f} {worst_trade.pnl_rs:>+10,.0f}")

    # ================================================================
    #  BY DIRECTION
    # ================================================================
    print(f"\n{'=' * 110}")
    print(f"  BY DIRECTION")
    print(f"{'=' * 110}")
    print(f"  {'Direction':<10} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12}")
    print(f"  {'':=<10} {'':=<7} {'':=<6} {'':=<7} {'':=<10} {'':=<12}")
    for d in ['CE', 'PE']:
        dt = [t for t in trades if t.direction == d]
        if not dt:
            print(f"  {d:<10} {'0':>7}")
            continue
        dw = sum(1 for t in dt if t.pnl_rs > 0)
        dwr = dw / len(dt) * 100
        davg = sum(t.pnl_rs for t in dt) / len(dt)
        dtotal = sum(t.pnl_rs for t in dt)
        print(f"  {d:<10} {len(dt):>7} {dw:>6} {dwr:>6.1f}% {davg:>+10,.0f} {dtotal:>+12,.0f}")

    # ================================================================
    #  EXIT ANALYSIS
    # ================================================================
    exit_reasons = defaultdict(list)
    for t in trades:
        exit_reasons[t.exit_reason].append(t)

    print(f"\n{'=' * 110}")
    print(f"  EXIT ANALYSIS")
    print(f"{'=' * 110}")
    print(f"  {'Reason':<15} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12}")
    print(f"  {'':=<15} {'':=<7} {'':=<6} {'':=<7} {'':=<10} {'':=<12}")
    for reason in sorted(exit_reasons.keys()):
        rtrades = exit_reasons[reason]
        rwins = sum(1 for t in rtrades if t.pnl_rs > 0)
        rwr = rwins / len(rtrades) * 100
        ravg = sum(t.pnl_rs for t in rtrades) / len(rtrades)
        rtotal = sum(t.pnl_rs for t in rtrades)
        print(f"  {reason:<15} {len(rtrades):>7} {rwins:>6} {rwr:>6.1f}% {ravg:>+10,.0f} {rtotal:>+12,.0f}")

    # ================================================================
    #  DAILY P&L TABLE
    # ================================================================
    print(f"\n{'=' * 110}")
    print(f"  DAILY P&L TABLE")
    print(f"{'=' * 110}")
    print(f"  {'Date':<12} {'Trades':>7} {'W-L':>7} {'Gross P&L':>12} {'Brokerage':>10} {'Net P&L':>12} {'Cumulative':>12} {'DD from Pk':>12}")
    print(f"  {'':=<12} {'':=<7} {'':=<7} {'':=<12} {'':=<10} {'':=<12} {'':=<12} {'':=<12}")

    cumulative = 0
    peak_cum = 0
    max_consec_wins_days = 0
    max_consec_losses_days = 0
    curr_w_days = 0
    curr_l_days = 0

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
        if cumulative > peak_cum:
            peak_cum = cumulative
        dd = peak_cum - cumulative

        if net > 0:
            curr_w_days += 1
            curr_l_days = 0
            max_consec_wins_days = max(max_consec_wins_days, curr_w_days)
        elif net < 0:
            curr_l_days += 1
            curr_w_days = 0
            max_consec_losses_days = max(max_consec_losses_days, curr_l_days)
        else:
            curr_w_days = 0
            curr_l_days = 0

        dd_str = f"Rs {dd:>+,.0f}" if dd > 0 else "-"
        print(f"  {date_str:<12} {len(dt):>7} {dw:>2}-{dl:<2}  {gross:>+12,.0f} {brok:>10,.0f} {net:>+12,.0f} {cumulative:>+12,.0f}  {dd_str:>10}")

    # ================================================================
    #  MONTHLY SUMMARY
    # ================================================================
    print(f"\n{'=' * 110}")
    print(f"  MONTHLY SUMMARY")
    print(f"{'=' * 110}")
    print(f"  {'Month':<10} {'Trd Days':>9} {'Trades':>7} {'WR%':>7} {'Gross P&L':>12} {'Net P&L':>12} {'Per Day':>12}")
    print(f"  {'':=<10} {'':=<9} {'':=<7} {'':=<7} {'':=<12} {'':=<12} {'':=<12}")

    trades_by_month = defaultdict(list)
    days_by_month = defaultdict(set)
    for t in trades:
        month = t.date[:7]
        trades_by_month[month].append(t)
        days_by_month[month].add(t.date)

    for month in sorted(trades_by_month.keys()):
        mt = trades_by_month[month]
        mw = sum(1 for t in mt if t.pnl_rs > 0)
        mwr = mw / len(mt) * 100
        mgross = sum(t.pnl_pts * LOT_SIZE for t in mt)
        mpnl = sum(t.pnl_rs for t in mt)
        trd_days = len(days_by_month[month])
        per_day = mpnl / trd_days if trd_days > 0 else 0
        print(f"  {month:<10} {trd_days:>9} {len(mt):>7} {mwr:>6.1f}% {mgross:>+12,.0f} {mpnl:>+12,.0f} {per_day:>+12,.0f}")

    # ================================================================
    #  TIME OF DAY ANALYSIS
    # ================================================================
    print(f"\n{'=' * 110}")
    print(f"  TIME OF DAY ANALYSIS (by hour of entry)")
    print(f"{'=' * 110}")
    print(f"  {'Hour':<8} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12}")
    print(f"  {'':=<8} {'':=<7} {'':=<6} {'':=<7} {'':=<10} {'':=<12}")

    trades_by_hour = defaultdict(list)
    for t in trades:
        trades_by_hour[t.entry_time.hour].append(t)

    for hour in sorted(trades_by_hour.keys()):
        ht = trades_by_hour[hour]
        hw = sum(1 for t in ht if t.pnl_rs > 0)
        hwr = hw / len(ht) * 100
        havg = sum(t.pnl_rs for t in ht) / len(ht)
        htotal = sum(t.pnl_rs for t in ht)
        print(f"  {hour:02d}:00    {len(ht):>7} {hw:>6} {hwr:>6.1f}% {havg:>+10,.0f} {htotal:>+12,.0f}")

    # ================================================================
    #  RISK METRICS
    # ================================================================
    print(f"\n{'=' * 110}")
    print(f"  RISK METRICS")
    print(f"{'=' * 110}")
    print(f"""
  Max Drawdown:                Rs {max_dd:,.0f} ({max_dd_pct:.1f}% of peak equity)
  Drawdown Duration:           {max_dd_duration} trading days
  Drawdown Period:             {dd_start_date} to {dd_end_date}

  Largest Winning Day:         {largest_winning_day[0]}  Rs {largest_winning_day[1]:+,.0f}
  Largest Losing Day:          {largest_losing_day[0]}  Rs {largest_losing_day[1]:+,.0f}

  Win Days:                    {win_days}
  Loss Days:                   {loss_days}
  Win Days %:                  {win_days/(win_days+loss_days)*100:.1f}%

  Max Consec Winning Days:     {max_consec_wins_days}
  Max Consec Losing Days:      {max_consec_losses_days}
  Max Consec Winning Trades:   {max_ct_wins}
  Max Consec Losing Trades:    {max_ct_losses}

  Avg Daily P&L:               Rs {daily_pnl_mean:+,.0f}
  Std Dev Daily P&L:           Rs {daily_pnl_std:,.0f}
  Sharpe Ratio (daily):        {sharpe:.2f}
  Calmar Ratio (annual):       {calmar:.2f}

  Avg Trades/Day:              {total / trading_days_count:.1f}
  Avg Hold Time:               {avg_hold:.1f} min
""")

    # ================================================================
    #  EQUITY CURVE (ASCII)
    # ================================================================
    print(f"{'=' * 110}")
    print(f"  EQUITY CURVE (Cumulative P&L)")
    print(f"{'=' * 110}")

    # Build cumulative by day
    cum_by_day = []
    cumulative = 0
    for date_str in sorted(all_dates):
        dt = trades_by_day.get(date_str, [])
        if not dt:
            continue
        net = sum(t.pnl_rs for t in dt)
        cumulative += net
        cum_by_day.append((date_str, cumulative))

    if cum_by_day:
        min_cum = min(c for _, c in cum_by_day)
        max_cum = max(c for _, c in cum_by_day)
        chart_width = 70

        # Scale
        range_cum = max_cum - min_cum
        if range_cum == 0:
            range_cum = 1

        print()
        for date_str, cum_val in cum_by_day:
            bar_pos = int((cum_val - min_cum) / range_cum * chart_width)
            zero_pos = int((0 - min_cum) / range_cum * chart_width)

            # Build the bar
            line = [' '] * (chart_width + 1)
            if zero_pos >= 0 and zero_pos <= chart_width:
                line[zero_pos] = '|'

            if cum_val >= 0:
                start = max(zero_pos, 0)
                end = bar_pos
                for i in range(start, min(end + 1, chart_width + 1)):
                    line[i] = '#'
            else:
                start = bar_pos
                end = min(zero_pos, chart_width)
                for i in range(max(start, 0), min(end + 1, chart_width + 1)):
                    line[i] = '-'

            bar_str = ''.join(line)
            short_date = date_str[5:]  # MM-DD
            print(f"  {short_date} {bar_str} Rs {cum_val:>+8,.0f}")

        print(f"\n  Scale: Rs {min_cum:+,.0f} to Rs {max_cum:+,.0f}")
    print()

    # ================================================================
    #  TRADE LOG (ALL TRADES)
    # ================================================================
    print(f"{'=' * 110}")
    print(f"  TRADE LOG (ALL {total} TRADES)")
    print(f"{'=' * 110}")
    print(f"  {'#':>3} {'Date':<12} {'Time':>6} {'Sig':>3} {'Dir':>3} {'Strike':>7} {'EntPrem':>8} {'ExPrem':>8} {'Chg%':>7} {'ExitReason':<12} {'Gross P&L':>10} {'Net P&L':>10} {'Cum P&L':>10}")
    print(f"  {'':=<3} {'':=<12} {'':=<6} {'':=<3} {'':=<3} {'':=<7} {'':=<8} {'':=<8} {'':=<7} {'':=<12} {'':=<10} {'':=<10} {'':=<10}")

    cumulative = 0
    for i, t in enumerate(sorted_trades, 1):
        chg = (t.exit_premium - t.entry_premium) / t.entry_premium * 100
        gross = t.pnl_pts * LOT_SIZE
        cumulative += t.pnl_rs
        print(f"  {i:>3} {t.date:<12} {t.entry_time.strftime('%H:%M'):>6} {t.signal_type:>3} {t.direction:>3} {t.strike:>7} {t.entry_premium:>8.1f} {t.exit_premium:>8.1f} {chg:>+6.1f}% {t.exit_reason:<12} {gross:>+10,.0f} {t.pnl_rs:>+10,.0f} {cumulative:>+10,.0f}")

    # ================================================================
    #  FINAL VERDICT
    # ================================================================
    print(f"\n{'=' * 110}")
    print(f"{'=' * 110}")
    print(f"  FINAL VERDICT")
    print(f"{'=' * 110}")
    print(f"{'=' * 110}")

    if total_pnl > 0:
        monthly_est = daily_pnl_mean * 22
        annual_est = daily_pnl_mean * 252
        print(f"""
  PROFITABLE: Rs {total_pnl:+,.0f} over {trading_days_count} trading days

  P&L Breakdown:
    Gross P&L:     Rs {gross_pnl_before:+,.0f}
    Brokerage:     Rs {total_brokerage:,.0f} ({total_brokerage/abs(gross_pnl_before)*100:.1f}% of gross)
    Net P&L:       Rs {total_pnl:+,.0f}

  Projections (assuming similar market conditions):
    Daily avg:     Rs {daily_pnl_mean:+,.0f}
    Monthly est:   Rs {monthly_est:+,.0f} (22 trading days)
    Annual est:    Rs {annual_est:+,.0f} (252 trading days)

  Key Metrics:
    Win Rate:      {wr:.1f}%
    Profit Factor: {profit_factor:.2f}
    Sharpe Ratio:  {sharpe:.2f}
    Calmar Ratio:  {calmar:.2f}
    Max Drawdown:  Rs {max_dd:,.0f}
""")
    else:
        print(f"""
  UNPROFITABLE: Rs {total_pnl:+,.0f} over {trading_days_count} trading days
  Avg Rs {daily_pnl_mean:+,.0f}/day
""")

    if profit_factor >= 1.5 and wr >= 50:
        verdict = "STRONG -- Ready for paper trading"
    elif profit_factor >= 1.5 and wr >= 45:
        verdict = "VIABLE -- Ready for paper trading with monitoring"
    elif profit_factor >= 1.2:
        verdict = "PROMISING -- Needs more out-of-sample data"
    elif profit_factor >= 1.0:
        verdict = "MARGINAL -- Needs refinement before deployment"
    else:
        verdict = "LOSING -- Do NOT deploy"

    print(f"  Verdict: {verdict}")
    print(f"{'=' * 110}")


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print("=" * 110)
    print("  RALLY CATCHER FINAL -- PRODUCTION BACKTEST")
    print("  Signals: C (Compression Breakout) + D (OI Trap + Premium Confirm) + E (Trend Continuation)")
    print("  Exits: +18% target, -10% SL, 2-stage trailing, 6% momentum exit, 30m conditional time exit")
    print(f"  Strike: 2 ITM (100 pts)  |  Lot: {LOT_SIZE} qty  |  Brokerage: Rs {BROKERAGE_TOTAL}/trade  |  Min Premium: Rs {MIN_PREMIUM}")
    print("=" * 110)
    print()

    days = load_all_data()
    sorted_dates = sorted(days.keys())

    print(f"\nProcessing {len(sorted_dates)} trading days: {sorted_dates[0]} to {sorted_dates[-1]}")
    print("-" * 110)

    # ── Run backtest ──
    trades = run_backtest(days, sorted_dates)

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
        sigs = ', '.join(f"{t.signal_type}-{t.direction}" for t in dt)
        print(f"  {date_str}  |  Trades: {len(dt):2d}  |  W: {dw}  L: {dl}  |  P&L: Rs {dpnl:>+8,.0f}  |  Cum: Rs {cumulative:>+10,.0f}  |  {sigs}")

    # ── Full Report ──
    print_full_report(trades, sorted_dates)


if __name__ == '__main__':
    main()
