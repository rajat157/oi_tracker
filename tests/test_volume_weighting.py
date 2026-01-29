"""
Test script to verify volume-weighted conviction scoring.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports when running from tests/
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_latest_snapshot
from oi_analyzer import analyze_tug_of_war, calculate_conviction_multiplier


def test_conviction_calculation():
    """Test the conviction multiplier formula."""
    print("=" * 80)
    print("Testing Conviction Multiplier Calculation")
    print("=" * 80)

    test_cases = [
        {"volume": 50000, "oi_change": 50000, "expected": 1.5, "label": "100% turnover (very fresh)"},
        {"volume": 30000, "oi_change": 50000, "expected": 1.5, "label": "60% turnover (fresh)"},
        {"volume": 15000, "oi_change": 50000, "expected": 1.0, "label": "30% turnover (moderate)"},
        {"volume": 5000, "oi_change": 50000, "expected": 0.5, "label": "10% turnover (stale)"},
        {"volume": 100, "oi_change": 50, "expected": 0.5, "label": "Negligible OI change"},
    ]

    for case in test_cases:
        result = calculate_conviction_multiplier(case["volume"], case["oi_change"])
        status = "✓" if abs(result - case["expected"]) < 0.01 else "✗"
        print(f"{status} {case['label']}")
        print(f"   Volume: {case['volume']:,}, OI Change: {case['oi_change']:,}")
        print(f"   Expected: {case['expected']:.2f}, Got: {result:.2f}")
        print()


def test_live_analysis():
    """Test volume weighting with live data."""
    print("=" * 80)
    print("Testing Volume-Weighted Analysis")
    print("=" * 80)

    snapshot = get_latest_snapshot()
    if not snapshot:
        print("No snapshot data available")
        return

    # Analyze without volume (old behavior)
    old_strikes = {
        k: {
            "ce_oi": v["ce_oi"],
            "ce_oi_change": v["ce_oi_change"],
            "pe_oi": v["pe_oi"],
            "pe_oi_change": v["pe_oi_change"]
        }
        for k, v in snapshot["strikes"].items()
    }

    old_analysis = analyze_tug_of_war(
        old_strikes,
        snapshot["spot_price"],
        include_atm=True,
        include_itm=True
    )

    # Analyze with volume (new behavior)
    new_analysis = analyze_tug_of_war(
        snapshot["strikes"],
        snapshot["spot_price"],
        include_atm=True,
        include_itm=True
    )

    print(f"Spot Price: {snapshot['spot_price']:,.1f}")
    print()
    print("WITHOUT Volume Weighting:")
    print(f"  Call OI Change: {old_analysis['call_oi_change']:,}")
    print(f"  Put OI Change: {old_analysis['put_oi_change']:,}")
    print(f"  Combined Score: {old_analysis['combined_score']:.1f}")
    print(f"  Verdict: {old_analysis['verdict']}")
    print()
    print("WITH Volume Weighting:")
    print(f"  Call OI Change (weighted): {new_analysis['call_oi_change']:,}")
    print(f"  Put OI Change (weighted): {new_analysis['put_oi_change']:,}")
    print(f"  Combined Score: {new_analysis['combined_score']:.1f}")
    print(f"  Verdict: {new_analysis['verdict']}")
    print(f"  Avg Call Conviction: {new_analysis.get('avg_call_conviction', 0):.2f}")
    print(f"  Avg Put Conviction: {new_analysis.get('avg_put_conviction', 0):.2f}")
    print()

    # Show per-strike breakdown
    print("OTM Calls Breakdown:")
    for call in new_analysis['otm_calls'][:3]:
        print(f"  Strike {call['strike']}: "
              f"OI Δ={call['oi_change']:,}, "
              f"Vol={call.get('volume', 0):,}, "
              f"Conviction={call.get('conviction', 1.0):.2f}x")
    print()

    print("OTM Puts Breakdown:")
    for put in new_analysis['otm_puts'][:3]:
        print(f"  Strike {put['strike']}: "
              f"OI Δ={put['oi_change']:,}, "
              f"Vol={put.get('volume', 0):,}, "
              f"Conviction={put.get('conviction', 1.0):.2f}x")


if __name__ == "__main__":
    test_conviction_calculation()
    print("\n")
    test_live_analysis()
