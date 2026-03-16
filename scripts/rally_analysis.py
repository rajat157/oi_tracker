"""
Rally Analysis Script
=====================
Analyzes spot price rallies in NIFTY data and reverse-engineers pre-rally indicators
from OI snapshots and analysis history.

Usage: uv run python scripts/rally_analysis.py
"""

import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"

# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────
RALLY_MIN_MAGNITUDE = 30       # Minimum 30-point move to qualify as rally
SWING_REVERSAL_THRESHOLD = 15  # Min reversal to confirm a swing high/low
PRE_RALLY_LOOKBACK = 3         # Number of candles (3-min each) to look back before rally
SPOT_SPIKE_THRESHOLD = 500     # Filter out bad data (>500 pt jump in 3 min)
STRIKE_STEP = 50               # NIFTY option strike step
ITM_OFFSET = 100               # How far ITM for premium tracking (2 strikes)


def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ──────────────────────────────────────────────────────────────────────
# Phase 1: Data Loading
# ──────────────────────────────────────────────────────────────────────

def load_spot_data(conn):
    """Load spot price time series grouped by day, filtering bad data."""
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, spot_price, vix, futures_oi, futures_oi_change,
               futures_basis, verdict, call_oi_change, put_oi_change,
               total_call_oi, total_put_oi, signal_confidence,
               atm_call_oi_change, atm_put_oi_change,
               itm_call_oi_change, itm_put_oi_change, iv_skew, max_pain
        FROM analysis_history
        ORDER BY timestamp
    """)

    days = defaultdict(list)
    for row in cur.fetchall():
        ts = datetime.fromisoformat(row["timestamp"])
        date_str = ts.strftime("%Y-%m-%d")
        days[date_str].append(dict(row) | {"ts": ts})

    # Filter out bad data points (spot = 23000 glitch, etc.)
    cleaned_days = {}
    for date_str, points in sorted(days.items()):
        cleaned = []
        for i, p in enumerate(points):
            if i > 0 and abs(p["spot_price"] - points[i-1]["spot_price"]) > SPOT_SPIKE_THRESHOLD:
                continue  # Skip spike
            if p["spot_price"] < 20000:  # Obviously bad
                continue
            cleaned.append(p)
        if len(cleaned) >= 10:  # Need enough data points
            cleaned_days[date_str] = cleaned

    return cleaned_days


def load_oi_snapshots(conn, timestamp):
    """Load OI snapshot data for a specific timestamp."""
    cur = conn.cursor()
    cur.execute("""
        SELECT strike_price, ce_oi, pe_oi, ce_oi_change, pe_oi_change,
               ce_ltp, pe_ltp, ce_volume, pe_volume, ce_iv, pe_iv,
               ce_buy_qty, ce_sell_qty, pe_buy_qty, pe_sell_qty, spot_price
        FROM oi_snapshots
        WHERE timestamp = ?
        ORDER BY strike_price
    """, (timestamp,))
    return [dict(r) for r in cur.fetchall()]


def load_oi_snapshots_for_day(conn, date_str):
    """Load all OI snapshots for a day, indexed by timestamp."""
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, strike_price, ce_oi, pe_oi, ce_oi_change, pe_oi_change,
               ce_ltp, pe_ltp, ce_volume, pe_volume, ce_iv, pe_iv,
               ce_buy_qty, ce_sell_qty, pe_buy_qty, pe_sell_qty, spot_price
        FROM oi_snapshots
        WHERE DATE(timestamp) = ?
        ORDER BY timestamp, strike_price
    """, (date_str,))

    by_ts = defaultdict(list)
    for row in cur.fetchall():
        by_ts[row["timestamp"]].append(dict(row))
    return dict(by_ts)


# ──────────────────────────────────────────────────────────────────────
# Phase 1: Swing Detection & Rally Identification
# ──────────────────────────────────────────────────────────────────────

def find_swings(prices):
    """
    Find local highs and lows using a swing detection algorithm.
    A swing high is confirmed when price drops by SWING_REVERSAL_THRESHOLD from a local max.
    A swing low is confirmed when price rises by SWING_REVERSAL_THRESHOLD from a local min.

    Returns list of (index, price, type) where type is 'HIGH' or 'LOW'.
    """
    if len(prices) < 3:
        return []

    swings = []
    # Track running max and min since last swing
    last_swing_type = None
    running_max = prices[0]
    running_max_idx = 0
    running_min = prices[0]
    running_min_idx = 0

    for i in range(1, len(prices)):
        p = prices[i]

        if p > running_max:
            running_max = p
            running_max_idx = i
        if p < running_min:
            running_min = p
            running_min_idx = i

        # Check for swing high confirmation
        if running_max - p >= SWING_REVERSAL_THRESHOLD and last_swing_type != 'HIGH':
            swings.append((running_max_idx, running_max, 'HIGH'))
            last_swing_type = 'HIGH'
            running_min = p
            running_min_idx = i

        # Check for swing low confirmation
        if p - running_min >= SWING_REVERSAL_THRESHOLD and last_swing_type != 'LOW':
            swings.append((running_min_idx, running_min, 'LOW'))
            last_swing_type = 'LOW'
            running_max = p
            running_max_idx = i

    return swings


def find_rallies(day_data):
    """
    Find rallies from swing points. A rally is a move from a swing low to the next
    swing high (up rally) or swing high to next swing low (down rally) of 30+ points.
    """
    prices = [p["spot_price"] for p in day_data]
    swings = find_swings(prices)

    rallies = []
    for i in range(len(swings) - 1):
        idx1, price1, type1 = swings[i]
        idx2, price2, type2 = swings[i + 1]

        magnitude = abs(price2 - price1)
        if magnitude < RALLY_MIN_MAGNITUDE:
            continue

        if type1 == 'LOW' and type2 == 'HIGH':
            direction = 'UP'
        elif type1 == 'HIGH' and type2 == 'LOW':
            direction = 'DOWN'
        else:
            continue

        start_point = day_data[idx1]
        end_point = day_data[idx2]
        duration_min = (end_point["ts"] - start_point["ts"]).total_seconds() / 60

        # What was happening 15 min before rally start?
        pre_context = classify_pre_rally_context(day_data, idx1)

        rallies.append({
            "start_idx": idx1,
            "end_idx": idx2,
            "direction": direction,
            "start_price": price1,
            "end_price": price2,
            "magnitude": magnitude,
            "start_time": start_point["ts"],
            "end_time": end_point["ts"],
            "duration_min": duration_min,
            "start_timestamp": start_point["timestamp"],
            "end_timestamp": end_point["timestamp"],
            "pre_context": pre_context,
        })

    return rallies


def classify_pre_rally_context(day_data, rally_start_idx):
    """Classify what spot was doing 15 min before rally start (~5 candles back)."""
    lookback = 5
    start = max(0, rally_start_idx - lookback)
    pre_prices = [day_data[i]["spot_price"] for i in range(start, rally_start_idx + 1)]

    if len(pre_prices) < 3:
        return "INSUFFICIENT_DATA"

    # Calculate net move and volatility
    net_move = pre_prices[-1] - pre_prices[0]
    high = max(pre_prices)
    low = min(pre_prices)
    price_range = high - low

    # Classify
    if price_range < 15:
        return "RANGING"
    elif abs(net_move) > price_range * 0.6:
        if net_move > 0:
            return "TRENDING_UP"
        else:
            return "TRENDING_DOWN"
    else:
        # Check if price reversed direction
        mid = len(pre_prices) // 2
        first_half_move = pre_prices[mid] - pre_prices[0]
        second_half_move = pre_prices[-1] - pre_prices[mid]
        if first_half_move * second_half_move < 0:
            return "REVERSING"
        return "CHOPPY"


# ──────────────────────────────────────────────────────────────────────
# Phase 1 & 2: Premium Tracking
# ──────────────────────────────────────────────────────────────────────

def get_nearest_strike(spot, offset=0):
    """Get nearest NIFTY strike to spot price, with optional offset."""
    base = round(spot / STRIKE_STEP) * STRIKE_STEP
    return base + offset


def find_premium_at_timestamp(oi_data_for_ts, target_strike, option_type):
    """Find premium (LTP) for a given strike and option type at a timestamp."""
    if not oi_data_for_ts:
        return None

    col = "ce_ltp" if option_type == "CE" else "pe_ltp"

    for snap in oi_data_for_ts:
        if snap["strike_price"] == target_strike:
            ltp = snap[col]
            return ltp if ltp and ltp > 0 else None
    return None


def track_premium_for_rally(rally, oi_by_ts, day_data):
    """Track CE/PE premium at rally start and peak."""
    spot_at_start = rally["start_price"]

    # For UP rally: track CE premium (slightly ITM = 2 strikes below spot)
    # For DOWN rally: track PE premium (slightly ITM = 2 strikes above spot)
    if rally["direction"] == "UP":
        strike = get_nearest_strike(spot_at_start, -ITM_OFFSET)
        opt_type = "CE"
    else:
        strike = get_nearest_strike(spot_at_start, ITM_OFFSET)
        opt_type = "PE"

    start_ts = rally["start_timestamp"]
    end_ts = rally["end_timestamp"]

    start_premium = find_premium_at_timestamp(
        oi_by_ts.get(start_ts, []), strike, opt_type
    )
    end_premium = find_premium_at_timestamp(
        oi_by_ts.get(end_ts, []), strike, opt_type
    )

    premium_change_pct = None
    premium_change_abs = None
    if start_premium and end_premium and start_premium > 0:
        premium_change_pct = ((end_premium - start_premium) / start_premium) * 100
        premium_change_abs = end_premium - start_premium

    return {
        "strike": strike,
        "option_type": opt_type,
        "start_premium": start_premium,
        "end_premium": end_premium,
        "premium_change_pct": premium_change_pct,
        "premium_change_abs": premium_change_abs,
    }


# ──────────────────────────────────────────────────────────────────────
# Phase 2 & 3: Pre-Rally Indicator Analysis
# ──────────────────────────────────────────────────────────────────────

def analyze_pre_rally_indicators(rally, day_data, oi_by_ts):
    """Analyze indicators in the 2-3 candles before rally start."""
    start_idx = rally["start_idx"]
    lookback_start = max(0, start_idx - PRE_RALLY_LOOKBACK)

    indicators = {
        "oi_buildup": False,
        "oi_buildup_detail": None,
        "premium_squeeze": False,
        "premium_squeeze_detail": None,
        "volume_spike": False,
        "volume_spike_detail": None,
        "vix_change": None,
        "futures_basis_change": None,
        "futures_oi_change": None,
        "pcr_change": None,
        "support_resistance_break": False,
    }

    pre_points = day_data[lookback_start:start_idx + 1]
    if len(pre_points) < 2:
        return indicators

    # --- OI Buildup Analysis ---
    total_ce_oi_chg = sum(p.get("call_oi_change", 0) or 0 for p in pre_points)
    total_pe_oi_chg = sum(p.get("put_oi_change", 0) or 0 for p in pre_points)

    if rally["direction"] == "UP":
        # Before up rally: PE OI buildup = trapped shorts (bullish signal)
        if total_pe_oi_chg > total_ce_oi_chg * 1.3 and total_pe_oi_chg > 0:
            indicators["oi_buildup"] = True
            indicators["oi_buildup_detail"] = f"PE OI +{total_pe_oi_chg:,} vs CE OI +{total_ce_oi_chg:,} (trapped shorts)"
    else:
        # Before down rally: CE OI buildup = trapped longs (bearish signal)
        if total_ce_oi_chg > total_pe_oi_chg * 1.3 and total_ce_oi_chg > 0:
            indicators["oi_buildup"] = True
            indicators["oi_buildup_detail"] = f"CE OI +{total_ce_oi_chg:,} vs PE OI +{total_pe_oi_chg:,} (trapped longs)"

    # --- VIX Change ---
    vix_values = [p.get("vix", 0) or 0 for p in pre_points if (p.get("vix", 0) or 0) > 0]
    if len(vix_values) >= 2:
        indicators["vix_change"] = vix_values[-1] - vix_values[0]

    # --- Futures Basis Change ---
    basis_values = [p.get("futures_basis", 0) or 0 for p in pre_points if (p.get("futures_basis", 0) or 0) != 0]
    if len(basis_values) >= 2:
        indicators["futures_basis_change"] = basis_values[-1] - basis_values[0]

    # --- Futures OI Change ---
    foi_values = [p.get("futures_oi_change", 0) or 0 for p in pre_points]
    indicators["futures_oi_change"] = sum(foi_values)

    # --- PCR Change ---
    pcr_values = []
    for p in pre_points:
        tco = p.get("total_call_oi", 0) or 0
        tpo = p.get("total_put_oi", 0) or 0
        if tco > 0:
            pcr_values.append(tpo / tco)
    if len(pcr_values) >= 2:
        indicators["pcr_change"] = pcr_values[-1] - pcr_values[0]

    # --- Premium Squeeze Analysis ---
    # Check if ATM premiums were compressing before breakout
    spot = rally["start_price"]
    atm_strike = get_nearest_strike(spot)
    ce_premiums = []
    pe_premiums = []

    for p in pre_points:
        ts = p["timestamp"]
        oi_data = oi_by_ts.get(ts, [])
        ce_p = find_premium_at_timestamp(oi_data, atm_strike, "CE")
        pe_p = find_premium_at_timestamp(oi_data, atm_strike, "PE")
        if ce_p:
            ce_premiums.append(ce_p)
        if pe_p:
            pe_premiums.append(pe_p)

    if len(ce_premiums) >= 2 and len(pe_premiums) >= 2:
        # Straddle value (CE + PE premium)
        straddle_start = ce_premiums[0] + pe_premiums[0]
        straddle_end = ce_premiums[-1] + pe_premiums[-1]
        if straddle_start > 0:
            straddle_change_pct = ((straddle_end - straddle_start) / straddle_start) * 100
            if straddle_change_pct < -2:  # Premiums compressing > 2%
                indicators["premium_squeeze"] = True
                indicators["premium_squeeze_detail"] = f"Straddle {straddle_start:.1f} -> {straddle_end:.1f} ({straddle_change_pct:+.1f}%)"

    # --- Volume Spike Analysis ---
    # Compare volume in pre-rally candles vs earlier average
    if start_idx >= 10:
        earlier_points = day_data[max(0, start_idx - 10):start_idx - PRE_RALLY_LOOKBACK]
        pre_ce_vol = sum(p.get("call_oi_change", 0) or 0 for p in pre_points)
        pre_pe_vol = sum(p.get("put_oi_change", 0) or 0 for p in pre_points)
        pre_total = abs(pre_ce_vol) + abs(pre_pe_vol)

        if earlier_points:
            avg_ce_vol = sum(abs(p.get("call_oi_change", 0) or 0) for p in earlier_points) / len(earlier_points)
            avg_pe_vol = sum(abs(p.get("put_oi_change", 0) or 0) for p in earlier_points) / len(earlier_points)
            avg_total = (avg_ce_vol + avg_pe_vol) * len(pre_points)

            if avg_total > 0 and pre_total > avg_total * 1.5:
                indicators["volume_spike"] = True
                indicators["volume_spike_detail"] = f"OI activity {pre_total:,.0f} vs avg {avg_total:,.0f} ({pre_total/avg_total:.1f}x)"

    # --- Support/Resistance Break ---
    # Check if spot broke through a significant OI cluster
    if rally["start_timestamp"] in oi_by_ts:
        oi_data = oi_by_ts[rally["start_timestamp"]]
        # Find max CE OI strike (resistance) and max PE OI strike (support)
        max_ce_oi = 0
        max_ce_strike = 0
        max_pe_oi = 0
        max_pe_strike = 0
        for snap in oi_data:
            if snap["ce_oi"] > max_ce_oi:
                max_ce_oi = snap["ce_oi"]
                max_ce_strike = snap["strike_price"]
            if snap["pe_oi"] > max_pe_oi:
                max_pe_oi = snap["pe_oi"]
                max_pe_strike = snap["strike_price"]

        if rally["direction"] == "UP" and max_ce_strike > 0:
            # Up rally breaking through CE resistance
            if rally["start_price"] < max_ce_strike and rally["end_price"] > max_ce_strike:
                indicators["support_resistance_break"] = True
        elif rally["direction"] == "DOWN" and max_pe_strike > 0:
            # Down rally breaking through PE support
            if rally["start_price"] > max_pe_strike and rally["end_price"] < max_pe_strike:
                indicators["support_resistance_break"] = True

    return indicators


# ──────────────────────────────────────────────────────────────────────
# Main Analysis
# ──────────────────────────────────────────────────────────────────────

def run_analysis():
    conn = get_connection()

    print("=" * 80)
    print("  NIFTY RALLY ANALYSIS - Reverse Engineering Pre-Rally Signals")
    print("=" * 80)
    print()

    # Load data
    print("Loading spot price data...")
    days_data = load_spot_data(conn)
    print(f"  Loaded {len(days_data)} trading days")
    print(f"  Date range: {min(days_data.keys())} to {max(days_data.keys())}")
    print(f"  Total data points: {sum(len(v) for v in days_data.values())}")
    print()

    # ── Phase 1: Rally Discovery ──
    print("=" * 80)
    print("  PHASE 1: RALLY DISCOVERY")
    print("=" * 80)
    print()

    all_rallies = []

    for date_str in sorted(days_data.keys()):
        day_data = days_data[date_str]
        prices = [p["spot_price"] for p in day_data]
        day_range = max(prices) - min(prices)

        rallies = find_rallies(day_data)

        if rallies:
            # Load OI data for premium tracking
            print(f"  Loading OI data for {date_str}...")
            oi_by_ts = load_oi_snapshots_for_day(conn, date_str)

            for rally in rallies:
                rally["date"] = date_str
                rally["day_range"] = day_range

                # Track premiums
                premium_info = track_premium_for_rally(rally, oi_by_ts, day_data)
                rally.update(premium_info)

                # Analyze pre-rally indicators
                indicators = analyze_pre_rally_indicators(rally, day_data, oi_by_ts)
                rally["indicators"] = indicators

            all_rallies.extend(rallies)

    # Print all rallies
    up_rallies = [r for r in all_rallies if r["direction"] == "UP"]
    down_rallies = [r for r in all_rallies if r["direction"] == "DOWN"]

    print()
    print(f"  Total rallies found: {len(all_rallies)}")
    print(f"    UP rallies:   {len(up_rallies)}")
    print(f"    DOWN rallies: {len(down_rallies)}")
    print()

    print("-" * 120)
    print(f"  {'Date':<12} {'Dir':>4} {'Start':>8} {'End':>8} {'Mag':>7} {'Dur':>5} "
          f"{'Strike':>7} {'Prem Start':>11} {'Prem End':>9} {'Prem %':>8} {'Pre-Context':<15}")
    print("-" * 120)

    for r in sorted(all_rallies, key=lambda x: x["start_time"]):
        prem_start = f"{r['start_premium']:.1f}" if r.get("start_premium") else "N/A"
        prem_end = f"{r['end_premium']:.1f}" if r.get("end_premium") else "N/A"
        prem_pct = f"{r['premium_change_pct']:+.1f}%" if r.get("premium_change_pct") is not None else "N/A"

        print(f"  {r['date']:<12} {r['direction']:>4} "
              f"{r['start_time'].strftime('%H:%M'):>8} {r['end_time'].strftime('%H:%M'):>8} "
              f"{r['magnitude']:>7.1f} {r['duration_min']:>5.0f}m "
              f"{r.get('strike', 'N/A'):>7} {prem_start:>11} {prem_end:>9} {prem_pct:>8} "
              f"{r['pre_context']:<15}")

    print("-" * 120)

    # ── Phase 2: Statistics Report ──
    print()
    print("=" * 80)
    print("  PHASE 2: STATISTICS REPORT")
    print("=" * 80)
    print()

    # Overall stats
    print("  --- Overall Rally Statistics ---")
    print()

    for label, subset in [("ALL", all_rallies), ("UP", up_rallies), ("DOWN", down_rallies)]:
        if not subset:
            continue
        mags = [r["magnitude"] for r in subset]
        durs = [r["duration_min"] for r in subset]

        prem_pcts = [r["premium_change_pct"] for r in subset if r.get("premium_change_pct") is not None]
        prem_abs = [r["premium_change_abs"] for r in subset if r.get("premium_change_abs") is not None]

        print(f"  {label} Rallies ({len(subset)} total):")
        print(f"    Magnitude:  avg={sum(mags)/len(mags):.1f} pts, "
              f"median={sorted(mags)[len(mags)//2]:.1f} pts, "
              f"min={min(mags):.1f} pts, max={max(mags):.1f} pts")
        print(f"    Duration:   avg={sum(durs)/len(durs):.0f} min, "
              f"median={sorted(durs)[len(durs)//2]:.0f} min, "
              f"min={min(durs):.0f} min, max={max(durs):.0f} min")

        if prem_pcts:
            print(f"    Premium %:  avg={sum(prem_pcts)/len(prem_pcts):+.1f}%, "
                  f"median={sorted(prem_pcts)[len(prem_pcts)//2]:+.1f}%, "
                  f"min={min(prem_pcts):+.1f}%, max={max(prem_pcts):+.1f}%")
        if prem_abs:
            print(f"    Premium Rs: avg={sum(prem_abs)/len(prem_abs):+.1f}, "
                  f"median={sorted(prem_abs)[len(prem_abs)//2]:+.1f}, "
                  f"min={min(prem_abs):+.1f}, max={max(prem_abs):+.1f}")
            # At 65 qty (1 lot)
            avg_profit = sum(prem_abs) / len(prem_abs) * 65
            print(f"    P&L (65 qty): avg Rs {avg_profit:+,.0f} per rally")
        print()

    # Time-of-day distribution
    print("  --- Time-of-Day Distribution (Rally Start Times) ---")
    print()

    hour_bins = defaultdict(lambda: {"UP": 0, "DOWN": 0, "total": 0})
    for r in all_rallies:
        # 30-min bins
        t = r["start_time"]
        bin_label = f"{t.hour:02d}:{(t.minute // 30) * 30:02d}"
        hour_bins[bin_label][r["direction"]] += 1
        hour_bins[bin_label]["total"] += 1

    print(f"    {'Time Bin':<10} {'UP':>5} {'DOWN':>5} {'Total':>6} {'Bar'}")
    print(f"    {'-'*10} {'-'*5} {'-'*5} {'-'*6} {'-'*30}")
    for bin_label in sorted(hour_bins.keys()):
        d = hour_bins[bin_label]
        bar = "#" * d["total"]
        print(f"    {bin_label:<10} {d['UP']:>5} {d['DOWN']:>5} {d['total']:>6} {bar}")
    print()

    # Pre-rally context distribution
    print("  --- Pre-Rally Context (15 min before rally) ---")
    print()

    context_counts = defaultdict(lambda: {"UP": 0, "DOWN": 0, "total": 0})
    for r in all_rallies:
        ctx = r["pre_context"]
        context_counts[ctx][r["direction"]] += 1
        context_counts[ctx]["total"] += 1

    print(f"    {'Context':<20} {'UP':>5} {'DOWN':>5} {'Total':>6} {'% of All':>8}")
    print(f"    {'-'*20} {'-'*5} {'-'*5} {'-'*6} {'-'*8}")
    for ctx in sorted(context_counts.keys(), key=lambda x: -context_counts[x]["total"]):
        d = context_counts[ctx]
        pct = d["total"] / len(all_rallies) * 100
        print(f"    {ctx:<20} {d['UP']:>5} {d['DOWN']:>5} {d['total']:>6} {pct:>7.1f}%")
    print()

    # Day-wise rally count
    print("  --- Day-wise Rally Count ---")
    print()

    day_counts = defaultdict(lambda: {"UP": 0, "DOWN": 0, "total": 0, "day_range": 0})
    for r in all_rallies:
        day_counts[r["date"]][r["direction"]] += 1
        day_counts[r["date"]]["total"] += 1
        day_counts[r["date"]]["day_range"] = r["day_range"]

    print(f"    {'Date':<12} {'UP':>4} {'DOWN':>4} {'Total':>5} {'Day Range':>10}")
    print(f"    {'-'*12} {'-'*4} {'-'*4} {'-'*5} {'-'*10}")
    for date_str in sorted(day_counts.keys()):
        d = day_counts[date_str]
        print(f"    {date_str:<12} {d['UP']:>4} {d['DOWN']:>4} {d['total']:>5} {d['day_range']:>10.1f}")

    # Days with no rallies
    no_rally_days = [d for d in sorted(days_data.keys()) if d not in day_counts]
    print(f"\n    Days with no 30+ pt rallies: {len(no_rally_days)}")
    for d in no_rally_days:
        prices = [p["spot_price"] for p in days_data[d]]
        print(f"      {d}: range={max(prices)-min(prices):.1f} pts")
    print()

    # Indicator prevalence
    print("  --- Pre-Rally Indicator Prevalence ---")
    print()

    indicator_names = [
        ("oi_buildup", "OI Buildup (trapped traders)"),
        ("premium_squeeze", "Premium Squeeze"),
        ("volume_spike", "Volume Spike (OI activity)"),
        ("support_resistance_break", "S/R Level Break"),
    ]

    print(f"    {'Indicator':<35} {'UP':>5} {'DOWN':>5} {'Total':>6} {'% of All':>8}")
    print(f"    {'-'*35} {'-'*5} {'-'*5} {'-'*6} {'-'*8}")

    for key, label in indicator_names:
        up_count = sum(1 for r in up_rallies if r["indicators"].get(key))
        down_count = sum(1 for r in down_rallies if r["indicators"].get(key))
        total = up_count + down_count
        pct = total / len(all_rallies) * 100 if all_rallies else 0
        print(f"    {label:<35} {up_count:>5} {down_count:>5} {total:>6} {pct:>7.1f}%")

    # Combined indicators
    print()
    print(f"    {'Indicator Combinations':<35} {'Count':>6} {'% of All':>8}")
    print(f"    {'-'*35} {'-'*6} {'-'*8}")

    combo_counts = defaultdict(int)
    for r in all_rallies:
        ind = r["indicators"]
        combo = []
        if ind["oi_buildup"]:
            combo.append("OI")
        if ind["premium_squeeze"]:
            combo.append("Squeeze")
        if ind["volume_spike"]:
            combo.append("VolSpike")
        if ind["support_resistance_break"]:
            combo.append("S/R")

        key = " + ".join(combo) if combo else "None"
        combo_counts[key] += 1

    for combo in sorted(combo_counts.keys(), key=lambda x: -combo_counts[x]):
        count = combo_counts[combo]
        pct = count / len(all_rallies) * 100
        print(f"    {combo:<35} {count:>6} {pct:>7.1f}%")

    # VIX behavior before rallies
    print()
    print("  --- VIX Behavior Before Rallies ---")
    print()

    vix_up = [r["indicators"]["vix_change"] for r in up_rallies if r["indicators"]["vix_change"] is not None]
    vix_down = [r["indicators"]["vix_change"] for r in down_rallies if r["indicators"]["vix_change"] is not None]

    if vix_up:
        print(f"    Before UP rallies:   avg VIX change = {sum(vix_up)/len(vix_up):+.3f} "
              f"(n={len(vix_up)}, rising={sum(1 for v in vix_up if v > 0)}, falling={sum(1 for v in vix_up if v < 0)})")
    if vix_down:
        print(f"    Before DOWN rallies: avg VIX change = {sum(vix_down)/len(vix_down):+.3f} "
              f"(n={len(vix_down)}, rising={sum(1 for v in vix_down if v > 0)}, falling={sum(1 for v in vix_down if v < 0)})")

    # Futures basis behavior
    print()
    print("  --- Futures Basis Behavior Before Rallies ---")
    print()

    basis_up = [r["indicators"]["futures_basis_change"] for r in up_rallies if r["indicators"]["futures_basis_change"] is not None]
    basis_down = [r["indicators"]["futures_basis_change"] for r in down_rallies if r["indicators"]["futures_basis_change"] is not None]

    if basis_up:
        print(f"    Before UP rallies:   avg basis change = {sum(basis_up)/len(basis_up):+.2f} "
              f"(n={len(basis_up)}, widening={sum(1 for v in basis_up if v > 0)}, narrowing={sum(1 for v in basis_up if v < 0)})")
    if basis_down:
        print(f"    Before DOWN rallies: avg basis change = {sum(basis_down)/len(basis_down):+.2f} "
              f"(n={len(basis_down)}, widening={sum(1 for v in basis_down if v > 0)}, narrowing={sum(1 for v in basis_down if v < 0)})")

    # PCR behavior
    print()
    print("  --- PCR Behavior Before Rallies ---")
    print()

    pcr_up = [r["indicators"]["pcr_change"] for r in up_rallies if r["indicators"]["pcr_change"] is not None]
    pcr_down = [r["indicators"]["pcr_change"] for r in down_rallies if r["indicators"]["pcr_change"] is not None]

    if pcr_up:
        print(f"    Before UP rallies:   avg PCR change = {sum(pcr_up)/len(pcr_up):+.4f} "
              f"(n={len(pcr_up)}, rising={sum(1 for v in pcr_up if v > 0)}, falling={sum(1 for v in pcr_up if v < 0)})")
    if pcr_down:
        print(f"    Before DOWN rallies: avg PCR change = {sum(pcr_down)/len(pcr_down):+.4f} "
              f"(n={len(pcr_down)}, rising={sum(1 for v in pcr_down if v > 0)}, falling={sum(1 for v in pcr_down if v < 0)})")

    # ── Phase 3: Signal Pattern Analysis ──
    print()
    print("=" * 80)
    print("  PHASE 3: SIGNAL PATTERN ANALYSIS")
    print("=" * 80)
    print()

    print("  --- Detailed Pre-Rally Patterns (2-3 candles before each rally) ---")
    print()

    for r in sorted(all_rallies, key=lambda x: x["start_time"]):
        ind = r["indicators"]
        patterns = []
        if ind["oi_buildup"]:
            patterns.append(f"OI BUILDUP: {ind['oi_buildup_detail']}")
        if ind["premium_squeeze"]:
            patterns.append(f"PREMIUM SQUEEZE: {ind['premium_squeeze_detail']}")
        if ind["volume_spike"]:
            patterns.append(f"VOLUME SPIKE: {ind['volume_spike_detail']}")
        if ind["support_resistance_break"]:
            patterns.append("S/R LEVEL BREAK")
        if ind["vix_change"] is not None:
            vix_dir = "rising" if ind["vix_change"] > 0 else "falling"
            patterns.append(f"VIX {vix_dir} ({ind['vix_change']:+.3f})")
        if ind["futures_basis_change"] is not None:
            basis_dir = "widening" if ind["futures_basis_change"] > 0 else "narrowing"
            patterns.append(f"Basis {basis_dir} ({ind['futures_basis_change']:+.2f})")
        if ind["pcr_change"] is not None:
            pcr_dir = "rising" if ind["pcr_change"] > 0 else "falling"
            patterns.append(f"PCR {pcr_dir} ({ind['pcr_change']:+.4f})")

        arrow = "^" if r["direction"] == "UP" else "v"
        print(f"  [{r['date']} {r['start_time'].strftime('%H:%M')}] {arrow} {r['direction']} "
              f"+{r['magnitude']:.0f}pts in {r['duration_min']:.0f}min | "
              f"Pre-context: {r['pre_context']}")
        if patterns:
            for p in patterns:
                print(f"      {p}")
        else:
            print(f"      (no strong pre-signals detected)")
        print()

    # Pattern frequency table
    print("  --- Pattern Frequency Table ---")
    print()

    pattern_freq = defaultdict(lambda: {"UP": 0, "DOWN": 0, "total": 0})

    for r in all_rallies:
        ind = r["indicators"]
        d = r["direction"]

        if ind["oi_buildup"]:
            pattern_freq["OI Buildup (trapped traders)"][d] += 1
            pattern_freq["OI Buildup (trapped traders)"]["total"] += 1
        if ind["premium_squeeze"]:
            pattern_freq["Premium Squeeze"][d] += 1
            pattern_freq["Premium Squeeze"]["total"] += 1
        if ind["volume_spike"]:
            pattern_freq["Volume Spike"][d] += 1
            pattern_freq["Volume Spike"]["total"] += 1
        if ind["support_resistance_break"]:
            pattern_freq["S/R Level Break"][d] += 1
            pattern_freq["S/R Level Break"]["total"] += 1

        # VIX patterns
        if ind["vix_change"] is not None:
            if ind["vix_change"] > 0.05:
                pattern_freq["VIX Rising (>0.05)"][d] += 1
                pattern_freq["VIX Rising (>0.05)"]["total"] += 1
            elif ind["vix_change"] < -0.05:
                pattern_freq["VIX Falling (<-0.05)"][d] += 1
                pattern_freq["VIX Falling (<-0.05)"]["total"] += 1

        # Basis patterns
        if ind["futures_basis_change"] is not None:
            if ind["futures_basis_change"] > 2:
                pattern_freq["Basis Widening (>2pts)"][d] += 1
                pattern_freq["Basis Widening (>2pts)"]["total"] += 1
            elif ind["futures_basis_change"] < -2:
                pattern_freq["Basis Narrowing (<-2pts)"][d] += 1
                pattern_freq["Basis Narrowing (<-2pts)"]["total"] += 1

        # PCR patterns
        if ind["pcr_change"] is not None:
            if ind["pcr_change"] > 0.01:
                pattern_freq["PCR Rising (>0.01)"][d] += 1
                pattern_freq["PCR Rising (>0.01)"]["total"] += 1
            elif ind["pcr_change"] < -0.01:
                pattern_freq["PCR Falling (<-0.01)"][d] += 1
                pattern_freq["PCR Falling (<-0.01)"]["total"] += 1

        # Context patterns
        ctx = r["pre_context"]
        pattern_freq[f"Context: {ctx}"][d] += 1
        pattern_freq[f"Context: {ctx}"]["total"] += 1

    print(f"    {'Pattern':<40} {'UP':>5} {'DOWN':>5} {'Total':>6} {'% of Rallies':>12}")
    print(f"    {'-'*40} {'-'*5} {'-'*5} {'-'*6} {'-'*12}")
    for pat in sorted(pattern_freq.keys(), key=lambda x: -pattern_freq[x]["total"]):
        d = pattern_freq[pat]
        pct = d["total"] / len(all_rallies) * 100
        print(f"    {pat:<40} {d['UP']:>5} {d['DOWN']:>5} {d['total']:>6} {pct:>11.1f}%")

    # ── Directional predictiveness ──
    print()
    print("  --- Directional Predictiveness of Patterns ---")
    print("  (Which patterns are most skewed toward UP or DOWN?)")
    print()

    print(f"    {'Pattern':<40} {'UP%':>6} {'DOWN%':>6} {'Skew':>8} {'n':>4}")
    print(f"    {'-'*40} {'-'*6} {'-'*6} {'-'*8} {'-'*4}")

    for pat in sorted(pattern_freq.keys(), key=lambda x: -pattern_freq[x]["total"]):
        d = pattern_freq[pat]
        if d["total"] < 3:
            continue
        up_pct = d["UP"] / d["total"] * 100
        down_pct = d["DOWN"] / d["total"] * 100
        skew = up_pct - down_pct
        skew_label = f"UP+{skew:.0f}" if skew > 0 else f"DN+{-skew:.0f}"
        print(f"    {pat:<40} {up_pct:>5.0f}% {down_pct:>5.0f}% {skew_label:>8} {d['total']:>4}")

    # ── Premium Capture Summary ──
    print()
    print("=" * 80)
    print("  PREMIUM CAPTURE ANALYSIS")
    print("=" * 80)
    print()

    # Bucket by magnitude
    mag_buckets = {
        "30-50 pts": (30, 50),
        "50-80 pts": (50, 80),
        "80-120 pts": (80, 120),
        "120-200 pts": (120, 200),
        "200+ pts": (200, 99999),
    }

    print(f"    {'Magnitude':<15} {'Count':>6} {'Avg Prem %':>11} {'Avg Prem Rs':>12} "
          f"{'Avg P&L(65q)':>13} {'Avg Duration':>12}")
    print(f"    {'-'*15} {'-'*6} {'-'*11} {'-'*12} {'-'*13} {'-'*12}")

    for bucket_label, (lo, hi) in mag_buckets.items():
        bucket = [r for r in all_rallies
                  if lo <= r["magnitude"] < hi and r.get("premium_change_abs") is not None]
        if not bucket:
            print(f"    {bucket_label:<15} {0:>6}")
            continue

        avg_prem_pct = sum(r["premium_change_pct"] for r in bucket) / len(bucket)
        avg_prem_abs = sum(r["premium_change_abs"] for r in bucket) / len(bucket)
        avg_pnl = avg_prem_abs * 65
        avg_dur = sum(r["duration_min"] for r in bucket) / len(bucket)

        print(f"    {bucket_label:<15} {len(bucket):>6} {avg_prem_pct:>+10.1f}% "
              f"{avg_prem_abs:>+11.1f} {avg_pnl:>+12,.0f} {avg_dur:>11.0f} min")

    # Top 10 most profitable rallies
    print()
    print("  --- Top 15 Most Profitable Rallies (by premium Rs capture) ---")
    print()

    profitable = sorted(
        [r for r in all_rallies if r.get("premium_change_abs") is not None],
        key=lambda x: x["premium_change_abs"],
        reverse=True
    )[:15]

    print(f"    {'Date':<12} {'Dir':>4} {'Time':>12} {'Mag':>7} {'Dur':>5} "
          f"{'Strike':>7} {'Prem':>12} {'P&L(65q)':>10} {'Context':<15}")
    print(f"    {'-'*12} {'-'*4} {'-'*12} {'-'*7} {'-'*5} {'-'*7} {'-'*12} {'-'*10} {'-'*15}")

    for r in profitable:
        prem = f"{r['start_premium']:.0f}->{r['end_premium']:.0f}" if r.get("start_premium") else "N/A"
        pnl = r["premium_change_abs"] * 65
        print(f"    {r['date']:<12} {r['direction']:>4} "
              f"{r['start_time'].strftime('%H:%M')}-{r['end_time'].strftime('%H:%M'):>5} "
              f"{r['magnitude']:>7.0f} {r['duration_min']:>4.0f}m "
              f"{r.get('strike', 'N/A'):>7} {prem:>12} {pnl:>+9,.0f} {r['pre_context']:<15}")

    # ── Summary & Strategy Insights ──
    print()
    print("=" * 80)
    print("  STRATEGY INSIGHTS & KEY FINDINGS")
    print("=" * 80)
    print()

    # Calculate key stats for summary
    rallies_with_premium = [r for r in all_rallies if r.get("premium_change_abs") is not None]
    profitable_rallies = [r for r in rallies_with_premium if r["premium_change_abs"] > 0]

    if rallies_with_premium:
        win_rate = len(profitable_rallies) / len(rallies_with_premium) * 100
        avg_capture = sum(r["premium_change_abs"] for r in rallies_with_premium) / len(rallies_with_premium)
        total_capture = sum(r["premium_change_abs"] for r in rallies_with_premium)

        print(f"  1. RALLY FREQUENCY: {len(all_rallies)} rallies across {len(days_data)} days "
              f"= {len(all_rallies)/len(days_data):.1f} rallies/day")
        print(f"  2. PREMIUM CAPTURE: {len(rallies_with_premium)} rallies with premium data, "
              f"{win_rate:.0f}% had positive premium change")
        print(f"  3. AVERAGE CAPTURE: Rs {avg_capture:+.1f} per rally ({avg_capture*65:+,.0f} at 65 qty)")
        print(f"  4. TOTAL OPPORTUNITY: Rs {total_capture:+.1f} per lot across all rallies "
              f"({total_capture*65:+,.0f} at 65 qty)")

    # Best time to trade
    if hour_bins:
        best_bin = max(hour_bins.items(), key=lambda x: x[1]["total"])
        print(f"  5. BEST TIME: {best_bin[0]} IST has most rally starts ({best_bin[1]['total']} rallies)")

    # Most predictive indicator
    if pattern_freq:
        # Among indicator patterns (not context), find highest frequency
        indicator_pats = {k: v for k, v in pattern_freq.items() if not k.startswith("Context:")}
        if indicator_pats:
            best_pat = max(indicator_pats.items(), key=lambda x: x[1]["total"])
            print(f"  6. TOP SIGNAL: '{best_pat[0]}' preceded {best_pat[1]['total']} rallies "
                  f"({best_pat[1]['total']/len(all_rallies)*100:.0f}%)")

    # Rallies with at least one pre-signal
    signaled = sum(1 for r in all_rallies if any([
        r["indicators"]["oi_buildup"],
        r["indicators"]["premium_squeeze"],
        r["indicators"]["volume_spike"],
        r["indicators"]["support_resistance_break"],
    ]))
    print(f"  7. SIGNALED RALLIES: {signaled}/{len(all_rallies)} ({signaled/len(all_rallies)*100:.0f}%) "
          f"had at least one pre-signal")

    # Average magnitude by direction
    if up_rallies:
        avg_up = sum(r["magnitude"] for r in up_rallies) / len(up_rallies)
        print(f"  8. UP RALLY AVG: {avg_up:.0f} pts over "
              f"{sum(r['duration_min'] for r in up_rallies)/len(up_rallies):.0f} min")
    if down_rallies:
        avg_down = sum(r["magnitude"] for r in down_rallies) / len(down_rallies)
        print(f"  9. DOWN RALLY AVG: {avg_down:.0f} pts over "
              f"{sum(r['duration_min'] for r in down_rallies)/len(down_rallies):.0f} min")

    print()
    print("=" * 80)
    print("  Analysis complete.")
    print("=" * 80)

    conn.close()


if __name__ == "__main__":
    run_analysis()
