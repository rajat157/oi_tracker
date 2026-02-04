"""Test script to diagnose self-learner status."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_connection
from self_learner import get_self_learner
import json

print("=" * 60)
print("SELF-LEARNER DIAGNOSTIC")
print("=" * 60)

# 1. Check self-learner initialization
print("\n1. Self-Learner Initialization:")
learner = get_self_learner()
print(f"   EMA Accuracy: {learner.signal_tracker.ema_tracker.ema_accuracy:.1%}")
print(f"   Should Trade: {learner.signal_tracker.ema_tracker.should_trade()}")
print(f"   Is Paused: {learner.signal_tracker.ema_tracker.is_paused}")
print(f"   Consecutive Errors: {learner.signal_tracker.ema_tracker.consecutive_errors}")

# 2. Check signal_outcomes table
print("\n2. Signal Outcomes Table:")
with get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as count FROM signal_outcomes')
    total_signals = cursor.fetchone()['count']
    print(f"   Total signals recorded: {total_signals}")

    cursor.execute('SELECT COUNT(*) as count FROM signal_outcomes WHERE outcome_timestamp IS NOT NULL')
    resolved_signals = cursor.fetchone()['count']
    print(f"   Resolved signals: {resolved_signals}")

    if total_signals > 0:
        print("\n   Recent signals (last 5):")
        cursor.execute('''
            SELECT signal_timestamp, verdict, strength, signal_confidence,
                   was_correct, profit_loss_pct
            FROM signal_outcomes
            ORDER BY signal_timestamp DESC
            LIMIT 5
        ''')
        for row in cursor.fetchall():
            status = "RESOLVED" if row['was_correct'] is not None else "PENDING"
            result = f"({'+' if row['was_correct'] else '-'}{row['profit_loss_pct']:.1f}%)" if row['was_correct'] is not None else ""
            print(f"      {row['signal_timestamp']}: {row['verdict']} ({row['signal_confidence']:.0f}%) - {status} {result}")

# 3. Check learned_weights table
print("\n3. Learned Weights Table:")
with get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as count FROM learned_weights')
    count = cursor.fetchone()['count']
    print(f"   Total weight snapshots: {count}")

    if count > 0:
        cursor.execute('SELECT * FROM learned_weights ORDER BY timestamp DESC LIMIT 1')
        row = cursor.fetchone()
        print(f"   Latest snapshot: {row['timestamp']}")
        print(f"   EMA Accuracy: {row['ema_accuracy'] * 100:.1f}%")
        print(f"   Is Paused: {row['is_paused']}")

# 4. Check analysis_history for self_learning field
print("\n4. Analysis History (Self-Learning Field):")
with get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute('''
        SELECT timestamp, verdict, signal_confidence, analysis_json
        FROM analysis_history
        WHERE date(timestamp) = '2026-02-03'
        ORDER BY timestamp DESC
        LIMIT 5
    ''')
    rows = cursor.fetchall()
    print(f"   Analyses on 2026-02-03: {len(rows)}")

    if rows:
        for row in rows:
            if row['analysis_json']:
                try:
                    analysis = json.loads(row['analysis_json'])
                    self_learning = analysis.get('self_learning', {})
                    print(f"   {row['timestamp']}: {row['verdict']} (conf:{row['signal_confidence']:.0f}%)")
                    print(f"      self_learning: {self_learning}")
                except:
                    print(f"   {row['timestamp']}: Could not parse analysis_json")

# 5. Check trade_setups win rate
print("\n5. Trade Setups Win Rate:")
with get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN status = 'LOST' THEN 1 ELSE 0 END) as losses
        FROM trade_setups
        WHERE status IN ('WON', 'LOST')
          AND created_at >= '2026-02-01'
    ''')
    row = cursor.fetchone()
    total = row['total']
    wins = row['wins']
    losses = row['losses']
    win_rate = (wins / total * 100) if total > 0 else 0

    print(f"   Total resolved trades: {total}")
    print(f"   Wins: {wins}")
    print(f"   Losses: {losses}")
    print(f"   Win Rate: {win_rate:.1f}%")

    if win_rate < 40:
        print(f"   ⚠️  Win rate below 40% threshold - self-learner should pause!")

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
