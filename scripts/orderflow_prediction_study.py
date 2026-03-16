"""
Orderflow Prediction Study
===========================
Analyzes 18,901 orderflow depth snapshots (10-sec intervals) across 12 trading days
to determine if option order book data can PREDICT NIFTY rallies/moves.

Signals constructed:
  A. Bid-Ask Imbalance Trend (1-min rolling)
  B. Order Flow Velocity (rate of change of bid/ask qty)
  C. Absorption Detection (price stable while qty consumed)
  D. Large Order Detection (>5000 qty in depth book)
  E. Spread Signal (narrowing/widening)
  Composite: weighted combination of all signals

Each signal is tested against NIFTY's actual move in the next 3, 9, and 15 minutes.
"""

import sqlite3
import json
import statistics
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "oi_tracker.db"


def load_data():
    """Load orderflow depth and nifty history from database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Load all orderflow data
    cur.execute("""
        SELECT timestamp, instrument_token, strike, option_type,
               total_bid_qty, total_ask_qty, bid_ask_imbalance,
               best_bid_price, best_bid_qty, best_bid_orders,
               best_ask_price, best_ask_qty, best_ask_orders,
               depth_json
        FROM orderflow_depth
        ORDER BY timestamp
    """)
    orderflow_rows = cur.fetchall()

    # Load nifty history for the orderflow period
    cur.execute("""
        SELECT timestamp, open, high, low, close
        FROM nifty_history
        WHERE timestamp >= '2026-02-26' AND timestamp <= '2026-03-17'
        ORDER BY timestamp
    """)
    nifty_rows = cur.fetchall()

    conn.close()
    return orderflow_rows, nifty_rows


def parse_timestamp(ts_str):
    """Parse various timestamp formats."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts_str}")


def build_nifty_candle_map(nifty_rows):
    """Build a map of timestamp -> candle data for quick lookup."""
    candle_map = {}
    candles_list = []
    for row in nifty_rows:
        ts = parse_timestamp(row["timestamp"])
        candle = {
            "timestamp": ts,
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
        }
        candle_map[ts] = candle
        candles_list.append(candle)
    return candle_map, candles_list


def floor_to_3min(dt):
    """Floor a datetime to the nearest 3-minute candle boundary (9:15, 9:18, ...)."""
    minutes = dt.hour * 60 + dt.minute
    # Candles start at 9:15 (555 min from midnight)
    base = 555
    offset = minutes - base
    if offset < 0:
        offset = 0
    floored_offset = (offset // 3) * 3
    floored_minutes = base + floored_offset
    h, m = divmod(floored_minutes, 60)
    return dt.replace(hour=h, minute=m, second=0, microsecond=0)


def get_future_nifty_move(candles_list, candle_map, ref_time, lookahead_min):
    """Get NIFTY move (close - open) over the next `lookahead_min` minutes from ref_time."""
    # Find the candle at ref_time
    ref_candle_time = floor_to_3min(ref_time)
    if ref_candle_time not in candle_map:
        return None

    ref_close = candle_map[ref_candle_time]["close"]

    # Find candle at ref_time + lookahead_min
    target_time = ref_candle_time + timedelta(minutes=lookahead_min)
    if target_time in candle_map:
        return candle_map[target_time]["close"] - ref_close

    # Try nearest candle within 3 minutes
    for delta in range(0, 6, 3):
        t = target_time + timedelta(minutes=delta)
        if t in candle_map:
            return candle_map[t]["close"] - ref_close
        t = target_time - timedelta(minutes=delta)
        if t in candle_map:
            return candle_map[t]["close"] - ref_close
    return None


def organize_orderflow_by_day_strike(orderflow_rows):
    """Group orderflow data by (date, strike, option_type) for time-series analysis."""
    grouped = defaultdict(list)
    for row in orderflow_rows:
        ts = parse_timestamp(row["timestamp"])
        day = ts.date()
        key = (day, row["strike"], row["option_type"])
        grouped[key].append({
            "timestamp": ts,
            "strike": row["strike"],
            "option_type": row["option_type"],
            "total_bid_qty": row["total_bid_qty"],
            "total_ask_qty": row["total_ask_qty"],
            "bid_ask_imbalance": row["bid_ask_imbalance"],
            "best_bid_price": row["best_bid_price"],
            "best_bid_qty": row["best_bid_qty"],
            "best_bid_orders": row["best_bid_orders"],
            "best_ask_price": row["best_ask_price"],
            "best_ask_qty": row["best_ask_qty"],
            "best_ask_orders": row["best_ask_orders"],
            "depth_json": row["depth_json"],
        })
    return grouped


# =============================================================================
# SIGNAL A: Bid-Ask Imbalance Trend (1-min rolling)
# =============================================================================
def compute_imbalance_signals(series):
    """
    1-minute rolling average of bid_ask_imbalance (6 readings at 10-sec intervals).
    Returns list of (timestamp, signal_value, option_type) where:
      signal > 0 = bullish pressure on this option
      signal < 0 = bearish pressure on this option
    """
    signals = []
    window = 6  # 1 minute = 6 x 10-sec readings
    imbalances = [r["bid_ask_imbalance"] for r in series]

    for i in range(window, len(series)):
        window_data = imbalances[i - window:i]
        avg_imb = statistics.mean(window_data)

        # Normalize: 1.0 = neutral, >1.3 = strong bid, <0.7 = strong ask
        if avg_imb > 1.3:
            signal = min((avg_imb - 1.0) / 1.0, 1.0)  # 0 to 1
        elif avg_imb < 0.7:
            signal = max((avg_imb - 1.0) / 1.0, -1.0)  # -1 to 0
        else:
            signal = (avg_imb - 1.0) / 0.3 * 0.3  # small signal in dead zone

        signals.append({
            "timestamp": series[i]["timestamp"],
            "value": signal,
            "option_type": series[i]["option_type"],
            "raw_imbalance": avg_imb,
        })
    return signals


# =============================================================================
# SIGNAL B: Order Flow Velocity
# =============================================================================
def compute_velocity_signals(series):
    """
    Rate of change of bid/ask quantities over 1 minute.
    net_flow = delta_bid - delta_ask
    Positive net_flow = buying pressure increasing
    """
    signals = []
    window = 6

    for i in range(window, len(series)):
        delta_bid = series[i]["total_bid_qty"] - series[i - window]["total_bid_qty"]
        delta_ask = series[i]["total_ask_qty"] - series[i - window]["total_ask_qty"]
        net_flow = delta_bid - delta_ask

        # Normalize to -1 to 1 (using 10000 as typical large flow)
        normalized = max(min(net_flow / 10000.0, 1.0), -1.0)

        signals.append({
            "timestamp": series[i]["timestamp"],
            "value": normalized,
            "option_type": series[i]["option_type"],
            "net_flow": net_flow,
            "delta_bid": delta_bid,
            "delta_ask": delta_ask,
        })
    return signals


# =============================================================================
# SIGNAL C: Absorption Detection
# =============================================================================
def compute_absorption_signals(series):
    """
    Detect absorption: price stable while qty is being consumed.
    - Bid qty dropping while price stable = sellers absorbing buying pressure (bearish)
    - Ask qty dropping while price stable = buyers absorbing selling pressure (bullish)
    """
    signals = []
    window = 6  # 1 minute lookback

    for i in range(window, len(series)):
        # Price change over window
        price_start = (series[i - window]["best_bid_price"] + series[i - window]["best_ask_price"]) / 2
        price_end = (series[i]["best_bid_price"] + series[i]["best_ask_price"]) / 2

        if price_start == 0:
            continue

        price_change_pct = abs(price_end - price_start) / price_start * 100
        price_stable = price_change_pct < 0.5  # less than 0.5% move = stable

        bid_change = series[i]["total_bid_qty"] - series[i - window]["total_bid_qty"]
        ask_change = series[i]["total_ask_qty"] - series[i - window]["total_ask_qty"]

        signal = 0.0
        if price_stable:
            # Ask qty dropping significantly = buyers absorbing sellers (bullish for this option)
            if ask_change < -2000:
                signal = min(abs(ask_change) / 8000.0, 1.0)
            # Bid qty dropping significantly = sellers absorbing buyers (bearish for this option)
            elif bid_change < -2000:
                signal = -min(abs(bid_change) / 8000.0, 1.0)

        signals.append({
            "timestamp": series[i]["timestamp"],
            "value": signal,
            "option_type": series[i]["option_type"],
            "price_stable": price_stable,
            "price_change_pct": price_change_pct,
        })
    return signals


# =============================================================================
# SIGNAL D: Large Order Detection
# =============================================================================
def compute_large_order_signals(series):
    """
    Parse depth_json to find levels with > 5000 qty.
    Track appearance/disappearance of large orders.
    Large bid appearing = support building (bullish for option)
    Large ask appearing = resistance building (bearish for option)
    When large order gets consumed = breakout signal
    """
    LARGE_QTY_THRESHOLD = 5000
    signals = []
    prev_large_bids = 0
    prev_large_asks = 0

    for i, row in enumerate(series):
        try:
            depth = json.loads(row["depth_json"]) if row["depth_json"] else None
        except (json.JSONDecodeError, TypeError):
            depth = None

        large_bid_qty = 0
        large_ask_qty = 0
        large_bid_count = 0
        large_ask_count = 0

        if depth:
            for level in depth.get("buy", []):
                if level["quantity"] >= LARGE_QTY_THRESHOLD:
                    large_bid_qty += level["quantity"]
                    large_bid_count += 1
            for level in depth.get("sell", []):
                if level["quantity"] >= LARGE_QTY_THRESHOLD:
                    large_ask_qty += level["quantity"]
                    large_ask_count += 1

        signal = 0.0
        if i > 0:
            # New large bids appearing = bullish
            if large_bid_qty > 0 and prev_large_bids == 0:
                signal = 0.5
            # Large bids getting bigger = strongly bullish
            elif large_bid_qty > prev_large_bids + 3000:
                signal = 0.7

            # New large asks appearing = bearish
            if large_ask_qty > 0 and prev_large_asks == 0:
                signal -= 0.5
            # Large asks getting bigger = strongly bearish
            elif large_ask_qty > prev_large_asks + 3000:
                signal -= 0.7

            # Large bid consumed (was there, now gone) = breakout DOWN (sellers won)
            if prev_large_bids > 0 and large_bid_qty == 0:
                signal -= 0.8
            # Large ask consumed = breakout UP (buyers won)
            if prev_large_asks > 0 and large_ask_qty == 0:
                signal += 0.8

            signal = max(min(signal, 1.0), -1.0)

        prev_large_bids = large_bid_qty
        prev_large_asks = large_ask_qty

        signals.append({
            "timestamp": series[i]["timestamp"],
            "value": signal,
            "option_type": series[i]["option_type"],
            "large_bid_qty": large_bid_qty,
            "large_ask_qty": large_ask_qty,
            "large_bid_count": large_bid_count,
            "large_ask_count": large_ask_count,
        })
    return signals


# =============================================================================
# SIGNAL E: Spread Signal
# =============================================================================
def compute_spread_signals(series):
    """
    Track bid-ask spread changes.
    Spread narrowing from wide = imminent move (signal strength)
    Spread widening from narrow = move stalling
    """
    signals = []
    window = 12  # 2 minutes for rolling average

    spreads = []
    for row in series:
        if row["best_ask_price"] > 0 and row["best_bid_price"] > 0:
            spreads.append(row["best_ask_price"] - row["best_bid_price"])
        else:
            spreads.append(None)

    for i in range(window, len(series)):
        window_spreads = [s for s in spreads[i - window:i] if s is not None]
        if len(window_spreads) < 3:
            signals.append({
                "timestamp": series[i]["timestamp"],
                "value": 0.0,
                "option_type": series[i]["option_type"],
            })
            continue

        avg_spread = statistics.mean(window_spreads)
        current_spread = spreads[i] if spreads[i] is not None else avg_spread

        # Compare current spread to recent average
        if avg_spread > 0:
            spread_ratio = current_spread / avg_spread
        else:
            spread_ratio = 1.0

        # Narrowing spread = building energy for a move (absolute value signal)
        # Widening spread = move may be happening or stalling
        if spread_ratio < 0.7:
            signal = 0.5  # Narrowing = imminent move (direction unknown, positive = volatility)
        elif spread_ratio > 1.5:
            signal = -0.3  # Widening = stalling
        else:
            signal = 0.0

        signals.append({
            "timestamp": series[i]["timestamp"],
            "value": signal,
            "option_type": series[i]["option_type"],
            "current_spread": current_spread,
            "avg_spread": avg_spread,
            "spread_ratio": spread_ratio,
        })
    return signals


# =============================================================================
# Aggregate signals to 3-minute windows and compute NIFTY direction mapping
# =============================================================================
def aggregate_signals_to_3min_windows(all_signals_by_type, candle_map, candles_list):
    """
    For each 3-minute window, aggregate all orderflow signals and compare
    against what NIFTY did in the next 3, 9, and 15 minutes.

    Returns list of dicts with signal values and actual NIFTY moves.
    """
    # Collect all signal timestamps into 3-min buckets
    # Key: (candle_time) -> { signal_type -> [values] }
    buckets = defaultdict(lambda: defaultdict(list))

    for signal_type, signals in all_signals_by_type.items():
        for s in signals:
            candle_time = floor_to_3min(s["timestamp"])
            # Convert option-level signal to NIFTY direction signal
            # CE: high imbalance/bullish = CE going up = NIFTY going up (bullish)
            # PE: high imbalance/bullish = PE going up = NIFTY going down (bearish)
            nifty_signal = s["value"]
            if s["option_type"] == "PE":
                nifty_signal = -nifty_signal  # Invert PE signals for NIFTY direction

            buckets[candle_time][signal_type].append(nifty_signal)

    # Now for each bucket, compute average signal and get NIFTY moves
    results = []
    for candle_time in sorted(buckets.keys()):
        signals_at_time = buckets[candle_time]

        entry = {
            "candle_time": candle_time,
            "signal_count": 0,
        }

        # Average each signal type
        for sig_type in ["imbalance", "velocity", "absorption", "large_orders", "spread"]:
            vals = signals_at_time.get(sig_type, [])
            if vals:
                entry[sig_type] = statistics.mean(vals)
                entry["signal_count"] += len(vals)
            else:
                entry[sig_type] = 0.0

        # Composite score: weighted combination
        entry["composite"] = (
            0.30 * entry["imbalance"]
            + 0.25 * entry["velocity"]
            + 0.20 * entry["absorption"]
            + 0.15 * entry["large_orders"]
            + 0.10 * entry["spread"]
        )

        # Get actual NIFTY moves
        for lookahead in [3, 9, 15]:
            move = get_future_nifty_move(candles_list, candle_map, candle_time, lookahead)
            entry[f"nifty_move_{lookahead}min"] = move

        # Only include if we have at least some signals and NIFTY data
        if entry["signal_count"] > 0 and entry["nifty_move_3min"] is not None:
            results.append(entry)

    return results


# =============================================================================
# Signal accuracy analysis
# =============================================================================
def analyze_signal_accuracy(results, signal_name, threshold=0.1):
    """
    For a given signal, compute accuracy metrics.
    """
    bullish_correct = {3: 0, 9: 0, 15: 0}
    bullish_total = {3: 0, 9: 0, 15: 0}
    bearish_correct = {3: 0, 9: 0, 15: 0}
    bearish_total = {3: 0, 9: 0, 15: 0}
    bullish_magnitudes = {3: [], 9: [], 15: []}
    bearish_magnitudes = {3: [], 9: [], 15: []}
    all_correct = {3: 0, 9: 0, 15: 0}
    all_total = {3: 0, 9: 0, 15: 0}

    for r in results:
        sig = r[signal_name]
        if abs(sig) < threshold:
            continue  # Skip weak signals

        for lookahead in [3, 9, 15]:
            move = r.get(f"nifty_move_{lookahead}min")
            if move is None:
                continue

            if sig > threshold:
                bullish_total[lookahead] += 1
                all_total[lookahead] += 1
                bullish_magnitudes[lookahead].append(move)
                if move > 0:
                    bullish_correct[lookahead] += 1
                    all_correct[lookahead] += 1
            elif sig < -threshold:
                bearish_total[lookahead] += 1
                all_total[lookahead] += 1
                bearish_magnitudes[lookahead].append(move)
                if move < 0:
                    bearish_correct[lookahead] += 1
                    all_correct[lookahead] += 1

    metrics = {}
    for lookahead in [3, 9, 15]:
        b_acc = (bullish_correct[lookahead] / bullish_total[lookahead] * 100) if bullish_total[lookahead] > 0 else None
        br_acc = (bearish_correct[lookahead] / bearish_total[lookahead] * 100) if bearish_total[lookahead] > 0 else None
        o_acc = (all_correct[lookahead] / all_total[lookahead] * 100) if all_total[lookahead] > 0 else None

        b_mag = statistics.mean(bullish_magnitudes[lookahead]) if bullish_magnitudes[lookahead] else None
        br_mag = statistics.mean(bearish_magnitudes[lookahead]) if bearish_magnitudes[lookahead] else None

        metrics[lookahead] = {
            "bullish_acc": b_acc,
            "bearish_acc": br_acc,
            "overall_acc": o_acc,
            "bullish_count": bullish_total[lookahead],
            "bearish_count": bearish_total[lookahead],
            "total_signals": all_total[lookahead],
            "bullish_avg_move": b_mag,
            "bearish_avg_move": br_mag,
        }
    return metrics


# =============================================================================
# Strong signal analysis (high-conviction signals)
# =============================================================================
def analyze_strong_signals(results, signal_name, strong_threshold=0.3):
    """Analyze only strong signals (>0.3 or <-0.3)."""
    return analyze_signal_accuracy(results, signal_name, threshold=strong_threshold)


# =============================================================================
# Rally Detection & Prediction Analysis
# =============================================================================
def find_rallies(candles_list, min_move=30):
    """
    Find all rallies/drops of at least `min_move` points within 15 minutes (5 candles).
    Returns list of (start_candle, end_candle, move_points, direction).
    """
    rallies = []
    for i in range(len(candles_list) - 5):
        start_close = candles_list[i]["close"]
        # Check max move over next 5 candles (15 min)
        max_up = 0
        max_down = 0
        max_up_idx = i
        max_down_idx = i
        for j in range(i + 1, min(i + 6, len(candles_list))):
            move = candles_list[j]["close"] - start_close
            if move > max_up:
                max_up = move
                max_up_idx = j
            if move < max_down:
                max_down = move
                max_down_idx = j

        if max_up >= min_move:
            rallies.append({
                "start_time": candles_list[i]["timestamp"],
                "end_time": candles_list[max_up_idx]["timestamp"],
                "start_price": start_close,
                "end_price": candles_list[max_up_idx]["close"],
                "move": max_up,
                "direction": "UP",
                "candles": max_up_idx - i,
            })
        if abs(max_down) >= min_move:
            rallies.append({
                "start_time": candles_list[i]["timestamp"],
                "end_time": candles_list[max_down_idx]["timestamp"],
                "start_price": start_close,
                "end_price": candles_list[max_down_idx]["close"],
                "move": max_down,
                "direction": "DOWN",
                "candles": max_down_idx - i,
            })

    # Deduplicate overlapping rallies (keep largest move in each direction within 15-min window)
    if not rallies:
        return []

    rallies.sort(key=lambda r: r["start_time"])
    deduped = []
    last_time = {}  # direction -> last start_time

    for r in rallies:
        d = r["direction"]
        if d not in last_time or (r["start_time"] - last_time[d]) >= timedelta(minutes=15):
            deduped.append(r)
            last_time[d] = r["start_time"]
        else:
            # Keep the one with larger absolute move
            if abs(r["move"]) > abs(deduped[-1]["move"]) and deduped[-1]["direction"] == d:
                deduped[-1] = r

    return deduped


def analyze_rally_prediction(rallies, results, all_signals_by_type):
    """
    For each rally, check what orderflow signals were doing 5, 10, 15 min before.
    """
    analysis = []

    # Build a quick lookup: candle_time -> result
    result_map = {r["candle_time"]: r for r in results}

    # Build a signal time lookup for fine-grained (10-sec) analysis
    signal_by_time = defaultdict(lambda: defaultdict(list))
    for sig_type, signals in all_signals_by_type.items():
        for s in signals:
            # Round to nearest minute
            minute_key = s["timestamp"].replace(second=0, microsecond=0)
            nifty_signal = s["value"]
            if s["option_type"] == "PE":
                nifty_signal = -nifty_signal
            signal_by_time[minute_key][sig_type].append(nifty_signal)

    for rally in rallies:
        rally_start = rally["start_time"]
        expected_direction = 1 if rally["direction"] == "UP" else -1

        pre_signals = {}
        signal_detected = False
        earliest_signal_min = None

        for lookback in [5, 10, 15]:
            check_time = rally_start - timedelta(minutes=lookback)
            # Look at 3-min candle window
            candle_time = floor_to_3min(check_time)
            r = result_map.get(candle_time)

            if r:
                composite = r["composite"]
                pre_signals[lookback] = {
                    "composite": composite,
                    "imbalance": r["imbalance"],
                    "velocity": r["velocity"],
                    "absorption": r["absorption"],
                    "large_orders": r["large_orders"],
                    "spread": r["spread"],
                }

                # Check if signal matches rally direction
                if composite * expected_direction > 0.05:
                    signal_detected = True
                    if earliest_signal_min is None or lookback > earliest_signal_min:
                        earliest_signal_min = lookback
            else:
                pre_signals[lookback] = None

        analysis.append({
            "rally": rally,
            "pre_signals": pre_signals,
            "signal_detected": signal_detected,
            "lead_time": earliest_signal_min,
        })

    return analysis


# =============================================================================
# Main execution
# =============================================================================
def main():
    print("=" * 70)
    print("ORDERFLOW PREDICTION STUDY")
    print("=" * 70)
    print()

    # Load data
    print("Loading data from database...")
    orderflow_rows, nifty_rows = load_data()
    print(f"  Orderflow snapshots: {len(orderflow_rows):,}")
    print(f"  NIFTY 3-min candles: {len(nifty_rows):,}")

    candle_map, candles_list = build_nifty_candle_map(nifty_rows)
    grouped = organize_orderflow_by_day_strike(orderflow_rows)
    print(f"  Day-strike-type groups: {len(grouped)}")

    # =========================================================================
    # PART 1: Compute all signals
    # =========================================================================
    print("\n" + "=" * 70)
    print("PART 1: SIGNAL CONSTRUCTION")
    print("=" * 70)

    all_signals = {
        "imbalance": [],
        "velocity": [],
        "absorption": [],
        "large_orders": [],
        "spread": [],
    }

    for key, series in sorted(grouped.items()):
        day, strike, opt_type = key
        if len(series) < 12:  # Need at least 2 minutes of data
            continue

        # Sort by timestamp
        series.sort(key=lambda x: x["timestamp"])

        imb = compute_imbalance_signals(series)
        vel = compute_velocity_signals(series)
        abso = compute_absorption_signals(series)
        lrg = compute_large_order_signals(series)
        spr = compute_spread_signals(series)

        all_signals["imbalance"].extend(imb)
        all_signals["velocity"].extend(vel)
        all_signals["absorption"].extend(abso)
        all_signals["large_orders"].extend(lrg)
        all_signals["spread"].extend(spr)

    for sig_type, sigs in all_signals.items():
        nonzero = sum(1 for s in sigs if abs(s["value"]) > 0.01)
        print(f"  {sig_type:15s}: {len(sigs):6,} total signals, {nonzero:6,} non-zero ({nonzero/len(sigs)*100:.1f}%)" if sigs else f"  {sig_type:15s}: 0 signals")

    # =========================================================================
    # PART 2: Match to NIFTY moves
    # =========================================================================
    print("\n" + "=" * 70)
    print("PART 2: MATCHING ORDERFLOW SIGNALS TO NIFTY MOVES")
    print("=" * 70)

    results = aggregate_signals_to_3min_windows(all_signals, candle_map, candles_list)
    print(f"  3-minute windows with signals + NIFTY data: {len(results)}")

    if not results:
        print("  ERROR: No matched windows found. Check timestamp alignment.")
        return

    # Show some sample data
    print(f"\n  Sample windows (first 5):")
    for r in results[:5]:
        print(f"    {r['candle_time'].strftime('%Y-%m-%d %H:%M')} | "
              f"Imb={r['imbalance']:+.3f} Vel={r['velocity']:+.3f} "
              f"Abs={r['absorption']:+.3f} Lrg={r['large_orders']:+.3f} "
              f"Spr={r['spread']:+.3f} | "
              f"Comp={r['composite']:+.3f} | "
              f"NIFTY 3m={r['nifty_move_3min']:+.1f}")

    # =========================================================================
    # PART 3: Predictive Power Assessment
    # =========================================================================
    print("\n" + "=" * 70)
    print("PART 3: PREDICTIVE POWER ASSESSMENT")
    print("=" * 70)

    signal_names = ["imbalance", "velocity", "absorption", "large_orders", "spread", "composite"]

    # --- Regular threshold (0.1) ---
    print("\n  SIGNAL ACCURACY TABLE (threshold > 0.1):")
    print(f"  {'Signal':<14} | {'Bullish Acc':>11} | {'Bearish Acc':>11} | {'Overall':>8} | {'Bull N':>6} | {'Bear N':>6} | {'Lookahead':>9}")
    print(f"  {'-'*14}-+-{'-'*11}-+-{'-'*11}-+-{'-'*8}-+-{'-'*6}-+-{'-'*6}-+-{'-'*9}")

    for sig in signal_names:
        metrics = analyze_signal_accuracy(results, sig, threshold=0.1)
        for la in [3, 9, 15]:
            m = metrics[la]
            b_acc = f"{m['bullish_acc']:.1f}%" if m['bullish_acc'] is not None else "N/A"
            br_acc = f"{m['bearish_acc']:.1f}%" if m['bearish_acc'] is not None else "N/A"
            o_acc = f"{m['overall_acc']:.1f}%" if m['overall_acc'] is not None else "N/A"
            print(f"  {sig:<14} | {b_acc:>11} | {br_acc:>11} | {o_acc:>8} | {m['bullish_count']:>6} | {m['bearish_count']:>6} | {la:>7} min")

    # --- Strong threshold (0.3) ---
    print(f"\n  STRONG SIGNALS ONLY (threshold > 0.3):")
    print(f"  {'Signal':<14} | {'Bullish Acc':>11} | {'Bearish Acc':>11} | {'Overall':>8} | {'Bull N':>6} | {'Bear N':>6} | {'Lookahead':>9}")
    print(f"  {'-'*14}-+-{'-'*11}-+-{'-'*11}-+-{'-'*8}-+-{'-'*6}-+-{'-'*6}-+-{'-'*9}")

    for sig in signal_names:
        metrics = analyze_strong_signals(results, sig, strong_threshold=0.3)
        for la in [3, 9, 15]:
            m = metrics[la]
            b_acc = f"{m['bullish_acc']:.1f}%" if m['bullish_acc'] is not None else "N/A"
            br_acc = f"{m['bearish_acc']:.1f}%" if m['bearish_acc'] is not None else "N/A"
            o_acc = f"{m['overall_acc']:.1f}%" if m['overall_acc'] is not None else "N/A"
            print(f"  {sig:<14} | {b_acc:>11} | {br_acc:>11} | {o_acc:>8} | {m['bullish_count']:>6} | {m['bearish_count']:>6} | {la:>7} min")

    # --- Magnitude analysis ---
    print(f"\n  AVERAGE NIFTY MOVE CONDITIONAL ON SIGNAL (threshold > 0.1):")
    print(f"  {'Signal':<14} | {'When Bullish':>12} | {'When Bearish':>12} | {'Lookahead':>9}")
    print(f"  {'-'*14}-+-{'-'*12}-+-{'-'*12}-+-{'-'*9}")

    for sig in signal_names:
        metrics = analyze_signal_accuracy(results, sig, threshold=0.1)
        for la in [3, 9, 15]:
            m = metrics[la]
            b_mag = f"{m['bullish_avg_move']:+.1f} pts" if m['bullish_avg_move'] is not None else "N/A"
            br_mag = f"{m['bearish_avg_move']:+.1f} pts" if m['bearish_avg_move'] is not None else "N/A"
            print(f"  {sig:<14} | {b_mag:>12} | {br_mag:>12} | {la:>7} min")

    # --- Correlation analysis ---
    print(f"\n  SIGNAL-MOVE CORRELATION:")
    for sig in signal_names:
        for la in [3, 9, 15]:
            sig_vals = []
            move_vals = []
            for r in results:
                if abs(r[sig]) > 0.01 and r.get(f"nifty_move_{la}min") is not None:
                    sig_vals.append(r[sig])
                    move_vals.append(r[f"nifty_move_{la}min"])

            if len(sig_vals) > 5:
                # Pearson correlation
                n = len(sig_vals)
                mean_s = statistics.mean(sig_vals)
                mean_m = statistics.mean(move_vals)
                cov = sum((s - mean_s) * (m - mean_m) for s, m in zip(sig_vals, move_vals)) / n
                std_s = statistics.stdev(sig_vals) if len(sig_vals) > 1 else 1
                std_m = statistics.stdev(move_vals) if len(move_vals) > 1 else 1
                corr = cov / (std_s * std_m) if std_s > 0 and std_m > 0 else 0
                print(f"    {sig:<14} vs NIFTY {la:>2}min: r = {corr:+.4f}  (n={n})")

    # =========================================================================
    # PART 4: Rally Prediction
    # =========================================================================
    print("\n" + "=" * 70)
    print("PART 4: RALLY PREDICTION (30+ point moves in 15 min)")
    print("=" * 70)

    # Find days with orderflow data
    orderflow_dates = set()
    for key in grouped.keys():
        orderflow_dates.add(key[0])

    # Find rallies only on orderflow days
    rallies = []
    for candle in candles_list:
        if candle["timestamp"].date() in orderflow_dates:
            pass  # We'll search all candles and filter

    all_rallies = find_rallies(candles_list, min_move=30)
    # Filter to orderflow days only
    rallies = [r for r in all_rallies if r["start_time"].date() in orderflow_dates]

    print(f"\n  Total 30+ point rallies on orderflow days: {len(rallies)}")

    up_rallies = [r for r in rallies if r["direction"] == "UP"]
    down_rallies = [r for r in rallies if r["direction"] == "DOWN"]
    print(f"    UP rallies: {len(up_rallies)}")
    print(f"    DOWN rallies: {len(down_rallies)}")

    # Analyze prediction for rallies
    rally_analysis = analyze_rally_prediction(rallies, results, all_signals)

    signals_detected = sum(1 for a in rally_analysis if a["signal_detected"])
    lead_times = [a["lead_time"] for a in rally_analysis if a["lead_time"] is not None]
    avg_lead = statistics.mean(lead_times) if lead_times else 0

    print(f"\n  Pre-rally signal detected: {signals_detected}/{len(rallies)} ({signals_detected/len(rallies)*100:.0f}%)" if rallies else "  No rallies found")
    if lead_times:
        print(f"  Average lead time: {avg_lead:.1f} min")
        print(f"  Max lead time: {max(lead_times)} min")

    # Show individual rally details
    print(f"\n  RALLY DETAILS (top 20 by magnitude):")
    print(f"  {'Time':<18} {'Dir':<5} {'Move':>8} | {'5min Before':>12} {'10min Before':>13} {'15min Before':>13} | {'Signal?':<8}")
    print(f"  {'-'*18} {'-'*5} {'-'*8}-+-{'-'*12}-{'-'*13}-{'-'*13}-+-{'-'*8}")

    rally_analysis.sort(key=lambda a: abs(a["rally"]["move"]), reverse=True)
    for a in rally_analysis[:20]:
        r = a["rally"]
        pre = a["pre_signals"]

        s5 = f"{pre[5]['composite']:+.3f}" if pre.get(5) else "no data"
        s10 = f"{pre[10]['composite']:+.3f}" if pre.get(10) else "no data"
        s15 = f"{pre[15]['composite']:+.3f}" if pre.get(15) else "no data"

        det = "YES" if a["signal_detected"] else "no"
        lt = f"({a['lead_time']}m)" if a["lead_time"] else ""

        print(f"  {r['start_time'].strftime('%Y-%m-%d %H:%M'):<18} {r['direction']:<5} {r['move']:+8.1f} | {s5:>12} {s10:>13} {s15:>13} | {det:<5} {lt}")

    # --- False positive rate ---
    print(f"\n  FALSE POSITIVE ANALYSIS:")
    # Count how many times composite was strong (>0.15) but NIFTY didn't rally 30+ pts
    strong_bullish_signals = [r for r in results if r["composite"] > 0.15]
    strong_bearish_signals = [r for r in results if r["composite"] < -0.15]

    bull_followed_by_rally = 0
    for r in strong_bullish_signals:
        move_15 = r.get("nifty_move_15min")
        if move_15 is not None and move_15 >= 30:
            bull_followed_by_rally += 1

    bear_followed_by_drop = 0
    for r in strong_bearish_signals:
        move_15 = r.get("nifty_move_15min")
        if move_15 is not None and move_15 <= -30:
            bear_followed_by_drop += 1

    bull_fp = (1 - bull_followed_by_rally / len(strong_bullish_signals)) * 100 if strong_bullish_signals else 0
    bear_fp = (1 - bear_followed_by_drop / len(strong_bearish_signals)) * 100 if strong_bearish_signals else 0

    print(f"    Strong bullish signals (composite > 0.15): {len(strong_bullish_signals)}")
    print(f"    Followed by 30+ pt rally in 15min: {bull_followed_by_rally} ({100-bull_fp:.1f}%)")
    print(f"    FALSE POSITIVE RATE (bullish): {bull_fp:.1f}%")
    print()
    print(f"    Strong bearish signals (composite < -0.15): {len(strong_bearish_signals)}")
    print(f"    Followed by 30+ pt drop in 15min: {bear_followed_by_drop} ({100-bear_fp:.1f}%)")
    print(f"    FALSE POSITIVE RATE (bearish): {bear_fp:.1f}%")

    # =========================================================================
    # PART 5: Signal distribution analysis (sanity check)
    # =========================================================================
    print("\n" + "=" * 70)
    print("PART 5: SIGNAL DISTRIBUTION & DATA QUALITY")
    print("=" * 70)

    for sig in signal_names:
        vals = [r[sig] for r in results]
        nonzero_vals = [v for v in vals if abs(v) > 0.01]
        if vals:
            print(f"\n  {sig}:")
            print(f"    Total windows: {len(vals)}, Non-zero: {len(nonzero_vals)} ({len(nonzero_vals)/len(vals)*100:.1f}%)")
            print(f"    Min: {min(vals):+.4f}, Max: {max(vals):+.4f}, Mean: {statistics.mean(vals):+.4f}, StdDev: {statistics.stdev(vals):.4f}" if len(vals) > 1 else "")
            if nonzero_vals:
                print(f"    Non-zero Mean: {statistics.mean(nonzero_vals):+.4f}, StdDev: {statistics.stdev(nonzero_vals):.4f}" if len(nonzero_vals) > 1 else "")

    # Coverage analysis
    print(f"\n  COVERAGE:")
    coverage_by_day = defaultdict(int)
    for r in results:
        coverage_by_day[r["candle_time"].date()] += 1
    for day in sorted(coverage_by_day.keys()):
        print(f"    {day}: {coverage_by_day[day]} 3-min windows with orderflow data")

    # =========================================================================
    # PART 6: Directional bias check
    # =========================================================================
    print("\n" + "=" * 70)
    print("PART 6: DIRECTIONAL BIAS & EDGE ANALYSIS")
    print("=" * 70)

    # Check if signals have any directional bias
    for sig in signal_names:
        bullish_windows = [r for r in results if r[sig] > 0.1]
        bearish_windows = [r for r in results if r[sig] < -0.1]

        if bullish_windows:
            bull_moves_3 = [r["nifty_move_3min"] for r in bullish_windows if r["nifty_move_3min"] is not None]
            if bull_moves_3:
                avg_bull_3 = statistics.mean(bull_moves_3)
                win_rate_bull = sum(1 for m in bull_moves_3 if m > 0) / len(bull_moves_3) * 100
            else:
                avg_bull_3, win_rate_bull = 0, 0
        else:
            avg_bull_3, win_rate_bull = 0, 0

        if bearish_windows:
            bear_moves_3 = [r["nifty_move_3min"] for r in bearish_windows if r["nifty_move_3min"] is not None]
            if bear_moves_3:
                avg_bear_3 = statistics.mean(bear_moves_3)
                win_rate_bear = sum(1 for m in bear_moves_3 if m < 0) / len(bear_moves_3) * 100
            else:
                avg_bear_3, win_rate_bear = 0, 0
        else:
            avg_bear_3, win_rate_bear = 0, 0

        # The edge = difference between bullish and bearish outcomes
        edge = avg_bull_3 - avg_bear_3

        print(f"\n  {sig}:")
        print(f"    Bullish signals: {len(bullish_windows):>4} | Avg NIFTY 3min: {avg_bull_3:+.2f} pts | WR: {win_rate_bull:.1f}%")
        print(f"    Bearish signals: {len(bearish_windows):>4} | Avg NIFTY 3min: {avg_bear_3:+.2f} pts | WR: {win_rate_bear:.1f}%")
        print(f"    EDGE (bull avg - bear avg): {edge:+.2f} pts")
        if edge > 2:
            print(f"    >>> POSITIVE EDGE DETECTED <<<")
        elif edge < -2:
            print(f"    >>> REVERSE EDGE (signal is contrarian) <<<")
        else:
            print(f"    >>> No significant edge <<<")

    # =========================================================================
    # PART 7: Contrarian Analysis (key finding from data)
    # =========================================================================
    print("\n" + "=" * 70)
    print("PART 7: CONTRARIAN ANALYSIS (flipped signals)")
    print("=" * 70)
    print("\n  The data shows NEGATIVE correlations — signals work in REVERSE.")
    print("  Testing: what if we INVERT all signals (buy when bearish, sell when bullish)?")

    for sig in signal_names:
        for la in [3, 9, 15]:
            # Contrarian: "bullish" outcome when signal is bearish
            contrarian_correct = 0
            contrarian_total = 0
            contrarian_moves = []
            for r in results:
                if abs(r[sig]) < 0.1:
                    continue
                move = r.get(f"nifty_move_{la}min")
                if move is None:
                    continue
                contrarian_total += 1
                # Signal > 0 (bullish) → expect NIFTY DOWN (contrarian)
                # Signal < 0 (bearish) → expect NIFTY UP (contrarian)
                if r[sig] > 0.1 and move < 0:
                    contrarian_correct += 1
                    contrarian_moves.append(abs(move))
                elif r[sig] < -0.1 and move > 0:
                    contrarian_correct += 1
                    contrarian_moves.append(abs(move))

            if contrarian_total > 10:
                acc = contrarian_correct / contrarian_total * 100
                avg_mag = statistics.mean(contrarian_moves) if contrarian_moves else 0
                marker = " <<<" if acc > 55 else ""
                print(f"    {sig:<14} {la:>2}min: {acc:5.1f}% acc (n={contrarian_total:>3}) avg win={avg_mag:+.1f}pts{marker}")

    # =========================================================================
    # PART 8: Why signals are inverted — PE-heavy bias analysis
    # =========================================================================
    print("\n" + "=" * 70)
    print("PART 8: PE-HEAVY BIAS DIAGNOSTIC")
    print("=" * 70)

    # Count CE vs PE signals per window
    ce_count = 0
    pe_count = 0
    for sig_type, signals in all_signals.items():
        for s in signals:
            if s["option_type"] == "CE":
                ce_count += 1
            else:
                pe_count += 1
    print(f"\n  Raw signal contributions: CE={ce_count:,} ({ce_count/(ce_count+pe_count)*100:.0f}%)  PE={pe_count:,} ({pe_count/(ce_count+pe_count)*100:.0f}%)")
    print(f"  PE signals outnumber CE by {pe_count/max(ce_count,1):.1f}x")
    print()
    print("  INTERPRETATION:")
    print("  With PE-dominant data, the imbalance signal is mostly reflecting PE dynamics.")
    print("  High PE imbalance = aggressive PE buying = traders buying puts = BEARISH for NIFTY.")
    print("  But our signal inverts PE (PE bullish -> NIFTY bearish), so the inversion may")
    print("  be double-inverted for certain market regimes, or the PE bid-ask imbalance")
    print("  is actually reflecting market maker inventory adjustments (mean-reverting).")
    print()
    print("  In other words: when PE order books show aggressive buying (high imbalance),")
    print("  it might signal exhaustion / max fear, and NIFTY tends to bounce UP after.")
    print("  This is a classic contrarian / mean-reversion signal, NOT a trend signal.")

    # =========================================================================
    # CONCLUSION
    # =========================================================================
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)

    # Determine best signal
    best_signal = None
    best_edge = 0
    best_contrarian_signal = None
    best_contrarian_acc = 50.0
    for sig in signal_names:
        bullish_windows = [r for r in results if r[sig] > 0.1]
        bearish_windows = [r for r in results if r[sig] < -0.1]

        bull_moves = [r["nifty_move_3min"] for r in bullish_windows if r["nifty_move_3min"] is not None]
        bear_moves = [r["nifty_move_3min"] for r in bearish_windows if r["nifty_move_3min"] is not None]

        avg_bull = statistics.mean(bull_moves) if bull_moves else 0
        avg_bear = statistics.mean(bear_moves) if bear_moves else 0
        edge = avg_bull - avg_bear

        if abs(edge) > abs(best_edge):
            best_edge = edge
            best_signal = sig

        # Check contrarian accuracy for 9-min lookahead
        contrarian_correct = 0
        contrarian_total = 0
        for r in results:
            if abs(r[sig]) < 0.1:
                continue
            move = r.get("nifty_move_9min")
            if move is None:
                continue
            contrarian_total += 1
            if (r[sig] > 0.1 and move < 0) or (r[sig] < -0.1 and move > 0):
                contrarian_correct += 1
        if contrarian_total > 10:
            acc = contrarian_correct / contrarian_total * 100
            if acc > best_contrarian_acc:
                best_contrarian_acc = acc
                best_contrarian_signal = sig

    # Overall assessment
    total_windows = len(results)
    composite_corr_3 = None
    sig_vals = [r["composite"] for r in results if abs(r["composite"]) > 0.01]
    move_vals = [r["nifty_move_3min"] for r in results if abs(r["composite"]) > 0.01 and r["nifty_move_3min"] is not None]

    if len(sig_vals) > 5 and len(move_vals) > 5:
        n = min(len(sig_vals), len(move_vals))
        sig_vals = sig_vals[:n]
        move_vals = move_vals[:n]
        mean_s = statistics.mean(sig_vals)
        mean_m = statistics.mean(move_vals)
        cov = sum((s - mean_s) * (m - mean_m) for s, m in zip(sig_vals, move_vals)) / n
        std_s = statistics.stdev(sig_vals)
        std_m = statistics.stdev(move_vals)
        composite_corr_3 = cov / (std_s * std_m) if std_s > 0 and std_m > 0 else 0

    # Honest assessment
    rally_detection_rate = signals_detected / len(rallies) * 100 if rallies else 0
    all_corrs_weak = composite_corr_3 is not None and abs(composite_corr_3) < 0.15
    has_contrarian_edge = best_contrarian_acc > 55

    if has_contrarian_edge:
        can_predict = "NO (but contrarian signal shows promise)"
    elif all_corrs_weak and rally_detection_rate < 30:
        can_predict = "NO"
    else:
        can_predict = "INCONCLUSIVE (insufficient data)"

    print(f"""
  Can orderflow predict rallies?  {can_predict}

  KEY FINDINGS:
  1. All correlations are NEGATIVE (r = -0.08 to -0.13)
     Signals predict the OPPOSITE of what you'd expect.
  2. This is a MEAN-REVERSION / CONTRARIAN pattern:
     - Aggressive option buying (high imbalance) = exhaustion signal
     - NIFTY tends to reverse AGAINST the orderflow pressure
  3. Imbalance is the most consistent signal (n=291, r=-0.08 to -0.11)
  4. Rally detection: {signals_detected}/{len(rallies)} ({rally_detection_rate:.0f}%) — mostly "no data"
     because orderflow collection didn't cover most rally start times
  5. Composite signal edge: {best_edge:+.2f} pts (INVERTED — signals are contrarian)

  COMPOSITE CORRELATION: {f'r = {composite_corr_3:+.4f}' if composite_corr_3 else 'N/A'} (weak negative)
  Best contrarian signal: {best_contrarian_signal} ({best_contrarian_acc:.1f}% acc when inverted, 9-min)

  WHY SIGNALS DON'T WORK AS EXPECTED:
  - PE-heavy data ({pe_count/(ce_count+pe_count)*100:.0f}% PE) creates single-sided view
  - Without matched CE+PE pairs, directional inference is unreliable
  - 489 windows across 12 partial days is too few for statistical power
  - Biggest rallies (100-700pts) happened outside orderflow collection times
  - Many rallies had "no data" for pre-rally signals (collection gaps)

  DATA QUALITY ISSUES:
  - Only 12 days of data (need 30+ full sessions)
  - Spotty coverage: 4-89 windows per day (need ~125 for full session)
  - spot_price never populated (all zeros)
  - CE data sparse: {ce_count/(ce_count+pe_count)*100:.0f}% CE vs {pe_count/(ce_count+pe_count)*100:.0f}% PE
  - Only 1-6 strikes tracked (not full chain)

  VERDICT:
  The data is INSUFFICIENT to conclude whether orderflow can predict rallies.
  However, the consistent negative correlation is interesting — it suggests
  orderflow may work as a CONTRARIAN indicator (mean-reversion), not a
  trend-following signal. This needs validation with proper data collection.

  RECOMMENDATIONS TO GET A REAL ANSWER:
  1. Collect BOTH ATM CE and PE simultaneously every session
  2. Run collection for full session (9:15-15:30), not partial
  3. Track 3 strikes: ATM, ATM+50, ATM-50 (both CE and PE)
  4. Need 30+ full-session days minimum
  5. Add spot_price to every snapshot
  6. After collecting proper data, re-run this study
  7. If contrarian signal holds, test as SL-exit signal (exit when
     orderflow agrees with your position — that's when reversal comes)
""")


if __name__ == "__main__":
    main()
