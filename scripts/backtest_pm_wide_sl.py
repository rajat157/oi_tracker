"""Backtest PM Reversal patterns - WIDE SL TEST"""
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('oi_tracker.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute('''
    SELECT detected_at, spot_price, pm_score, pm_change, confidence
    FROM detected_patterns 
    WHERE pattern_type = 'PM_REVERSAL_FROM_EXTREME'
    ORDER BY detected_at
''')
patterns = cursor.fetchall()

cursor.execute('SELECT timestamp, spot_price FROM analysis_history ORDER BY timestamp')
all_prices = cursor.fetchall()

price_data = []
for p in all_prices:
    try:
        dt = datetime.fromisoformat(p['timestamp'])
        price_data.append({'dt': dt, 'price': p['spot_price']})
    except:
        continue

# Test different parameters
configs = [
    {'target': 40, 'sl': -25, 'name': 'Tight (T:40, SL:-25)'},
    {'target': 40, 'sl': -40, 'name': 'Medium (T:40, SL:-40)'},
    {'target': 50, 'sl': -50, 'name': 'Wide (T:50, SL:-50)'},
    {'target': 60, 'sl': -60, 'name': 'Very Wide (T:60, SL:-60)'},
    {'target': 40, 'sl': -100, 'name': 'Hold (T:40, SL:-100)'},
]

print('PM REVERSAL BACKTEST - PARAMETER OPTIMIZATION')
print('='*100)
print('Testing different Target/SL combinations (no pyramiding, 2hr timeout)')
print('='*100)
print()

TIMEOUT_MINS = 120

for cfg in configs:
    TARGET = cfg['target']
    STOPLOSS = cfg['sl']
    
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
        
        if in_trade and trade_end_time and signal_dt < trade_end_time:
            continue
        
        exit_price = None
        exit_reason = None
        exit_time = None
        
        for pd in price_data:
            if pd['dt'] <= signal_dt:
                continue
            
            mins_elapsed = (pd['dt'] - signal_dt).total_seconds() / 60
            current_price = pd['price']
            pnl = current_price - entry_spot
            
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
            continue
        
        pnl = exit_price - entry_spot
        result = 'WIN' if pnl > 0 else 'LOSS'
        
        in_trade = True
        trade_end_time = exit_time
        
        trades.append({
            'pnl': pnl,
            'reason': exit_reason,
            'result': result
        })
    
    total = len(trades)
    if total == 0:
        continue
        
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    losses = total - wins
    total_pnl = sum(t['pnl'] for t in trades)
    avg_pnl = total_pnl / total
    
    targets = sum(1 for t in trades if t['reason'] == 'TARGET')
    sls = sum(1 for t in trades if t['reason'] == 'STOPLOSS')
    timeouts = sum(1 for t in trades if t['reason'] == 'TIMEOUT')
    
    wr = wins/total*100
    
    print(f"{cfg['name']:<25} | Trades: {total:>2} | Win: {wins:>2} ({wr:>4.0f}%) | T:{targets:>2} SL:{sls:>2} TO:{timeouts:>2} | P/L: {total_pnl:>+7.1f} | Avg: {avg_pnl:>+6.1f}")

print()
print('='*100)
print()

# Best config analysis - let's try the hold strategy in detail
print('DETAILED: "HOLD" Strategy (T:40, SL:-100)')
print('-'*80)

TARGET = 40
STOPLOSS = -100

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
    
    if in_trade and trade_end_time and signal_dt < trade_end_time:
        continue
    
    exit_price = None
    exit_reason = None
    exit_time = None
    max_p = entry_spot
    min_p = entry_spot
    
    for pd in price_data:
        if pd['dt'] <= signal_dt:
            continue
        
        mins_elapsed = (pd['dt'] - signal_dt).total_seconds() / 60
        current_price = pd['price']
        pnl = current_price - entry_spot
        max_p = max(max_p, current_price)
        min_p = min(min_p, current_price)
        
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
        continue
    
    pnl = exit_price - entry_spot
    duration = (exit_time - signal_dt).total_seconds() / 60
    
    in_trade = True
    trade_end_time = exit_time
    
    date_str = signal_dt.strftime('%m-%d')
    time_str = signal_dt.strftime('%H:%M')
    result = 'WIN' if pnl > 0 else 'LOSS'
    dd = entry_spot - min_p
    
    print(f"{date_str} {time_str} | Entry: {entry_spot:>7.1f} | Exit: {exit_price:>7.1f} | P/L: {pnl:>+6.1f} | DD: {dd:>5.1f} | {exit_reason:<8} | {result}")
    
    trades.append({'pnl': pnl, 'result': result})

print('-'*80)
total = len(trades)
wins = sum(1 for t in trades if t['result'] == 'WIN')
total_pnl = sum(t['pnl'] for t in trades)
print(f"TOTAL: {total} trades, {wins} wins ({wins/total*100:.0f}%), P/L: {total_pnl:+.1f} pts")

conn.close()
