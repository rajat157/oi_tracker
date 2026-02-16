"""
Find ALL possible 1:2 RR winning buying entries per day.
For each trading day, scan every analysis snapshot as a potential entry.
Track which entries would have hit 1:2 target (50% gain) before SL (25% loss).
Then analyze patterns in the winning entries.
"""
import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict
import json

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

def get_all_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               futures_oi_change, futures_basis, vix, iv_skew, prev_verdict,
               total_call_oi, total_put_oi, call_oi_change, put_oi_change
        FROM analysis_history
        WHERE DATE(timestamp) >= '2026-02-01'
        ORDER BY timestamp
    """)
    analysis = [dict(r) for r in c.fetchall()]
    
    c.execute("""
        SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp, ce_iv, pe_iv
        FROM oi_snapshots
        WHERE DATE(timestamp) >= '2026-02-01' AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price
    """)
    snapshots = [dict(r) for r in c.fetchall()]
    conn.close()
    return analysis, snapshots

def group_by_day(items):
    by_day = defaultdict(list)
    for item in items:
        by_day[item['timestamp'][:10]].append(item)
    return by_day

def find_best_entry_per_day(day_analysis, day_snapshots, sl_pct=25, target_pct=50):
    """
    For each analysis snapshot in the day, try buying ATM CE (if bullish signal)
    or ATM PE (if bearish signal) and see if it hits 1:2 target before SL.
    Returns ALL winning entries and the BEST one.
    """
    results = []
    
    for a in day_analysis:
        ts = datetime.fromisoformat(a['timestamp'])
        # Only 9:30 - 14:00 window (wider than Config B to find hidden gems)
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30) or ts.hour >= 14:
            continue
        
        spot = a['spot_price']
        atm = round(spot / 50) * 50
        verdict = a['verdict']
        confidence = a['signal_confidence'] or 0
        vix = a.get('vix', 0) or 0
        fut_oi = a.get('futures_oi_change', 0) or 0
        basis = a.get('futures_basis', 0) or 0
        iv_skew = a.get('iv_skew', 0) or 0
        prev_v = a.get('prev_verdict', '') or ''
        call_oi_chg = a.get('call_oi_change', 0) or 0
        put_oi_chg = a.get('put_oi_change', 0) or 0
        
        # Try BOTH directions at each snapshot
        for direction in ['CALL', 'PUT']:
            option_type = 'CE' if direction == 'CALL' else 'PE'
            strike = atm
            
            # Get entry premium
            best_prem = None
            best_diff = float('inf')
            for snap in day_snapshots:
                if snap['strike_price'] == strike:
                    snap_time = datetime.fromisoformat(snap['timestamp'])
                    diff = abs((snap_time - ts).total_seconds())
                    if diff < best_diff:
                        best_diff = diff
                        best_prem = snap['ce_ltp'] if option_type == 'CE' else snap['pe_ltp']
            
            if not best_prem or best_prem < 5:
                continue
            
            sl_p = best_prem * (1 - sl_pct / 100)
            tgt_p = best_prem * (1 + target_pct / 100)
            
            exit_prem = None
            exit_reason = None
            exit_time = None
            max_prem = best_prem
            min_prem = best_prem
            
            for snap in day_snapshots:
                snap_time = datetime.fromisoformat(snap['timestamp'])
                if snap_time <= ts:
                    continue
                if snap['strike_price'] != strike:
                    continue
                price = snap['ce_ltp'] if option_type == 'CE' else snap['pe_ltp']
                if not price or price <= 0:
                    continue
                
                max_prem = max(max_prem, price)
                min_prem = min(min_prem, price)
                
                if price <= sl_p:
                    exit_prem = price; exit_reason = 'SL'; exit_time = snap_time; break
                if price >= tgt_p:
                    exit_prem = price; exit_reason = 'TARGET'; exit_time = snap_time; break
                if snap_time.hour == 15 and snap_time.minute >= 20:
                    exit_prem = price; exit_reason = 'EOD'; exit_time = snap_time; break
            
            if not exit_prem:
                continue
            
            pnl = ((exit_prem - best_prem) / best_prem) * 100
            won = exit_reason == 'TARGET'
            
            # Determine if entry direction matches verdict
            verdict_aligned = (
                (direction == 'CALL' and 'Bullish' in verdict) or
                (direction == 'PUT' and 'Bearish' in verdict)
            )
            
            results.append({
                'entry_time': ts,
                'hour': ts.hour,
                'minute': ts.minute,
                'direction': f'BUY_{direction}',
                'strike': strike,
                'option_type': option_type,
                'entry_premium': best_prem,
                'exit_premium': exit_prem,
                'sl_premium': sl_p,
                'target_premium': tgt_p,
                'max_premium': max_prem,
                'min_premium': min_prem,
                'pnl_pct': pnl,
                'exit_reason': exit_reason,
                'won': won,
                'exit_time': exit_time,
                'verdict': verdict,
                'verdict_aligned': verdict_aligned,
                'confidence': confidence,
                'vix': vix,
                'futures_oi_change': fut_oi,
                'futures_basis': basis,
                'iv_skew': iv_skew,
                'prev_verdict': prev_v,
                'call_oi_change': call_oi_chg,
                'put_oi_change': put_oi_chg,
                'spot': spot,
            })
    
    return results

if __name__ == '__main__':
    print("Loading data...")
    analysis, snapshots = get_all_data()
    analysis_by_day = group_by_day(analysis)
    snapshots_by_day = group_by_day(snapshots)
    
    days = sorted(d for d in analysis_by_day.keys() if d != '2026-02-16')
    print(f"Scanning {len(days)} trading days: {days[0]} to {days[-1]}")
    print(f"Looking for 1:2 RR (25% SL / 50% Target) winning entries\n")
    
    all_winners = []
    all_losers = []
    day_summary = []
    
    for day in days:
        results = find_best_entry_per_day(
            analysis_by_day[day], snapshots_by_day.get(day, [])
        )
        
        winners = [r for r in results if r['won']]
        losers = [r for r in results if not r['won']]
        
        # Find the BEST winner per day (earliest that hits target)
        best_winner = None
        if winners:
            # Sort by entry time - first winning entry
            winners.sort(key=lambda x: x['entry_time'])
            best_winner = winners[0]
        
        all_winners.extend(winners)
        all_losers.extend(losers)
        
        has_win = len(winners) > 0
        day_summary.append({
            'day': day,
            'total_entries': len(results),
            'winners': len(winners),
            'losers': len(losers),
            'has_win': has_win,
            'best_winner': best_winner,
        })
        
        # Print day summary
        status = "HAS WINNERS" if has_win else "NO WINNERS"
        print(f"\n{'='*90}")
        print(f"[{day}] {status} — {len(winners)} winning entries out of {len(results)} possible")
        print(f"{'='*90}")
        
        if best_winner:
            bw = best_winner
            print(f"  BEST ENTRY: {bw['entry_time'].strftime('%H:%M')} {bw['direction']} {bw['strike']} "
                  f"@ Rs{bw['entry_premium']:.2f} -> Rs{bw['exit_premium']:.2f} ({bw['pnl_pct']:+.1f}%) "
                  f"exit {bw['exit_time'].strftime('%H:%M')} [{bw['exit_reason']}]")
            print(f"  Context: verdict={bw['verdict']}, conf={bw['confidence']:.0f}%, VIX={bw['vix']:.1f}, "
                  f"fut_oi_chg={bw['futures_oi_change']}, iv_skew={bw['iv_skew']:.1f}, "
                  f"aligned={bw['verdict_aligned']}, prev={bw['prev_verdict']}")
        
        # Show ALL winners for the day with their conditions
        if winners:
            print(f"\n  ALL WINNING ENTRIES:")
            print(f"  {'Time':<6} {'Dir':<10} {'Verdict':<22} {'Aligned':>7} {'Conf':>5} {'VIX':>5} {'FutOI':>7} {'IVSkew':>7} {'Entry':>7} {'P&L':>7}")
            for w in sorted(winners, key=lambda x: x['entry_time']):
                print(f"  {w['entry_time'].strftime('%H:%M'):<6} {w['direction']:<10} {w['verdict']:<22} "
                      f"{'YES' if w['verdict_aligned'] else 'NO':>7} {w['confidence']:>5.0f} {w['vix']:>5.1f} "
                      f"{w['futures_oi_change']:>7} {w['iv_skew']:>7.1f} {w['entry_premium']:>7.2f} {w['pnl_pct']:>+6.1f}%")
    
    # ============================================================
    # PATTERN ANALYSIS
    # ============================================================
    print(f"\n\n{'='*100}")
    print("PATTERN ANALYSIS — What do winning entries have in common?")
    print(f"{'='*100}")
    
    print(f"\nTotal winning entries: {len(all_winners)}")
    print(f"Total losing entries: {len(all_losers)}")
    print(f"Days with at least 1 winner: {sum(1 for d in day_summary if d['has_win'])}/{len(day_summary)}")
    
    # Analyze verdict alignment
    aligned_wins = [w for w in all_winners if w['verdict_aligned']]
    unaligned_wins = [w for w in all_winners if not w['verdict_aligned']]
    print(f"\nVerdict Alignment:")
    print(f"  Aligned winners: {len(aligned_wins)} ({len(aligned_wins)/len(all_winners)*100:.0f}%)")
    print(f"  Unaligned winners: {len(unaligned_wins)} ({len(unaligned_wins)/len(all_winners)*100:.0f}%)")
    
    # Analyze by verdict
    print(f"\nBy Verdict:")
    verdicts = defaultdict(lambda: {'wins': 0, 'total': 0})
    for w in all_winners:
        verdicts[w['verdict']]['wins'] += 1
    for l in all_losers:
        pass  # losers don't matter for verdict count here
    for w in all_winners + all_losers:
        verdicts[w['verdict']]['total'] += 1
    for v in sorted(verdicts.keys()):
        d = verdicts[v]
        print(f"  {v:<25} wins={d['wins']:<4} total={d['total']:<4} "
              f"wr={d['wins']/d['total']*100:.1f}%" if d['total'] else "")
    
    # Analyze by time
    print(f"\nBy Entry Hour:")
    hours = defaultdict(lambda: {'wins': 0, 'total': 0})
    for w in all_winners:
        hours[w['hour']]['wins'] += 1
    for item in all_winners + all_losers:
        hours[item['hour']]['total'] += 1
    for h in sorted(hours.keys()):
        d = hours[h]
        print(f"  {h:02d}:00  wins={d['wins']:<4} total={d['total']:<4} "
              f"wr={d['wins']/d['total']*100:.1f}%")
    
    # Analyze by VIX
    print(f"\nBy VIX Range:")
    for label, vmin, vmax in [("<11.5", 0, 11.5), ("11.5-12", 11.5, 12), ("12-12.5", 12, 12.5), ("12.5-13", 12.5, 13), (">13", 13, 100)]:
        w = [x for x in all_winners if vmin <= x['vix'] < vmax]
        t = [x for x in all_winners + all_losers if vmin <= x['vix'] < vmax]
        if t:
            print(f"  VIX {label:<10} wins={len(w):<4} total={len(t):<4} wr={len(w)/len(t)*100:.1f}%")
    
    # Analyze by confidence
    print(f"\nBy Confidence:")
    for label, cmin, cmax in [("<65", 0, 65), ("65-75", 65, 75), ("75-85", 75, 85), ("85+", 85, 101)]:
        w = [x for x in all_winners if cmin <= x['confidence'] < cmax]
        t = [x for x in all_winners + all_losers if cmin <= x['confidence'] < cmax]
        if t:
            print(f"  Conf {label:<8} wins={len(w):<4} total={len(t):<4} wr={len(w)/len(t)*100:.1f}%")
    
    # Analyze by futures OI change
    print(f"\nBy Futures OI Change:")
    for label, fn in [("Positive (>0)", lambda x: (x.get('futures_oi_change') or 0) > 0),
                       ("Negative (<0)", lambda x: (x.get('futures_oi_change') or 0) < 0),
                       ("Large neg (<-100)", lambda x: (x.get('futures_oi_change') or 0) < -100)]:
        w = [x for x in all_winners if fn(x)]
        t = [x for x in all_winners + all_losers if fn(x)]
        if t:
            print(f"  FutOI {label:<20} wins={len(w):<4} total={len(t):<4} wr={len(w)/len(t)*100:.1f}%")
    
    # Analyze by IV skew
    print(f"\nBy IV Skew:")
    for label, smin, smax in [("Negative (<0)", -100, 0), ("Low (0-2)", 0, 2), ("Mid (2-5)", 2, 5), ("High (>5)", 5, 100)]:
        w = [x for x in all_winners if smin <= x['iv_skew'] < smax]
        t = [x for x in all_winners + all_losers if smin <= x['iv_skew'] < smax]
        if t:
            print(f"  IVSkew {label:<15} wins={len(w):<4} total={len(t):<4} wr={len(w)/len(t)*100:.1f}%")
    
    # Analyze direction
    print(f"\nBy Direction:")
    for d in ['BUY_CALL', 'BUY_PUT']:
        w = [x for x in all_winners if x['direction'] == d]
        t = [x for x in all_winners + all_losers if x['direction'] == d]
        if t:
            print(f"  {d:<10} wins={len(w):<4} total={len(t):<4} wr={len(w)/len(t)*100:.1f}%")
    
    # BEST SINGLE ENTRY PER DAY (earliest winner, verdict-aligned)
    print(f"\n\n{'='*100}")
    print("BEST STRATEGY: First verdict-aligned winning entry per day")
    print(f"{'='*100}")
    
    best_per_day = []
    for ds in day_summary:
        if not ds['has_win']:
            print(f"  {ds['day']}: NO winning entry available")
            continue
        # First verdict-aligned winner
        day_wins = [w for w in all_winners if w['entry_time'].strftime('%Y-%m-%d') == ds['day'] and w['verdict_aligned']]
        if day_wins:
            day_wins.sort(key=lambda x: x['entry_time'])
            best = day_wins[0]
            best_per_day.append(best)
            print(f"  {best['entry_time'].strftime('%Y-%m-%d %H:%M')} {best['direction']:<10} {best['strike']} "
                  f"v={best['verdict']:<20} conf={best['confidence']:.0f}% VIX={best['vix']:.1f} "
                  f"entry={best['entry_premium']:.2f} P&L={best['pnl_pct']:+.1f}%")
    
    print(f"\n  Days with aligned winners: {len(best_per_day)}/{len(days)}")
    if best_per_day:
        print(f"  Hypothetical WR: {len(best_per_day)}/{len(days)} = {len(best_per_day)/len(days)*100:.1f}%")
        print(f"  Total P&L: {sum(b['pnl_pct'] for b in best_per_day):+.1f}%")
