"""
Rally Reverse Engineer — Find perfect trades, then reverse-engineer what preceded them.

Phases:
1. Find BEST possible trade each day (theoretical max)
2. Find REALISTIC best trade each day (constrained)
3. Analyze what happened BEFORE each realistic best trade entry
4. Build a Quality Score model and simulate
5. "Wait for the Dip" premium-bounce strategy
"""

import sqlite3
import json
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"
LOT_SIZE = 65
BROKERAGE = 72.0
MARKET_OPEN = "09:24"
MARKET_CLOSE = "14:30"
ENTRY_CUTOFF = "14:00"

# --- Data Structures --------------------------------------------------------

@dataclass
class PerfectTrade:
    date: str
    side: str  # CE or PE
    strike: int
    entry_time: str
    exit_time: str
    duration_min: float
    entry_premium: float
    exit_premium: float
    pct_gain: float
    pnl_rs: float  # at LOT_SIZE minus brokerage
    spot_at_entry: float
    spot_at_exit: float
    spot_direction: str  # UP or DOWN


@dataclass
class CandleData:
    timestamp: str
    spot: float
    ce_ltp: float
    pe_ltp: float
    ce_strike: int
    pe_strike: int
    # OI data
    ce_oi: int = 0
    pe_oi: int = 0
    ce_oi_change: int = 0
    pe_oi_change: int = 0
    ce_iv: float = 0.0
    pe_iv: float = 0.0
    ce_volume: int = 0
    pe_volume: int = 0
    # Analysis data
    vix: float = 0.0
    iv_skew: float = 0.0
    futures_basis: float = 0.0
    verdict: str = ""
    signal_confidence: float = 0.0
    futures_oi_change: int = 0


@dataclass
class SimTrade:
    date: str
    side: str
    strike: int
    entry_time: str
    exit_time: str
    entry_premium: float
    exit_premium: float
    pct_gain: float
    pnl_rs: float
    exit_reason: str
    spot_at_entry: float = 0.0
    spot_at_exit: float = 0.0
    quality_score: float = 0.0


# --- Database Loading --------------------------------------------------------

def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_trading_days(conn):
    cur = conn.execute(
        "SELECT DISTINCT DATE(timestamp) as d FROM analysis_history ORDER BY d"
    )
    return [r["d"] for r in cur.fetchall()]


def parse_ts(ts_str):
    """Parse ISO timestamp to datetime."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts_str}")


def time_str(dt):
    return dt.strftime("%H:%M")


def minutes_between(dt1, dt2):
    return (dt2 - dt1).total_seconds() / 60.0


def load_day_candles(conn, date_str):
    """Load aligned candle data for a day: spot + premiums for ITM strikes + OI + analysis."""

    # Get all timestamps + spot for the day from analysis_history
    analysis_rows = conn.execute("""
        SELECT timestamp, spot_price, vix, iv_skew, futures_basis, verdict,
               signal_confidence, futures_oi_change
        FROM analysis_history
        WHERE DATE(timestamp) = ?
        ORDER BY timestamp
    """, (date_str,)).fetchall()

    if not analysis_rows:
        return []

    # Determine opening spot and strikes
    # Filter to market hours first
    market_rows = []
    for r in analysis_rows:
        dt = parse_ts(r["timestamp"])
        t = dt.strftime("%H:%M")
        if t >= MARKET_OPEN and t <= MARKET_CLOSE:
            market_rows.append(r)

    if not market_rows:
        return []

    open_spot = market_rows[0]["spot_price"]
    atm_open = round(open_spot / 50) * 50

    # CE strike: 100 pts below ATM (slightly ITM for CE = below spot)
    # PE strike: 100 pts above ATM (slightly ITM for PE = above spot)
    ce_strike = atm_open - 100
    pe_strike = atm_open + 100

    # Load option premiums for these strikes
    candles = []
    for r in market_rows:
        ts = r["timestamp"]
        dt = parse_ts(ts)

        # Get CE premium at ce_strike
        snap_ce = conn.execute("""
            SELECT ce_ltp, ce_oi, ce_oi_change, ce_iv, ce_volume
            FROM oi_snapshots
            WHERE timestamp = ? AND strike_price = ?
        """, (ts, ce_strike)).fetchone()

        # Get PE premium at pe_strike
        snap_pe = conn.execute("""
            SELECT pe_ltp, pe_oi, pe_oi_change, pe_iv, pe_volume
            FROM oi_snapshots
            WHERE timestamp = ? AND strike_price = ?
        """, (ts, pe_strike)).fetchone()

        ce_ltp = snap_ce["ce_ltp"] if snap_ce else 0.0
        pe_ltp = snap_pe["pe_ltp"] if snap_pe else 0.0

        # Skip if premium data missing
        if ce_ltp <= 0 and pe_ltp <= 0:
            continue

        # Also get OI data for the nearby strikes (for pattern analysis)
        # Aggregate OI changes across a few strikes near ATM
        atm_now = round(r["spot_price"] / 50) * 50
        oi_data = conn.execute("""
            SELECT SUM(ce_oi_change) as net_ce_oi_chg, SUM(pe_oi_change) as net_pe_oi_chg,
                   SUM(ce_oi) as total_ce_oi, SUM(pe_oi) as total_pe_oi
            FROM oi_snapshots
            WHERE timestamp = ? AND strike_price BETWEEN ? AND ?
        """, (ts, atm_now - 150, atm_now + 150)).fetchone()

        c = CandleData(
            timestamp=ts,
            spot=r["spot_price"],
            ce_ltp=ce_ltp,
            pe_ltp=pe_ltp,
            ce_strike=ce_strike,
            pe_strike=pe_strike,
            ce_oi=oi_data["total_ce_oi"] if oi_data and oi_data["total_ce_oi"] else 0,
            pe_oi=oi_data["total_pe_oi"] if oi_data and oi_data["total_pe_oi"] else 0,
            ce_oi_change=oi_data["net_ce_oi_chg"] if oi_data and oi_data["net_ce_oi_chg"] else 0,
            pe_oi_change=oi_data["net_pe_oi_chg"] if oi_data and oi_data["net_pe_oi_chg"] else 0,
            ce_iv=snap_ce["ce_iv"] if snap_ce else 0.0,
            pe_iv=snap_pe["pe_iv"] if snap_pe else 0.0,
            ce_volume=snap_ce["ce_volume"] if snap_ce else 0,
            pe_volume=snap_pe["pe_volume"] if snap_pe else 0,
            vix=r["vix"] or 0.0,
            iv_skew=r["iv_skew"] or 0.0,
            futures_basis=r["futures_basis"] or 0.0,
            verdict=r["verdict"] or "",
            signal_confidence=r["signal_confidence"] or 0.0,
            futures_oi_change=r["futures_oi_change"] or 0,
        )
        candles.append(c)

    return candles


# --- Phase 1: Perfect Trades ------------------------------------------------

def find_perfect_trade(candles, side):
    """Find the maximum premium swing low->high for CE or PE."""
    if len(candles) < 2:
        return None

    premiums = [(c, c.ce_ltp if side == "CE" else c.pe_ltp) for c in candles]
    # Filter out zero premiums
    premiums = [(c, p) for c, p in premiums if p > 0]
    if len(premiums) < 2:
        return None

    best_trade = None
    best_pnl = -999999

    # For each possible entry, find the best exit after it
    min_prem = premiums[0][1]
    min_candle = premiums[0][0]

    for i in range(1, len(premiums)):
        c, p = premiums[i]

        # Check if selling here after buying at min gives better result
        if min_prem > 0:
            pct = (p - min_prem) / min_prem * 100
            raw_pnl = (p - min_prem) * LOT_SIZE - BROKERAGE

            if raw_pnl > best_pnl:
                best_pnl = raw_pnl
                entry_dt = parse_ts(min_candle.timestamp)
                exit_dt = parse_ts(c.timestamp)
                spot_dir = "UP" if c.spot > min_candle.spot else "DOWN"

                best_trade = PerfectTrade(
                    date=entry_dt.strftime("%Y-%m-%d"),
                    side=side,
                    strike=min_candle.ce_strike if side == "CE" else min_candle.pe_strike,
                    entry_time=time_str(entry_dt),
                    exit_time=time_str(exit_dt),
                    duration_min=minutes_between(entry_dt, exit_dt),
                    entry_premium=min_prem,
                    exit_premium=p,
                    pct_gain=pct,
                    pnl_rs=raw_pnl,
                    spot_at_entry=min_candle.spot,
                    spot_at_exit=c.spot,
                    spot_direction=spot_dir,
                )

        # Update minimum
        if p < min_prem:
            min_prem = p
            min_candle = c

    return best_trade


# --- Phase 2: Realistic Best Trades -----------------------------------------

def find_realistic_trade(candles, side):
    """Find best trade with constraints: min hold 6m, max 45m, min prem 80, min swing 10%, directional."""
    if len(candles) < 3:
        return None

    premiums = [(c, c.ce_ltp if side == "CE" else c.pe_ltp) for c in candles]
    premiums = [(c, p) for c, p in premiums if p > 0]
    if len(premiums) < 3:
        return None

    best_trade = None
    best_pnl = -999999

    for i in range(len(premiums)):
        entry_c, entry_p = premiums[i]
        if entry_p < 80:
            continue

        entry_dt = parse_ts(entry_c.timestamp)

        for j in range(i + 1, len(premiums)):
            exit_c, exit_p = premiums[j]
            exit_dt = parse_ts(exit_c.timestamp)

            dur = minutes_between(entry_dt, exit_dt)
            if dur < 6 or dur > 45:
                continue

            pct = (exit_p - entry_p) / entry_p * 100
            if pct < 10:
                continue

            # Direction check: CE for spot up, PE for spot down
            spot_moved_up = exit_c.spot > entry_c.spot
            if side == "CE" and not spot_moved_up:
                continue
            if side == "PE" and spot_moved_up:
                continue

            raw_pnl = (exit_p - entry_p) * LOT_SIZE - BROKERAGE
            if raw_pnl > best_pnl:
                best_pnl = raw_pnl
                best_trade = PerfectTrade(
                    date=entry_dt.strftime("%Y-%m-%d"),
                    side=side,
                    strike=entry_c.ce_strike if side == "CE" else entry_c.pe_strike,
                    entry_time=time_str(entry_dt),
                    exit_time=time_str(exit_dt),
                    duration_min=dur,
                    entry_premium=entry_p,
                    exit_premium=exit_p,
                    pct_gain=pct,
                    pnl_rs=raw_pnl,
                    spot_at_entry=entry_c.spot,
                    spot_at_exit=exit_c.spot,
                    spot_direction="UP" if spot_moved_up else "DOWN",
                )

    return best_trade


# --- Phase 3: Pattern Analysis ----------------------------------------------

@dataclass
class PatternIndicators:
    # Spot behavior
    spot_trending_up_15m: bool = False
    spot_trending_down_15m: bool = False
    spot_ranging_15m: bool = False
    spot_reversing_15m: bool = False
    spot_move_15m: float = 0.0
    spot_move_9m: float = 0.0
    spot_move_6m: float = 0.0
    spot_at_local_low: bool = False
    spot_at_local_high: bool = False

    # OI behavior
    net_ce_oi_chg_15m: float = 0.0
    net_pe_oi_chg_15m: float = 0.0
    oi_shift_bullish: bool = False
    oi_shift_bearish: bool = False
    ce_oi_buildup: int = 0
    pe_oi_buildup: int = 0

    # Premium behavior
    prem_near_daily_low: bool = False
    prem_pct_chg_15m: float = 0.0
    iv_rising: bool = False
    iv_falling: bool = False

    # Analysis data
    vix_level: float = 0.0
    vix_rising: bool = False
    futures_basis_positive: bool = False
    verdict_bullish: bool = False
    verdict_bearish: bool = False
    confidence_high: bool = False

    # Trade outcome
    pnl: float = 0.0
    side: str = ""


def analyze_precursors(candles, trade, all_candles_today):
    """Analyze the 5 candles before a trade entry point."""
    if trade is None:
        return None

    # Find entry candle index
    entry_idx = None
    for i, c in enumerate(candles):
        if time_str(parse_ts(c.timestamp)) == trade.entry_time:
            entry_idx = i
            break

    if entry_idx is None or entry_idx < 2:
        return None

    # Get lookback candles (up to 5, minimum 2)
    lb_start = max(0, entry_idx - 5)
    lookback = candles[lb_start:entry_idx]

    if len(lookback) < 2:
        return None

    entry_c = candles[entry_idx]
    p = PatternIndicators()
    p.pnl = trade.pnl_rs
    p.side = trade.side

    # --- Spot behavior ---
    spots = [c.spot for c in lookback]
    p.spot_move_15m = (spots[-1] - spots[0]) / spots[0] * 100 if len(spots) >= 5 else 0
    p.spot_move_9m = (spots[-1] - spots[-min(3, len(spots))]) / spots[-min(3, len(spots))] * 100
    p.spot_move_6m = (spots[-1] - spots[-min(2, len(spots))]) / spots[-min(2, len(spots))] * 100

    # Trending: consistent direction
    ups = sum(1 for i in range(1, len(spots)) if spots[i] > spots[i-1])
    downs = sum(1 for i in range(1, len(spots)) if spots[i] < spots[i-1])
    total_moves = len(spots) - 1

    if total_moves > 0:
        if ups / total_moves >= 0.7:
            p.spot_trending_up_15m = True
        elif downs / total_moves >= 0.7:
            p.spot_trending_down_15m = True
        else:
            p.spot_ranging_15m = True

    # Reversing: first half opposite to second half
    if len(spots) >= 4:
        mid = len(spots) // 2
        first_half_dir = spots[mid] - spots[0]
        second_half_dir = spots[-1] - spots[mid]
        if (first_half_dir > 0 and second_half_dir < 0) or (first_half_dir < 0 and second_half_dir > 0):
            p.spot_reversing_15m = True

    # Local high/low: compare to all candles in day so far
    prior_spots = [c.spot for c in all_candles_today[:entry_idx]]
    if prior_spots:
        spot_range = max(prior_spots) - min(prior_spots)
        if spot_range > 0:
            spot_pos = (entry_c.spot - min(prior_spots)) / spot_range
            p.spot_at_local_low = spot_pos < 0.2
            p.spot_at_local_high = spot_pos > 0.8

    # --- OI behavior ---
    if len(lookback) >= 2:
        p.net_ce_oi_chg_15m = lookback[-1].ce_oi_change - lookback[0].ce_oi_change
        p.net_pe_oi_chg_15m = lookback[-1].pe_oi_change - lookback[0].pe_oi_change

        # OI shift: PE building faster = bullish support
        if p.net_pe_oi_chg_15m > p.net_ce_oi_chg_15m and p.net_pe_oi_chg_15m > 0:
            p.oi_shift_bullish = True
        if p.net_ce_oi_chg_15m > p.net_pe_oi_chg_15m and p.net_ce_oi_chg_15m > 0:
            p.oi_shift_bearish = True

    p.ce_oi_buildup = entry_c.ce_oi
    p.pe_oi_buildup = entry_c.pe_oi

    # --- Premium behavior ---
    prem_key = "ce_ltp" if trade.side == "CE" else "pe_ltp"
    prior_prems = [getattr(c, prem_key) for c in all_candles_today[:entry_idx] if getattr(c, prem_key) > 0]
    entry_prem = getattr(entry_c, prem_key)

    if prior_prems and min(prior_prems) > 0:
        daily_low = min(prior_prems)
        p.prem_near_daily_low = (entry_prem - daily_low) / daily_low < 0.05

    lb_prems = [getattr(c, prem_key) for c in lookback if getattr(c, prem_key) > 0]
    if lb_prems and lb_prems[0] > 0:
        p.prem_pct_chg_15m = (lb_prems[-1] - lb_prems[0]) / lb_prems[0] * 100

    # IV direction
    iv_key = "ce_iv" if trade.side == "CE" else "pe_iv"
    lb_ivs = [getattr(c, iv_key) for c in lookback if getattr(c, iv_key) > 0]
    if len(lb_ivs) >= 2:
        p.iv_rising = lb_ivs[-1] > lb_ivs[0]
        p.iv_falling = lb_ivs[-1] < lb_ivs[0]

    # --- Analysis data ---
    p.vix_level = entry_c.vix
    if len(lookback) >= 2 and lookback[0].vix > 0:
        p.vix_rising = lookback[-1].vix > lookback[0].vix

    p.futures_basis_positive = entry_c.futures_basis > 0

    verdict_lower = entry_c.verdict.lower()
    p.verdict_bullish = "bull" in verdict_lower
    p.verdict_bearish = "bear" in verdict_lower
    p.confidence_high = entry_c.signal_confidence >= 70

    return p


def compute_pattern_stats(patterns):
    """Compute pattern frequency table."""
    if not patterns:
        return {}

    indicators = [
        ("spot_trending_up_15m", "Spot trending UP (15m)"),
        ("spot_trending_down_15m", "Spot trending DOWN (15m)"),
        ("spot_ranging_15m", "Spot ranging (15m)"),
        ("spot_reversing_15m", "Spot reversing (15m)"),
        ("spot_at_local_low", "Spot at local LOW (<20%)"),
        ("spot_at_local_high", "Spot at local HIGH (>80%)"),
        ("oi_shift_bullish", "OI shift BULLISH (PE building)"),
        ("oi_shift_bearish", "OI shift BEARISH (CE building)"),
        ("prem_near_daily_low", "Premium near daily LOW (<5%)"),
        ("iv_rising", "IV rising"),
        ("iv_falling", "IV falling"),
        ("vix_rising", "VIX rising"),
        ("futures_basis_positive", "Futures basis positive"),
        ("verdict_bullish", "Verdict bullish"),
        ("verdict_bearish", "Verdict bearish"),
        ("confidence_high", "Confidence >= 70"),
    ]

    # Also add continuous indicators as buckets
    continuous = [
        ("spot_move_15m_pos", "Spot move 15m > 0", lambda p: p.spot_move_15m > 0),
        ("spot_move_15m_neg", "Spot move 15m < 0", lambda p: p.spot_move_15m < 0),
        ("spot_move_15m_big_pos", "Spot move 15m > +0.1%", lambda p: p.spot_move_15m > 0.1),
        ("spot_move_15m_big_neg", "Spot move 15m < -0.1%", lambda p: p.spot_move_15m < -0.1),
        ("prem_falling_15m", "Premium falling 15m (< -3%)", lambda p: p.prem_pct_chg_15m < -3),
        ("prem_rising_15m", "Premium rising 15m (> +3%)", lambda p: p.prem_pct_chg_15m > 3),
        ("ce_trade_spot_up", "CE trade + spot trending up", lambda p: p.side == "CE" and p.spot_trending_up_15m),
        ("pe_trade_spot_down", "PE trade + spot trending down", lambda p: p.side == "PE" and p.spot_trending_down_15m),
        ("prem_dip_then_entry", "Prem near low + spot at low", lambda p: p.prem_near_daily_low and p.spot_at_local_low),
        ("aligned_ce", "CE: OI bullish + verdict bull", lambda p: p.side == "CE" and p.oi_shift_bullish and p.verdict_bullish),
        ("aligned_pe", "PE: OI bearish + verdict bear", lambda p: p.side == "PE" and p.oi_shift_bearish and p.verdict_bearish),
        ("contrarian_ce", "CE: Premium falling + spot at low", lambda p: p.side == "CE" and p.prem_pct_chg_15m < -3 and p.spot_at_local_low),
        ("contrarian_pe", "PE: Premium falling + spot at high", lambda p: p.side == "PE" and p.prem_pct_chg_15m < -3 and p.spot_at_local_high),
        ("high_vix", "VIX > 14", lambda p: p.vix_level > 14),
        ("low_vix", "VIX <= 14", lambda p: p.vix_level > 0 and p.vix_level <= 14),
    ]

    results = {}
    total = len(patterns)

    for attr, label in indicators:
        present = [p for p in patterns if getattr(p, attr)]
        count = len(present)
        avg_pnl = sum(p.pnl for p in present) / count if count > 0 else 0
        results[label] = {
            "count": count,
            "pct": count / total * 100,
            "avg_pnl": avg_pnl,
        }

    for key, label, fn in continuous:
        present = [p for p in patterns if fn(p)]
        count = len(present)
        avg_pnl = sum(p.pnl for p in present) / count if count > 0 else 0
        results[label] = {
            "count": count,
            "pct": count / total * 100,
            "avg_pnl": avg_pnl,
        }

    return results


# --- Phase 4: Quality Score Model -------------------------------------------

def compute_quality_score(candles, idx, pattern_weights):
    """Compute quality score for a candle based on precursor indicators."""
    if idx < 5:
        return 0.0, None

    lookback = candles[max(0, idx - 5):idx]
    entry_c = candles[idx]

    if len(lookback) < 2:
        return 0.0, None

    score = 0.0
    components = {}

    # Spot trends
    spots = [c.spot for c in lookback]
    ups = sum(1 for i in range(1, len(spots)) if spots[i] > spots[i-1])
    downs = sum(1 for i in range(1, len(spots)) if spots[i] < spots[i-1])
    total_moves = len(spots) - 1

    spot_trending_up = ups / total_moves >= 0.7 if total_moves > 0 else False
    spot_trending_down = downs / total_moves >= 0.7 if total_moves > 0 else False

    # Spot move magnitudes
    spot_move_15m = (spots[-1] - spots[0]) / spots[0] * 100 if spots[0] > 0 else 0

    # Spot position in day range
    prior_spots = [c.spot for c in candles[:idx]]
    spot_pos = 0.5
    if prior_spots:
        sr = max(prior_spots) - min(prior_spots)
        if sr > 0:
            spot_pos = (entry_c.spot - min(prior_spots)) / sr

    spot_at_low = spot_pos < 0.2
    spot_at_high = spot_pos > 0.8

    # OI shifts
    ce_oi_delta = lookback[-1].ce_oi_change - lookback[0].ce_oi_change
    pe_oi_delta = lookback[-1].pe_oi_change - lookback[0].pe_oi_change
    oi_bullish = pe_oi_delta > ce_oi_delta and pe_oi_delta > 0
    oi_bearish = ce_oi_delta > pe_oi_delta and ce_oi_delta > 0

    # Premium near daily low
    ce_prems = [c.ce_ltp for c in candles[:idx] if c.ce_ltp > 0]
    pe_prems = [c.pe_ltp for c in candles[:idx] if c.pe_ltp > 0]

    ce_near_low = False
    pe_near_low = False
    if ce_prems and min(ce_prems) > 0:
        ce_near_low = (entry_c.ce_ltp - min(ce_prems)) / min(ce_prems) < 0.05
    if pe_prems and min(pe_prems) > 0:
        pe_near_low = (entry_c.pe_ltp - min(pe_prems)) / min(pe_prems) < 0.05

    # Premium trend
    lb_ce_prems = [c.ce_ltp for c in lookback if c.ce_ltp > 0]
    lb_pe_prems = [c.pe_ltp for c in lookback if c.pe_ltp > 0]
    ce_prem_chg = (lb_ce_prems[-1] - lb_ce_prems[0]) / lb_ce_prems[0] * 100 if len(lb_ce_prems) >= 2 and lb_ce_prems[0] > 0 else 0
    pe_prem_chg = (lb_pe_prems[-1] - lb_pe_prems[0]) / lb_pe_prems[0] * 100 if len(lb_pe_prems) >= 2 and lb_pe_prems[0] > 0 else 0

    # Verdict
    verdict_lower = entry_c.verdict.lower()
    verdict_bull = "bull" in verdict_lower
    verdict_bear = "bear" in verdict_lower

    # --- CE score ---
    ce_score = 0.0
    # Momentum: spot trending up
    if spot_trending_up:
        ce_score += pattern_weights.get("momentum", 2.0)
    # Dip: premium was falling then stopped (near low)
    if ce_near_low:
        ce_score += pattern_weights.get("prem_near_low", 3.0)
    # Premium dip (falling recently - about to bounce)
    if ce_prem_chg < -3:
        ce_score += pattern_weights.get("prem_falling", 1.5)
    # OI support: put writing building (bullish)
    if oi_bullish:
        ce_score += pattern_weights.get("oi_aligned", 2.0)
    # Verdict aligned
    if verdict_bull:
        ce_score += pattern_weights.get("verdict_aligned", 1.0)
    # Spot at local low (mean reversion potential)
    if spot_at_low:
        ce_score += pattern_weights.get("spot_at_extreme", 2.5)
    # High confidence
    if entry_c.signal_confidence >= 70:
        ce_score += pattern_weights.get("high_confidence", 1.0)
    # Spot momentum magnitude
    if spot_move_15m > 0.1:
        ce_score += pattern_weights.get("strong_momentum", 1.5)

    # --- PE score ---
    pe_score = 0.0
    if spot_trending_down:
        pe_score += pattern_weights.get("momentum", 2.0)
    if pe_near_low:
        pe_score += pattern_weights.get("prem_near_low", 3.0)
    if pe_prem_chg < -3:
        pe_score += pattern_weights.get("prem_falling", 1.5)
    if oi_bearish:
        pe_score += pattern_weights.get("oi_aligned", 2.0)
    if verdict_bear:
        pe_score += pattern_weights.get("verdict_aligned", 1.0)
    if spot_at_high:
        pe_score += pattern_weights.get("spot_at_extreme", 2.5)
    if entry_c.signal_confidence >= 70:
        pe_score += pattern_weights.get("high_confidence", 1.0)
    if spot_move_15m < -0.1:
        pe_score += pattern_weights.get("strong_momentum", 1.5)

    # Return best side
    if ce_score >= pe_score:
        return ce_score, "CE"
    else:
        return pe_score, "PE"


def simulate_trade(candles, entry_idx, side, sl_pct=-10, target_pct=20,
                   trail1_trigger=10, trail1_sl=4, trail2_trigger=15, trail2_sl=10,
                   max_hold_flat=30, max_hold_forced=45, min_premium=100):
    """Simulate a single trade with SL/target/trailing/time exits."""
    entry_c = candles[entry_idx]
    entry_prem = entry_c.ce_ltp if side == "CE" else entry_c.pe_ltp

    if entry_prem < min_premium:
        return None

    entry_dt = parse_ts(entry_c.timestamp)
    strike = entry_c.ce_strike if side == "CE" else entry_c.pe_strike

    sl_price = entry_prem * (1 + sl_pct / 100)
    target_price = entry_prem * (1 + target_pct / 100)

    current_sl = sl_price
    peak_prem = entry_prem

    for j in range(entry_idx + 1, len(candles)):
        c = candles[j]
        cur_dt = parse_ts(c.timestamp)
        hold_min = minutes_between(entry_dt, cur_dt)
        cur_prem = c.ce_ltp if side == "CE" else c.pe_ltp

        if cur_prem <= 0:
            continue

        # Update peak
        if cur_prem > peak_prem:
            peak_prem = cur_prem

        # Trailing SL updates
        pct_from_entry = (peak_prem - entry_prem) / entry_prem * 100
        if pct_from_entry >= trail2_trigger:
            new_sl = entry_prem * (1 + trail2_sl / 100)
            current_sl = max(current_sl, new_sl)
        elif pct_from_entry >= trail1_trigger:
            new_sl = entry_prem * (1 + trail1_sl / 100)
            current_sl = max(current_sl, new_sl)

        # Check target hit
        if cur_prem >= target_price:
            pnl = (cur_prem - entry_prem) * LOT_SIZE - BROKERAGE
            return SimTrade(
                date=entry_dt.strftime("%Y-%m-%d"),
                side=side, strike=strike,
                entry_time=time_str(entry_dt),
                exit_time=time_str(cur_dt),
                entry_premium=entry_prem, exit_premium=cur_prem,
                pct_gain=(cur_prem - entry_prem) / entry_prem * 100,
                pnl_rs=pnl, exit_reason="TARGET",
                spot_at_entry=entry_c.spot, spot_at_exit=c.spot,
            )

        # Check SL hit
        if cur_prem <= current_sl:
            pnl = (cur_prem - entry_prem) * LOT_SIZE - BROKERAGE
            reason = "TRAIL_SL" if current_sl > sl_price else "SL"
            return SimTrade(
                date=entry_dt.strftime("%Y-%m-%d"),
                side=side, strike=strike,
                entry_time=time_str(entry_dt),
                exit_time=time_str(cur_dt),
                entry_premium=entry_prem, exit_premium=cur_prem,
                pct_gain=(cur_prem - entry_prem) / entry_prem * 100,
                pnl_rs=pnl, exit_reason=reason,
                spot_at_entry=entry_c.spot, spot_at_exit=c.spot,
            )

        # Time exits
        if hold_min >= max_hold_forced:
            pnl = (cur_prem - entry_prem) * LOT_SIZE - BROKERAGE
            return SimTrade(
                date=entry_dt.strftime("%Y-%m-%d"),
                side=side, strike=strike,
                entry_time=time_str(entry_dt),
                exit_time=time_str(cur_dt),
                entry_premium=entry_prem, exit_premium=cur_prem,
                pct_gain=(cur_prem - entry_prem) / entry_prem * 100,
                pnl_rs=pnl, exit_reason="TIME_45M",
                spot_at_entry=entry_c.spot, spot_at_exit=c.spot,
            )

        if hold_min >= max_hold_flat:
            # Check if still profitable
            pct_now = (cur_prem - entry_prem) / entry_prem * 100
            if pct_now < 5:  # Not making enough progress
                pnl = (cur_prem - entry_prem) * LOT_SIZE - BROKERAGE
                return SimTrade(
                    date=entry_dt.strftime("%Y-%m-%d"),
                    side=side, strike=strike,
                    entry_time=time_str(entry_dt),
                    exit_time=time_str(cur_dt),
                    entry_premium=entry_prem, exit_premium=cur_prem,
                    pct_gain=(cur_prem - entry_prem) / entry_prem * 100,
                    pnl_rs=pnl, exit_reason="TIME_30M_FLAT",
                    spot_at_entry=entry_c.spot, spot_at_exit=c.spot,
                )

    # End of day - force exit at last candle
    last_c = candles[-1]
    last_prem = last_c.ce_ltp if side == "CE" else last_c.pe_ltp
    if last_prem > 0:
        pnl = (last_prem - entry_prem) * LOT_SIZE - BROKERAGE
        return SimTrade(
            date=entry_dt.strftime("%Y-%m-%d"),
            side=side, strike=strike,
            entry_time=time_str(entry_dt),
            exit_time=time_str(parse_ts(last_c.timestamp)),
            entry_premium=entry_prem, exit_premium=last_prem,
            pct_gain=(last_prem - entry_prem) / entry_prem * 100,
            pnl_rs=pnl, exit_reason="EOD",
            spot_at_entry=entry_c.spot, spot_at_exit=last_c.spot,
        )

    return None


def run_quality_score_strategy(all_days_candles, pattern_weights, threshold_pct):
    """Run quality score strategy across all days."""
    # First compute all scores
    all_scores = []
    for date_str, candles in all_days_candles.items():
        for i in range(5, len(candles)):
            dt = parse_ts(candles[i].timestamp)
            t = dt.strftime("%H:%M")
            if t > ENTRY_CUTOFF:
                continue
            score, side = compute_quality_score(candles, i, pattern_weights)
            if score > 0:
                all_scores.append(score)

    if not all_scores:
        return [], 0

    all_scores.sort(reverse=True)
    threshold_idx = max(1, int(len(all_scores) * threshold_pct / 100))
    threshold_val = all_scores[min(threshold_idx, len(all_scores) - 1)]

    # Now simulate
    trades = []
    for date_str, candles in sorted(all_days_candles.items()):
        day_trades = 0
        in_trade = False
        cooldown_until = None

        for i in range(5, len(candles)):
            if day_trades >= 2:
                break

            dt = parse_ts(candles[i].timestamp)
            t = dt.strftime("%H:%M")
            if t > ENTRY_CUTOFF:
                break

            if in_trade:
                continue

            if cooldown_until and dt < cooldown_until:
                continue

            score, side = compute_quality_score(candles, i, pattern_weights)

            if score >= threshold_val and side:
                trade = simulate_trade(candles, i, side)
                if trade:
                    trade.quality_score = score
                    trades.append(trade)
                    day_trades += 1

                    # Set cooldown
                    exit_dt = parse_ts(f"{date_str}T{trade.exit_time}:00")
                    cooldown_until = exit_dt + timedelta(minutes=6)
                    in_trade = False  # Trade already resolved

    return trades, threshold_val


# --- Phase 5: Premium Dip Strategy ------------------------------------------

def run_premium_dip_strategy(all_days_candles):
    """Wait for premium to dip then bounce back by 5%."""
    trades = []

    for date_str, candles in sorted(all_days_candles.items()):
        if len(candles) < 10:
            continue

        day_trades = 0
        in_trade = False
        cooldown_until = None

        # Track running lows for CE and PE
        ce_running_low = candles[0].ce_ltp if candles[0].ce_ltp > 0 else 99999
        pe_running_low = candles[0].pe_ltp if candles[0].pe_ltp > 0 else 99999
        ce_low_idx = 0
        pe_low_idx = 0

        for i in range(1, len(candles)):
            if day_trades >= 2:
                break

            c = candles[i]
            dt = parse_ts(c.timestamp)
            t = dt.strftime("%H:%M")

            if t > ENTRY_CUTOFF:
                break

            if in_trade:
                continue

            if cooldown_until and dt < cooldown_until:
                # Still update lows
                if c.ce_ltp > 0 and c.ce_ltp < ce_running_low:
                    ce_running_low = c.ce_ltp
                    ce_low_idx = i
                if c.pe_ltp > 0 and c.pe_ltp < pe_running_low:
                    pe_running_low = c.pe_ltp
                    pe_low_idx = i
                continue

            # Update running lows
            if c.ce_ltp > 0 and c.ce_ltp < ce_running_low:
                ce_running_low = c.ce_ltp
                ce_low_idx = i
            if c.pe_ltp > 0 and c.pe_ltp < pe_running_low:
                pe_running_low = c.pe_ltp
                pe_low_idx = i

            # Check CE bounce: premium bounced 5% from its low
            ce_bounce = False
            pe_bounce = False

            if ce_running_low > 0 and c.ce_ltp > 0 and ce_running_low >= 100:
                ce_bounce_pct = (c.ce_ltp - ce_running_low) / ce_running_low * 100
                if ce_bounce_pct >= 5 and i > ce_low_idx:
                    ce_bounce = True

            if pe_running_low > 0 and c.pe_ltp > 0 and pe_running_low >= 100:
                pe_bounce_pct = (c.pe_ltp - pe_running_low) / pe_running_low * 100
                if pe_bounce_pct >= 5 and i > pe_low_idx:
                    pe_bounce = True

            # Pick the one with bigger bounce
            entry_side = None
            if ce_bounce and pe_bounce:
                ce_b = (c.ce_ltp - ce_running_low) / ce_running_low * 100
                pe_b = (c.pe_ltp - pe_running_low) / pe_running_low * 100
                entry_side = "CE" if ce_b >= pe_b else "PE"
            elif ce_bounce:
                entry_side = "CE"
            elif pe_bounce:
                entry_side = "PE"

            if entry_side:
                trade = simulate_trade(candles, i, entry_side)
                if trade:
                    trades.append(trade)
                    day_trades += 1

                    exit_dt = parse_ts(f"{date_str}T{trade.exit_time}:00")
                    cooldown_until = exit_dt + timedelta(minutes=6)

                    # Reset lows after trade
                    if entry_side == "CE":
                        ce_running_low = 99999
                    else:
                        pe_running_low = 99999

    return trades


# --- Reporting ---------------------------------------------------------------

def print_separator(char="=", width=120):
    print(char * width)


def print_header(title, width=120):
    print()
    print_separator("=", width)
    padding = (width - len(title) - 4) // 2
    print(f"{'=' * padding}  {title}  {'=' * (width - padding - len(title) - 4)}")
    print_separator("=", width)


def format_rs(val):
    sign = "+" if val >= 0 else ""
    return f"{sign}Rs {val:,.0f}"


def print_trade_stats(trades, label=""):
    if not trades:
        print(f"  No trades{f' for {label}' if label else ''}.")
        return

    wins = [t for t in trades if t.pnl_rs > 0]
    losses = [t for t in trades if t.pnl_rs <= 0]
    total_pnl = sum(t.pnl_rs for t in trades)
    gross_profit = sum(t.pnl_rs for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_rs for t in losses)) if losses else 0
    wr = len(wins) / len(trades) * 100
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    avg_win = sum(t.pnl_rs for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_rs for t in losses) / len(losses) if losses else 0

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t.pnl_rs
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Sharpe (daily P&L based)
    daily_pnl = defaultdict(float)
    for t in trades:
        daily_pnl[t.date] += t.pnl_rs
    daily_vals = list(daily_pnl.values())
    if len(daily_vals) > 1:
        mean_d = sum(daily_vals) / len(daily_vals)
        std_d = (sum((x - mean_d)**2 for x in daily_vals) / (len(daily_vals) - 1)) ** 0.5
        sharpe = (mean_d / std_d * (252 ** 0.5)) if std_d > 0 else 0
    else:
        sharpe = 0

    # Exit reason breakdown
    exit_reasons = defaultdict(int)
    for t in trades:
        exit_reasons[t.exit_reason] += 1

    print(f"  Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)} | WR: {wr:.1f}%")
    print(f"  Net P&L: {format_rs(total_pnl)} | Avg Win: {format_rs(avg_win)} | Avg Loss: {format_rs(avg_loss)}")
    print(f"  PF: {pf:.2f} | Max DD: Rs {max_dd:,.0f} | Sharpe: {sharpe:.2f}")
    print(f"  Exit reasons: {dict(exit_reasons)}")


# --- Main --------------------------------------------------------------------

def main():
    conn = get_connection()
    days = get_trading_days(conn)
    print(f"Loading data for {len(days)} trading days: {days[0]} to {days[-1]}")

    # Load all day candles
    all_days_candles = {}
    for d in days:
        candles = load_day_candles(conn, d)
        if candles:
            all_days_candles[d] = candles
            print(f"  {d}: {len(candles)} candles, spot {candles[0].spot:.0f}->{candles[-1].spot:.0f}, "
                  f"CE@{candles[0].ce_strike} PE@{candles[0].pe_strike}")

    print(f"\nLoaded {len(all_days_candles)} days with valid data.\n")

    # =======================================================================
    # PHASE 1: Perfect Trades
    # =======================================================================
    print_header("PHASE 1: PERFECT TRADES (Theoretical Maximum)")

    perfect_ce = {}
    perfect_pe = {}

    print(f"\n{'Date':<12} {'Best CE Trade':<55} {'Best PE Trade':<55}")
    print(f"{'':-<12} {'':-<55} {'':-<55}")

    total_perfect_pnl = 0

    for d, candles in sorted(all_days_candles.items()):
        ce_trade = find_perfect_trade(candles, "CE")
        pe_trade = find_perfect_trade(candles, "PE")
        perfect_ce[d] = ce_trade
        perfect_pe[d] = pe_trade

        ce_str = "—"
        pe_str = "—"
        day_best = 0

        if ce_trade and ce_trade.pnl_rs > 0:
            ce_str = (f"{ce_trade.entry_time}->{ce_trade.exit_time} "
                     f"{ce_trade.entry_premium:.0f}->{ce_trade.exit_premium:.0f} "
                     f"({ce_trade.pct_gain:+.0f}%) {format_rs(ce_trade.pnl_rs)}")
            day_best = max(day_best, ce_trade.pnl_rs)

        if pe_trade and pe_trade.pnl_rs > 0:
            pe_str = (f"{pe_trade.entry_time}->{pe_trade.exit_time} "
                     f"{pe_trade.entry_premium:.0f}->{pe_trade.exit_premium:.0f} "
                     f"({pe_trade.pct_gain:+.0f}%) {format_rs(pe_trade.pnl_rs)}")
            day_best = max(day_best, pe_trade.pnl_rs)

        # Take best of CE/PE each day
        best_daily = max(
            ce_trade.pnl_rs if ce_trade and ce_trade.pnl_rs > 0 else 0,
            pe_trade.pnl_rs if pe_trade and pe_trade.pnl_rs > 0 else 0,
        )
        total_perfect_pnl += best_daily

        print(f"{d:<12} {ce_str:<55} {pe_str:<55}")

    avg_daily = total_perfect_pnl / len(all_days_candles)
    print(f"\n  SUMMARY: Total perfect P&L (best of CE/PE per day): {format_rs(total_perfect_pnl)}")
    print(f"  Average daily max possible: {format_rs(avg_daily)}")

    # =======================================================================
    # PHASE 2: Realistic Best Trades
    # =======================================================================
    print_header("PHASE 2: REALISTIC BEST TRADES (With Constraints)")
    print("  Constraints: hold 6-45min, premium >= Rs 80, swing >= 10%, directional only\n")

    realistic_trades = {}

    print(f"{'Date':<12} {'Best CE Trade':<55} {'Best PE Trade':<55}")
    print(f"{'':-<12} {'':-<55} {'':-<55}")

    total_realistic_pnl = 0
    all_realistic = []

    for d, candles in sorted(all_days_candles.items()):
        ce_trade = find_realistic_trade(candles, "CE")
        pe_trade = find_realistic_trade(candles, "PE")

        ce_str = "—"
        pe_str = "—"

        if ce_trade and ce_trade.pnl_rs > 0:
            ce_str = (f"{ce_trade.entry_time}->{ce_trade.exit_time} "
                     f"{ce_trade.entry_premium:.0f}->{ce_trade.exit_premium:.0f} "
                     f"({ce_trade.pct_gain:+.0f}%) {format_rs(ce_trade.pnl_rs)} "
                     f"[{ce_trade.duration_min:.0f}m]")

        if pe_trade and pe_trade.pnl_rs > 0:
            pe_str = (f"{pe_trade.entry_time}->{pe_trade.exit_time} "
                     f"{pe_trade.entry_premium:.0f}->{pe_trade.exit_premium:.0f} "
                     f"({pe_trade.pct_gain:+.0f}%) {format_rs(pe_trade.pnl_rs)} "
                     f"[{pe_trade.duration_min:.0f}m]")

        # Take best of CE/PE
        best = None
        if ce_trade and ce_trade.pnl_rs > 0 and pe_trade and pe_trade.pnl_rs > 0:
            best = ce_trade if ce_trade.pnl_rs >= pe_trade.pnl_rs else pe_trade
        elif ce_trade and ce_trade.pnl_rs > 0:
            best = ce_trade
        elif pe_trade and pe_trade.pnl_rs > 0:
            best = pe_trade

        if best:
            realistic_trades[d] = best
            total_realistic_pnl += best.pnl_rs
            all_realistic.append(best)

        print(f"{d:<12} {ce_str:<55} {pe_str:<55}")

    avg_realistic = total_realistic_pnl / len(all_days_candles) if all_days_candles else 0
    days_with_trade = len(realistic_trades)
    print(f"\n  SUMMARY: Realistic P&L (best per day): {format_rs(total_realistic_pnl)}")
    print(f"  Days with valid trade: {days_with_trade}/{len(all_days_candles)}")
    print(f"  Average daily (all days): {format_rs(avg_realistic)}")
    print(f"  Average daily (trade days): {format_rs(total_realistic_pnl / days_with_trade) if days_with_trade > 0 else 'N/A'}")
    if all_realistic:
        avg_dur = sum(t.duration_min for t in all_realistic) / len(all_realistic)
        avg_pct = sum(t.pct_gain for t in all_realistic) / len(all_realistic)
        ce_count = sum(1 for t in all_realistic if t.side == "CE")
        pe_count = sum(1 for t in all_realistic if t.side == "PE")
        print(f"  Avg duration: {avg_dur:.0f}min | Avg gain: {avg_pct:.1f}% | CE: {ce_count} | PE: {pe_count}")

    # =======================================================================
    # PHASE 3: Pattern Analysis
    # =======================================================================
    print_header("PHASE 3: PATTERN ANALYSIS (What preceded the best trades?)")

    all_patterns = []

    for d, trade in realistic_trades.items():
        candles = all_days_candles[d]
        pattern = analyze_precursors(candles, trade, candles)
        if pattern:
            all_patterns.append(pattern)

    print(f"\n  Analyzed {len(all_patterns)} realistic best trades.\n")

    if all_patterns:
        stats = compute_pattern_stats(all_patterns)

        # Sort by avg P&L descending
        sorted_stats = sorted(stats.items(), key=lambda x: x[1]["avg_pnl"], reverse=True)

        print(f"  {'Indicator':<45} {'Count':>6} {'% Present':>10} {'Avg P&L':>12}")
        print(f"  {'':-<45} {'':->6} {'':->10} {'':->12}")

        for label, s in sorted_stats:
            if s["count"] > 0:
                print(f"  {label:<45} {s['count']:>6} {s['pct']:>9.1f}% {format_rs(s['avg_pnl']):>12}")

        # Top 10 most predictive (by avg P&L, min 3 occurrences)
        print(f"\n  TOP 10 MOST PREDICTIVE (min 3 occurrences):")
        print(f"  {'Rank':<6} {'Indicator':<45} {'Count':>6} {'Avg P&L':>12}")
        print(f"  {'':-<6} {'':-<45} {'':->6} {'':->12}")

        top10 = [(l, s) for l, s in sorted_stats if s["count"] >= 3][:10]
        for rank, (label, s) in enumerate(top10, 1):
            print(f"  {rank:<6} {label:<45} {s['count']:>6} {format_rs(s['avg_pnl']):>12}")

        # Continuous variable summaries
        print(f"\n  CONTINUOUS VARIABLE DISTRIBUTIONS:")
        spot_moves = [p.spot_move_15m for p in all_patterns]
        prem_chgs = [p.prem_pct_chg_15m for p in all_patterns]
        vix_levels = [p.vix_level for p in all_patterns if p.vix_level > 0]

        if spot_moves:
            print(f"  Spot move 15m: mean={sum(spot_moves)/len(spot_moves):.3f}%, "
                  f"min={min(spot_moves):.3f}%, max={max(spot_moves):.3f}%")
        if prem_chgs:
            print(f"  Prem change 15m: mean={sum(prem_chgs)/len(prem_chgs):.1f}%, "
                  f"min={min(prem_chgs):.1f}%, max={max(prem_chgs):.1f}%")
        if vix_levels:
            print(f"  VIX levels: mean={sum(vix_levels)/len(vix_levels):.1f}, "
                  f"min={min(vix_levels):.1f}, max={max(vix_levels):.1f}")

    # =======================================================================
    # PHASE 4: Quality Score Model
    # =======================================================================
    print_header("PHASE 4: QUALITY SCORE MODEL")

    # Build weights from Phase 3 findings
    pattern_weights = {
        "momentum": 2.0,
        "prem_near_low": 3.0,
        "prem_falling": 1.5,
        "oi_aligned": 2.0,
        "verdict_aligned": 1.0,
        "spot_at_extreme": 2.5,
        "high_confidence": 1.0,
        "strong_momentum": 1.5,
    }

    # Try different thresholds
    thresholds = [5, 10, 15, 20]

    print(f"\n  {'Threshold':>10} {'Trades':>7} {'Wins':>6} {'WR':>7} {'Net P&L':>12} {'Max DD':>10} "
          f"{'PF':>7} {'Sharpe':>8} {'Avg/Trade':>10}")
    print(f"  {'':->10} {'':->7} {'':->6} {'':->7} {'':->12} {'':->10} {'':->7} {'':->8} {'':->10}")

    best_threshold = None
    best_threshold_pnl = -999999
    best_threshold_trades = []

    for thr in thresholds:
        trades, thr_val = run_quality_score_strategy(all_days_candles, pattern_weights, thr)

        if not trades:
            print(f"  {'Top ' + str(thr) + '%':>10} {'0':>7}")
            continue

        wins = [t for t in trades if t.pnl_rs > 0]
        losses = [t for t in trades if t.pnl_rs <= 0]
        total_pnl = sum(t.pnl_rs for t in trades)
        wr = len(wins) / len(trades) * 100

        gross_profit = sum(t.pnl_rs for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl_rs for t in losses)) if losses else 1
        pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Max DD
        cum = 0
        peak = 0
        max_dd = 0
        for t in trades:
            cum += t.pnl_rs
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        # Sharpe
        daily_pnl = defaultdict(float)
        for t in trades:
            daily_pnl[t.date] += t.pnl_rs
        daily_vals = list(daily_pnl.values())
        if len(daily_vals) > 1:
            mean_d = sum(daily_vals) / len(daily_vals)
            std_d = (sum((x - mean_d)**2 for x in daily_vals) / (len(daily_vals) - 1)) ** 0.5
            sharpe = (mean_d / std_d * (252 ** 0.5)) if std_d > 0 else 0
        else:
            sharpe = 0

        avg_per_trade = total_pnl / len(trades)

        print(f"  {'Top ' + str(thr) + '%':>10} {len(trades):>7} {len(wins):>6} {wr:>6.1f}% "
              f"{format_rs(total_pnl):>12} {f'Rs {max_dd:,.0f}':>10} {pf:>7.2f} {sharpe:>8.2f} "
              f"{format_rs(avg_per_trade):>10}")

        if total_pnl > best_threshold_pnl:
            best_threshold_pnl = total_pnl
            best_threshold = thr
            best_threshold_trades = trades

    # Print best threshold details
    if best_threshold_trades:
        print(f"\n  BEST THRESHOLD: Top {best_threshold}% — Detailed Results:")
        print(f"\n  {'Date':<12} {'#':>3} {'Side':<5} {'Strike':>7} {'Entry':>6} {'Exit':>6} "
              f"{'Entry->Exit':>12} {'Prem%':>7} {'P&L':>10} {'Exit Reason':<15} {'Score':>6}")
        print(f"  {'':-<12} {'':->3} {'':-<5} {'':->7} {'':->6} {'':->6} "
              f"{'':->12} {'':->7} {'':->10} {'':-<15} {'':->6}")

        cum_pnl = 0
        for i, t in enumerate(best_threshold_trades, 1):
            cum_pnl += t.pnl_rs
            prem_str = f"{t.entry_premium:.0f}->{t.exit_premium:.0f}"
            print(f"  {t.date:<12} {i:>3} {t.side:<5} {t.strike:>7} {t.entry_time:>6} {t.exit_time:>6} "
                  f"{prem_str:>12} {t.pct_gain:>+6.1f}% {format_rs(t.pnl_rs):>10} "
                  f"{t.exit_reason:<15} {t.quality_score:>6.1f}")

        print(f"\n  Cumulative P&L: {format_rs(cum_pnl)}")
        print_trade_stats(best_threshold_trades, f"Quality Score Top {best_threshold}%")

        # Daily P&L
        print(f"\n  DAILY P&L:")
        daily_pnl = defaultdict(float)
        daily_count = defaultdict(int)
        for t in best_threshold_trades:
            daily_pnl[t.date] += t.pnl_rs
            daily_count[t.date] += 1

        cum = 0
        for d in sorted(daily_pnl.keys()):
            cum += daily_pnl[d]
            print(f"  {d}: {daily_count[d]} trades, {format_rs(daily_pnl[d]):<12} (cum: {format_rs(cum)})")

    # =======================================================================
    # PHASE 5: Premium Dip Strategy
    # =======================================================================
    print_header("PHASE 5: PREMIUM DIP STRATEGY (Wait for Bounce)")
    print("  Logic: Wait for premium to hit daily low, then bounce 5% -> enter\n")

    dip_trades = run_premium_dip_strategy(all_days_candles)

    if dip_trades:
        print(f"  {'Date':<12} {'#':>3} {'Side':<5} {'Strike':>7} {'Entry':>6} {'Exit':>6} "
              f"{'Entry->Exit':>12} {'Prem%':>7} {'P&L':>10} {'Exit Reason':<15}")
        print(f"  {'':-<12} {'':->3} {'':-<5} {'':->7} {'':->6} {'':->6} "
              f"{'':->12} {'':->7} {'':->10} {'':-<15}")

        cum_pnl = 0
        for i, t in enumerate(dip_trades, 1):
            cum_pnl += t.pnl_rs
            prem_str = f"{t.entry_premium:.0f}->{t.exit_premium:.0f}"
            print(f"  {t.date:<12} {i:>3} {t.side:<5} {t.strike:>7} {t.entry_time:>6} {t.exit_time:>6} "
                  f"{prem_str:>12} {t.pct_gain:>+6.1f}% {format_rs(t.pnl_rs):>10} "
                  f"{t.exit_reason:<15}")

        print()
        print_trade_stats(dip_trades, "Premium Dip")

        # Daily P&L
        print(f"\n  DAILY P&L:")
        daily_pnl = defaultdict(float)
        daily_count = defaultdict(int)
        for t in dip_trades:
            daily_pnl[t.date] += t.pnl_rs
            daily_count[t.date] += 1

        cum = 0
        for d in sorted(daily_pnl.keys()):
            cum += daily_pnl[d]
            print(f"  {d}: {daily_count[d]} trades, {format_rs(daily_pnl[d]):<12} (cum: {format_rs(cum)})")
    else:
        print("  No trades generated.")

    # =======================================================================
    # FINAL COMPARISON
    # =======================================================================
    print_header("FINAL COMPARISON")

    strategies = {
        "Perfect (Phase 1)": total_perfect_pnl,
        "Realistic (Phase 2)": total_realistic_pnl,
    }

    # Quality Score
    if best_threshold_trades:
        qs_pnl = sum(t.pnl_rs for t in best_threshold_trades)
        qs_wins = sum(1 for t in best_threshold_trades if t.pnl_rs > 0)
        qs_wr = qs_wins / len(best_threshold_trades) * 100 if best_threshold_trades else 0
        strategies[f"Quality Score Top {best_threshold}% (Phase 4)"] = qs_pnl

    # Dip
    if dip_trades:
        dip_pnl = sum(t.pnl_rs for t in dip_trades)
        dip_wins = sum(1 for t in dip_trades if t.pnl_rs > 0)
        dip_wr = dip_wins / len(dip_trades) * 100 if dip_trades else 0
        strategies["Premium Dip (Phase 5)"] = dip_pnl

    print(f"\n  {'Strategy':<40} {'Trades':>7} {'WR':>7} {'Net P&L':>14} {'Per Trade':>12} {'Per Day':>12}")
    print(f"  {'':-<40} {'':->7} {'':->7} {'':->14} {'':->12} {'':->12}")

    n_days = len(all_days_candles)

    # Perfect
    perfect_count = sum(1 for d in all_days_candles
                       if (perfect_ce.get(d) and perfect_ce[d].pnl_rs > 0) or
                          (perfect_pe.get(d) and perfect_pe[d].pnl_rs > 0))
    print(f"  {'Perfect (Phase 1)':<40} {perfect_count:>7} {'100.0%':>7} "
          f"{format_rs(total_perfect_pnl):>14} "
          f"{format_rs(total_perfect_pnl / max(perfect_count, 1)):>12} "
          f"{format_rs(total_perfect_pnl / n_days):>12}")

    # Realistic
    print(f"  {'Realistic (Phase 2)':<40} {len(all_realistic):>7} {'100.0%':>7} "
          f"{format_rs(total_realistic_pnl):>14} "
          f"{format_rs(total_realistic_pnl / max(len(all_realistic), 1)):>12} "
          f"{format_rs(total_realistic_pnl / n_days):>12}")

    # Quality Score
    if best_threshold_trades:
        print(f"  {f'Quality Score Top {best_threshold}% (Phase 4)':<40} "
              f"{len(best_threshold_trades):>7} {qs_wr:>6.1f}% "
              f"{format_rs(qs_pnl):>14} "
              f"{format_rs(qs_pnl / len(best_threshold_trades)):>12} "
              f"{format_rs(qs_pnl / n_days):>12}")

    # Dip
    if dip_trades:
        print(f"  {'Premium Dip (Phase 5)':<40} "
              f"{len(dip_trades):>7} {dip_wr:>6.1f}% "
              f"{format_rs(dip_pnl):>14} "
              f"{format_rs(dip_pnl / len(dip_trades)):>12} "
              f"{format_rs(dip_pnl / n_days):>12}")

    # Capture rate
    print(f"\n  CAPTURE RATES (vs Perfect):")
    if total_perfect_pnl > 0:
        print(f"  Realistic: {total_realistic_pnl / total_perfect_pnl * 100:.1f}% of perfect")
        if best_threshold_trades:
            print(f"  Quality Score: {qs_pnl / total_perfect_pnl * 100:.1f}% of perfect")
        if dip_trades:
            print(f"  Premium Dip: {dip_pnl / total_perfect_pnl * 100:.1f}% of perfect")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
