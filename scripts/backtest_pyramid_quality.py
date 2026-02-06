"""Check if pyramiding adds to winners or losers"""
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('oi_tracker.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute('''
    SELECT detected_at, spot_price, pm_score
    FROM detected_patterns 
    WHERE pattern_type = 'PM_REVERSAL_FROM_EXTREME' AND pm_score > 0
    ORDER BY detected_at
''')
patterns = list(cursor.fetchall())

cursor.execute('SELECT timestamp, spot_price FROM analysis_history ORDER BY timestamp')
price_data = []
for p in cursor.fetchall():
    try:
        price_data.append({'dt': datetime.fromisoformat(p['timestamp']), 'price': p['spot_price']})
    except:
        continue

TARGET, STOPLOSS, TIMEOUT = 40, -50, 120

# Track all trades with pyramiding
all_trades = []
for p in patterns:
    entry_spot = p['spot_price']
    signal_dt = datetime.fromisoformat(p['detected_at'])
    
    exit_price = exit_time = None
    for pd in price_data:
        if pd['dt'] <= signal_dt:
            continue
        mins = (pd['dt'] - signal_dt).total_seconds() / 60
        pnl = pd['price'] - entry_spot
        
        if pnl >= TARGET:
            exit_price, exit_time = pd['price'], pd['dt']
            break
        elif pnl <= STOPLOSS:
            exit_price, exit_time = pd['price'], pd['dt']
            break
        elif mins >= TIMEOUT:
            exit_price, exit_time = pd['price'], pd['dt']
            break
    
    if exit_price:
        all_trades.append({
            'entry_time': signal_dt,
            'exit_time': exit_time,
            'entry': entry_spot,
            'exit': exit_price,
            'pnl': exit_price - entry_spot
        })

# Now group overlapping trades
print("PYRAMID QUALITY ANALYSIS")
print("="*100)
print("Question: When we pyramid, are we adding to WINNERS or LOSERS?")
print("="*100)
print()

# Find trade groups (overlapping trades)
groups = []
used = set()

for i, t1 in enumerate(all_trades):
    if i in used:
        continue
    
    group = [t1]
    used.add(i)
    
    for j, t2 in enumerate(all_trades):
        if j in used:
            continue
        # Check if t2 starts during t1 or any trade in group
        for gt in group:
            if t2['entry_time'] > gt['entry_time'] and t2['entry_time'] < gt['exit_time']:
                group.append(t2)
                used.add(j)
                break
    
    groups.append(group)

# Analyze each group
print(f"Found {len(groups)} trade groups (moves)")
print()

adding_to_winners = 0
adding_to_losers = 0
total_added = 0

for i, group in enumerate(groups):
    if len(group) == 1:
        continue  # Single trade, no pyramiding
    
    # Sort by entry time
    group = sorted(group, key=lambda x: x['entry_time'])
    base_trade = group[0]
    adds = group[1:]
    
    print(f"GROUP {i+1}: {len(group)} trades")
    print(f"  BASE: {base_trade['entry_time'].strftime('%m-%d %H:%M')} @ {base_trade['entry']:.1f} -> P/L: {base_trade['pnl']:+.1f}")
    
    for add in adds:
        # At time of add, what was the base trade's unrealized P/L?
        # Find spot price at add entry time
        add_time = add['entry_time']
        spot_at_add = None
        for pd in price_data:
            if pd['dt'] >= add_time:
                spot_at_add = pd['price']
                break
        
        if spot_at_add:
            base_unrealized = spot_at_add - base_trade['entry']
            status = "WINNING" if base_unrealized > 0 else "LOSING"
            
            print(f"  ADD:  {add['entry_time'].strftime('%m-%d %H:%M')} @ {add['entry']:.1f} -> P/L: {add['pnl']:+.1f}")
            print(f"        Base was at {base_unrealized:+.1f} ({status}) when we added")
            
            total_added += 1
            if base_unrealized > 0:
                adding_to_winners += 1
            else:
                adding_to_losers += 1
    print()

print("="*100)
print("SUMMARY")
print("="*100)
print(f"Total pyramid adds: {total_added}")
print(f"  Added to WINNERS: {adding_to_winners} ({adding_to_winners/total_added*100:.0f}%)")
print(f"  Added to LOSERS:  {adding_to_losers} ({adding_to_losers/total_added*100:.0f}%)")
print()

if adding_to_winners > adding_to_losers:
    print("VERDICT: Pyramiding is GOOD - mostly adding to winners (smart scaling)")
elif adding_to_winners < adding_to_losers:
    print("VERDICT: Pyramiding is BAD - mostly adding to losers (averaging down)")
else:
    print("VERDICT: Mixed - no clear pattern")

conn.close()
