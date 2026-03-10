"""
Backtest Scalper Engine -- Validate premium chart patterns on historical data.

Tests mechanical signals (no Claude) to find profitable setups:
A) VWAP Breakout: Premium crosses above VWAP after being below
B) Support Bounce: Premium touches support and reverses
C) Momentum Burst: 3 consecutive higher closes above VWAP

Sweeps SL/Target/Cooldown parameters and outputs results.

Usage:
    uv run python scripts/backtest_scalper.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

from strategies.scalper_engine import ScalperEngine, NIFTY_STEP, STRIKES_OFFSET

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "oi_tracker.db")

engine = ScalperEngine()


def get_trading_dates() -> List[str]:
    """Get all dates with sufficient data."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT DATE(timestamp) as d, COUNT(DISTINCT timestamp) as snapshots
        FROM oi_snapshots
        WHERE ce_ltp > 0
        GROUP BY d
        HAVING snapshots >= 20
        ORDER BY d
    """)
    dates = [row["d"] for row in cur.fetchall()]
    conn.close()
    return dates


def get_day_data(date_str: str) -> Tuple[List[Dict], float]:
    """Get all snapshots for a day, return (snapshots_by_timestamp, first_spot)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get first spot price to determine strikes
    cur.execute("""
        SELECT spot_price FROM oi_snapshots
        WHERE DATE(timestamp) = ? AND spot_price > 0
        ORDER BY timestamp LIMIT 1
    """, (date_str,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return [], 0
    first_spot = row["spot_price"]

    # Get strikes
    strikes = engine.get_scalp_strikes(first_spot)
    ce_strike = strikes["ce_strike"]
    pe_strike = strikes["pe_strike"]

    # Get all CE data for the day
    cur.execute("""
        SELECT timestamp, spot_price, ce_ltp as ltp, ce_volume as volume,
               ce_iv as iv, ce_oi as oi
        FROM oi_snapshots
        WHERE DATE(timestamp) = ? AND strike_price = ? AND ce_ltp > 0
        ORDER BY timestamp
    """, (date_str, ce_strike))
    ce_candles = [dict(r) for r in cur.fetchall()]

    # Get all PE data
    cur.execute("""
        SELECT timestamp, spot_price, pe_ltp as ltp, pe_volume as volume,
               pe_iv as iv, pe_oi as oi
        FROM oi_snapshots
        WHERE DATE(timestamp) = ? AND strike_price = ? AND pe_ltp > 0
        ORDER BY timestamp
    """, (date_str, pe_strike))
    pe_candles = [dict(r) for r in cur.fetchall()]

    # Rename 'timestamp' to 'ts' for consistency with engine
    for c in ce_candles:
        c["ts"] = c.pop("timestamp")
    for c in pe_candles:
        c["ts"] = c.pop("timestamp")

    conn.close()
    return ce_candles, pe_candles, first_spot, ce_strike, pe_strike


def simulate_trade(candles: List[Dict], entry_idx: int, entry_price: float,
                   sl_pct: float, target_pct: float) -> Dict:
    """
    Simulate a trade from entry_idx forward.
    Returns result dict with outcome, exit price, duration, max favorable/adverse.
    """
    sl_price = entry_price * (1 - sl_pct / 100)
    target_price = entry_price * (1 + target_pct / 100)

    max_premium = entry_price
    min_premium = entry_price

    for i in range(entry_idx + 1, len(candles)):
        price = candles[i]["ltp"]
        max_premium = max(max_premium, price)
        min_premium = min(min_premium, price)

        if price <= sl_price:
            pnl = (price - entry_price) / entry_price * 100
            return {
                "outcome": "LOST",
                "exit_idx": i,
                "exit_price": price,
                "pnl_pct": pnl,
                "duration_candles": i - entry_idx,
                "max_premium": max_premium,
                "min_premium": min_premium,
            }

        if price >= target_price:
            pnl = (price - entry_price) / entry_price * 100
            return {
                "outcome": "WON",
                "exit_idx": i,
                "exit_price": price,
                "pnl_pct": pnl,
                "duration_candles": i - entry_idx,
                "max_premium": max_premium,
                "min_premium": min_premium,
            }

    # EOD — exit at last candle
    last_price = candles[-1]["ltp"]
    pnl = (last_price - entry_price) / entry_price * 100
    return {
        "outcome": "WON" if pnl > 0 else "LOST",
        "exit_idx": len(candles) - 1,
        "exit_price": last_price,
        "pnl_pct": pnl,
        "duration_candles": len(candles) - 1 - entry_idx,
        "max_premium": max_premium,
        "min_premium": min_premium,
        "eod": True,
    }


def detect_signals(candles: List[Dict], min_candle_idx: int = 5) -> List[Dict]:
    """
    Detect mechanical signals on a candle series.
    Returns list of signal dicts with idx, type, entry_price.
    """
    signals = []
    if len(candles) < min_candle_idx + 1:
        return signals

    vwap = engine.compute_vwap(candles)

    for i in range(min_candle_idx, len(candles)):
        price = candles[i]["ltp"]
        prev = candles[i-1]["ltp"]

        # Signal A: VWAP Breakout
        # Price crosses above VWAP after 2+ candles below
        if len(vwap) > i:
            curr_vwap = vwap[i]
            below_count = sum(
                1 for j in range(max(0, i-3), i)
                if j < len(vwap) and candles[j]["ltp"] < vwap[j]
            )
            if below_count >= 2 and prev < vwap[i-1] and price > curr_vwap:
                signals.append({
                    "idx": i,
                    "type": "VWAP_BREAKOUT",
                    "entry_price": price,
                })
                continue  # Only one signal per candle

        # Signal B: Support Bounce (need swing detection)
        if i >= 8:
            partial = candles[:i+1]
            swings = engine.detect_swing_points(partial, lookback=2)
            sr = engine.detect_support_resistance(partial, swings)
            for level, touches in sr.get("support", []):
                if touches >= 2:
                    dist_pct = abs(price - level) / level * 100
                    if dist_pct < 3 and price > prev and prev <= candles[i-2]["ltp"]:
                        signals.append({
                            "idx": i,
                            "type": "SUPPORT_BOUNCE",
                            "entry_price": price,
                            "support_level": level,
                        })
                        break

        # Signal C: Momentum Burst (CHC-3 above VWAP)
        if i >= 3 and len(vwap) > i:
            chc = all(
                candles[i-j]["ltp"] > candles[i-j-1]["ltp"]
                for j in range(3)
            )
            if chc and price > vwap[i]:
                signals.append({
                    "idx": i,
                    "type": "MOMENTUM_BURST",
                    "entry_price": price,
                })

    return signals


def run_backtest(sl_pct: float = 8.0, target_pct: float = 10.0,
                 cooldown_candles: int = 2, max_trades_per_day: int = 5,
                 signal_types: List[str] = None) -> Dict:
    """
    Run backtest across all historical dates.
    Returns aggregated results.
    """
    dates = get_trading_dates()
    all_trades = []

    for date_str in dates:
        result = get_day_data(date_str)
        if not result or len(result) < 5:
            continue
        ce_candles, pe_candles, first_spot, ce_strike, pe_strike = result

        for side, candles, strike in [("CE", ce_candles, ce_strike), ("PE", pe_candles, pe_strike)]:
            if len(candles) < 10:
                continue

            signals = detect_signals(candles)
            if signal_types:
                signals = [s for s in signals if s["type"] in signal_types]

            day_trades = 0
            last_exit_idx = -1

            for sig in signals:
                if day_trades >= max_trades_per_day:
                    break
                if sig["idx"] <= last_exit_idx + cooldown_candles:
                    continue

                trade = simulate_trade(candles, sig["idx"], sig["entry_price"],
                                       sl_pct, target_pct)
                trade["date"] = date_str
                trade["side"] = side
                trade["strike"] = strike
                trade["signal_type"] = sig["type"]
                trade["entry_idx"] = sig["idx"]

                all_trades.append(trade)
                day_trades += 1
                last_exit_idx = trade["exit_idx"]

    return aggregate_results(all_trades)


def aggregate_results(trades: List[Dict]) -> Dict:
    """Aggregate trade results into summary statistics."""
    if not trades:
        return {"total": 0, "message": "No trades generated"}

    total = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "WON")
    losses = total - wins
    wr = wins / total * 100 if total > 0 else 0

    total_pnl = sum(t["pnl_pct"] for t in trades)
    avg_win = sum(t["pnl_pct"] for t in trades if t["outcome"] == "WON") / wins if wins > 0 else 0
    avg_loss = sum(t["pnl_pct"] for t in trades if t["outcome"] == "LOST") / losses if losses > 0 else 0
    avg_duration = sum(t["duration_candles"] for t in trades) / total

    # Max drawdown (cumulative P&L)
    cum_pnl = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cum_pnl += t["pnl_pct"]
        peak = max(peak, cum_pnl)
        dd = peak - cum_pnl
        max_dd = max(max_dd, dd)

    # By signal type
    by_type = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0})
    for t in trades:
        st = t["signal_type"]
        by_type[st]["total"] += 1
        if t["outcome"] == "WON":
            by_type[st]["wins"] += 1
        by_type[st]["pnl"] += t["pnl_pct"]

    # By side
    by_side = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0})
    for t in trades:
        s = t["side"]
        by_side[s]["total"] += 1
        if t["outcome"] == "WON":
            by_side[s]["wins"] += 1
        by_side[s]["pnl"] += t["pnl_pct"]

    # Profit factor
    gross_profit = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "total_pnl": round(total_pnl, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_duration_candles": round(avg_duration, 1),
        "max_drawdown": round(max_dd, 1),
        "profit_factor": round(pf, 2),
        "by_signal_type": dict(by_type),
        "by_side": dict(by_side),
        "dates_tested": len(set(t["date"] for t in trades)),
        "trades": trades,  # For detailed analysis
    }


def print_results(results: Dict, label: str = ""):
    """Print backtest results in a readable format."""
    if results.get("total", 0) == 0:
        print(f"\n{label}: No trades generated")
        return

    print(f"\n{'='*60}")
    print(f" {label}" if label else " BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f" Total Trades:  {results['total']}")
    print(f" Wins:          {results['wins']} | Losses: {results['losses']}")
    print(f" Win Rate:      {results['win_rate']}%")
    print(f" Total P&L:     {results['total_pnl']:+.1f}%")
    print(f" Avg Win:       {results['avg_win']:+.2f}%")
    print(f" Avg Loss:      {results['avg_loss']:+.2f}%")
    print(f" Profit Factor: {results['profit_factor']}")
    print(f" Max Drawdown:  {results['max_drawdown']:.1f}%")
    print(f" Avg Duration:  {results['avg_duration_candles']:.1f} candles ({results['avg_duration_candles']*3:.0f} min)")
    print(f" Dates Tested:  {results['dates_tested']}")

    print(f"\n By Signal Type:")
    for sig_type, stats in results.get("by_signal_type", {}).items():
        wr = stats["wins"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"   {sig_type:20s}: {stats['total']:3d} trades | WR: {wr:.0f}% | P&L: {stats['pnl']:+.1f}%")

    print(f"\n By Side:")
    for side, stats in results.get("by_side", {}).items():
        wr = stats["wins"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"   {side}: {stats['total']:3d} trades | WR: {wr:.0f}% | P&L: {stats['pnl']:+.1f}%")


def main():
    print("Scalper Engine Backtest")
    print(f"Database: {DB_PATH}")

    dates = get_trading_dates()
    print(f"Trading dates available: {len(dates)}")
    if dates:
        print(f"  From: {dates[0]} to {dates[-1]}")

    # Main sweep: test different SL/Target combinations
    configs = [
        {"sl_pct": 5, "target_pct": 8, "label": "Conservative (5/8)"},
        {"sl_pct": 8, "target_pct": 10, "label": "Balanced (8/10)"},
        {"sl_pct": 8, "target_pct": 12, "label": "Asymmetric (8/12)"},
        {"sl_pct": 10, "target_pct": 10, "label": "Wide SL 1:1 (10/10)"},
        {"sl_pct": 10, "target_pct": 15, "label": "Wide 1:1.5 (10/15)"},
        {"sl_pct": 12, "target_pct": 15, "label": "Loose (12/15)"},
    ]

    best_pnl = float("-inf")
    best_config = None

    for cfg in configs:
        results = run_backtest(
            sl_pct=cfg["sl_pct"],
            target_pct=cfg["target_pct"],
            cooldown_candles=2,
            max_trades_per_day=5,
        )
        print_results(results, label=cfg["label"])

        if results.get("total_pnl", 0) > best_pnl:
            best_pnl = results["total_pnl"]
            best_config = cfg

    if best_config:
        print(f"\n{'='*60}")
        print(f" BEST CONFIG: {best_config['label']}")
        print(f" SL: {best_config['sl_pct']}% | Target: {best_config['target_pct']}%")
        print(f" Total P&L: {best_pnl:+.1f}%")
        print(f"{'='*60}")

    # Test individual signal types with best config
    if best_config:
        print(f"\n\n--- Signal Type Breakdown (using best config) ---")
        for sig_type in ["VWAP_BREAKOUT", "SUPPORT_BOUNCE", "MOMENTUM_BURST"]:
            results = run_backtest(
                sl_pct=best_config["sl_pct"],
                target_pct=best_config["target_pct"],
                cooldown_candles=2,
                max_trades_per_day=5,
                signal_types=[sig_type],
            )
            print_results(results, label=f"{sig_type} only")


if __name__ == "__main__":
    main()
