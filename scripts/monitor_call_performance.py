#!/usr/bin/env python3
"""
Monitor CALL-only trading performance after disabling PUT trades.

Usage:
    python scripts/monitor_call_performance.py [--days 7]

This script tracks:
1. CALL trade win rate (target: ≥40%)
2. Self-learner status (should unpause when win rate reaches threshold)
3. Missed bearish opportunities (for future PUT strategy evaluation)
4. Daily performance metrics
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import argparse


def get_db_connection():
    """Get database connection."""
    db_path = Path(__file__).parent.parent / 'oi_tracker.db'
    return sqlite3.connect(str(db_path))


def get_call_performance(days: int = 7) -> Dict:
    """Get CALL trade performance for specified days.

    Args:
        days: Number of days to look back

    Returns:
        Dict with performance metrics
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # Get CALL trades only
    cursor.execute("""
        SELECT
            status,
            profit_loss_pct,
            profit_loss_points,
            created_at,
            activated_at,
            resolved_at,
            signal_confidence,
            verdict_at_creation
        FROM trade_setups
        WHERE direction = 'BUY_CALL'
        AND created_at >= ?
        ORDER BY created_at DESC
    """, (cutoff_date,))

    trades = cursor.fetchall()
    conn.close()

    if not trades:
        return {
            'total': 0,
            'wins': 0,
            'losses': 0,
            'pending': 0,
            'win_rate': 0.0,
            'avg_win_pct': 0.0,
            'avg_loss_pct': 0.0,
            'avg_confidence': 0.0,
            'expectancy': 0.0,
            'trades': []
        }

    wins = [t for t in trades if t[0] == 'WON']
    losses = [t for t in trades if t[0] == 'LOST']
    pending = [t for t in trades if t[0] in ('PENDING', 'ACTIVATED')]

    total_resolved = len(wins) + len(losses)
    win_rate = (len(wins) / total_resolved * 100) if total_resolved > 0 else 0.0

    avg_win_pct = sum(t[1] for t in wins) / len(wins) if wins else 0.0
    avg_loss_pct = sum(t[1] for t in losses) / len(losses) if losses else 0.0
    avg_confidence = sum(t[6] for t in trades) / len(trades) if trades else 0.0

    # Calculate expectancy: (win_rate * avg_win) + (loss_rate * avg_loss)
    loss_rate = (len(losses) / total_resolved * 100) if total_resolved > 0 else 0.0
    expectancy = (win_rate/100 * avg_win_pct) + (loss_rate/100 * avg_loss_pct)

    return {
        'total': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'pending': len(pending),
        'win_rate': win_rate,
        'avg_win_pct': avg_win_pct,
        'avg_loss_pct': avg_loss_pct,
        'avg_confidence': avg_confidence,
        'expectancy': expectancy,
        'trades': trades
    }


def get_missed_bearish_signals(days: int = 7) -> List[Dict]:
    """Get bearish signals that were filtered out (potential PUT trades).

    Args:
        days: Number of days to look back

    Returns:
        List of bearish analyses
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # Get analyses with bearish verdicts (these would have been PUT trades)
    cursor.execute("""
        SELECT
            timestamp,
            verdict,
            signal_confidence,
            spot_price
        FROM analysis_history
        WHERE timestamp >= ?
        AND (verdict LIKE '%Bearish%' OR verdict LIKE '%Bears%')
        ORDER BY timestamp DESC
    """, (cutoff_date,))

    signals = cursor.fetchall()
    conn.close()

    return [
        {
            'timestamp': s[0],
            'verdict': s[1],
            'signal_confidence': s[2],
            'spot_price': s[3]
        }
        for s in signals
    ]


def get_self_learner_status() -> Optional[Dict]:
    """Get current self-learner status.

    Returns:
        Dict with self-learner metrics or None if not available
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            timestamp,
            analysis_json
        FROM analysis_history
        WHERE analysis_json IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 1
    """)

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    import json
    analysis = json.loads(row[1])
    metrics = analysis.get('self_learning', {})

    return {
        'timestamp': row[0],
        'is_paused': metrics.get('is_paused'),
        'ema_accuracy': metrics.get('ema_accuracy'),
        'recent_trades_count': metrics.get('recent_trades_count'),
        'recent_wins': metrics.get('recent_wins'),
        'pause_reason': metrics.get('pause_reason', '')
    }


def print_report(days: int = 7):
    """Print comprehensive monitoring report.

    Args:
        days: Number of days to analyze
    """
    print("=" * 80)
    print(f"CALL-ONLY PERFORMANCE MONITOR")
    print(f"Analysis Period: Last {days} days")
    print(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()

    # 1. CALL Performance
    print("[PERFORMANCE] CALL TRADE PERFORMANCE")
    print("-" * 80)
    perf = get_call_performance(days)

    print(f"Total CALL trades:     {perf['total']}")
    print(f"  - Wins:              {perf['wins']}")
    print(f"  - Losses:            {perf['losses']}")
    print(f"  - Pending/Active:    {perf['pending']}")
    print()
    print(f"Win Rate:              {perf['win_rate']:.1f}% {'[OK]' if perf['win_rate'] >= 40 else '[LOW]'} (Target: >=40%)")
    print(f"Average Win:           +{perf['avg_win_pct']:.2f}%")
    print(f"Average Loss:          {perf['avg_loss_pct']:.2f}%")
    print(f"Average Confidence:    {perf['avg_confidence']:.1f}%")
    print(f"Expectancy:            {perf['expectancy']:.2f}% {'[OK]' if perf['expectancy'] > 0 else '[NEG]'} (Target: >0%)")
    print()

    # Status assessment
    if perf['total'] == 0:
        print("[WARNING] STATUS: No CALL trades created (filters may be too strict)")
    elif perf['win_rate'] >= 40 and perf['expectancy'] > 0:
        print("[SUCCESS] STATUS: Performance GOOD - System should unpause")
    elif perf['win_rate'] < 40:
        print("[WARNING] STATUS: Win rate below threshold - Monitor closely")
    elif perf['expectancy'] <= 0:
        print("[WARNING] STATUS: Negative expectancy - Review filters")

    print()

    # 2. Self-Learner Status
    print("[SELF-LEARNER] STATUS")
    print("-" * 80)
    sl = get_self_learner_status()

    if sl:
        print(f"Last Update:           {sl['timestamp'][:19]}")
        print(f"Is Paused:             {sl['is_paused']} {'[PAUSED - System NOT trading]' if sl['is_paused'] else '[ACTIVE - System trading]'}")
        print(f"EMA Accuracy:          {sl['ema_accuracy']:.1f}%")
        print(f"Recent Trades:         {sl['recent_trades_count']}")
        print(f"Recent Wins:           {sl['recent_wins']}")
        if sl['pause_reason']:
            print(f"Pause Reason:          {sl['pause_reason']}")
    else:
        print("[WARNING] No self-learner data available")

    print()

    # 3. Missed Bearish Opportunities
    print("[BEARISH] MISSED BEARISH SIGNALS (Filtered PUT trades)")
    print("-" * 80)
    missed = get_missed_bearish_signals(days)

    print(f"Total Bearish Signals: {len(missed)}")
    if missed:
        print(f"Average Confidence:    {sum(s['signal_confidence'] for s in missed) / len(missed):.1f}%")
        print()
        print("Recent bearish signals that were NOT traded:")
        print(f"{'Timestamp':20} | {'Verdict':30} | {'Confidence':10}")
        print("-" * 80)
        for signal in missed[:10]:
            print(f"{signal['timestamp'][:19]:20} | {signal['verdict']:30} | {signal['signal_confidence']:8.1f}%")

    print()

    # 4. Daily Breakdown
    if perf['trades']:
        print("[DAILY] BREAKDOWN (CALL trades)")
        print("-" * 80)

        # Group by date
        daily = {}
        for trade in perf['trades']:
            date = trade[3][:10]  # created_at date
            if date not in daily:
                daily[date] = {'wins': 0, 'losses': 0, 'pending': 0}

            status = trade[0]
            if status == 'WON':
                daily[date]['wins'] += 1
            elif status == 'LOST':
                daily[date]['losses'] += 1
            else:
                daily[date]['pending'] += 1

        print(f"{'Date':12} | {'Trades':8} | {'Wins':6} | {'Losses':8} | {'Win Rate':10}")
        print("-" * 80)
        for date in sorted(daily.keys(), reverse=True):
            d = daily[date]
            total = d['wins'] + d['losses']
            wr = (d['wins'] / total * 100) if total > 0 else 0.0
            print(f"{date:12} | {d['wins'] + d['losses'] + d['pending']:8} | {d['wins']:6} | {d['losses']:8} | {wr:8.1f}%")

    print()
    print("=" * 80)
    print()

    # 5. Recommendations
    print("[RECOMMENDATIONS]")
    print("-" * 80)

    if perf['total'] == 0:
        print("[WARNING] No CALL trades created")
        print("   -> Check if filters are too strict (lines 224-229 in trade_tracker.py)")
        print("   -> Verify market regime detection is working")
        print("   -> Review recent analyses for signal generation issues")
    elif perf['win_rate'] >= 40 and perf['expectancy'] > 0:
        print("[SUCCESS] CALL-only strategy performing well")
        print("   -> Continue monitoring for 2 weeks")
        print("   -> Track missed bearish opportunities")
        print("   -> Wait for self-learner to unpause")
    elif perf['win_rate'] < 35:
        print("[WARNING] Win rate below expected range")
        print("   -> Investigate CALL signal quality")
        print("   -> Review confidence calculation (higher != better?)")
        print("   -> Consider adjusting entry timing or stop loss")

    if len(missed) > perf['total'] * 2:
        print(f"\n[WARNING] Missing {len(missed)} bearish signals vs {perf['total']} CALL trades")
        print("   -> Track hypothetical PUT outcomes")
        print("   -> Evaluate opportunity cost of disabling PUTs")

    print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Monitor CALL-only trading performance'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='Number of days to analyze (default: 7)'
    )

    args = parser.parse_args()

    try:
        print_report(days=args.days)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == '__main__':
    exit(main())
