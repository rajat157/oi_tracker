"""
Test script to verify momentum fix with historical data.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports when running from tests/
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_analysis_history, get_strikes_for_timestamp, get_recent_price_trend
from oi_analyzer import analyze_tug_of_war, calculate_price_momentum


def test_momentum_on_historical_data():
    """Test momentum calculation on historical data from today."""
    print("=" * 80)
    print("Testing Momentum Fix on Historical Data")
    print("=" * 80)
    print()

    # Get last 10 records
    history = get_analysis_history(10)

    if len(history) < 4:
        print("Not enough historical data to test momentum (need at least 4 records)")
        return

    print(f"Analyzing last {len(history)} data points:\n")

    for i, record in enumerate(history):
        if i < 3:  # Need at least 3 previous records for momentum
            print(f"[{i+1}] {record['timestamp']}: Spot={record['spot_price']:.1f}, "
                  f"Verdict={record['verdict']} (skipping - insufficient history)")
            continue

        # Get price history for momentum (last 3-4 records = 9-12 minutes)
        recent_prices = [
            {'spot_price': h['spot_price']}
            for h in history[max(0, i-3):i+1]
        ]

        # Calculate momentum
        momentum = calculate_price_momentum(recent_prices)

        # Get strikes for this timestamp
        strikes = get_strikes_for_timestamp(record['timestamp'])

        if not strikes:
            print(f"[{i+1}] {record['timestamp']}: No strike data available")
            continue

        # Re-analyze with momentum
        analysis = analyze_tug_of_war(
            strikes,
            record['spot_price'],
            include_atm=True,
            include_itm=True,
            price_history=recent_prices
        )

        # Calculate price change
        old_price = recent_prices[0]['spot_price']
        new_price = recent_prices[-1]['spot_price']
        price_change = new_price - old_price
        price_change_pct = (price_change / old_price * 100) if old_price > 0 else 0

        print(f"[{i+1}] {record['timestamp']}")
        print(f"    Spot Price:      {record['spot_price']:.1f}")
        print(f"    Price Change:    {price_change:+.1f} ({price_change_pct:+.2f}%)")
        print(f"    Momentum Score:  {momentum:+.1f}")
        print(f"    Old Verdict:     {record['verdict']}")
        print(f"    New Verdict:     {analysis['verdict']}")
        print(f"    Old Score:       N/A (not stored)")
        print(f"    New Score:       {analysis['combined_score']:+.1f}")
        print(f"    Weight Breakdown:")
        print(f"      - OTM:         {analysis['weights']['otm']*100:.0f}%")
        print(f"      - ATM:         {analysis['weights']['atm']*100:.0f}%")
        print(f"      - ITM:         {analysis['weights']['itm']*100:.0f}%")
        print(f"      - Momentum:    {analysis['weights']['momentum']*100:.0f}%")
        print()

    print("=" * 80)
    print("Test Complete")
    print("=" * 80)
    print()
    print("EXPECTED RESULTS:")
    print("- If price is rising (positive momentum), verdict should shift towards")
    print("  bullish/neutral compared to old verdict")
    print("- If price is falling (negative momentum), verdict should shift towards")
    print("  bearish compared to old verdict")
    print("- Momentum weight should be 20% when price history is available")


def test_momentum_calculation():
    """Test the momentum calculation function directly."""
    print("\n" + "=" * 80)
    print("Testing Momentum Calculation Function")
    print("=" * 80)
    print()

    test_cases = [
        {
            "name": "Rising price (+0.4%)",
            "prices": [
                {"spot_price": 25220.0},
                {"spot_price": 25240.0},
                {"spot_price": 25280.0},
                {"spot_price": 25320.0},
            ],
            "expected_momentum": "Positive (~16-20)"
        },
        {
            "name": "Falling price (-0.4%)",
            "prices": [
                {"spot_price": 25320.0},
                {"spot_price": 25280.0},
                {"spot_price": 25240.0},
                {"spot_price": 25220.0},
            ],
            "expected_momentum": "Negative (~-16 to -20)"
        },
        {
            "name": "Flat price (0%)",
            "prices": [
                {"spot_price": 25300.0},
                {"spot_price": 25302.0},
                {"spot_price": 25298.0},
                {"spot_price": 25300.0},
            ],
            "expected_momentum": "Near zero (~0)"
        }
    ]

    for test in test_cases:
        momentum = calculate_price_momentum(test['prices'])
        price_change_pct = (
            (test['prices'][-1]['spot_price'] - test['prices'][0]['spot_price']) /
            test['prices'][0]['spot_price'] * 100
        )
        print(f"Test: {test['name']}")
        print(f"  Price Change: {price_change_pct:+.2f}%")
        print(f"  Momentum Score: {momentum:+.1f}")
        print(f"  Expected: {test['expected_momentum']}")
        print()


if __name__ == "__main__":
    test_momentum_calculation()
    test_momentum_on_historical_data()
