"""Backtest PM Reversal - FILTERED signals only"""
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('oi_tracker.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute('SELECT timestamp, spot_price FROM analysis_history ORDER BY timestamp')
all_prices = cursor.fetchall()

price_data = []
for p in all_prices:
    try:
        dt = datetime.fromisoformat(p['timestamp'])
        price_data.append({'dt': dt, 'price': p['spot_price']})
    except:
        continue

TIMEOUT_MINS = 120

filters = [
    {'name': 'ALL SIGNALS', 'pm_min': -999, 'conf_min': 0},
    {'name': 'PM > 0 (positive)', 'pm_min': 0, 'conf_min': 0},
    {'name': 'PM > 50 (strong)', 'pm_min': 50, 'conf_min': 0},
    {'name': 'Conf >= 80%', 'pm_min': -999, 'conf_min': 80},
    {'name': 'PM > 50 + Conf >= 80%', 'pm_min': 50, 'conf_min': 80},
    {'name': 'PM > 70 (very strong)', 'pm_min': 70, 'conf_min': 0},
    {'name': 'PM_change > 100', 'pm_min': -999, 'conf_min': 0, 'pm_change_min': 100},
]

print('PM REVERSAL BACKTEST - FILTERED SIGNALS')
print('='*110)
print('Target: 40pts, SL: -50pts, Timeout: 2hrs, No Pyramiding')
print('='*110)
print()

TARGET = 40
STOPLOSS = -50

for filt in filters:
    pm_min = filt['pm_min']
    conf_min = filt['conf_min']
    pm_change_min = filt.get('pm_change_min', -999)
    
    # Get filtered patterns
    cursor.execute('''
        SELECT detected_at, spot_price, pm_score, pm_change, confidence
        FROM detected_patterns 
        WHERE pattern_type = 'PM_REVERSAL_FROM_EXTREME'
          AND pm_score >= ?
          AND confidence >= ?
          AND pm_change >= ?
        ORDER BY detected_at
    ''', (pm_min, conf_min, pm_change_min))
    patterns = cursor.fetchall()
    
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
        
        trades.append({'pnl': pnl, 'reason': exit_reason, 'result': result})
    
    total = len(trades)
    if total == 0:
        print(f"{filt['name']:<30} | No trades")
        continue
        
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    losses = total - wins
    total_pnl = sum(t['pnl'] for t in trades)
    avg_pnl = total_pnl / total
    wr = wins/total*100
    
    targets = sum(1 for t in trades if t['reason'] == 'TARGET')
    sls = sum(1 for t in trades if t['reason'] == 'STOPLOSS')
    
    status = "PROFIT" if total_pnl > 0 else "LOSS"
    print(f"{filt['name']:<30} | Trades: {total:>2} | Win: {wins}/{total} ({wr:>4.0f}%) | P/L: {total_pnl:>+7.1f} | Avg: {avg_pnl:>+6.1f} {status}")

# Now let's test different SL values with the best filter
print()
print('='*110)
print('OPTIMIZING SL for PM > 50 filter')
print('='*110)

cursor.execute('''
    SELECT detected_at, spot_price, pm_score, pm_change, confidence
    FROM detected_patterns 
    WHERE pattern_type = 'PM_REVERSAL_FROM_EXTREME'
      AND pm_score >= 50
    ORDER BY detected_at
''')
strong_patterns = cursor.fetchall()

for sl in [-30, -40, -50, -60, -70, -80]:
    trades = []
    in_trade = False
    trade_end_time = None
    
    for p in strong_patterns:
        detected = p['detected_at']
        entry_spot = p['spot_price']
        
        try:
            signal_dt = datetime.fromisoformat(detected)
        except:
            continue
        
        if in_trade and trade_end_time and signal_dt < trade_end_time:
            continue
        
        exit_price = None
        exit_time = None
        
        for pd in price_data:
            if pd['dt'] <= signal_dt:
                continue
            
            mins_elapsed = (pd['dt'] - signal_dt).total_seconds() / 60
            pnl = pd['price'] - entry_spot
            
            if pnl >= TARGET:
                exit_price = pd['price']
                exit_time = pd['dt']
                break
            elif pnl <= sl:
                exit_price = pd['price']
                exit_time = pd['dt']
                break
            elif mins_elapsed >= TIMEOUT_MINS:
                exit_price = pd['price']
                exit_time = pd['dt']
                break
        
        if exit_price is None:
            continue
        
        pnl = exit_price - entry_spot
        in_trade = True
        trade_end_time = exit_time
        trades.append({'pnl': pnl, 'result': 'WIN' if pnl > 0 else 'LOSS'})
    
    total = len(trades)
    if total == 0:
        continue
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    total_pnl = sum(t['pnl'] for t in trades)
    avg_pnl = total_pnl / total
    status = "++ PROFITABLE" if total_pnl > 0 else "--"
    print(f"SL: {sl:>3} | Trades: {total:>2} | Wins: {wins}/{total} ({wins/total*100:>4.0f}%) | P/L: {total_pnl:>+7.1f} | Avg: {avg_pnl:>+6.1f} {status}")

conn.close()
