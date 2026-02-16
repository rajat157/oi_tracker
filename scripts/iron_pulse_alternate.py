"""
On days where the 'Slightly + Premium > 50' filter would skip,
check if an alternate qualifying entry existed later that day.
"""
import sqlite3, os
from datetime import datetime
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

c = conn.cursor()
c.execute("""SELECT timestamp, spot_price, verdict, signal_confidence, vix, iv_skew, max_pain
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

aby = group(analysis)
sby = group(snapshots)

def get_atm_premium(day_s, strike, otype, ts):
    best = None; bd = 1e9
    for s in day_s:
        if s['strike_price'] == strike:
            st = datetime.fromisoformat(s['timestamp'])
            d = abs((st - ts).total_seconds())
            if d < bd:
                bd = d
                best = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
    return best

def sim(ts, strike, otype, day_s, sl=20, tgt=22):
    bp = get_atm_premium(day_s, strike, otype, ts)
    if not bp or bp < 5: return None
    sl_p = bp * (1 - sl/100); tgt_p = bp * (1 + tgt/100)
    mx = bp; mn = bp
    for s in day_s:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= ts or s['strike_price'] != strike: continue
        p = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
        if not p or p <= 0: continue
        mx = max(mx, p); mn = min(mn, p)
        if p <= sl_p: return ('SL', (p-bp)/bp*100, bp, p, st)
        if p >= tgt_p: return ('TGT', (p-bp)/bp*100, bp, p, st)
        if st.hour == 15 and st.minute >= 20: return ('EOD', (p-bp)/bp*100, bp, p, st)
    return None

# Days where current filter loses: Feb 5 (EOD -9.2%), Feb 6 (SL), Feb 10 (SL)
skip_days = ['2026-02-05', '2026-02-06', '2026-02-10']

for day in skip_days:
    print(f"\n{'='*80}")
    print(f"  {day} — Scanning for alternate entries")
    print(f"{'='*80}")
    
    day_a = aby.get(day, [])
    day_s = sby.get(day, [])
    
    found_any = False
    entries_checked = 0
    
    # Check ALL entries in 11:00-14:00 that pass the new filter
    for a in day_a:
        ts = datetime.fromisoformat(a['timestamp'])
        if ts.hour < 11 or ts.hour >= 14:
            continue
        
        v = a.get('verdict', '') or ''
        co = a.get('signal_confidence', 0) or 0
        
        # New filter: Slightly + conf >= 65
        if 'Slightly' not in v:
            continue
        if co < 65:
            continue
        
        spot = a['spot_price']
        atm = round(spot / 50) * 50
        
        if 'Bear' in v:
            d = 'BUY_PUT'; ot = 'PE'
        elif 'Bull' in v:
            d = 'BUY_CALL'; ot = 'CE'
        else:
            continue
        
        # Check premium > 50
        prem = get_atm_premium(day_s, atm, ot, ts)
        if not prem or prem < 50:
            entries_checked += 1
            continue
        
        # We have a qualifying entry! Simulate it.
        result = sim(ts, atm, ot, day_s)
        if not result:
            entries_checked += 1
            continue
        
        reason, pnl, ep, xp, xt = result
        emoji = "WIN" if reason == 'TGT' else ("SL" if reason == 'SL' else "EOD")
        won = reason == 'TGT' or (reason == 'EOD' and pnl > 0)
        
        print(f"\n  {'*' if not found_any else ' '} {ts.strftime('%H:%M')} — {d} {atm} {ot} @ Rs {ep:.2f}")
        print(f"    Verdict: {v} ({co:.0f}%)")
        print(f"    Result: {emoji} at {xt.strftime('%H:%M')} — P&L: {pnl:+.1f}%")
        print(f"    {'WOULD HAVE WON!' if won else 'Still a loss'}")
        
        if not found_any:
            found_any = True
            # Only take first qualifying entry (one-trade-per-day rule)
            print(f"\n  >>> FIRST qualifying entry: {'PROFITABLE' if won else 'UNPROFITABLE'}")
            break
    
    if not found_any:
        # Show what verdicts were available
        available = [(datetime.fromisoformat(a['timestamp']).strftime('%H:%M'), 
                      a['verdict'], a['signal_confidence'])
                     for a in day_a 
                     if 11 <= datetime.fromisoformat(a['timestamp']).hour < 14]
        print(f"\n  NO qualifying entries found (Slightly + prem>50 + conf>=65)")
        print(f"  Verdicts available in window:")
        for t, v, c in available[:10]:
            print(f"    {t}: {v} ({c:.0f}%)")
        if len(available) > 10:
            print(f"    ... and {len(available)-10} more")

print(f"\n{'='*80}")
print("SUMMARY")
print(f"{'='*80}")
print("If 'Slightly + Premium > 50' filter was applied:")
print("  - Feb 5, 6, 10 original entries would be SKIPPED")
print("  - Check above for whether alternate entries existed and were profitable")
