"""Analyze why 75% of trades are losing - root cause diagnostic."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_connection
from datetime import datetime, timedelta

print("=" * 80)
print("TRADE LOSS ROOT CAUSE ANALYSIS")
print("=" * 80)

with get_connection() as conn:
    cursor = conn.cursor()

    # Get all resolved trades from Feb 1-3
    cursor.execute('''
        SELECT *
        FROM trade_setups
        WHERE status IN ('WON', 'LOST', 'CANCELLED', 'EXPIRED')
          AND created_at >= '2026-02-01'
        ORDER BY created_at ASC
    ''')
    trades = [dict(row) for row in cursor.fetchall()]

print(f"\n{'='*80}")
print(f"OVERVIEW: {len(trades)} Resolved Trades (Feb 1-3)")
print(f"{'='*80}")

won = [t for t in trades if t['status'] == 'WON']
lost = [t for t in trades if t['status'] == 'LOST']
cancelled = [t for t in trades if t['status'] == 'CANCELLED']
expired = [t for t in trades if t['status'] == 'EXPIRED']

print(f"WON:       {len(won)} trades")
print(f"LOST:      {len(lost)} trades")
print(f"CANCELLED: {len(cancelled)} trades")
print(f"EXPIRED:   {len(expired)} trades")
print(f"Win Rate:  {len(won)}/{len(won)+len(lost)} = {len(won)/(len(won)+len(lost))*100 if (len(won)+len(lost))>0 else 0:.1f}%")

# 1. ENTRY EXECUTION ANALYSIS
print(f"\n{'='*80}")
print("1. ENTRY EXECUTION ANALYSIS")
print(f"{'='*80}")

never_activated = [t for t in trades if t['activated_at'] is None and t['status'] in ('LOST', 'EXPIRED')]
activated = [t for t in trades if t['activated_at'] is not None]

print(f"Never Activated (stuck in PENDING): {len(never_activated)}/{len(trades)} ({len(never_activated)/len(trades)*100:.1f}%)")
print(f"Successfully Activated:              {len(activated)}/{len(trades)} ({len(activated)/len(trades)*100:.1f}%)")

if never_activated:
    print("\nTrades that never activated:")
    for t in never_activated:
        print(f"  #{t['id']}: {t['direction']} {t['strike']} @ {t['entry_premium']:.2f}")
        print(f"     Created: {t['created_at']}, Resolved: {t['resolved_at']}, Status: {t['status']}")
        print(f"     Verdict: {t['verdict_at_creation']}, Confidence: {t['signal_confidence']:.0f}%")

# Calculate activation slippage
if activated:
    slippages = [t['activation_premium'] - t['entry_premium'] for t in activated if t['activation_premium']]
    avg_slippage = sum(slippages) / len(slippages) if slippages else 0
    print(f"\nAverage Entry Slippage: {avg_slippage:+.2f} points")

# 2. STOP LOSS ANALYSIS
print(f"\n{'='*80}")
print("2. STOP LOSS ANALYSIS")
print(f"{'='*80}")

hit_sl = [t for t in lost if t.get('hit_sl')]
hit_target = [t for t in won if t.get('hit_target')]

print(f"Lost trades that hit SL:    {len(hit_sl)}/{len(lost)}")
print(f"Won trades that hit Target: {len(hit_target)}/{len(won)}")

if activated:
    # Calculate time to resolution
    for t in activated:
        if t['activated_at'] and t['resolved_at']:
            activated_time = datetime.fromisoformat(t['activated_at'])
            resolved_time = datetime.fromisoformat(t['resolved_at'])
            duration = (resolved_time - activated_time).total_seconds() / 60  # minutes
            t['duration_minutes'] = duration
        else:
            t['duration_minutes'] = None

    won_with_duration = [t for t in won if t.get('duration_minutes')]
    lost_with_duration = [t for t in lost if t.get('duration_minutes')]

    if won_with_duration:
        avg_won_duration = sum(t['duration_minutes'] for t in won_with_duration) / len(won_with_duration)
        print(f"\nAverage time to WIN:  {avg_won_duration:.1f} minutes")

    if lost_with_duration:
        avg_lost_duration = sum(t['duration_minutes'] for t in lost_with_duration) / len(lost_with_duration)
        print(f"Average time to LOSS: {avg_lost_duration:.1f} minutes")

# 3. DIRECTION ANALYSIS
print(f"\n{'='*80}")
print("3. DIRECTION ANALYSIS (BUY_CALL vs BUY_PUT)")
print(f"{'='*80}")

call_trades = [t for t in trades if t['direction'] == 'BUY_CALL' and t['status'] in ('WON', 'LOST')]
put_trades = [t for t in trades if t['direction'] == 'BUY_PUT' and t['status'] in ('WON', 'LOST')]

call_won = [t for t in call_trades if t['status'] == 'WON']
put_won = [t for t in put_trades if t['status'] == 'WON']

print(f"BUY_CALL: {len(call_won)}/{len(call_trades)} wins = {len(call_won)/len(call_trades)*100 if call_trades else 0:.1f}% win rate")
print(f"BUY_PUT:  {len(put_won)}/{len(put_trades)} wins = {len(put_won)/len(put_trades)*100 if put_trades else 0:.1f}% win rate")

# Show breakdown by day and direction
print("\nDaily Breakdown:")
for date in ['2026-02-01', '2026-02-02', '2026-02-03']:
    day_trades = [t for t in trades if t['created_at'].startswith(date) and t['status'] in ('WON', 'LOST')]
    if day_trades:
        day_calls = [t for t in day_trades if t['direction'] == 'BUY_CALL']
        day_puts = [t for t in day_trades if t['direction'] == 'BUY_PUT']
        day_won = [t for t in day_trades if t['status'] == 'WON']

        print(f"\n  {date}:")
        print(f"    Total: {len(day_won)}/{len(day_trades)} wins = {len(day_won)/len(day_trades)*100 if day_trades else 0:.1f}%")
        if day_calls:
            call_wins = len([t for t in day_calls if t['status'] == 'WON'])
            print(f"    CALLS: {call_wins}/{len(day_calls)} wins = {call_wins/len(day_calls)*100:.1f}%")
        if day_puts:
            put_wins = len([t for t in day_puts if t['status'] == 'WON'])
            print(f"    PUTS:  {put_wins}/{len(day_puts)} wins = {put_wins/len(day_puts)*100:.1f}%")

# 4. CONFIDENCE VS OUTCOME
print(f"\n{'='*80}")
print("4. CONFIDENCE VS OUTCOME")
print(f"{'='*80}")

won_resolved = [t for t in won if 'signal_confidence' in t]
lost_resolved = [t for t in lost if 'signal_confidence' in t]

if won_resolved:
    avg_won_conf = sum(t['signal_confidence'] for t in won_resolved) / len(won_resolved)
    print(f"Average confidence of WINNING trades: {avg_won_conf:.1f}%")

if lost_resolved:
    avg_lost_conf = sum(t['signal_confidence'] for t in lost_resolved) / len(lost_resolved)
    print(f"Average confidence of LOSING trades:  {avg_lost_conf:.1f}%")

# 5. PROFIT/LOSS MAGNITUDE
print(f"\n{'='*80}")
print("5. PROFIT/LOSS MAGNITUDE")
print(f"{'='*80}")

if won:
    won_pnl = [t['profit_loss_pct'] for t in won if t.get('profit_loss_pct')]
    if won_pnl:
        avg_win = sum(won_pnl) / len(won_pnl)
        max_win = max(won_pnl)
        print(f"Average WIN:  {avg_win:+.2f}% (max: {max_win:+.2f}%)")

if lost:
    lost_pnl = [t['profit_loss_pct'] for t in lost if t.get('profit_loss_pct')]
    if lost_pnl:
        avg_loss = sum(lost_pnl) / len(lost_pnl)
        max_loss = min(lost_pnl)
        print(f"Average LOSS: {avg_loss:+.2f}% (worst: {max_loss:+.2f}%)")

    if won_pnl and lost_pnl:
        avg_win = sum(won_pnl) / len(won_pnl)
        avg_loss = sum(lost_pnl) / len(lost_pnl)
        expectancy = (len(won)/(len(won)+len(lost))) * avg_win + (len(lost)/(len(won)+len(lost))) * avg_loss
        print(f"\nExpectancy: {expectancy:+.2f}% per trade")
        if expectancy < 0:
            print("  [NEGATIVE - System is losing money on average]")

# 6. MONEYNESS ANALYSIS
print(f"\n{'='*80}")
print("6. MONEYNESS ANALYSIS (ATM/ITM/OTM)")
print(f"{'='*80}")

for moneyness in ['ATM', 'ITM', 'OTM']:
    m_trades = [t for t in trades if t.get('moneyness') == moneyness and t['status'] in ('WON', 'LOST')]
    if m_trades:
        m_won = [t for t in m_trades if t['status'] == 'WON']
        print(f"{moneyness}: {len(m_won)}/{len(m_trades)} wins = {len(m_won)/len(m_trades)*100:.1f}%")

# 7. DETAILED TRADE LIST
print(f"\n{'='*80}")
print("7. DETAILED TRADE LIST")
print(f"{'='*80}")

for t in trades:
    status_icon = "✓" if t['status'] == 'WON' else "✗" if t['status'] == 'LOST' else "○"
    pnl = f"{t['profit_loss_pct']:+.1f}%" if t.get('profit_loss_pct') else "N/A"
    activated = "Y" if t['activated_at'] else "N"

    print(f"\n#{t['id']} [{status_icon}] {t['direction']} {t['strike']} {t['option_type']} ({t['moneyness']})")
    print(f"  Created: {t['created_at'][:19]}")
    print(f"  Entry: {t['entry_premium']:.2f}, SL: {t['sl_premium']:.2f}, T1: {t['target1_premium']:.2f}")
    print(f"  Activated: {activated}, Status: {t['status']}, P/L: {pnl}")
    print(f"  Verdict: {t['verdict_at_creation']}, Confidence: {t['signal_confidence']:.0f}%")
    if t.get('trade_reasoning'):
        print(f"  Reasoning: {t['trade_reasoning']}")

print(f"\n{'='*80}")
print("ANALYSIS COMPLETE")
print(f"{'='*80}")
