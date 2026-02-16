"""
Deep dive: Can Iron Pulse hit 90%?
Focus on the actual SL losses and what differentiates them.
"""
import sqlite3, os
from datetime import datetime, timedelta
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

c = conn.cursor()
c.execute("""SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
    vix, iv_skew, max_pain, call_oi_change, put_oi_change, futures_oi_change, futures_basis,
    atm_call_oi_change, atm_put_oi_change
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

def sim(ts, strike, otype, day_s, sl=20, tgt=22):
    bp = None; bd = 1e9
    for s in day_s:
        if s['strike_price'] == strike:
            st = datetime.fromisoformat(s['timestamp'])
            d = abs((st - ts).total_seconds())
            if d < bd: bd = d; bp = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
    if not bp or bp < 5: return None
    sl_p = bp * (1 - sl/100); tgt_p = bp * (1 + tgt/100)
    mx = bp; mn = bp
    for s in day_s:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= ts or s['strike_price'] != strike: continue
        p = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
        if not p or p <= 0: continue
        mx = max(mx, p); mn = min(mn, p)
        if p <= sl_p: return ('SL', (p-bp)/bp*100, bp, p, st, mx, mn)
        if p >= tgt_p: return ('TGT', (p-bp)/bp*100, bp, p, st, mx, mn)
        if st.hour == 15 and st.minute >= 20: return ('EOD', (p-bp)/bp*100, bp, p, st, mx, mn)
    return None

# Build trades
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
        r = sim(ts, atm, ot, sby.get(day,[]))
        if not r: continue
        reason, pnl, ep, xp, xt, mx, mn = r
        done = True
        
        # Premium / spot ratio (higher = more expensive option)
        prem_spot_ratio = ep / spot * 100
        
        # ATM OI at entry
        atm_call_oi = a.get('atm_call_oi_change', 0) or 0
        atm_put_oi = a.get('atm_put_oi_change', 0) or 0
        
        trades.append({
            'day': day, 'ts': ts, 'd': d, 'strike': atm, 'ot': ot,
            'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': reason,
            'won': reason == 'TGT', 'sl_hit': reason == 'SL',
            'v': v, 'co': co, 'vix': a.get('vix',0) or 0,
            'ivs': a.get('iv_skew',0) or 0, 'mp': a.get('max_pain',0) or 0,
            'atm_mp': atm - (a.get('max_pain',0) or 0),
            'poi': a.get('put_oi_change',0) or 0,
            'coi': a.get('call_oi_change',0) or 0,
            'foi': a.get('futures_oi_change',0) or 0,
            'fb': a.get('futures_basis',0) or 0,
            'mx': mx, 'mn': mn,
            'psr': prem_spot_ratio,
            'atm_coi': atm_call_oi, 'atm_poi': atm_put_oi,
            'dur': (xt-ts).total_seconds()/60,
        })

# Separate by outcome  
sl_trades = [t for t in trades if t['sl_hit']]
eod_loss = [t for t in trades if t['reason'] == 'EOD' and t['pnl'] < 0]
wins = [t for t in trades if t['won']]
eod_win = [t for t in trades if t['reason'] == 'EOD' and t['pnl'] >= 0]

print("=" * 80)
print("IRON PULSE — Path to 90%")
print("=" * 80)
print(f"Total: {len(trades)} trades")
print(f"  TARGET hits (wins): {len(wins)}")
print(f"  SL hits (losses):   {len(sl_trades)}")
print(f"  EOD positive:       {len(eod_win)}")
print(f"  EOD negative:       {len(eod_loss)}")

# If we count EOD+ as wins: 
real_wins = len(wins) + len(eod_win)
real_losses = len(sl_trades) + len(eod_loss)
print(f"\nAdjusted WR (EOD+ = win): {real_wins}W/{real_losses}L = {real_wins/(real_wins+real_losses)*100:.1f}%")
print(f"To hit 90% with {len(trades)} trades: max {int(len(trades)*0.1)} losses allowed")

print("\n" + "=" * 80)
print("SL LOSSES — What went wrong?")
print("=" * 80)
for t in sl_trades:
    peak_pnl = (t['mx'] - t['ep'])/t['ep']*100
    print(f"\n  {t['day']} — {t['d']} {t['strike']} {t['ot']} @ Rs {t['ep']:.2f}")
    print(f"  Verdict: {t['v']} ({t['co']:.0f}%) | VIX: {t['vix']:.1f} | IV Skew: {t['ivs']:.2f}")
    print(f"  Premium/Spot: {t['psr']:.3f}% | ATM vs MP: {t['atm_mp']:+.0f}")
    print(f"  Call OI chg: {t['coi']:,.0f} | Put OI chg: {t['poi']:,.0f}")
    print(f"  ATM Call OI: {t['atm_coi']:,.0f} | ATM Put OI: {t['atm_poi']:,.0f}")
    print(f"  Futures OI: {t['foi']:,.0f} | Basis: {t['fb']:.2f}")
    print(f"  Peak before SL: {peak_pnl:+.1f}% | SL in {t['dur']:.0f} min")

print("\n" + "=" * 80)
print("EOD LOSSES — Near misses")
print("=" * 80)
for t in eod_loss:
    peak_pnl = (t['mx'] - t['ep'])/t['ep']*100
    print(f"\n  {t['day']} — {t['d']} {t['strike']} {t['ot']} @ Rs {t['ep']:.2f}")
    print(f"  Final P&L: {t['pnl']:+.1f}% | Peak: {peak_pnl:+.1f}%")
    print(f"  Verdict: {t['v']} ({t['co']:.0f}%) | VIX: {t['vix']:.1f} | IV Skew: {t['ivs']:.2f}")
    print(f"  Premium/Spot: {t['psr']:.3f}% | ATM vs MP: {t['atm_mp']:+.0f}")

print("\n" + "=" * 80)
print("EXHAUSTIVE FILTER SEARCH — Target 90%+ WR")
print("=" * 80)

# Build atomic filters
atomic = {
    'slightly': lambda t: 'Slightly' in t['v'],
    'not_strongly': lambda t: 'Strongly' not in t['v'],
    'co65_80': lambda t: 65 <= t['co'] <= 80,
    'co_ge70': lambda t: t['co'] >= 70,
    'co_ge75': lambda t: t['co'] >= 75,
    'vix_lt13': lambda t: t['vix'] < 13 or t['vix'] == 0,
    'vix_lt14': lambda t: t['vix'] < 14 or t['vix'] == 0,
    'ivs_pos': lambda t: t['ivs'] > 0,
    'ivs_gt1': lambda t: t['ivs'] > 1,
    'ivs_gt2': lambda t: t['ivs'] > 2,
    'atm_below_mp': lambda t: t['atm_mp'] < 0,
    'atm_at_mp': lambda t: t['atm_mp'] == 0,
    'atm_above_mp': lambda t: t['atm_mp'] > 0,
    'poi_gt_coi': lambda t: t['poi'] > t['coi'],
    'coi_gt_poi': lambda t: t['coi'] > t['poi'],
    'prem_gt50': lambda t: t['ep'] > 50,
    'prem_gt80': lambda t: t['ep'] > 80,
    'prem_gt100': lambda t: t['ep'] > 100,
    'prem_lt150': lambda t: t['ep'] < 150,
    'fb_lt70': lambda t: t['fb'] < 70,
    'fb_gt50': lambda t: t['fb'] > 50,
    'foi_neg': lambda t: t['foi'] < 0,
    'foi_pos': lambda t: t['foi'] > 0,
}

# Test all singles
print("\n--- Single Filters (min 4 trades) ---")
results = []
for n, f in atomic.items():
    ft = [t for t in trades if f(t)]
    if len(ft) < 4: continue
    w = sum(1 for t in ft if t['won'] or (t['reason']=='EOD' and t['pnl']>=0))
    l = len(ft) - w
    wr = w/len(ft)*100
    pnl = sum(t['pnl'] for t in ft)
    results.append((wr, n, w, l, len(ft), pnl))

results.sort(reverse=True)
for wr, n, w, l, tot, pnl in results:
    flag = " ***" if wr >= 90 else ""
    print(f"  {n:<25} {w}W/{l}L ({wr:5.1f}%) P&L: {pnl:+7.1f}% [{tot} trades]{flag}")

# Test all pairs
print("\n--- Pair Filters (min 4 trades, WR >= 80%) ---")
import itertools
results2 = []
names = list(atomic.keys())
for a, b in itertools.combinations(names, 2):
    ft = [t for t in trades if atomic[a](t) and atomic[b](t)]
    if len(ft) < 4: continue
    w = sum(1 for t in ft if t['won'] or (t['reason']=='EOD' and t['pnl']>=0))
    l = len(ft) - w
    wr = w/len(ft)*100
    if wr < 80: continue
    pnl = sum(t['pnl'] for t in ft)
    results2.append((wr, f"{a} + {b}", w, l, len(ft), pnl))

results2.sort(key=lambda x: (-x[0], -x[4]))
for wr, n, w, l, tot, pnl in results2[:20]:
    flag = " ***" if wr >= 90 else ""
    print(f"  {n:<45} {w}W/{l}L ({wr:5.1f}%) P&L: {pnl:+7.1f}% [{tot} trades]{flag}")

# Test triples
print("\n--- Triple Filters (min 4 trades, WR >= 85%) ---")
results3 = []
for a, b, cc in itertools.combinations(names, 3):
    ft = [t for t in trades if atomic[a](t) and atomic[b](t) and atomic[cc](t)]
    if len(ft) < 4: continue
    w = sum(1 for t in ft if t['won'] or (t['reason']=='EOD' and t['pnl']>=0))
    l = len(ft) - w
    wr = w/len(ft)*100
    if wr < 85: continue
    pnl = sum(t['pnl'] for t in ft)
    results3.append((wr, f"{a} + {b} + {cc}", w, l, len(ft), pnl))

results3.sort(key=lambda x: (-x[0], -x[4]))
for wr, n, w, l, tot, pnl in results3[:20]:
    flag = " ***" if wr >= 90 else ""
    print(f"  {n:<55} {w}W/{l}L ({wr:5.1f}%) P&L: {pnl:+7.1f}% [{tot} trades]{flag}")
