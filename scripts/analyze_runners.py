"""
Analyze: Which Iron Pulse trades ran way past target?
Can we predict runners at entry to hold instead of booking?
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_connection
from datetime import datetime
from collections import defaultdict
import sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Get all analysis data
c = conn.cursor()
c.execute("""SELECT timestamp, spot_price, verdict, signal_confidence, vix, iv_skew, max_pain,
    call_oi_change, put_oi_change FROM analysis_history 
    WHERE DATE(timestamp) >= '2026-02-01' ORDER BY timestamp""")
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

# Simulate all Iron Pulse entries and track max premium reached
print("=" * 90)
print("IRON PULSE — Runner Analysis (how far past target did each trade go?)")
print("=" * 90)
print(f"{'Day':<12} {'Dir':<10} {'Entry':>7} {'Tgt(+22%)':>10} {'Max':>7} {'Max%':>7} {'At Tgt':>8} {'Verdict':<22} {'VIX':>5} {'IVSkew':>7}")
print("-" * 90)

for day in sorted(aby.keys()):
    done = False
    for a in aby[day]:
        if done: break
        ts = datetime.fromisoformat(a['timestamp'])
        if ts.hour < 11 or ts.hour >= 14: continue
        v = a.get('verdict', '') or ''
        co = a.get('signal_confidence', 0) or 0
        if co < 65: continue
        spot = a['spot_price']; atm = round(spot / 50) * 50
        if 'Bear' in v: ot = 'PE'
        elif 'Bull' in v: ot = 'CE'
        else: continue

        day_s = sby.get(day, [])
        # Get entry premium
        bp = None; bd = 1e9
        for s in day_s:
            if s['strike_price'] == atm:
                st = datetime.fromisoformat(s['timestamp'])
                d = abs((st - ts).total_seconds())
                if d < bd: bd = d; bp = s['ce_ltp' if ot == 'CE' else 'pe_ltp']
        if not bp or bp < 5: continue

        tgt = bp * 1.22
        sl = bp * 0.80

        # Track: did it hit target? What was max after target? What was max overall?
        hit_tgt = False
        hit_sl = False
        max_after_tgt = 0
        max_overall = bp
        tgt_time = None
        
        for s in day_s:
            st = datetime.fromisoformat(s['timestamp'])
            if st <= ts or s['strike_price'] != atm: continue
            p = s['ce_ltp' if ot == 'CE' else 'pe_ltp']
            if not p or p <= 0: continue
            max_overall = max(max_overall, p)
            
            if not hit_tgt and not hit_sl:
                if p <= sl:
                    hit_sl = True
                    break
                if p >= tgt:
                    hit_tgt = True
                    tgt_time = st
            
            if hit_tgt:
                max_after_tgt = max(max_after_tgt, p)

        if not hit_tgt: 
            max_pct = (max_overall - bp) / bp * 100
            status = "SL" if hit_sl else "EOD"
            print(f"{day:<12} BUY_{ot:<4} {bp:>7.1f} {tgt:>10.1f} {max_overall:>7.1f} {max_pct:>+6.1f}% {'--':>8} {v:<22} {a.get('vix',0):>5.1f} {a.get('iv_skew',0):>7.2f}  ({status})")
            continue

        done = True
        max_pct = (max_overall - bp) / bp * 100
        extra_pct = (max_after_tgt - tgt) / tgt * 100 if max_after_tgt > 0 else 0
        runner = max_pct > 40  # Ran more than ~2x target

        flag = " <<< RUNNER" if runner else ""
        print(f"{day:<12} BUY_{ot:<4} {bp:>7.1f} {tgt:>10.1f} {max_overall:>7.1f} {max_pct:>+6.1f}% {extra_pct:>+7.1f}% {v:<22} {a.get('vix',0):>5.1f} {a.get('iv_skew',0):>7.2f}{flag}")

print("\n" + "=" * 90)
print("INSIGHT: Features at entry for RUNNERS (>40% max rise) vs NORMAL (22-40%)")
print("=" * 90)

runners = []
normals = []

for day in sorted(aby.keys()):
    done = False
    for a in aby[day]:
        if done: break
        ts = datetime.fromisoformat(a['timestamp'])
        if ts.hour < 11 or ts.hour >= 14: continue
        v = a.get('verdict', '') or ''
        co = a.get('signal_confidence', 0) or 0
        if co < 65: continue
        spot = a['spot_price']; atm = round(spot / 50) * 50
        if 'Bear' in v: ot = 'PE'
        elif 'Bull' in v: ot = 'CE'
        else: continue

        day_s = sby.get(day, [])
        bp = None; bd = 1e9
        for s in day_s:
            if s['strike_price'] == atm:
                st = datetime.fromisoformat(s['timestamp'])
                d = abs((st - ts).total_seconds())
                if d < bd: bd = d; bp = s['ce_ltp' if ot == 'CE' else 'pe_ltp']
        if not bp or bp < 5: continue

        tgt = bp * 1.22
        hit_tgt = False; hit_sl = False; max_overall = bp
        for s in day_s:
            st = datetime.fromisoformat(s['timestamp'])
            if st <= ts or s['strike_price'] != atm: continue
            p = s['ce_ltp' if ot == 'CE' else 'pe_ltp']
            if not p or p <= 0: continue
            max_overall = max(max_overall, p)
            if not hit_tgt and not hit_sl:
                if p <= bp * 0.80: hit_sl = True; break
                if p >= tgt: hit_tgt = True
        
        if not hit_tgt: continue
        done = True
        max_pct = (max_overall - bp) / bp * 100
        entry = {
            'day': day, 'prem': bp, 'max_pct': max_pct,
            'v': v, 'co': co, 'vix': a.get('vix', 0) or 0,
            'ivs': a.get('iv_skew', 0) or 0, 'mp': a.get('max_pain', 0) or 0,
            'spot': spot, 'atm_mp': atm - (a.get('max_pain', 0) or 0),
            'coi': a.get('call_oi_change', 0) or 0,
            'poi': a.get('put_oi_change', 0) or 0,
        }
        if max_pct > 40:
            runners.append(entry)
        else:
            normals.append(entry)

print(f"\nRunners (>40%): {len(runners)} trades")
for r in runners:
    print(f"  {r['day']}: max {r['max_pct']:+.1f}% | {r['v']} ({r['co']:.0f}%) | VIX {r['vix']:.1f} | IV Skew {r['ivs']:.2f} | Prem Rs {r['prem']:.1f}")

print(f"\nNormals (22-40%): {len(normals)} trades")
for r in normals:
    print(f"  {r['day']}: max {r['max_pct']:+.1f}% | {r['v']} ({r['co']:.0f}%) | VIX {r['vix']:.1f} | IV Skew {r['ivs']:.2f} | Prem Rs {r['prem']:.1f}")

if runners and normals:
    print(f"\n{'Feature':<20} {'Runners avg':>15} {'Normals avg':>15}")
    print("-" * 50)
    for f in ['co', 'vix', 'ivs', 'prem', 'atm_mp']:
        ra = sum(r[f] for r in runners) / len(runners)
        na = sum(r[f] for r in normals) / len(normals)
        print(f"  {f:<18} {ra:>15.2f} {na:>15.2f}")
