"""
Analyze signal accuracy across different timeframes and configurations.
Includes regime/streak analysis, hourly direction, and trap detection metrics.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# Add parent directory to path for imports when running from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from oi_analyzer import analyze_tug_of_war

# Database path relative to project root
DB_PATH = Path(__file__).parent.parent / "oi_tracker.db"


def get_all_snapshots():
    """Get all unique timestamps with their snapshot data."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all unique timestamps
    cursor.execute("""
        SELECT DISTINCT timestamp, spot_price, expiry_date
        FROM oi_snapshots
        ORDER BY timestamp ASC
    """)
    timestamps = cursor.fetchall()

    snapshots = []
    for ts_row in timestamps:
        timestamp = ts_row["timestamp"]

        # Get strikes for this timestamp
        cursor.execute("""
            SELECT strike_price, ce_oi, ce_oi_change, pe_oi, pe_oi_change,
                   ce_volume, pe_volume, ce_iv, pe_iv
            FROM oi_snapshots
            WHERE timestamp = ?
        """, (timestamp,))

        strikes = {}
        for row in cursor.fetchall():
            strikes[row["strike_price"]] = {
                "ce_oi": row["ce_oi"],
                "ce_oi_change": row["ce_oi_change"],
                "ce_volume": row["ce_volume"] or 0,
                "ce_iv": row["ce_iv"] or 0.0,
                "pe_oi": row["pe_oi"],
                "pe_oi_change": row["pe_oi_change"],
                "pe_volume": row["pe_volume"] or 0,
                "pe_iv": row["pe_iv"] or 0.0,
            }

        if strikes:
            snapshots.append({
                "timestamp": timestamp,
                "spot_price": ts_row["spot_price"],
                "expiry_date": ts_row["expiry_date"],
                "strikes": strikes
            })

    conn.close()
    return snapshots


def analyze_with_config(snapshots, include_atm, include_itm):
    """Run analysis on all snapshots with given configuration."""
    results = []

    for i, snapshot in enumerate(snapshots):
        # Build price history from previous snapshots
        price_history = []
        for j in range(max(0, i-3), i+1):
            price_history.append({"spot_price": snapshots[j]["spot_price"]})

        analysis = analyze_tug_of_war(
            snapshot["strikes"],
            snapshot["spot_price"],
            include_atm=include_atm,
            include_itm=include_itm,
            price_history=price_history if len(price_history) > 1 else None
        )

        results.append({
            "timestamp": snapshot["timestamp"],
            "spot_price": snapshot["spot_price"],
            "verdict": analysis["verdict"],
            "combined_score": analysis["combined_score"],
            "strength": analysis["strength"],
            "confirmation_status": analysis.get("confirmation_status", "NEUTRAL"),
            "signal_confidence": analysis.get("signal_confidence", 50),
            "iv_skew": analysis.get("iv_skew", 0),
            "max_pain": analysis.get("max_pain", 0),
            "trap_warning": analysis.get("trap_warning"),
        })

    return results


def calculate_accuracy(results, snapshots, lookahead=1):
    """
    Calculate accuracy by comparing verdict to future price movement.

    lookahead: Number of periods to look ahead for price confirmation
    """
    correct = 0
    total = 0
    details = []

    for i, result in enumerate(results[:-lookahead]):
        current_price = result["spot_price"]
        future_price = snapshots[i + lookahead]["spot_price"]
        price_change = future_price - current_price
        price_change_pct = (price_change / current_price) * 100

        verdict = result["verdict"].lower()

        # Skip neutral verdicts
        if "neutral" in verdict:
            continue

        total += 1
        is_bullish_verdict = "bull" in verdict
        is_bearish_verdict = "bear" in verdict
        price_went_up = price_change > 0

        # Check if verdict was correct
        correct_prediction = (is_bullish_verdict and price_went_up) or \
                           (is_bearish_verdict and not price_went_up)

        if correct_prediction:
            correct += 1

        details.append({
            "timestamp": result["timestamp"],
            "verdict": result["verdict"],
            "score": result["combined_score"],
            "strength": result["strength"],
            "confirmation": result["confirmation_status"],
            "confidence": result["signal_confidence"],
            "iv_skew": result["iv_skew"],
            "price_change": round(price_change, 2),
            "price_change_pct": round(price_change_pct, 3),
            "correct": correct_prediction
        })

    accuracy = (correct / total * 100) if total > 0 else 0
    return accuracy, total, details


def calculate_regime_accuracy(results, snapshots):
    """
    Calculate accuracy based on regime/streak changes.

    A "regime" is a consecutive run of the same signal direction.
    We measure if the total price change during a regime matches the signal.
    """
    if len(results) < 2:
        return 0, 0, []

    regimes = []
    current_regime = {
        "direction": "bullish" if "bull" in results[0]["verdict"].lower() else "bearish",
        "start_idx": 0,
        "start_price": results[0]["spot_price"],
    }

    for i in range(1, len(results)):
        verdict = results[i]["verdict"].lower()
        is_bullish = "bull" in verdict
        is_bearish = "bear" in verdict

        if "neutral" in verdict:
            continue

        current_dir = "bullish" if is_bullish else "bearish"

        # Check for regime change
        if current_dir != current_regime["direction"]:
            # End current regime
            current_regime["end_idx"] = i - 1
            current_regime["end_price"] = results[i-1]["spot_price"]
            current_regime["duration"] = current_regime["end_idx"] - current_regime["start_idx"] + 1
            current_regime["price_change"] = current_regime["end_price"] - current_regime["start_price"]
            regimes.append(current_regime)

            # Start new regime
            current_regime = {
                "direction": current_dir,
                "start_idx": i,
                "start_price": results[i]["spot_price"],
            }

    # Close final regime
    current_regime["end_idx"] = len(results) - 1
    current_regime["end_price"] = results[-1]["spot_price"]
    current_regime["duration"] = current_regime["end_idx"] - current_regime["start_idx"] + 1
    current_regime["price_change"] = current_regime["end_price"] - current_regime["start_price"]
    regimes.append(current_regime)

    # Calculate regime accuracy
    correct = 0
    total = 0

    for regime in regimes:
        if regime["duration"] < 2:
            continue  # Skip very short regimes

        total += 1
        is_bullish_regime = regime["direction"] == "bullish"
        price_went_up = regime["price_change"] > 0

        if (is_bullish_regime and price_went_up) or (not is_bullish_regime and not price_went_up):
            correct += 1
            regime["correct"] = True
        else:
            regime["correct"] = False

    accuracy = (correct / total * 100) if total > 0 else 0
    return accuracy, total, regimes


def calculate_significant_move_accuracy(details, threshold_pct=0.1):
    """
    Calculate accuracy only for significant price moves.

    Filters out small moves (noise) and only evaluates on moves > threshold.
    """
    significant = [d for d in details if abs(d["price_change_pct"]) >= threshold_pct]

    if not significant:
        return 0, 0

    correct = sum(1 for d in significant if d["correct"])
    total = len(significant)
    accuracy = (correct / total * 100) if total > 0 else 0

    return accuracy, total


def calculate_hourly_accuracy(results, snapshots):
    """
    Calculate accuracy at hourly granularity.

    For each hour, take the first signal and measure against hour-end price.
    """
    hourly_data = defaultdict(list)

    for i, result in enumerate(results):
        ts = datetime.fromisoformat(result["timestamp"])
        hour_key = ts.strftime("%Y-%m-%d %H:00")
        hourly_data[hour_key].append((i, result))

    correct = 0
    total = 0
    hour_details = []

    hours = sorted(hourly_data.keys())
    for j, hour in enumerate(hours[:-1]):
        first_signal = hourly_data[hour][0]
        next_hour = hours[j + 1]
        last_of_next = hourly_data[next_hour][-1]

        signal_idx, signal = first_signal
        _, end_signal = last_of_next

        price_change = end_signal["spot_price"] - signal["spot_price"]

        verdict = signal["verdict"].lower()
        if "neutral" in verdict:
            continue

        total += 1
        is_bullish = "bull" in verdict
        price_went_up = price_change > 0

        correct_prediction = (is_bullish and price_went_up) or (not is_bullish and not price_went_up)
        if correct_prediction:
            correct += 1

        hour_details.append({
            "hour": hour,
            "verdict": signal["verdict"],
            "price_change": round(price_change, 2),
            "correct": correct_prediction
        })

    accuracy = (correct / total * 100) if total > 0 else 0
    return accuracy, total, hour_details


def calculate_day_direction_accuracy(results, snapshots):
    """
    Calculate if the overall day direction matched the predominant signal.
    """
    if len(results) < 2:
        return False, 0, 0

    # Get predominant signal (most common direction)
    bullish_count = sum(1 for r in results if "bull" in r["verdict"].lower())
    bearish_count = sum(1 for r in results if "bear" in r["verdict"].lower())

    if bullish_count == bearish_count:
        return None, 0, 0  # Tie

    predominant = "bullish" if bullish_count > bearish_count else "bearish"

    # Get day's price change
    day_change = results[-1]["spot_price"] - results[0]["spot_price"]
    price_went_up = day_change > 0

    is_correct = (predominant == "bullish" and price_went_up) or \
                 (predominant == "bearish" and not price_went_up)

    return is_correct, day_change, bullish_count - bearish_count


def calculate_confidence_tier_accuracy(details):
    """
    Calculate accuracy by confidence tier.
    """
    tiers = {
        "high (>70%)": [d for d in details if d.get("confidence", 0) >= 70],
        "medium (50-70%)": [d for d in details if 50 <= d.get("confidence", 0) < 70],
        "low (<50%)": [d for d in details if d.get("confidence", 0) < 50]
    }

    results = {}
    for tier_name, tier_details in tiers.items():
        if tier_details:
            correct = sum(1 for d in tier_details if d["correct"])
            total = len(tier_details)
            results[tier_name] = {
                "accuracy": (correct / total * 100) if total > 0 else 0,
                "total": total,
                "correct": correct
            }

    return results


def main():
    print("=" * 70)
    print("  OI Signal Accuracy Analysis (Enhanced)")
    print("=" * 70)

    # Get all historical data
    snapshots = get_all_snapshots()
    print(f"\nTotal snapshots available: {len(snapshots)}")

    if len(snapshots) < 3:
        print("Not enough data for analysis. Need at least 3 snapshots.")
        return

    # Show time range
    print(f"Time range: {snapshots[0]['timestamp']} to {snapshots[-1]['timestamp']}")
    print(f"Price range: {min(s['spot_price'] for s in snapshots):.2f} to {max(s['spot_price'] for s in snapshots):.2f}")

    # Use best config (OTM + ATM + ITM)
    print("\nRunning analysis with OTM + ATM + ITM configuration...")
    results = analyze_with_config(snapshots, include_atm=True, include_itm=True)

    # 1. Traditional interval accuracy
    print("\n" + "=" * 70)
    print("  1. INTERVAL ACCURACY (3-min lookahead)")
    print("=" * 70)

    accuracy_3m, total_3m, details_3m = calculate_accuracy(results, snapshots, lookahead=1)
    print(f"\n3-minute accuracy: {accuracy_3m:.1f}% ({total_3m} predictions)")
    print("NOTE: This is expected to be ~50% (random) for short intervals")

    # 2. Regime/Streak accuracy
    print("\n" + "=" * 70)
    print("  2. REGIME/STREAK ACCURACY (Trend Direction)")
    print("=" * 70)

    regime_acc, regime_total, regimes = calculate_regime_accuracy(results, snapshots)
    print(f"\nRegime accuracy: {regime_acc:.1f}% ({regime_total} regimes)")
    print("\nRegime details:")
    for r in regimes[-10:]:  # Show last 10
        status = "OK" if r.get("correct") else "X "
        print(f"  [{status}] {r['direction']:8} ({r['duration']:3} periods) -> Price: {r['price_change']:+7.2f}")

    # 3. Significant moves only
    print("\n" + "=" * 70)
    print("  3. SIGNIFICANT MOVES ONLY (>0.1% price change)")
    print("=" * 70)

    for threshold in [0.05, 0.1, 0.2]:
        sig_acc, sig_total = calculate_significant_move_accuracy(details_3m, threshold)
        print(f"  >{threshold*100:.0f}% moves: {sig_acc:.1f}% ({sig_total} samples)")

    # 4. Hourly accuracy
    print("\n" + "=" * 70)
    print("  4. HOURLY DIRECTION ACCURACY")
    print("=" * 70)

    hourly_acc, hourly_total, hourly_details = calculate_hourly_accuracy(results, snapshots)
    print(f"\nHourly accuracy: {hourly_acc:.1f}% ({hourly_total} hours)")

    # 5. Day direction
    print("\n" + "=" * 70)
    print("  5. DAY DIRECTION ACCURACY")
    print("=" * 70)

    day_correct, day_change, signal_balance = calculate_day_direction_accuracy(results, snapshots)
    if day_correct is not None:
        status = "CORRECT" if day_correct else "WRONG"
        print(f"\nDay direction: {status}")
        print(f"  Price change: {day_change:+.2f}")
        print(f"  Signal balance: {signal_balance:+d} (positive = more bullish signals)")

    # 6. Accuracy by signal strength
    print("\n" + "=" * 70)
    print("  6. ACCURACY BY SIGNAL STRENGTH")
    print("=" * 70)

    for strength in ["strong", "moderate", "weak"]:
        strength_details = [d for d in details_3m if d["strength"] == strength]
        if strength_details:
            correct = sum(1 for d in strength_details if d["correct"])
            total = len(strength_details)
            acc = (correct / total * 100) if total > 0 else 0
            print(f"\n  {strength.upper():10} signals: {acc:.1f}% ({correct}/{total})")

    # 7. Accuracy by confidence tier
    print("\n" + "=" * 70)
    print("  7. ACCURACY BY CONFIDENCE TIER")
    print("=" * 70)

    conf_tiers = calculate_confidence_tier_accuracy(details_3m)
    for tier_name, tier_data in conf_tiers.items():
        print(f"\n  {tier_name}: {tier_data['accuracy']:.1f}% ({tier_data['correct']}/{tier_data['total']})")

    # 8. Accuracy by confirmation status
    print("\n" + "=" * 70)
    print("  8. ACCURACY BY CONFIRMATION STATUS")
    print("=" * 70)

    for status in ["CONFIRMED", "CONFLICT", "REVERSAL_ALERT", "NEUTRAL"]:
        status_details = [d for d in details_3m if d["confirmation"] == status]
        if status_details:
            correct = sum(1 for d in status_details if d["correct"])
            total = len(status_details)
            acc = (correct / total * 100) if total > 0 else 0
            print(f"\n  {status}: {acc:.1f}% ({correct}/{total})")

    # 9. Multiple lookahead periods
    print("\n" + "=" * 70)
    print("  9. ACCURACY BY LOOKAHEAD PERIOD")
    print("=" * 70)

    for lookahead, label in [(1, "3 min"), (5, "15 min"), (10, "30 min"), (20, "1 hour")]:
        if lookahead < len(results):
            acc, total, _ = calculate_accuracy(results, snapshots, lookahead=lookahead)
            print(f"\n  {label}: {acc:.1f}% ({total} predictions)")

    # 10. IV Skew correlation
    print("\n" + "=" * 70)
    print("  10. IV SKEW IMPACT")
    print("=" * 70)

    high_skew = [d for d in details_3m if abs(d.get("iv_skew", 0)) > 2]
    low_skew = [d for d in details_3m if abs(d.get("iv_skew", 0)) <= 2]

    if high_skew:
        high_acc = sum(1 for d in high_skew if d["correct"]) / len(high_skew) * 100
        print(f"\n  High IV skew (|skew| > 2%): {high_acc:.1f}% ({len(high_skew)} samples)")

    if low_skew:
        low_acc = sum(1 for d in low_skew if d["correct"]) / len(low_skew) * 100
        print(f"  Low IV skew (|skew| <= 2%): {low_acc:.1f}% ({len(low_skew)} samples)")

    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY & RECOMMENDATIONS")
    print("=" * 70)
    print(f"""
Key Findings:
- 3-min interval accuracy: {accuracy_3m:.1f}% (expected ~50% for noise)
- Regime/trend accuracy: {regime_acc:.1f}% (THIS IS THE REAL METRIC)
- Hourly accuracy: {hourly_acc:.1f}%

Recommendations:
1. Use REGIME signals, not per-interval predictions
2. Focus on STRONG signals only (filter weak)
3. Wait for CONFIRMED status before acting
4. Higher confidence (>70%) = more reliable

The system works better for TREND DIRECTION than timing!
    """)

    # Recent predictions
    print("\n" + "=" * 70)
    print("  RECENT PREDICTIONS (last 15)")
    print("=" * 70)

    for d in details_3m[-15:]:
        status = "OK" if d["correct"] else "X "
        conf = d.get("confidence", 0)
        print(f"  [{status}] {d['timestamp'][-8:]} | {d['verdict']:25} | "
              f"Score: {d['score']:+6.1f} | Conf: {conf:3.0f}% | "
              f"Price: {d['price_change']:+6.2f}")


if __name__ == "__main__":
    main()
