"""
Backtest: Rally Catcher Strategy
=================================
Detects NIFTY rallies using three signal types:
  A) Momentum Breakout — jump on existing rally
  B) Reversal Entry — catch the very bottom/top (mean reversion)
  C) Breakout from Compression — breakout after consolidation

Exit logic: +15% target, -8% SL, trailing stop, 30-min time exit, 15:15 force close.

Data: oi_tracker.db, 3-min candles, 32 trading days (2026-01-30 to 2026-03-16)

Usage:
    cd D:/Projects/oi_tracker && uv run python scripts/backtest_rally_catcher.py
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
BROKERAGE_ENTRY = 32    # Rs per trade
BROKERAGE_EXIT = 40     # Rs per trade (higher STT)
BROKERAGE_TOTAL = BROKERAGE_ENTRY + BROKERAGE_EXIT  # Rs 72

# Entry timing
ENTRY_START = time(9, 24)
ENTRY_END = time(14, 30)
FORCE_EXIT_TIME = time(15, 15)

# Position management
MAX_TRADES_PER_DAY = 8
COOLDOWN_MINUTES = 6
MAX_OPEN_POSITIONS = 1

# Exit parameters
TARGET_PCT = 0.15       # +15% premium
STOP_LOSS_PCT = -0.08   # -8% premium
TIME_EXIT_MINUTES = 30  # exit after 30 min
TRAIL_TRIGGER_PCT = 0.08  # after +8% premium gain...
TRAIL_LOCK_PCT = 0.03   # ...lock in +3% profit

# Signal A — Momentum Breakout
SIG_A_MOVE_2_CANDLES = 20    # pts in 2 candles (6 min)
SIG_A_MOVE_3_CANDLES = 25    # pts in 3 candles (9 min)

# Signal B — Reversal Entry
SIG_B_TREND_PTS = 20         # pts in one direction over 15 min (5 candles)
SIG_B_REVERSAL_PTS = 10      # pts reversal in last 2 candles

# Signal C — Breakout from Compression
SIG_C_RANGE_PTS = 20         # max range during consolidation
SIG_C_CONSOLIDATION_CANDLES = 5   # 15+ minutes (5 candles)
SIG_C_BREAKOUT_PTS = 10      # breakout beyond range

MIN_PREMIUM = 20             # Don't enter if premium too low
MAX_PREMIUM = 500            # Don't enter if premium too high


# ── Data Classes ─────────────────────────────────────────────────────
@dataclass
class Candle:
    timestamp: datetime
    spot_price: float
    vix: float
    futures_basis: float
    atm_strike: int
    # OI data by strike: {strike: {ce_oi, pe_oi, ce_oi_change, pe_oi_change, ce_ltp, pe_ltp, ce_volume, pe_volume}}
    strikes: Dict[int, dict] = field(default_factory=dict)


@dataclass
class Trade:
    entry_time: datetime
    exit_time: Optional[datetime]
    signal_type: str          # 'A', 'B', 'C'
    direction: str            # 'CE' or 'PE'
    strike: int
    entry_premium: float
    exit_premium: float
    entry_spot: float
    exit_spot: float
    pnl_pts: float            # premium change in points
    pnl_rs: float             # after brokerage
    exit_reason: str          # 'TARGET', 'SL', 'TRAIL_SL', 'TIME', 'EOD', 'FORCE'
    date: str                 # YYYY-MM-DD


@dataclass
class OpenPosition:
    entry_time: datetime
    signal_type: str
    direction: str            # 'CE' or 'PE'
    strike: int
    entry_premium: float
    entry_spot: float
    highest_premium: float    # for trailing stop
    trailing_active: bool = False


# ── Database Loading ─────────────────────────────────────────────────
def load_all_data() -> Dict[str, List[Candle]]:
    """Load all data from DB, grouped by trading day."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Load analysis_history for spot, vix, basis, atm
    print("Loading analysis_history...")
    cur = conn.execute("""
        SELECT timestamp, spot_price, vix, futures_basis, atm_strike
        FROM analysis_history
        ORDER BY timestamp
    """)
    analysis_rows = cur.fetchall()
    print(f"  {len(analysis_rows)} analysis records loaded")

    # Build a map of timestamp -> analysis data
    analysis_map = {}
    for row in analysis_rows:
        ts = row['timestamp']
        analysis_map[ts] = {
            'spot_price': row['spot_price'],
            'vix': row['vix'],
            'futures_basis': row['futures_basis'],
            'atm_strike': row['atm_strike'],
        }

    # Load oi_snapshots
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

    # Group snapshots by timestamp
    snapshots_by_ts: Dict[str, list] = defaultdict(list)
    for row in snapshot_rows:
        snapshots_by_ts[row['timestamp']].append(row)

    # Build candles grouped by day
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

        # Attach strike-level data
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

    # Sort each day by timestamp
    for day_candles in days.values():
        day_candles.sort(key=lambda c: c.timestamp)

    print(f"  {len(days)} trading days loaded")
    return days


# ── Strike Selection ─────────────────────────────────────────────────
def get_atm_strike(spot_price: float) -> int:
    """Round to nearest 50."""
    return int(round(spot_price / NIFTY_GAP) * NIFTY_GAP)


def get_trade_strike(spot_price: float, direction: str) -> int:
    """
    CE: 2 strikes below ATM (slightly ITM) = ATM - 100
    PE: 2 strikes above ATM (slightly ITM) = ATM + 100
    """
    atm = get_atm_strike(spot_price)
    if direction == 'CE':
        return atm - ITM_DEPTH * NIFTY_GAP  # e.g., 22650 -> 22550 CE
    else:
        return atm + ITM_DEPTH * NIFTY_GAP  # e.g., 22650 -> 22750 PE


def get_premium(candle: Candle, strike: int, direction: str) -> Optional[float]:
    """Get option premium from candle's strike data."""
    if strike not in candle.strikes:
        return None
    data = candle.strikes[strike]
    if direction == 'CE':
        ltp = data['ce_ltp']
    else:
        ltp = data['pe_ltp']
    if ltp is None or ltp <= 0:
        return None
    return ltp


def get_volume(candle: Candle, strike: int, direction: str) -> int:
    """Get volume for a specific strike and direction."""
    if strike not in candle.strikes:
        return 0
    data = candle.strikes[strike]
    if direction == 'CE':
        return data.get('ce_volume', 0) or 0
    else:
        return data.get('pe_volume', 0) or 0


def get_total_volume(candle: Candle, direction: str) -> int:
    """Get total volume across all strikes for CE or PE."""
    total = 0
    for strike_data in candle.strikes.values():
        if direction == 'CE':
            total += strike_data.get('ce_volume', 0) or 0
        else:
            total += strike_data.get('pe_volume', 0) or 0
    return total


def get_oi_buildup(candle: Candle, direction: str, around_atm: int, depth: int = 3) -> int:
    """Get OI change buildup around ATM for a direction."""
    total = 0
    for offset in range(-depth, depth + 1):
        strike = around_atm + offset * NIFTY_GAP
        if strike in candle.strikes:
            if direction == 'CE':
                total += candle.strikes[strike].get('ce_oi_change', 0) or 0
            else:
                total += candle.strikes[strike].get('pe_oi_change', 0) or 0
    return total


# ── Signal Detection ─────────────────────────────────────────────────
def detect_signal_a(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal A — Momentum Breakout:
    - Spot moved 20+ pts in last 2 candles in one direction
    - Spot moved 25+ pts in last 3 candles in same direction
    - Volume above average for that side
    Returns: 'CE' (up) or 'PE' (down) or None
    """
    if idx < 2:
        return None

    current = candles[idx]
    prev1 = candles[idx - 1]
    prev2 = candles[idx - 2]

    move_2c = current.spot_price - prev2.spot_price
    abs_move_2c = abs(move_2c)

    if abs_move_2c < SIG_A_MOVE_2_CANDLES:
        return None

    direction = 'CE' if move_2c > 0 else 'PE'

    # Check 3-candle move if we have data
    if idx >= 3:
        prev3 = candles[idx - 3]
        move_3c = current.spot_price - prev3.spot_price

        # Must be same direction and 25+ pts
        if direction == 'CE' and move_3c < SIG_A_MOVE_3_CANDLES:
            return None
        if direction == 'PE' and move_3c > -SIG_A_MOVE_3_CANDLES:
            return None
    else:
        # Only 2 candles available — need stronger 2-candle move
        if abs_move_2c < SIG_A_MOVE_3_CANDLES:
            return None

    # Volume check: relevant side should have above-avg volume
    # Compare current candle volume to average of last 10 candles
    vol_lookback = min(idx, 10)
    if vol_lookback < 3:
        return direction  # Not enough data for volume check, pass

    vol_side = 'CE' if direction == 'CE' else 'PE'
    recent_vols = []
    for j in range(idx - vol_lookback, idx):
        v = get_total_volume(candles[j], vol_side)
        if v > 0:
            recent_vols.append(v)

    if recent_vols:
        avg_vol = sum(recent_vols) / len(recent_vols)
        curr_vol = get_total_volume(current, vol_side)
        if avg_vol > 0 and curr_vol < avg_vol * 0.7:
            return None  # Volume too low

    return direction


def detect_signal_b(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal B — Reversal Entry:
    - Spot was trending in one direction (moved 20+ pts in 5 candles)
    - Then reverses: last 2 candles show 10+ point reversal
    - OI confirms trapped traders on losing side
    Returns: 'CE' (reversal up) or 'PE' (reversal down) or None
    """
    if idx < 5:
        return None

    current = candles[idx]
    prev2 = candles[idx - 2]
    prev5 = candles[idx - 5]

    # Prior trend over candles [idx-5] to [idx-2]
    prior_move = prev2.spot_price - prev5.spot_price

    if abs(prior_move) < SIG_B_TREND_PTS:
        return None

    prior_direction = 'UP' if prior_move > 0 else 'DOWN'

    # Reversal in last 2 candles
    reversal_move = current.spot_price - prev2.spot_price

    if prior_direction == 'UP':
        # Was going up, now reversing down
        if reversal_move > -SIG_B_REVERSAL_PTS:
            return None
        direction = 'PE'
    else:
        # Was going down, now reversing up
        if reversal_move < SIG_B_REVERSAL_PTS:
            return None
        direction = 'CE'

    # OI confirmation: trapped traders on the losing side
    atm = get_atm_strike(current.spot_price)
    if direction == 'CE':
        # Reversing up: check PE OI buildup (shorts getting trapped)
        pe_oi_buildup = get_oi_buildup(current, 'PE', atm)
        if pe_oi_buildup < 0:
            return None  # No trapped shorts
    else:
        # Reversing down: check CE OI buildup (longs getting trapped)
        ce_oi_buildup = get_oi_buildup(current, 'CE', atm)
        if ce_oi_buildup < 0:
            return None  # No trapped longs

    # Premium compression check: the entry side premium should have compressed
    # (i.e., it was cheap relative to recent candles)
    strike = get_trade_strike(current.spot_price, direction)
    curr_prem = get_premium(current, strike, direction)
    if curr_prem is None:
        return None

    # Check that premium dipped (or is at least not at highs)
    recent_prems = []
    for j in range(max(0, idx - 5), idx):
        s = get_trade_strike(candles[j].spot_price, direction)
        p = get_premium(candles[j], s, direction)
        if p is not None:
            recent_prems.append(p)

    if recent_prems and curr_prem > max(recent_prems) * 1.05:
        return None  # Premium is at highs, not compressed

    return direction


def detect_signal_c(candles: List[Candle], idx: int) -> Optional[str]:
    """
    Signal C — Breakout from Compression:
    - Spot in a 20-point range for last 5+ candles (15 min)
    - Breakout: spot moves beyond range by 10+ points
    - OI shows buildup on the broken side (trapped traders fuel move)
    Returns: 'CE' (upside breakout) or 'PE' (downside breakout) or None
    """
    if idx < SIG_C_CONSOLIDATION_CANDLES + 1:
        return None

    current = candles[idx]

    # Find range over the consolidation period (candles [idx-C-1] to [idx-1])
    lookback_start = idx - SIG_C_CONSOLIDATION_CANDLES - 1
    lookback_end = idx - 1

    highs = [candles[j].spot_price for j in range(lookback_start, lookback_end + 1)]
    range_high = max(highs)
    range_low = min(highs)
    range_width = range_high - range_low

    if range_width > SIG_C_RANGE_PTS:
        return None  # Not in consolidation

    # Breakout detection
    if current.spot_price > range_high + SIG_C_BREAKOUT_PTS:
        direction = 'CE'
    elif current.spot_price < range_low - SIG_C_BREAKOUT_PTS:
        direction = 'PE'
    else:
        return None

    # OI confirmation: buildup on the broken side (trapped traders)
    atm = get_atm_strike(current.spot_price)
    if direction == 'CE':
        # Upside breakout: PE writers are trapped → PE OI buildup
        pe_oi = get_oi_buildup(current, 'PE', atm)
        if pe_oi < 0:
            return None
    else:
        # Downside breakout: CE writers are trapped → CE OI buildup
        ce_oi = get_oi_buildup(current, 'CE', atm)
        if ce_oi < 0:
            return None

    return direction


# ── Position Management ──────────────────────────────────────────────
def check_exit(position: OpenPosition, candle: Candle) -> Optional[Tuple[float, str]]:
    """
    Check if position should be exited.
    Returns (exit_premium, reason) or None.
    """
    premium = get_premium(candle, position.strike, position.direction)
    if premium is None:
        return None  # Can't exit without price

    pct_change = (premium - position.entry_premium) / position.entry_premium

    # Update highest premium for trailing
    if premium > position.highest_premium:
        position.highest_premium = premium

    # Check target
    if pct_change >= TARGET_PCT:
        return (premium, 'TARGET')

    # Check stop loss
    if pct_change <= STOP_LOSS_PCT:
        return (premium, 'SL')

    # Check trailing stop
    if not position.trailing_active and pct_change >= TRAIL_TRIGGER_PCT:
        position.trailing_active = True

    if position.trailing_active:
        trail_level = position.entry_premium * (1 + TRAIL_LOCK_PCT)
        if premium <= trail_level:
            return (premium, 'TRAIL_SL')

    # Check time-based exit (30 minutes)
    elapsed = (candle.timestamp - position.entry_time).total_seconds() / 60
    if elapsed >= TIME_EXIT_MINUTES:
        return (premium, 'TIME')

    # Check end-of-day
    if candle.timestamp.time() >= FORCE_EXIT_TIME:
        return (premium, 'EOD')

    return None


# ── Main Backtest Engine ─────────────────────────────────────────────
def run_backtest():
    print("=" * 80)
    print("  RALLY CATCHER BACKTEST")
    print("  3 Signal Types | 32 Trading Days | 3-Min Candles")
    print("=" * 80)
    print()

    days = load_all_data()
    all_trades: List[Trade] = []
    skipped_days = []

    sorted_dates = sorted(days.keys())
    print(f"\nProcessing {len(sorted_dates)} trading days: {sorted_dates[0]} to {sorted_dates[-1]}")
    print("-" * 80)

    for date_str in sorted_dates:
        candles = days[date_str]

        if len(candles) < 10:
            skipped_days.append(date_str)
            continue

        position: Optional[OpenPosition] = None
        day_trades = 0
        last_exit_time: Optional[datetime] = None
        day_trade_list: List[Trade] = []

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
                        exit_prem = position.entry_premium * 0.95  # Assume 5% loss if no price
                        reason = 'EOD_NO_PRICE'

                    pnl_pts = exit_prem - position.entry_premium
                    pnl_rs = pnl_pts * LOT_SIZE - BROKERAGE_TOTAL

                    trade = Trade(
                        entry_time=position.entry_time,
                        exit_time=candle.timestamp,
                        signal_type=position.signal_type,
                        direction=position.direction,
                        strike=position.strike,
                        entry_premium=position.entry_premium,
                        exit_premium=exit_prem,
                        entry_spot=position.entry_spot,
                        exit_spot=candle.spot_price,
                        pnl_pts=pnl_pts,
                        pnl_rs=pnl_rs,
                        exit_reason=reason,
                        date=date_str,
                    )
                    day_trade_list.append(trade)
                    last_exit_time = candle.timestamp
                    position = None
                    continue

                # Normal exit check
                result = check_exit(position, candle)
                if result is not None:
                    exit_prem, reason = result
                    pnl_pts = exit_prem - position.entry_premium
                    pnl_rs = pnl_pts * LOT_SIZE - BROKERAGE_TOTAL

                    trade = Trade(
                        entry_time=position.entry_time,
                        exit_time=candle.timestamp,
                        signal_type=position.signal_type,
                        direction=position.direction,
                        strike=position.strike,
                        entry_premium=position.entry_premium,
                        exit_premium=exit_prem,
                        entry_spot=position.entry_spot,
                        exit_spot=candle.spot_price,
                        pnl_pts=pnl_pts,
                        pnl_rs=pnl_rs,
                        exit_reason=reason,
                        date=date_str,
                    )
                    day_trade_list.append(trade)
                    last_exit_time = candle.timestamp
                    position = None

            # ── Check entries ──
            if position is not None:
                continue  # Already in a trade

            if ct < ENTRY_START or ct > ENTRY_END:
                continue

            if day_trades >= MAX_TRADES_PER_DAY:
                continue

            # Cooldown check
            if last_exit_time is not None:
                elapsed = (candle.timestamp - last_exit_time).total_seconds() / 60
                if elapsed < COOLDOWN_MINUTES:
                    continue

            # Try signals in priority order: A, B, C
            signal_type = None
            direction = None

            dir_a = detect_signal_a(candles, idx)
            if dir_a:
                signal_type = 'A'
                direction = dir_a

            if signal_type is None:
                dir_b = detect_signal_b(candles, idx)
                if dir_b:
                    signal_type = 'B'
                    direction = dir_b

            if signal_type is None:
                dir_c = detect_signal_c(candles, idx)
                if dir_c:
                    signal_type = 'C'
                    direction = dir_c

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

        # End of day: force-close any open position
        if position is not None and candles:
            last_candle = candles[-1]
            prem = get_premium(last_candle, position.strike, position.direction)
            if prem is None:
                prem = position.entry_premium * 0.95

            pnl_pts = prem - position.entry_premium
            pnl_rs = pnl_pts * LOT_SIZE - BROKERAGE_TOTAL

            trade = Trade(
                entry_time=position.entry_time,
                exit_time=last_candle.timestamp,
                signal_type=position.signal_type,
                direction=position.direction,
                strike=position.strike,
                entry_premium=position.entry_premium,
                exit_premium=prem,
                entry_spot=position.entry_spot,
                exit_spot=last_candle.spot_price,
                pnl_pts=pnl_pts,
                pnl_rs=pnl_rs,
                exit_reason='FORCE_CLOSE',
                date=date_str,
            )
            day_trade_list.append(trade)

        all_trades.extend(day_trade_list)

        # Daily summary line
        day_pnl = sum(t.pnl_rs for t in day_trade_list)
        day_wins = sum(1 for t in day_trade_list if t.pnl_rs > 0)
        day_losses = sum(1 for t in day_trade_list if t.pnl_rs <= 0)
        print(f"  {date_str}  |  Trades: {len(day_trade_list):2d}  |  W: {day_wins}  L: {day_losses}  |  P&L: Rs {day_pnl:+,.0f}")

    if skipped_days:
        print(f"\n  Skipped {len(skipped_days)} days (insufficient data): {skipped_days}")

    print()
    print_report(all_trades, sorted_dates)


# ── Reporting ────────────────────────────────────────────────────────
def print_report(trades: List[Trade], all_dates: List[str]):
    print()
    print("=" * 80)
    print("  DETAILED BACKTEST REPORT")
    print("=" * 80)

    if not trades:
        print("\n  NO TRADES GENERATED. Check signal parameters.")
        return

    total = len(trades)
    wins = [t for t in trades if t.pnl_rs > 0]
    losses = [t for t in trades if t.pnl_rs <= 0]
    total_pnl = sum(t.pnl_rs for t in trades)
    avg_pnl = total_pnl / total

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

    # ── Summary Statistics ──
    print(f"""
{'─' * 80}
  SUMMARY STATISTICS
{'─' * 80}
  Total Trades:        {total}
  Wins:                {len(wins)} ({len(wins)/total*100:.1f}%)
  Losses:              {len(losses)} ({len(losses)/total*100:.1f}%)
  Win Rate:            {len(wins)/total*100:.1f}%

  Total P&L:           Rs {total_pnl:+,.0f}
  Average P&L/Trade:   Rs {avg_pnl:+,.0f}
  Profit Factor:       {profit_factor:.2f}

  Gross Wins:          Rs {gross_wins:+,.0f}
  Gross Losses:        Rs {-gross_losses:,.0f}

  Avg Winner:          Rs {avg_winner:+,.0f}
  Avg Loser:           Rs {avg_loser:+,.0f}
  Win/Loss Ratio:      {abs(avg_winner/avg_loser):.2f}x

  Best Trade:          Rs {best_trade.pnl_rs:+,.0f} ({best_trade.signal_type}-{best_trade.direction} on {best_trade.date} at {best_trade.entry_time.strftime('%H:%M')})
  Worst Trade:         Rs {worst_trade.pnl_rs:+,.0f} ({worst_trade.signal_type}-{worst_trade.direction} on {worst_trade.date} at {worst_trade.entry_time.strftime('%H:%M')})

  Max Drawdown:        Rs {max_dd:,.0f} ({max_dd_pct:.1f}%)
""")

    # ── Exit Reason Breakdown ──
    exit_reasons = defaultdict(list)
    for t in trades:
        exit_reasons[t.exit_reason].append(t)

    print(f"{'─' * 80}")
    print(f"  EXIT REASON BREAKDOWN")
    print(f"{'─' * 80}")
    print(f"  {'Reason':<15} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12}")
    print(f"  {'─'*15} {'─'*7} {'─'*6} {'─'*7} {'─'*10} {'─'*12}")
    for reason in sorted(exit_reasons.keys()):
        rtrades = exit_reasons[reason]
        rwins = sum(1 for t in rtrades if t.pnl_rs > 0)
        rwr = rwins / len(rtrades) * 100 if rtrades else 0
        ravg = sum(t.pnl_rs for t in rtrades) / len(rtrades) if rtrades else 0
        rtotal = sum(t.pnl_rs for t in rtrades)
        print(f"  {reason:<15} {len(rtrades):>7} {rwins:>6} {rwr:>6.1f}% {ravg:>+10,.0f} {rtotal:>+12,.0f}")

    # ── Signal Analysis ──
    print(f"\n{'─' * 80}")
    print(f"  SIGNAL ANALYSIS")
    print(f"{'─' * 80}")
    print(f"  {'Signal':<10} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12} {'Avg Winner':>11} {'Avg Loser':>11}")
    print(f"  {'─'*10} {'─'*7} {'─'*6} {'─'*7} {'─'*7} {'─'*10} {'─'*12} {'─'*11} {'─'*11}")

    for sig in ['A', 'B', 'C']:
        sig_trades = [t for t in trades if t.signal_type == sig]
        if not sig_trades:
            print(f"  Signal {sig:<5} {'0':>7} {'–':>6} {'–':>7} {'–':>7} {'–':>10} {'–':>12} {'–':>11} {'–':>11}")
            continue
        sw = [t for t in sig_trades if t.pnl_rs > 0]
        sl = [t for t in sig_trades if t.pnl_rs <= 0]
        swr = len(sw) / len(sig_trades) * 100
        savg = sum(t.pnl_rs for t in sig_trades) / len(sig_trades)
        stotal = sum(t.pnl_rs for t in sig_trades)
        sawg = sum(t.pnl_rs for t in sw) / len(sw) if sw else 0
        salg = sum(t.pnl_rs for t in sl) / len(sl) if sl else 0
        print(f"  Signal {sig:<5} {len(sig_trades):>7} {len(sw):>6} {len(sl):>7} {swr:>6.1f}% {savg:>+10,.0f} {stotal:>+12,.0f} {sawg:>+11,.0f} {salg:>+11,.0f}")

    # Total row
    print(f"  {'─'*10} {'─'*7} {'─'*6} {'─'*7} {'─'*7} {'─'*10} {'─'*12} {'─'*11} {'─'*11}")
    swr_total = len(wins)/total*100
    print(f"  {'TOTAL':<10} {total:>7} {len(wins):>6} {len(losses):>7} {swr_total:>6.1f}% {avg_pnl:>+10,.0f} {total_pnl:>+12,.0f} {avg_winner:>+11,.0f} {avg_loser:>+11,.0f}")

    # ── Direction Analysis ──
    print(f"\n{'─' * 80}")
    print(f"  DIRECTION ANALYSIS (CE vs PE)")
    print(f"{'─' * 80}")
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
    print(f"\n{'─' * 80}")
    print(f"  DAILY BREAKDOWN")
    print(f"{'─' * 80}")
    print(f"  {'Date':<12} {'Trades':>7} {'Wins':>5} {'Losses':>7} {'WR%':>7} {'P&L (Rs)':>12} {'Cumulative':>12}")
    print(f"  {'─'*12} {'─'*7} {'─'*5} {'─'*7} {'─'*7} {'─'*12} {'─'*12}")

    trades_by_day = defaultdict(list)
    for t in trades:
        trades_by_day[t.date].append(t)

    cumulative = 0
    daily_pnls = []
    max_consec_wins = 0
    max_consec_losses = 0
    curr_wins = 0
    curr_losses = 0
    largest_winning_day = ("", float('-inf'))
    largest_losing_day = ("", float('inf'))

    for date_str in sorted(all_dates):
        dt = trades_by_day.get(date_str, [])
        if not dt:
            continue
        dw = sum(1 for t in dt if t.pnl_rs > 0)
        dl = sum(1 for t in dt if t.pnl_rs <= 0)
        dpnl = sum(t.pnl_rs for t in dt)
        cumulative += dpnl
        daily_pnls.append(dpnl)
        dwr = dw / len(dt) * 100 if dt else 0

        if dpnl > largest_winning_day[1]:
            largest_winning_day = (date_str, dpnl)
        if dpnl < largest_losing_day[1]:
            largest_losing_day = (date_str, dpnl)

        # Consecutive tracking (by day)
        if dpnl > 0:
            curr_wins += 1
            curr_losses = 0
            max_consec_wins = max(max_consec_wins, curr_wins)
        else:
            curr_losses += 1
            curr_wins = 0
            max_consec_losses = max(max_consec_losses, curr_losses)

        print(f"  {date_str:<12} {len(dt):>7} {dw:>5} {dl:>7} {dwr:>6.1f}% {dpnl:>+12,.0f} {cumulative:>+12,.0f}")

    # ── Monthly Summary ──
    print(f"\n{'─' * 80}")
    print(f"  MONTHLY SUMMARY")
    print(f"{'─' * 80}")
    print(f"  {'Month':<10} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'P&L (Rs)':>12} {'Avg/Trade':>12}")
    print(f"  {'─'*10} {'─'*7} {'─'*6} {'─'*7} {'─'*12} {'─'*12}")

    trades_by_month = defaultdict(list)
    for t in trades:
        month = t.date[:7]
        trades_by_month[month].append(t)

    for month in sorted(trades_by_month.keys()):
        mt = trades_by_month[month]
        mw = sum(1 for t in mt if t.pnl_rs > 0)
        mwr = mw / len(mt) * 100
        mpnl = sum(t.pnl_rs for t in mt)
        mavg = mpnl / len(mt)
        print(f"  {month:<10} {len(mt):>7} {mw:>6} {mwr:>6.1f}% {mpnl:>+12,.0f} {mavg:>+12,.0f}")

    # ── Time Analysis (by hour) ──
    print(f"\n{'─' * 80}")
    print(f"  TIME ANALYSIS (by hour of entry)")
    print(f"{'─' * 80}")
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
    trading_days = len([d for d in all_dates if d in trades_by_day])
    avg_trades_per_day = total / trading_days if trading_days > 0 else 0
    daily_pnl_std = (sum((p - (sum(daily_pnls)/len(daily_pnls)))**2 for p in daily_pnls) / len(daily_pnls)) ** 0.5 if daily_pnls else 0
    avg_daily_pnl = sum(daily_pnls) / len(daily_pnls) if daily_pnls else 0
    sharpe_like = avg_daily_pnl / daily_pnl_std if daily_pnl_std > 0 else 0

    # Consecutive wins/losses by trade
    max_consec_trade_wins = 0
    max_consec_trade_losses = 0
    cw = 0
    cl = 0
    for t in sorted(trades, key=lambda x: x.entry_time):
        if t.pnl_rs > 0:
            cw += 1
            cl = 0
            max_consec_trade_wins = max(max_consec_trade_wins, cw)
        else:
            cl += 1
            cw = 0
            max_consec_trade_losses = max(max_consec_trade_losses, cl)

    # Average hold time
    hold_times = []
    for t in trades:
        if t.exit_time:
            ht_min = (t.exit_time - t.entry_time).total_seconds() / 60
            hold_times.append(ht_min)
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0
    max_hold = max(hold_times) if hold_times else 0
    min_hold = min(hold_times) if hold_times else 0

    print(f"""
{'─' * 80}
  RISK METRICS
{'─' * 80}
  Max Drawdown:              Rs {max_dd:,.0f} ({max_dd_pct:.1f}%)
  Avg Trades/Day:            {avg_trades_per_day:.1f}
  Trading Days:              {trading_days}
  Days with Trades:          {len(trades_by_day)}

  Largest Winning Day:       {largest_winning_day[0]}  Rs {largest_winning_day[1]:+,.0f}
  Largest Losing Day:        {largest_losing_day[0]}  Rs {largest_losing_day[1]:+,.0f}

  Max Consec Winning Days:   {max_consec_wins}
  Max Consec Losing Days:    {max_consec_losses}
  Max Consec Winning Trades: {max_consec_trade_wins}
  Max Consec Losing Trades:  {max_consec_trade_losses}

  Avg Daily P&L:             Rs {avg_daily_pnl:+,.0f}
  Std Dev Daily P&L:         Rs {daily_pnl_std:,.0f}
  Sharpe-like Ratio:         {sharpe_like:.2f}  (avg daily / std daily)

  Avg Hold Time:             {avg_hold:.1f} min
  Min Hold Time:             {min_hold:.1f} min
  Max Hold Time:             {max_hold:.1f} min
""")

    # ── Premium Analysis ──
    avg_entry_prem = sum(t.entry_premium for t in trades) / total
    avg_exit_prem = sum(t.exit_premium for t in trades) / total
    avg_prem_move = sum(t.pnl_pts for t in trades) / total

    print(f"{'─' * 80}")
    print(f"  PREMIUM ANALYSIS")
    print(f"{'─' * 80}")
    print(f"  Avg Entry Premium:       Rs {avg_entry_prem:.1f}")
    print(f"  Avg Exit Premium:        Rs {avg_exit_prem:.1f}")
    print(f"  Avg Premium Move:        Rs {avg_prem_move:+.1f}")
    print(f"  Avg Premium Move (win):  Rs {sum(t.pnl_pts for t in wins)/len(wins):+.1f}" if wins else "")
    print(f"  Avg Premium Move (loss): Rs {sum(t.pnl_pts for t in losses)/len(losses):+.1f}" if losses else "")

    # Brokerage impact
    total_brokerage = total * BROKERAGE_TOTAL
    pnl_before_brokerage = sum(t.pnl_pts * LOT_SIZE for t in trades)
    print(f"\n  P&L Before Brokerage:    Rs {pnl_before_brokerage:+,.0f}")
    print(f"  Total Brokerage:         Rs {total_brokerage:,.0f} ({total} trades x Rs {BROKERAGE_TOTAL})")
    print(f"  P&L After Brokerage:     Rs {total_pnl:+,.0f}")
    print(f"  Brokerage as % of Gross: {total_brokerage/pnl_before_brokerage*100:.1f}%" if pnl_before_brokerage > 0 else "")

    # ── Signal-wise Detail: A vs B vs C ──
    print(f"\n{'─' * 80}")
    print(f"  SIGNAL-WISE EXIT REASONS")
    print(f"{'─' * 80}")

    for sig in ['A', 'B', 'C']:
        sig_trades = [t for t in trades if t.signal_type == sig]
        if not sig_trades:
            continue
        print(f"\n  Signal {sig} ({len(sig_trades)} trades):")
        sig_exits = defaultdict(list)
        for t in sig_trades:
            sig_exits[t.exit_reason].append(t)
        print(f"    {'Reason':<15} {'Count':>6} {'WR%':>7} {'Avg P&L':>10}")
        print(f"    {'─'*15} {'─'*6} {'─'*7} {'─'*10}")
        for reason in sorted(sig_exits.keys()):
            rt = sig_exits[reason]
            rw = sum(1 for t in rt if t.pnl_rs > 0)
            rwr = rw / len(rt) * 100
            ravg = sum(t.pnl_rs for t in rt) / len(rt)
            print(f"    {reason:<15} {len(rt):>6} {rwr:>6.1f}% {ravg:>+10,.0f}")

    # ── Top 10 Best and Worst Trades ──
    sorted_by_pnl = sorted(trades, key=lambda t: t.pnl_rs, reverse=True)

    print(f"\n{'─' * 80}")
    print(f"  TOP 10 BEST TRADES")
    print(f"{'─' * 80}")
    print(f"  {'#':>3} {'Date':<12} {'Time':<6} {'Sig':>3} {'Dir':>3} {'Strike':>7} {'Entry':>7} {'Exit':>7} {'P&L':>10} {'Reason':<10}")
    print(f"  {'─'*3} {'─'*12} {'─'*6} {'─'*3} {'─'*3} {'─'*7} {'─'*7} {'─'*7} {'─'*10} {'─'*10}")
    for i, t in enumerate(sorted_by_pnl[:10], 1):
        print(f"  {i:>3} {t.date:<12} {t.entry_time.strftime('%H:%M'):<6} {t.signal_type:>3} {t.direction:>3} {t.strike:>7} {t.entry_premium:>7.1f} {t.exit_premium:>7.1f} {t.pnl_rs:>+10,.0f} {t.exit_reason:<10}")

    print(f"\n{'─' * 80}")
    print(f"  TOP 10 WORST TRADES")
    print(f"{'─' * 80}")
    print(f"  {'#':>3} {'Date':<12} {'Time':<6} {'Sig':>3} {'Dir':>3} {'Strike':>7} {'Entry':>7} {'Exit':>7} {'P&L':>10} {'Reason':<10}")
    print(f"  {'─'*3} {'─'*12} {'─'*6} {'─'*3} {'─'*3} {'─'*7} {'─'*7} {'─'*7} {'─'*10} {'─'*10}")
    for i, t in enumerate(reversed(sorted_by_pnl[-10:]), 1):
        print(f"  {i:>3} {t.date:<12} {t.entry_time.strftime('%H:%M'):<6} {t.signal_type:>3} {t.direction:>3} {t.strike:>7} {t.entry_premium:>7.1f} {t.exit_premium:>7.1f} {t.pnl_rs:>+10,.0f} {t.exit_reason:<10}")

    # ── Full Trade Log ──
    print(f"\n{'─' * 80}")
    print(f"  COMPLETE TRADE LOG ({total} trades)")
    print(f"{'─' * 80}")
    print(f"  {'#':>3} {'Date':<12} {'Entry':>6} {'Exit':>6} {'Sig':>3} {'Dir':>3} {'Strike':>7} {'EntPrem':>8} {'ExPrem':>8} {'P&L pts':>9} {'P&L Rs':>10} {'Reason':<12}")
    print(f"  {'─'*3} {'─'*12} {'─'*6} {'─'*6} {'─'*3} {'─'*3} {'─'*7} {'─'*8} {'─'*8} {'─'*9} {'─'*10} {'─'*12}")
    for i, t in enumerate(sorted(trades, key=lambda x: x.entry_time), 1):
        exit_t = t.exit_time.strftime('%H:%M') if t.exit_time else '  -- '
        print(f"  {i:>3} {t.date:<12} {t.entry_time.strftime('%H:%M'):>6} {exit_t:>6} {t.signal_type:>3} {t.direction:>3} {t.strike:>7} {t.entry_premium:>8.1f} {t.exit_premium:>8.1f} {t.pnl_pts:>+9.1f} {t.pnl_rs:>+10,.0f} {t.exit_reason:<12}")

    # ── Final Verdict ──
    print(f"\n{'=' * 80}")
    print(f"  FINAL VERDICT")
    print(f"{'=' * 80}")
    if total_pnl > 0:
        print(f"  PROFITABLE: Rs {total_pnl:+,.0f} over {trading_days} trading days")
        print(f"  Avg Rs {avg_daily_pnl:+,.0f}/day = ~Rs {avg_daily_pnl * 22:+,.0f}/month (est. 22 trading days)")
    else:
        print(f"  UNPROFITABLE: Rs {total_pnl:+,.0f} over {trading_days} trading days")
        print(f"  Avg Rs {avg_daily_pnl:+,.0f}/day")

    print(f"  Win Rate: {len(wins)/total*100:.1f}% | Profit Factor: {profit_factor:.2f} | Sharpe: {sharpe_like:.2f}")

    if profit_factor >= 1.5 and len(wins)/total >= 0.45:
        print(f"\n  >> Strategy looks VIABLE for live trading")
    elif profit_factor >= 1.0:
        print(f"\n  >> Strategy is MARGINAL — needs refinement")
    else:
        print(f"\n  >> Strategy is LOSING — do NOT deploy")

    print(f"{'=' * 80}")


if __name__ == '__main__':
    run_backtest()
