"""
1. Analyze PM tracker patterns vs 1:2 winning entries
2. Find the SINGLE best 1:2 trade per day and look for common patterns
"""
import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

def q(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(sql, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

# ============================================================
# PART 1: PM Tracker Analysis
# ============================================================
print("="*100)
print("PART 1: PM TRACKER — Detected Patterns")
print("="*100)

patterns = q("SELECT * FROM detected_patterns ORDER BY detected_at")
print(f"Total detected patterns: {len(patterns)}")

by_type = defaultdict(list)
for p in patterns:
    by_type[p['pattern_type']].append(p)

for ptype, items in sorted(by_type.items()):
    print(f"\n  {ptype}: {len(items)} occurrences")
    # Show a few examples
    for item in items[:3]:
        print(f"    {item['detected_at'][:16]} spot={item['spot_price']:.0f} pm={item['pm_score']:.1f} "
              f"prev={item['pm_prev']:.1f} chg={item['pm_change']:.1f} "
              f"v={item['verdict']} conf={item['confidence']:.0f}% "
              f"strike={item['strike']} {item['option_type']}")

# Group patterns by day
print(f"\n\nPatterns by day:")
pat_by_day = defaultdict(list)
for p in patterns:
    pat_by_day[p['detected_at'][:10]].append(p)
for day in sorted(pat_by_day.keys()):
    items = pat_by_day[day]
    types = defaultdict(int)
    for i in items:
        types[i['pattern_type']] += 1
    type_str = ", ".join(f"{t}:{c}" for t,c in sorted(types.items()))
    print(f"  {day}: {len(items)} patterns — {type_str}")

# ============================================================
# PART 2: Find BEST single 1:2 trade per day
# ============================================================
print(f"\n\n{'='*100}")
print("PART 2: BEST SINGLE 1:2 TRADE PER DAY (Manual Analysis)")
print("="*100)

analysis_data = q("""
    SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
           futures_oi_change, futures_basis, vix, iv_skew, prev_verdict,
           call_oi_change, put_oi_change
    FROM analysis_history
    WHERE DATE(timestamp) >= '2026-02-01' AND DATE(timestamp) < '2026-02-16'
    ORDER BY timestamp
""")

snapshots_data = q("""
    SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp, ce_iv, pe_iv
    FROM oi_snapshots
    WHERE DATE(timestamp) >= '2026-02-01' AND DATE(timestamp) < '2026-02-16'
        AND (ce_ltp > 0 OR pe_ltp > 0)
    ORDER BY timestamp, strike_price
""")

a_by_day = defaultdict(list)
for a in analysis_data:
    a_by_day[a['timestamp'][:10]].append(a)

s_by_day = defaultdict(list)
for s in snapshots_data:
    s_by_day[s['timestamp'][:10]].append(s)

def get_prem(snaps, ts, strike, ot):
    best, bd = None, float('inf')
    for s in snaps:
        if s['strike_price'] == strike:
            d = abs((datetime.fromisoformat(s['timestamp']) - ts).total_seconds())
            if d < bd:
                bd = d
                best = s['ce_ltp'] if ot == 'CE' else s['pe_ltp']
    return best if best and best > 0 else None

def sim_buy(snaps, entry_time, strike, ot, entry_p, sl_pct=25, tgt_pct=50):
    sl = entry_p * (1 - sl_pct/100)
    tgt = entry_p * (1 + tgt_pct/100)
    for s in snaps:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= entry_time or s['strike_price'] != strike:
            continue
        p = s['ce_ltp'] if ot == 'CE' else s['pe_ltp']
        if not p or p <= 0:
            continue
        if p <= sl:
            return p, 'SL', st
        if p >= tgt:
            return p, 'TARGET', st
        if st.hour == 15 and st.minute >= 20:
            return p, 'EOD', st
    return None, None, None

days = sorted(a_by_day.keys())

# For each day, find the single best entry that hits TARGET
# Try both directions at every analysis point
best_trades = []

for day in days:
    snaps = s_by_day.get(day, [])
    if not snaps:
        best_trades.append({'day': day, 'found': False})
        continue
    
    day_winners = []
    for a in a_by_day[day]:
        ts = datetime.fromisoformat(a['timestamp'])
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30) or ts.hour >= 14:
            continue
        
        spot = a['spot_price']
        atm = round(spot / 50) * 50
        
        for ot in ['CE', 'PE']:
            ep = get_prem(snaps, ts, atm, ot)
            if not ep or ep < 10:
                continue
            exit_p, reason, exit_t = sim_buy(snaps, ts, atm, ot, ep)
            if reason == 'TARGET':
                pnl = ((exit_p - ep) / ep) * 100
                aligned = ('Bullish' in a['verdict'] and ot == 'CE') or ('Bearish' in a['verdict'] and ot == 'PE')
                day_winners.append({
                    'day': day,
                    'entry_time': ts,
                    'exit_time': exit_t,
                    'direction': f"BUY_{'CALL' if ot=='CE' else 'PUT'}",
                    'strike': atm,
                    'ot': ot,
                    'entry_p': ep,
                    'exit_p': exit_p,
                    'pnl': pnl,
                    'verdict': a['verdict'],
                    'aligned': aligned,
                    'confidence': a['signal_confidence'] or 0,
                    'vix': a.get('vix', 0) or 0,
                    'fut_oi': a.get('futures_oi_change', 0) or 0,
                    'iv_skew': a.get('iv_skew', 0) or 0,
                    'prev_verdict': a.get('prev_verdict', '') or '',
                    'call_oi_chg': a.get('call_oi_change', 0) or 0,
                    'put_oi_chg': a.get('put_oi_change', 0) or 0,
                    'basis': a.get('futures_basis', 0) or 0,
                    'hold_mins': (exit_t - ts).total_seconds() / 60 if exit_t else 0,
                })
    
    if day_winners:
        # Pick the best: earliest entry that's verdict-aligned, else earliest overall
        aligned_wins = [w for w in day_winners if w['aligned']]
        if aligned_wins:
            aligned_wins.sort(key=lambda x: x['entry_time'])
            best = aligned_wins[0]
        else:
            day_winners.sort(key=lambda x: x['entry_time'])
            best = day_winners[0]
        best['found'] = True
        best['total_winners'] = len(day_winners)
        best_trades.append(best)
    else:
        best_trades.append({'day': day, 'found': False})

# Display results
print(f"\n{'Day':<12} {'Found':>5} {'Time':<6} {'Dir':<10} {'Strike':<7} {'Entry':>7} {'P&L':>7} "
      f"{'Hold':>5} {'Verdict':<22} {'Algn':>4} {'Conf':>4} {'VIX':>5} {'FutOI':>6} {'IVSkw':>5} {'PrevV':<20}")
print("-" * 170)

for t in best_trades:
    if not t['found']:
        print(f"{t['day']:<12}    NO — no 1:2 winning entry exists this day")
        continue
    print(f"{t['day']:<12}   YES {t['entry_time'].strftime('%H:%M'):<6} {t['direction']:<10} {t['strike']:<7} "
          f"{t['entry_p']:>7.2f} {t['pnl']:>+6.1f}% {t['hold_mins']:>4.0f}m "
          f"{t['verdict']:<22} {'Y' if t['aligned'] else 'N':>4} {t['confidence']:>4.0f} {t['vix']:>5.1f} "
          f"{t['fut_oi']:>6} {t['iv_skew']:>5.1f} {t['prev_verdict']:<20}")

# ============================================================
# PART 3: PATTERN ANALYSIS on best trades
# ============================================================
found = [t for t in best_trades if t['found']]
not_found = [t for t in best_trades if not t['found']]

print(f"\n\n{'='*100}")
print(f"PART 3: PATTERN ANALYSIS — What separates winning days from losing days?")
print(f"{'='*100}")
print(f"\nWinning days (1:2 possible): {len(found)}/11")
print(f"No-win days: {len(not_found)}/11")

# Analyze what the no-win days look like
print(f"\n--- NO-WIN DAYS ANALYSIS ---")
for t in not_found:
    day = t['day']
    day_a = a_by_day[day]
    # Get the range of verdicts and conditions for these days
    verdicts = [a['verdict'] for a in day_a]
    v_counts = defaultdict(int)
    for v in verdicts: v_counts[v] += 1
    vix_vals = [a.get('vix', 0) or 0 for a in day_a if (a.get('vix', 0) or 0) > 0]
    avg_vix = sum(vix_vals)/len(vix_vals) if vix_vals else 0
    conf_vals = [a.get('signal_confidence', 0) or 0 for a in day_a]
    avg_conf = sum(conf_vals)/len(conf_vals) if conf_vals else 0
    spots = [a['spot_price'] for a in day_a]
    spot_range = max(spots) - min(spots) if spots else 0
    
    top_verdict = max(v_counts.items(), key=lambda x: x[1])
    print(f"  {day}: VIX={avg_vix:.1f}, AvgConf={avg_conf:.0f}%, SpotRange={spot_range:.0f}pts, "
          f"TopVerdict={top_verdict[0]}({top_verdict[1]})")

print(f"\n--- WINNING DAYS ANALYSIS ---")
for t in found:
    day = t['day']
    day_a = a_by_day[day]
    vix_vals = [a.get('vix', 0) or 0 for a in day_a if (a.get('vix', 0) or 0) > 0]
    avg_vix = sum(vix_vals)/len(vix_vals) if vix_vals else 0
    spots = [a['spot_price'] for a in day_a]
    spot_range = max(spots) - min(spots) if spots else 0
    
    print(f"  {day}: VIX={avg_vix:.1f}, SpotRange={spot_range:.0f}pts, "
          f"BestTrade: {t['direction']} @ {t['entry_time'].strftime('%H:%M')} "
          f"v={t['verdict']} hold={t['hold_mins']:.0f}m P&L={t['pnl']:+.1f}%")

# Key differentiator analysis
print(f"\n--- KEY DIFFERENTIATORS ---")
w_vix = [t['vix'] for t in found]
nw_days_vix = []
for t in not_found:
    day_a = a_by_day[t['day']]
    vv = [a.get('vix',0) or 0 for a in day_a if (a.get('vix',0) or 0) > 0]
    if vv: nw_days_vix.append(sum(vv)/len(vv))

print(f"  Avg VIX on winning days: {sum(w_vix)/len(w_vix):.1f}" if w_vix else "  No VIX data")
print(f"  Avg VIX on no-win days: {sum(nw_days_vix)/len(nw_days_vix):.1f}" if nw_days_vix else "  No VIX data")

w_range = []
nw_range = []
for t in best_trades:
    day = t['day']
    spots = [a['spot_price'] for a in a_by_day[day]]
    r = max(spots) - min(spots) if spots else 0
    if t['found']:
        w_range.append(r)
    else:
        nw_range.append(r)

print(f"  Avg spot range on winning days: {sum(w_range)/len(w_range):.0f} pts" if w_range else "")
print(f"  Avg spot range on no-win days: {sum(nw_range)/len(nw_range):.0f} pts" if nw_range else "")

# Direction analysis
print(f"\n--- DIRECTION PATTERNS ---")
for t in found:
    print(f"  {t['day']}: {t['direction']:<10} (verdict was {t['verdict']}, aligned={t['aligned']})")

calls = [t for t in found if t['direction'] == 'BUY_CALL']
puts = [t for t in found if t['direction'] == 'BUY_PUT']
print(f"\n  CALL winners: {len(calls)}/{len(found)}")
print(f"  PUT winners: {len(puts)}/{len(found)}")
aligned_count = sum(1 for t in found if t['aligned'])
print(f"  Verdict-aligned: {aligned_count}/{len(found)}")

# PM patterns on winning days vs losing days
print(f"\n--- PM PATTERNS ON WINNING vs NO-WIN DAYS ---")
for t in best_trades:
    day = t['day']
    day_pats = pat_by_day.get(day, [])
    types = defaultdict(int)
    for p in day_pats:
        types[p['pattern_type']] += 1
    status = "WIN" if t['found'] else "NO-WIN"
    type_str = ", ".join(f"{k}:{v}" for k,v in sorted(types.items())) if types else "none"
    print(f"  {day} [{status:>6}]: {len(day_pats)} PM patterns — {type_str}")
