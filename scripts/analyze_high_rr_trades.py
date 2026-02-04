"""
Analyze Historical Data for High RR Trades

Finds trades that achieved 1:2 or 1:3 risk-reward moves and analyzes
what conditions were present at entry to identify patterns.

Usage:
    uv run python scripts/analyze_high_rr_trades.py
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_snapshots_for_premium_tracking(start_time: str, end_time: str, strike: int):
    """Get OI snapshots for premium tracking."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT timestamp, ce_ltp, pe_ltp, spot_price
        FROM oi_snapshots
        WHERE timestamp >= ? AND timestamp <= ?
          AND strike_price = ?
        ORDER BY timestamp ASC
    """, (start_time, end_time, strike))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def analyze_max_move(analysis: dict, option_type: str, entry_premium: float, sl_premium: float):
    """
    Track the maximum favorable move for a trade setup.

    Returns:
        Dict with max_premium, max_rr_achieved, time_to_max, conditions at peak
    """
    trade_setup = analysis.get("trade_setup", {})
    if not trade_setup:
        return None

    strike = trade_setup.get("strike", 0)
    timestamp = analysis.get("_db_timestamp", "")

    if not strike or not timestamp:
        return None

    # Look ahead 3 hours
    try:
        start_dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return None

    end_dt = start_dt + timedelta(hours=3)

    snapshots = get_snapshots_for_premium_tracking(
        timestamp,
        end_dt.isoformat(),
        strike
    )

    if not snapshots:
        return None

    ltp_key = "ce_ltp" if option_type == "CE" else "pe_ltp"
    risk = entry_premium - sl_premium

    if risk <= 0:
        return None

    max_premium = entry_premium
    max_rr = 0
    time_to_max = 0
    max_time_ts = timestamp
    hit_sl = False
    sl_time = None
    min_premium = entry_premium
    min_time_ts = timestamp

    for i, snap in enumerate(snapshots[1:], 1):
        current_premium = snap.get(ltp_key, 0)
        if current_premium <= 0:
            continue

        # Track min premium (drawdown)
        if current_premium < min_premium:
            min_premium = current_premium
            min_time_ts = snap.get("timestamp", "")

        # Check if SL was hit first
        if current_premium <= sl_premium and not hit_sl:
            hit_sl = True
            sl_time = snap.get("timestamp", "")

        # Track max premium (even if SL was hit, to see potential)
        if current_premium > max_premium:
            max_premium = current_premium
            time_to_max = i * 3  # Approx minutes (3-min intervals)
            max_time_ts = snap.get("timestamp", "")

    # Calculate max RR achieved
    max_move = max_premium - entry_premium
    max_rr = max_move / risk if risk > 0 else 0

    # Calculate drawdown
    drawdown = entry_premium - min_premium
    drawdown_pct = (drawdown / entry_premium * 100) if entry_premium > 0 else 0

    return {
        "entry_premium": entry_premium,
        "sl_premium": sl_premium,
        "risk": risk,
        "max_premium": max_premium,
        "max_time_ts": max_time_ts,
        "max_move": max_move,
        "max_rr": max_rr,
        "time_to_max_mins": time_to_max,
        "hit_sl_first": hit_sl,
        "sl_time": sl_time,
        "min_premium": min_premium,
        "min_time_ts": min_time_ts,
        "drawdown": drawdown,
        "drawdown_pct": drawdown_pct,
    }


def calculate_quality_score(analysis: dict) -> int:
    """Calculate quality score for trade setup."""
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


def main():
    print("=" * 100)
    print("HIGH RR TRADE ANALYSIS")
    print("Finding trades that achieved 1:2+ risk-reward moves")
    print("=" * 100)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, timestamp, spot_price, verdict, signal_confidence, analysis_json
        FROM analysis_history
        WHERE analysis_json IS NOT NULL
        ORDER BY timestamp ASC
    """)

    rows = cursor.fetchall()
    conn.close()

    print(f"\nAnalyzing {len(rows)} records...")

    all_trades = []
    high_rr_trades = []  # Trades that achieved 1:2+

    for row in rows:
        try:
            analysis = json.loads(row["analysis_json"])
            analysis["_db_id"] = row["id"]
            analysis["_db_timestamp"] = row["timestamp"]
        except (json.JSONDecodeError, TypeError):
            continue

        trade_setup = analysis.get("trade_setup", {})
        if not trade_setup:
            continue

        entry_premium = trade_setup.get("entry_premium", 0)
        sl_premium = trade_setup.get("sl_premium", 0)
        option_type = trade_setup.get("option_type", "CE")

        if entry_premium <= 0 or sl_premium <= 0:
            continue

        # Analyze max move
        move_data = analyze_max_move(analysis, option_type, entry_premium, sl_premium)
        if not move_data:
            continue

        quality_score = calculate_quality_score(analysis)

        # Get target premiums from setup
        target1 = trade_setup.get("target1_premium", 0)
        target2 = trade_setup.get("target2_premium", 0)
        risk_pct = trade_setup.get("risk_pct", 0)

        trade_info = {
            "timestamp": row["timestamp"],
            "date": row["timestamp"][:10],
            "entry_time": row["timestamp"][11:19] if len(row["timestamp"]) >= 19 else "N/A",
            "spot_price": row["spot_price"],
            "verdict": analysis.get("verdict", ""),
            "confidence": analysis.get("signal_confidence", 0),
            "quality_score": quality_score,
            "direction": trade_setup.get("direction", ""),
            "strike": trade_setup.get("strike", 0),
            "moneyness": trade_setup.get("moneyness", ""),
            "option_type": option_type,
            "target1": target1,
            "target2": target2,
            "risk_pct": risk_pct,
            "regime": analysis.get("market_regime", {}).get("regime", "unknown"),
            "confirmation": analysis.get("confirmation_status", ""),
            "premium_momentum": analysis.get("premium_momentum", {}).get("premium_momentum_score", 0),
            "iv_skew": analysis.get("iv_skew", {}).get("skew_score", 0) if isinstance(analysis.get("iv_skew"), dict) else 0,
            "call_oi_change": analysis.get("call_oi_change", 0),
            "put_oi_change": analysis.get("put_oi_change", 0),
            **move_data
        }

        all_trades.append(trade_info)

        if move_data["max_rr"] >= 2.0:
            high_rr_trades.append(trade_info)

    print(f"Total setups analyzed: {len(all_trades)}")
    print(f"Setups that achieved 1:2+ RR: {len(high_rr_trades)}")

    # Summary statistics
    print("\n" + "-" * 100)
    print("RR DISTRIBUTION (Max RR achieved by all setups)")
    print("-" * 100)

    rr_buckets = defaultdict(list)
    for t in all_trades:
        rr = t["max_rr"]
        if rr < 0.5:
            rr_buckets["< 0.5 (SL zone)"].append(t)
        elif rr < 1.0:
            rr_buckets["0.5-1.0"].append(t)
        elif rr < 1.5:
            rr_buckets["1.0-1.5"].append(t)
        elif rr < 2.0:
            rr_buckets["1.5-2.0"].append(t)
        elif rr < 3.0:
            rr_buckets["2.0-3.0 (HIGH RR)"].append(t)
        else:
            rr_buckets["3.0+ (EXCELLENT)"].append(t)

    for bucket in ["< 0.5 (SL zone)", "0.5-1.0", "1.0-1.5", "1.5-2.0", "2.0-3.0 (HIGH RR)", "3.0+ (EXCELLENT)"]:
        count = len(rr_buckets.get(bucket, []))
        pct = (count / len(all_trades) * 100) if all_trades else 0
        print(f"  {bucket:<25}: {count:3} trades ({pct:5.1f}%)")

    # Lot size for INR calculation
    LOT_SIZE = 65

    # Analyze high RR trades
    if high_rr_trades:
        print("\n" + "=" * 120)
        print("HIGH RR TRADES - DETAILED LOG (Achieved 1:2+ move)")
        print("=" * 120)

        # Group by date
        by_date = defaultdict(list)
        for t in high_rr_trades:
            by_date[t["date"]].append(t)

        for date in sorted(by_date.keys()):
            trades = sorted(by_date[date], key=lambda x: x["timestamp"])
            print(f"\n{'='*120}")
            print(f"DATE: {date} | {len(trades)} high RR setups")
            print("=" * 120)

            print(f"\n{'#':>2} | {'Entry Time':<10} | {'Entry':>8} | {'SL':>8} | {'T1':>8} | "
                  f"{'Max Time':<10} | {'Max':>8} | {'P&L INR':>10} | {'RR':>5} | {'Verdict':<25}")
            print("-" * 120)

            for i, t in enumerate(trades, 1):
                max_time = t.get("max_time_ts", "")[11:19] if t.get("max_time_ts") else "N/A"
                pnl_inr = (t["max_premium"] - t["entry_premium"]) * LOT_SIZE
                pnl_sign = "+" if pnl_inr >= 0 else ""

                print(f"{i:>2} | {t['entry_time']:<10} | "
                      f"{t['entry_premium']:>8.2f} | {t['sl_premium']:>8.2f} | {t['target1']:>8.2f} | "
                      f"{max_time:<10} | {t['max_premium']:>8.2f} | "
                      f"{pnl_sign}{pnl_inr:>9,.0f} | {t['max_rr']:>5.2f} | {t['verdict']:<25}")

            # Show detailed breakdown for each trade
            print(f"\n--- DETAILED BREAKDOWN ---")
            for i, t in enumerate(trades, 1):
                sl_first = "YES - SL hit first!" if t["hit_sl_first"] else "NO"
                max_time = t.get("max_time_ts", "")[11:19] if t.get("max_time_ts") else "N/A"
                min_time = t.get("min_time_ts", "")[11:19] if t.get("min_time_ts") else "N/A"
                pnl_inr = (t["max_premium"] - t["entry_premium"]) * LOT_SIZE

                print(f"\nTrade #{i}: {t['direction']} {t['strike']} {t['option_type']} ({t['moneyness']})")
                print(f"  Entry: {t['entry_time']} @ {t['entry_premium']:.2f}")
                print(f"  SL: {t['sl_premium']:.2f} | Target1: {t['target1']:.2f} | Risk: {t['risk']:.2f} ({t['risk_pct']:.0f}%)")
                print(f"  Max Premium: {t['max_premium']:.2f} @ {max_time} (took {t['time_to_max_mins']} mins)")
                print(f"  Max RR: {t['max_rr']:.2f} | Potential P&L: +{pnl_inr:,.0f} INR")
                print(f"  Min Premium: {t['min_premium']:.2f} @ {min_time} (Drawdown: {t['drawdown']:.2f} / {t['drawdown_pct']:.1f}%)")
                print(f"  Hit SL before max? {sl_first}")
                print(f"  ---")
                print(f"  Quality Score: {t['quality_score']} | Confidence: {t['confidence']:.0f}%")
                print(f"  Regime: {t['regime']} | Confirmation: {t['confirmation']}")
                print(f"  Premium Momentum: {t['premium_momentum']:.1f} | IV Skew: {t['iv_skew']:.1f}")
                print(f"  Call OI Change: {t['call_oi_change']:,} | Put OI Change: {t['put_oi_change']:,}")
                print(f"  Spot: {t['spot_price']:.2f}")

        # Summary table
        print("\n" + "=" * 120)
        print("SUMMARY TABLE - ALL HIGH RR TRADES")
        print("=" * 120)
        print(f"\n{'#':>2} | {'Date':<10} | {'Entry Time':<10} | {'Dir':<9} | {'Strike':<6} | "
              f"{'Entry':>7} | {'Max':>7} | {'RR':>5} | {'Mins':>4} | {'Score':>5} | {'Conf%':>5} | {'SL 1st?':<7}")
        print("-" * 120)

        for i, t in enumerate(sorted(high_rr_trades, key=lambda x: -x["max_rr"]), 1):
            sl_first = "YES" if t["hit_sl_first"] else "NO"
            print(f"{i:>2} | {t['date']:<10} | {t['entry_time']:<10} | {t['direction']:<9} | {t['strike']:<6} | "
                  f"{t['entry_premium']:>7.2f} | {t['max_premium']:>7.2f} | "
                  f"{t['max_rr']:>5.2f} | {t['time_to_max_mins']:>4} | "
                  f"{t['quality_score']:>5} | {t['confidence']:>5.0f} | {sl_first:<7}")

        # Analyze common conditions
        print("\n" + "-" * 100)
        print("COMMON CONDITIONS IN HIGH RR TRADES")
        print("-" * 100)

        # Quality scores
        scores = [t["quality_score"] for t in high_rr_trades]
        print(f"\nQuality Score Distribution:")
        for score in sorted(set(scores), reverse=True):
            count = scores.count(score)
            print(f"  Score {score}: {count} trades ({count/len(high_rr_trades)*100:.0f}%)")

        # Regimes
        regimes = [t["regime"] for t in high_rr_trades]
        print(f"\nMarket Regime Distribution:")
        for regime in set(regimes):
            count = regimes.count(regime)
            print(f"  {regime}: {count} trades ({count/len(high_rr_trades)*100:.0f}%)")

        # Confirmation status
        confs = [t["confirmation"] for t in high_rr_trades]
        print(f"\nConfirmation Status:")
        for conf in set(confs):
            count = confs.count(conf)
            print(f"  {conf}: {count} trades ({count/len(high_rr_trades)*100:.0f}%)")

        # Moneyness
        moneyness = [t["moneyness"] for t in high_rr_trades]
        print(f"\nMoneyness:")
        for m in set(moneyness):
            count = moneyness.count(m)
            print(f"  {m}: {count} trades ({count/len(high_rr_trades)*100:.0f}%)")

        # Premium momentum
        pm_scores = [t["premium_momentum"] for t in high_rr_trades]
        avg_pm = sum(pm_scores) / len(pm_scores) if pm_scores else 0
        print(f"\nPremium Momentum Score: avg={avg_pm:.1f}, range=[{min(pm_scores):.1f}, {max(pm_scores):.1f}]")

        # Confidence
        confidences = [t["confidence"] for t in high_rr_trades]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0
        print(f"Signal Confidence: avg={avg_conf:.1f}%, range=[{min(confidences):.0f}%, {max(confidences):.0f}%]")

        # Hit SL first?
        sl_first_count = sum(1 for t in high_rr_trades if t["hit_sl_first"])
        print(f"\nHit SL before max: {sl_first_count}/{len(high_rr_trades)} ({sl_first_count/len(high_rr_trades)*100:.0f}%)")

        # Time to max
        times = [t["time_to_max_mins"] for t in high_rr_trades]
        avg_time = sum(times) / len(times) if times else 0
        print(f"Time to max premium: avg={avg_time:.0f} mins, range=[{min(times)}, {max(times)}] mins")

    # Compare high RR vs low RR conditions
    low_rr_trades = [t for t in all_trades if t["max_rr"] < 1.0]

    if high_rr_trades and low_rr_trades:
        print("\n" + "=" * 100)
        print("HIGH RR vs LOW RR COMPARISON")
        print("=" * 100)

        print(f"\n{'Metric':<30} | {'High RR (>=2)':>15} | {'Low RR (<1)':>15}")
        print("-" * 70)

        # Average quality score
        high_avg_score = sum(t["quality_score"] for t in high_rr_trades) / len(high_rr_trades)
        low_avg_score = sum(t["quality_score"] for t in low_rr_trades) / len(low_rr_trades)
        print(f"{'Avg Quality Score':<30} | {high_avg_score:>15.1f} | {low_avg_score:>15.1f}")

        # Average confidence
        high_avg_conf = sum(t["confidence"] for t in high_rr_trades) / len(high_rr_trades)
        low_avg_conf = sum(t["confidence"] for t in low_rr_trades) / len(low_rr_trades)
        print(f"{'Avg Confidence %':<30} | {high_avg_conf:>15.1f} | {low_avg_conf:>15.1f}")

        # Premium momentum
        high_avg_pm = sum(t["premium_momentum"] for t in high_rr_trades) / len(high_rr_trades)
        low_avg_pm = sum(t["premium_momentum"] for t in low_rr_trades) / len(low_rr_trades)
        print(f"{'Avg Premium Momentum':<30} | {high_avg_pm:>15.1f} | {low_avg_pm:>15.1f}")

        # CONFIRMED %
        high_confirmed = sum(1 for t in high_rr_trades if t["confirmation"] == "CONFIRMED") / len(high_rr_trades) * 100
        low_confirmed = sum(1 for t in low_rr_trades if t["confirmation"] == "CONFIRMED") / len(low_rr_trades) * 100
        print(f"{'% CONFIRMED':<30} | {high_confirmed:>14.0f}% | {low_confirmed:>14.0f}%")

        # ITM %
        high_itm = sum(1 for t in high_rr_trades if t["moneyness"] == "ITM") / len(high_rr_trades) * 100
        low_itm = sum(1 for t in low_rr_trades if t["moneyness"] == "ITM") / len(low_rr_trades) * 100
        print(f"{'% ITM':<30} | {high_itm:>14.0f}% | {low_itm:>14.0f}%")

        # Trending regime %
        high_trending = sum(1 for t in high_rr_trades if "trending" in t["regime"]) / len(high_rr_trades) * 100
        low_trending = sum(1 for t in low_rr_trades if "trending" in t["regime"]) / len(low_rr_trades) * 100
        print(f"{'% Trending Regime':<30} | {high_trending:>14.0f}% | {low_trending:>14.0f}%")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    main()
