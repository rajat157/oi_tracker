"""
Backtest: Can we find a high-WR 1:2 RR BUY_CALL dessert strategy?
Scan all possible BUY_CALL entries across all days, find winners, look for patterns.
"""
import sqlite3, os
from datetime import datetime, time, timedelta
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

c = conn.cursor()
c.execute("""SELECT timestamp, spot_price, verdict, signal_confidence, iv_skew, max_pain, vix
    FROM analysis_history WHERE DATE(timestamp) >= '2026-02-01' ORDER BY timestamp""")
analysis = [dict(r) for r in c.fetchall()]

c.execute("""SELECT timestamp, strike_price, ce_ltp, pe_ltp FROM oi_snapshots
    WHERE DATE(timestamp) >= '2026-02-01' AND (ce_ltp > 0 OR pe_ltp > 0)
    ORDER BY timestamp, strike_price""")
snapshots = [dict(r) for r in c.fetchall()]
conn.close()

def group(items):
    d = defaultdict(list)
    for i in items: d[i['timestamp'][:10]].append(i)
    return d

aby = group(analysis)
sby = group(snapshots)

NIFTY_STEP = 50
SL_PCT = 25
TP_PCT = 50

def sim_trade(ts, strike, otype, day_s):
    """Simulate a 1:2 RR trade."""
    bp = None; bd = 1e9
    for s in day_s:
        if s['strike_price'] == strike:
            st = datetime.fromisoformat(s['timestamp'])
            d = abs((st - ts).total_seconds())
            if d < bd: bd = d; bp = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
    if not bp or bp < 5: return None

    sl_p = bp * (1 - SL_PCT / 100)
    tp_p = bp * (1 + TP_PCT / 100)
    mx = bp

    for s in day_s:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= ts or s['strike_price'] != strike: continue
        p = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
        if not p or p <= 0: continue
        mx = max(mx, p)

        if p <= sl_p:
            return {'won': False, 'pnl': (p - bp) / bp * 100, 'ep': bp, 'xp': p, 'mx': mx, 'reason': 'SL'}
        if p >= tp_p:
            return {'won': True, 'pnl': (p - bp) / bp * 100, 'ep': bp, 'xp': p, 'mx': mx, 'reason': 'TP'}

        if st.hour == 15 and st.minute >= 20:
            pnl = (p - bp) / bp * 100
            return {'won': pnl > 0, 'pnl': pnl, 'ep': bp, 'xp': p, 'mx': mx, 'reason': 'EOD'}
    return None

# Scan ALL possible BUY_CALL entries
print("=" * 110)
print("DESSERT BUY_CALL — Full Scan (1:2 RR, SL 25%, TP 50%)")
print("=" * 110)

all_trades = []
for day in sorted(aby.keys()):
    for a in aby[day]:
        ts = datetime.fromisoformat(a['timestamp'])
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30): continue
        if ts.hour >= 14: continue
        
        v = a.get('verdict', '') or ''
        conf = a.get('signal_confidence', 0) or 0
        ivs = a.get('iv_skew') 
        mp = a.get('max_pain', 0) or 0
        spot = a['spot_price']
        atm = round(spot / NIFTY_STEP) * NIFTY_STEP
        
        r = sim_trade(ts, atm, 'CE', sby.get(day, []))
        if not r: continue
        
        r['day'] = day
        r['ts'] = a['timestamp']
        r['v'] = v
        r['conf'] = conf
        r['ivs'] = ivs
        r['mp'] = mp
        r['spot'] = spot
        r['atm'] = atm
        r['above_mp'] = spot > mp if mp > 0 else None
        r['spot_dir'] = 'Bull' in v
        all_trades.append(r)

winners = [t for t in all_trades if t['won']]
losers = [t for t in all_trades if not t['won']]
print(f"\nTotal BUY_CALL entries scanned: {len(all_trades)}")
print(f"Winners: {len(winners)} ({len(winners)/len(all_trades)*100:.1f}%)")
print(f"Losers: {len(losers)}")

# Analyze winners by pattern
print("\n" + "=" * 110)
print("WINNER ANALYSIS — What do winning BUY_CALL 1:2 entries have in common?")
print("=" * 110)

# Group winners by verdict
print("\n--- By Verdict ---")
for vtype in ['Bull', 'Bear', 'Neutral']:
    w = [t for t in winners if vtype in t['v']]
    a = [t for t in all_trades if vtype in t['v']]
    wr = len(w) / len(a) * 100 if a else 0
    print(f"  {vtype:10s}: {len(w)}W/{len(a)-len(w)}L out of {len(a)} ({wr:.1f}% WR)")

# By confidence
print("\n--- By Confidence ---")
for lo, hi in [(0, 50), (50, 65), (65, 80), (80, 101)]:
    w = [t for t in winners if lo <= t['conf'] < hi]
    a = [t for t in all_trades if lo <= t['conf'] < hi]
    wr = len(w) / len(a) * 100 if a else 0
    print(f"  Conf {lo}-{hi}: {len(w)}W/{len(a)-len(w)}L out of {len(a)} ({wr:.1f}% WR)")

# By IV Skew
print("\n--- By IV Skew ---")
for lo, hi, lbl in [(-99, 0, '< 0'), (0, 1, '0-1'), (1, 2, '1-2'), (2, 99, '> 2')]:
    w = [t for t in winners if t['ivs'] is not None and lo <= t['ivs'] < hi]
    a = [t for t in all_trades if t['ivs'] is not None and lo <= t['ivs'] < hi]
    wr = len(w) / len(a) * 100 if a else 0
    print(f"  IV Skew {lbl:5s}: {len(w)}W/{len(a)-len(w)}L out of {len(a)} ({wr:.1f}% WR)")

# By above/below max pain
print("\n--- By vs Max Pain ---")
for label, check in [('Above MP', True), ('Below MP', False)]:
    w = [t for t in winners if t['above_mp'] == check]
    a = [t for t in all_trades if t['above_mp'] == check]
    wr = len(w) / len(a) * 100 if a else 0
    print(f"  {label:10s}: {len(w)}W/{len(a)-len(w)}L out of {len(a)} ({wr:.1f}% WR)")

# By time window
print("\n--- By Time Window ---")
for start_h, end_h, lbl in [(9, 11, '9:30-11:00'), (11, 13, '11:00-13:00'), (13, 14, '13:00-14:00')]:
    w = [t for t in winners if start_h <= datetime.fromisoformat(t['ts']).hour < end_h]
    a = [t for t in all_trades if start_h <= datetime.fromisoformat(t['ts']).hour < end_h]
    wr = len(w) / len(a) * 100 if a else 0
    print(f"  {lbl:15s}: {len(w)}W/{len(a)-len(w)}L out of {len(a)} ({wr:.1f}% WR)")

# Now test specific strategies (one per day, first trigger)
print("\n" + "=" * 110)
print("STRATEGY TESTING — One trade per day, first trigger wins")
print("=" * 110)

strategies = [
    ("Aligned Call (Bull + conf>=65)", lambda t: 'Bull' in t['v'] and t['conf'] >= 65),
    ("Aligned Call (Bull + conf>=65 + IV>1)", lambda t: 'Bull' in t['v'] and t['conf'] >= 65 and t['ivs'] is not None and t['ivs'] > 1),
    ("Contra Call (Bear + IV>2)", lambda t: 'Bear' in t['v'] and t['ivs'] is not None and t['ivs'] > 2),
    ("Contra Call (Bear + IV>1 + above MP)", lambda t: 'Bear' in t['v'] and t['ivs'] is not None and t['ivs'] > 1 and t['above_mp']),
    ("Contra Call (Bear + conf<50)", lambda t: 'Bear' in t['v'] and t['conf'] < 50),
    ("Contra Call (Bear + conf<50 + IV>1)", lambda t: 'Bear' in t['v'] and t['conf'] < 50 and t['ivs'] is not None and t['ivs'] > 1),
    ("Low conf Call (conf<50 + IV>1)", lambda t: t['conf'] < 50 and t['ivs'] is not None and t['ivs'] > 1),
    ("Low conf Call (conf<50 + above MP)", lambda t: t['conf'] < 50 and t['above_mp']),
    ("High IV Skew Call (IV>2)", lambda t: t['ivs'] is not None and t['ivs'] > 2),
    ("High IV Skew Call (IV>2 + above MP)", lambda t: t['ivs'] is not None and t['ivs'] > 2 and t['above_mp']),
    ("Above MP + Bull + IV>1", lambda t: 'Bull' in t['v'] and t['above_mp'] and t['ivs'] is not None and t['ivs'] > 1),
    ("Above MP + IV<0 + conf>=65", lambda t: t['above_mp'] and t['ivs'] is not None and t['ivs'] < 0 and t['conf'] >= 65),
    ("Below MP + Bear (contra)", lambda t: 'Bear' in t['v'] and not t['above_mp']),
    ("Bull + IV<0 (calls cheap)", lambda t: 'Bull' in t['v'] and t['ivs'] is not None and t['ivs'] < 0),
    ("Bull + IV<0 + conf>=65", lambda t: 'Bull' in t['v'] and t['ivs'] is not None and t['ivs'] < 0 and t['conf'] >= 65),
]

print(f"\n{'Strategy':<40} {'Trades':>6} {'W':>3} {'L':>3} {'WR':>7} {'P&L':>9}")
print("-" * 75)

for name, filt in strategies:
    trades = []
    for day in sorted(aby.keys()):
        day_trades = [t for t in all_trades if t['day'] == day and filt(t)]
        if day_trades:
            trades.append(day_trades[0])  # first trigger only
    
    if not trades:
        print(f"  {name:<38} {'0':>6}")
        continue
    
    wins = sum(1 for t in trades if t['won'])
    losses = len(trades) - wins
    wr = wins / len(trades) * 100
    total_pnl = sum(t['pnl'] for t in trades)
    print(f"  {name:<38} {len(trades):>6} {wins:>3} {losses:>3} {wr:>6.1f}% {total_pnl:>+8.1f}%")

# Show details for top strategies
print("\n" + "=" * 110)
print("DETAILED RESULTS — Top Strategies")
print("=" * 110)

for name, filt in strategies:
    trades = []
    for day in sorted(aby.keys()):
        day_trades = [t for t in all_trades if t['day'] == day and filt(t)]
        if day_trades:
            trades.append(day_trades[0])
    
    if not trades: continue
    wins = sum(1 for t in trades if t['won'])
    wr = wins / len(trades) * 100
    if wr >= 70 and len(trades) >= 3:
        print(f"\n--- {name} ({wins}W/{len(trades)-wins}L, {wr:.1f}% WR) ---")
        for t in trades:
            e = "W" if t['won'] else "L"
            print(f"  {t['day']} | BUY_CE | Entry {t['ep']:>6.1f} | Exit {t['xp']:>6.1f} | {t['pnl']:>+7.1f}% | {t['reason']:<3} | {t['v'][:15]:15s} ({t['conf']:>3.0f}%) | IV:{str(t['ivs']):>5s} | {'ABV' if t['above_mp'] else 'BLW'} MP | {e}")
