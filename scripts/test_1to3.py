"""Quick test: Can any 1:3 RR config work on Iron Pulse data?"""
import sqlite3, os
from datetime import datetime
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute("""SELECT timestamp, spot_price, verdict, signal_confidence
    FROM analysis_history WHERE DATE(timestamp) >= '2026-02-01' ORDER BY timestamp""")
analysis = [dict(r) for r in c.fetchall()]
c.execute("""SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp
    FROM oi_snapshots WHERE DATE(timestamp) >= '2026-02-01' AND (ce_ltp > 0 OR pe_ltp > 0)
    ORDER BY timestamp, strike_price""")
snapshots = [dict(r) for r in c.fetchall()]
conn.close()

def group(items):
    d = defaultdict(list)
    for i in items: d[i['timestamp'][:10]].append(i)
    return d
aby = group(analysis); sby = group(snapshots)

def sim(ts, strike, otype, day_s, sl, tgt):
    bp = None; bd = 1e9
    for s in day_s:
        if s['strike_price'] == strike:
            st = datetime.fromisoformat(s['timestamp'])
            d = abs((st-ts).total_seconds())
            if d < bd: bd=d; bp = s['ce_ltp' if otype=='CE' else 'pe_ltp']
    if not bp or bp < 5: return None
    sl_p = bp*(1-sl/100); tgt_p = bp*(1+tgt/100)
    for s in day_s:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= ts or s['strike_price'] != strike: continue
        p = s['ce_ltp' if otype=='CE' else 'pe_ltp']
        if not p or p <= 0: continue
        if p <= sl_p: return ('SL', (p-bp)/bp*100, bp)
        if p >= tgt_p: return ('TGT', (p-bp)/bp*100, bp)
        if st.hour==15 and st.minute>=20: return ('EOD', (p-bp)/bp*100, bp)
    return None

configs_1to3 = [(15,45),(20,60),(25,75),(30,90),(35,105)]

print("=" * 80)
print("1:3 RR SWEEP — Iron Pulse filters (11:00-14:00, aligned, conf>=65)")
print("=" * 80)
print(f"{'SL':>4} {'TP':>4} {'Trades':>7} {'TGT':>4} {'SL':>4} {'EOD+':>5} {'EOD-':>5} {'WR':>7} {'P&L':>8}")
print("-"*55)

for sl, tp in configs_1to3:
    trades = []
    for day in sorted(aby.keys()):
        done = False
        for a in aby[day]:
            if done: break
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < 11 or ts.hour >= 14: continue
            v = a.get('verdict','') or ''; co = a.get('signal_confidence',0) or 0
            if co < 65: continue
            spot = a['spot_price']; atm = round(spot/50)*50
            if 'Bear' in v: d='BUY_PUT'; ot='PE'
            elif 'Bull' in v: d='BUY_CALL'; ot='CE'
            else: continue
            r = sim(ts, atm, ot, sby.get(day,[]), sl, tp)
            if not r: continue
            done = True
            trades.append(r)
    
    w = sum(1 for t in trades if t[0]=='TGT')
    sl_l = sum(1 for t in trades if t[0]=='SL')
    ep = sum(1 for t in trades if t[0]=='EOD' and t[1]>=0)
    en = sum(1 for t in trades if t[0]=='EOD' and t[1]<0)
    wins = w + ep; total = len(trades)
    wr = wins/total*100 if total else 0
    pnl = sum(t[1] for t in trades)
    print(f" {sl:>3}% {tp:>3}% {total:>7} {w:>4} {sl_l:>4} {ep:>5} {en:>5} {wr:>6.1f}% {pnl:>+7.1f}%")

# Also test wider: what's the max TP any trade hit?
print("\n" + "=" * 80)
print("MAX PREMIUM RISE PER TRADE (how far could target go?)")
print("=" * 80)

for day in sorted(aby.keys()):
    for a in aby[day]:
        ts = datetime.fromisoformat(a['timestamp'])
        if ts.hour < 11 or ts.hour >= 14: continue
        v = a.get('verdict','') or ''; co = a.get('signal_confidence',0) or 0
        if co < 65: continue
        spot = a['spot_price']; atm = round(spot/50)*50
        if 'Bear' in v: ot='PE'
        elif 'Bull' in v: ot='CE'
        else: continue
        
        # Find entry premium and max premium after
        bp = None; bd = 1e9
        day_s = sby.get(day, [])
        for s in day_s:
            if s['strike_price'] == atm:
                st = datetime.fromisoformat(s['timestamp'])
                d = abs((st-ts).total_seconds())
                if d < bd: bd=d; bp = s['ce_ltp' if ot=='CE' else 'pe_ltp']
        if not bp or bp < 5: break
        
        mx = bp
        for s in day_s:
            st = datetime.fromisoformat(s['timestamp'])
            if st <= ts or s['strike_price'] != atm: continue
            p = s['ce_ltp' if ot=='CE' else 'pe_ltp']
            if p and p > 0: mx = max(mx, p)
        
        max_rise = (mx - bp) / bp * 100
        bar = '#' * int(max_rise / 5)
        print(f"  {day} BUY_{ot} Rs {bp:>7.1f} -> max Rs {mx:>7.1f} ({max_rise:>+6.1f}%) {bar}")
        break
