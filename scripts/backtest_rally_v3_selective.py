"""
Backtest: Rally Catcher V3 — Selective
=======================================
Philosophy: FEWER trades, HIGHER quality, MAXIMUM selectivity.

Key changes from V1/V2/Final:
  - 2 entry signals: Confirmed Momentum Burst + Compression Breakout (stricter)
  - Time windows: 09:24-10:00 AND 11:45-13:00 only (skip toxic 10:00-11:45)
  - Max 2 trades/day (HARD LIMIT — biggest edge preserver)
  - Wider target (+20%), 2-stage trailing, momentum exit, extended time exit
  - Volume + premium confirmation on ALL signals
  - Anti-revenge: after SL, next signal needs all confirmations

4 Variations tested:
  A: Morning Only (09:24-10:00, max 1/day)
  B: 2-Window (09:24-10:00 + 11:45-13:00, max 2/day) — base
  C: PE-Only (same as B but only PE trades)
  D: High Conviction Only (triple confirmation required)

Data: oi_tracker.db, 3-min candles, 32 trading days (2026-01-30 to 2026-03-16)

Usage:
    cd D:/Projects/oi_tracker && uv run python scripts/backtest_rally_v3_selective.py
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

# Entry timing — 2 windows
WINDOW_1_START = time(9, 24)
WINDOW_1_END = time(10, 0)
WINDOW_2_START = time(11, 45)
WINDOW_2_END = time(13, 0)
FORCE_EXIT_TIME = time(15, 15)

# Position management
MAX_TRADES_PER_DAY = 2   # HARD LIMIT
COOLDOWN_MINUTES = 12
MAX_OPEN_POSITIONS = 1

# Exit parameters
TARGET_PCT = 0.20       # +20% premium (wider — let winners run)
STOP_LOSS_PCT = -0.10   # -10% premium

# Trailing stop — 2 stages
TRAIL_1_TRIGGER = 0.10  # after +10%, move SL to +4%
TRAIL_1_LOCK = 0.04
TRAIL_2_TRIGGER = 0.15  # after +15%, move SL to +10%
TRAIL_2_LOCK = 0.10

# Momentum exit: premium drops 5% from peak in single candle
MOMENTUM_DROP_PCT = 0.05
MOMENTUM_WINDOW_CANDLES = 1  # 1 candle = 3 min

# Time exits
TIME_EXIT_MINUTES = 30       # dead trade exit if flat
TIME_EXIT_DEAD_ZONE = (-0.03, 0.03)  # only time-exit if P&L between -3% and +3%
EXTENDED_TIME_EXIT = 45      # absolute exit after 45 min

# Signal MB — Confirmed Momentum Burst
SIG_MB_SPOT_MOVE = 20        # 20+ pts in 6 min (2 candles)
SIG_MB_PREMIUM_MOVE = 0.03   # +3% premium in 6 min
SIG_MB_OI_LOOKBACK = 3       # candles for OI trap confirmation

# Signal CB — Compression Breakout (stricter)
SIG_CB_RANGE_PTS = 20        # max range (was 25 — stricter)
SIG_CB_CONSOLIDATION_CANDLES = 7  # 21 min (7 candles)
SIG_CB_BREAKOUT_PTS = 15     # 15+ pts beyond range (was 12 — stricter)

# Premium / volume filters
MIN_PREMIUM = 100      # raised from 80
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
    signal_type: str          # 'MB' or 'CB'
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


@dataclass
class VariationConfig:
    name: str
    label: str
    windows: List[Tuple[time, time]]   # allowed entry windows
    max_trades_per_day: int
    pe_only: bool = False
    high_conviction: bool = False       # require ALL confirmations


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
        return atm - ITM_DEPTH * NIFTY_GAP  # 100 pts below ATM
    else:
        return atm + ITM_DEPTH * NIFTY_GAP  # 100 pts above ATM


def get_premium(candle: Candle, strike: int, direction: str) -> Optional[float]:
    if strike not in candle.strikes:
        return None
    data = candle.strikes[strike]
    ltp = data['ce_ltp'] if direction == 'CE' else data['pe_ltp']
    if ltp is None or ltp <= 0:
        return None
    return ltp


def get_volume(candle: Candle, strike: int, direction: str) -> int:
    """Get volume for a specific strike and direction."""
    if strike not in candle.strikes:
        return 0
    data = candle.strikes[strike]
    key = 'ce_volume' if direction == 'CE' else 'pe_volume'
    return data.get(key, 0) or 0


def get_oi_change_near_atm(candle: Candle, direction: str, around_atm: int, depth: int = 5) -> int:
    """Get OI change near ATM for a direction."""
    total = 0
    for offset in range(-depth, depth + 1):
        strike = around_atm + offset * NIFTY_GAP
        if strike in candle.strikes:
            key = 'ce_oi_change' if direction == 'CE' else 'pe_oi_change'
            total += candle.strikes[strike].get(key, 0) or 0
    return total


def get_total_oi_on_side(candle: Candle, direction: str, around_atm: int, depth: int = 5) -> int:
    """Get total OI (not change) near ATM for a direction — fuel for breakout."""
    total = 0
    for offset in range(-depth, depth + 1):
        strike = around_atm + offset * NIFTY_GAP
        if strike in candle.strikes:
            key = 'ce_oi' if direction == 'CE' else 'pe_oi'
            total += candle.strikes[strike].get(key, 0) or 0
    return total


def get_avg_volume_today(candles: List[Candle], idx: int, strike: int, direction: str) -> float:
    """Get average volume for this strike today up to current candle."""
    vols = []
    for j in range(max(0, idx - 30), idx):  # look back up to 30 candles (90 min)
        v = get_volume(candles[j], strike, direction)
        if v > 0:
            vols.append(v)
    return sum(vols) / len(vols) if vols else 0


# ── Signal Detection ─────────────────────────────────────────────────
def detect_signal_mb(candles: List[Candle], idx: int) -> Optional[Tuple[str, dict]]:
    """
    Signal MB — Confirmed Momentum Burst:
    1. Spot moved 20+ pts in last 6 min (2 candles)
    2. OI confirmation: trapped traders on OPPOSITE side
       (e.g., bullish: PE OI increased OR CE OI decreased)
    3. Premium of the option we'd buy has risen at least 3% in last 6 min
    4. Volume in last candle is above average for that strike today
    5. Min premium >= Rs 100

    Returns (direction, details_dict) or None.
    """
    if idx < 3:
        return None

    current = candles[idx]
    prev1 = candles[idx - 1]
    prev2 = candles[idx - 2]

    # 1. Spot move: 20+ pts in 2 candles (6 min)
    spot_move = current.spot_price - prev2.spot_price
    if abs(spot_move) < SIG_MB_SPOT_MOVE:
        return None

    direction = 'CE' if spot_move > 0 else 'PE'

    # Get trade strike and premium
    strike = get_trade_strike(current.spot_price, direction)
    curr_prem = get_premium(current, strike, direction)
    if curr_prem is None or curr_prem < MIN_PREMIUM or curr_prem > MAX_PREMIUM:
        return None

    # 3. Premium confirmation: +3% in last 6 min
    prev_prem = get_premium(prev2, strike, direction)
    if prev_prem is None or prev_prem <= 0:
        # Try strike from prev2's ATM perspective
        alt_strike = get_trade_strike(prev2.spot_price, direction)
        prev_prem = get_premium(prev2, alt_strike, direction)
        if prev_prem is None or prev_prem <= 0:
            return None

    prem_move = (curr_prem - prev_prem) / prev_prem
    if prem_move < SIG_MB_PREMIUM_MOVE:
        return None

    # 2. OI confirmation: trapped traders on opposite side
    atm = get_atm_strike(current.spot_price)
    oi_confirmed = False
    oi_detail = ""

    if direction == 'CE':
        # Bullish: PE OI should have increased (PE writers trapped)
        # OR CE OI decreased (shorts covering)
        pe_oi_change = 0
        ce_oi_change = 0
        for j in range(max(0, idx - SIG_MB_OI_LOOKBACK), idx + 1):
            c = candles[j]
            c_atm = get_atm_strike(c.spot_price)
            pe_oi_change += get_oi_change_near_atm(c, 'PE', c_atm, depth=3)
            ce_oi_change += get_oi_change_near_atm(c, 'CE', c_atm, depth=3)
        if pe_oi_change > 0:
            oi_confirmed = True
            oi_detail = f"PE_OI+{pe_oi_change:,}"
        elif ce_oi_change < 0:
            oi_confirmed = True
            oi_detail = f"CE_OI{ce_oi_change:,}"
    else:
        # Bearish: CE OI should have increased (CE writers trapped)
        # OR PE OI decreased (shorts covering)
        ce_oi_change = 0
        pe_oi_change = 0
        for j in range(max(0, idx - SIG_MB_OI_LOOKBACK), idx + 1):
            c = candles[j]
            c_atm = get_atm_strike(c.spot_price)
            ce_oi_change += get_oi_change_near_atm(c, 'CE', c_atm, depth=3)
            pe_oi_change += get_oi_change_near_atm(c, 'PE', c_atm, depth=3)
        if ce_oi_change > 0:
            oi_confirmed = True
            oi_detail = f"CE_OI+{ce_oi_change:,}"
        elif pe_oi_change < 0:
            oi_confirmed = True
            oi_detail = f"PE_OI{pe_oi_change:,}"

    if not oi_confirmed:
        return None

    # 4. Volume: last candle volume above average for this strike today
    curr_vol = get_volume(current, strike, direction)
    avg_vol = get_avg_volume_today(candles, idx, strike, direction)
    vol_confirmed = curr_vol > avg_vol if avg_vol > 0 else curr_vol > 0

    if not vol_confirmed:
        return None

    details = {
        'spot_move': spot_move,
        'prem_move_pct': prem_move * 100,
        'oi_detail': oi_detail,
        'volume': curr_vol,
        'avg_volume': avg_vol,
    }

    return (direction, details)


def detect_signal_cb(candles: List[Candle], idx: int) -> Optional[Tuple[str, dict]]:
    """
    Signal CB — Compression Breakout (stricter):
    1. Range in last 21 min (7 candles) <= 20 pts
    2. Breakout: 15+ pts beyond range in one candle
    3. OI supports: total OI on the losing side has been building (fuel)
    4. Premium must be >= Rs 100
    5. Volume must confirm

    Returns (direction, details_dict) or None.
    """
    if idx < SIG_CB_CONSOLIDATION_CANDLES + 1:
        return None

    current = candles[idx]

    # 1. Consolidation range: candles [idx-8] to [idx-1] (7 candles)
    lookback_start = idx - SIG_CB_CONSOLIDATION_CANDLES - 1
    lookback_end = idx - 1

    prices = [candles[j].spot_price for j in range(lookback_start, lookback_end + 1)]
    range_high = max(prices)
    range_low = min(prices)
    range_width = range_high - range_low

    if range_width > SIG_CB_RANGE_PTS:
        return None  # Not in compression

    # 2. Breakout detection: 15+ pts beyond range
    if current.spot_price > range_high + SIG_CB_BREAKOUT_PTS:
        direction = 'CE'
    elif current.spot_price < range_low - SIG_CB_BREAKOUT_PTS:
        direction = 'PE'
    else:
        return None

    # Get trade strike and premium
    strike = get_trade_strike(current.spot_price, direction)
    curr_prem = get_premium(current, strike, direction)
    if curr_prem is None or curr_prem < MIN_PREMIUM or curr_prem > MAX_PREMIUM:
        return None

    # 3. OI: total OI on the LOSING side has been building (fuel for squeeze)
    atm = get_atm_strike(current.spot_price)
    if direction == 'CE':
        # Upside breakout: PE writers are the fuel (their OI = fuel for short squeeze)
        losing_side_oi = get_total_oi_on_side(current, 'PE', atm, depth=5)
        # Also check OI change — trapped writers should have ADDED OI during compression
        oi_change_sum = 0
        for j in range(lookback_start, lookback_end + 1):
            c = candles[j]
            c_atm = get_atm_strike(c.spot_price)
            oi_change_sum += get_oi_change_near_atm(c, 'PE', c_atm, depth=3)
    else:
        # Downside breakout: CE writers are the fuel
        losing_side_oi = get_total_oi_on_side(current, 'CE', atm, depth=5)
        oi_change_sum = 0
        for j in range(lookback_start, lookback_end + 1):
            c = candles[j]
            c_atm = get_atm_strike(c.spot_price)
            oi_change_sum += get_oi_change_near_atm(c, 'CE', c_atm, depth=3)

    if oi_change_sum <= 0:
        return None  # No OI buildup on losing side = no fuel

    # 5. Volume confirmation
    curr_vol = get_volume(current, strike, direction)
    avg_vol = get_avg_volume_today(candles, idx, strike, direction)
    vol_confirmed = curr_vol > avg_vol if avg_vol > 0 else curr_vol > 0

    if not vol_confirmed:
        return None

    details = {
        'range_width': range_width,
        'breakout_pts': abs(current.spot_price - (range_high if direction == 'CE' else range_low)),
        'losing_side_oi': losing_side_oi,
        'oi_buildup': oi_change_sum,
        'volume': curr_vol,
        'avg_volume': avg_vol,
    }

    return (direction, details)


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

    # Check target (+20%)
    if pct_change >= TARGET_PCT:
        return (premium, 'TARGET')

    # Check stop loss (-10%)
    if pct_change <= STOP_LOSS_PCT:
        return (premium, 'SL')

    # Trailing stop — stage 2 first (tighter)
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

    # Momentum exit: premium drops 5% from peak in a SINGLE 3-min candle
    if len(position.premium_history) >= 2:
        prev_prem = position.premium_history[-2]
        if prev_prem > 0:
            drop_from_prev = (prev_prem - premium) / prev_prem
            if drop_from_prev >= MOMENTUM_DROP_PCT and pct_change > 0:
                # Only momentum-exit if still in profit
                return (premium, 'MOMENTUM')

    elapsed = (candle.timestamp - position.entry_time).total_seconds() / 60

    # Time-based exit: after 30 min if P&L is flat (-3% to +3%)
    if elapsed >= TIME_EXIT_MINUTES:
        if TIME_EXIT_DEAD_ZONE[0] <= pct_change <= TIME_EXIT_DEAD_ZONE[1]:
            return (premium, 'TIME')

    # Extended time exit: after 45 min, exit regardless
    if elapsed >= EXTENDED_TIME_EXIT:
        return (premium, 'TIME_EXT')

    # EOD
    if candle.timestamp.time() >= FORCE_EXIT_TIME:
        return (premium, 'EOD')

    return None


def is_in_window(ct: time, windows: List[Tuple[time, time]]) -> bool:
    """Check if current time is within any of the allowed windows."""
    for w_start, w_end in windows:
        if w_start <= ct <= w_end:
            return True
    return False


# ── Backtest Engine ──────────────────────────────────────────────────
def run_backtest(
    days: Dict[str, List[Candle]],
    sorted_dates: List[str],
    config: VariationConfig,
) -> List[Trade]:
    """Run a single backtest with the given variation config."""
    all_trades: List[Trade] = []

    for date_str in sorted_dates:
        candles = days[date_str]
        if len(candles) < 10:
            continue

        position: Optional[OpenPosition] = None
        day_trades = 0
        last_exit_time: Optional[datetime] = None
        last_exit_was_sl = False  # track for anti-revenge logic

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
                    last_exit_was_sl = False
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
                    last_exit_was_sl = (reason == 'SL')
                    position = None

            # ── Check entries ──
            if position is not None:
                continue

            # Time window filter
            if not is_in_window(ct, config.windows):
                continue

            # Max trades per day
            if day_trades >= config.max_trades_per_day:
                continue

            # Cooldown
            if last_exit_time is not None:
                elapsed = (candle.timestamp - last_exit_time).total_seconds() / 60
                if elapsed < COOLDOWN_MINUTES:
                    continue

            # Try signals: MB first (momentum), then CB (compression)
            signal_type = None
            direction = None
            sig_details = None

            result_mb = detect_signal_mb(candles, idx)
            if result_mb:
                signal_type = 'MB'
                direction, sig_details = result_mb

            if signal_type is None:
                result_cb = detect_signal_cb(candles, idx)
                if result_cb:
                    signal_type = 'CB'
                    direction, sig_details = result_cb

            if signal_type is None:
                continue

            # PE-only filter
            if config.pe_only and direction != 'PE':
                continue

            # High conviction filter: require BOTH OI + premium + volume
            # (already required by default — high_conviction mode just re-checks
            #  that all sub-confirmations are strong)
            if config.high_conviction:
                if signal_type == 'MB':
                    # For MB, ensure OI, premium, AND volume are all strong
                    # Volume must be at least 1.5x average (not just above)
                    if sig_details and sig_details.get('avg_volume', 0) > 0:
                        if sig_details['volume'] < sig_details['avg_volume'] * 1.5:
                            continue
                    # Premium move must be at least 5% (not just 3%)
                    if sig_details and sig_details.get('prem_move_pct', 0) < 5.0:
                        continue
                elif signal_type == 'CB':
                    # For CB, volume must be 1.5x avg and OI buildup must be substantial
                    if sig_details and sig_details.get('avg_volume', 0) > 0:
                        if sig_details['volume'] < sig_details['avg_volume'] * 1.5:
                            continue

            # Anti-revenge: if last trade was SL, require signal to have ALL
            # confirmations at higher thresholds
            if last_exit_was_sl:
                if signal_type == 'MB':
                    # After SL, MB needs stronger spot move (25+ instead of 20)
                    if sig_details and abs(sig_details.get('spot_move', 0)) < 25:
                        continue
                    # And stronger premium move (5% instead of 3%)
                    if sig_details and sig_details.get('prem_move_pct', 0) < 5.0:
                        continue
                elif signal_type == 'CB':
                    # After SL, CB needs wider breakout (20+ instead of 15)
                    if sig_details and sig_details.get('breakout_pts', 0) < 20:
                        continue

            # Get strike and premium (may differ from signal check if ATM shifted)
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


# ── Summary Stats Calculator ─────────────────────────────────────────
def calc_stats(trades: List[Trade], all_dates: List[str]) -> dict:
    """Calculate summary statistics for a list of trades."""
    if not trades:
        return {'trades': 0}

    total = len(trades)
    wins = [t for t in trades if t.pnl_rs > 0]
    losses = [t for t in trades if t.pnl_rs <= 0]
    total_pnl = sum(t.pnl_rs for t in trades)
    gross_pnl = sum(t.pnl_pts * LOT_SIZE for t in trades)
    total_brokerage = total * BROKERAGE_TOTAL

    wr = len(wins) / total * 100
    avg_pnl = total_pnl / total

    gross_wins = sum(t.pnl_rs for t in wins)
    gross_losses = abs(sum(t.pnl_rs for t in losses))
    pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    avg_winner = sum(t.pnl_rs for t in wins) / len(wins) if wins else 0
    avg_loser = sum(t.pnl_rs for t in losses) / len(losses) if losses else 0

    # Max drawdown
    sorted_trades = sorted(trades, key=lambda t: t.entry_time)
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in sorted_trades:
        cumulative += t.pnl_rs
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Daily stats for Sharpe
    trades_by_day = defaultdict(list)
    for t in trades:
        trades_by_day[t.date].append(t)

    daily_pnls = []
    for date_str in sorted(all_dates):
        dt = trades_by_day.get(date_str, [])
        if dt:
            daily_pnls.append(sum(t.pnl_rs for t in dt))

    trading_days_count = len(trades_by_day)
    win_days = sum(1 for p in daily_pnls if p > 0)

    daily_mean = sum(daily_pnls) / len(daily_pnls) if daily_pnls else 0
    daily_std = (sum((p - daily_mean)**2 for p in daily_pnls) / len(daily_pnls)) ** 0.5 if daily_pnls else 0
    sharpe = daily_mean / daily_std if daily_std > 0 else 0

    avg_trades_per_day = total / trading_days_count if trading_days_count > 0 else 0

    # Monthly
    trades_by_month = defaultdict(list)
    for t in trades:
        trades_by_month[t.date[:7]].append(t)
    monthly_pnl = {}
    for month in sorted(trades_by_month.keys()):
        monthly_pnl[month] = sum(t.pnl_rs for t in trades_by_month[month])

    return {
        'trades': total,
        'wins': len(wins),
        'losses': len(losses),
        'wr': wr,
        'gross_pnl': gross_pnl,
        'brokerage': total_brokerage,
        'net_pnl': total_pnl,
        'pf': pf,
        'avg_winner': avg_winner,
        'avg_loser': avg_loser,
        'expectancy': avg_pnl,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'win_days': win_days,
        'trading_days': trading_days_count,
        'avg_trades_per_day': avg_trades_per_day,
        'monthly_pnl': monthly_pnl,
    }


# ── Variation Summary ────────────────────────────────────────────────
def print_variation_summary(label: str, name: str, stats: dict):
    """Print compact summary for a variation."""
    if stats['trades'] == 0:
        print(f"\n  VARIATION {label}: {name}")
        print(f"  - Trades: 0 | NO TRADES GENERATED")
        return

    monthly_str = " | ".join(
        f"{m[5:]} Rs {p:+,.0f}" for m, p in stats['monthly_pnl'].items()
    )

    print(f"\n  VARIATION {label}: {name}")
    print(f"  - Trades: {stats['trades']} | WR: {stats['wr']:.1f}% | Gross P&L: Rs {stats['gross_pnl']:+,.0f} | Brokerage: Rs {stats['brokerage']:,.0f} | Net P&L: Rs {stats['net_pnl']:+,.0f}")
    print(f"  - PF: {stats['pf']:.2f} | Avg Winner: Rs {stats['avg_winner']:+,.0f} | Avg Loser: Rs {stats['avg_loser']:+,.0f} | Expectancy: Rs {stats['expectancy']:+,.0f}/trade")
    print(f"  - Max DD: Rs {stats['max_dd']:,.0f} | Sharpe: {stats['sharpe']:.2f}")
    print(f"  - Win Days: {stats['win_days']}/{stats['trading_days']} | Avg Trades/Day: {stats['avg_trades_per_day']:.1f}")
    print(f"  - Monthly: {monthly_str}")


# ── Full Detailed Report ─────────────────────────────────────────────
def print_full_report(trades: List[Trade], all_dates: List[str], config: VariationConfig):
    """Print full detailed report for the best variation."""
    if not trades:
        print("\n  NO TRADES to report.")
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
    pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    avg_winner = sum(t.pnl_rs for t in wins) / len(wins) if wins else 0
    avg_loser = sum(t.pnl_rs for t in losses) / len(losses) if losses else 0

    best_trade = max(trades, key=lambda t: t.pnl_rs)
    worst_trade = min(trades, key=lambda t: t.pnl_rs)

    sorted_trades = sorted(trades, key=lambda t: t.entry_time)
    wr = len(wins) / total * 100

    # Max drawdown
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

    # Consecutive
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

    # Daily P&L
    trades_by_day = defaultdict(list)
    for t in trades:
        trades_by_day[t.date].append(t)

    daily_pnls = []
    for date_str in sorted(all_dates):
        dt = trades_by_day.get(date_str, [])
        if dt:
            daily_pnls.append(sum(t.pnl_rs for t in dt))

    trading_days_count = len(trades_by_day)
    win_days = sum(1 for p in daily_pnls if p > 0)
    loss_days = sum(1 for p in daily_pnls if p <= 0)

    daily_mean = sum(daily_pnls) / len(daily_pnls) if daily_pnls else 0
    daily_std = (sum((p - daily_mean)**2 for p in daily_pnls) / len(daily_pnls)) ** 0.5 if daily_pnls else 0
    sharpe = daily_mean / daily_std if daily_std > 0 else 0

    annualized = daily_mean * 252
    calmar = annualized / max_dd if max_dd > 0 else float('inf')

    wl_ratio = abs(avg_winner / avg_loser) if avg_loser != 0 else float('inf')

    # ── Executive Summary ──
    print(f"""
{'=' * 110}
{'=' * 110}
  DETAILED REPORT: VARIATION {config.label} -- {config.name}
{'=' * 110}
{'=' * 110}

  Period:        {sorted_trades[0].date} to {sorted_trades[-1].date} ({trading_days_count} trading days with trades)
  Windows:       {', '.join(f'{w[0].strftime("%H:%M")}-{w[1].strftime("%H:%M")}' for w in config.windows)}
  Max Trades/Day: {config.max_trades_per_day}{'  |  PE-Only' if config.pe_only else ''}{'  |  High Conviction' if config.high_conviction else ''}
  Parameters:    SL=-10%, Target=+20%, Trail1=+10%->+4%, Trail2=+15%->+10%, Time=30m, ExtTime=45m

{'=' * 110}
  TRADE STATISTICS
{'=' * 110}

  Total Trades:            {total}
  Wins:                    {len(wins)} ({wr:.1f}%)
  Losses:                  {len(losses)} ({100-wr:.1f}%)

  P&L Before Brokerage:   Rs {gross_pnl_before:>+12,.0f}
  Total Brokerage:         Rs {total_brokerage:>12,.0f}  ({total} x Rs {BROKERAGE_TOTAL})
  Net P&L After Brokerage: Rs {total_pnl:>+12,.0f}

  Profit Factor:           {pf:.2f}
  Expectancy/Trade:        Rs {avg_pnl:>+,.0f}
  Win/Loss Ratio:          {wl_ratio:.2f}x

  Average Winner:          Rs {avg_winner:>+,.0f}
  Average Loser:           Rs {avg_loser:>+,.0f}

  Max Consec Wins:         {max_ct_wins}
  Max Consec Losses:       {max_ct_losses}
  Avg Holding Time:        {avg_hold:.1f} min

  Best Trade:              Rs {best_trade.pnl_rs:>+,.0f}  ({best_trade.signal_type}-{best_trade.direction} {best_trade.date} {best_trade.entry_time.strftime('%H:%M')})
  Worst Trade:             Rs {worst_trade.pnl_rs:>+,.0f}  ({worst_trade.signal_type}-{worst_trade.direction} {worst_trade.date} {worst_trade.entry_time.strftime('%H:%M')})
""")

    # ── By Signal Type ──
    print(f"{'=' * 110}")
    print(f"  BY SIGNAL TYPE")
    print(f"  MB = Confirmed Momentum Burst  |  CB = Compression Breakout")
    print(f"{'=' * 110}")
    print(f"  {'Signal':<10} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR%':>7} {'Avg P&L':>10} {'Total P&L':>12} {'PF':>6} {'Best':>10} {'Worst':>10}")
    print(f"  {'':=<10} {'':=<7} {'':=<6} {'':=<7} {'':=<7} {'':=<10} {'':=<12} {'':=<6} {'':=<10} {'':=<10}")

    for sig, sig_name in [('MB', 'Mom Burst'), ('CB', 'Compress')]:
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
        spf = gw / gl if gl > 0 else float('inf')
        best_s = max(sig_trades, key=lambda t: t.pnl_rs)
        worst_s = min(sig_trades, key=lambda t: t.pnl_rs)
        print(f"  {sig_name:<10} {len(sig_trades):>7} {len(sw):>6} {len(sl_t):>7} {swr:>6.1f}% {savg:>+10,.0f} {stotal:>+12,.0f} {spf:>6.2f} {best_s.pnl_rs:>+10,.0f} {worst_s.pnl_rs:>+10,.0f}")

    print(f"  {'':=<10} {'':=<7} {'':=<6} {'':=<7} {'':=<7} {'':=<10} {'':=<12} {'':=<6} {'':=<10} {'':=<10}")
    print(f"  {'TOTAL':<10} {total:>7} {len(wins):>6} {len(losses):>7} {wr:>6.1f}% {avg_pnl:>+10,.0f} {total_pnl:>+12,.0f} {pf:>6.2f} {best_trade.pnl_rs:>+10,.0f} {worst_trade.pnl_rs:>+10,.0f}")

    # ── By Direction ──
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

    # ── Exit Analysis ──
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

    # ── Time of Day ──
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

    # ── Daily P&L Table ──
    print(f"\n{'=' * 110}")
    print(f"  DAILY P&L TABLE")
    print(f"{'=' * 110}")
    print(f"  {'Date':<12} {'Trades':>7} {'W-L':>7} {'Gross P&L':>12} {'Brokerage':>10} {'Net P&L':>12} {'Cumulative':>12} {'DD from Pk':>12}")
    print(f"  {'':=<12} {'':=<7} {'':=<7} {'':=<12} {'':=<10} {'':=<12} {'':=<12} {'':=<12}")

    cumulative = 0
    peak_cum = 0

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
        dd_str = f"Rs {dd:>+,.0f}" if dd > 0 else "-"
        print(f"  {date_str:<12} {len(dt):>7} {dw:>2}-{dl:<2}  {gross:>+12,.0f} {brok:>10,.0f} {net:>+12,.0f} {cumulative:>+12,.0f}  {dd_str:>10}")

    # ── Monthly Summary ──
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

    # ── Equity Curve (ASCII) ──
    print(f"\n{'=' * 110}")
    print(f"  EQUITY CURVE (Cumulative P&L)")
    print(f"{'=' * 110}")

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
        chart_width = 60

        range_cum = max_cum - min_cum
        if range_cum == 0:
            range_cum = 1

        print()
        for date_str, cum_val in cum_by_day:
            bar_pos = int((cum_val - min_cum) / range_cum * chart_width)
            zero_pos = int((0 - min_cum) / range_cum * chart_width)

            line = [' '] * (chart_width + 1)
            if 0 <= zero_pos <= chart_width:
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

    # ── Trade Log (ALL) ──
    print(f"{'=' * 110}")
    print(f"  TRADE LOG (ALL {total} TRADES)")
    print(f"{'=' * 110}")
    print(f"  {'#':>3} {'Date':<12} {'Entry':>6} {'Exit':>6} {'Sig':>3} {'Dir':>3} {'Strike':>7} {'EntPrem':>8} {'ExPrem':>8} {'Chg%':>7} {'ExitReason':<12} {'Gross P&L':>10} {'Net P&L':>10} {'Cum P&L':>10}")
    print(f"  {'':=<3} {'':=<12} {'':=<6} {'':=<6} {'':=<3} {'':=<3} {'':=<7} {'':=<8} {'':=<8} {'':=<7} {'':=<12} {'':=<10} {'':=<10} {'':=<10}")

    cumulative = 0
    for i, t in enumerate(sorted_trades, 1):
        chg = (t.exit_premium - t.entry_premium) / t.entry_premium * 100
        gross = t.pnl_pts * LOT_SIZE
        cumulative += t.pnl_rs
        exit_t = t.exit_time.strftime('%H:%M') if t.exit_time else '  -- '
        print(f"  {i:>3} {t.date:<12} {t.entry_time.strftime('%H:%M'):>6} {exit_t:>6} {t.signal_type:>3} {t.direction:>3} {t.strike:>7} {t.entry_premium:>8.1f} {t.exit_premium:>8.1f} {chg:>+6.1f}% {t.exit_reason:<12} {gross:>+10,.0f} {t.pnl_rs:>+10,.0f} {cumulative:>+10,.0f}")

    # ── Risk Metrics ──
    print(f"\n{'=' * 110}")
    print(f"  RISK METRICS")
    print(f"{'=' * 110}")
    print(f"""
  Max Drawdown:                Rs {max_dd:,.0f}{f' ({max_dd_pct:.1f}% of peak equity)' if max_dd_pct > 0 else ''}
  Drawdown Period:             {dd_start_date} to {dd_end_date}

  Win Days:                    {win_days}
  Loss Days:                   {loss_days}
  Win Days %:                  {win_days/(win_days+loss_days)*100:.1f}%

  Max Consec Winning Trades:   {max_ct_wins}
  Max Consec Losing Trades:    {max_ct_losses}

  Avg Daily P&L:               Rs {daily_mean:+,.0f}
  Std Dev Daily P&L:           Rs {daily_std:,.0f}
  Sharpe Ratio (daily):        {sharpe:.2f}
  Calmar Ratio (annual):       {calmar:.2f}

  Avg Trades/Day:              {total / trading_days_count:.1f}
  Avg Hold Time:               {avg_hold:.1f} min
""")

    # ── Final Verdict ──
    print(f"{'=' * 110}")
    print(f"{'=' * 110}")
    print(f"  FINAL VERDICT")
    print(f"{'=' * 110}")

    if total_pnl > 0:
        monthly_est = daily_mean * 22
        print(f"""
  PROFITABLE: Rs {total_pnl:+,.0f} over {trading_days_count} trading days

  P&L Breakdown:
    Gross P&L:     Rs {gross_pnl_before:+,.0f}
    Brokerage:     Rs {total_brokerage:,.0f} ({total_brokerage/abs(gross_pnl_before)*100:.1f}% of gross)
    Net P&L:       Rs {total_pnl:+,.0f}

  Projections (assuming similar market):
    Daily avg:     Rs {daily_mean:+,.0f}
    Monthly est:   Rs {monthly_est:+,.0f} (22 trading days)

  Key Metrics:
    Win Rate:      {wr:.1f}%
    Profit Factor: {pf:.2f}
    Sharpe Ratio:  {sharpe:.2f}
    Max Drawdown:  Rs {max_dd:,.0f}
""")
    else:
        print(f"""
  UNPROFITABLE: Rs {total_pnl:+,.0f} over {trading_days_count} trading days
  Avg Rs {daily_mean:+,.0f}/day
""")

    if pf >= 1.5 and wr >= 50:
        verdict = "STRONG -- Ready for paper trading"
    elif pf >= 1.5 and wr >= 45:
        verdict = "VIABLE -- Ready for paper trading with monitoring"
    elif pf >= 1.2:
        verdict = "PROMISING -- Needs more out-of-sample data"
    elif pf >= 1.0:
        verdict = "MARGINAL -- Needs refinement"
    else:
        verdict = "LOSING -- Do NOT deploy"

    print(f"  Verdict: {verdict}")
    print(f"{'=' * 110}")


# ── Comparison Table ─────────────────────────────────────────────────
def print_comparison_table(results: List[Tuple[str, str, dict]]):
    """Print side-by-side comparison of all variations."""
    print(f"\n{'=' * 110}")
    print(f"{'=' * 110}")
    print(f"  COMPARISON TABLE -- ALL VARIATIONS")
    print(f"{'=' * 110}")
    print(f"{'=' * 110}")

    print(f"\n  {'Metric':<25}", end="")
    for label, name, _ in results:
        header = f"{label}: {name}"
        print(f" {header:>20}", end="")
    print()
    print(f"  {'':=<25}", end="")
    for _ in results:
        print(f" {'':=<20}", end="")
    print()

    metrics = [
        ('Trades', lambda s: f"{s['trades']}" if s['trades'] > 0 else "0"),
        ('Win Rate', lambda s: f"{s['wr']:.1f}%" if s['trades'] > 0 else "-"),
        ('Net P&L', lambda s: f"Rs {s['net_pnl']:+,.0f}" if s['trades'] > 0 else "-"),
        ('Gross P&L', lambda s: f"Rs {s['gross_pnl']:+,.0f}" if s['trades'] > 0 else "-"),
        ('Brokerage', lambda s: f"Rs {s['brokerage']:,.0f}" if s['trades'] > 0 else "-"),
        ('Profit Factor', lambda s: f"{s['pf']:.2f}" if s['trades'] > 0 else "-"),
        ('Expectancy/Trade', lambda s: f"Rs {s['expectancy']:+,.0f}" if s['trades'] > 0 else "-"),
        ('Avg Winner', lambda s: f"Rs {s['avg_winner']:+,.0f}" if s['trades'] > 0 else "-"),
        ('Avg Loser', lambda s: f"Rs {s['avg_loser']:+,.0f}" if s['trades'] > 0 else "-"),
        ('Max Drawdown', lambda s: f"Rs {s['max_dd']:,.0f}" if s['trades'] > 0 else "-"),
        ('Sharpe', lambda s: f"{s['sharpe']:.2f}" if s['trades'] > 0 else "-"),
        ('Win Days', lambda s: f"{s['win_days']}/{s['trading_days']}" if s['trades'] > 0 else "-"),
        ('Avg Trades/Day', lambda s: f"{s['avg_trades_per_day']:.1f}" if s['trades'] > 0 else "-"),
    ]

    for metric_name, fmt_fn in metrics:
        print(f"  {metric_name:<25}", end="")
        for _, _, stats in results:
            print(f" {fmt_fn(stats):>20}", end="")
        print()

    # Monthly breakdown
    all_months = set()
    for _, _, stats in results:
        if stats['trades'] > 0:
            all_months.update(stats['monthly_pnl'].keys())

    for month in sorted(all_months):
        print(f"  {month:<25}", end="")
        for _, _, stats in results:
            if stats['trades'] > 0 and month in stats['monthly_pnl']:
                print(f" Rs {stats['monthly_pnl'][month]:>+14,.0f}", end="")
            else:
                print(f" {'  -':>20}", end="")
        print()

    # Highlight best
    valid = [(l, n, s) for l, n, s in results if s['trades'] > 0]
    if valid:
        best_pnl = max(valid, key=lambda x: x[2]['net_pnl'])
        best_sharpe = max(valid, key=lambda x: x[2]['sharpe'])
        best_wr = max(valid, key=lambda x: x[2]['wr'])
        print(f"\n  {'':=<25} {'':=<80}")
        print(f"  Best Net P&L:           {best_pnl[0]}: {best_pnl[1]} (Rs {best_pnl[2]['net_pnl']:+,.0f})")
        print(f"  Best Sharpe:            {best_sharpe[0]}: {best_sharpe[1]} ({best_sharpe[2]['sharpe']:.2f})")
        print(f"  Best Win Rate:          {best_wr[0]}: {best_wr[1]} ({best_wr[2]['wr']:.1f}%)")

    print(f"\n{'=' * 110}")


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print("=" * 110)
    print("  RALLY CATCHER V3 -- SELECTIVE (FEWER trades, HIGHER quality)")
    print("  Signals: MB (Confirmed Momentum Burst) + CB (Compression Breakout)")
    print("  Windows: 09:24-10:00 + 11:45-13:00 | Max 2/day | 12m cooldown | Anti-revenge")
    print("  Exits: +20% target, -10% SL, 2-stage trailing, 5% momentum exit, 30m/45m time exit")
    print(f"  Strike: 2 ITM (100 pts)  |  Lot: {LOT_SIZE} qty  |  Brokerage: Rs {BROKERAGE_TOTAL}/trade  |  Min Premium: Rs {MIN_PREMIUM}")
    print("=" * 110)
    print()

    days = load_all_data()
    sorted_dates = sorted(days.keys())

    print(f"\nProcessing {len(sorted_dates)} trading days: {sorted_dates[0]} to {sorted_dates[-1]}")
    print("-" * 110)

    # ── Define variations ──
    variations = [
        VariationConfig(
            name="Morning Only",
            label="A",
            windows=[(WINDOW_1_START, WINDOW_1_END)],
            max_trades_per_day=1,
        ),
        VariationConfig(
            name="2-Window",
            label="B",
            windows=[(WINDOW_1_START, WINDOW_1_END), (WINDOW_2_START, WINDOW_2_END)],
            max_trades_per_day=2,
        ),
        VariationConfig(
            name="PE-Only",
            label="C",
            windows=[(WINDOW_1_START, WINDOW_1_END), (WINDOW_2_START, WINDOW_2_END)],
            max_trades_per_day=2,
            pe_only=True,
        ),
        VariationConfig(
            name="High Conviction Only",
            label="D",
            windows=[(WINDOW_1_START, WINDOW_1_END), (WINDOW_2_START, WINDOW_2_END)],
            max_trades_per_day=2,
            high_conviction=True,
        ),
    ]

    # ── Run all variations ──
    all_results: List[Tuple[str, str, dict, List[Trade], VariationConfig]] = []

    for config in variations:
        print(f"\n  Running Variation {config.label}: {config.name}...")
        trades = run_backtest(days, sorted_dates, config)
        stats = calc_stats(trades, sorted_dates)
        all_results.append((config.label, config.name, stats, trades, config))

        # Print daily progress for this variation
        if trades:
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
                print(f"    {date_str}  |  T: {len(dt):2d}  W: {dw}  L: {dl}  |  P&L: Rs {dpnl:>+8,.0f}  |  Cum: Rs {cumulative:>+10,.0f}  |  {sigs}")

    # ── Print variation summaries ──
    print(f"\n\n{'=' * 110}")
    print(f"{'=' * 110}")
    print(f"  VARIATION SUMMARIES")
    print(f"{'=' * 110}")

    for label, name, stats, _, _ in all_results:
        print_variation_summary(label, name, stats)

    # ── Find the best variation (by net P&L) ──
    valid_results = [(l, n, s, t, c) for l, n, s, t, c in all_results if s['trades'] > 0]
    if valid_results:
        best = max(valid_results, key=lambda x: x[2]['net_pnl'])
        best_label, best_name, best_stats, best_trades, best_config = best

        print(f"\n\n{'=' * 110}")
        print(f"  >>> BEST VARIATION: {best_label} ({best_name}) — Rs {best_stats['net_pnl']:+,.0f}")
        print(f"{'=' * 110}")
        print(f"  Printing full detailed report below...")

        # ── Full detailed report for best ──
        print_full_report(best_trades, sorted_dates, best_config)

    # ── Comparison table ──
    comparison_data = [(l, n, s) for l, n, s, _, _ in all_results]
    print_comparison_table(comparison_data)


if __name__ == '__main__':
    main()
