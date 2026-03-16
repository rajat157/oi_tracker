"""
RALLY CATCHER -- Quality Score Model v3 (Final)

Enhanced quality score with:
- 5 NEW factors: VIX direction, premium bounce from low, futures basis shift,
  volume surge, IV compression
- Full grid search over 864 parameter combinations
- Top 20 by P&L, Top 10 by Sharpe, Top 10 by combined rank
- Professional report for winning combination
- Robustness check (first half vs second half)
- Comparison with Scalper Agent baseline (+Rs 8,997)
"""

import sqlite3
import json
import math
import sys
import os
import time

# Force UTF-8 output on Windows
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
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
    score_components: dict = field(default_factory=dict)


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
    """Load aligned candle data for a day."""
    analysis_rows = conn.execute("""
        SELECT timestamp, spot_price, vix, iv_skew, futures_basis, verdict,
               signal_confidence, futures_oi_change
        FROM analysis_history
        WHERE DATE(timestamp) = ?
        ORDER BY timestamp
    """, (date_str,)).fetchall()

    if not analysis_rows:
        return []

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
    ce_strike = atm_open - 100
    pe_strike = atm_open + 100

    candles = []
    for r in market_rows:
        ts = r["timestamp"]
        dt = parse_ts(ts)

        snap_ce = conn.execute("""
            SELECT ce_ltp, ce_oi, ce_oi_change, ce_iv, ce_volume
            FROM oi_snapshots
            WHERE timestamp = ? AND strike_price = ?
        """, (ts, ce_strike)).fetchone()

        snap_pe = conn.execute("""
            SELECT pe_ltp, pe_oi, pe_oi_change, pe_iv, pe_volume
            FROM oi_snapshots
            WHERE timestamp = ? AND strike_price = ?
        """, (ts, pe_strike)).fetchone()

        ce_ltp = snap_ce["ce_ltp"] if snap_ce else 0.0
        pe_ltp = snap_pe["pe_ltp"] if snap_pe else 0.0

        if ce_ltp <= 0 and pe_ltp <= 0:
            continue

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


# --- Enhanced Quality Score v3 -----------------------------------------------

def compute_quality_score_v3(candles, idx, weights):
    """Compute enhanced quality score with 13 factors (8 original + 5 new)."""
    if idx < 5:
        return 0.0, None, {}

    lookback = candles[max(0, idx - 5):idx]
    entry_c = candles[idx]

    if len(lookback) < 2:
        return 0.0, None, {}

    # ===== EXISTING FACTORS (recalculated) =====

    # 1. Spot momentum / trending
    spots = [c.spot for c in lookback]
    ups = sum(1 for i in range(1, len(spots)) if spots[i] > spots[i-1])
    downs = sum(1 for i in range(1, len(spots)) if spots[i] < spots[i-1])
    total_moves = len(spots) - 1
    spot_trending_up = ups / total_moves >= 0.7 if total_moves > 0 else False
    spot_trending_down = downs / total_moves >= 0.7 if total_moves > 0 else False

    # Spot move magnitude
    spot_move_15m = (spots[-1] - spots[0]) / spots[0] * 100 if spots[0] > 0 else 0

    # 2. Spot position in day range
    prior_spots = [c.spot for c in candles[:idx]]
    spot_pos = 0.5
    if prior_spots:
        sr = max(prior_spots) - min(prior_spots)
        if sr > 0:
            spot_pos = (entry_c.spot - min(prior_spots)) / sr
    spot_at_low = spot_pos < 0.2
    spot_at_high = spot_pos > 0.8

    # 3. OI shifts
    ce_oi_delta = lookback[-1].ce_oi_change - lookback[0].ce_oi_change
    pe_oi_delta = lookback[-1].pe_oi_change - lookback[0].pe_oi_change
    oi_bullish = pe_oi_delta > ce_oi_delta and pe_oi_delta > 0
    oi_bearish = ce_oi_delta > pe_oi_delta and ce_oi_delta > 0

    # 4. Premium near daily low
    ce_prems = [c.ce_ltp for c in candles[:idx] if c.ce_ltp > 0]
    pe_prems = [c.pe_ltp for c in candles[:idx] if c.pe_ltp > 0]
    ce_near_low = False
    pe_near_low = False
    ce_daily_low = min(ce_prems) if ce_prems else 0
    pe_daily_low = min(pe_prems) if pe_prems else 0
    if ce_prems and ce_daily_low > 0:
        ce_near_low = (entry_c.ce_ltp - ce_daily_low) / ce_daily_low < 0.05
    if pe_prems and pe_daily_low > 0:
        pe_near_low = (entry_c.pe_ltp - pe_daily_low) / pe_daily_low < 0.05

    # 5. Premium trend (falling = about to bounce)
    lb_ce_prems = [c.ce_ltp for c in lookback if c.ce_ltp > 0]
    lb_pe_prems = [c.pe_ltp for c in lookback if c.pe_ltp > 0]
    ce_prem_chg = (lb_ce_prems[-1] - lb_ce_prems[0]) / lb_ce_prems[0] * 100 if len(lb_ce_prems) >= 2 and lb_ce_prems[0] > 0 else 0
    pe_prem_chg = (lb_pe_prems[-1] - lb_pe_prems[0]) / lb_pe_prems[0] * 100 if len(lb_pe_prems) >= 2 and lb_pe_prems[0] > 0 else 0

    # 6. Verdict
    verdict_lower = entry_c.verdict.lower()
    verdict_bull = "bull" in verdict_lower
    verdict_bear = "bear" in verdict_lower

    # ===== NEW FACTORS =====

    # 7. VIX direction (new)
    vix_values = [c.vix for c in lookback if c.vix > 0]
    vix_rising = False
    vix_falling = False
    if len(vix_values) >= 2:
        vix_rising = vix_values[-1] > vix_values[0]
        vix_falling = vix_values[-1] < vix_values[0]

    # 8. Premium bounce from low (new -- STRONGER than just near_low)
    # Premium within 5% of daily low AND bounced 2%+ in last candle
    ce_bounce_from_low = False
    pe_bounce_from_low = False
    if len(lb_ce_prems) >= 2 and ce_daily_low > 0:
        pct_from_low = (entry_c.ce_ltp - ce_daily_low) / ce_daily_low * 100
        last_candle_bounce = (entry_c.ce_ltp - lb_ce_prems[-1]) / lb_ce_prems[-1] * 100 if lb_ce_prems[-1] > 0 else 0
        ce_bounce_from_low = pct_from_low <= 5 and last_candle_bounce >= 2
    if len(lb_pe_prems) >= 2 and pe_daily_low > 0:
        pct_from_low = (entry_c.pe_ltp - pe_daily_low) / pe_daily_low * 100
        last_candle_bounce = (entry_c.pe_ltp - lb_pe_prems[-1]) / lb_pe_prems[-1] * 100 if lb_pe_prems[-1] > 0 else 0
        pe_bounce_from_low = pct_from_low <= 5 and last_candle_bounce >= 2

    # 9. Futures basis shift (new)
    basis_values = [c.futures_basis for c in lookback if c.futures_basis != 0]
    basis_turning_positive = False
    basis_turning_negative = False
    if len(basis_values) >= 2:
        basis_turning_positive = basis_values[-1] > 0 and basis_values[-1] > basis_values[0]
        basis_turning_negative = basis_values[-1] < 0 and basis_values[-1] < basis_values[0]

    # 10. Volume surge (new): last candle volume is 2x+ day's average
    ce_vol_surge = False
    pe_vol_surge = False
    prior_ce_vols = [c.ce_volume for c in candles[:idx] if c.ce_volume > 0]
    prior_pe_vols = [c.pe_volume for c in candles[:idx] if c.pe_volume > 0]
    if prior_ce_vols and entry_c.ce_volume > 0:
        avg_ce_vol = sum(prior_ce_vols) / len(prior_ce_vols)
        ce_vol_surge = entry_c.ce_volume >= 2 * avg_ce_vol if avg_ce_vol > 0 else False
    if prior_pe_vols and entry_c.pe_volume > 0:
        avg_pe_vol = sum(prior_pe_vols) / len(prior_pe_vols)
        pe_vol_surge = entry_c.pe_volume >= 2 * avg_pe_vol if avg_pe_vol > 0 else False

    # 11. IV compression then expansion (new)
    # IV dropped 5%+ in lookback then started rising in last candle
    ce_iv_compress = False
    pe_iv_compress = False
    lb_ce_ivs = [c.ce_iv for c in lookback if c.ce_iv > 0]
    lb_pe_ivs = [c.pe_iv for c in lookback if c.pe_iv > 0]
    if len(lb_ce_ivs) >= 3 and entry_c.ce_iv > 0:
        iv_drop = (lb_ce_ivs[-1] - lb_ce_ivs[0]) / lb_ce_ivs[0] * 100 if lb_ce_ivs[0] > 0 else 0
        iv_bounce = entry_c.ce_iv > lb_ce_ivs[-1]
        ce_iv_compress = iv_drop <= -5 and iv_bounce
    if len(lb_pe_ivs) >= 3 and entry_c.pe_iv > 0:
        iv_drop = (lb_pe_ivs[-1] - lb_pe_ivs[0]) / lb_pe_ivs[0] * 100 if lb_pe_ivs[0] > 0 else 0
        iv_bounce = entry_c.pe_iv > lb_pe_ivs[-1]
        pe_iv_compress = iv_drop <= -5 and iv_bounce

    # ===== SCORE CALCULATION =====

    w_momentum = weights.get("momentum", 2.0)
    w_prem_near_low = weights.get("prem_near_low", 3.5)
    w_prem_falling = weights.get("prem_falling", 1.0)
    w_oi_aligned = weights.get("oi_aligned", 2.5)
    w_verdict = weights.get("verdict_aligned", 1.0)
    w_spot_extreme = weights.get("spot_at_extreme", 3.0)
    w_confidence = weights.get("high_confidence", 1.0)
    w_strong_mom = weights.get("strong_momentum", 1.5)
    # New factor weights
    w_vix_dir = weights.get("vix_direction", 1.5)
    w_prem_bounce = weights.get("prem_bounce_low", 3.5)
    w_basis_shift = weights.get("basis_shift", 1.5)
    w_vol_surge = weights.get("volume_surge", 2.0)
    w_iv_compress = weights.get("iv_compression", 1.5)

    # --- CE score ---
    ce_score = 0.0
    ce_comp = {}
    if spot_trending_up:
        ce_score += w_momentum
        ce_comp["momentum"] = w_momentum
    if ce_near_low:
        ce_score += w_prem_near_low
        ce_comp["prem_near_low"] = w_prem_near_low
    if ce_prem_chg < -3:
        ce_score += w_prem_falling
        ce_comp["prem_falling"] = w_prem_falling
    if oi_bullish:
        ce_score += w_oi_aligned
        ce_comp["oi_aligned"] = w_oi_aligned
    if verdict_bull:
        ce_score += w_verdict
        ce_comp["verdict"] = w_verdict
    if spot_at_low:
        ce_score += w_spot_extreme
        ce_comp["spot_extreme"] = w_spot_extreme
    if entry_c.signal_confidence >= 70:
        ce_score += w_confidence
        ce_comp["confidence"] = w_confidence
    if spot_move_15m > 0.1:
        ce_score += w_strong_mom
        ce_comp["strong_mom"] = w_strong_mom
    # New factors for CE
    if vix_falling:  # VIX falling = less fear = bullish
        ce_score += w_vix_dir
        ce_comp["vix_dir"] = w_vix_dir
    if ce_bounce_from_low:
        ce_score += w_prem_bounce
        ce_comp["prem_bounce"] = w_prem_bounce
    if basis_turning_positive:  # Futures basis positive = bullish
        ce_score += w_basis_shift
        ce_comp["basis_shift"] = w_basis_shift
    if ce_vol_surge:
        ce_score += w_vol_surge
        ce_comp["vol_surge"] = w_vol_surge
    if ce_iv_compress:
        ce_score += w_iv_compress
        ce_comp["iv_compress"] = w_iv_compress

    # --- PE score ---
    pe_score = 0.0
    pe_comp = {}
    if spot_trending_down:
        pe_score += w_momentum
        pe_comp["momentum"] = w_momentum
    if pe_near_low:
        pe_score += w_prem_near_low
        pe_comp["prem_near_low"] = w_prem_near_low
    if pe_prem_chg < -3:
        pe_score += w_prem_falling
        pe_comp["prem_falling"] = w_prem_falling
    if oi_bearish:
        pe_score += w_oi_aligned
        pe_comp["oi_aligned"] = w_oi_aligned
    if verdict_bear:
        pe_score += w_verdict
        pe_comp["verdict"] = w_verdict
    if spot_at_high:
        pe_score += w_spot_extreme
        pe_comp["spot_extreme"] = w_spot_extreme
    if entry_c.signal_confidence >= 70:
        pe_score += w_confidence
        pe_comp["confidence"] = w_confidence
    if spot_move_15m < -0.1:
        pe_score += w_strong_mom
        pe_comp["strong_mom"] = w_strong_mom
    # New factors for PE
    if vix_rising:  # VIX rising = more fear = bearish
        pe_score += w_vix_dir
        pe_comp["vix_dir"] = w_vix_dir
    if pe_bounce_from_low:
        pe_score += w_prem_bounce
        pe_comp["prem_bounce"] = w_prem_bounce
    if basis_turning_negative:  # Futures basis negative = bearish
        pe_score += w_basis_shift
        pe_comp["basis_shift"] = w_basis_shift
    if pe_vol_surge:
        pe_score += w_vol_surge
        pe_comp["vol_surge"] = w_vol_surge
    if pe_iv_compress:
        pe_score += w_iv_compress
        pe_comp["iv_compress"] = w_iv_compress

    if ce_score >= pe_score:
        return ce_score, "CE", ce_comp
    else:
        return pe_score, "PE", pe_comp


# --- Trade Simulation --------------------------------------------------------

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

        if cur_prem > peak_prem:
            peak_prem = cur_prem

        pct_from_entry = (peak_prem - entry_prem) / entry_prem * 100
        if pct_from_entry >= trail2_trigger:
            new_sl = entry_prem * (1 + trail2_sl / 100)
            current_sl = max(current_sl, new_sl)
        elif pct_from_entry >= trail1_trigger:
            new_sl = entry_prem * (1 + trail1_sl / 100)
            current_sl = max(current_sl, new_sl)

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
            pct_now = (cur_prem - entry_prem) / entry_prem * 100
            if pct_now < 5:
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


# --- Strategy Runner ---------------------------------------------------------

def run_strategy(all_days_candles, weights, threshold_pct, sl_pct=-10,
                 target_pct=20, max_trades_day=2, min_premium=100,
                 day_filter=None):
    """Run quality score strategy. Returns (trades, threshold_val)."""
    # Compute all scores to determine threshold
    all_scores = []
    target_days = day_filter if day_filter else sorted(all_days_candles.keys())

    for date_str in target_days:
        candles = all_days_candles.get(date_str)
        if not candles:
            continue
        for i in range(5, len(candles)):
            dt = parse_ts(candles[i].timestamp)
            t = dt.strftime("%H:%M")
            if t > ENTRY_CUTOFF:
                continue
            score, side, _ = compute_quality_score_v3(candles, i, weights)
            if score > 0:
                all_scores.append(score)

    if not all_scores:
        return [], 0

    all_scores.sort(reverse=True)
    threshold_idx = max(1, int(len(all_scores) * threshold_pct / 100))
    threshold_val = all_scores[min(threshold_idx, len(all_scores) - 1)]

    # Simulate
    trades = []
    for date_str in target_days:
        candles = all_days_candles.get(date_str)
        if not candles:
            continue

        day_trades = 0
        cooldown_until = None

        for i in range(5, len(candles)):
            if day_trades >= max_trades_day:
                break

            dt = parse_ts(candles[i].timestamp)
            t = dt.strftime("%H:%M")
            if t > ENTRY_CUTOFF:
                break

            if cooldown_until and dt < cooldown_until:
                continue

            score, side, components = compute_quality_score_v3(candles, i, weights)

            if score >= threshold_val and side:
                trade = simulate_trade(
                    candles, i, side,
                    sl_pct=sl_pct, target_pct=target_pct,
                    min_premium=min_premium
                )
                if trade:
                    trade.quality_score = score
                    trade.score_components = components
                    trades.append(trade)
                    day_trades += 1

                    exit_dt = parse_ts(f"{date_str}T{trade.exit_time}:00")
                    cooldown_until = exit_dt + timedelta(minutes=6)

    return trades, threshold_val


# --- Metrics Calculation -----------------------------------------------------

def calc_metrics(trades):
    """Calculate full metrics dict from a list of trades."""
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "wr": 0, "net_pnl": 0,
            "gross_profit": 0, "gross_loss": 0, "pf": 0, "max_dd": 0,
            "sharpe": 0, "calmar": 0, "avg_win": 0, "avg_loss": 0,
            "avg_per_trade": 0, "win_days": 0, "loss_days": 0,
        }

    wins = [t for t in trades if t.pnl_rs > 0]
    losses = [t for t in trades if t.pnl_rs <= 0]
    net_pnl = sum(t.pnl_rs for t in trades)
    gross_profit = sum(t.pnl_rs for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_rs for t in losses)) if losses else 0
    wr = len(wins) / len(trades) * 100 if trades else 0
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

    # Sharpe (daily P&L)
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

    # Calmar
    calmar = net_pnl / max_dd if max_dd > 0 else float('inf')

    # Win/loss days
    win_days = sum(1 for v in daily_vals if v > 0)
    loss_days = sum(1 for v in daily_vals if v <= 0)

    # Consecutive
    max_consec_win = 0
    max_consec_loss = 0
    cw = cl = 0
    for t in trades:
        if t.pnl_rs > 0:
            cw += 1
            cl = 0
        else:
            cl += 1
            cw = 0
        max_consec_win = max(max_consec_win, cw)
        max_consec_loss = max(max_consec_loss, cl)

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "wr": wr,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "pf": pf,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "calmar": calmar,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_per_trade": net_pnl / len(trades) if trades else 0,
        "win_days": win_days,
        "loss_days": loss_days,
        "max_consec_win": max_consec_win,
        "max_consec_loss": max_consec_loss,
    }


def format_rs(val):
    sign = "+" if val >= 0 else ""
    return f"{sign}Rs {val:,.0f}"


# --- Grid Search -------------------------------------------------------------

def run_grid_search(all_days_candles, weights):
    """Run full grid search over parameter space."""
    thresholds = [5, 8, 10, 12, 15, 18, 20, 25]
    sl_values = [-8, -10, -12]
    target_values = [15, 18, 20, 25]
    max_trades_values = [1, 2, 3]
    min_prem_values = [80, 100, 120]

    total_combos = len(thresholds) * len(sl_values) * len(target_values) * len(max_trades_values) * len(min_prem_values)
    print(f"\n  Grid search: {total_combos} parameter combinations")
    print(f"  Thresholds: {thresholds}")
    print(f"  SL: {sl_values}%  |  Target: {target_values}%")
    print(f"  Max trades/day: {max_trades_values}  |  Min premium: {min_prem_values}")

    results = []
    done = 0
    start_time = time.time()

    for thr in thresholds:
        for sl in sl_values:
            for target in target_values:
                for mtd in max_trades_values:
                    for mp in min_prem_values:
                        trades, thr_val = run_strategy(
                            all_days_candles, weights,
                            threshold_pct=thr, sl_pct=sl,
                            target_pct=target, max_trades_day=mtd,
                            min_premium=mp
                        )
                        m = calc_metrics(trades)
                        results.append({
                            "threshold": thr,
                            "sl": sl,
                            "target": target,
                            "max_trades_day": mtd,
                            "min_premium": mp,
                            "trades": m["trades"],
                            "wr": m["wr"],
                            "net_pnl": m["net_pnl"],
                            "max_dd": m["max_dd"],
                            "pf": m["pf"],
                            "sharpe": m["sharpe"],
                        })
                        done += 1
                        if done % 100 == 0:
                            elapsed = time.time() - start_time
                            rate = done / elapsed
                            eta = (total_combos - done) / rate
                            print(f"  ... {done}/{total_combos} ({done/total_combos*100:.0f}%) "
                                  f"-- {elapsed:.0f}s elapsed, ~{eta:.0f}s remaining", flush=True)

    elapsed = time.time() - start_time
    print(f"  Grid search complete in {elapsed:.1f}s ({total_combos} combos)")

    return results


# --- Report Printing ---------------------------------------------------------

def print_separator(char="=", width=100):
    print(char * width)


def print_header(title, width=100):
    print()
    print_separator("=", width)
    padding = (width - len(title) - 4) // 2
    print(f"{'=' * padding}  {title}  {'=' * (width - padding - len(title) - 4)}")
    print_separator("=", width)


def print_winning_report(trades, params, all_days_candles, n_total_days):
    """Print the complete professional report for the winning combination."""

    m = calc_metrics(trades)

    print_header("RALLY CATCHER -- QUALITY SCORE MODEL v3")

    # PARAMETERS
    print(f"\n  PARAMETERS:")
    print(f"  {'-'*50}")
    print(f"  Threshold:        Top {params['threshold']}% of quality scores")
    print(f"  Stop Loss:        {params['sl']}%")
    print(f"  Target:           +{params['target']}%")
    print(f"  Max Trades/Day:   {params['max_trades_day']}")
    print(f"  Min Premium:      Rs {params['min_premium']}")
    print(f"  Trailing SL 1:    +10% peak -> SL at +4%")
    print(f"  Trailing SL 2:    +15% peak -> SL at +10%")
    print(f"  Time Exit:        30m flat (<5% gain) / 45m forced")
    print(f"  Entry Cutoff:     {ENTRY_CUTOFF}")
    print(f"  Lot Size:         {LOT_SIZE} qty")
    print(f"  Brokerage:        Rs {BROKERAGE:.0f} round-trip")

    # EXECUTIVE SUMMARY
    print(f"\n  EXECUTIVE SUMMARY")
    print(f"  {'='*60}")
    print(f"  +--------------------+-----------------------+")
    print(f"  | Net P&L            | {format_rs(m['net_pnl']):>21} |")
    print(f"  | Trades             | {m['trades']:>21} |")
    print(f"  | Win Rate           | {m['wr']:>20.1f}% |")
    print(f"  | Profit Factor      | {m['pf']:>21.2f} |")
    print(f"  | Sharpe Ratio       | {m['sharpe']:>21.2f} |")
    print(f"  | Max Drawdown       | {'Rs {:,.0f}'.format(m['max_dd']):>21} |")
    print(f"  | Calmar Ratio       | {m['calmar']:>21.2f} |")
    print(f"  | Per Trade          | {format_rs(m['avg_per_trade']):>21} |")
    print(f"  | Per Day (all)      | {format_rs(m['net_pnl'] / n_total_days):>21} |")
    print(f"  +--------------------+-----------------------+")

    # TRADE STATISTICS
    print(f"\n  TRADE STATISTICS")
    print(f"  {'-'*50}")
    print(f"  Total Trades:      {m['trades']}")
    print(f"  Winners:           {m['wins']} ({m['wr']:.1f}%)")
    print(f"  Losers:            {m['losses']} ({100 - m['wr']:.1f}%)")
    print(f"  Gross Profit:      {format_rs(m['gross_profit'])}")
    print(f"  Gross Loss:        {format_rs(-m['gross_loss'])}")
    print(f"  Net P&L:           {format_rs(m['net_pnl'])}")
    print(f"  Avg Win:           {format_rs(m['avg_win'])}")
    print(f"  Avg Loss:          {format_rs(m['avg_loss'])}")
    print(f"  Avg Per Trade:     {format_rs(m['avg_per_trade'])}")
    print(f"  Best Trade:        {format_rs(max(t.pnl_rs for t in trades))}")
    print(f"  Worst Trade:       {format_rs(min(t.pnl_rs for t in trades))}")
    total_brokerage = len(trades) * BROKERAGE
    print(f"  Total Brokerage:   Rs {total_brokerage:,.0f} ({len(trades)} x Rs {BROKERAGE:.0f})")
    print(f"  Consec Wins:       {m['max_consec_win']}")
    print(f"  Consec Losses:     {m['max_consec_loss']}")

    # BY DIRECTION
    ce_trades = [t for t in trades if t.side == "CE"]
    pe_trades = [t for t in trades if t.side == "PE"]
    ce_m = calc_metrics(ce_trades)
    pe_m = calc_metrics(pe_trades)

    print(f"\n  BY DIRECTION (CE vs PE)")
    print(f"  {'-'*60}")
    print(f"  {'Metric':<20} {'CE':>18} {'PE':>18}")
    print(f"  {'-'*20} {'-'*18} {'-'*18}")
    print(f"  {'Trades':<20} {ce_m['trades']:>18} {pe_m['trades']:>18}")
    print(f"  {'Win Rate':<20} {ce_m['wr']:>17.1f}% {pe_m['wr']:>17.1f}%")
    print(f"  {'Net P&L':<20} {format_rs(ce_m['net_pnl']):>18} {format_rs(pe_m['net_pnl']):>18}")
    print(f"  {'Avg Per Trade':<20} {format_rs(ce_m['avg_per_trade']):>18} {format_rs(pe_m['avg_per_trade']):>18}")
    print(f"  {'PF':<20} {ce_m['pf']:>18.2f} {pe_m['pf']:>18.2f}")

    # EXIT ANALYSIS
    exit_reasons = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in trades:
        exit_reasons[t.exit_reason]["count"] += 1
        exit_reasons[t.exit_reason]["pnl"] += t.pnl_rs
        if t.pnl_rs > 0:
            exit_reasons[t.exit_reason]["wins"] += 1

    print(f"\n  EXIT ANALYSIS")
    print(f"  {'-'*65}")
    print(f"  {'Exit Reason':<18} {'Count':>6} {'WR':>7} {'Net P&L':>12} {'Avg P&L':>12}")
    print(f"  {'-'*18} {'-'*6} {'-'*7} {'-'*12} {'-'*12}")
    for reason in sorted(exit_reasons.keys()):
        d = exit_reasons[reason]
        wr = d["wins"] / d["count"] * 100 if d["count"] > 0 else 0
        avg = d["pnl"] / d["count"] if d["count"] > 0 else 0
        print(f"  {reason:<18} {d['count']:>6} {wr:>6.1f}% {format_rs(d['pnl']):>12} {format_rs(avg):>12}")

    # DAILY P&L
    daily_pnl = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "gross": 0.0})
    for t in trades:
        daily_pnl[t.date]["trades"] += 1
        daily_pnl[t.date]["gross"] += t.pnl_rs
        if t.pnl_rs > 0:
            daily_pnl[t.date]["wins"] += 1
        else:
            daily_pnl[t.date]["losses"] += 1

    print(f"\n  DAILY P&L")
    print(f"  {'-'*95}")
    print(f"  {'Date':<12} {'Trades':>7} {'W-L':>7} {'Gross':>10} {'Brok':>8} {'Net':>10} {'Cumulative':>12} {'DD':>10}")
    print(f"  {'-'*12} {'-'*7} {'-'*7} {'-'*10} {'-'*8} {'-'*10} {'-'*12} {'-'*10}")

    cum = 0
    peak = 0
    all_dates = sorted(all_days_candles.keys())
    for d in all_dates:
        dp = daily_pnl.get(d)
        if dp:
            brok = dp["trades"] * BROKERAGE
            net = dp["gross"]
            cum += net
            if cum > peak:
                peak = cum
            dd = peak - cum
            wl = f"{dp['wins']}-{dp['losses']}"
            print(f"  {d:<12} {dp['trades']:>7} {wl:>7} {format_rs(dp['gross'] + brok):>10} "
                  f"{f'Rs {brok:.0f}':>8} {format_rs(net):>10} {format_rs(cum):>12} "
                  f"{'Rs {:,.0f}'.format(dd):>10}")
        else:
            dd = peak - cum
            print(f"  {d:<12} {'0':>7} {'0-0':>7} {'--':>10} {'--':>8} {'--':>10} "
                  f"{format_rs(cum):>12} {'Rs {:,.0f}'.format(dd):>10}")

    # MONTHLY
    monthly = defaultdict(lambda: {"days": set(), "trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        month = t.date[:7]
        monthly[month]["days"].add(t.date)
        monthly[month]["trades"] += 1
        monthly[month]["pnl"] += t.pnl_rs
        if t.pnl_rs > 0:
            monthly[month]["wins"] += 1

    print(f"\n  MONTHLY SUMMARY")
    print(f"  {'-'*65}")
    print(f"  {'Month':<10} {'Days':>6} {'Trades':>7} {'WR':>7} {'Net P&L':>12} {'Per Day':>12}")
    print(f"  {'-'*10} {'-'*6} {'-'*7} {'-'*7} {'-'*12} {'-'*12}")
    for month in sorted(monthly.keys()):
        md = monthly[month]
        wr = md["wins"] / md["trades"] * 100 if md["trades"] > 0 else 0
        n_days = len(md["days"])
        print(f"  {month:<10} {n_days:>6} {md['trades']:>7} {wr:>6.1f}% "
              f"{format_rs(md['pnl']):>12} {format_rs(md['pnl'] / n_days):>12}")

    # TIME OF DAY
    hourly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        hour = t.entry_time[:2]
        hourly[hour]["trades"] += 1
        hourly[hour]["pnl"] += t.pnl_rs
        if t.pnl_rs > 0:
            hourly[hour]["wins"] += 1

    print(f"\n  TIME OF DAY ANALYSIS")
    print(f"  {'-'*55}")
    print(f"  {'Hour':<8} {'Trades':>7} {'WR':>7} {'Net P&L':>12} {'Avg P&L':>12}")
    print(f"  {'-'*8} {'-'*7} {'-'*7} {'-'*12} {'-'*12}")
    for hour in sorted(hourly.keys()):
        hd = hourly[hour]
        wr = hd["wins"] / hd["trades"] * 100 if hd["trades"] > 0 else 0
        avg = hd["pnl"] / hd["trades"] if hd["trades"] > 0 else 0
        print(f"  {hour}:xx    {hd['trades']:>7} {wr:>6.1f}% {format_rs(hd['pnl']):>12} {format_rs(avg):>12}")

    # RISK METRICS
    print(f"\n  RISK METRICS")
    print(f"  {'-'*50}")
    print(f"  Max Drawdown:         Rs {m['max_dd']:,.0f}")
    print(f"  Sharpe Ratio:         {m['sharpe']:.2f}")
    print(f"  Calmar Ratio:         {m['calmar']:.2f}")
    daily_vals = list(daily_pnl.values())
    win_day_count = sum(1 for d in daily_vals if d["gross"] > 0)
    loss_day_count = sum(1 for d in daily_vals if d["gross"] <= 0)
    total_active_days = len(daily_vals)
    no_trade_days = n_total_days - total_active_days
    print(f"  Win Days:             {win_day_count}/{total_active_days} active ({win_day_count/total_active_days*100:.0f}%)" if total_active_days > 0 else "")
    print(f"  Loss Days:            {loss_day_count}/{total_active_days} active")
    print(f"  No-Trade Days:        {no_trade_days}")
    print(f"  Max Consec Wins:      {m['max_consec_win']}")
    print(f"  Max Consec Losses:    {m['max_consec_loss']}")

    # Calculate risk of ruin (simplified)
    if m["wr"] > 0 and m["wr"] < 100:
        p = m["wr"] / 100
        q = 1 - p
        if m["avg_win"] > 0 and abs(m["avg_loss"]) > 0:
            r = abs(m["avg_win"]) / abs(m["avg_loss"])
            if r > 0:
                ror = ((q / p) ** 10) * 100 if p > q else 99.9
                ror = min(ror, 99.9)
                print(f"  Risk of Ruin (10u):   {ror:.1f}%")

    # EQUITY CURVE (ASCII)
    print(f"\n  EQUITY CURVE")
    print(f"  {'-'*80}")
    cum_values = []
    cum = 0
    for t in trades:
        cum += t.pnl_rs
        cum_values.append(cum)

    if cum_values:
        min_val = min(0, min(cum_values))
        max_val = max(cum_values)
        chart_height = 15
        chart_width = min(len(cum_values), 70)

        # Resample if needed
        if len(cum_values) > chart_width:
            step = len(cum_values) / chart_width
            sampled = [cum_values[int(i * step)] for i in range(chart_width)]
        else:
            sampled = cum_values

        val_range = max_val - min_val if max_val != min_val else 1

        for row in range(chart_height, -1, -1):
            val_at_row = min_val + (row / chart_height) * val_range
            label = f"  {val_at_row:>8,.0f} |"
            line = ""
            for v in sampled:
                v_row = int((v - min_val) / val_range * chart_height)
                if v_row == row:
                    line += "*"
                elif v_row > row and row == 0:
                    line += "-"
                elif v_row > row:
                    line += "|"
                else:
                    line += " "
            print(f"{label}{line}")
        print(f"  {'':>8} +{'-' * len(sampled)}")
        print(f"  {'':>8}  Trade 1{' ' * (len(sampled) - 15)}Trade {len(cum_values)}")

    # TRADE LOG
    print(f"\n  COMPLETE TRADE LOG")
    print(f"  {'-'*120}")
    print(f"  {'#':>3} {'Date':<12} {'Time':>5} {'Sig':<4} {'Dir':<4} {'Strike':>7} "
          f"{'Entry':>7} {'Exit':>7} {'Chg%':>7} {'Reason':<14} {'Net P&L':>10} {'Cum':>10} {'Score':>6}")
    print(f"  {'-'*3} {'-'*12} {'-'*5} {'-'*4} {'-'*4} {'-'*7} "
          f"{'-'*7} {'-'*7} {'-'*7} {'-'*14} {'-'*10} {'-'*10} {'-'*6}")

    cum = 0
    for i, t in enumerate(trades, 1):
        cum += t.pnl_rs
        sig = "BUY"
        print(f"  {i:>3} {t.date:<12} {t.entry_time:>5} {sig:<4} {t.side:<4} {t.strike:>7} "
              f"{t.entry_premium:>7.1f} {t.exit_premium:>7.1f} {t.pct_gain:>+6.1f}% "
              f"{t.exit_reason:<14} {format_rs(t.pnl_rs):>10} {format_rs(cum):>10} {t.quality_score:>6.1f}")


def print_comparison_with_agent(winning_pnl, winning_trades, winning_wr):
    """Compare with existing Scalper Agent."""
    agent_pnl = 8997  # From memory

    print_header("COMPARISON WITH SCALPER AGENT")
    print(f"\n  {'Metric':<25} {'Quality Score v3':>18} {'Scalper Agent':>18} {'Delta':>15}")
    print(f"  {'-'*25} {'-'*18} {'-'*18} {'-'*15}")
    print(f"  {'Net P&L':<25} {format_rs(winning_pnl):>18} {format_rs(agent_pnl):>18} {format_rs(winning_pnl - agent_pnl):>15}")
    print(f"  {'Trades':<25} {winning_trades:>18} {'~118':>18}")
    print(f"  {'Win Rate':<25} {winning_wr:>17.1f}% {'~50%':>18}")

    if winning_pnl > agent_pnl:
        improvement = (winning_pnl - agent_pnl) / agent_pnl * 100
        print(f"\n  Quality Score v3 OUTPERFORMS Scalper Agent by {format_rs(winning_pnl - agent_pnl)} ({improvement:.0f}% improvement)")
    else:
        shortfall = (agent_pnl - winning_pnl) / agent_pnl * 100
        print(f"\n  Quality Score v3 UNDERPERFORMS Scalper Agent by {format_rs(agent_pnl - winning_pnl)} ({shortfall:.0f}% shortfall)")


def print_robustness_check(all_days_candles, weights, params):
    """Split data in half and test each period."""
    all_dates = sorted(all_days_candles.keys())
    mid = len(all_dates) // 2
    first_half = all_dates[:mid]
    second_half = all_dates[mid:]

    print_header("ROBUSTNESS CHECK -- SPLIT HALF ANALYSIS")

    print(f"\n  First Half:  {first_half[0]} to {first_half[-1]} ({len(first_half)} days)")
    print(f"  Second Half: {second_half[0]} to {second_half[-1]} ({len(second_half)} days)")

    trades_h1, _ = run_strategy(
        all_days_candles, weights,
        threshold_pct=params["threshold"],
        sl_pct=params["sl"],
        target_pct=params["target"],
        max_trades_day=params["max_trades_day"],
        min_premium=params["min_premium"],
        day_filter=first_half
    )
    trades_h2, _ = run_strategy(
        all_days_candles, weights,
        threshold_pct=params["threshold"],
        sl_pct=params["sl"],
        target_pct=params["target"],
        max_trades_day=params["max_trades_day"],
        min_premium=params["min_premium"],
        day_filter=second_half
    )

    m1 = calc_metrics(trades_h1)
    m2 = calc_metrics(trades_h2)

    print(f"\n  {'Metric':<25} {'First Half':>18} {'Second Half':>18}")
    print(f"  {'-'*25} {'-'*18} {'-'*18}")
    print(f"  {'Days':<25} {len(first_half):>18} {len(second_half):>18}")
    print(f"  {'Trades':<25} {m1['trades']:>18} {m2['trades']:>18}")
    print(f"  {'Win Rate':<25} {m1['wr']:>17.1f}% {m2['wr']:>17.1f}%")
    print(f"  {'Net P&L':<25} {format_rs(m1['net_pnl']):>18} {format_rs(m2['net_pnl']):>18}")
    print(f"  {'PF':<25} {m1['pf']:>18.2f} {m2['pf']:>18.2f}")
    print(f"  {'Max DD':<25} {'Rs {:,.0f}'.format(m1['max_dd']):>18} {'Rs {:,.0f}'.format(m2['max_dd']):>18}")
    print(f"  {'Sharpe':<25} {m1['sharpe']:>18.2f} {m2['sharpe']:>18.2f}")
    print(f"  {'Per Trade':<25} {format_rs(m1['avg_per_trade']):>18} {format_rs(m2['avg_per_trade']):>18}")

    # Verdict
    h1_profitable = m1["net_pnl"] > 0
    h2_profitable = m2["net_pnl"] > 0

    print(f"\n  VERDICT:")
    if h1_profitable and h2_profitable:
        print(f"  ROBUST -- Both halves profitable")
        pnl_ratio = min(m1["net_pnl"], m2["net_pnl"]) / max(m1["net_pnl"], m2["net_pnl"]) * 100
        print(f"  P&L ratio (smaller/larger): {pnl_ratio:.0f}%")
        if pnl_ratio > 50:
            print(f"  STRONG consistency -- halves within 2x of each other")
        else:
            print(f"  MODERATE consistency -- one half significantly better")
    elif h1_profitable or h2_profitable:
        print(f"  WARNING: POTENTIAL OVERFIT -- Only one half is profitable!")
        if not h1_profitable:
            print(f"  First half UNPROFITABLE ({format_rs(m1['net_pnl'])})")
        else:
            print(f"  Second half UNPROFITABLE ({format_rs(m2['net_pnl'])})")
    else:
        print(f"  FAIL -- Neither half is profitable. Model likely broken.")


# --- Main --------------------------------------------------------------------

def main():
    print("=" * 100)
    print("  RALLY CATCHER -- QUALITY SCORE MODEL v3 (FINAL)")
    print("  Enhanced with 5 new factors + full grid search over 864 combinations")
    print("=" * 100)

    conn = get_connection()
    days = get_trading_days(conn)
    print(f"\nLoading data for {len(days)} trading days: {days[0]} to {days[-1]}")

    all_days_candles = {}
    for d in days:
        candles = load_day_candles(conn, d)
        if candles:
            all_days_candles[d] = candles
            print(f"  {d}: {len(candles)} candles, spot {candles[0].spot:.0f}->{candles[-1].spot:.0f}, "
                  f"CE@{candles[0].ce_strike} PE@{candles[0].pe_strike}")

    conn.close()
    n_total_days = len(all_days_candles)
    print(f"\nLoaded {n_total_days} days with valid data.\n")

    # Enhanced weights (v3)
    weights = {
        # Original factors (adjusted)
        "momentum": 2.0,
        "prem_near_low": 3.5,       # up from 3.0
        "prem_falling": 1.0,        # down from 1.5
        "oi_aligned": 2.5,          # up from 2.0
        "verdict_aligned": 1.0,
        "spot_at_extreme": 3.0,     # up from 2.5
        "high_confidence": 1.0,
        "strong_momentum": 1.5,
        # NEW factors
        "vix_direction": 1.5,
        "prem_bounce_low": 3.5,
        "basis_shift": 1.5,
        "volume_surge": 2.0,
        "iv_compression": 1.5,
    }

    max_possible_score = sum(weights.values())
    print(f"  Quality Score Weights (max possible = {max_possible_score:.1f}):")
    for k, v in weights.items():
        print(f"    {k:<20} = {v:.1f}")

    # =======================================================================
    # SECTION A: Quick validation with v3 score at previous best threshold (15%)
    # =======================================================================
    print_header("A. V3 SCORE VALIDATION (Top 15% baseline)")

    trades_v3, thr_val = run_strategy(all_days_candles, weights, threshold_pct=15)
    m_v3 = calc_metrics(trades_v3)

    print(f"\n  v3 at Top 15%: {m_v3['trades']} trades, {m_v3['wr']:.1f}% WR, "
          f"{format_rs(m_v3['net_pnl'])}, PF {m_v3['pf']:.2f}, Sharpe {m_v3['sharpe']:.2f}")
    print(f"  (v2 baseline was: 58 trades, 55.2% WR, +Rs 13,338)")

    # =======================================================================
    # SECTION B: Grid Search
    # =======================================================================
    print_header("B. GRID SEARCH -- 864 PARAMETER COMBINATIONS")

    grid_results = run_grid_search(all_days_candles, weights)

    # Filter out zero-trade results
    valid_results = [r for r in grid_results if r["trades"] >= 5]
    print(f"\n  Valid results (>= 5 trades): {len(valid_results)} / {len(grid_results)}")

    # Sort by net P&L
    by_pnl = sorted(valid_results, key=lambda x: x["net_pnl"], reverse=True)

    # Sort by Sharpe
    by_sharpe = sorted(valid_results, key=lambda x: x["sharpe"], reverse=True)

    # Combined rank
    for r in valid_results:
        r["pnl_rank"] = 0
        r["sharpe_rank"] = 0

    for i, r in enumerate(by_pnl):
        r["pnl_rank"] = i + 1
    for i, r in enumerate(by_sharpe):
        r["sharpe_rank"] = i + 1

    for r in valid_results:
        r["combined_rank"] = (r["pnl_rank"] + r["sharpe_rank"]) / 2

    by_combined = sorted(valid_results, key=lambda x: x["combined_rank"])

    # TOP 20 BY P&L
    print(f"\n  TOP 20 BY NET P&L:")
    print(f"  {'#':>3} {'Thr':>5} {'SL':>5} {'Tgt':>5} {'MTD':>4} {'MinP':>5} "
          f"{'Trades':>7} {'WR':>7} {'Net P&L':>12} {'Max DD':>10} {'PF':>7} {'Sharpe':>8}")
    print(f"  {'-'*3} {'-'*5} {'-'*5} {'-'*5} {'-'*4} {'-'*5} "
          f"{'-'*7} {'-'*7} {'-'*12} {'-'*10} {'-'*7} {'-'*8}")

    for i, r in enumerate(by_pnl[:20], 1):
        print(f"  {i:>3} {r['threshold']:>4}% {r['sl']:>4}% {r['target']:>4}% {r['max_trades_day']:>4} "
              f"{r['min_premium']:>5} {r['trades']:>7} {r['wr']:>6.1f}% "
              f"{format_rs(r['net_pnl']):>12} {'Rs {:,.0f}'.format(r['max_dd']):>10} "
              f"{r['pf']:>7.2f} {r['sharpe']:>8.2f}")

    # TOP 10 BY SHARPE
    print(f"\n  TOP 10 BY SHARPE RATIO:")
    print(f"  {'#':>3} {'Thr':>5} {'SL':>5} {'Tgt':>5} {'MTD':>4} {'MinP':>5} "
          f"{'Trades':>7} {'WR':>7} {'Net P&L':>12} {'Max DD':>10} {'PF':>7} {'Sharpe':>8}")
    print(f"  {'-'*3} {'-'*5} {'-'*5} {'-'*5} {'-'*4} {'-'*5} "
          f"{'-'*7} {'-'*7} {'-'*12} {'-'*10} {'-'*7} {'-'*8}")

    for i, r in enumerate(by_sharpe[:10], 1):
        print(f"  {i:>3} {r['threshold']:>4}% {r['sl']:>4}% {r['target']:>4}% {r['max_trades_day']:>4} "
              f"{r['min_premium']:>5} {r['trades']:>7} {r['wr']:>6.1f}% "
              f"{format_rs(r['net_pnl']):>12} {'Rs {:,.0f}'.format(r['max_dd']):>10} "
              f"{r['pf']:>7.2f} {r['sharpe']:>8.2f}")

    # TOP 10 BY COMBINED RANK
    print(f"\n  TOP 10 BY COMBINED RANK (avg of P&L rank + Sharpe rank):")
    print(f"  {'#':>3} {'Thr':>5} {'SL':>5} {'Tgt':>5} {'MTD':>4} {'MinP':>5} "
          f"{'Trades':>7} {'WR':>7} {'Net P&L':>12} {'Max DD':>10} {'PF':>7} {'Sharpe':>8} {'CRank':>7}")
    print(f"  {'-'*3} {'-'*5} {'-'*5} {'-'*5} {'-'*4} {'-'*5} "
          f"{'-'*7} {'-'*7} {'-'*12} {'-'*10} {'-'*7} {'-'*8} {'-'*7}")

    for i, r in enumerate(by_combined[:10], 1):
        print(f"  {i:>3} {r['threshold']:>4}% {r['sl']:>4}% {r['target']:>4}% {r['max_trades_day']:>4} "
              f"{r['min_premium']:>5} {r['trades']:>7} {r['wr']:>6.1f}% "
              f"{format_rs(r['net_pnl']):>12} {'Rs {:,.0f}'.format(r['max_dd']):>10} "
              f"{r['pf']:>7.2f} {r['sharpe']:>8.2f} {r['combined_rank']:>7.1f}")

    # =======================================================================
    # SECTION C: Winning Combination -- Full Report
    # =======================================================================
    # Use top combined rank as the "winner" (balanced approach)
    winner = by_combined[0]
    winner_params = {
        "threshold": winner["threshold"],
        "sl": winner["sl"],
        "target": winner["target"],
        "max_trades_day": winner["max_trades_day"],
        "min_premium": winner["min_premium"],
    }

    print_header(f"C. WINNING COMBINATION -- FULL REPORT")
    print(f"\n  Selected: Top combined rank (balanced P&L + Sharpe)")
    print(f"  Params: Threshold={winner_params['threshold']}%, SL={winner_params['sl']}%, "
          f"Target=+{winner_params['target']}%, MTD={winner_params['max_trades_day']}, "
          f"MinPrem={winner_params['min_premium']}")

    winning_trades, _ = run_strategy(
        all_days_candles, weights,
        threshold_pct=winner_params["threshold"],
        sl_pct=winner_params["sl"],
        target_pct=winner_params["target"],
        max_trades_day=winner_params["max_trades_day"],
        min_premium=winner_params["min_premium"]
    )

    winning_m = calc_metrics(winning_trades)

    print_winning_report(winning_trades, winner_params, all_days_candles, n_total_days)

    # =======================================================================
    # Also show top P&L winner if different
    # =======================================================================
    pnl_winner = by_pnl[0]
    if (pnl_winner["threshold"] != winner["threshold"] or
        pnl_winner["sl"] != winner["sl"] or
        pnl_winner["target"] != winner["target"] or
        pnl_winner["max_trades_day"] != winner["max_trades_day"] or
        pnl_winner["min_premium"] != winner["min_premium"]):

        print_header("C2. TOP P&L COMBINATION (for reference)")
        pnl_params = {
            "threshold": pnl_winner["threshold"],
            "sl": pnl_winner["sl"],
            "target": pnl_winner["target"],
            "max_trades_day": pnl_winner["max_trades_day"],
            "min_premium": pnl_winner["min_premium"],
        }
        print(f"\n  Params: Threshold={pnl_params['threshold']}%, SL={pnl_params['sl']}%, "
              f"Target=+{pnl_params['target']}%, MTD={pnl_params['max_trades_day']}, "
              f"MinPrem={pnl_params['min_premium']}")

        pnl_trades, _ = run_strategy(
            all_days_candles, weights,
            threshold_pct=pnl_params["threshold"],
            sl_pct=pnl_params["sl"],
            target_pct=pnl_params["target"],
            max_trades_day=pnl_params["max_trades_day"],
            min_premium=pnl_params["min_premium"]
        )
        pnl_m = calc_metrics(pnl_trades)

        print(f"\n  Trades: {pnl_m['trades']} | WR: {pnl_m['wr']:.1f}% | Net: {format_rs(pnl_m['net_pnl'])} "
              f"| PF: {pnl_m['pf']:.2f} | Sharpe: {pnl_m['sharpe']:.2f} | MaxDD: Rs {pnl_m['max_dd']:,.0f}")

    # =======================================================================
    # SECTION D: Comparison with Scalper Agent
    # =======================================================================
    print_comparison_with_agent(winning_m["net_pnl"], winning_m["trades"], winning_m["wr"])

    # =======================================================================
    # SECTION E: Robustness Check
    # =======================================================================
    print_robustness_check(all_days_candles, weights, winner_params)

    # =======================================================================
    # FINAL SUMMARY
    # =======================================================================
    print_header("FINAL SUMMARY")
    print(f"""
  Quality Score v3 Model -- Enhanced with 13 factors (8 original + 5 new):
  - VIX direction, Premium bounce from low, Futures basis shift,
    Volume surge, IV compression

  Grid Search: {len(grid_results)} combinations tested
  Valid results: {len(valid_results)} (>= 5 trades)

  WINNING COMBINATION (by combined rank):
    Threshold:  Top {winner_params['threshold']}%
    SL:         {winner_params['sl']}%
    Target:     +{winner_params['target']}%
    MaxT/Day:   {winner_params['max_trades_day']}
    Min Prem:   Rs {winner_params['min_premium']}

  RESULTS:
    Net P&L:    {format_rs(winning_m['net_pnl'])}
    Trades:     {winning_m['trades']}
    Win Rate:   {winning_m['wr']:.1f}%
    PF:         {winning_m['pf']:.2f}
    Sharpe:     {winning_m['sharpe']:.2f}
    Max DD:     Rs {winning_m['max_dd']:,.0f}

  vs Scalper Agent: {format_rs(winning_m['net_pnl'] - 8997)} delta
""")

    print("Done.")


if __name__ == "__main__":
    main()
