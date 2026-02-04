"""
Analyze Entry Timing for High RR Setups

Goal: Find what technical conditions existed at the BOTTOM of the drawdown
(the optimal entry point) vs at the original entry (too early).

If we can identify when the dip is OVER, we can enter at a better price
with tighter SL and higher probability of success.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"
LOT_SIZE = 65
COOLDOWN_MINUTES = 12


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_price_snapshots(cursor, start_time: str, end_time: str, strike: int):
    """Get price snapshots for a strike."""
    cursor.execute("""
        SELECT timestamp, ce_ltp, pe_ltp, spot_price
        FROM oi_snapshots
        WHERE timestamp >= ? AND timestamp <= ?
          AND strike_price = ?
        ORDER BY timestamp ASC
    """, (start_time, end_time, strike))
    return [dict(row) for row in cursor.fetchall()]


def get_analysis_at_time(cursor, target_time: str):
    """Get the analysis record closest to a given time."""
    cursor.execute("""
        SELECT timestamp, analysis_json
        FROM analysis_history
        WHERE timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (target_time,))
    row = cursor.fetchone()
    if row:
        try:
            return json.loads(row["analysis_json"])
        except:
            pass
    return None


def main():
    conn = get_connection()
    cursor = conn.cursor()

    # Get all analysis records
    cursor.execute("""
        SELECT id, timestamp, spot_price, analysis_json
        FROM analysis_history
        WHERE analysis_json IS NOT NULL
        ORDER BY timestamp ASC
    """)
    rows = cursor.fetchall()

    print("=" * 120)
    print("ENTRY TIMING ANALYSIS")
    print("Goal: Find better entry points for high RR setups")
    print("=" * 120)

    # Find unique high RR setups (no pyramiding)
    high_rr_setups = []
    last_exit_time = None

    for row in rows:
        try:
            analysis = json.loads(row["analysis_json"])
        except:
            continue

        trade_setup = analysis.get("trade_setup", {})
        if not trade_setup:
            continue

        strike = trade_setup.get("strike", 0)
        entry_premium = trade_setup.get("entry_premium", 0)
        sl_premium = trade_setup.get("sl_premium", 0)
        option_type = trade_setup.get("option_type", "CE")

        if not strike or entry_premium <= 0 or sl_premium <= 0:
            continue

        risk = entry_premium - sl_premium
        if risk <= 0:
            continue

        # Check pyramiding
        entry_dt = datetime.fromisoformat(row["timestamp"])
        if last_exit_time:
            try:
                exit_dt = datetime.fromisoformat(last_exit_time)
                cooldown_end = exit_dt + timedelta(minutes=COOLDOWN_MINUTES)
                if entry_dt < cooldown_end:
                    continue
            except:
                pass

        # Get future price data
        end_dt = entry_dt + timedelta(hours=3)
        snapshots = get_price_snapshots(cursor, row["timestamp"], end_dt.isoformat(), strike)

        if not snapshots:
            continue

        ltp_key = "ce_ltp" if option_type == "CE" else "pe_ltp"

        # Track price journey
        max_premium = entry_premium
        max_time = row["timestamp"]
        min_premium = entry_premium
        min_time = row["timestamp"]
        hit_sl = False

        for snap in snapshots[1:]:
            current = snap.get(ltp_key, 0)
            if current <= 0:
                continue

            if current < min_premium:
                min_premium = current
                min_time = snap["timestamp"]

            if current <= sl_premium and not hit_sl:
                hit_sl = True

            if current > max_premium:
                max_premium = current
                max_time = snap["timestamp"]

        max_rr = (max_premium - entry_premium) / risk

        if max_rr >= 2.0:
            high_rr_setups.append({
                "timestamp": row["timestamp"],
                "analysis": analysis,
                "strike": strike,
                "option_type": option_type,
                "entry_premium": entry_premium,
                "sl_premium": sl_premium,
                "risk": risk,
                "min_premium": min_premium,
                "min_time": min_time,
                "max_premium": max_premium,
                "max_time": max_time,
                "max_rr": max_rr,
                "hit_sl": hit_sl,
            })
            last_exit_time = max_time

    print(f"\nFound {len(high_rr_setups)} unique high RR setups")

    # Analyze each setup
    print("\n" + "=" * 120)
    print("DETAILED ENTRY TIMING ANALYSIS")
    print("=" * 120)

    better_entries = []

    for i, setup in enumerate(high_rr_setups, 1):
        print(f"\n{'='*120}")
        print(f"SETUP #{i}: {setup['timestamp'][:19]}")
        print(f"{'='*120}")

        entry_time = setup["timestamp"]
        min_time = setup["min_time"]
        max_time = setup["max_time"]

        entry_premium = setup["entry_premium"]
        min_premium = setup["min_premium"]
        max_premium = setup["max_premium"]
        sl_premium = setup["sl_premium"]
        risk = setup["risk"]

        drawdown = entry_premium - min_premium
        drawdown_pct = (drawdown / entry_premium * 100) if entry_premium > 0 else 0

        # Calculate what RR would be if we entered at the bottom
        optimal_entry = min_premium
        optimal_sl = optimal_entry * 0.85  # 15% SL from optimal entry
        optimal_risk = optimal_entry - optimal_sl
        optimal_rr = (max_premium - optimal_entry) / optimal_risk if optimal_risk > 0 else 0

        print(f"""
ORIGINAL ENTRY (our algo):
  Time: {entry_time[11:19]}
  Entry Premium: {entry_premium:.2f}
  SL: {sl_premium:.2f} (Risk: {risk:.2f})
  Hit SL? {'YES' if setup['hit_sl'] else 'NO'}

PRICE JOURNEY:
  Min Premium: {min_premium:.2f} @ {min_time[11:19]} (Drawdown: {drawdown:.2f} / {drawdown_pct:.1f}%)
  Max Premium: {max_premium:.2f} @ {max_time[11:19]}
  Original RR: {setup['max_rr']:.2f}

OPTIMAL ENTRY (at the bottom):
  Time: {min_time[11:19]}
  Entry Premium: {optimal_entry:.2f}
  SL (15%): {optimal_sl:.2f} (Risk: {optimal_risk:.2f})
  RR from optimal: {optimal_rr:.2f}
""")

        # Get technical conditions at original entry
        orig_analysis = setup["analysis"]
        orig_pm = orig_analysis.get("premium_momentum", {}).get("premium_momentum_score", 0)
        orig_conf = orig_analysis.get("signal_confidence", 0)
        orig_status = orig_analysis.get("confirmation_status", "")
        orig_verdict = orig_analysis.get("verdict", "")
        orig_call_oi = orig_analysis.get("call_oi_change", 0)
        orig_put_oi = orig_analysis.get("put_oi_change", 0)

        print(f"""TECHNICALS AT ORIGINAL ENTRY ({entry_time[11:19]}):
  Verdict: {orig_verdict}
  Confidence: {orig_conf:.0f}%
  Confirmation: {orig_status}
  Premium Momentum: {orig_pm:+.1f}
  Call OI Change: {orig_call_oi:,.0f}
  Put OI Change: {orig_put_oi:,.0f}
""")

        # Get technical conditions at optimal entry (bottom)
        optimal_analysis = get_analysis_at_time(cursor, min_time)
        if optimal_analysis:
            opt_pm = optimal_analysis.get("premium_momentum", {}).get("premium_momentum_score", 0)
            opt_conf = optimal_analysis.get("signal_confidence", 0)
            opt_status = optimal_analysis.get("confirmation_status", "")
            opt_verdict = optimal_analysis.get("verdict", "")
            opt_call_oi = optimal_analysis.get("call_oi_change", 0)
            opt_put_oi = optimal_analysis.get("put_oi_change", 0)

            print(f"""TECHNICALS AT OPTIMAL ENTRY / BOTTOM ({min_time[11:19]}):
  Verdict: {opt_verdict}
  Confidence: {opt_conf:.0f}%
  Confirmation: {opt_status}
  Premium Momentum: {opt_pm:+.1f}
  Call OI Change: {opt_call_oi:,.0f}
  Put OI Change: {opt_put_oi:,.0f}
""")

            # Calculate changes
            pm_change = opt_pm - orig_pm
            conf_change = opt_conf - orig_conf

            print(f"""CHANGES FROM ORIGINAL TO OPTIMAL:
  PM Change: {orig_pm:+.1f} -> {opt_pm:+.1f} (delta: {pm_change:+.1f})
  Confidence Change: {orig_conf:.0f}% -> {opt_conf:.0f}% (delta: {conf_change:+.0f}%)
  Status Change: {orig_status} -> {opt_status}
""")

            better_entries.append({
                "setup_num": i,
                "original_time": entry_time[11:19],
                "optimal_time": min_time[11:19],
                "drawdown_pct": drawdown_pct,
                "original_rr": setup["max_rr"],
                "optimal_rr": optimal_rr,
                "orig_pm": orig_pm,
                "opt_pm": opt_pm,
                "pm_change": pm_change,
                "orig_conf": orig_conf,
                "opt_conf": opt_conf,
                "orig_status": orig_status,
                "opt_status": opt_status,
                "orig_verdict": orig_verdict,
                "opt_verdict": opt_verdict,
                "hit_sl": setup["hit_sl"],
            })

    # Summary analysis
    print("\n" + "=" * 120)
    print("SUMMARY: WHAT CHANGED AT THE OPTIMAL ENTRY POINT?")
    print("=" * 120)

    if better_entries:
        print(f"\n{'#':>2} | {'Orig Time':<10} | {'Opt Time':<10} | {'DD%':>6} | "
              f"{'Orig RR':>7} | {'Opt RR':>7} | {'Orig PM':>8} | {'Opt PM':>8} | {'Orig Status':<10} | {'Opt Status':<10}")
        print("-" * 120)

        for e in better_entries:
            print(f"{e['setup_num']:>2} | {e['original_time']:<10} | {e['optimal_time']:<10} | "
                  f"{e['drawdown_pct']:>5.1f}% | {e['original_rr']:>7.2f} | {e['optimal_rr']:>7.2f} | "
                  f"{e['orig_pm']:>+8.1f} | {e['opt_pm']:>+8.1f} | {e['orig_status']:<10} | {e['opt_status']:<10}")

        # Find patterns
        print("\n" + "-" * 120)
        print("PATTERN ANALYSIS")
        print("-" * 120)

        # PM changes
        avg_orig_pm = sum(e["orig_pm"] for e in better_entries) / len(better_entries)
        avg_opt_pm = sum(e["opt_pm"] for e in better_entries) / len(better_entries)
        avg_pm_change = sum(e["pm_change"] for e in better_entries) / len(better_entries)

        print(f"""
1. PREMIUM MOMENTUM:
   - Avg PM at original entry: {avg_orig_pm:+.1f}
   - Avg PM at optimal entry:  {avg_opt_pm:+.1f}
   - Avg PM change:            {avg_pm_change:+.1f}
""")

        # Confidence changes
        avg_orig_conf = sum(e["orig_conf"] for e in better_entries) / len(better_entries)
        avg_opt_conf = sum(e["opt_conf"] for e in better_entries) / len(better_entries)

        print(f"""2. SIGNAL CONFIDENCE:
   - Avg confidence at original entry: {avg_orig_conf:.0f}%
   - Avg confidence at optimal entry:  {avg_opt_conf:.0f}%
""")

        # Status changes
        print("3. CONFIRMATION STATUS CHANGES:")
        for e in better_entries:
            if e["orig_status"] != e["opt_status"]:
                print(f"   Setup #{e['setup_num']}: {e['orig_status']} -> {e['opt_status']}")

        # Verdict changes
        print("\n4. VERDICT CHANGES:")
        for e in better_entries:
            if e["orig_verdict"] != e["opt_verdict"]:
                print(f"   Setup #{e['setup_num']}: {e['orig_verdict'][:30]} -> {e['opt_verdict'][:30]}")

        # Drawdown analysis
        avg_drawdown = sum(e["drawdown_pct"] for e in better_entries) / len(better_entries)
        print(f"""
5. DRAWDOWN BEFORE OPTIMAL ENTRY:
   - Avg drawdown from original entry: {avg_drawdown:.1f}%
   - This is how much the premium fell before recovering
""")

        # RR improvement
        avg_orig_rr = sum(e["original_rr"] for e in better_entries) / len(better_entries)
        avg_opt_rr = sum(e["optimal_rr"] for e in better_entries) / len(better_entries)

        print(f"""6. RR IMPROVEMENT:
   - Avg RR from original entry: {avg_orig_rr:.2f}
   - Avg RR from optimal entry:  {avg_opt_rr:.2f}
   - RR improvement: {(avg_opt_rr / avg_orig_rr - 1) * 100:.0f}%
""")

    # Recommendations
    print("\n" + "=" * 120)
    print("RECOMMENDATIONS FOR ALGO OPTIMIZATION")
    print("=" * 120)

    hit_sl_count = sum(1 for e in better_entries if e["hit_sl"])
    total = len(better_entries)

    # Analyze what was different at optimal entry
    pm_went_more_negative = sum(1 for e in better_entries if e["opt_pm"] < e["orig_pm"])
    pm_turned_positive = sum(1 for e in better_entries if e["orig_pm"] < 0 and e["opt_pm"] > e["orig_pm"])

    print(f"""
Based on {total} unique high RR setups:

PROBLEM:
- {hit_sl_count}/{total} ({hit_sl_count/total*100:.0f}%) hit SL before reaching target
- We are entering TOO EARLY, before the dip completes

OBSERVATIONS:
- Avg drawdown before recovery: {avg_drawdown:.1f}%
- PM went MORE negative at bottom: {pm_went_more_negative}/{total} cases
- PM started recovering at bottom: {pm_turned_positive}/{total} cases

POTENTIAL ALGO CHANGES TO TEST:

1. WAIT FOR PM REVERSAL:
   - Don't enter when PM is falling (negative and getting more negative)
   - Wait for PM to start recovering (still negative but improving)
   - Current: We enter at PM = {avg_orig_pm:+.1f}
   - Optimal: We should enter at PM = {avg_opt_pm:+.1f}

2. REQUIRE PM CONFIRMATION:
   - If PM was very negative (< -50), wait for it to cross above -50
   - This confirms the selling pressure is reducing

3. TIME-BASED FILTER:
   - Most drawdowns completed within 30-60 minutes
   - Consider a "cooling off" period after initial signal

4. PRICE-BASED CONFIRMATION:
   - Don't enter on the first bullish signal
   - Wait for price to make a higher low (confirm reversal)

5. RE-ENTRY STRATEGY:
   - If original entry hits SL, watch for re-entry at better price
   - The dip was a shakeout, not a trend change

NEXT STEPS:
- Gather more data over the coming days
- Track PM reversal patterns
- Test delayed entry (wait 1-2 candles after signal)
""")

    conn.close()


if __name__ == "__main__":
    main()
