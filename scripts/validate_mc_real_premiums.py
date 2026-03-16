"""MC Strategy — Real Premium Validation.

Tests the MC (Momentum Continuation) strategy against ACTUAL option premium
data from oi_snapshots (ce_ltp/pe_ltp) across 32 trading days.

No delta model, no simulation — pure real premiums from the database.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants (from MCConfig)
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).resolve().parent.parent / "oi_tracker.db"

NIFTY_STEP = 50
LOT_SIZE = 65
BROKERAGE = 72  # round-trip per trade

# Signal detection
TIME_START = time(10, 0)
TIME_END = time(14, 0)
FORCE_CLOSE_TIME = time(15, 15)
MAX_TRADES_PER_DAY = 1
COOLDOWN_MINUTES = 12
MIN_PREMIUM = 100.0
MAX_PREMIUM = 500.0
RALLY_MIN_PTS = 25.0
PULLBACK_MIN_PCT = 0.20
PULLBACK_MAX_PCT = 0.65
PULLBACK_CANDLES = 5

# Exit management
SL_PCT = 15.0
TARGET_PCT = 8.0
TRAIL_1_TRIGGER = 10.0
TRAIL_1_LOCK = 4.0
TRAIL_2_TRIGGER = 15.0
TRAIL_2_LOCK = 10.0
TIME_EXIT_MIN = 30
TIME_EXIT_DEAD_PCT = 3.0
MAX_DURATION_MIN = 45


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SpotCandle:
    timestamp: datetime
    spot_price: float


@dataclass
class PremiumSnapshot:
    timestamp: datetime
    strike: int
    ce_ltp: float
    pe_ltp: float


@dataclass
class Trade:
    date: str
    signal_time: str
    direction: str  # "CE" or "PE"
    strike: int
    entry_premium: float
    entry_time: datetime
    sl_premium: float
    target_premium: float
    trail_stage: int = 0
    max_premium: float = 0.0
    exit_premium: float = 0.0
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    pnl_pct: float = 0.0
    gross_pnl_rs: float = 0.0
    net_pnl_rs: float = 0.0
    signal_data: Dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_trading_days() -> List[str]:
    """Get all distinct dates in oi_snapshots."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT DATE(timestamp) as dt FROM oi_snapshots ORDER BY dt"
    ).fetchall()
    conn.close()
    return [r["dt"] for r in rows]


def load_spot_history(conn: sqlite3.Connection, day: str) -> List[SpotCandle]:
    """Load spot prices from analysis_history for a given day."""
    rows = conn.execute(
        "SELECT timestamp, spot_price FROM analysis_history "
        "WHERE DATE(timestamp) = ? AND spot_price > 0 "
        "ORDER BY timestamp",
        (day,),
    ).fetchall()
    result = []
    for r in rows:
        ts = datetime.fromisoformat(r["timestamp"])
        result.append(SpotCandle(timestamp=ts, spot_price=r["spot_price"]))
    return result


def load_premium_snapshots(
    conn: sqlite3.Connection, day: str, strike: int
) -> Dict[str, PremiumSnapshot]:
    """Load all premium snapshots for a specific strike on a day.

    Returns dict keyed by timestamp string for fast lookup.
    """
    rows = conn.execute(
        "SELECT timestamp, ce_ltp, pe_ltp FROM oi_snapshots "
        "WHERE DATE(timestamp) = ? AND strike_price = ? "
        "ORDER BY timestamp",
        (day, strike),
    ).fetchall()
    result = {}
    for r in rows:
        ts_str = r["timestamp"]
        result[ts_str] = PremiumSnapshot(
            timestamp=datetime.fromisoformat(ts_str),
            strike=strike,
            ce_ltp=r["ce_ltp"] or 0.0,
            pe_ltp=r["pe_ltp"] or 0.0,
        )
    return result


def get_premium_at_time(
    conn: sqlite3.Connection, day: str, strike: int, ts: datetime, option_type: str
) -> float:
    """Get the premium closest to (but not after) the given timestamp.

    Falls back to closest available if no exact match.
    """
    ts_str = ts.isoformat()
    key = "ce_ltp" if option_type == "CE" else "pe_ltp"

    # Get closest timestamp <= ts
    row = conn.execute(
        f"SELECT {key} FROM oi_snapshots "
        "WHERE DATE(timestamp) = ? AND strike_price = ? "
        f"AND timestamp <= ? AND {key} > 0 "
        "ORDER BY timestamp DESC LIMIT 1",
        (day, strike, ts_str),
    ).fetchone()

    if row and row[0] > 0:
        return row[0]

    # Fallback: closest timestamp >= ts
    row = conn.execute(
        f"SELECT {key} FROM oi_snapshots "
        "WHERE DATE(timestamp) = ? AND strike_price = ? "
        f"AND timestamp >= ? AND {key} > 0 "
        "ORDER BY timestamp ASC LIMIT 1",
        (day, strike, ts_str),
    ).fetchone()

    if row and row[0] > 0:
        return row[0]

    return 0.0


def compute_weekly_trend(conn: sqlite3.Connection, current_day: str) -> str:
    """UP/DOWN/NEUTRAL based on last 2 trading day closes before current_day."""
    rows = conn.execute(
        "SELECT DATE(timestamp) as dt, close FROM nifty_history "
        "WHERE DATE(timestamp) < ? AND time(timestamp) >= '15:00' "
        "GROUP BY dt ORDER BY dt DESC LIMIT 2",
        (current_day,),
    ).fetchall()

    if len(rows) < 2:
        return "NEUTRAL"

    latest_close = rows[0]["close"]
    prev_close = rows[1]["close"]

    if latest_close > prev_close + 20:
        return "UP"
    elif latest_close < prev_close - 20:
        return "DOWN"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# MC Signal Detection (standalone, no import needed)
# ---------------------------------------------------------------------------

def get_mc_strike(spot: float, option_type: str) -> int:
    """CE: ATM - 100, PE: ATM + 100."""
    atm = round(spot / NIFTY_STEP) * NIFTY_STEP
    if option_type == "CE":
        return atm - 2 * NIFTY_STEP
    else:
        return atm + 2 * NIFTY_STEP


def detect_rally(closes: List[float], day_open: float) -> Optional[Dict]:
    """Check if spot moved 25+ pts from day open in one direction."""
    current = closes[-1]
    move = current - day_open

    if abs(move) < RALLY_MIN_PTS:
        return None

    direction = "UP" if move > 0 else "DOWN"
    peak = max(closes) if direction == "UP" else min(closes)

    return {
        "direction": direction,
        "rally_pts": abs(move),
        "rally_peak": peak,
    }


def detect_pullback(closes: List[float], rally: Dict) -> Optional[Dict]:
    """Check if last 5 candles show 20-65% retracement of rally."""
    n = PULLBACK_CANDLES
    if len(closes) < n + 1:
        return None

    recent = closes[-n:]
    peak = rally["rally_peak"]
    direction = rally["direction"]
    rally_pts = rally["rally_pts"]

    if direction == "UP":
        pullback_extreme = min(recent)
        pullback_pts = peak - pullback_extreme
    else:
        pullback_extreme = max(recent)
        pullback_pts = pullback_extreme - peak

    if rally_pts <= 0:
        return None

    pullback_pct = pullback_pts / rally_pts

    if pullback_pct < PULLBACK_MIN_PCT or pullback_pct > PULLBACK_MAX_PCT:
        return None

    return {
        "pullback_pct": pullback_pct,
        "pullback_extreme": pullback_extreme,
    }


def check_resumption(closes: List[float], rally: Dict) -> bool:
    """Last candle closed in original rally direction."""
    if len(closes) < 2:
        return False
    if rally["direction"] == "UP":
        return closes[-1] > closes[-2]
    else:
        return closes[-1] < closes[-2]


def detect_mc_signal(
    closes: List[float],
    day_open: float,
    weekly_trend: str,
) -> Optional[Dict]:
    """Full MC signal detection. Returns signal dict or None."""
    if len(closes) < 10:
        return None

    rally = detect_rally(closes, day_open)
    if rally is None:
        return None

    pullback = detect_pullback(closes, rally)
    if pullback is None:
        return None

    if not check_resumption(closes, rally):
        return None

    # Weekly trend filter
    rally_dir = rally["direction"]
    if weekly_trend != "NEUTRAL":
        if rally_dir == "UP" and weekly_trend == "DOWN":
            return None
        if rally_dir == "DOWN" and weekly_trend == "UP":
            return None

    option_type = "CE" if rally_dir == "UP" else "PE"
    spot = closes[-1]
    strike = get_mc_strike(spot, option_type)

    return {
        "option_type": option_type,
        "strike": strike,
        "rally_pts": rally["rally_pts"],
        "rally_direction": rally_dir,
        "pullback_pct": pullback["pullback_pct"],
        "weekly_trend": weekly_trend,
        "day_open": day_open,
        "spot_at_signal": spot,
    }


# ---------------------------------------------------------------------------
# Trade management (exit logic)
# ---------------------------------------------------------------------------

def check_exit(
    trade: Trade,
    current_premium: float,
    current_time: datetime,
) -> Optional[Tuple[str, str]]:
    """Check all exit conditions. Returns (status, reason) or None.

    Also updates trade.trail_stage and trade.sl_premium as needed.
    """
    entry = trade.entry_premium
    sl = trade.sl_premium
    target = trade.target_premium

    pnl_pct = ((current_premium - entry) / entry) * 100

    # Update max premium
    if current_premium > trade.max_premium:
        trade.max_premium = current_premium

    # Trailing stop logic (update SL before checking)
    if pnl_pct >= TRAIL_2_TRIGGER and trade.trail_stage < 2:
        new_sl = round(entry * (1 + TRAIL_2_LOCK / 100), 2)
        if new_sl > sl:
            trade.sl_premium = new_sl
            sl = new_sl
            trade.trail_stage = 2

    elif pnl_pct >= TRAIL_1_TRIGGER and trade.trail_stage < 1:
        new_sl = round(entry * (1 + TRAIL_1_LOCK / 100), 2)
        if new_sl > sl:
            trade.sl_premium = new_sl
            sl = new_sl
            trade.trail_stage = 1

    # SL hit
    if current_premium <= sl:
        reason = "TRAIL_SL" if trade.trail_stage > 0 else "SL"
        status = "WON" if pnl_pct > 0 else "LOST"
        return (status, reason)

    # Target hit
    if current_premium >= target:
        return ("WON", "TARGET")

    # Time-based exits
    elapsed_min = (current_time - trade.entry_time).total_seconds() / 60

    if elapsed_min >= MAX_DURATION_MIN:
        status = "WON" if pnl_pct > 0 else "LOST"
        return (status, "MAX_TIME")

    if elapsed_min >= TIME_EXIT_MIN and abs(pnl_pct) < TIME_EXIT_DEAD_PCT:
        status = "WON" if pnl_pct > 0 else "LOST"
        return (status, "TIME_FLAT")

    # EOD force close
    if current_time.time() >= FORCE_CLOSE_TIME:
        status = "WON" if pnl_pct > 0 else "LOST"
        return (status, "EOD")

    return None


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

def run_backtest() -> List[Trade]:
    """Run MC strategy across all trading days with real premium data."""
    conn = get_conn()
    trading_days = get_trading_days()

    all_trades: List[Trade] = []

    for day in trading_days:
        spots = load_spot_history(conn, day)
        if not spots:
            continue

        weekly_trend = compute_weekly_trend(conn, day)

        # Filter to 10:00-14:00 for signal detection (but we track trades beyond)
        day_open_spot = spots[0].spot_price

        active_trade: Optional[Trade] = None
        day_trades: List[Trade] = []
        last_exit_time: Optional[datetime] = None

        for i, candle in enumerate(spots):
            t = candle.timestamp.time()

            # ----------------------------------------------------------
            # Check active trade exits (any time)
            # ----------------------------------------------------------
            if active_trade is not None:
                premium = get_premium_at_time(
                    conn, day, active_trade.strike,
                    candle.timestamp, active_trade.direction,
                )
                if premium <= 0:
                    # No premium data — skip this candle
                    continue

                result = check_exit(active_trade, premium, candle.timestamp)
                if result is not None:
                    status, reason = result
                    pnl_pct = ((premium - active_trade.entry_premium) /
                               active_trade.entry_premium) * 100
                    gross_rs = (premium - active_trade.entry_premium) * LOT_SIZE
                    net_rs = gross_rs - BROKERAGE

                    active_trade.exit_premium = premium
                    active_trade.exit_time = candle.timestamp
                    active_trade.exit_reason = reason
                    active_trade.pnl_pct = pnl_pct
                    active_trade.gross_pnl_rs = gross_rs
                    active_trade.net_pnl_rs = net_rs

                    all_trades.append(active_trade)
                    day_trades.append(active_trade)
                    last_exit_time = candle.timestamp
                    active_trade = None
                continue  # Don't look for new signals while in a trade

            # ----------------------------------------------------------
            # Signal detection: 10:00-14:00 only
            # ----------------------------------------------------------
            if t < TIME_START or t > TIME_END:
                continue

            # Max trades per day
            if len(day_trades) >= MAX_TRADES_PER_DAY:
                continue

            # Cooldown
            if last_exit_time is not None:
                elapsed = (candle.timestamp - last_exit_time).total_seconds()
                if elapsed < COOLDOWN_MINUTES * 60:
                    continue

            # Build spot closes up to this candle
            closes = [s.spot_price for s in spots[:i + 1]]

            signal = detect_mc_signal(closes, day_open_spot, weekly_trend)
            if signal is None:
                continue

            # Look up REAL premium
            strike = signal["strike"]
            option_type = signal["option_type"]
            entry_premium = get_premium_at_time(
                conn, day, strike, candle.timestamp, option_type,
            )

            if entry_premium <= 0:
                continue
            if entry_premium < MIN_PREMIUM or entry_premium > MAX_PREMIUM:
                continue

            sl_premium = round(entry_premium * (1 - SL_PCT / 100), 2)
            target_premium = round(entry_premium * (1 + TARGET_PCT / 100), 2)

            active_trade = Trade(
                date=day,
                signal_time=candle.timestamp.strftime("%H:%M"),
                direction=option_type,
                strike=strike,
                entry_premium=entry_premium,
                entry_time=candle.timestamp,
                sl_premium=sl_premium,
                target_premium=target_premium,
                trail_stage=0,
                max_premium=entry_premium,
                signal_data=signal,
            )

        # End of day: force-close any active trade
        if active_trade is not None:
            # Use last available premium
            last_candle = spots[-1]
            premium = get_premium_at_time(
                conn, day, active_trade.strike,
                last_candle.timestamp, active_trade.direction,
            )
            if premium <= 0:
                premium = active_trade.entry_premium  # worst case: flat

            pnl_pct = ((premium - active_trade.entry_premium) /
                       active_trade.entry_premium) * 100
            gross_rs = (premium - active_trade.entry_premium) * LOT_SIZE
            net_rs = gross_rs - BROKERAGE

            active_trade.exit_premium = premium
            active_trade.exit_time = last_candle.timestamp
            active_trade.exit_reason = "EOD"
            active_trade.pnl_pct = pnl_pct
            active_trade.gross_pnl_rs = gross_rs
            active_trade.net_pnl_rs = net_rs

            all_trades.append(active_trade)

    conn.close()
    return all_trades


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(trades: List[Trade]):
    """Print full professional report."""

    trading_days = get_trading_days()
    n_days = len(trading_days)
    first_day = trading_days[0] if trading_days else "?"
    last_day = trading_days[-1] if trading_days else "?"

    total = len(trades)
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    n_wins = len(wins)
    n_losses = len(losses)
    wr = (n_wins / total * 100) if total > 0 else 0

    gross_pnl = sum(t.gross_pnl_rs for t in trades)
    total_brokerage = total * BROKERAGE
    net_pnl = sum(t.net_pnl_rs for t in trades)

    avg_winner = (sum(t.net_pnl_rs for t in wins) / n_wins) if n_wins > 0 else 0
    avg_loser = (sum(t.net_pnl_rs for t in losses) / n_losses) if n_losses > 0 else 0

    # Profit factor
    total_gross_wins = sum(t.gross_pnl_rs for t in wins) if wins else 0
    total_gross_losses = abs(sum(t.gross_pnl_rs for t in losses)) if losses else 1
    pf = total_gross_wins / total_gross_losses if total_gross_losses > 0 else 0

    # Max drawdown
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    for t in trades:
        cum += t.net_pnl_rs
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak * 100) if peak > 0 else 0

    # Consecutive wins/losses
    max_consec_w = 0
    max_consec_l = 0
    cur_w = 0
    cur_l = 0
    for t in trades:
        if t.pnl_pct > 0:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        max_consec_w = max(max_consec_w, cur_w)
        max_consec_l = max(max_consec_l, cur_l)

    # Sharpe (daily returns)
    daily_returns: Dict[str, float] = {}
    for t in trades:
        daily_returns[t.date] = daily_returns.get(t.date, 0) + t.net_pnl_rs
    returns_list = list(daily_returns.values())
    if len(returns_list) > 1:
        import statistics
        avg_ret = statistics.mean(returns_list)
        std_ret = statistics.stdev(returns_list)
        sharpe = (avg_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0
    else:
        sharpe = 0

    # By direction
    ce_trades = [t for t in trades if t.direction == "CE"]
    pe_trades = [t for t in trades if t.direction == "PE"]

    def dir_stats(tlist):
        n = len(tlist)
        w = sum(1 for t in tlist if t.pnl_pct > 0)
        pnl = sum(t.net_pnl_rs for t in tlist)
        wr = (w / n * 100) if n > 0 else 0
        return n, wr, pnl

    ce_n, ce_wr, ce_pnl = dir_stats(ce_trades)
    pe_n, pe_wr, pe_pnl = dir_stats(pe_trades)

    # Exit analysis
    exit_reasons: Dict[str, List[Trade]] = {}
    for t in trades:
        r = t.exit_reason
        if r not in exit_reasons:
            exit_reasons[r] = []
        exit_reasons[r].append(t)

    # Monthly stats
    monthly: Dict[str, List[Trade]] = {}
    for t in trades:
        month = t.date[:7]
        if month not in monthly:
            monthly[month] = []
        monthly[month].append(t)

    # =====================================================================
    # Print report
    # =====================================================================

    print()
    print("=" * 70)
    print("MC STRATEGY -- REAL PREMIUM VALIDATION")
    print("=" * 70)
    print(f"{n_days} Trading Days | {first_day} to {last_day}")
    print("Using ACTUAL ce_ltp/pe_ltp from oi_snapshots")
    print()

    # SUMMARY
    print("SUMMARY:")
    print(f"  Trades: {total} | WR: {wr:.1f}% | PF: {pf:.2f}")
    print(f"  Gross P&L: Rs {gross_pnl:,.0f} | Brokerage: Rs {total_brokerage:,.0f} | Net P&L: Rs {net_pnl:,.0f}")
    print(f"  Max DD: Rs {max_dd:,.0f} ({max_dd_pct:.1f}% of peak)")
    print(f"  Avg Winner: Rs {avg_winner:,.0f} | Avg Loser: Rs {avg_loser:,.0f}")
    print()

    # BY DIRECTION
    print("BY DIRECTION:")
    print(f"  CE: {ce_n} trades, {ce_wr:.1f}% WR, Rs {ce_pnl:,.0f} P&L")
    print(f"  PE: {pe_n} trades, {pe_wr:.1f}% WR, Rs {pe_pnl:,.0f} P&L")
    print()

    # EXIT ANALYSIS
    print("EXIT ANALYSIS:")
    print(f"  {'Reason':<12} | {'Count':>5} | {'WR%':>5} | {'Avg P&L':>10}")
    print(f"  {'-'*12}-+-{'-'*5}-+-{'-'*5}-+-{'-'*10}")
    for reason in sorted(exit_reasons.keys()):
        tlist = exit_reasons[reason]
        n = len(tlist)
        w = sum(1 for t in tlist if t.pnl_pct > 0)
        wr_r = (w / n * 100) if n > 0 else 0
        avg_pnl = sum(t.net_pnl_rs for t in tlist) / n if n > 0 else 0
        print(f"  {reason:<12} | {n:>5} | {wr_r:>4.0f}% | Rs {avg_pnl:>7,.0f}")
    print()

    # DAILY TABLE
    print("DAILY TABLE:")
    header = (
        f"  {'Date':<12} {'#':>2} {'Time':<6} {'Dir':<3} {'Strike':>6} "
        f"{'Entry':>7} {'Exit':>7} {'Chg%':>6} {'Reason':<10} "
        f"{'Gross':>8} {'Net':>8} {'Cum':>9}"
    )
    print(header)
    print(f"  {'-' * (len(header) - 2)}")

    cum_net = 0.0
    trade_num = 0
    for t in trades:
        trade_num += 1
        cum_net += t.net_pnl_rs
        print(
            f"  {t.date:<12} {trade_num:>2} {t.signal_time:<6} {t.direction:<3} "
            f"{t.strike:>6} {t.entry_premium:>7.2f} {t.exit_premium:>7.2f} "
            f"{t.pnl_pct:>+5.1f}% {t.exit_reason:<10} "
            f"Rs {t.gross_pnl_rs:>+6,.0f} Rs {t.net_pnl_rs:>+6,.0f} Rs {cum_net:>+7,.0f}"
        )

    # Days with no trade
    trade_dates = set(t.date for t in trades)
    no_trade_days = [d for d in trading_days if d not in trade_dates]
    print(f"\n  Days with no signal: {len(no_trade_days)}/{n_days}")
    if no_trade_days:
        # Show a few
        shown = no_trade_days[:10]
        print(f"  ({', '.join(shown)}{'...' if len(no_trade_days) > 10 else ''})")
    print()

    # MONTHLY
    print("MONTHLY:")
    print(f"  {'Month':<8} | {'Trades':>6} | {'WR':>5} | {'Net P&L':>10}")
    print(f"  {'-'*8}-+-{'-'*6}-+-{'-'*5}-+-{'-'*10}")
    for month in sorted(monthly.keys()):
        tlist = monthly[month]
        n = len(tlist)
        w = sum(1 for t in tlist if t.pnl_pct > 0)
        wr_m = (w / n * 100) if n > 0 else 0
        pnl_m = sum(t.net_pnl_rs for t in tlist)
        print(f"  {month:<8} | {n:>6} | {wr_m:>4.0f}% | Rs {pnl_m:>+7,.0f}")
    print()

    # RISK
    print("RISK:")
    print(f"  Max Drawdown: Rs {max_dd:,.0f} ({max_dd_pct:.1f}% of peak)")
    print(f"  Annualized Sharpe: {sharpe:.2f}")
    print(f"  Max Consecutive Wins: {max_consec_w}")
    print(f"  Max Consecutive Losses: {max_consec_l}")
    print()

    # COMPARISON placeholder
    print("COMPARISON WITH SIMULATED:")
    print("  (No simulated MC backtest exists for this exact period.)")
    print(f"  Real Premium Result: Rs {net_pnl:,.0f} over {total} trades")
    print()

    # Trade details for debugging
    print("=" * 70)
    print("TRADE DETAILS (signal data):")
    print("=" * 70)
    for i, t in enumerate(trades, 1):
        sd = t.signal_data
        duration = ""
        if t.exit_time and t.entry_time:
            dur_min = (t.exit_time - t.entry_time).total_seconds() / 60
            duration = f"{dur_min:.0f}m"
        print(
            f"  #{i:>2} {t.date} {t.signal_time} | {t.direction} {t.strike} | "
            f"Rally: {sd.get('rally_pts', 0):.0f}pts {sd.get('rally_direction', '?')} | "
            f"PB: {sd.get('pullback_pct', 0):.0%} | "
            f"Weekly: {sd.get('weekly_trend', '?')} | "
            f"Entry: {t.entry_premium:.2f} -> Exit: {t.exit_premium:.2f} ({t.exit_reason}, {duration}) | "
            f"Max: {t.max_premium:.2f} | Trail: {t.trail_stage}"
        )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Loading data from oi_tracker.db ...")
    trades = run_backtest()
    print(f"Backtest complete: {len(trades)} trades found.")
    print_report(trades)
