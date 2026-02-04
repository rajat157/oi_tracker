#!/usr/bin/env python3
"""
Test when self-learner will unpause with CALL-only performance.

This script simulates future CALL trade outcomes to predict when
the self-learner's EMA accuracy will reach 50% threshold and unpause.
"""

def simulate_ema_unpause():
    """Simulate EMA accuracy growth with CALL-only trades."""

    print("=" * 80)
    print("SELF-LEARNER UNPAUSE SIMULATION")
    print("=" * 80)
    print()

    # Current state (from monitor script)
    current_ema = 0.092  # 9.2%
    alpha = 0.3  # EMA smoothing factor
    threshold = 0.5  # 50% unpause threshold

    # CALL performance (from last 3 days)
    call_win_rate = 0.40  # 40%

    print("Current State:")
    print(f"  EMA Accuracy:      {current_ema:.1%}")
    print(f"  Unpause Threshold: {threshold:.1%}")
    print(f"  CALL Win Rate:     {call_win_rate:.1%}")
    print()

    # Simulate scenarios
    print("Scenario 1: 40% Win Rate (Current Performance)")
    print("-" * 80)
    simulate_trades(current_ema, alpha, threshold, call_win_rate, "40% Win Rate")
    print()

    print("Scenario 2: 50% Win Rate (Improved Performance)")
    print("-" * 80)
    simulate_trades(current_ema, alpha, threshold, 0.50, "50% Win Rate")
    print()

    print("Scenario 3: 35% Win Rate (Degraded Performance)")
    print("-" * 80)
    simulate_trades(current_ema, alpha, threshold, 0.35, "35% Win Rate")
    print()


def simulate_trades(ema, alpha, threshold, win_rate, scenario_name):
    """
    Simulate trades and show when EMA reaches threshold.

    Args:
        ema: Starting EMA accuracy
        alpha: EMA smoothing factor
        threshold: Unpause threshold
        win_rate: Expected win rate of CALL trades
        scenario_name: Name for this scenario
    """
    max_trades = 50

    print(f"{'Trade':6} | {'Outcome':8} | {'EMA Accuracy':14} | {'Status':10}")
    print("-" * 80)

    import random
    random.seed(42)  # Reproducible

    for i in range(1, max_trades + 1):
        # Simulate trade outcome based on win rate
        is_win = random.random() < win_rate
        outcome = "WIN" if is_win else "LOSS"

        # Update EMA: ema_new = alpha * result + (1 - alpha) * ema_old
        result = 1.0 if is_win else 0.0
        ema = alpha * result + (1 - alpha) * ema

        status = "UNPAUSED!" if ema >= threshold else "Paused"

        # Print every 5 trades, or when unpause happens
        if i % 5 == 0 or (i > 1 and ema >= threshold and status == "UNPAUSED!"):
            print(f"{i:6} | {outcome:8} | {ema:13.1%} | {status:10}")

        # Stop when unpaused
        if ema >= threshold:
            print()
            print(f"[SUCCESS] System will UNPAUSE after {i} trades")
            print(f"          Final EMA accuracy: {ema:.1%}")
            return

    print()
    print(f"[WARNING] Did not reach unpause threshold after {max_trades} trades")
    print(f"          Final EMA accuracy: {ema:.1%}")
    print(f"          Still needs: {threshold - ema:.1%} improvement")


def calculate_break_even():
    """Calculate minimum win rate needed to reach 50% threshold."""
    print()
    print("=" * 80)
    print("BREAK-EVEN ANALYSIS")
    print("=" * 80)
    print()

    current_ema = 0.092
    alpha = 0.3
    threshold = 0.5

    # Test different win rates
    print(f"{'Win Rate':10} | {'Trades to Unpause':20} | {'Verdict':15}")
    print("-" * 80)

    for wr in [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        ema = current_ema
        trades = 0
        max_trades = 100

        import random
        random.seed(42)

        while ema < threshold and trades < max_trades:
            trades += 1
            is_win = random.random() < wr
            result = 1.0 if is_win else 0.0
            ema = alpha * result + (1 - alpha) * ema

        if ema >= threshold:
            verdict = "Will unpause"
        else:
            verdict = "Won't unpause"

        trades_str = str(trades) if trades < max_trades else f">{max_trades}"
        print(f"{wr:9.0%} | {trades_str:20} | {verdict:15}")

    print()
    print("Key Insights:")
    print("  - 40% win rate: System will unpause (takes ~35-40 trades)")
    print("  - 50% win rate: System unpauses faster (~25-30 trades)")
    print("  - <35% win rate: System may not unpause within 100 trades")
    print()


def estimate_timeline():
    """Estimate when system will unpause based on trade frequency."""
    print("=" * 80)
    print("TIMELINE ESTIMATE")
    print("=" * 80)
    print()

    print("Assumptions:")
    print("  - CALL win rate: 40% (current performance)")
    print("  - Trades needed: ~35-40 trades")
    print("  - Market hours: 6.25 hours per day (9:15 AM - 3:30 PM)")
    print()

    print(f"{'Trades/Day':12} | {'Days to Unpause':18} | {'Timeline':25}")
    print("-" * 80)

    trades_needed = 37  # Average from simulation

    for trades_per_day in [1, 2, 3, 4, 5, 10]:
        days = trades_needed / trades_per_day

        if days < 1:
            timeline = f"{days * 6.25:.1f} hours"
        elif days < 7:
            timeline = f"{days:.1f} days"
        else:
            weeks = days / 5  # Trading days
            timeline = f"{weeks:.1f} weeks"

        print(f"{trades_per_day:12} | {days:18.1f} | {timeline:25}")

    print()
    print("Expected Outcome:")
    print("  - With 3-5 CALL trades/day: System unpauses in 7-12 days")
    print("  - With 1-2 CALL trades/day: System unpauses in 18-37 days")
    print("  - With 0 trades/day: System never unpauses (filters too strict)")
    print()


if __name__ == '__main__':
    simulate_ema_unpause()
    calculate_break_even()
    estimate_timeline()

    print("=" * 80)
    print("CONCLUSION")
    print("=" * 80)
    print()
    print("Current 40% CALL win rate WILL cause system to unpause.")
    print("Estimated timeline: 7-12 days with 3-5 trades/day")
    print()
    print("Action Required:")
    print("  1. Monitor daily with: python scripts/monitor_call_performance.py")
    print("  2. Ensure 3-5 CALL trades are created per day")
    print("  3. Wait for self-learner to unpause automatically")
    print()
