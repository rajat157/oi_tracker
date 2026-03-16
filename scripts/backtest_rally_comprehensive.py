"""
Rally Catcher Comprehensive Backtest
=====================================
Multi-timeframe rally-catching strategy across 300 trading days.

Architecture for speed:
1. Pre-scan ALL potential signals with all metadata (once)
2. Pre-compute candle arrays per day for fast trade management
3. Optimization just filters pre-computed signals — O(signals) not O(candles)

Premium Model (calibrated from 1296 real samples):
- 100pts ITM option: ~230 Rs premium
- Delta: 0.65 (median from real data)
- SL/targets in premium % terms
"""

import sqlite3
import numpy as np
from datetime import datetime, timedelta, time as dtime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
import sys
import os

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "oi_tracker.db")

LOT_SIZE = 65
BROKERAGE = 72
DELTA = 0.65
CAPITAL = 100_000  # Reference capital for DD% calculation (margin for 1 lot)

ALL_SIGNALS = ["ORB", "VB", "SR", "MC", "CB"]

DEFAULT_PARAMS = {
    "sl_prem_pct": 12,
    "target_prem_pct": 15,
    "trail_be_pct": 8,
    "trail_lock_pct": 12,
    "trail_lock_val_pct": 5,
    "max_trades_day": 2,
    "cooldown_min": 12,
    "tf_alignment_required": 1,
    "tf_alignment_after_loss": 2,
    "time_exit_min_1": 39,
    "time_exit_min_1_req_pct": 3,
    "time_exit_min_2": 54,
}


# ─── Data structures ─────────────────────────────────────────────────────────
@dataclass
class DayCandles:
    """Pre-processed candle data for a day as numpy arrays."""
    date: str
    timestamps: list       # list of datetime
    minutes: np.ndarray    # minutes since midnight (for time checks)
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    vix: np.ndarray
    n: int

@dataclass
class RawSignal:
    """A potential entry signal detected during pre-scan."""
    date: str
    candle_idx: int        # index into day's candle arrays
    timestamp: datetime
    direction: str         # CE / PE
    signal_type: str       # ORB / VB / SR / MC / CB
    entry_spot: float
    vix_at_entry: float
    tf_alignment: int      # how many timeframes agree (0-3)
    daily_trend_aligns: bool
    weekly_trend_aligns: bool
    monthly_trend_aligns: bool

@dataclass
class Trade:
    signal: RawSignal
    entry_premium: float
    exit_time: Optional[datetime] = None
    exit_spot: float = 0
    exit_premium: float = 0
    exit_reason: str = ""
    premium_pnl: float = 0
    real_prem_entry: float = 0
    real_prem_exit: float = 0
    real_prem_pnl: float = 0

@dataclass
class DayContext:
    date: str
    monthly_trend: str = "NEUTRAL"
    monthly_support: float = 0
    monthly_resistance: float = 0
    weekly_trend: str = "NEUTRAL"
    weekly_support: float = 0
    weekly_resistance: float = 0
    prev_day_high: float = 0
    prev_day_low: float = 0
    prev_day_close: float = 0
    prev_day_open: float = 0
    prev_day_trend: str = "NEUTRAL"
    daily_trend: str = "NEUTRAL"  # based on 2-day comparison
    gap: float = 0


# ─── Loading ──────────────────────────────────────────────────────────────────
def load_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT timestamp, open, high, low, close FROM nifty_history ORDER BY timestamp")
    nifty = cur.fetchall()
    cur.execute("SELECT timestamp, close FROM vix_history ORDER BY timestamp")
    vix = cur.fetchall()
    conn.close()

    # Group by day
    days_n = defaultdict(list)
    days_v = defaultdict(list)
    for r in nifty:
        ts = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
        days_n[ts.strftime("%Y-%m-%d")].append((ts, r[1], r[2], r[3], r[4]))
    for r in vix:
        ts = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
        days_v[ts.strftime("%Y-%m-%d")].append(r[1])

    all_dates = sorted(days_n.keys())

    # Convert to DayCandles
    day_data = {}
    for d in all_dates:
        rows = days_n[d]
        vrows = days_v.get(d, [])
        n = len(rows)
        if n < 5:
            continue
        timestamps = [r[0] for r in rows]
        minutes = np.array([r[0].hour * 60 + r[0].minute for r in rows])
        opens = np.array([r[1] for r in rows])
        highs = np.array([r[2] for r in rows])
        lows = np.array([r[3] for r in rows])
        closes = np.array([r[4] for r in rows])
        vx = np.array(vrows[:n] if len(vrows) >= n else vrows + [14.0] * (n - len(vrows)))
        day_data[d] = DayCandles(d, timestamps, minutes, opens, highs, lows, closes, vx, n)

    print(f"  Loaded {len(all_dates)} trading days, {len(nifty)} candles")
    return all_dates, day_data


def load_real_premiums():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp
        FROM oi_snapshots WHERE ce_ltp > 0 OR pe_ltp > 0
        ORDER BY timestamp, strike_price
    """)
    rows = cur.fetchall()
    conn.close()

    premiums = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        ts_str = r[0]
        ts = datetime.fromisoformat(ts_str) if 'T' in ts_str else datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        d = ts.strftime("%Y-%m-%d")
        m = (ts.minute // 3) * 3
        rnd = ts.replace(minute=m, second=0, microsecond=0)
        rs = rnd.strftime("%Y-%m-%d %H:%M:%S")
        premiums[d][rs][int(r[2])] = {"ce": r[3], "pe": r[4], "spot": r[1]}

    print(f"  Loaded real premiums for {len(premiums)} days")
    return premiums


def get_real_premium(pdata, date_str, ts, spot, direction):
    if date_str not in pdata:
        return None
    m = (ts.minute // 3) * 3
    rnd = ts.replace(minute=m, second=0, microsecond=0)
    rs = rnd.strftime("%Y-%m-%d %H:%M:%S")
    snaps = pdata[date_str]
    if rs not in snaps:
        all_ts = sorted(snaps.keys())
        if not all_ts:
            return None
        best = min(all_ts, key=lambda x: abs((datetime.strptime(x, "%Y-%m-%d %H:%M:%S") - rnd).total_seconds()))
        if abs((datetime.strptime(best, "%Y-%m-%d %H:%M:%S") - rnd).total_seconds()) > 300:
            return None
        rs = best
    sd = snaps[rs]
    atm = round(spot / 50) * 50
    tgt_s = (atm - 100) if direction == "CE" else (atm + 100)
    key = "ce" if direction == "CE" else "pe"
    bs = None; bd = float('inf')
    for s in sd:
        dd = abs(s - tgt_s)
        if dd < bd and sd[s][key] > 0:
            bd = dd; bs = s
    if bs is None or bd > 150:
        return None
    return sd[bs][key]


# ─── Context computation ─────────────────────────────────────────────────────
def compute_contexts(all_dates, day_data):
    daily_ohlc = {}
    for d in all_dates:
        if d not in day_data:
            continue
        dd = day_data[d]
        daily_ohlc[d] = {
            "open": dd.opens[0], "high": float(dd.highs.max()),
            "low": float(dd.lows.min()), "close": dd.closes[-1],
        }

    contexts = {}
    for i, d in enumerate(all_dates):
        ctx = DayContext(date=d)
        if i == 0 or d not in day_data:
            contexts[d] = ctx
            continue

        prev_d = all_dates[i-1]
        if prev_d in daily_ohlc:
            p = daily_ohlc[prev_d]
            ctx.prev_day_high = p["high"]
            ctx.prev_day_low = p["low"]
            ctx.prev_day_close = p["close"]
            ctx.prev_day_open = p["open"]
            ctx.prev_day_trend = "UP" if p["close"] > p["open"] else "DOWN"
            ctx.gap = daily_ohlc[d]["open"] - p["close"] if d in daily_ohlc else 0

        # Daily trend (2-day)
        if i >= 2 and all_dates[i-2] in daily_ohlc:
            ctx.daily_trend = "UP" if ctx.prev_day_close > daily_ohlc[all_dates[i-2]]["close"] else "DOWN"

        # Weekly (5 days)
        ws = max(0, i-5)
        wd = [all_dates[j] for j in range(ws, i) if all_dates[j] in daily_ohlc]
        if len(wd) >= 2:
            wh = max(daily_ohlc[x]["high"] for x in wd)
            wl = min(daily_ohlc[x]["low"] for x in wd)
            ctx.weekly_resistance = wh
            ctx.weekly_support = wl
            ctx.weekly_trend = "UP" if daily_ohlc[wd[-1]]["close"] > daily_ohlc[wd[0]]["close"] else "DOWN"

        # Monthly (22 days)
        ms = max(0, i-22)
        md = [all_dates[j] for j in range(ms, i) if all_dates[j] in daily_ohlc]
        if len(md) >= 5:
            mh = max(daily_ohlc[x]["high"] for x in md)
            ml = min(daily_ohlc[x]["low"] for x in md)
            ctx.monthly_resistance = mh
            ctx.monthly_support = ml
            ctx.monthly_trend = "UP" if daily_ohlc[md[-1]]["close"] > daily_ohlc[md[0]]["close"] else "DOWN"

        contexts[d] = ctx
    return contexts


# ─── Signal Pre-Scan ──────────────────────────────────────────────────────────
def _tf_align(direction, ctx):
    """Count and return individual TF alignments."""
    tgt = "UP" if direction == "CE" else "DOWN"
    da = ctx.daily_trend == tgt
    wa = ctx.weekly_trend == tgt
    ma = ctx.monthly_trend == tgt
    return int(da) + int(wa) + int(ma), da, wa, ma


def prescan_signals(all_dates, day_data, contexts):
    """Pre-scan all days and extract every possible signal with metadata.
    Signal conditions here are LOOSE — filtering happens at backtest time."""
    signals_by_day = defaultdict(list)
    signal_counts = defaultdict(int)

    for d in all_dates:
        if d not in day_data:
            continue
        dd = day_data[d]
        ctx = contexts.get(d)
        if ctx is None or dd.n < 10:
            continue

        n = dd.n
        closes = dd.closes
        highs = dd.highs
        lows = dd.lows
        minutes = dd.minutes
        vix = dd.vix

        # VWAP (cumulative)
        vwap = np.cumsum(closes) / np.arange(1, n + 1)

        # Opening range (first 5 candles, 9:15-9:27)
        or_high = float(highs[:5].max()) if n >= 5 else float(highs[0])
        or_low = float(lows[:5].min()) if n >= 5 else float(lows[0])
        or_range = or_high - or_low

        day_open = closes[0]

        for ci in range(n):
            t_min = minutes[ci]
            ts = dd.timestamps[ci]
            c = closes[ci]
            h = highs[ci]
            lo = lows[ci]
            v = vix[ci]

            # ── ORB ──
            if 570 <= t_min <= 630 and ci >= 5:  # 9:30-10:30
                # Various breakout thresholds — store OR info
                if 8 <= or_range <= 80:
                    for bp in [2, 3, 5, 8]:
                        if c > or_high + bp:
                            al, da, wa, ma = _tf_align("CE", ctx)
                            signals_by_day[d].append(RawSignal(
                                d, ci, ts, "CE", "ORB", c, v, al, da, wa, ma
                            ))
                            signal_counts["ORB"] += 1
                            break  # only one ORB CE per candle
                    for bp in [2, 3, 5, 8]:
                        if c < or_low - bp:
                            al, da, wa, ma = _tf_align("PE", ctx)
                            signals_by_day[d].append(RawSignal(
                                d, ci, ts, "PE", "ORB", c, v, al, da, wa, ma
                            ))
                            signal_counts["ORB"] += 1
                            break

            # ── VB (VWAP Bounce) ──
            if 585 <= t_min <= 810 and ci >= 3:  # 9:45-13:30
                vw = vwap[ci]
                c2, c1 = closes[ci-2], closes[ci-1]
                # Was near VWAP?
                near_2 = abs(c2 - vwap[ci-2]) if ci >= 3 else 999
                near_1 = abs(c1 - vwap[ci-1]) if ci >= 2 else 999
                was_near = min(near_2, near_1)

                if was_near < 12:  # loose threshold
                    # Bounce up
                    if c > vw + 5 and c > c1 and c1 > c2:
                        al, da, wa, ma = _tf_align("CE", ctx)
                        signals_by_day[d].append(RawSignal(
                            d, ci, ts, "CE", "VB", c, v, al, da, wa, ma
                        ))
                        signal_counts["VB"] += 1
                    # Bounce down
                    if c < vw - 5 and c < c1 and c1 < c2:
                        al, da, wa, ma = _tf_align("PE", ctx)
                        signals_by_day[d].append(RawSignal(
                            d, ci, ts, "PE", "VB", c, v, al, da, wa, ma
                        ))
                        signal_counts["VB"] += 1

            # ── SR (Support/Resistance) ──
            if 570 <= t_min <= 840 and ci >= 3:  # 9:30-14:00
                if ctx.prev_day_low > 0:
                    recent_low = float(lows[max(0, ci-3):ci].min()) if ci >= 3 else lo
                    if abs(recent_low - ctx.prev_day_low) < 20:
                        move = c - recent_low
                        if move > 8:
                            al, da, wa, ma = _tf_align("CE", ctx)
                            signals_by_day[d].append(RawSignal(
                                d, ci, ts, "CE", "SR", c, v, al, da, wa, ma
                            ))
                            signal_counts["SR"] += 1

                    recent_high = float(highs[max(0, ci-3):ci].max()) if ci >= 3 else h
                    if abs(recent_high - ctx.prev_day_high) < 20:
                        move = recent_high - c
                        if move > 8:
                            al, da, wa, ma = _tf_align("PE", ctx)
                            signals_by_day[d].append(RawSignal(
                                d, ci, ts, "PE", "SR", c, v, al, da, wa, ma
                            ))
                            signal_counts["SR"] += 1

            # ── MC (Momentum Continuation) ──
            if 600 <= t_min <= 840 and ci >= 6:  # 10:00-14:00
                move = c - day_open
                abs_move = abs(move)
                if abs_move >= 25:  # loose threshold
                    is_up = move > 0
                    if is_up:
                        peak = float(highs[:ci+1].max())
                        pb = peak - float(lows[max(0, ci-4):ci+1].min())
                        pb_pct = pb / abs_move * 100 if abs_move > 0 else 0
                        if 20 <= pb_pct <= 65:
                            # Resume check
                            if ci >= 2 and closes[ci-1] > closes[ci-2]:
                                al, da, wa, ma = _tf_align("CE", ctx)
                                signals_by_day[d].append(RawSignal(
                                    d, ci, ts, "CE", "MC", c, v, al, da, wa, ma
                                ))
                                signal_counts["MC"] += 1
                    else:
                        trough = float(lows[:ci+1].min())
                        pb = float(highs[max(0, ci-4):ci+1].max()) - trough
                        pb_pct = pb / abs_move * 100 if abs_move > 0 else 0
                        if 20 <= pb_pct <= 65:
                            if ci >= 2 and closes[ci-1] < closes[ci-2]:
                                al, da, wa, ma = _tf_align("PE", ctx)
                                signals_by_day[d].append(RawSignal(
                                    d, ci, ts, "PE", "MC", c, v, al, da, wa, ma
                                ))
                                signal_counts["MC"] += 1

            # ── CB (Compression Breakout) ──
            if 585 <= t_min <= 810 and ci >= 6:  # 9:45-13:30
                for comp_n in [5, 6, 7]:
                    if ci < comp_n + 1:
                        continue
                    rh = highs[ci-comp_n:ci]
                    rl = lows[ci-comp_n:ci]
                    ch = float(rh.max())
                    cl = float(rl.min())
                    cr = ch - cl
                    if cr <= 30:  # loose
                        # VIX stable check
                        vs = vix[ci-comp_n] if ci >= comp_n else v
                        vix_stable = v <= vs + 2

                        if vix_stable:
                            if c > ch + 5:
                                al, da, wa, ma = _tf_align("CE", ctx)
                                signals_by_day[d].append(RawSignal(
                                    d, ci, ts, "CE", "CB", c, v, al, da, wa, ma
                                ))
                                signal_counts["CB"] += 1
                                break  # one CB per candle
                            if c < cl - 5:
                                al, da, wa, ma = _tf_align("PE", ctx)
                                signals_by_day[d].append(RawSignal(
                                    d, ci, ts, "PE", "CB", c, v, al, da, wa, ma
                                ))
                                signal_counts["CB"] += 1
                                break

    total = sum(signal_counts.values())
    print(f"  Pre-scanned {total:,} raw signals: " +
          ", ".join(f"{k}={v:,}" for k, v in sorted(signal_counts.items())))
    return signals_by_day


# ─── Premium helpers ──────────────────────────────────────────────────────────
def estimate_premium(vix):
    tv = 80.0 + (vix - 12.0) * 10.0
    tv = max(60.0, min(tv, 290.0))
    return 100.0 + tv

def premium_at_spot(entry_prem, spot_change, direction):
    """Premium change for a fixed-strike option given spot change."""
    if direction == "CE":
        return entry_prem + DELTA * spot_change
    else:
        return entry_prem + DELTA * (-spot_change)


# ─── Fast Backtest Engine ────────────────────────────────────────────────────
def run_backtest(all_dates, day_data, signals_by_day, real_premiums, params, enabled_signals):
    """Run backtest using pre-computed signals. Fast because it doesn't re-scan candles."""
    p = params
    trades = []
    daily_pnl = {}
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    equity_points = [0.0]

    enabled_set = set(enabled_signals)
    sl_pct = p["sl_prem_pct"]
    tgt_pct = p["target_prem_pct"]
    trail_be = p["trail_be_pct"]
    trail_lock = p["trail_lock_pct"]
    trail_lock_val = p["trail_lock_val_pct"]
    max_t = p["max_trades_day"]
    cd_min = p["cooldown_min"]
    tf_req = p["tf_alignment_required"]
    tf_loss = p["tf_alignment_after_loss"]
    te1 = p["time_exit_min_1"]
    te1_req = p["time_exit_min_1_req_pct"]
    te2 = p["time_exit_min_2"]

    for d in all_dates:
        if d not in day_data:
            daily_pnl[d] = 0.0
            continue

        dd = day_data[d]
        day_signals = signals_by_day.get(d, [])
        # Filter by enabled signals and TF alignment
        # Sort by candle index
        day_signals = sorted(
            [s for s in day_signals if s.signal_type in enabled_set],
            key=lambda s: s.candle_idx
        )

        day_pnl_val = 0.0
        day_trade_count = 0
        last_exit_idx = -999
        had_loss = False

        sig_ptr = 0  # pointer into sorted signals

        for sig in day_signals:
            ci = sig.candle_idx
            t_min = dd.minutes[ci]

            # Trading hours check
            if t_min < 570 or t_min > 870:  # 9:30-14:30
                continue

            # Max trades
            if day_trade_count >= max_t:
                break

            # Cooldown: need cd_min minutes gap (cd_min / 3 candles)
            cd_candles = int(cd_min / 3)
            if ci - last_exit_idx < cd_candles:
                continue

            # TF alignment
            req = tf_loss if had_loss else tf_req
            if sig.tf_alignment < req:
                continue

            # Entry
            entry_prem = estimate_premium(sig.vix_at_entry)
            sl_prem = entry_prem * (1 - sl_pct / 100)
            tgt_prem = entry_prem * (1 + tgt_pct / 100)
            trail_sl = sl_prem

            entry_spot = sig.entry_spot
            direction = sig.direction

            # Real premium entry
            rp_entry = 0.0
            if d in real_premiums:
                rp = get_real_premium(real_premiums, d, sig.timestamp, entry_spot, direction)
                if rp is not None:
                    rp_entry = rp

            # Simulate trade forward from ci+1
            exit_reason = None
            exit_ci = dd.n - 1
            exit_prem = 0.0
            exit_spot = dd.closes[-1]

            for j in range(ci + 1, dd.n):
                jt_min = dd.minutes[j]
                spot_j = dd.closes[j]
                high_j = dd.highs[j]
                low_j = dd.lows[j]

                # Current premium
                curr_prem = premium_at_spot(entry_prem, spot_j - entry_spot, direction)
                # Worst/best premium in candle
                if direction == "CE":
                    worst_prem = premium_at_spot(entry_prem, low_j - entry_spot, direction)
                    best_prem = premium_at_spot(entry_prem, high_j - entry_spot, direction)
                else:
                    worst_prem = premium_at_spot(entry_prem, high_j - entry_spot, direction)
                    best_prem = premium_at_spot(entry_prem, low_j - entry_spot, direction)

                dur_min = (j - ci) * 3

                # SL
                if worst_prem <= trail_sl:
                    exit_reason = "SL"
                    exit_prem = trail_sl
                    exit_ci = j
                    exit_spot = spot_j
                    break

                # Target
                if best_prem >= tgt_prem:
                    exit_reason = "TARGET"
                    exit_prem = tgt_prem
                    exit_ci = j
                    exit_spot = spot_j
                    break

                # Trail update
                gain_pct = (best_prem - entry_prem) / entry_prem * 100
                if gain_pct >= trail_lock:
                    new_sl = entry_prem * (1 + trail_lock_val / 100)
                    trail_sl = max(trail_sl, new_sl)
                elif gain_pct >= trail_be:
                    trail_sl = max(trail_sl, entry_prem)

                # Time exits
                if dur_min >= te2:
                    exit_reason = "TIME_EXIT"
                    exit_prem = curr_prem
                    exit_ci = j
                    exit_spot = spot_j
                    break
                elif dur_min >= te1:
                    cpct = (curr_prem - entry_prem) / entry_prem * 100
                    if cpct < te1_req:
                        exit_reason = "TIME_EXIT"
                        exit_prem = curr_prem
                        exit_ci = j
                        exit_spot = spot_j
                        break

                # EOD (15:12 = 912 min)
                if jt_min >= 912:
                    exit_reason = "EOD"
                    exit_prem = curr_prem
                    exit_ci = j
                    exit_spot = spot_j
                    break

            if exit_reason is None:
                exit_reason = "EOD"
                exit_prem = premium_at_spot(entry_prem, dd.closes[-1] - entry_spot, direction)
                exit_ci = dd.n - 1
                exit_spot = dd.closes[-1]

            pnl = (exit_prem - entry_prem) * LOT_SIZE - BROKERAGE

            # Real premium exit
            rp_exit = 0.0
            rp_pnl = 0.0
            if rp_entry > 0 and d in real_premiums:
                rpe = get_real_premium(real_premiums, d, dd.timestamps[exit_ci], exit_spot, direction)
                if rpe is not None:
                    rp_exit = rpe
                    rp_pnl = (rpe - rp_entry) * LOT_SIZE - BROKERAGE

            t = Trade(
                signal=sig,
                entry_premium=entry_prem,
                exit_time=dd.timestamps[exit_ci],
                exit_spot=exit_spot,
                exit_premium=exit_prem,
                exit_reason=exit_reason,
                premium_pnl=pnl,
                real_prem_entry=rp_entry,
                real_prem_exit=rp_exit,
                real_prem_pnl=rp_pnl,
            )
            trades.append(t)
            day_pnl_val += pnl
            day_trade_count += 1
            last_exit_idx = exit_ci
            if pnl < 0:
                had_loss = True

        daily_pnl[d] = day_pnl_val
        eq += day_pnl_val
        equity_points.append(eq)
        peak = max(peak, eq)
        dd_val = peak - eq
        # DD% relative to deployment capital (not peak equity which can be tiny)
        dd_pct = dd_val / CAPITAL * 100
        max_dd = max(max_dd, dd_val)
        max_dd_pct = max(max_dd_pct, dd_pct)

    return trades, equity_points, daily_pnl, max_dd, max_dd_pct


# ─── Metrics ──────────────────────────────────────────────────────────────────
def compute_metrics(trades, equity, daily_pnl, max_dd, max_dd_pct):
    if not trades:
        return {"total": 0, "wr": 0, "pf": 0, "net": 0, "avg": 0,
                "avg_w": 0, "avg_l": 0, "dd": 0, "dd_pct": 0,
                "sharpe": 0, "calmar": 0, "mcw": 0, "mcl": 0,
                "wd": 0, "ld": 0, "dd_dur": 0}
    w = [t for t in trades if t.premium_pnl > 0]
    l = [t for t in trades if t.premium_pnl <= 0]
    tw = sum(t.premium_pnl for t in w) if w else 0
    tl = abs(sum(t.premium_pnl for t in l)) if l else 0
    net = sum(t.premium_pnl for t in trades)
    wr = len(w) / len(trades) * 100
    pf = tw / tl if tl > 0 else float('inf')
    nz = [v for v in daily_pnl.values() if v != 0]
    sharpe = (np.mean(nz) / np.std(nz)) * np.sqrt(252) if len(nz) > 1 and np.std(nz) > 0 else 0
    ann = net * 252 / max(len(daily_pnl), 1)
    calmar = ann / max_dd if max_dd > 0 else 0

    cw = cl = mcw = mcl = 0
    for t in trades:
        if t.premium_pnl > 0:
            cw += 1; cl = 0; mcw = max(mcw, cw)
        else:
            cl += 1; cw = 0; mcl = max(mcl, cl)
    wd = sum(1 for v in daily_pnl.values() if v > 0)
    ld = sum(1 for v in daily_pnl.values() if v < 0)

    dd_s = None; dd_md = 0; pk = 0
    for i, e in enumerate(equity):
        if e >= pk:
            pk = e
            if dd_s is not None:
                dd_md = max(dd_md, i - dd_s)
            dd_s = None
        elif dd_s is None:
            dd_s = i

    return {
        "total": len(trades), "wr": wr, "pf": pf, "net": net,
        "avg": net / len(trades),
        "avg_w": tw / len(w) if w else 0,
        "avg_l": -tl / len(l) if l else 0,
        "dd": max_dd, "dd_pct": max_dd_pct,
        "sharpe": sharpe, "calmar": calmar,
        "mcw": mcw, "mcl": mcl, "wd": wd, "ld": ld, "dd_dur": dd_md,
    }


# ─── Rally Discovery ─────────────────────────────────────────────────────────
def discover_rallies(all_dates, day_data):
    rbm = defaultdict(list)
    rbh = defaultdict(list)
    dirs = {"UP": 0, "DOWN": 0}
    total = 0

    for d in all_dates:
        if d not in day_data:
            continue
        dd = day_data[d]
        mo = d[:7]
        for i in range(dd.n):
            for j in range(i+1, min(i+16, dd.n)):
                mv = dd.closes[j] - dd.closes[i]
                if abs(mv) >= 30:
                    dr = "UP" if mv > 0 else "DOWN"
                    dirs[dr] += 1
                    rbm[mo].append(abs(mv))
                    rbh[dd.timestamps[i].hour].append(abs(mv))
                    total += 1
                    break
    avg = total / len(all_dates) if all_dates else 0
    return total, rbm, rbh, dirs, avg


# ─── Reporting ────────────────────────────────────────────────────────────────
def print_rally_report(total, rbm, rbh, dirs, avg):
    print()
    print("=" * 70)
    print("  PHASE 1: RALLY DISCOVERY (30+ pt moves within 45 min)")
    print("=" * 70)
    print(f"  Total rallies:    {total:,}")
    print(f"  Per day avg:      {avg:.1f}")
    print(f"  UP / DOWN:        {dirs['UP']:,} / {dirs['DOWN']:,}")

    print(f"\n  {'Month':<10} {'Count':>6} {'Avg':>6} {'Max':>6}")
    print(f"  {'-'*10} {'-'*6} {'-'*6} {'-'*6}")
    for m in sorted(rbm.keys()):
        v = rbm[m]
        print(f"  {m:<10} {len(v):>6} {np.mean(v):>6.1f} {max(v):>6.1f}")

    print(f"\n  {'Hour':<6} {'Count':>6} {'Avg':>6}")
    print(f"  {'-'*6} {'-'*6} {'-'*6}")
    for h in sorted(rbh.keys()):
        v = rbh[h]
        print(f"  {h:02d}:00  {len(v):>6} {np.mean(v):>6.1f}")


def print_full_report(trades, equity, daily_pnl, max_dd, max_dd_pct,
                      all_dates, params, signals, title="STRATEGY RESULTS"):
    m = compute_metrics(trades, equity, daily_pnl, max_dd, max_dd_pct)
    print()
    print("=" * 70)
    print(f"  {title}")
    print(f"  {len(all_dates)} Trading Days | Jan 2025 - Mar 2026")
    if params:
        print(f"  SL={params['sl_prem_pct']}% Tgt={params['target_prem_pct']}% MaxT={params['max_trades_day']} TF={params['tf_alignment_required']} CD={params['cooldown_min']}min")
        print(f"  Signals: {', '.join(signals)}")
    print("=" * 70)

    print(f"\n  EXECUTIVE SUMMARY:")
    print(f"  {'-'*60}")
    print(f"  Net P&L:           Rs {m['net']:>12,.0f}")
    print(f"  Total Trades:      {m['total']:>8}")
    print(f"  Win Rate:          {m['wr']:>10.1f}%")
    print(f"  Profit Factor:     {m['pf']:>10.2f}")
    print(f"  Max Drawdown:      Rs {m['dd']:>12,.0f} ({m['dd_pct']:.1f}% of capital)")
    print(f"  Sharpe Ratio:      {m['sharpe']:>10.2f}")
    print(f"  Calmar Ratio:      {m['calmar']:>10.2f}")
    print(f"  Avg Win:           Rs {m['avg_w']:>12,.0f}")
    print(f"  Avg Loss:          Rs {m['avg_l']:>12,.0f}")

    # By Signal
    print(f"\n  BY SIGNAL:")
    print(f"  {'Sig':<5} {'Tr':>5} {'WR%':>7} {'PF':>7} {'Avg':>9} {'Total':>12}")
    print(f"  {'-'*5} {'-'*5} {'-'*7} {'-'*7} {'-'*9} {'-'*12}")
    for sig in ALL_SIGNALS:
        st = [t for t in trades if t.signal.signal_type == sig]
        if not st:
            print(f"  {sig:<5} {'0':>5}")
            continue
        sw = [t for t in st if t.premium_pnl > 0]
        stw = sum(t.premium_pnl for t in sw) if sw else 0
        stl = abs(sum(t.premium_pnl for t in st if t.premium_pnl <= 0))
        swr = len(sw) / len(st) * 100
        spf = stw / stl if stl > 0 else float('inf')
        sn = sum(t.premium_pnl for t in st)
        sa = sn / len(st)
        print(f"  {sig:<5} {len(st):>5} {swr:>6.1f}% {spf:>7.2f} {sa:>9,.0f} {sn:>12,.0f}")

    # By Direction
    print(f"\n  BY DIRECTION:")
    for dr in ["CE", "PE"]:
        dt = [t for t in trades if t.signal.direction == dr]
        if not dt:
            continue
        dw = [t for t in dt if t.premium_pnl > 0]
        dwr = len(dw) / len(dt) * 100
        dn = sum(t.premium_pnl for t in dt)
        print(f"  {dr}: {len(dt)} trades, WR={dwr:.1f}%, P&L=Rs {dn:,.0f}")

    # By Month
    print(f"\n  BY MONTH:")
    print(f"  {'Mon':<8} {'Tr':>4} {'WR%':>6} {'Net':>12} {'DD':>10}")
    print(f"  {'-'*8} {'-'*4} {'-'*6} {'-'*12} {'-'*10}")
    tbm = defaultdict(list)
    for t in trades:
        tbm[t.signal.timestamp.strftime("%Y-%m")].append(t)
    for mo in sorted(tbm.keys()):
        mt = tbm[mo]
        mw = [t for t in mt if t.premium_pnl > 0]
        mwr = len(mw) / len(mt) * 100
        mn = sum(t.premium_pnl for t in mt)
        me = 0; mp = 0; md = 0
        for t in mt:
            me += t.premium_pnl; mp = max(mp, me); md = max(md, mp - me)
        print(f"  {mo:<8} {len(mt):>4} {mwr:>5.1f}% {mn:>12,.0f} {md:>10,.0f}")

    # Time of Day
    print(f"\n  TIME OF DAY:")
    for h in range(9, 15):
        ht = [t for t in trades if t.signal.timestamp.hour == h]
        if not ht:
            continue
        hw = [t for t in ht if t.premium_pnl > 0]
        hwr = len(hw) / len(ht) * 100
        hn = sum(t.premium_pnl for t in ht)
        print(f"  {h:02d}:00 - {len(ht)} trades, WR={hwr:.1f}%, P&L=Rs {hn:,.0f}")

    # Exit Analysis
    print(f"\n  EXIT ANALYSIS:")
    print(f"  {'Reason':<12} {'Count':>5} {'WR%':>6} {'Avg':>10}")
    print(f"  {'-'*12} {'-'*5} {'-'*6} {'-'*10}")
    er = defaultdict(list)
    for t in trades:
        er[t.exit_reason].append(t)
    for reason in sorted(er.keys()):
        rt = er[reason]
        rw = [t for t in rt if t.premium_pnl > 0]
        rwr = len(rw) / len(rt) * 100
        ra = sum(t.premium_pnl for t in rt) / len(rt)
        print(f"  {reason:<12} {len(rt):>5} {rwr:>5.1f}% {ra:>10,.0f}")

    # Monthly equity
    print(f"\n  MONTHLY EQUITY:")
    meq = defaultdict(float)
    running = 0; mstart = {}
    for dd in sorted(daily_pnl.keys()):
        mo = dd[:7]
        if mo not in mstart:
            mstart[mo] = running
        running += daily_pnl[dd]
        meq[mo] = running
    print(f"  {'Mon':<8} {'Start':>10} {'End':>10} {'Chg':>10}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10}")
    for mo in sorted(meq.keys()):
        s = mstart.get(mo, 0); e = meq[mo]; c = e - s
        print(f"  {mo:<8} {s:>10,.0f} {e:>10,.0f} {c:>10,.0f}")

    # Daily for last 32 days
    rpd = sorted([dd for dd in daily_pnl if dd >= "2026-01-30"])
    if rpd:
        print(f"\n  DAILY P&L (Last 32 days):")
        print(f"  {'Date':<12} {'Tr':>4} {'P&L':>10}")
        print(f"  {'-'*12} {'-'*4} {'-'*10}")
        for dd in rpd:
            dt = [t for t in trades if t.signal.date == dd]
            print(f"  {dd:<12} {len(dt):>4} {daily_pnl[dd]:>10,.0f}")

    # Risk
    print(f"\n  RISK METRICS:")
    print(f"  {'-'*50}")
    print(f"  Max Drawdown:      Rs {m['dd']:>10,.0f} ({m['dd_pct']:.1f}% of capital)")
    print(f"  DD Duration:       {m['dd_dur']:>6} days")
    print(f"  Sharpe:            {m['sharpe']:>8.2f}")
    print(f"  Calmar:            {m['calmar']:>8.2f}")
    print(f"  Win/Loss Days:     {m['wd']}/{m['ld']}")
    print(f"  Max Consec W/L:    {m['mcw']}/{m['mcl']}")

    # ASCII equity
    print(f"\n  EQUITY CURVE (monthly):")
    pts = [(mo, meq[mo]) for mo in sorted(meq.keys())]
    if pts:
        mn_v = min(p[1] for p in pts)
        mx_v = max(p[1] for p in pts)
        w = 40
        for mo, ev in pts:
            bl = int((ev - mn_v) / (mx_v - mn_v) * w) if mx_v > mn_v else w // 2
            bl = max(bl, 0)
            print(f"  {mo} |{'#' * bl} Rs {ev:,.0f}")

    # Quarterly
    print(f"\n  QUARTERLY ROBUSTNESS:")
    quarters = [("Q1-25", "2025-01", "2025-03"), ("Q2-25", "2025-04", "2025-06"),
                ("Q3-25", "2025-07", "2025-09"), ("Q4-25", "2025-10", "2025-12"),
                ("Q1-26", "2026-01", "2026-03")]
    print(f"  {'Qtr':<7} {'Tr':>4} {'WR%':>6} {'Net':>12} {'DD':>10}")
    print(f"  {'-'*7} {'-'*4} {'-'*6} {'-'*12} {'-'*10}")
    pq = 0
    for qn, qs, qe in quarters:
        qt = [t for t in trades if qs <= t.signal.timestamp.strftime("%Y-%m") <= qe]
        if not qt:
            print(f"  {qn:<7} {'0':>4}")
            continue
        qw = [t for t in qt if t.premium_pnl > 0]
        qwr = len(qw) / len(qt) * 100
        qn_v = sum(t.premium_pnl for t in qt)
        qe_v = 0; qp = 0; qd = 0
        for t in qt:
            qe_v += t.premium_pnl; qp = max(qp, qe_v); qd = max(qd, qp - qe_v)
        print(f"  {qn:<7} {len(qt):>4} {qwr:>5.1f}% {qn_v:>12,.0f} {qd:>10,.0f}")
        if qn_v > 0:
            pq += 1
    tag = "[PASS]" if pq >= 3 else "[FAIL]"
    print(f"\n  Profitable quarters: {pq}/5 {tag}")

    return m


def print_sim_vs_real(trades, real_premiums):
    print()
    print("=" * 70)
    print("  PHASE 4: SIMULATION vs REALITY (32 days)")
    print("=" * 70)
    rt = [t for t in trades if t.real_prem_entry > 0 and t.real_prem_exit > 0]
    if not rt:
        pt = [t for t in trades if t.signal.date >= "2026-01-30"]
        we = [t for t in trades if t.real_prem_entry > 0]
        print(f"\n  No trades with full real premium data.")
        print(f"  Trades in period: {len(pt)}, with real entry: {len(we)}")
        return

    print(f"\n  Trades matched: {len(rt)}")
    print(f"\n  {'Date':<12} {'Sig':<4} {'Dir':<3} {'Sim':>10} {'Real':>10} {'Err':>7}")
    print(f"  {'-'*12} {'-'*4} {'-'*3} {'-'*10} {'-'*10} {'-'*7}")
    ts = tr = 0; errs = []
    for t in rt:
        s = t.premium_pnl; r = t.real_prem_pnl
        err = abs(s - r) / max(abs(r), 1) * 100
        errs.append(err)
        ts += s; tr += r
        dd = t.signal.date
        print(f"  {dd:<12} {t.signal.signal_type:<4} {t.signal.direction:<3} {s:>10,.0f} {r:>10,.0f} {err:>6.1f}%")
    print(f"\n  Simulated total: Rs {ts:>10,.0f}")
    print(f"  Real total:      Rs {tr:>10,.0f}")
    print(f"  Mean error:      {np.mean(errs):>8.1f}%")
    print(f"  Median error:    {np.median(errs):>8.1f}%")


# ─── Phase 3: Optimization ──────────────────────────────────────────────────
def run_optimization(all_dates, day_data, signals_by_day, real_premiums):
    print()
    print("=" * 70)
    print("  PHASE 3: PARAMETER OPTIMIZATION")
    print("  Target: WR >= 51%, PF > 1.0, DD <= 10%")
    print("=" * 70)

    sl_vals = [8, 10, 12, 15, 18]
    tgt_vals = [8, 10, 12, 15, 18, 22, 25]
    max_t_vals = [1, 2, 3]
    tf_vals = [1, 2]
    cd_vals = [6, 12, 18]

    sig_subsets = [
        ["ORB"], ["VB"], ["SR"], ["MC"], ["CB"],
        ["ORB", "VB"], ["ORB", "MC"], ["VB", "MC"], ["VB", "SR"],
        ["VB", "CB"], ["MC", "CB"], ["ORB", "CB"],
        ["ORB", "VB", "MC"], ["ORB", "VB", "SR"], ["VB", "MC", "CB"],
        ["ORB", "VB", "SR", "MC"], ["ORB", "VB", "MC", "CB"],
        ALL_SIGNALS,
    ]

    total = len(sl_vals) * len(tgt_vals) * len(max_t_vals) * len(tf_vals) * len(cd_vals) * len(sig_subsets)
    print(f"\n  Testing {total:,} combinations...")
    sys.stdout.flush()

    valid = []
    all_combos = []
    tested = 0

    for sl in sl_vals:
        for tgt in tgt_vals:
            for max_t in max_t_vals:
                for tf in tf_vals:
                    for cd in cd_vals:
                        for sigs in sig_subsets:
                            tested += 1
                            if tested % 1000 == 0:
                                print(f"\r  {tested:,}/{total:,} ({len(valid)} valid)", end="")
                                sys.stdout.flush()

                            p = DEFAULT_PARAMS.copy()
                            p["sl_prem_pct"] = sl
                            p["target_prem_pct"] = tgt
                            p["max_trades_day"] = max_t
                            p["tf_alignment_required"] = tf
                            p["tf_alignment_after_loss"] = min(tf + 1, 3)
                            p["cooldown_min"] = cd
                            p["trail_be_pct"] = max(int(tgt * 0.5), 3)
                            p["trail_lock_pct"] = max(int(tgt * 0.75), 5)
                            p["trail_lock_val_pct"] = max(int(tgt * 0.3), 2)

                            trades, eq, dpnl, mdd, mddp = run_backtest(
                                all_dates, day_data, signals_by_day, real_premiums,
                                params=p, enabled_signals=sigs
                            )
                            if not trades or len(trades) < 15:
                                continue

                            met = compute_metrics(trades, eq, dpnl, mdd, mddp)
                            combo = {
                                "sl": sl, "tgt": tgt, "mt": max_t, "tf": tf,
                                "cd": cd, "sigs": ",".join(sigs),
                                "tr": met["total"], "wr": met["wr"], "pf": met["pf"],
                                "net": met["net"], "ddp": mddp, "sh": met["sharpe"],
                                "params": p.copy(), "enabled": list(sigs),
                            }
                            all_combos.append(combo)
                            if met["wr"] >= 51 and met["pf"] > 1.0 and mddp <= 10.0:
                                valid.append(combo)

    print(f"\r  Tested {tested:,} combinations" + " " * 30)
    print(f"  Valid: {len(valid)}")

    if valid:
        valid.sort(key=lambda x: x["net"], reverse=True)
        _print_combo_table(valid[:25], "TOP 25 VALID COMBINATIONS")
        best = valid[0]
        print(f"\n  BEST: SL={best['sl']}% Tgt={best['tgt']}% MT={best['mt']} TF={best['tf']} CD={best['cd']}")
        print(f"  Signals: {best['sigs']}")
        print(f"  {best['tr']}T WR={best['wr']:.1f}% PF={best['pf']:.2f} P&L=Rs {best['net']:,.0f} DD={best['ddp']:.1f}%")
        return best
    else:
        all_combos.sort(key=lambda x: x["net"], reverse=True)
        _print_combo_table(all_combos[:25], "TOP 25 BY P&L (constraints not met)")

        # Find best compromise
        for c in all_combos:
            c["score"] = 0
            if c["wr"] >= 51: c["score"] += 1
            if c["pf"] > 1.0: c["score"] += 1
            if c["ddp"] <= 10: c["score"] += 1
            c["score"] += min(c["wr"], 55) / 55 * 0.3
            c["score"] += min(c["pf"], 1.5) / 1.5 * 0.3
        all_combos.sort(key=lambda x: (x["score"], x["net"]), reverse=True)
        best = all_combos[0]
        print(f"\n  BEST COMPROMISE: SL={best['sl']}% Tgt={best['tgt']}% MT={best['mt']} TF={best['tf']} CD={best['cd']}")
        print(f"  Signals: {best['sigs']}")
        print(f"  {best['tr']}T WR={best['wr']:.1f}% PF={best['pf']:.2f} P&L=Rs {best['net']:,.0f} DD={best['ddp']:.1f}%")
        return best


def _print_combo_table(combos, title):
    print(f"\n  {title}:")
    print(f"  {'#':>3} {'SL':>3} {'Tgt':>4} {'MT':>3} {'TF':>3} {'CD':>3} {'Signals':<18} {'Tr':>4} {'WR%':>6} {'PF':>5} {'Net':>12} {'DD%':>6}")
    print(f"  {'-'*3} {'-'*3} {'-'*4} {'-'*3} {'-'*3} {'-'*3} {'-'*18} {'-'*4} {'-'*6} {'-'*5} {'-'*12} {'-'*6}")
    for i, c in enumerate(combos):
        sigs_short = c['sigs'][:18]
        print(f"  {i+1:>3} {c['sl']:>3} {c['tgt']:>4} {c['mt']:>3} {c['tf']:>3} {c['cd']:>3} {sigs_short:<18} {c['tr']:>4} {c['wr']:>5.1f}% {c['pf']:>5.2f} {c['net']:>12,.0f} {c['ddp']:>5.1f}%")


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("  RALLY CATCHER COMPREHENSIVE BACKTEST")
    print("  300 Trading Days | 65 qty | Rs 72 brokerage")
    print("  Premium: delta=0.65 calibrated | ITM ~230 Rs")
    print("=" * 70)

    print("\n[1/6] Loading data...")
    all_dates, day_data = load_data()
    real_premiums = load_real_premiums()

    print("\n[2/6] Computing contexts...")
    contexts = compute_contexts(all_dates, day_data)

    print("\n[3/6] Discovering rallies...")
    tot, rbm, rbh, dirs, avg = discover_rallies(all_dates, day_data)
    print_rally_report(tot, rbm, rbh, dirs, avg)

    print("\n[4/6] Pre-scanning signals...")
    signals_by_day = prescan_signals(all_dates, day_data, contexts)

    print("\n[5/6] Running default strategy...")
    trades, eq, dpnl, mdd, mddp = run_backtest(
        all_dates, day_data, signals_by_day, real_premiums,
        DEFAULT_PARAMS, ALL_SIGNALS
    )
    m = print_full_report(trades, eq, dpnl, mdd, mddp, all_dates,
                         DEFAULT_PARAMS, ALL_SIGNALS, "PHASE 2: DEFAULT RESULTS")

    ok = m["wr"] >= 51 and m["pf"] > 1.0 and mddp <= 10.0
    print(f"\n  CONSTRAINT CHECK:")
    print(f"  WR >= 51%:  {m['wr']:.1f}% {'[PASS]' if m['wr'] >= 51 else '[FAIL]'}")
    print(f"  PF > 1.0:   {m['pf']:.2f} {'[PASS]' if m['pf'] > 1.0 else '[FAIL]'}")
    print(f"  DD <= 10%:  {mddp:.1f}% {'[PASS]' if mddp <= 10 else '[FAIL]'}")

    if not ok:
        print("\n[6/6] Running optimization...")
        best = run_optimization(all_dates, day_data, signals_by_day, real_premiums)

        if best:
            print("\n  Re-running with optimized params...")
            t2, e2, d2, md2, mdp2 = run_backtest(
                all_dates, day_data, signals_by_day, real_premiums,
                best["params"], best["enabled"]
            )
            m2 = print_full_report(t2, e2, d2, md2, mdp2, all_dates,
                                  best["params"], best["enabled"],
                                  "OPTIMIZED STRATEGY RESULTS")

            print(f"\n  OPTIMIZED CONSTRAINT CHECK:")
            print(f"  WR >= 51%:  {m2['wr']:.1f}% {'[PASS]' if m2['wr'] >= 51 else '[FAIL]'}")
            print(f"  PF > 1.0:   {m2['pf']:.2f} {'[PASS]' if m2['pf'] > 1.0 else '[FAIL]'}")
            print(f"  DD <= 10%:  {mdp2:.1f}% {'[PASS]' if mdp2 <= 10 else '[FAIL]'}")

            print_sim_vs_real(t2, real_premiums)
    else:
        print("\n[6/6] All constraints met!")
        print_sim_vs_real(trades, real_premiums)

    print()
    print("=" * 70)
    print("  BACKTEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
