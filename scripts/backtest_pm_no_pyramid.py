"""Backtest PM Reversal patterns - NO PYRAMIDING"""
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('oi_tracker.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get all PM reversal patterns
cursor.execute('''
    SELECT detected_at, spot_price, pm_score, pm_change, confidence
    FROM detected_patterns 
    WHERE pattern_type = 'PM_REVERSAL_FROM_EXTREME'
    ORDER BY detected_at
''')
patterns = cursor.fetchall()

# Get all price data for tracking
cursor.execute('''
    SELECT timestamp, spot_price FROM analysis_history
    ORDER BY timestamp
''')
all_prices = cursor.fetchall()

# Convert to list of dicts with parsed timestamps
price_data = []
for p in all_prices:
    try:
        dt = datetime.fromisoformat(p['timestamp'])
        price_data.append({'dt': dt, 'price': p['spot_price']})
    except:
        continue

print('PM REVERSAL BACKTEST - NO PYRAMIDING')
print('='*120)
print('Rules: Target +40pts, SL -25pts, Timeout 2hrs, One trade at a time')
print('='*120)
print(f"{'#':<3} {'Date':<12} {'Time':<8} {'Entry':>8} {'Exit':>8} {'P/L':>8} {'Duration':<12} {'Exit Reason':<15} {'Result':<8}")
print('-'*120)

# Trading parameters
TARGET = 40  # pts
STOPLOSS = -25  # pts
TIMEOUT_MINS = 120

# Track trades
trades = []
in_trade = False
trade_end_time = None

for p in patterns:
    detected = p['detected_at']
    entry_spot = p['spot_price']
    
    try:
        signal_dt = datetime.fromisoformat(detected)
    except:
        continue
    
    # Skip if we're still in a trade
    if in_trade and trade_end_time and signal_dt < trade_end_time:
        continue
    
    # Find exit point
    exit_price = None
    exit_reason = None
    exit_time = None
    max_price = entry_spot
    min_price = entry_spot
    
    for pd in price_data:
        if pd['dt'] <= signal_dt:
            continue
        
        mins_elapsed = (pd['dt'] - signal_dt).total_seconds() / 60
        current_price = pd['price']
        pnl = current_price - entry_spot
        
        max_price = max(max_price, current_price)
        min_price = min(min_price, current_price)
        
        # Check exit conditions
        if pnl >= TARGET:
            exit_price = current_price
            exit_reason = 'TARGET'
            exit_time = pd['dt']
            break
        elif pnl <= STOPLOSS:
            exit_price = current_price
            exit_reason = 'STOPLOSS'
            exit_time = pd['dt']
            break
        elif mins_elapsed >= TIMEOUT_MINS:
            exit_price = current_price
            exit_reason = 'TIMEOUT'
            exit_time = pd['dt']
            break
    
    if exit_price is None:
        # No exit found (end of data)
        continue
    
    # Record trade
    pnl = exit_price - entry_spot
    duration_mins = (exit_time - signal_dt).total_seconds() / 60
    result = 'WIN' if pnl > 0 else 'LOSS'
    
    in_trade = True
    trade_end_time = exit_time
    
    trades.append({
        'entry_time': signal_dt,
        'entry': entry_spot,
        'exit': exit_price,
        'pnl': pnl,
        'duration': duration_mins,
        'reason': exit_reason,
        'result': result,
        'max': max_price,
        'min': min_price
    })
    
    date_str = signal_dt.strftime('%Y-%m-%d')
    time_str = signal_dt.strftime('%H:%M')
    dur_str = f"{int(duration_mins)}min"
    
    print(f"{len(trades):<3} {date_str:<12} {time_str:<8} {entry_spot:>8.1f} {exit_price:>8.1f} {pnl:>+8.1f} {dur_str:<12} {exit_reason:<15} {result:<8}")

print('-'*120)
print()

# Summary
total = len(trades)
wins = sum(1 for t in trades if t['result'] == 'WIN')
losses = total - wins
total_pnl = sum(t['pnl'] for t in trades)
avg_pnl = total_pnl / total if total else 0

targets_hit = sum(1 for t in trades if t['reason'] == 'TARGET')
sl_hit = sum(1 for t in trades if t['reason'] == 'STOPLOSS')
timeouts = sum(1 for t in trades if t['reason'] == 'TIMEOUT')

avg_win = sum(t['pnl'] for t in trades if t['result'] == 'WIN') / wins if wins else 0
avg_loss = sum(t['pnl'] for t in trades if t['result'] == 'LOSS') / losses if losses else 0

print('='*60)
print('SUMMARY - NO PYRAMIDING')
print('='*60)
print(f"Total Trades:     {total}")
print(f"Wins:             {wins} ({wins/total*100:.0f}%)" if total else "")
print(f"Losses:           {losses} ({losses/total*100:.0f}%)" if total else "")
print()
print(f"Target Hit:       {targets_hit}")
print(f"Stoploss Hit:     {sl_hit}")
print(f"Timeout:          {timeouts}")
print()
print(f"Total P/L:        {total_pnl:+.1f} pts")
print(f"Avg P/L per trade:{avg_pnl:+.1f} pts")
print(f"Avg Win:          {avg_win:+.1f} pts")
print(f"Avg Loss:         {avg_loss:+.1f} pts")
print()

if wins and losses:
    rr = abs(avg_win / avg_loss) if avg_loss else 0
    print(f"Risk:Reward:      1:{rr:.1f}")
    
    # Expectancy
    expectancy = (wins/total * avg_win) + (losses/total * avg_loss)
    print(f"Expectancy:       {expectancy:+.1f} pts/trade")

# Per day breakdown
print()
print('='*60)
print('PER DAY BREAKDOWN')
print('='*60)
by_date = {}
for t in trades:
    d = t['entry_time'].strftime('%Y-%m-%d')
    if d not in by_date:
        by_date[d] = {'trades': 0, 'pnl': 0, 'wins': 0}
    by_date[d]['trades'] += 1
    by_date[d]['pnl'] += t['pnl']
    if t['result'] == 'WIN':
        by_date[d]['wins'] += 1

for date, stats in sorted(by_date.items()):
    wr = stats['wins']/stats['trades']*100 if stats['trades'] else 0
    print(f"{date}: {stats['trades']} trades, {stats['wins']} wins ({wr:.0f}%), P/L: {stats['pnl']:+.1f} pts")

conn.close()
