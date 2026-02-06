"""Backtest PM Reversal patterns"""
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

print('PM REVERSAL BACKTEST RESULTS')
print('='*110)
print(f"{'Date':<12} {'Time':<8} {'Entry':>8} {'15min':>8} {'30min':>8} {'60min':>8} {'Max2hr':>8} {'P/L 30m':>8} {'P/L Max':>8} {'Result':<8}")
print('-'*110)

wins_30m = 0
wins_60m = 0
wins_max = 0
total = 0
total_pl_max = 0

results = []

for p in patterns:
    detected = p['detected_at']
    entry_spot = p['spot_price']
    
    # Parse timestamp
    try:
        dt = datetime.fromisoformat(detected)
    except:
        continue
    
    # Get subsequent prices
    cursor.execute('''
        SELECT timestamp, spot_price FROM analysis_history
        WHERE timestamp > ? AND timestamp <= ?
        ORDER BY timestamp
    ''', (detected, (dt + timedelta(hours=2)).isoformat()))
    
    subsequent = cursor.fetchall()
    if not subsequent:
        continue
    
    total += 1
    
    # Find prices at different intervals
    spot_15m = spot_30m = spot_60m = None
    max_spot = entry_spot
    min_spot = entry_spot
    
    for s in subsequent:
        try:
            s_dt = datetime.fromisoformat(s['timestamp'])
            s_price = s['spot_price']
            
            mins_elapsed = (s_dt - dt).total_seconds() / 60
            
            if mins_elapsed <= 18 and spot_15m is None:
                spot_15m = s_price
            if mins_elapsed <= 33:
                spot_30m = s_price
            if mins_elapsed <= 63:
                spot_60m = s_price
            
            max_spot = max(max_spot, s_price)
            min_spot = min(min_spot, s_price)
        except:
            continue
    
    # Calculate P/L
    pl_30m = (spot_30m - entry_spot) if spot_30m else 0
    pl_max = max_spot - entry_spot
    drawdown = entry_spot - min_spot
    
    total_pl_max += pl_max
    
    # Determine result (win = gained 20+ points)
    result = 'WIN' if pl_max >= 20 else 'LOSS' if pl_max < -20 else 'FLAT'
    
    if pl_30m >= 15: wins_30m += 1
    if spot_60m and (spot_60m - entry_spot) >= 15: wins_60m += 1
    if pl_max >= 20: wins_max += 1
    
    date_str = dt.strftime('%Y-%m-%d')
    time_str = dt.strftime('%H:%M')
    
    results.append({
        'date': date_str,
        'time': time_str,
        'entry': entry_spot,
        '15m': spot_15m,
        '30m': spot_30m,
        '60m': spot_60m,
        'max': max_spot,
        'min': min_spot,
        'pl_30m': pl_30m,
        'pl_max': pl_max,
        'drawdown': drawdown,
        'result': result
    })
    
    print(f"{date_str:<12} {time_str:<8} {entry_spot:>8.1f} {spot_15m or 0:>8.1f} {spot_30m or 0:>8.1f} {spot_60m or 0:>8.1f} {max_spot:>8.1f} {pl_30m:>+8.1f} {pl_max:>+8.1f} {result:<8}")

print('-'*110)
print()
print('='*60)
print('SUMMARY STATISTICS')
print('='*60)
print(f"Total PM Reversal Signals: {total}")
print()
print("WIN RATES (CALL entry at signal):")
print(f"  30min hold (+15pts): {wins_30m}/{total} = {wins_30m/total*100:.0f}%" if total else "")
print(f"  60min hold (+15pts): {wins_60m}/{total} = {wins_60m/total*100:.0f}%" if total else "")
print(f"  Max 2hr (+20pts):    {wins_max}/{total} = {wins_max/total*100:.0f}%" if total else "")
print()
print("P/L ANALYSIS:")
avg_pl = total_pl_max / total if total else 0
print(f"  Average Max P/L: {avg_pl:+.1f} pts")
print(f"  Total Max P/L:   {total_pl_max:+.1f} pts")
print()

# Best and worst
if results:
    best = max(results, key=lambda x: x['pl_max'])
    worst = min(results, key=lambda x: x['pl_max'])
    print(f"  Best signal:  {best['date']} {best['time']} -> {best['pl_max']:+.1f} pts")
    print(f"  Worst signal: {worst['date']} {worst['time']} -> {worst['pl_max']:+.1f} pts")

# Filter by high confidence
high_conf_wins = sum(1 for r in results if r['pl_max'] >= 20)
print()
print("BY RESULT:")
wins = sum(1 for r in results if r['result'] == 'WIN')
losses = sum(1 for r in results if r['result'] == 'LOSS')
flats = sum(1 for r in results if r['result'] == 'FLAT')
print(f"  WIN (+20pts):  {wins}")
print(f"  FLAT:          {flats}")
print(f"  LOSS (-20pts): {losses}")

conn.close()
