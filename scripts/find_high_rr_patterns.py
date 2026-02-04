"""
Deep dive into High RR setups to find common technical patterns.
Filters out pyramided trades to get unique setups.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"
LOT_SIZE = 65
COOLDOWN_MINUTES = 12  # Match production - no pyramiding


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_snapshots_for_premium_tracking(cursor, start_time: str, end_time: str, strike: int):
    cursor.execute("""
        SELECT timestamp, ce_ltp, pe_ltp, spot_price
        FROM oi_snapshots
        WHERE timestamp >= ? AND timestamp <= ?
          AND strike_price = ?
        ORDER BY timestamp ASC
    """, (start_time, end_time, strike))
    return [dict(row) for row in cursor.fetchall()]


def analyze_trade(cursor, analysis: dict, timestamp: str):
    """Analyze a single trade setup for max RR potential."""
    trade_setup = analysis.get("trade_setup", {})
    if not trade_setup:
        return None

    strike = trade_setup.get("strike", 0)
    entry_premium = trade_setup.get("entry_premium", 0)
    sl_premium = trade_setup.get("sl_premium", 0)
    option_type = trade_setup.get("option_type", "CE")

    if not strike or entry_premium <= 0 or sl_premium <= 0:
        return None

    risk = entry_premium - sl_premium
    if risk <= 0:
        return None

    # Look ahead 3 hours
    try:
        start_dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return None

    end_dt = start_dt + timedelta(hours=3)

    snapshots = get_snapshots_for_premium_tracking(
        cursor, timestamp, end_dt.isoformat(), strike
    )

    if not snapshots:
        return None

    ltp_key = "ce_ltp" if option_type == "CE" else "pe_ltp"

    max_premium = entry_premium
    max_time = timestamp
    min_premium = entry_premium
    hit_sl = False
    sl_time = None

    for snap in snapshots[1:]:
        current = snap.get(ltp_key, 0)
        if current <= 0:
            continue

        if current < min_premium:
            min_premium = current

        if current <= sl_premium and not hit_sl:
            hit_sl = True
            sl_time = snap.get("timestamp", "")

        if current > max_premium:
            max_premium = current
            max_time = snap.get("timestamp", "")

    max_rr = (max_premium - entry_premium) / risk
    drawdown_pct = ((entry_premium - min_premium) / entry_premium * 100) if entry_premium > 0 else 0

    return {
        "max_premium": max_premium,
        "max_time": max_time,
        "max_rr": max_rr,
        "min_premium": min_premium,
        "drawdown_pct": drawdown_pct,
        "hit_sl_first": hit_sl,
        "sl_time": sl_time,
    }


def main():
    conn = get_connection()
    cursor = conn.cursor()

    # Fetch all analysis records
    cursor.execute("""
        SELECT id, timestamp, spot_price, analysis_json
        FROM analysis_history
        WHERE analysis_json IS NOT NULL
        ORDER BY timestamp ASC
    """)

    rows = cursor.fetchall()
    print(f"Analyzing {len(rows)} records...")

    all_setups = []

    for row in rows:
        try:
            analysis = json.loads(row["analysis_json"])
        except:
            continue

        trade_setup = analysis.get("trade_setup", {})
        if not trade_setup:
            continue

        move_data = analyze_trade(cursor, analysis, row["timestamp"])
        if not move_data:
            continue

        # Extract all technical factors
        entry_premium = trade_setup.get("entry_premium", 0)
        sl_premium = trade_setup.get("sl_premium", 0)
        risk = entry_premium - sl_premium

        # OI data
        call_oi_change = analysis.get("call_oi_change", 0)
        put_oi_change = analysis.get("put_oi_change", 0)
        total_oi_change = call_oi_change + put_oi_change
        oi_ratio = (put_oi_change / call_oi_change) if call_oi_change != 0 else 0

        # Spot vs Max Pain
        spot = row["spot_price"]
        max_pain = analysis.get("max_pain", 0)
        spot_vs_mp = spot - max_pain if max_pain else 0
        spot_vs_mp_pct = (spot_vs_mp / max_pain * 100) if max_pain else 0

        # Premium momentum
        pm = analysis.get("premium_momentum", {})
        pm_score = pm.get("premium_momentum_score", 0) if isinstance(pm, dict) else 0

        # IV skew
        iv_skew = analysis.get("iv_skew", {})
        skew_score = iv_skew.get("skew_score", 0) if isinstance(iv_skew, dict) else 0

        # PCR
        pcr = analysis.get("pcr", 0)

        setup_info = {
            "timestamp": row["timestamp"],
            "date": row["timestamp"][:10],
            "time": row["timestamp"][11:19],
            "hour": int(row["timestamp"][11:13]),
            "spot": spot,
            "direction": trade_setup.get("direction", ""),
            "strike": trade_setup.get("strike", 0),
            "option_type": trade_setup.get("option_type", ""),
            "moneyness": trade_setup.get("moneyness", ""),
            "entry_premium": entry_premium,
            "sl_premium": sl_premium,
            "risk": risk,
            "risk_pct": trade_setup.get("risk_pct", 0),
            "target1": trade_setup.get("target1_premium", 0),
            # Technical factors
            "call_oi_change": call_oi_change,
            "put_oi_change": put_oi_change,
            "total_oi_change": total_oi_change,
            "oi_ratio": oi_ratio,  # Put/Call OI change ratio
            "spot_vs_mp": spot_vs_mp,
            "spot_vs_mp_pct": spot_vs_mp_pct,
            "pm_score": pm_score,
            "iv_skew": skew_score,
            "pcr": pcr,
            "max_pain": max_pain,
            "verdict": analysis.get("verdict", ""),
            "confidence": analysis.get("signal_confidence", 0),
            "confirmation": analysis.get("confirmation_status", ""),
            "regime": analysis.get("market_regime", {}).get("regime", ""),
            # Move data
            **move_data,
        }

        all_setups.append(setup_info)

    conn.close()

    # Filter to high RR setups (1:2+)
    high_rr_all = [s for s in all_setups if s["max_rr"] >= 2.0]
    print(f"Total setups with 1:2+ max RR: {len(high_rr_all)}")

    # Remove pyramided trades - only keep first trade in each active period
    # Sort by timestamp and filter out overlapping trades
    unique_high_rr = []
    last_exit_time = None

    for setup in sorted(high_rr_all, key=lambda x: x["timestamp"]):
        entry_dt = datetime.fromisoformat(setup["timestamp"])

        # Check if previous trade is still active or in cooldown
        if last_exit_time:
            try:
                exit_dt = datetime.fromisoformat(last_exit_time)
                cooldown_end = exit_dt + timedelta(minutes=COOLDOWN_MINUTES)
                if entry_dt < cooldown_end:
                    continue  # Skip - overlapping or in cooldown
            except:
                pass

        unique_high_rr.append(setup)
        last_exit_time = setup["max_time"]  # Use max time as proxy for exit

    print(f"Unique high RR setups (no pyramiding): {len(unique_high_rr)}")

    # ============================================================
    # ANALYSIS: Find common patterns
    # ============================================================

    print("\n" + "=" * 120)
    print("UNIQUE HIGH RR SETUPS - DETAILED LIST")
    print("=" * 120)

    print(f"\n{'#':>2} | {'Date':<10} | {'Time':<8} | {'Dir':<9} | {'Strike':<6} | "
          f"{'Entry':>7} | {'Max':>7} | {'RR':>5} | {'OI Ratio':>8} | {'PM':>6} | {'Spot-MP':>8} | {'Conf':>4}")
    print("-" * 120)

    for i, t in enumerate(unique_high_rr, 1):
        print(f"{i:>2} | {t['date']:<10} | {t['time']:<8} | {t['direction']:<9} | {t['strike']:<6} | "
              f"{t['entry_premium']:>7.2f} | {t['max_premium']:>7.2f} | {t['max_rr']:>5.2f} | "
              f"{t['oi_ratio']:>8.2f} | {t['pm_score']:>6.1f} | {t['spot_vs_mp']:>+8.0f} | {t['confirmation'][:4]:>4}")

    # ============================================================
    # PATTERN ANALYSIS
    # ============================================================

    print("\n" + "=" * 120)
    print("PATTERN ANALYSIS - COMMON FACTORS IN HIGH RR SETUPS")
    print("=" * 120)

    # 1. Time of Day Analysis
    print("\n--- TIME OF DAY ---")
    hour_dist = defaultdict(list)
    for t in unique_high_rr:
        hour_dist[t["hour"]].append(t)

    print(f"{'Hour':<6} | {'Count':>5} | {'Avg RR':>7} | {'Avg PM':>7}")
    print("-" * 40)
    for hour in sorted(hour_dist.keys()):
        trades = hour_dist[hour]
        avg_rr = sum(t["max_rr"] for t in trades) / len(trades)
        avg_pm = sum(t["pm_score"] for t in trades) / len(trades)
        print(f"{hour:02d}:00  | {len(trades):>5} | {avg_rr:>7.2f} | {avg_pm:>+7.1f}")

    # 2. Premium Momentum Analysis
    print("\n--- PREMIUM MOMENTUM AT ENTRY ---")
    pm_buckets = {
        "Very Negative (<-50)": [t for t in unique_high_rr if t["pm_score"] < -50],
        "Negative (-50 to -10)": [t for t in unique_high_rr if -50 <= t["pm_score"] < -10],
        "Neutral (-10 to +10)": [t for t in unique_high_rr if -10 <= t["pm_score"] <= 10],
        "Positive (+10 to +50)": [t for t in unique_high_rr if 10 < t["pm_score"] <= 50],
        "Very Positive (>+50)": [t for t in unique_high_rr if t["pm_score"] > 50],
    }

    print(f"{'PM Range':<25} | {'Count':>5} | {'Avg RR':>7} | {'% of Total':>10}")
    print("-" * 55)
    for bucket, trades in pm_buckets.items():
        if trades:
            avg_rr = sum(t["max_rr"] for t in trades) / len(trades)
            pct = len(trades) / len(unique_high_rr) * 100
            print(f"{bucket:<25} | {len(trades):>5} | {avg_rr:>7.2f} | {pct:>9.0f}%")

    # 3. OI Ratio Analysis (Put OI Change / Call OI Change)
    print("\n--- OI RATIO (Put/Call OI Change) ---")
    oi_buckets = {
        "Put Heavy (>1.5)": [t for t in unique_high_rr if t["oi_ratio"] > 1.5],
        "Balanced (0.8-1.5)": [t for t in unique_high_rr if 0.8 <= t["oi_ratio"] <= 1.5],
        "Call Heavy (<0.8)": [t for t in unique_high_rr if t["oi_ratio"] < 0.8],
    }

    print(f"{'OI Ratio':<20} | {'Count':>5} | {'Avg RR':>7} | {'% of Total':>10}")
    print("-" * 50)
    for bucket, trades in oi_buckets.items():
        if trades:
            avg_rr = sum(t["max_rr"] for t in trades) / len(trades)
            pct = len(trades) / len(unique_high_rr) * 100
            print(f"{bucket:<20} | {len(trades):>5} | {avg_rr:>7.2f} | {pct:>9.0f}%")

    # 4. Spot vs Max Pain
    print("\n--- SPOT vs MAX PAIN ---")
    mp_buckets = {
        "Far Below MP (<-50)": [t for t in unique_high_rr if t["spot_vs_mp"] < -50],
        "Below MP (-50 to 0)": [t for t in unique_high_rr if -50 <= t["spot_vs_mp"] < 0],
        "At/Near MP (0 to +50)": [t for t in unique_high_rr if 0 <= t["spot_vs_mp"] <= 50],
        "Above MP (>+50)": [t for t in unique_high_rr if t["spot_vs_mp"] > 50],
    }

    print(f"{'Spot vs MP':<25} | {'Count':>5} | {'Avg RR':>7} | {'% of Total':>10}")
    print("-" * 55)
    for bucket, trades in mp_buckets.items():
        if trades:
            avg_rr = sum(t["max_rr"] for t in trades) / len(trades)
            pct = len(trades) / len(unique_high_rr) * 100
            print(f"{bucket:<25} | {len(trades):>5} | {avg_rr:>7.2f} | {pct:>9.0f}%")

    # 5. Confirmation Status
    print("\n--- CONFIRMATION STATUS ---")
    conf_dist = defaultdict(list)
    for t in unique_high_rr:
        conf_dist[t["confirmation"]].append(t)

    print(f"{'Status':<15} | {'Count':>5} | {'Avg RR':>7} | {'% of Total':>10}")
    print("-" * 45)
    for status, trades in sorted(conf_dist.items(), key=lambda x: -len(x[1])):
        avg_rr = sum(t["max_rr"] for t in trades) / len(trades)
        pct = len(trades) / len(unique_high_rr) * 100
        print(f"{status:<15} | {len(trades):>5} | {avg_rr:>7.2f} | {pct:>9.0f}%")

    # 6. Confidence Level
    print("\n--- SIGNAL CONFIDENCE ---")
    conf_buckets = {
        "Low (<50%)": [t for t in unique_high_rr if t["confidence"] < 50],
        "Medium (50-70%)": [t for t in unique_high_rr if 50 <= t["confidence"] < 70],
        "High (70-85%)": [t for t in unique_high_rr if 70 <= t["confidence"] <= 85],
        "Very High (>85%)": [t for t in unique_high_rr if t["confidence"] > 85],
    }

    print(f"{'Confidence':<20} | {'Count':>5} | {'Avg RR':>7} | {'% of Total':>10}")
    print("-" * 50)
    for bucket, trades in conf_buckets.items():
        if trades:
            avg_rr = sum(t["max_rr"] for t in trades) / len(trades)
            pct = len(trades) / len(unique_high_rr) * 100
            print(f"{bucket:<20} | {len(trades):>5} | {avg_rr:>7.2f} | {pct:>9.0f}%")

    # 7. Hit SL first then recovered?
    print("\n--- HIT SL BEFORE MAX? ---")
    sl_first = [t for t in unique_high_rr if t["hit_sl_first"]]
    no_sl = [t for t in unique_high_rr if not t["hit_sl_first"]]

    print(f"{'Pattern':<20} | {'Count':>5} | {'Avg RR':>7} | {'% of Total':>10}")
    print("-" * 50)
    if sl_first:
        avg_rr = sum(t["max_rr"] for t in sl_first) / len(sl_first)
        print(f"{'Hit SL then recovered':<20} | {len(sl_first):>5} | {avg_rr:>7.2f} | {len(sl_first)/len(unique_high_rr)*100:>9.0f}%")
    if no_sl:
        avg_rr = sum(t["max_rr"] for t in no_sl) / len(no_sl)
        print(f"{'Never hit SL':<20} | {len(no_sl):>5} | {avg_rr:>7.2f} | {len(no_sl)/len(unique_high_rr)*100:>9.0f}%")

    # ============================================================
    # FIND COMMON CLUSTERS
    # ============================================================

    print("\n" + "=" * 120)
    print("IDENTIFYING COMMON PATTERNS / CLUSTERS")
    print("=" * 120)

    # Look for setups that share multiple characteristics
    patterns = []

    for t in unique_high_rr:
        pattern_tags = []

        # Time pattern
        if 9 <= t["hour"] <= 10:
            pattern_tags.append("MORNING")
        elif 13 <= t["hour"] <= 15:
            pattern_tags.append("AFTERNOON")

        # PM pattern
        if t["pm_score"] < -10:
            pattern_tags.append("PM_NEG")
        elif t["pm_score"] > 50:
            pattern_tags.append("PM_HIGH")

        # OI pattern
        if t["oi_ratio"] > 1.5:
            pattern_tags.append("PUT_HEAVY")
        elif t["oi_ratio"] < 0.8:
            pattern_tags.append("CALL_HEAVY")

        # Spot vs MP
        if t["spot_vs_mp"] < -30:
            pattern_tags.append("BELOW_MP")
        elif t["spot_vs_mp"] > 30:
            pattern_tags.append("ABOVE_MP")

        # Confirmation
        if t["confirmation"] == "CONFIRMED":
            pattern_tags.append("CONFIRMED")

        # SL pattern
        if t["hit_sl_first"]:
            pattern_tags.append("SL_RECOVERY")

        t["pattern_tags"] = pattern_tags
        patterns.append(t)

    # Count pattern combinations
    pattern_combos = defaultdict(list)
    for t in patterns:
        key = tuple(sorted(t["pattern_tags"]))
        pattern_combos[key].append(t)

    # Sort by frequency
    sorted_patterns = sorted(pattern_combos.items(), key=lambda x: -len(x[1]))

    print("\nMost common pattern combinations:")
    print(f"{'Pattern Tags':<50} | {'Count':>5} | {'Avg RR':>7}")
    print("-" * 70)

    for tags, trades in sorted_patterns[:15]:
        if len(trades) >= 1:
            tag_str = " + ".join(tags) if tags else "(no strong pattern)"
            avg_rr = sum(t["max_rr"] for t in trades) / len(trades)
            print(f"{tag_str:<50} | {len(trades):>5} | {avg_rr:>7.2f}")

    # ============================================================
    # DETAILED BREAKDOWN OF TOP PATTERNS
    # ============================================================

    print("\n" + "=" * 120)
    print("TOP PATTERN DETAILS")
    print("=" * 120)

    # Find the most promising pattern
    for tags, trades in sorted_patterns[:5]:
        if len(trades) >= 2:
            tag_str = " + ".join(tags) if tags else "(no strong pattern)"
            print(f"\n--- PATTERN: {tag_str} ({len(trades)} trades) ---")

            avg_rr = sum(t["max_rr"] for t in trades) / len(trades)
            avg_pm = sum(t["pm_score"] for t in trades) / len(trades)
            avg_conf = sum(t["confidence"] for t in trades) / len(trades)
            avg_oi_ratio = sum(t["oi_ratio"] for t in trades) / len(trades)

            print(f"Avg RR: {avg_rr:.2f} | Avg PM: {avg_pm:+.1f} | Avg Conf: {avg_conf:.0f}% | Avg OI Ratio: {avg_oi_ratio:.2f}")

            print(f"\n{'Date':<10} | {'Time':<8} | {'Entry':>7} | {'Max':>7} | {'RR':>5} | {'PM':>6} | {'Verdict':<20}")
            print("-" * 85)
            for t in trades:
                print(f"{t['date']:<10} | {t['time']:<8} | {t['entry_premium']:>7.2f} | "
                      f"{t['max_premium']:>7.2f} | {t['max_rr']:>5.2f} | {t['pm_score']:>+6.1f} | {t['verdict']:<20}")

    # ============================================================
    # SUMMARY: What to watch for
    # ============================================================

    print("\n" + "=" * 120)
    print("SUMMARY: INDICATORS TO WATCH FOR HIGH RR SETUPS")
    print("=" * 120)

    # Calculate stats
    avg_pm = sum(t["pm_score"] for t in unique_high_rr) / len(unique_high_rr)
    avg_oi_ratio = sum(t["oi_ratio"] for t in unique_high_rr) / len(unique_high_rr)
    avg_spot_vs_mp = sum(t["spot_vs_mp"] for t in unique_high_rr) / len(unique_high_rr)
    pct_afternoon = sum(1 for t in unique_high_rr if t["hour"] >= 13) / len(unique_high_rr) * 100

    print(f"""
Based on {len(unique_high_rr)} unique high RR setups (no pyramiding):

1. PREMIUM MOMENTUM: Avg = {avg_pm:+.1f}
   - Negative PM (buying at dip) tends to produce higher RR

2. OI RATIO (Put/Call): Avg = {avg_oi_ratio:.2f}
   - Higher ratio = more put OI being added = bullish for calls

3. SPOT vs MAX PAIN: Avg = {avg_spot_vs_mp:+.0f} points
   - Most setups formed with spot near or above max pain

4. TIME OF DAY:
   - {pct_afternoon:.0f}% of high RR setups occurred in afternoon session (13:00+)

5. CONFIRMATION STATUS:
   - Only {sum(1 for t in unique_high_rr if t['confirmation'] == 'CONFIRMED')}/{len(unique_high_rr)} ({sum(1 for t in unique_high_rr if t['confirmation'] == 'CONFIRMED')/len(unique_high_rr)*100:.0f}%) were CONFIRMED
   - Don't over-rely on confirmation for high RR potential

6. SL RECOVERY:
   - {sum(1 for t in unique_high_rr if t['hit_sl_first'])}/{len(unique_high_rr)} ({sum(1 for t in unique_high_rr if t['hit_sl_first'])/len(unique_high_rr)*100:.0f}%) hit SL first then recovered
   - Consider wider SL or re-entry strategy
""")


if __name__ == "__main__":
    main()
