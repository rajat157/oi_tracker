"""
Regime Reclassification Backtest
=================================
Compares daily (once at open) vs mid-day (reclassify at 12:00) regime classification.

Uses 3-min NIFTY + VIX data from nifty_history/vix_history tables.
Detects MC/MOM/VWAP signals, simulates trades with regime-specific params.

Premium model: 100pts ITM, delta=0.56 CE / 0.58 PE (calibrated from real data).
"""

import sqlite3
import sys
import os
import numpy as np
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "oi_tracker.db")

LOT_SIZE = 65
BROKERAGE = 72
NIFTY_STEP = 50

# Regime params (same as config.py RR_REGIME_PARAMS)
REGIME_PARAMS = {
    "HIGH_VOL_DOWN": {"signals": {"MC", "MOM"}, "sl_pts": 30, "tgt_pts": 40, "max_hold": 15,
                      "direction": "CE_ONLY", "time_start": 630, "time_end": 840,
                      "cooldown": 8, "max_trades": 2},
    "HIGH_VOL_UP":   {"signals": {"MC", "VWAP"}, "sl_pts": 25, "tgt_pts": 35, "max_hold": 35,
                      "direction": "PE_ONLY", "time_start": 585, "time_end": 855,
                      "cooldown": 8, "max_trades": 2},
    "LOW_VOL":       {"signals": {"MC"}, "sl_pts": 40, "tgt_pts": 25, "max_hold": 40,
                      "direction": "PE_ONLY", "time_start": 630, "time_end": 840,
                      "cooldown": 12, "max_trades": 1},
    "NORMAL":        {"signals": {"MC", "MOM"}, "sl_pts": 40, "tgt_pts": 20, "max_hold": 35,
                      "direction": "BOTH", "time_start": 585, "time_end": 855,
                      "cooldown": 8, "max_trades": 3},
    "TRENDING_DOWN": {"signals": {"MOM", "VWAP"}, "sl_pts": 40, "tgt_pts": 50, "max_hold": 30,
                      "direction": "PE_ONLY", "time_start": 570, "time_end": 870,
                      "cooldown": 6, "max_trades": 3},
    "TRENDING_UP":   {"signals": {"MC", "MOM"}, "sl_pts": 40, "tgt_pts": 50, "max_hold": 40,
                      "direction": "CE_ONLY", "time_start": 570, "time_end": 870,
                      "cooldown": 6, "max_trades": 3},
}


# ── Regime Classification ─────────────────────────────────────────────────────

def classify_regime(daily_ranges, daily_returns, avg_vix):
    """Classify market regime from recent history."""
    if len(daily_ranges) < 2 or len(daily_returns) < 1:
        return "NORMAL"

    avg_range = np.mean(daily_ranges)
    net_return = sum(daily_returns)
    avg_abs_return = np.mean(np.abs(daily_returns)) if daily_returns else 1
    trend_strength = abs(net_return) / (avg_abs_return * len(daily_returns) + 1e-9)

    if avg_vix > 16 or avg_range > 250:
        if net_return > 100:
            return "HIGH_VOL_UP"
        elif net_return < -100:
            return "HIGH_VOL_DOWN"
        return "HIGH_VOL_DOWN"
    if avg_vix < 12 or avg_range < 120:
        return "LOW_VOL"
    if trend_strength > 0.15 and net_return > 150:
        return "TRENDING_UP"
    if trend_strength > 0.15 and net_return < -150:
        return "TRENDING_DOWN"
    return "NORMAL"


def get_regime_for_day(day_idx, day_stats, lookback=5):
    """Classify regime using previous N days' stats."""
    start = max(0, day_idx - lookback)
    end = day_idx
    if end <= start:
        return "NORMAL"

    ranges = [d["range"] for d in day_stats[start:end]]
    # Returns = close[i] - close[i-1]
    closes = [d["close"] for d in day_stats[start:end]]
    returns = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    vix_vals = [d["avg_vix"] for d in day_stats[start:end]]
    avg_vix = np.mean(vix_vals) if vix_vals else 13.5

    return classify_regime(ranges, returns, avg_vix)


def get_regime_midday(day_idx, day_stats, today_candles, lookback=5):
    """Reclassify regime at midday using morning data + previous days.

    Replaces the oldest lookback day with today's morning stats.
    """
    start = max(0, day_idx - lookback + 1)  # one fewer historical day
    end = day_idx
    if end < start:
        return "NORMAL"

    # Previous days
    ranges = [d["range"] for d in day_stats[start:end]]
    closes = [d["close"] for d in day_stats[start:end]]
    vix_vals = [d["avg_vix"] for d in day_stats[start:end]]

    # Add today's morning (up to 12:00)
    morning = [c for c in today_candles if c["mins"] < 720]
    if morning:
        morning_high = max(c["high"] for c in morning)
        morning_low = min(c["low"] for c in morning)
        morning_range = morning_high - morning_low
        morning_close = morning[-1]["close"]
        morning_vix = np.mean([c["vix"] for c in morning if c["vix"] > 0])

        ranges.append(morning_range)
        closes.append(morning_close)
        vix_vals.append(morning_vix)

    if len(closes) < 2:
        return "NORMAL"

    returns = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_vix = np.mean(vix_vals) if vix_vals else 13.5

    return classify_regime(ranges, returns, avg_vix)


# ── Signal Detection ──────────────────────────────────────────────────────────

def detect_signals(closes, mins, vix_arr, day_open, regime_config):
    """Detect MC/MOM/VWAP signals at each candle."""
    signals = []
    n = len(closes)
    if n < 10:
        return signals

    allowed = regime_config["signals"]
    direction_filter = regime_config["direction"]
    time_start = regime_config["time_start"]
    time_end = regime_config["time_end"]

    # Cumulative VWAP
    cum = np.cumsum(closes)
    vwap = cum / np.arange(1, n + 1)

    for i in range(9, n):
        t = mins[i]
        if t < time_start or t > time_end:
            continue

        spot = closes[i]
        fired = []

        # MC signal
        if "MC" in allowed:
            move = spot - day_open
            if abs(move) >= 25:
                rally_dir = "UP" if move > 0 else "DOWN"
                peak = float(np.max(closes[:i + 1])) if rally_dir == "UP" else float(np.min(closes[:i + 1]))
                rally_pts = abs(peak - day_open)
                if rally_pts > 0:
                    recent_n = min(8, i)
                    recent = closes[i - recent_n:i + 1]
                    if rally_dir == "UP":
                        pb_pts = peak - float(np.min(recent))
                    else:
                        pb_pts = float(np.max(recent)) - peak
                    pb_pct = pb_pts / rally_pts
                    resum = (closes[i] > closes[i - 1]) if rally_dir == "UP" else (closes[i] < closes[i - 1])
                    if 0.15 <= pb_pct <= 0.70 and resum:
                        d = "CE" if rally_dir == "UP" else "PE"
                        fired.append(("MC", d))

        # MOM signal
        if "MOM" in allowed and i >= 4:
            if all(closes[i - j] > closes[i - j - 1] for j in range(4)):
                fired.append(("MOM", "CE"))
            if all(closes[i - j] < closes[i - j - 1] for j in range(4)):
                fired.append(("MOM", "PE"))

        # VWAP signal
        if "VWAP" in allowed and i >= 2:
            if closes[i - 1] < vwap[i - 1] and closes[i] > vwap[i] and (closes[i] - vwap[i]) > 3:
                fired.append(("VWAP", "CE"))
            if closes[i - 1] > vwap[i - 1] and closes[i] < vwap[i] and (vwap[i] - closes[i]) > 3:
                fired.append(("VWAP", "PE"))

        # Direction filter
        for sig_type, direction in fired:
            if direction_filter == "BOTH" or \
               (direction_filter == "CE_ONLY" and direction == "CE") or \
               (direction_filter == "PE_ONLY" and direction == "PE"):
                signals.append({
                    "idx": i, "type": sig_type, "direction": direction,
                    "spot": spot, "mins": t,
                    "vix": float(vix_arr[i]) if i < len(vix_arr) else 13.5,
                })

    return signals


# ── Trade Simulation ──────────────────────────────────────────────────────────

def simulate_trade(closes, entry_idx, direction, sl_pts, tgt_pts, max_hold, max_duration=45):
    """Simulate a single trade from entry_idx forward. Returns (pnl_rs, hold_min, exit_reason)."""
    n = len(closes)
    entry_spot = closes[entry_idx]

    remaining = min(max_duration, n - 1 - entry_idx)
    if remaining < 1:
        return 0, 0, "NO_DATA"

    for offset in range(1, remaining + 1):
        current = closes[entry_idx + offset]

        if direction == "CE":
            spot_move = current - entry_spot
        else:
            spot_move = entry_spot - current

        # Premium P&L approximation
        delta = 0.56 if direction == "CE" else 0.58
        prem_pnl = spot_move * delta * LOT_SIZE - BROKERAGE

        # Check SL (spot pts)
        if direction == "CE" and (current - entry_spot) <= -sl_pts:
            return prem_pnl, offset * 3, "SL"
        if direction == "PE" and (entry_spot - current) <= -sl_pts:
            return prem_pnl, offset * 3, "SL"

        # Check target (spot pts)
        if direction == "CE" and (current - entry_spot) >= tgt_pts:
            return prem_pnl, offset * 3, "TGT"
        if direction == "PE" and (entry_spot - current) >= tgt_pts:
            return prem_pnl, offset * 3, "TGT"

        # Time exit: flat after max_hold
        hold_min = offset * 3
        if hold_min >= max_hold and abs(spot_move) < 5:
            return prem_pnl, hold_min, "TIME_FLAT"

        # Max duration
        if hold_min >= max_duration * 3:
            return prem_pnl, hold_min, "MAX_TIME"

    # End of data
    current = closes[min(entry_idx + remaining, n - 1)]
    if direction == "CE":
        spot_move = current - entry_spot
    else:
        spot_move = entry_spot - current
    delta = 0.56 if direction == "CE" else 0.58
    prem_pnl = spot_move * delta * LOT_SIZE - BROKERAGE
    return prem_pnl, remaining * 3, "EOD"


# ── Data Loading ──────────────────────────────────────────────────────────────

def load_data():
    """Load all 3-min NIFTY + VIX data, grouped by day."""
    conn = sqlite3.connect(DB_PATH)

    print("Loading data...", flush=True)
    nifty = conn.execute(
        "SELECT timestamp, open, high, low, close FROM nifty_history ORDER BY timestamp"
    ).fetchall()

    vix_map = {}
    for row in conn.execute("SELECT timestamp, close FROM vix_history ORDER BY timestamp"):
        vix_map[row[0]] = row[1]

    conn.close()
    print(f"  {len(nifty):,} NIFTY candles, {len(vix_map):,} VIX values", flush=True)

    # Group by day
    days = {}
    for ts, op, hi, lo, cl in nifty:
        dt = ts[:10]
        if dt not in days:
            days[dt] = []
        t = ts[11:16]
        hour, minute = int(t[:2]), int(t[3:5])
        mins = hour * 60 + minute
        days[dt].append({
            "ts": ts, "open": op, "high": hi, "low": lo, "close": cl,
            "vix": vix_map.get(ts, 0), "mins": mins,
        })

    # Sort days and compute daily stats
    sorted_dates = sorted(days.keys())
    day_stats = []
    day_candles = []

    for dt in sorted_dates:
        candles = sorted(days[dt], key=lambda c: c["mins"])
        if len(candles) < 10:
            continue

        day_high = max(c["high"] for c in candles)
        day_low = min(c["low"] for c in candles)
        day_close = candles[-1]["close"]
        day_open = candles[0]["open"]
        vix_vals = [c["vix"] for c in candles if c["vix"] > 0]
        avg_vix = np.mean(vix_vals) if vix_vals else 13.5

        day_stats.append({
            "date": dt, "range": day_high - day_low,
            "open": day_open, "close": day_close,
            "avg_vix": avg_vix,
        })
        day_candles.append(candles)

    print(f"  {len(day_stats)} trading days", flush=True)
    return day_stats, day_candles


# ── Backtest Runner ───────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    label: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0
    gross_loss: float = 0
    net_pnl: float = 0
    max_dd: float = 0
    regime_counts: Dict = field(default_factory=lambda: defaultdict(int))
    regime_changes: int = 0
    monthly: Dict = field(default_factory=lambda: defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0}))


def run_backtest(day_stats, day_candles, mode="daily", lookback=5):
    """Run backtest with either 'daily' or 'midday' regime classification."""
    result = BacktestResult(label=mode)
    equity = 0
    peak_equity = 0

    for day_idx in range(lookback, len(day_stats)):
        candles = day_candles[day_idx]
        dt = day_stats[day_idx]["date"]
        month = dt[:7]

        closes = np.array([c["close"] for c in candles])
        mins = np.array([c["mins"] for c in candles])
        vix_arr = np.array([c["vix"] for c in candles])
        day_open = candles[0]["open"]

        # Classify regime at open
        regime_open = get_regime_for_day(day_idx, day_stats, lookback)
        result.regime_counts[regime_open] += 1

        if mode == "daily":
            # Single regime all day
            config = REGIME_PARAMS.get(regime_open, REGIME_PARAMS["NORMAL"])
            signals = detect_signals(closes, mins, vix_arr, day_open, config)
            regime_for_trade = regime_open
        else:
            # Split: morning uses open regime, afternoon may reclassify
            config_open = REGIME_PARAMS.get(regime_open, REGIME_PARAMS["NORMAL"])
            signals_morning = detect_signals(closes, mins, vix_arr, day_open, config_open)
            signals_morning = [s for s in signals_morning if s["mins"] < 720]

            # Reclassify at 12:00 using morning data
            regime_midday = get_regime_midday(day_idx, day_stats, candles, lookback)
            if regime_midday != regime_open:
                result.regime_changes += 1

            config_midday = REGIME_PARAMS.get(regime_midday, REGIME_PARAMS["NORMAL"])
            signals_afternoon = detect_signals(closes, mins, vix_arr, day_open, config_midday)
            signals_afternoon = [s for s in signals_afternoon if s["mins"] >= 720]

            signals = signals_morning + signals_afternoon
            regime_for_trade = regime_open  # for counting

        # Simulate trades with cooldown + max trades
        config_for_sim = REGIME_PARAMS.get(regime_for_trade, REGIME_PARAMS["NORMAL"])
        max_trades = config_for_sim["max_trades"]
        cooldown_candles = config_for_sim["cooldown"] // 3  # 3-min candles

        trades_today = 0
        last_exit_idx = -999

        # Priority: MC > MOM > VWAP
        priority = {"MC": 0, "MOM": 1, "VWAP": 2}
        signals.sort(key=lambda s: (s["idx"], priority.get(s["type"], 99)))

        # Deduplicate: one signal per candle index
        seen_idx = set()
        unique_signals = []
        for s in signals:
            if s["idx"] not in seen_idx:
                unique_signals.append(s)
                seen_idx.add(s["idx"])

        for sig in unique_signals:
            if trades_today >= max_trades:
                break
            if sig["idx"] <= last_exit_idx + cooldown_candles:
                continue

            # Get regime config for this signal's time
            if mode == "midday" and sig["mins"] >= 720:
                trade_config = config_midday
            else:
                trade_config = config_for_sim if mode == "daily" else config_open

            pnl, hold_min, reason = simulate_trade(
                closes, sig["idx"], sig["direction"],
                trade_config["sl_pts"], trade_config["tgt_pts"],
                trade_config["max_hold"],
            )

            trades_today += 1
            result.trades += 1
            exit_candles = hold_min // 3
            last_exit_idx = sig["idx"] + exit_candles

            if pnl > 0:
                result.wins += 1
                result.gross_profit += pnl
            else:
                result.losses += 1
                result.gross_loss += abs(pnl)

            equity += pnl
            result.net_pnl = equity
            peak_equity = max(peak_equity, equity)
            dd = peak_equity - equity
            result.max_dd = max(result.max_dd, dd)

            result.monthly[month]["trades"] += 1
            result.monthly[month]["pnl"] += pnl
            if pnl > 0:
                result.monthly[month]["wins"] += 1

    return result


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_result(r: BacktestResult):
    wr = r.wins / r.trades * 100 if r.trades else 0
    pf = r.gross_profit / r.gross_loss if r.gross_loss > 0 else 999
    avg_win = r.gross_profit / r.wins if r.wins else 0
    avg_loss = r.gross_loss / r.losses if r.losses else 0

    print(f"\n{'=' * 60}")
    print(f"  {r.label.upper()} REGIME CLASSIFICATION")
    print(f"{'=' * 60}")
    print(f"  Trades: {r.trades}  |  Wins: {r.wins}  |  Losses: {r.losses}")
    print(f"  WR: {wr:.1f}%  |  PF: {pf:.2f}")
    print(f"  Net P&L: Rs {r.net_pnl:+,.0f}")
    print(f"  Max DD: Rs {r.max_dd:,.0f}")
    print(f"  Avg Win: Rs {avg_win:+,.0f}  |  Avg Loss: Rs {avg_loss:,.0f}")

    if r.label == "midday":
        print(f"  Regime changes at midday: {r.regime_changes} days")

    print(f"\n  Regime distribution:")
    for regime, count in sorted(r.regime_counts.items()):
        print(f"    {regime:20s}: {count} days")

    print(f"\n  Monthly breakdown:")
    all_pass = True
    for month in sorted(r.monthly.keys()):
        m = r.monthly[month]
        mwr = m["wins"] / m["trades"] * 100 if m["trades"] else 0
        status = "PASS" if mwr > 51 and m["pnl"] > 0 else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"    {month}: {m['trades']:3d}T  WR={mwr:5.1f}%  P&L=Rs {m['pnl']:+8,.0f}  {status}")

    print(f"\n  All months pass: {'YES' if all_pass else 'NO'}")


def main():
    day_stats, day_candles = load_data()

    print("\nRunning backtests...", flush=True)

    result_daily = run_backtest(day_stats, day_candles, mode="daily")
    result_midday = run_backtest(day_stats, day_candles, mode="midday")

    print_result(result_daily)
    print_result(result_midday)

    # Comparison
    print(f"\n{'=' * 60}")
    print(f"  COMPARISON: DAILY vs MIDDAY")
    print(f"{'=' * 60}")

    d_wr = result_daily.wins / result_daily.trades * 100 if result_daily.trades else 0
    m_wr = result_midday.wins / result_midday.trades * 100 if result_midday.trades else 0
    d_pf = result_daily.gross_profit / result_daily.gross_loss if result_daily.gross_loss > 0 else 999
    m_pf = result_midday.gross_profit / result_midday.gross_loss if result_midday.gross_loss > 0 else 999

    print(f"  {'Metric':20s} {'Daily':>12s} {'Midday':>12s} {'Diff':>12s}")
    print(f"  {'-'*56}")
    print(f"  {'Trades':20s} {result_daily.trades:12d} {result_midday.trades:12d} {result_midday.trades - result_daily.trades:+12d}")
    print(f"  {'Win Rate':20s} {d_wr:11.1f}% {m_wr:11.1f}% {m_wr - d_wr:+11.1f}%")
    print(f"  {'Profit Factor':20s} {d_pf:12.2f} {m_pf:12.2f} {m_pf - d_pf:+12.2f}")
    print(f"  {'Net P&L':20s} Rs{result_daily.net_pnl:+10,.0f} Rs{result_midday.net_pnl:+10,.0f} Rs{result_midday.net_pnl - result_daily.net_pnl:+10,.0f}")
    print(f"  {'Max DD':20s} Rs{result_daily.max_dd:10,.0f} Rs{result_midday.max_dd:10,.0f} Rs{result_midday.max_dd - result_daily.max_dd:+10,.0f}")
    print(f"  {'Regime changes':20s} {'N/A':>12s} {result_midday.regime_changes:12d}")


if __name__ == "__main__":
    main()
