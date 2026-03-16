"""
Rally Prediction Analysis v2
=============================
Recalibrated: A "rally" is defined as a LARGE directional move relative to
the day's average volatility. We focus on predicting:
1. DIRECTION of the biggest move of the day
2. BIG move days (top 25% daily range) vs normal days
3. Intraday timing: can we predict when the next 50+ pt move starts?

Key insight from v1: 30 pts in 45 min happens on EVERY day (100% baseline).
Need higher thresholds and directional prediction to find real edges.
"""

import sqlite3
import json
from datetime import datetime, timedelta, date
from collections import defaultdict
import statistics
import math

DB_PATH = "oi_tracker.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# Data Loading
# ============================================================

def load_nifty_candles():
    conn = get_conn()
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM nifty_history ORDER BY timestamp"
    ).fetchall()
    conn.close()
    daily = defaultdict(list)
    for r in rows:
        dt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        daily[dt.date()].append({
            "ts": dt, "open": r["open"], "high": r["high"],
            "low": r["low"], "close": r["close"],
        })
    return daily


def load_vix_candles():
    conn = get_conn()
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close FROM vix_history ORDER BY timestamp"
    ).fetchall()
    conn.close()
    daily = defaultdict(list)
    for r in rows:
        dt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        daily[dt.date()].append({
            "ts": dt, "open": r["open"], "high": r["high"],
            "low": r["low"], "close": r["close"],
        })
    return daily


def load_orderflow_data():
    conn = get_conn()
    rows = conn.execute(
        "SELECT timestamp, instrument_token, strike, option_type, "
        "total_bid_qty, total_ask_qty, bid_ask_imbalance, "
        "best_bid_price, best_bid_qty, best_ask_price, best_ask_qty "
        "FROM orderflow_depth ORDER BY timestamp"
    ).fetchall()
    conn.close()
    daily = defaultdict(list)
    for r in rows:
        dt = datetime.fromisoformat(r["timestamp"])
        daily[dt.date()].append({
            "ts": dt, "strike": r["strike"], "option_type": r["option_type"],
            "total_bid_qty": r["total_bid_qty"], "total_ask_qty": r["total_ask_qty"],
            "bid_ask_imbalance": r["bid_ask_imbalance"],
            "best_bid_price": r["best_bid_price"], "best_bid_qty": r["best_bid_qty"],
            "best_ask_price": r["best_ask_price"], "best_ask_qty": r["best_ask_qty"],
        })
    return daily


# ============================================================
# Swing Detection (peaks and troughs in price)
# ============================================================

def find_swings(candles, min_swing=40):
    """
    Find swing highs and lows. A swing is confirmed when price reverses
    by min_swing pts from a local extreme.
    Returns list of {idx, ts, price, type: 'high'|'low'}.
    """
    if len(candles) < 3:
        return []

    swings = []
    # Track running high and low since last swing
    run_high = candles[0]["high"]
    run_high_idx = 0
    run_low = candles[0]["low"]
    run_low_idx = 0
    last_swing_type = None

    for i in range(1, len(candles)):
        c = candles[i]

        if c["high"] > run_high:
            run_high = c["high"]
            run_high_idx = i
        if c["low"] < run_low:
            run_low = c["low"]
            run_low_idx = i

        # Check for swing high confirmation (price dropped min_swing from high)
        if run_high - c["low"] >= min_swing and last_swing_type != "high":
            swings.append({
                "idx": run_high_idx,
                "ts": candles[run_high_idx]["ts"],
                "price": run_high,
                "type": "high",
            })
            last_swing_type = "high"
            run_low = c["low"]
            run_low_idx = i

        # Check for swing low confirmation
        if c["high"] - run_low >= min_swing and last_swing_type != "low":
            swings.append({
                "idx": run_low_idx,
                "ts": candles[run_low_idx]["ts"],
                "price": run_low,
                "type": "low",
            })
            last_swing_type = "low"
            run_high = c["high"]
            run_high_idx = i

    return swings


def find_big_moves(candles, min_move=50):
    """
    Find all directional moves of min_move+ pts by looking at swing-to-swing.
    Returns moves with start/end info.
    """
    swings = find_swings(candles, min_swing=min_move)
    moves = []
    for i in range(1, len(swings)):
        prev = swings[i - 1]
        curr = swings[i]
        magnitude = abs(curr["price"] - prev["price"])
        if magnitude >= min_move:
            direction = "UP" if curr["price"] > prev["price"] else "DOWN"
            duration = (curr["ts"] - prev["ts"]).total_seconds() / 60
            moves.append({
                "start_idx": prev["idx"],
                "end_idx": curr["idx"],
                "start_ts": prev["ts"],
                "end_ts": curr["ts"],
                "start_price": prev["price"],
                "end_price": curr["price"],
                "direction": direction,
                "magnitude": magnitude,
                "duration_min": duration,
            })
    return moves


# ============================================================
# Daily Stats
# ============================================================

def get_daily_stats(nifty_daily, move_threshold=50):
    """Compute daily OHLC and moves from 3-min candles."""
    stats = {}
    for d, candles in sorted(nifty_daily.items()):
        if len(candles) < 10:
            continue

        day_high = max(c["high"] for c in candles)
        day_low = min(c["low"] for c in candles)
        day_range = day_high - day_low

        moves = find_big_moves(candles, min_move=move_threshold)
        max_up = max((m["magnitude"] for m in moves if m["direction"] == "UP"), default=0)
        max_down = max((m["magnitude"] for m in moves if m["direction"] == "DOWN"), default=0)

        # Net direction: close vs open
        net_move = candles[-1]["close"] - candles[0]["open"]

        stats[d] = {
            "date": d,
            "open": candles[0]["open"],
            "high": day_high,
            "low": day_low,
            "close": candles[-1]["close"],
            "range": day_range,
            "net_move": net_move,
            "net_direction": "UP" if net_move > 0 else "DOWN",
            "moves": moves,
            "move_count": len(moves),
            "max_up": max_up,
            "max_down": max_down,
            "biggest_move": max(max_up, max_down),
            "candles": candles,
        }
    return stats


# ============================================================
# Section 1: Orderflow Analysis
# ============================================================

def analyze_orderflow(nifty_daily, of_daily):
    print("\n" + "=" * 80)
    print("SECTION 1: ORDERFLOW DEPTH — PREDICTIVE SIGNAL ANALYSIS")
    print("=" * 80)

    of_dates = sorted(of_daily.keys())
    print(f"\n  Data: {len(of_dates)} days, ~10-second intervals, 1-7 strikes per day")
    print(f"  Dates: {of_dates[0]} to {of_dates[-1]}")
    print(f"  Total ticks: {sum(len(v) for v in of_daily.values())}")

    # For each orderflow day, compute rolling imbalance metrics
    # and correlate with subsequent NIFTY moves
    signal_results = []

    for d in of_dates:
        if d not in nifty_daily:
            continue

        candles = nifty_daily[d]
        of_data = of_daily[d]
        if len(candles) < 10 or len(of_data) < 20:
            continue

        moves = find_big_moves(candles, min_move=50)
        if not moves:
            moves = find_big_moves(candles, min_move=40)

        print(f"\n  --- {d} ---")
        print(f"  Orderflow: {len(of_data)} ticks, "
              f"{of_data[0]['ts'].strftime('%H:%M')}-{of_data[-1]['ts'].strftime('%H:%M')}")
        print(f"  Big moves (50+ pts): {len(moves)}")

        # Build 1-minute aggregated orderflow
        of_by_minute = defaultdict(list)
        for tick in of_data:
            minute_key = tick["ts"].replace(second=0, microsecond=0)
            of_by_minute[minute_key].append(tick)

        # Compute rolling bid-ask imbalance (3-min windows)
        sorted_minutes = sorted(of_by_minute.keys())
        rolling_imbalance = []  # (timestamp, imbalance_ratio, bid_momentum)

        for i, minute in enumerate(sorted_minutes):
            # Aggregate this minute
            ticks = of_by_minute[minute]
            avg_imbalance = statistics.mean(t["bid_ask_imbalance"] for t in ticks)
            total_bid = sum(t["total_bid_qty"] for t in ticks)
            total_ask = sum(t["total_ask_qty"] for t in ticks)
            ratio = total_bid / max(total_ask, 1)

            # Compute momentum (change from 3 min ago)
            bid_momentum = 0
            if i >= 3:
                prev_ticks = of_by_minute[sorted_minutes[i - 3]]
                prev_bid = sum(t["total_bid_qty"] for t in prev_ticks)
                prev_ask = sum(t["total_ask_qty"] for t in prev_ticks)
                prev_ratio = prev_bid / max(prev_ask, 1)
                bid_momentum = ratio - prev_ratio

            rolling_imbalance.append({
                "ts": minute,
                "imbalance": avg_imbalance,
                "bid_ask_ratio": ratio,
                "bid_momentum": bid_momentum,
                "total_bid": total_bid,
                "total_ask": total_ask,
            })

        for move in moves:
            # Find orderflow state BEFORE the move
            pre_move_of = [
                ri for ri in rolling_imbalance
                if move["start_ts"] - timedelta(minutes=15) <= ri["ts"] < move["start_ts"]
            ]
            if len(pre_move_of) < 3:
                continue

            # Metrics
            avg_ratio = statistics.mean(ri["bid_ask_ratio"] for ri in pre_move_of)
            ratio_trend = pre_move_of[-1]["bid_ask_ratio"] - pre_move_of[0]["bid_ask_ratio"]
            avg_momentum = statistics.mean(ri["bid_momentum"] for ri in pre_move_of)

            # What strikes were tracked?
            pre_of_raw = [t for t in of_data
                          if move["start_ts"] - timedelta(minutes=15) <= t["ts"] < move["start_ts"]]
            strikes_tracked = set((t["strike"], t["option_type"]) for t in pre_of_raw)
            strike_str = ", ".join(f"{s}{t}" for s, t in sorted(strikes_tracked))

            # Determine signal
            # Key insight: for options orderflow interpretation:
            # - PE options: high bid/ask ratio = buying PEs = bearish bet
            # - CE options: high bid/ask ratio = buying CEs = bullish bet
            # We need to weight by option type

            pe_ticks = [t for t in pre_of_raw if t["option_type"] == "PE"]
            ce_ticks = [t for t in pre_of_raw if t["option_type"] == "CE"]

            pe_signal = 0  # positive = bearish
            ce_signal = 0  # positive = bullish

            if pe_ticks:
                pe_imb = statistics.mean(t["bid_ask_imbalance"] for t in pe_ticks)
                pe_bid_total = sum(t["total_bid_qty"] for t in pe_ticks)
                pe_ask_total = sum(t["total_ask_qty"] for t in pe_ticks)
                pe_ratio = pe_bid_total / max(pe_ask_total, 1)
                # PE bid > ask means bearish sentiment
                pe_signal = (pe_ratio - 1.0) * 100  # positive = bearish

            if ce_ticks:
                ce_imb = statistics.mean(t["bid_ask_imbalance"] for t in ce_ticks)
                ce_bid_total = sum(t["total_bid_qty"] for t in ce_ticks)
                ce_ask_total = sum(t["total_ask_qty"] for t in ce_ticks)
                ce_ratio = ce_bid_total / max(ce_ask_total, 1)
                # CE bid > ask means bullish sentiment
                ce_signal = (ce_ratio - 1.0) * 100  # positive = bullish

            # Combined: net signal (positive = bullish, negative = bearish)
            net_signal = ce_signal - pe_signal

            predicted_dir = "UP" if net_signal > 0 else "DOWN"
            correct = predicted_dir == move["direction"]

            print(f"    Move: {move['direction']} {move['magnitude']:.0f}pts "
                  f"{move['start_ts'].strftime('%H:%M')}-{move['end_ts'].strftime('%H:%M')} | "
                  f"Strikes: {strike_str}")
            print(f"      Pre-move OF (15 min): CE_signal={ce_signal:+.1f}, "
                  f"PE_signal={pe_signal:+.1f}, Net={net_signal:+.1f} "
                  f"-> Predicted {predicted_dir} | {'CORRECT' if correct else 'WRONG'}")

            signal_results.append({
                "date": d,
                "move_dir": move["direction"],
                "magnitude": move["magnitude"],
                "net_signal": net_signal,
                "predicted": predicted_dir,
                "correct": correct,
                "ce_signal": ce_signal,
                "pe_signal": pe_signal,
            })

    # Summary
    print(f"\n  {'='*60}")
    print(f"  ORDERFLOW SIGNAL SUMMARY")
    print(f"  {'='*60}")
    if signal_results:
        total = len(signal_results)
        correct = sum(1 for r in signal_results if r["correct"])
        print(f"  Total predictions: {total}")
        print(f"  Correct: {correct}/{total} ({100*correct/total:.1f}%)")
        print(f"  (50% = random, need >55% to be useful)")

        # By signal strength
        strong = [r for r in signal_results if abs(r["net_signal"]) > 5]
        if strong:
            strong_correct = sum(1 for r in strong if r["correct"])
            print(f"\n  Strong signals (|net| > 5): {len(strong)}")
            print(f"    Correct: {strong_correct}/{len(strong)} ({100*strong_correct/len(strong):.1f}%)")

        weak = [r for r in signal_results if abs(r["net_signal"]) <= 5]
        if weak:
            weak_correct = sum(1 for r in weak if r["correct"])
            print(f"  Weak signals (|net| <= 5): {len(weak)}")
            print(f"    Correct: {weak_correct}/{len(weak)} ({100*weak_correct/len(weak):.1f}%)")

        # By move size
        big = [r for r in signal_results if r["magnitude"] >= 70]
        if big:
            big_correct = sum(1 for r in big if r["correct"])
            print(f"\n  For big moves (70+ pts): {big_correct}/{len(big)} ({100*big_correct/len(big):.1f}%)")
    else:
        print("  No predictions generated (insufficient overlap of OF data and moves)")

    return signal_results


# ============================================================
# Section 2: Pattern Analysis with correct baseline
# ============================================================

def analyze_patterns(nifty_daily, vix_daily):
    print("\n" + "=" * 80)
    print("SECTION 2: PREDICTIVE PATTERN ANALYSIS (300 days)")
    print("=" * 80)

    day_stats = get_daily_stats(nifty_daily, move_threshold=50)
    sorted_days = sorted(day_stats.keys())

    # Use "big move day" = top 25% of daily range as the target
    ranges = [day_stats[d]["range"] for d in sorted_days]
    range_p75 = sorted(ranges)[3 * len(ranges) // 4]
    range_p50 = sorted(ranges)[len(ranges) // 2]

    big_move_days = set(d for d in sorted_days if day_stats[d]["range"] >= range_p75)
    baseline_big = len(big_move_days) / len(sorted_days) * 100

    print(f"\n  Daily range: min={min(ranges):.0f}, p25={sorted(ranges)[len(ranges)//4]:.0f}, "
          f"median={range_p50:.0f}, p75={range_p75:.0f}, max={max(ranges):.0f}")
    print(f"  'Big move day' threshold (p75): {range_p75:.0f} pts")
    print(f"  Big move days: {len(big_move_days)}/{len(sorted_days)} ({baseline_big:.1f}%)")

    # Also track directional prediction accuracy
    # Net move direction = open to close direction
    up_days = sum(1 for d in sorted_days if day_stats[d]["net_direction"] == "UP")
    down_days = len(sorted_days) - up_days
    print(f"  UP days: {up_days}, DOWN days: {down_days}")

    pattern_results = []

    # ---- Pattern A: Narrow Range Day -> Big Move Next Day ----
    print(f"\n  {'-'*60}")
    print(f"  PATTERN A: Narrow Range Yesterday -> Big Move Today")
    print(f"  {'-'*60}")

    p20 = sorted(ranges)[len(ranges) // 5]
    narrow_results = {"big": 0, "total": 0, "ranges": [], "directions": []}

    for i in range(len(sorted_days) - 1):
        d = sorted_days[i]
        d_next = sorted_days[i + 1]
        if day_stats[d]["range"] <= p20:
            narrow_results["total"] += 1
            narrow_results["ranges"].append(day_stats[d_next]["range"])
            narrow_results["directions"].append(day_stats[d_next]["net_direction"])
            if d_next in big_move_days:
                narrow_results["big"] += 1

    if narrow_results["total"] > 0:
        pct = narrow_results["big"] / narrow_results["total"] * 100
        avg_range = statistics.mean(narrow_results["ranges"])
        avg_overall = statistics.mean(ranges)
        up_pct = sum(1 for d in narrow_results["directions"] if d == "UP") / narrow_results["total"] * 100
        print(f"    Narrow days (range <= {p20:.0f}): {narrow_results['total']}")
        print(f"    Big move next day: {narrow_results['big']}/{narrow_results['total']} ({pct:.1f}%) vs baseline {baseline_big:.1f}%")
        print(f"    EDGE: {pct - baseline_big:+.1f}%")
        print(f"    Avg next-day range: {avg_range:.0f} pts (overall {avg_overall:.0f})")
        print(f"    Next-day UP bias: {up_pct:.1f}%")
        pattern_results.append({
            "name": "A: Narrow Range -> Big Move",
            "frequency": narrow_results["total"],
            "freq_pct": narrow_results["total"] / len(sorted_days) * 100,
            "accuracy": pct,
            "baseline": baseline_big,
            "edge": pct - baseline_big,
            "avg_range_boost": avg_range - avg_overall,
        })

    # ---- Pattern B: VIX Compression ----
    print(f"\n  {'-'*60}")
    print(f"  PATTERN B: VIX Compression (3+ declining days) -> Big Move")
    print(f"  {'-'*60}")

    vix_daily_close = {}
    for d, candles in sorted(vix_daily.items()):
        if len(candles) >= 5:
            vix_daily_close[d] = candles[-1]["close"]

    vix_streak = 0
    compression_events = []
    vix_sorted = sorted(vix_daily_close.keys())
    for i in range(1, len(vix_sorted)):
        if vix_daily_close[vix_sorted[i]] < vix_daily_close[vix_sorted[i-1]]:
            vix_streak += 1
        else:
            if vix_streak >= 3:
                # Next trading day
                if i < len(vix_sorted):
                    compression_events.append({
                        "end_date": vix_sorted[i-1],
                        "next_date": vix_sorted[i],
                        "streak": vix_streak,
                        "vix_drop": vix_daily_close[vix_sorted[i-1]] - vix_daily_close[vix_sorted[i-vix_streak]],
                    })
            vix_streak = 0

    print(f"    VIX 3+ day decline events: {len(compression_events)}")
    if compression_events:
        big_after = sum(1 for e in compression_events if e["next_date"] in big_move_days)
        pct = big_after / len(compression_events) * 100
        print(f"    Big move after compression: {big_after}/{len(compression_events)} ({pct:.1f}%) vs baseline {baseline_big:.1f}%")
        print(f"    EDGE: {pct - baseline_big:+.1f}%")

        # Also check range
        comp_ranges = [day_stats[e["next_date"]]["range"] for e in compression_events if e["next_date"] in day_stats]
        if comp_ranges:
            print(f"    Avg range after compression: {statistics.mean(comp_ranges):.0f} pts")

        pattern_results.append({
            "name": "B: VIX Compression -> Big Move",
            "frequency": len(compression_events),
            "freq_pct": len(compression_events) / len(sorted_days) * 100,
            "accuracy": pct,
            "baseline": baseline_big,
            "edge": pct - baseline_big,
        })

    # ---- Pattern C: Gap Analysis (Direction Prediction) ----
    print(f"\n  {'-'*60}")
    print(f"  PATTERN C: Gap Open -> Direction Prediction")
    print(f"  {'-'*60}")

    gap_stats = {"up_gap": [], "down_gap": [], "no_gap": []}
    for i in range(1, len(sorted_days)):
        d_prev = sorted_days[i - 1]
        d = sorted_days[i]
        gap = day_stats[d]["open"] - day_stats[d_prev]["close"]
        entry = {"date": d, "gap": gap, "net_dir": day_stats[d]["net_direction"],
                 "range": day_stats[d]["range"]}

        if gap > 30:
            gap_stats["up_gap"].append(entry)
        elif gap < -30:
            gap_stats["down_gap"].append(entry)
        else:
            gap_stats["no_gap"].append(entry)

    print(f"    Gap-up (>30): {len(gap_stats['up_gap'])} days")
    if gap_stats["up_gap"]:
        # Gap-up: does price continue up or reverse?
        continues = sum(1 for e in gap_stats["up_gap"] if e["net_dir"] == "UP")
        reverses = len(gap_stats["up_gap"]) - continues
        print(f"      Continues UP: {continues}/{len(gap_stats['up_gap'])} ({100*continues/len(gap_stats['up_gap']):.1f}%)")
        print(f"      Reverses DOWN: {reverses}/{len(gap_stats['up_gap'])} ({100*reverses/len(gap_stats['up_gap']):.1f}%)")
        big_pct = sum(1 for e in gap_stats["up_gap"] if e["date"] in big_move_days) / len(gap_stats["up_gap"]) * 100
        print(f"      Big move day rate: {big_pct:.1f}% (baseline {baseline_big:.1f}%)")
        # Gap fill rate
        filled = 0
        for e in gap_stats["up_gap"]:
            if e["date"] in day_stats:
                if day_stats[e["date"]]["low"] <= day_stats[sorted_days[sorted_days.index(e["date"])-1]]["close"]:
                    filled += 1
        print(f"      Gap filled: {filled}/{len(gap_stats['up_gap'])} ({100*filled/len(gap_stats['up_gap']):.1f}%)")

    print(f"\n    Gap-down (<-30): {len(gap_stats['down_gap'])} days")
    if gap_stats["down_gap"]:
        continues = sum(1 for e in gap_stats["down_gap"] if e["net_dir"] == "DOWN")
        reverses = len(gap_stats["down_gap"]) - continues
        print(f"      Continues DOWN: {continues}/{len(gap_stats['down_gap'])} ({100*continues/len(gap_stats['down_gap']):.1f}%)")
        print(f"      Reverses UP: {reverses}/{len(gap_stats['down_gap'])} ({100*reverses/len(gap_stats['down_gap']):.1f}%)")
        big_pct = sum(1 for e in gap_stats["down_gap"] if e["date"] in big_move_days) / len(gap_stats["down_gap"]) * 100
        print(f"      Big move day rate: {big_pct:.1f}% (baseline {baseline_big:.1f}%)")
        filled = 0
        for e in gap_stats["down_gap"]:
            if e["date"] in day_stats:
                prev_idx = sorted_days.index(e["date"]) - 1
                if day_stats[e["date"]]["high"] >= day_stats[sorted_days[prev_idx]]["close"]:
                    filled += 1
        print(f"      Gap filled: {filled}/{len(gap_stats['down_gap'])} ({100*filled/len(gap_stats['down_gap']):.1f}%)")

    # For pattern scoring, use gap-down reversal (buy signal)
    if gap_stats["down_gap"]:
        reversal_pct = sum(1 for e in gap_stats["down_gap"] if e["net_dir"] == "UP") / len(gap_stats["down_gap"]) * 100
        pattern_results.append({
            "name": "C: Gap-Down -> Reversal UP (buy)",
            "frequency": len(gap_stats["down_gap"]),
            "freq_pct": len(gap_stats["down_gap"]) / len(sorted_days) * 100,
            "accuracy": reversal_pct,
            "baseline": 50.0,
            "edge": reversal_pct - 50.0,
        })
    if gap_stats["up_gap"]:
        cont_pct = sum(1 for e in gap_stats["up_gap"] if e["net_dir"] == "UP") / len(gap_stats["up_gap"]) * 100
        pattern_results.append({
            "name": "C: Gap-Up -> Continuation UP",
            "frequency": len(gap_stats["up_gap"]),
            "freq_pct": len(gap_stats["up_gap"]) / len(sorted_days) * 100,
            "accuracy": cont_pct,
            "baseline": 50.0,
            "edge": cont_pct - 50.0,
        })

    # ---- Pattern D: Inside Day + Close Position ----
    print(f"\n  {'-'*60}")
    print(f"  PATTERN D: Inside Day / Close Position -> Next Day")
    print(f"  {'-'*60}")

    inside_day_results = {"big": 0, "total": 0, "ranges": [], "net_dirs": []}
    close_high_results = {"total": 0, "up_next": 0, "ranges": []}
    close_low_results = {"total": 0, "down_next": 0, "ranges": []}

    for i in range(1, len(sorted_days) - 1):
        d_prev = sorted_days[i - 1]
        d = sorted_days[i]
        d_next = sorted_days[i + 1]

        prev = day_stats[d_prev]
        curr = day_stats[d]

        # Inside day
        if curr["high"] <= prev["high"] and curr["low"] >= prev["low"]:
            inside_day_results["total"] += 1
            inside_day_results["ranges"].append(day_stats[d_next]["range"])
            inside_day_results["net_dirs"].append(day_stats[d_next]["net_direction"])
            if d_next in big_move_days:
                inside_day_results["big"] += 1

        # Close position
        rng = curr["range"]
        if rng > 0:
            close_pct = (curr["close"] - curr["low"]) / rng
            if close_pct > 0.8:
                close_high_results["total"] += 1
                close_high_results["ranges"].append(day_stats[d_next]["range"])
                if day_stats[d_next]["net_direction"] == "UP":
                    close_high_results["up_next"] += 1
            elif close_pct < 0.2:
                close_low_results["total"] += 1
                close_low_results["ranges"].append(day_stats[d_next]["range"])
                if day_stats[d_next]["net_direction"] == "DOWN":
                    close_low_results["down_next"] += 1

    if inside_day_results["total"] > 0:
        n = inside_day_results["total"]
        big_pct = inside_day_results["big"] / n * 100
        avg_range = statistics.mean(inside_day_results["ranges"])
        avg_overall = statistics.mean(ranges)
        print(f"    Inside days: {n}")
        print(f"    Big move next day: {inside_day_results['big']}/{n} ({big_pct:.1f}%) vs baseline {baseline_big:.1f}%")
        print(f"    EDGE for big move: {big_pct - baseline_big:+.1f}%")
        print(f"    Avg next-day range: {avg_range:.0f} pts (overall {avg_overall:.0f})")
        up_next = sum(1 for d in inside_day_results["net_dirs"] if d == "UP")
        print(f"    Next-day direction: UP={up_next}, DOWN={n - up_next}")
        pattern_results.append({
            "name": "D: Inside Day -> Big Move",
            "frequency": n,
            "freq_pct": n / len(sorted_days) * 100,
            "accuracy": big_pct,
            "baseline": baseline_big,
            "edge": big_pct - baseline_big,
            "avg_range_boost": avg_range - avg_overall,
        })

    if close_high_results["total"] > 0:
        n = close_high_results["total"]
        cont_pct = close_high_results["up_next"] / n * 100
        print(f"\n    Close near HIGH: {n} days")
        print(f"    Next day UP (continuation): {close_high_results['up_next']}/{n} ({cont_pct:.1f}%) vs 50%")
        print(f"    EDGE: {cont_pct - 50:+.1f}%")
        pattern_results.append({
            "name": "D: Close Near High -> UP Next",
            "frequency": n,
            "freq_pct": n / len(sorted_days) * 100,
            "accuracy": cont_pct,
            "baseline": 50.0,
            "edge": cont_pct - 50.0,
        })

    if close_low_results["total"] > 0:
        n = close_low_results["total"]
        cont_pct = close_low_results["down_next"] / n * 100
        print(f"\n    Close near LOW: {n} days")
        print(f"    Next day DOWN (continuation): {close_low_results['down_next']}/{n} ({cont_pct:.1f}%) vs 50%")
        print(f"    EDGE: {cont_pct - 50:+.1f}%")
        pattern_results.append({
            "name": "D: Close Near Low -> DOWN Next",
            "frequency": n,
            "freq_pct": n / len(sorted_days) * 100,
            "accuracy": cont_pct,
            "baseline": 50.0,
            "edge": cont_pct - 50.0,
        })

    # ---- Pattern E: Weekly Level Proximity ----
    print(f"\n  {'-'*60}")
    print(f"  PATTERN E: Near Weekly High/Low -> Direction")
    print(f"  {'-'*60}")

    # Build weekly highs/lows
    weekly = defaultdict(lambda: {"high": 0, "low": float('inf'), "days": []})
    for d in sorted_days:
        wk = d.isocalendar()[:2]
        weekly[wk]["high"] = max(weekly[wk]["high"], day_stats[d]["high"])
        weekly[wk]["low"] = min(weekly[wk]["low"], day_stats[d]["low"])
        weekly[wk]["days"].append(d)

    near_high_results = {"total": 0, "up": 0, "big": 0}
    near_low_results = {"total": 0, "down": 0, "big": 0}

    wk_list = sorted(weekly.keys())
    for i in range(1, len(wk_list)):
        prev_wk = wk_list[i - 1]
        curr_wk = wk_list[i]
        prev_high = weekly[prev_wk]["high"]
        prev_low = weekly[prev_wk]["low"]

        for d in weekly[curr_wk]["days"]:
            if abs(day_stats[d]["open"] - prev_high) <= 50:
                near_high_results["total"] += 1
                if day_stats[d]["net_direction"] == "UP":
                    near_high_results["up"] += 1
                if d in big_move_days:
                    near_high_results["big"] += 1

            if abs(day_stats[d]["open"] - prev_low) <= 50:
                near_low_results["total"] += 1
                if day_stats[d]["net_direction"] == "DOWN":
                    near_low_results["down"] += 1
                if d in big_move_days:
                    near_low_results["big"] += 1

    if near_high_results["total"] > 0:
        n = near_high_results["total"]
        up_pct = near_high_results["up"] / n * 100
        big_pct = near_high_results["big"] / n * 100
        print(f"    Near prev week HIGH: {n} days")
        print(f"    Breakout UP: {near_high_results['up']}/{n} ({up_pct:.1f}%)")
        print(f"    Big move day: {near_high_results['big']}/{n} ({big_pct:.1f}%) vs baseline {baseline_big:.1f}%")
        pattern_results.append({
            "name": "E: Near Weekly High -> UP Breakout",
            "frequency": n,
            "freq_pct": n / len(sorted_days) * 100,
            "accuracy": up_pct,
            "baseline": 50.0,
            "edge": up_pct - 50.0,
        })

    if near_low_results["total"] > 0:
        n = near_low_results["total"]
        down_pct = near_low_results["down"] / n * 100
        big_pct = near_low_results["big"] / n * 100
        print(f"\n    Near prev week LOW: {n} days")
        print(f"    Breakdown DOWN: {near_low_results['down']}/{n} ({down_pct:.1f}%)")
        print(f"    Big move day: {near_low_results['big']}/{n} ({big_pct:.1f}%) vs baseline {baseline_big:.1f}%")

    # ---- Pattern F: Opening Range ----
    print(f"\n  {'-'*60}")
    print(f"  PATTERN F: First 15-min Pattern -> Rest of Day")
    print(f"  {'-'*60}")

    or_stats = []
    for d in sorted_days:
        candles = nifty_daily[d]
        if len(candles) < 15:
            continue

        first_5 = candles[:5]  # 15 min
        rest = candles[5:]

        or_high = max(c["high"] for c in first_5)
        or_low = min(c["low"] for c in first_5)
        or_range = or_high - or_low
        or_dir = "UP" if first_5[-1]["close"] > first_5[0]["open"] else "DOWN"
        or_move = first_5[-1]["close"] - first_5[0]["open"]

        rest_high = max(c["high"] for c in rest)
        rest_low = min(c["low"] for c in rest)
        rest_up = rest_high - or_high  # How much it went above OR
        rest_down = or_low - rest_low  # How much it went below OR

        # Biggest rest-of-day move
        rest_moves = find_big_moves(rest, min_move=50)
        rest_max_up = max((m["magnitude"] for m in rest_moves if m["direction"] == "UP"), default=0)
        rest_max_down = max((m["magnitude"] for m in rest_moves if m["direction"] == "DOWN"), default=0)

        # Net rest-of-day direction
        rest_net = rest[-1]["close"] - rest[0]["open"]
        rest_net_dir = "UP" if rest_net > 0 else "DOWN"

        or_stats.append({
            "date": d,
            "or_range": or_range,
            "or_dir": or_dir,
            "or_move": or_move,
            "rest_net_dir": rest_net_dir,
            "rest_max_up": rest_max_up,
            "rest_max_down": rest_max_down,
            "rest_up_breakout": rest_up,
            "rest_down_breakout": rest_down,
            "is_big_day": d in big_move_days,
        })

    or_ranges = [s["or_range"] for s in or_stats]
    or_p25 = sorted(or_ranges)[len(or_ranges) // 4]
    or_p75 = sorted(or_ranges)[3 * len(or_ranges) // 4]

    narrow_or = [s for s in or_stats if s["or_range"] <= or_p25]
    wide_or = [s for s in or_stats if s["or_range"] >= or_p75]

    print(f"    OR range: min={min(or_ranges):.0f}, p25={or_p25:.0f}, "
          f"median={statistics.median(or_ranges):.0f}, p75={or_p75:.0f}, max={max(or_ranges):.0f}")

    print(f"\n    NARROW OR (bottom 25%): {len(narrow_or)} days")
    if narrow_or:
        big_pct = sum(1 for s in narrow_or if s["is_big_day"]) / len(narrow_or) * 100
        print(f"      Big move day rate: {big_pct:.1f}% (baseline {baseline_big:.1f}%)")
        print(f"      EDGE: {big_pct - baseline_big:+.1f}%")
        pattern_results.append({
            "name": "F: Narrow OR -> Big Move Day",
            "frequency": len(narrow_or),
            "freq_pct": len(narrow_or) / len(sorted_days) * 100,
            "accuracy": big_pct,
            "baseline": baseline_big,
            "edge": big_pct - baseline_big,
        })

    print(f"\n    WIDE OR (top 25%): {len(wide_or)} days")
    if wide_or:
        big_pct = sum(1 for s in wide_or if s["is_big_day"]) / len(wide_or) * 100
        print(f"      Big move day rate: {big_pct:.1f}% (baseline {baseline_big:.1f}%)")

    # OR direction predicts rest-of-day?
    print(f"\n    OR DIRECTION -> REST-OF-DAY DIRECTION:")
    up_or = [s for s in or_stats if s["or_dir"] == "UP"]
    down_or = [s for s in or_stats if s["or_dir"] == "DOWN"]
    if up_or:
        continuation = sum(1 for s in up_or if s["rest_net_dir"] == "UP") / len(up_or) * 100
        print(f"      Bullish OR -> rest UP: {continuation:.1f}% (of {len(up_or)} days)")
        pattern_results.append({
            "name": "F: Bullish OR -> UP Rest-of-Day",
            "frequency": len(up_or),
            "freq_pct": len(up_or) / len(sorted_days) * 100,
            "accuracy": continuation,
            "baseline": 50.0,
            "edge": continuation - 50.0,
        })
    if down_or:
        continuation = sum(1 for s in down_or if s["rest_net_dir"] == "DOWN") / len(down_or) * 100
        print(f"      Bearish OR -> rest DOWN: {continuation:.1f}% (of {len(down_or)} days)")
        pattern_results.append({
            "name": "F: Bearish OR -> DOWN Rest-of-Day",
            "frequency": len(down_or),
            "freq_pct": len(down_or) / len(sorted_days) * 100,
            "accuracy": continuation,
            "baseline": 50.0,
            "edge": continuation - 50.0,
        })

    # OR breakout direction
    print(f"\n    FIRST OR BREAKOUT DIRECTION -> REST-OF-DAY:")
    first_breakout_up = []
    first_breakout_down = []
    for s in or_stats:
        if s["or_range"] > 0:
            candles = nifty_daily[s["date"]]
            if len(candles) < 10:
                continue
            or_high = max(c["high"] for c in candles[:5])
            or_low = min(c["low"] for c in candles[:5])
            # Find which breaks first
            broke_high_first = False
            broke_low_first = False
            for c in candles[5:]:
                if c["high"] > or_high and not broke_low_first:
                    broke_high_first = True
                    break
                if c["low"] < or_low and not broke_high_first:
                    broke_low_first = True
                    break

            if broke_high_first:
                first_breakout_up.append(s)
            elif broke_low_first:
                first_breakout_down.append(s)

    if first_breakout_up:
        up_cont = sum(1 for s in first_breakout_up if s["rest_net_dir"] == "UP") / len(first_breakout_up) * 100
        print(f"      Broke OR high first ({len(first_breakout_up)} days) -> rest UP: {up_cont:.1f}%")
        pattern_results.append({
            "name": "F: OR High Breakout -> UP",
            "frequency": len(first_breakout_up),
            "freq_pct": len(first_breakout_up) / len(sorted_days) * 100,
            "accuracy": up_cont,
            "baseline": 50.0,
            "edge": up_cont - 50.0,
        })
    if first_breakout_down:
        down_cont = sum(1 for s in first_breakout_down if s["rest_net_dir"] == "DOWN") / len(first_breakout_down) * 100
        print(f"      Broke OR low first ({len(first_breakout_down)} days) -> rest DOWN: {down_cont:.1f}%")
        pattern_results.append({
            "name": "F: OR Low Breakout -> DOWN",
            "frequency": len(first_breakout_down),
            "freq_pct": len(first_breakout_down) / len(sorted_days) * 100,
            "accuracy": down_cont,
            "baseline": 50.0,
            "edge": down_cont - 50.0,
        })

    return pattern_results, day_stats, sorted_days


# ============================================================
# Section 3: Intraday compression -> breakout
# ============================================================

def analyze_intraday_compression(nifty_daily):
    print("\n" + "=" * 80)
    print("SECTION 3: INTRADAY COMPRESSION -> BREAKOUT ANALYSIS")
    print("=" * 80)

    # For each day, find sequences of narrow bars and check if breakout follows
    compression_results = []
    all_breakout_results = []

    for d, candles in sorted(nifty_daily.items()):
        if len(candles) < 20:
            continue

        for i in range(3, len(candles) - 5):
            # Check 3-bar compression: all 3 bars have range < threshold
            bars = candles[i-3:i]
            bar_ranges = [c["high"] - c["low"] for c in bars]

            # Dynamic threshold: 40% of average bar range for the day
            day_avg_range = statistics.mean(c["high"] - c["low"] for c in candles)
            threshold = day_avg_range * 0.4

            if all(r <= threshold for r in bar_ranges):
                # Compression detected. Check next 5 bars for breakout
                compression_high = max(c["high"] for c in bars)
                compression_low = min(c["low"] for c in bars)
                comp_range = compression_high - compression_low

                post_bars = candles[i:min(i + 10, len(candles))]
                max_up = max(c["high"] for c in post_bars) - compression_high
                max_down = compression_low - min(c["low"] for c in post_bars)

                broke_up = max_up > comp_range
                broke_down = max_down > comp_range

                # Direction of breakout
                if broke_up and not broke_down:
                    direction = "UP"
                elif broke_down and not broke_up:
                    direction = "DOWN"
                elif broke_up and broke_down:
                    # Check which broke first
                    for c in post_bars:
                        if c["high"] > compression_high + comp_range:
                            direction = "UP"
                            break
                        if c["low"] < compression_low - comp_range:
                            direction = "DOWN"
                            break
                    else:
                        direction = "BOTH"
                else:
                    direction = "NONE"

                magnitude = max(max_up, max_down)
                compression_results.append({
                    "date": d,
                    "time": candles[i]["ts"].strftime("%H:%M"),
                    "comp_range": comp_range,
                    "broke_up": broke_up,
                    "broke_down": broke_down,
                    "direction": direction,
                    "magnitude": magnitude,
                    "max_up": max_up,
                    "max_down": max_down,
                })

    total = len(compression_results)
    if total == 0:
        print("  No compression patterns found")
        return

    broke_any = sum(1 for r in compression_results if r["direction"] in ("UP", "DOWN"))
    broke_big = sum(1 for r in compression_results if r["magnitude"] >= 30)

    print(f"  Total 3-bar compression patterns: {total}")
    print(f"  Breakout (directional): {broke_any}/{total} ({100*broke_any/total:.1f}%)")
    print(f"  Big breakout (30+ pts): {broke_big}/{total} ({100*broke_big/total:.1f}%)")

    # Direction accuracy: among those that broke out, was direction predictable?
    up_breakouts = [r for r in compression_results if r["direction"] == "UP"]
    down_breakouts = [r for r in compression_results if r["direction"] == "DOWN"]
    print(f"  UP breakouts: {len(up_breakouts)}, DOWN breakouts: {len(down_breakouts)}")

    if up_breakouts:
        avg_mag = statistics.mean(r["magnitude"] for r in up_breakouts)
        print(f"  UP breakout avg magnitude: {avg_mag:.0f} pts")
    if down_breakouts:
        avg_mag = statistics.mean(r["magnitude"] for r in down_breakouts)
        print(f"  DOWN breakout avg magnitude: {avg_mag:.0f} pts")

    # Time of day analysis
    print(f"\n  COMPRESSION BREAKOUT BY TIME OF DAY:")
    time_buckets = defaultdict(lambda: {"total": 0, "big": 0})
    for r in compression_results:
        hour = int(r["time"].split(":")[0])
        bucket = f"{hour:02d}:00-{hour:02d}:59"
        time_buckets[bucket]["total"] += 1
        if r["magnitude"] >= 30:
            time_buckets[bucket]["big"] += 1

    for bucket in sorted(time_buckets.keys()):
        s = time_buckets[bucket]
        pct = s["big"] / s["total"] * 100 if s["total"] > 0 else 0
        print(f"    {bucket}: {s['total']} patterns, {s['big']} big breakouts ({pct:.0f}%)")

    # Can we predict direction from prior trend?
    print(f"\n  DIRECTION PREDICTION FROM PRIOR TREND:")
    trend_correct = 0
    trend_total = 0
    for r_idx, r in enumerate(compression_results):
        if r["direction"] not in ("UP", "DOWN"):
            continue
        # Find the candle index
        d = r["date"]
        candles = nifty_daily[d]
        for ci, c in enumerate(candles):
            if c["ts"].strftime("%H:%M") == r["time"]:
                if ci >= 10:
                    # Prior trend = direction of last 10 bars
                    prior_move = candles[ci]["close"] - candles[ci - 10]["close"]
                    prior_dir = "UP" if prior_move > 0 else "DOWN"
                    # Does breakout continue or reverse?
                    if r["direction"] == prior_dir:
                        trend_correct += 1
                    trend_total += 1
                break

    if trend_total > 0:
        print(f"    Continuation (same as prior trend): {trend_correct}/{trend_total} ({100*trend_correct/trend_total:.1f}%)")
        print(f"    Reversal: {trend_total - trend_correct}/{trend_total} ({100*(trend_total-trend_correct)/trend_total:.1f}%)")


# ============================================================
# Section 4: Combined Analysis
# ============================================================

def combined_analysis(nifty_daily, vix_daily, day_stats, sorted_days):
    print("\n" + "=" * 80)
    print("SECTION 4: COMBINED MULTI-PATTERN ANALYSIS")
    print("=" * 80)

    ranges = [day_stats[d]["range"] for d in sorted_days]
    range_p75 = sorted(ranges)[3 * len(ranges) // 4]
    big_move_days = set(d for d in sorted_days if day_stats[d]["range"] >= range_p75)
    baseline_big = len(big_move_days) / len(sorted_days) * 100

    vix_daily_close = {}
    for d, candles in sorted(vix_daily.items()):
        if len(candles) >= 5:
            vix_daily_close[d] = candles[-1]["close"]

    # Per-day signals
    day_signals = defaultdict(set)

    # Narrow range
    p20 = sorted(ranges)[len(ranges) // 5]
    for i in range(len(sorted_days) - 1):
        if day_stats[sorted_days[i]]["range"] <= p20:
            day_signals[sorted_days[i + 1]].add("narrow_range_yday")

    # Inside day
    for i in range(1, len(sorted_days) - 1):
        prev = day_stats[sorted_days[i - 1]]
        curr = day_stats[sorted_days[i]]
        if curr["high"] <= prev["high"] and curr["low"] >= prev["low"]:
            day_signals[sorted_days[i + 1]].add("inside_day_yday")

    # Gap
    for i in range(1, len(sorted_days)):
        gap = day_stats[sorted_days[i]]["open"] - day_stats[sorted_days[i-1]]["close"]
        if gap > 30:
            day_signals[sorted_days[i]].add("gap_up")
        elif gap < -30:
            day_signals[sorted_days[i]].add("gap_down")

    # VIX compression
    vix_sorted = sorted(vix_daily_close.keys())
    streak = 0
    for i in range(1, len(vix_sorted)):
        if vix_daily_close[vix_sorted[i]] < vix_daily_close[vix_sorted[i-1]]:
            streak += 1
            if streak >= 3 and i + 1 < len(vix_sorted):
                day_signals[vix_sorted[i + 1]].add("vix_compressed")
        else:
            streak = 0

    # Close near high/low
    for i in range(len(sorted_days) - 1):
        d = sorted_days[i]
        rng = day_stats[d]["range"]
        if rng > 0:
            close_pct = (day_stats[d]["close"] - day_stats[d]["low"]) / rng
            if close_pct > 0.8:
                day_signals[sorted_days[i + 1]].add("prev_close_high")
            elif close_pct < 0.2:
                day_signals[sorted_days[i + 1]].add("prev_close_low")

    # Analysis by signal count
    print(f"\n  BIG MOVE DAY PREDICTION (range >= {range_p75:.0f} pts, baseline {baseline_big:.1f}%):")
    for n in range(1, 5):
        qualified = [d for d in sorted_days if len(day_signals.get(d, set())) >= n]
        if not qualified:
            continue
        big = sum(1 for d in qualified if d in big_move_days)
        pct = big / len(qualified) * 100
        print(f"    {n}+ signals: {len(qualified)} days, big move rate {pct:.1f}% (edge {pct-baseline_big:+.1f}%)")

    # Direction prediction combining signals
    print(f"\n  DIRECTION PREDICTION (net day close vs open):")
    # Bullish combo: prev_close_high + gap_up
    # Bearish combo: prev_close_low + gap_down
    bullish_signals = {"prev_close_high", "gap_up", "vix_compressed"}
    bearish_signals = {"prev_close_low", "gap_down"}

    for d in sorted_days:
        sigs = day_signals.get(d, set())
        bull_count = len(sigs & bullish_signals)
        bear_count = len(sigs & bearish_signals)

    # Check specific combos
    combos = [
        ("prev_close_high", "UP"),
        ("prev_close_low", "DOWN"),
        ("gap_up + prev_close_high", "UP"),
        ("gap_down + prev_close_low", "DOWN"),
        ("narrow_range_yday + inside_day_yday", None),
    ]

    for combo_str, expected_dir in combos:
        required = set(combo_str.split(" + "))
        qualified = [d for d in sorted_days if required.issubset(day_signals.get(d, set()))]
        if not qualified:
            continue

        if expected_dir:
            correct = sum(1 for d in qualified if day_stats[d]["net_direction"] == expected_dir)
            pct = correct / len(qualified) * 100
            print(f"    {combo_str}: {len(qualified)} days, {expected_dir} correct {correct}/{len(qualified)} ({pct:.1f}%)")
        else:
            big = sum(1 for d in qualified if d in big_move_days)
            pct = big / len(qualified) * 100
            print(f"    {combo_str}: {len(qualified)} days, big move {big}/{len(qualified)} ({pct:.1f}%)")

    # Best combo search
    print(f"\n  ALL 2-SIGNAL COMBINATIONS (min 5 occurrences):")
    all_signals = set()
    for sigs in day_signals.values():
        all_signals.update(sigs)
    all_signals = sorted(all_signals)

    combo_results = []
    for i in range(len(all_signals)):
        for j in range(i + 1, len(all_signals)):
            s1, s2 = all_signals[i], all_signals[j]
            qualified = [d for d in sorted_days if {s1, s2}.issubset(day_signals.get(d, set()))]
            if len(qualified) < 5:
                continue
            big = sum(1 for d in qualified if d in big_move_days)
            pct = big / len(qualified) * 100
            combo_results.append((f"{s1} + {s2}", len(qualified), pct))

    combo_results.sort(key=lambda x: -x[2])
    for combo_str, n, pct in combo_results[:10]:
        print(f"    {combo_str}: {n} days, big move {pct:.1f}% (edge {pct-baseline_big:+.1f}%)")


# ============================================================
# Section 5: Ranking and Recommendations
# ============================================================

def print_ranking_and_recommendations(pattern_results, of_results):
    print("\n" + "=" * 80)
    print("SECTION 5: PATTERN RANKING BY PREDICTIVE POWER")
    print("=" * 80)

    if not pattern_results:
        print("  No patterns to rank")
        return

    # Score = edge * sqrt(frequency) — rewards both accuracy and frequency
    for p in pattern_results:
        p["score"] = p["edge"] * math.sqrt(p["freq_pct"]) / 10

    pattern_results.sort(key=lambda x: -x["score"])

    print(f"\n  {'#':<3} {'Pattern':<40} {'Freq%':>6} {'Accuracy':>9} {'Base':>6} {'Edge':>7} {'Score':>7}")
    print("  " + "-" * 80)
    for i, p in enumerate(pattern_results):
        print(f"  {i+1:<3} {p['name']:<40} {p['freq_pct']:>5.1f}% {p['accuracy']:>8.1f}% "
              f"{p['baseline']:>5.1f}% {p['edge']:>+6.1f}% {p['score']:>6.2f}")

    print("\n" + "=" * 80)
    print("SECTION 6: RECOMMENDATIONS FOR RALLY PREDICTION STRATEGY")
    print("=" * 80)

    # Classify patterns
    directional = [p for p in pattern_results if p["baseline"] == 50.0]
    volatility = [p for p in pattern_results if p["baseline"] != 50.0]

    print(f"""
  DATA SUMMARY:
  - NIFTY 3-min candles: 300 trading days (Jan 2025 - Mar 2026)
  - VIX 3-min candles: same period
  - Orderflow depth: 12 days, ~10-sec intervals, limited strike coverage
  - All patterns use swing-based move detection (50+ pt threshold)

  ORDERFLOW ASSESSMENT:
  - 12 days of data is insufficient for statistical significance
  - Current accuracy: ~{100*sum(1 for r in of_results if r['correct'])/max(len(of_results),1):.0f}% (need >55% to be useful)
  - Main limitation: tracks only 1-7 strikes, not the full chain
  - RECOMMENDATION: Continue collecting, focus on ATM +/- 2 strikes for
    both CE and PE simultaneously. Need 30+ days minimum.

  TOP DIRECTIONAL PATTERNS (predict UP vs DOWN):""")

    for p in sorted(directional, key=lambda x: -abs(x["edge"]))[:5]:
        arrow = "^" if p["edge"] > 0 else "v"
        print(f"    {arrow} {p['name']}: {p['accuracy']:.1f}% accuracy ({p['edge']:+.1f}% edge, {p['freq_pct']:.1f}% freq)")

    print(f"""
  TOP VOLATILITY PATTERNS (predict big move day):""")

    for p in sorted(volatility, key=lambda x: -x["edge"])[:5]:
        print(f"    {p['name']}: {p['accuracy']:.1f}% accuracy ({p['edge']:+.1f}% edge, {p['freq_pct']:.1f}% freq)")

    # Find best directional patterns
    best_dir = sorted(directional, key=lambda x: -abs(x["edge"]))
    best_vol = sorted(volatility, key=lambda x: -x["edge"])

    print(f"""
  PROPOSED PREDICTIVE STRATEGY:

  1. PRE-MARKET SETUP (overnight):
     - Check: Inside day yesterday? Narrow range? Close near high/low?
     - Check: VIX declining 3+ days?
     - These predict WHETHER a big move will happen (volatility)

  2. OPENING RANGE (9:15-9:30):
     - Calculate 15-min OR high/low
     - Narrow OR = high probability of breakout
     - First breakout direction has {best_dir[0]['accuracy']:.0f}% directional accuracy

  3. ENTRY SIGNAL:
     - Wait for OR breakout (direction confirmed)
     - Look for 3-bar compression (narrow bars) near OR level
     - Enter on breakout of compression in OR breakout direction

  4. CONFIDENCE MULTIPLIER:
     - If yesterday was inside day + narrow range: HIGH confidence
     - If VIX was compressed: expect LARGER move
     - If gap aligns with breakout direction: STRONGER signal

  KEY INSIGHT:
  The most reliable predictor is the FIRST OR breakout direction.
  Rather than predicting before market open, the strategy should be:
  - Use overnight signals to SET UP for a trade
  - Use OR breakout for DIRECTION
  - Use compression for TIMING
  - This typically gives a signal by 9:45-10:00 AM
""")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 80)
    print("RALLY PREDICTION ANALYSIS v2 — COMPREHENSIVE REPORT")
    print("=" * 80)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("Recalibrated: 50+ pt swings, directional prediction, big-move-day prediction")

    print("\nLoading data...")
    nifty_daily = load_nifty_candles()
    vix_daily = load_vix_candles()
    of_daily = load_orderflow_data()

    print(f"  NIFTY: {sum(len(v) for v in nifty_daily.values())} candles, {len(nifty_daily)} days")
    print(f"  VIX: {sum(len(v) for v in vix_daily.values())} candles, {len(vix_daily)} days")
    print(f"  Orderflow: {sum(len(v) for v in of_daily.values())} ticks, {len(of_daily)} days")

    # Section 1: Orderflow
    of_results = analyze_orderflow(nifty_daily, of_daily)

    # Section 2: Patterns (A-F)
    pattern_results, day_stats, sorted_days = analyze_patterns(nifty_daily, vix_daily)

    # Section 3: Intraday compression
    analyze_intraday_compression(nifty_daily)

    # Section 4: Combined
    combined_analysis(nifty_daily, vix_daily, day_stats, sorted_days)

    # Section 5+6: Ranking and Recommendations
    print_ranking_and_recommendations(pattern_results, of_results)


if __name__ == "__main__":
    main()
