"""
Analyze Quality Score >= 7 trades in detail
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"
LOT_SIZE = 65

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

cursor = conn.cursor()
cursor.execute("""
    SELECT id, timestamp, spot_price, verdict, signal_confidence, analysis_json
    FROM analysis_history
    WHERE analysis_json IS NOT NULL
    ORDER BY timestamp ASC
""")

rows = cursor.fetchall()


def calculate_quality_score(analysis):
    score = 0
    if analysis.get("confirmation_status") == "CONFIRMED":
        score += 2
    conf = analysis.get("signal_confidence", 0)
    if 60 <= conf <= 85:
        score += 2
    elif 50 <= conf < 60 or 85 < conf <= 95:
        score += 1
    verdict = analysis.get("verdict", "")
    if "Winning" in verdict:
        score += 1
    setup = analysis.get("trade_setup", {})
    if setup:
        if setup.get("moneyness") == "ITM":
            score += 1
        if setup.get("risk_pct", 20) <= 15:
            score += 1
    pm = analysis.get("premium_momentum", {})
    pm_score = pm.get("premium_momentum_score", 0) if isinstance(pm, dict) else 0
    is_bullish = "bull" in verdict.lower()
    if (is_bullish and pm_score > 10) or (not is_bullish and pm_score < -10):
        score += 1
    return score


def get_snapshots(start_time, end_time, strike):
    cursor.execute("""
        SELECT timestamp, ce_ltp, pe_ltp, spot_price
        FROM oi_snapshots
        WHERE timestamp >= ? AND timestamp <= ? AND strike_price = ?
        ORDER BY timestamp ASC
    """, (start_time, end_time, strike))
    return [dict(r) for r in cursor.fetchall()]


high_quality_trades = []

for row in rows:
    try:
        analysis = json.loads(row["analysis_json"])
    except:
        continue

    trade_setup = analysis.get("trade_setup", {})
    if not trade_setup:
        continue

    quality_score = calculate_quality_score(analysis)
    if quality_score < 7:
        continue

    entry_premium = trade_setup.get("entry_premium", 0)
    sl_premium = trade_setup.get("sl_premium", 0)
    target1 = trade_setup.get("target1_premium", 0)
    option_type = trade_setup.get("option_type", "CE")
    strike = trade_setup.get("strike", 0)

    if entry_premium <= 0 or sl_premium <= 0:
        continue

    risk = entry_premium - sl_premium
    if risk <= 0:
        continue

    # Get future price data
    start_dt = datetime.fromisoformat(row["timestamp"])
    end_dt = start_dt + timedelta(hours=3)
    snapshots = get_snapshots(row["timestamp"], end_dt.isoformat(), strike)

    if not snapshots:
        continue

    ltp_key = "ce_ltp" if option_type == "CE" else "pe_ltp"

    max_premium = entry_premium
    max_time = row["timestamp"]
    min_premium = entry_premium
    hit_sl = False
    hit_target = False
    target_time = None
    sl_time = None

    for snap in snapshots[1:]:
        current = snap.get(ltp_key, 0)
        if current <= 0:
            continue

        if current > max_premium:
            max_premium = current
            max_time = snap["timestamp"]

        if current < min_premium:
            min_premium = current

        if current <= sl_premium and not hit_sl:
            hit_sl = True
            sl_time = snap["timestamp"]

        if current >= target1 and not hit_target:
            hit_target = True
            target_time = snap["timestamp"]

    max_rr = (max_premium - entry_premium) / risk

    high_quality_trades.append({
        "timestamp": row["timestamp"],
        "quality_score": quality_score,
        "direction": trade_setup.get("direction"),
        "strike": strike,
        "option_type": option_type,
        "moneyness": trade_setup.get("moneyness"),
        "entry_premium": entry_premium,
        "sl_premium": sl_premium,
        "target1": target1,
        "risk": risk,
        "risk_pct": trade_setup.get("risk_pct", 0),
        "max_premium": max_premium,
        "max_time": max_time,
        "min_premium": min_premium,
        "max_rr": max_rr,
        "hit_sl": hit_sl,
        "sl_time": sl_time,
        "hit_target": hit_target,
        "target_time": target_time,
        "verdict": analysis.get("verdict"),
        "confidence": analysis.get("signal_confidence"),
        "confirmation": analysis.get("confirmation_status"),
        "regime": analysis.get("market_regime", {}).get("regime"),
        "pm_score": analysis.get("premium_momentum", {}).get("premium_momentum_score", 0),
    })

conn.close()

print("=" * 100)
print("ALL QUALITY SCORE >= 7 TRADES - DETAILED ANALYSIS")
print("=" * 100)
print(f"Total trades with Score >= 7: {len(high_quality_trades)}")

# Separate by RR achieved
high_rr = [t for t in high_quality_trades if t["max_rr"] >= 2.0]
low_rr = [t for t in high_quality_trades if t["max_rr"] < 2.0]

print(f"  - Achieved 1:2+ RR: {len(high_rr)}")
print(f"  - Did NOT achieve 1:2+ RR: {len(low_rr)}")

print()
print("=" * 100)
print("SCORE >= 7 TRADES THAT ACHIEVED 1:2+ RR")
print("=" * 100)

for i, t in enumerate(sorted(high_rr, key=lambda x: -x["max_rr"]), 1):
    pnl_at_max = (t["max_premium"] - t["entry_premium"]) * LOT_SIZE
    pnl_at_target = (t["target1"] - t["entry_premium"]) * LOT_SIZE if t["hit_target"] else 0

    print(f"""
Trade #{i}: {t['direction']} {t['strike']} {t['option_type']} ({t['moneyness']})
  Date/Time: {t['timestamp']}
  Quality Score: {t['quality_score']}

  ENTRY: {t['entry_premium']:.2f}
  SL: {t['sl_premium']:.2f} (Risk: {t['risk']:.2f} / {t['risk_pct']:.0f}%)
  TARGET: {t['target1']:.2f}

  MAX ACHIEVED: {t['max_premium']:.2f} @ {t['max_time'][11:19] if t['max_time'] else 'N/A'}
  MAX RR: {t['max_rr']:.2f}
  POTENTIAL P&L: +{pnl_at_max:,.0f} INR

  Hit Target (1:1)? {'YES @ ' + t['target_time'][11:19] if t['hit_target'] else 'NO (but max was higher!)'}
  Hit SL first? {'YES @ ' + t['sl_time'][11:19] if t['hit_sl'] else 'NO'}

  Conditions at entry:
    - Verdict: {t['verdict']}
    - Confidence: {t['confidence']:.0f}%
    - Confirmation: {t['confirmation']}
    - Regime: {t['regime']}
    - Premium Momentum: {t['pm_score']:.1f}
""")

print("=" * 100)
print("SCORE >= 7 TRADES THAT DID NOT ACHIEVE 1:2+ RR")
print("=" * 100)

for i, t in enumerate(sorted(low_rr, key=lambda x: -x["max_rr"]), 1):
    pnl_at_max = (t["max_premium"] - t["entry_premium"]) * LOT_SIZE

    if t["hit_target"]:
        outcome = f"HIT TARGET @ {t['target_time'][11:19]}"
    elif t["hit_sl"]:
        outcome = f"HIT SL @ {t['sl_time'][11:19]}"
    else:
        outcome = "EXPIRED"

    print(f"""
Trade #{i}: {t['direction']} {t['strike']} {t['option_type']} ({t['moneyness']})
  Date/Time: {t['timestamp']}
  Quality Score: {t['quality_score']}

  ENTRY: {t['entry_premium']:.2f}
  SL: {t['sl_premium']:.2f} (Risk: {t['risk']:.2f} / {t['risk_pct']:.0f}%)
  TARGET: {t['target1']:.2f}

  MAX ACHIEVED: {t['max_premium']:.2f} (RR: {t['max_rr']:.2f})
  MIN REACHED: {t['min_premium']:.2f}
  OUTCOME: {outcome}

  Conditions at entry:
    - Verdict: {t['verdict']}
    - Confidence: {t['confidence']:.0f}%
    - Confirmation: {t['confirmation']}
    - Regime: {t['regime']}
    - Premium Momentum: {t['pm_score']:.1f}
""")

# Summary
print("=" * 100)
print("SUMMARY: QUALITY SCORE >= 7 PERFORMANCE")
print("=" * 100)

total = len(high_quality_trades)
achieved_2x = len(high_rr)
hit_target = sum(1 for t in high_quality_trades if t["hit_target"])
hit_sl = sum(1 for t in high_quality_trades if t["hit_sl"])
neither = total - hit_target - hit_sl

avg_max_rr = sum(t["max_rr"] for t in high_quality_trades) / total if total else 0

print(f"""
Total Score >= 7 setups: {total}

OUTCOMES:
  - Hit Target (1:1): {hit_target} ({hit_target/total*100:.0f}%)
  - Hit SL: {hit_sl} ({hit_sl/total*100:.0f}%)
  - Neither (expired): {neither} ({neither/total*100:.0f}%)

MAX RR POTENTIAL:
  - Achieved 1:2+ max: {achieved_2x} ({achieved_2x/total*100:.0f}%)
  - Avg Max RR: {avg_max_rr:.2f}

If we held for 1:2 target instead of 1:1:
  - Would have won: {sum(1 for t in high_quality_trades if t['max_rr'] >= 2.0 and not t['hit_sl'])}
  - Would have lost (SL hit first): {sum(1 for t in high_quality_trades if t['hit_sl'])}
""")

# Calculate actual P&L at 1:1 vs potential at 1:2
pnl_1_1 = 0
pnl_1_2_potential = 0
for t in high_quality_trades:
    if t["hit_target"]:
        pnl_1_1 += (t["target1"] - t["entry_premium"]) * LOT_SIZE
    elif t["hit_sl"]:
        pnl_1_1 += (t["sl_premium"] - t["entry_premium"]) * LOT_SIZE

    # For 1:2, check if we would have hit it
    target_2x = t["entry_premium"] + (t["risk"] * 2)
    if t["max_premium"] >= target_2x and not t["hit_sl"]:
        pnl_1_2_potential += (target_2x - t["entry_premium"]) * LOT_SIZE
    elif t["hit_sl"]:
        pnl_1_2_potential += (t["sl_premium"] - t["entry_premium"]) * LOT_SIZE

print(f"""
P&L COMPARISON:
  - At 1:1 RR (actual): {pnl_1_1:+,.0f} INR
  - At 1:2 RR (if held): {pnl_1_2_potential:+,.0f} INR
""")
