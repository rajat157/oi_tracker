"""Analyze: Is pyramiding real edge or overcounting?"""
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('oi_tracker.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get all PM reversal patterns with PM > 0 (our profitable filter)
cursor.execute('''
    SELECT detected_at, spot_price, pm_score, pm_change, confidence
    FROM detected_patterns 
    WHERE pattern_type = 'PM_REVERSAL_FROM_EXTREME'
      AND pm_score > 0
    ORDER BY detected_at
''')
patterns = list(cursor.fetchall())

cursor.execute('SELECT timestamp, spot_price FROM analysis_history ORDER BY timestamp')
all_prices = cursor.fetchall()

price_data = []
for p in all_prices:
    try:
        dt = datetime.fromisoformat(p['timestamp'])
        price_data.append({'dt': dt, 'price': p['spot_price']})
    except:
        continue

print("="*100)
print("PYRAMIDING ANALYSIS: Is it real edge or same-move overcounting?")
print("="*100)
print()

# First, analyze signal clustering
print("SIGNAL TIMING ANALYSIS (PM > 0 filter)")
print("-"*80)

prev_dt = None
for p in patterns:
    dt = datetime.fromisoformat(p['detected_at'])
    spot = p['spot_price']
    pm = p['pm_score']
    
    gap = ""
    if prev_dt:
        gap_mins = (dt - prev_dt).total_seconds() / 60
        gap = f"(+{gap_mins:.0f}min)" if gap_mins < 120 else f"(+{gap_mins/60:.1f}hr)"
    
    print(f"{dt.strftime('%m-%d %H:%M')} | Spot: {spot:>8.1f} | PM: {pm:>+6.1f} | Gap: {gap}")
    prev_dt = dt

print()
print("="*100)
print("STRATEGY COMPARISON")
print("="*100)

TARGET = 40
STOPLOSS = -50
TIMEOUT = 120

def run_backtest(patterns, allow_pyramid=False, min_gap_mins=0):
    """Run backtest with optional pyramiding"""
    trades = []
    open_trades = []  # For pyramiding
    
    for p in patterns:
        detected = p['detected_at']
        entry_spot = p['spot_price']
        signal_dt = datetime.fromisoformat(detected)
        
        # Check if we can take this trade
        can_trade = True
        
        if not allow_pyramid:
            # No pyramid: skip if any trade is open
            for ot in open_trades:
                if signal_dt < ot['exit_time']:
                    can_trade = False
                    break
        else:
            # With pyramid: only skip if signal is too close to last entry
            if open_trades and min_gap_mins > 0:
                active = [ot for ot in open_trades if signal_dt < ot['exit_time']]
                if active:
                    last_entry = max(ot['entry_time'] for ot in active)
                    if (signal_dt - last_entry).total_seconds() / 60 < min_gap_mins:
                        can_trade = False
        
        if not can_trade:
            continue
        
        # Find exit
        exit_price = None
        exit_time = None
        max_price = entry_spot
        
        for pd in price_data:
            if pd['dt'] <= signal_dt:
                continue
            
            mins = (pd['dt'] - signal_dt).total_seconds() / 60
            pnl = pd['price'] - entry_spot
            max_price = max(max_price, pd['price'])
            
            if pnl >= TARGET:
                exit_price = pd['price']
                exit_time = pd['dt']
                break
            elif pnl <= STOPLOSS:
                exit_price = pd['price']
                exit_time = pd['dt']
                break
            elif mins >= TIMEOUT:
                exit_price = pd['price']
                exit_time = pd['dt']
                break
        
        if exit_price is None:
            continue
        
        pnl = exit_price - entry_spot
        trades.append({
            'entry_time': signal_dt,
            'exit_time': exit_time,
            'entry': entry_spot,
            'exit': exit_price,
            'pnl': pnl,
            'max': max_price
        })
        
        open_trades.append({
            'entry_time': signal_dt,
            'exit_time': exit_time
        })
    
    return trades

# Test different strategies
strategies = [
    ('No Pyramid', False, 0),
    ('Pyramid (no gap)', True, 0),
    ('Pyramid (15min gap)', True, 15),
    ('Pyramid (30min gap)', True, 30),
    ('Pyramid (60min gap)', True, 60),
]

print()
print(f"{'Strategy':<25} | {'Trades':>6} | {'Wins':>6} | {'Win%':>6} | {'Total P/L':>10} | {'Avg P/L':>8}")
print("-"*80)

for name, allow_pyr, gap in strategies:
    trades = run_backtest(patterns, allow_pyr, gap)
    total = len(trades)
    if total == 0:
        print(f"{name:<25} | {0:>6} | {'-':>6} | {'-':>6} | {'-':>10} | {'-':>8}")
        continue
    
    wins = sum(1 for t in trades if t['pnl'] > 0)
    total_pnl = sum(t['pnl'] for t in trades)
    avg_pnl = total_pnl / total
    wr = wins/total*100
    
    print(f"{name:<25} | {total:>6} | {wins:>6} | {wr:>5.0f}% | {total_pnl:>+10.1f} | {avg_pnl:>+8.1f}")

# Now analyze if pyramiding adds unique value
print()
print("="*100)
print("UNIQUE MOVE ANALYSIS: Does pyramiding capture different moves?")
print("="*100)

# Get trades with pyramiding
pyr_trades = run_backtest(patterns, True, 0)
no_pyr_trades = run_backtest(patterns, False, 0)

print()
print("NO PYRAMID trades:")
for t in no_pyr_trades:
    dt = t['entry_time'].strftime('%m-%d %H:%M')
    print(f"  {dt} | Entry: {t['entry']:>7.1f} | Exit: {t['exit']:>7.1f} | P/L: {t['pnl']:>+6.1f}")

print()
print("PYRAMID trades (showing which ones OVERLAP with no-pyramid):")
for t in pyr_trades:
    dt = t['entry_time'].strftime('%m-%d %H:%M')
    
    # Check if this overlaps with a no-pyramid trade
    overlap = "NEW"
    for npt in no_pyr_trades:
        if t['entry_time'] >= npt['entry_time'] and t['entry_time'] < npt['exit_time']:
            overlap = "OVERLAP"
            break
    
    print(f"  {dt} | Entry: {t['entry']:>7.1f} | Exit: {t['exit']:>7.1f} | P/L: {t['pnl']:>+6.1f} | {overlap}")

# Count new vs overlap
overlap_count = 0
new_count = 0
overlap_pnl = 0
new_pnl = 0

for t in pyr_trades:
    is_overlap = False
    for npt in no_pyr_trades:
        if t['entry_time'] >= npt['entry_time'] and t['entry_time'] < npt['exit_time']:
            is_overlap = True
            break
    
    if is_overlap:
        overlap_count += 1
        overlap_pnl += t['pnl']
    else:
        new_count += 1
        new_pnl += t['pnl']

print()
print(f"OVERLAP trades: {overlap_count} (P/L: {overlap_pnl:+.1f})")
print(f"NEW trades:     {new_count} (P/L: {new_pnl:+.1f})")
print()

if overlap_pnl > 0 and new_pnl > 0:
    print("VERDICT: Pyramiding captures BOTH overlapping AND new moves profitably")
elif overlap_pnl > 0:
    print("VERDICT: Pyramiding mainly profits from same-move overcounting")
elif new_pnl > 0:
    print("VERDICT: Pyramiding captures genuinely new moves")
else:
    print("VERDICT: Pyramiding doesn't add value")

conn.close()
