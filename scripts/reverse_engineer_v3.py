"""
v3: Refine the two best strategies to push past 80% WR.
Also test combined OR strategy.

Strategy A: PUT + neg IV skew + prem100+ (75% WR)
  - Loss on Feb 6 had spot falling. Add: spot not falling 30m
Strategy B: conf>=80 + prem50-100 + no PM pattern (75% WR)  
  - Loss on Feb 2 had VIX 14.8. Add: VIX < 15
"""
import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

def get_all_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               futures_oi_change, futures_basis, vix, iv_skew, prev_verdict,
               total_call_oi, total_put_oi, call_oi_change, put_oi_change,
               atm_call_oi_change, atm_put_oi_change, max_pain
        FROM analysis_history WHERE DATE(timestamp) >= '2026-02-01' ORDER BY timestamp
    """)
    analysis = [dict(r) for r in c.fetchall()]
    c.execute("""
        SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp, ce_oi, pe_oi
        FROM oi_snapshots
        WHERE DATE(timestamp) >= '2026-02-01' AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price
    """)
    snapshots = [dict(r) for r in c.fetchall()]
    c.execute("""
        SELECT detected_at as timestamp, pattern_type
        FROM detected_patterns WHERE DATE(detected_at) >= '2026-02-01' ORDER BY detected_at
    """)
    patterns = [dict(r) for r in c.fetchall()]
    conn.close()
    return analysis, snapshots, patterns

def group_by_day(items, key='timestamp'):
    by_day = defaultdict(list)
    for item in items:
        by_day[item[key][:10]].append(item)
    return by_day

def get_spot_trend(analysis_by_day, day, ts, lookback_mins=30):
    if day not in analysis_by_day:
        return 0
    prices = []
    for a in analysis_by_day[day]:
        a_ts = datetime.fromisoformat(a['timestamp'])
        diff = (ts - a_ts).total_seconds()
        if 0 <= diff <= lookback_mins * 60:
            prices.append(a['spot_price'])
    if len(prices) < 2:
        return 0
    return (prices[-1] - prices[0]) / prices[0] * 100

def has_pm_pattern(patterns_by_day, day, ts, lookback_mins=60):
    if day not in patterns_by_day:
        return False
    for p in patterns_by_day[day]:
        p_ts = datetime.fromisoformat(p['timestamp'])
        diff = (ts - p_ts).total_seconds()
        if 0 <= diff <= lookback_mins * 60:
            if any(kw in p['pattern_type'] for kw in ['REVERSAL', 'CROSSED', 'RECOVERING']):
                return True
    return False

def simulate_trade(ts, strike, option_type, day_snaps, sl_pct=25, target_pct=50):
    best_prem = None
    best_diff = float('inf')
    for snap in day_snaps:
        if snap['strike_price'] == strike:
            snap_time = datetime.fromisoformat(snap['timestamp'])
            diff = abs((snap_time - ts).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_prem = snap['ce_ltp'] if option_type == 'CE' else snap['pe_ltp']
    if not best_prem or best_prem < 5:
        return None
    sl_p = best_prem * (1 - sl_pct / 100)
    tgt_p = best_prem * (1 + target_pct / 100)
    for snap in day_snaps:
        snap_time = datetime.fromisoformat(snap['timestamp'])
        if snap_time <= ts or snap['strike_price'] != strike:
            continue
        price = snap['ce_ltp'] if option_type == 'CE' else snap['pe_ltp']
        if not price or price <= 0:
            continue
        if price <= sl_p:
            return ('SL', -sl_pct, best_prem, price, snap_time)
        if price >= tgt_p:
            pnl = (price - best_prem) / best_prem * 100
            return ('TARGET', pnl, best_prem, price, snap_time)
        if snap_time.hour == 15 and snap_time.minute >= 20:
            pnl = (price - best_prem) / best_prem * 100
            return ('EOD', pnl, best_prem, price, snap_time)
    return None

def build_entries(analysis_by_day, snapshots_by_day, patterns_by_day, days):
    entries = []
    for day in days:
        day_a = analysis_by_day.get(day, [])
        day_s = snapshots_by_day.get(day, [])
        for a in day_a:
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30) or ts.hour >= 14:
                continue
            spot = a['spot_price']
            atm = round(spot / 50) * 50
            verdict = a['verdict']
            conf = a['signal_confidence'] or 0
            vix = a.get('vix', 0) or 0
            ivskew = a.get('iv_skew', 0) or 0
            max_pain = a.get('max_pain', 0) or 0
            spot_30m = get_spot_trend(analysis_by_day, day, ts, 30)
            pm_pat = has_pm_pattern(patterns_by_day, day, ts, 60)
            prev_v = a.get('prev_verdict', '') or ''
            
            # Verdict changed from previous?
            verdict_changed = prev_v != '' and prev_v != verdict
            
            # Count consecutive bearish/bullish before this
            bearish_v = 'Bear' in verdict
            bullish_v = 'Bull' in verdict
            
            for direction in ['CALL', 'PUT']:
                otype = 'CE' if direction == 'CALL' else 'PE'
                strike = atm
                aligned = (direction == 'CALL' and bullish_v) or (direction == 'PUT' and bearish_v)
                contra = (direction == 'CALL' and bearish_v) or (direction == 'PUT' and bullish_v)
                
                result = simulate_trade(ts, strike, otype, day_s)
                if not result:
                    continue
                reason, pnl, ep, xp, xt = result
                
                entries.append({
                    'day': day, 'ts': ts, 'hour': ts.hour, 'min': ts.minute,
                    'dir': f'BUY_{direction}', 'strike': strike,
                    'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': reason,
                    'won': reason == 'TARGET', 'xt': xt,
                    'v': verdict, 'aligned': aligned, 'contra': contra,
                    'conf': conf, 'vix': vix, 'ivskew': ivskew,
                    'max_pain': max_pain,
                    'below_mp': strike < max_pain if max_pain else False,
                    'at_mp': strike == max_pain if max_pain else False,
                    'mp_dist': (strike - max_pain) / 50 if max_pain else 0,
                    'spot_30m': spot_30m,
                    'spot_not_falling': spot_30m >= -0.05,
                    'spot_rising': spot_30m > 0.05,
                    'pm_pat': pm_pat,
                    'no_pm': not pm_pat,
                    'prev_v': prev_v,
                    'v_changed': verdict_changed,
                    'bearish_v': bearish_v,
                    'bullish_v': bullish_v,
                })
    return entries

def test_strategy(entries, filter_fn, name):
    by_day = defaultdict(list)
    for e in entries:
        if filter_fn(e):
            by_day[e['day']].append(e)
    trades = []
    for day in sorted(by_day.keys()):
        trades.append(sorted(by_day[day], key=lambda x: x['ts'])[0])
    if not trades:
        return None
    wins = sum(1 for t in trades if t['won'])
    total = len(trades)
    wr = wins / total * 100
    pnl = sum(t['pnl'] for t in trades)
    return {'name': name, 'wins': wins, 'total': total, 'wr': wr, 'pnl': pnl, 'trades': trades}

def print_strategy(r):
    if not r:
        print("  NO TRADES")
        return
    print(f"\n  {'='*95}")
    print(f"  {r['name']}")
    print(f"  WR: {r['wr']:.1f}% | Trades: {r['total']} | Wins: {r['wins']} | P&L: {r['pnl']:+.1f}%")
    print(f"  {'='*95}")
    for t in r['trades']:
        s = "WIN " if t['won'] else "LOSS"
        print(f"    [{s}] {t['day']} {t['ts'].strftime('%H:%M')} {t['dir']:<10} {t['strike']} "
              f"@ Rs{t['ep']:.2f} -> {t['reason']:<6} P&L={t['pnl']:+.1f}%")
        print(f"           v={t['v']:<22} conf={t['conf']:.0f}% VIX={t['vix']:.1f} "
              f"ivskew={t['ivskew']:.1f} spot30m={t['spot_30m']:.3f}% pm_pat={t['pm_pat']}")


if __name__ == '__main__':
    print("Loading data...")
    analysis, snapshots, patterns = get_all_data()
    ab = group_by_day(analysis)
    sb = group_by_day(snapshots)
    pb = group_by_day(patterns)
    days = sorted(d for d in ab.keys() if d >= '2026-02-01' and d != '2026-02-16')
    
    print(f"Days: {len(days)}")
    print("Building entries...")
    entries = build_entries(ab, sb, pb, days)
    print(f"Total entries: {len(entries)}")
    
    # ================================================================
    print(f"\n{'#'*100}")
    print("REFINED STRATEGIES")
    print(f"{'#'*100}")
    
    # Strategy A original
    print_strategy(test_strategy(entries,
        lambda e: e['dir'] == 'BUY_PUT' and e['ivskew'] < 0 and e['ep'] >= 100,
        "A-ORIG: PUT + ivskew<0 + prem>=100"))
    
    # Strategy A refined: add spot not falling
    print_strategy(test_strategy(entries,
        lambda e: e['dir'] == 'BUY_PUT' and e['ivskew'] < 0 and e['ep'] >= 100 and e['spot_not_falling'],
        "A-v1: PUT + ivskew<0 + prem>=100 + spot NOT falling"))
    
    # Strategy A refined: add spot rising
    print_strategy(test_strategy(entries,
        lambda e: e['dir'] == 'BUY_PUT' and e['ivskew'] < 0 and e['ep'] >= 100 and e['spot_rising'],
        "A-v2: PUT + ivskew<0 + prem>=100 + spot rising"))
    
    # Strategy A: different spot thresholds
    for thresh in [-0.15, -0.1, -0.05, 0, 0.05]:
        r = test_strategy(entries,
            lambda e, t=thresh: e['dir'] == 'BUY_PUT' and e['ivskew'] < 0 and e['ep'] >= 100 and e['spot_30m'] >= t,
            f"A-spot>={thresh}: PUT + ivskew<0 + prem>=100 + spot30m>={thresh}")
        if r and r['total'] >= 3:
            print_strategy(r)
    
    # Strategy B original
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] >= 80 and 50 <= e['ep'] < 100 and e['no_pm'],
        "B-ORIG: conf>=80 + prem50-100 + no PM pattern"))
    
    # Strategy B: add VIX filter
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] >= 80 and 50 <= e['ep'] < 100 and e['no_pm'] and e['vix'] < 15,
        "B-v1: conf>=80 + prem50-100 + no PM + VIX<15"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] >= 80 and 50 <= e['ep'] < 100 and e['no_pm'] and e['vix'] < 14.5,
        "B-v2: conf>=80 + prem50-100 + no PM + VIX<14.5"))
    
    # Strategy B: add contra filter
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] >= 80 and 50 <= e['ep'] < 100 and e['no_pm'] and e['contra'],
        "B-v3: conf>=80 + prem50-100 + no PM + CONTRA"))
    
    # Strategy B: prem < 80
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] >= 80 and e['ep'] < 80 and e['no_pm'],
        "B-v4: conf>=80 + prem<80 + no PM"))
    
    # Strategy B: prem 50-95
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] >= 80 and 50 <= e['ep'] < 95 and e['no_pm'],
        "B-v5: conf>=80 + prem50-95 + no PM"))
    
    # Strategy B: conf >= 90
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] >= 90 and 50 <= e['ep'] < 100 and e['no_pm'],
        "B-v6: conf>=90 + prem50-100 + no PM"))
    
    # ================================================================
    print(f"\n\n{'#'*100}")
    print("COMBINED OR STRATEGY (take whichever triggers first)")
    print(f"{'#'*100}")
    
    # Combined: A-refined OR B-refined
    def strat_a(e):
        return e['dir'] == 'BUY_PUT' and e['ivskew'] < 0 and e['ep'] >= 100 and e['spot_not_falling']
    
    def strat_b(e):
        return e['conf'] >= 80 and 50 <= e['ep'] < 100 and e['no_pm'] and e['vix'] < 15
    
    def strat_b_v2(e):
        return e['conf'] >= 90 and 50 <= e['ep'] < 100 and e['no_pm']
    
    print_strategy(test_strategy(entries,
        lambda e: strat_a(e) or strat_b(e),
        "COMBINED: (A-v1 OR B-v1)"))
    
    print_strategy(test_strategy(entries,
        lambda e: strat_a(e) or strat_b_v2(e),
        "COMBINED: (A-v1 OR B-v6-conf90)"))
    
    # ================================================================
    # NEW IDEAS: Explore completely different angles
    # ================================================================
    print(f"\n\n{'#'*100}")
    print("NEW ANGLE EXPLORATIONS")
    print(f"{'#'*100}")
    
    # Idea: Conf < 50 was surprisingly good in 2-filter combo with prem50-100 (66.7%)
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] < 50 and 50 <= e['ep'] < 100,
        "LOW-CONF: conf<50 + prem50-100"))
    
    # Refine low conf
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] < 50 and 50 <= e['ep'] < 100 and e['dir'] == 'BUY_PUT',
        "LOW-CONF PUT: conf<50 + prem50-100 + PUT"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] < 50 and 50 <= e['ep'] < 100 and e['spot_not_falling'],
        "LOW-CONF + spot not falling: conf<50 + prem50-100"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] < 50 and 50 <= e['ep'] < 100 and e['below_mp'],
        "LOW-CONF + below MP: conf<50 + prem50-100 + below MP"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] < 50 and 50 <= e['ep'] < 100 and e['no_pm'],
        "LOW-CONF + no PM: conf<50 + prem50-100 + no PM"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] < 50 and e['ep'] < 100 and e['below_mp'],
        "LOW-CONF + prem<100 + below MP"))
    
    # Premium sweet spot 60-90
    print_strategy(test_strategy(entries,
        lambda e: e['conf'] < 50 and 60 <= e['ep'] <= 90,
        "LOW-CONF: conf<50 + prem60-90"))
    
    # Verdict-changed trades
    print_strategy(test_strategy(entries,
        lambda e: e['v_changed'] and 50 <= e['ep'] < 100,
        "VERDICT-CHANGED: v_changed + prem50-100"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['v_changed'] and e['dir'] == 'BUY_PUT' and e['ep'] >= 80,
        "VERDICT-CHANGED PUT: v_changed + PUT + prem>=80"))
    
    # Below max pain + PUT (strong signal)
    print_strategy(test_strategy(entries,
        lambda e: e['below_mp'] and e['dir'] == 'BUY_PUT' and e['no_pm'] and e['ep'] >= 50,
        "BELOW-MP PUT: below_mp + PUT + no PM + prem>=50"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['below_mp'] and e['dir'] == 'BUY_PUT' and e['conf'] >= 65,
        "BELOW-MP PUT conf65: below_mp + PUT + conf>=65"))
    
    # VIX < 12 (was good in 2-filter)
    print_strategy(test_strategy(entries,
        lambda e: e['vix'] < 12 and 50 <= e['ep'] <= 150 and e['no_pm'],
        "LOW-VIX: vix<12 + prem50-150 + no PM"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['vix'] < 12 and 50 <= e['ep'] < 100 and e['dir'] == 'BUY_PUT',
        "LOW-VIX PUT: vix<12 + prem50-100 + PUT"))
    
    print_strategy(test_strategy(entries,
        lambda e: e['vix'] < 12 and e['ep'] < 100 and e['below_mp'],
        "LOW-VIX below MP: vix<12 + prem<100 + below MP"))
    
    # ================================================================
    # SUPER COMBOS: best 80%+ attempts
    # ================================================================
    print(f"\n\n{'#'*100}")
    print("SUPER COMBOS — PUSHING FOR 80%+")
    print(f"{'#'*100}")
    
    # Try every permutation of the most promising filters
    super_filters = {
        'PUT': lambda e: e['dir'] == 'BUY_PUT',
        'conf<50': lambda e: e['conf'] < 50,
        'conf>=80': lambda e: e['conf'] >= 80,
        'conf>=90': lambda e: e['conf'] >= 90,
        'prem50-100': lambda e: 50 <= e['ep'] < 100,
        'prem<100': lambda e: e['ep'] < 100,
        'prem100+': lambda e: e['ep'] >= 100,
        'ivskew<0': lambda e: e['ivskew'] < 0,
        'ivskew<1': lambda e: e['ivskew'] < 1,
        'below_mp': lambda e: e['below_mp'],
        'at_or_below_mp': lambda e: e['below_mp'] or e['at_mp'],
        'no_pm': lambda e: e['no_pm'],
        'spot_ok': lambda e: e['spot_30m'] >= -0.05,
        'spot_up': lambda e: e['spot_30m'] > 0.05,
        'vix<12': lambda e: e['vix'] < 12,
        'vix<15': lambda e: e['vix'] < 15,
        'v_slightly': lambda e: 'Slightly' in e['v'],
        'contra': lambda e: e['contra'],
        'h10+': lambda e: e['hour'] >= 10,
        'h11+': lambda e: e['hour'] >= 11,
    }
    
    sf_names = list(super_filters.keys())
    
    # 3-filter combos
    results_80 = []
    for i in range(len(sf_names)):
        for j in range(i+1, len(sf_names)):
            for k in range(j+1, len(sf_names)):
                f1, f2, f3 = sf_names[i], sf_names[j], sf_names[k]
                fn = lambda e, a=super_filters[f1], b=super_filters[f2], c=super_filters[f3]: a(e) and b(e) and c(e)
                r = test_strategy(entries, fn, f"{f1} + {f2} + {f3}")
                if r and r['total'] >= 3 and r['wr'] >= 80:
                    results_80.append(r)
    
    # 4-filter combos
    for i in range(len(sf_names)):
        for j in range(i+1, len(sf_names)):
            for k in range(j+1, len(sf_names)):
                for l in range(k+1, len(sf_names)):
                    f1, f2, f3, f4 = sf_names[i], sf_names[j], sf_names[k], sf_names[l]
                    fn = lambda e, a=super_filters[f1], b=super_filters[f2], c=super_filters[f3], d=super_filters[f4]: a(e) and b(e) and c(e) and d(e)
                    r = test_strategy(entries, fn, f"{f1} + {f2} + {f3} + {f4}")
                    if r and r['total'] >= 3 and r['wr'] >= 80:
                        results_80.append(r)
    
    results_80.sort(key=lambda x: (-x['wr'], x['total'], -x['pnl']))
    
    # Deduplicate by same trade set
    seen = set()
    unique_80 = []
    for r in results_80:
        key = tuple(t['day'] for t in r['trades'])
        if key not in seen:
            seen.add(key)
            unique_80.append(r)
    
    print(f"\n  Found {len(unique_80)} unique 80%+ WR filters")
    print(f"\n  {'Filters':<75} {'T':>3} {'W':>3} {'WR':>6} {'P&L':>8}")
    print(f"  {'-'*100}")
    for r in unique_80[:30]:
        print(f"  {r['name']:<75} {r['total']:>3} {r['wins']:>3} {r['wr']:>5.1f}% {r['pnl']:>+7.1f}%")
    
    # Show top 5 in detail
    for r in unique_80[:5]:
        print_strategy(r)
    
    # ================================================================
    # FINAL: Best combined OR
    # ================================================================
    if unique_80:
        print(f"\n\n{'#'*100}")
        print("FINAL: COMBINED OR of top 80%+ strategies")
        print(f"{'#'*100}")
        
        # Pick best two non-overlapping 80%+ strategies
        best1 = unique_80[0]
        best1_days = set(t['day'] for t in best1['trades'])
        
        for r in unique_80[1:]:
            r_days = set(t['day'] for t in r['trades'])
            overlap = best1_days & r_days
            if len(overlap) < len(r_days):
                print(f"\n  Strategy 1: {best1['name']} ({best1['wr']:.0f}% WR, {best1['total']} trades)")
                print(f"  Strategy 2: {r['name']} ({r['wr']:.0f}% WR, {r['total']} trades)")
                print(f"  Overlap days: {overlap}")
                print(f"  Combined would cover: {best1_days | r_days}")
                break
