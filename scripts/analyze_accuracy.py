"""
Analyze signal accuracy across different OTM/ATM/ITM configurations.
Compares verdicts against subsequent price movements.
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

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
                   ce_volume, pe_volume
            FROM oi_snapshots
            WHERE timestamp = ?
        """, (timestamp,))

        strikes = {}
        for row in cursor.fetchall():
            strikes[row["strike_price"]] = {
                "ce_oi": row["ce_oi"],
                "ce_oi_change": row["ce_oi_change"],
                "ce_volume": row["ce_volume"] or 0,
                "pe_oi": row["pe_oi"],
                "pe_oi_change": row["pe_oi_change"],
                "pe_volume": row["pe_volume"] or 0,
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
            "price_change": round(price_change, 2),
            "price_change_pct": round(price_change_pct, 3),
            "correct": correct_prediction
        })

    accuracy = (correct / total * 100) if total > 0 else 0
    return accuracy, total, details


def main():
    print("=" * 70)
    print("  OI Signal Accuracy Analysis")
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

    # Test different configurations
    configs = [
        ("OTM Only", False, False),
        ("OTM + ATM", True, False),
        ("OTM + ITM", False, True),
        ("OTM + ATM + ITM", True, True),
    ]

    print("\n" + "=" * 70)
    print("  Results (1-period lookahead = 3 minutes)")
    print("=" * 70)

    all_results = {}

    for name, include_atm, include_itm in configs:
        results = analyze_with_config(snapshots, include_atm, include_itm)
        accuracy, total, details = calculate_accuracy(results, snapshots, lookahead=1)
        all_results[name] = {"accuracy": accuracy, "total": total, "details": details}

        print(f"\n{name}:")
        print(f"  Accuracy: {accuracy:.1f}% ({total} predictions)")

    # Test multiple lookahead periods
    for lookahead, mins in [(2, 6), (5, 15), (10, 30)]:
        print("\n" + "=" * 70)
        print(f"  Results ({lookahead}-period lookahead = {mins} minutes)")
        print("=" * 70)

        for name, include_atm, include_itm in configs:
            results = analyze_with_config(snapshots, include_atm, include_itm)
            accuracy, total, details = calculate_accuracy(results, snapshots, lookahead=lookahead)

            print(f"\n{name}:")
            print(f"  Accuracy: {accuracy:.1f}% ({total} predictions)")

    # Detailed breakdown by strength for best config
    print("\n" + "=" * 70)
    print("  Breakdown by Signal Strength (OTM + ATM + ITM, 1-period)")
    print("=" * 70)

    results = analyze_with_config(snapshots, True, True)
    _, _, details = calculate_accuracy(results, snapshots, lookahead=1)

    for strength in ["strong", "moderate", "weak"]:
        strength_details = [d for d in details if d["strength"] == strength]
        if strength_details:
            correct = sum(1 for d in strength_details if d["correct"])
            total = len(strength_details)
            acc = (correct / total * 100) if total > 0 else 0
            print(f"\n{strength.upper()} signals:")
            print(f"  Accuracy: {acc:.1f}% ({correct}/{total})")

    # Breakdown by confirmation status
    print("\n" + "=" * 70)
    print("  Breakdown by Confirmation Status (OTM + ATM + ITM, 1-period)")
    print("=" * 70)

    for status in ["CONFIRMED", "CONFLICT", "REVERSAL_ALERT", "NEUTRAL"]:
        status_details = [d for d in details if d["confirmation"] == status]
        if status_details:
            correct = sum(1 for d in status_details if d["correct"])
            total = len(status_details)
            acc = (correct / total * 100) if total > 0 else 0
            print(f"\n{status}:")
            print(f"  Accuracy: {acc:.1f}% ({correct}/{total})")

    # Show recent predictions
    print("\n" + "=" * 70)
    print("  Recent Predictions (last 10)")
    print("=" * 70)

    for d in details[-10:]:
        status = "OK" if d["correct"] else "X "
        print(f"  [{status}] {d['timestamp'][-8:]} | {d['verdict']:25} | Score: {d['score']:+6.1f} | "
              f"Price: {d['price_change']:+6.2f} ({d['price_change_pct']:+.3f}%)")


if __name__ == "__main__":
    main()
