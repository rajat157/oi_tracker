"""
Reverse-engineer v2: Deeper filter combos for 80%+ WR on 1:2 buying.
Focuses on the promising signals found in v1:
- PE buildup at ATM (42.5% WR)
- Strike below max pain (29.4% WR)  
- Premium 50-100 (30.9% WR)
- Negative IV skew (28.9% WR)
- VIX < 12 (33.3% as daily filter)
- Low confidence < 90 (better than high)
- PM reversal patterns near entry

Also explores:
- Combining 3-4 filters
- ATM OI ratios
- Spot momentum signals
- Time-based filters with other combos
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
        SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp, ce_oi, pe_oi,
               ce_volume, pe_volume, ce_iv, pe_iv
        FROM oi_snapshots
        WHERE DATE(timestamp) >= '2026-02-01' AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price
    """)
    snapshots = [dict(r) for r in c.fetchall()]
    
    c.execute("""
        SELECT timestamp, pm_score, spot_price as pm_spot, confidence as pm_confidence
        FROM pm_history WHERE DATE(timestamp) >= '2026-02-01' ORDER BY timestamp
    """)
    pm_data = [dict(r) for r in c.fetchall()]
    
    c.execute("""
        SELECT detected_at as timestamp, pattern_type, spot_price, pm_score as pat_pm_score,
               confidence as pat_confidence, verdict as pat_verdict, outcome
        FROM detected_patterns WHERE DATE(detected_at) >= '2026-02-01' ORDER BY detected_at
    """)
    patterns = [dict(r) for r in c.fetchall()]
    
    conn.close()
    return analysis, snapshots, pm_data, patterns

def group_by_day(items, key='timestamp'):
    by_day = defaultdict(list)
    for item in items:
        by_day[item[key][:10]].append(item)
    return by_day

def get_recent_patterns(patterns_by_day, day, ts, lookback_mins=60):
    if day not in patterns_by_day:
        return []
    result = []
    for p in patterns_by_day[day]:
        p_ts = datetime.fromisoformat(p['timestamp'])
        diff = (ts - p_ts).total_seconds()
        if 0 <= diff <= lookback_mins * 60:
            result.append(p)
    return result

def get_patterns_before_market(patterns_by_day, day):
    """Get patterns detected before 10:00 (early session patterns)"""
    if day not in patterns_by_day:
        return []
    return [p for p in patterns_by_day[day] 
            if datetime.fromisoformat(p['timestamp']).hour < 10]

def get_pm_near(pm_by_day, day, ts, window_mins=30):
    """Get nearest PM score within window"""
    if day not in pm_by_day:
        return None
    best = None
    best_diff = float('inf')
    for pm in pm_by_day[day]:
        pm_ts = datetime.fromisoformat(pm['timestamp'])
        diff = abs((pm_ts - ts).total_seconds())
        if diff < best_diff and diff <= window_mins * 60:
            best_diff = diff
            best = pm
    return best

def get_spot_trend(analysis_by_day, day, ts, lookback_mins):
    """Get spot price trend stats over lookback period"""
    if day not in analysis_by_day:
        return {}
    prices = []
    for a in analysis_by_day[day]:
        a_ts = datetime.fromisoformat(a['timestamp'])
        diff = (ts - a_ts).total_seconds()
        if 0 <= diff <= lookback_mins * 60:
            prices.append(a['spot_price'])
    if len(prices) < 2:
        return {}
    return {
        'move_pct': (prices[-1] - prices[0]) / prices[0] * 100,
        'range_pct': (max(prices) - min(prices)) / prices[0] * 100,
        'trending_down': prices[-1] < prices[0],
        'high_range': (max(prices) - min(prices)) / prices[0] * 100 > 0.3,
    }

def get_oi_context(analysis_by_day, day, ts):
    """Get OI context at the point of entry"""
    if day not in analysis_by_day:
        return {}
    best = None
    best_diff = float('inf')
    for a in analysis_by_day[day]:
        a_ts = datetime.fromisoformat(a['timestamp'])
        diff = abs((ts - a_ts).total_seconds())
        if diff < best_diff:
            best_diff = diff
            best = a
    if not best:
        return {}
    
    total_call = best.get('total_call_oi') or 0
    total_put = best.get('total_put_oi') or 0
    pcr = total_put / total_call if total_call > 0 else 0
    
    call_chg = best.get('call_oi_change') or 0
    put_chg = best.get('put_oi_change') or 0
    atm_ce_chg = best.get('atm_call_oi_change') or 0
    atm_pe_chg = best.get('atm_put_oi_change') or 0
    
    return {
        'pcr': pcr,
        'call_oi_change': call_chg,
        'put_oi_change': put_chg,
        'atm_ce_oi_chg': atm_ce_chg,
        'atm_pe_oi_chg': atm_pe_chg,
        'pe_buildup': atm_pe_chg > atm_ce_chg and atm_pe_chg > 0,
        'ce_buildup': atm_ce_chg > atm_pe_chg and atm_ce_chg > 0,
        'put_oi_dominant': put_chg > call_chg,
        'max_pain': best.get('max_pain') or 0,
    }

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
    max_p = best_prem
    min_p = best_prem
    
    for snap in day_snaps:
        snap_time = datetime.fromisoformat(snap['timestamp'])
        if snap_time <= ts or snap['strike_price'] != strike:
            continue
        price = snap['ce_ltp'] if option_type == 'CE' else snap['pe_ltp']
        if not price or price <= 0:
            continue
        max_p = max(max_p, price)
        min_p = min(min_p, price)
        if price <= sl_p:
            return ('SL', -sl_pct, best_prem, price, snap_time, max_p, min_p)
        if price >= tgt_p:
            pnl = (price - best_prem) / best_prem * 100
            return ('TARGET', pnl, best_prem, price, snap_time, max_p, min_p)
        if snap_time.hour == 15 and snap_time.minute >= 20:
            pnl = (price - best_prem) / best_prem * 100
            return ('EOD', pnl, best_prem, price, snap_time, max_p, min_p)
    return None


def build_enriched_entries(analysis_by_day, snapshots_by_day, pm_by_day, patterns_by_day, days):
    """Build all possible entries with rich feature set"""
    all_entries = []
    
    for day in days:
        day_analysis = analysis_by_day.get(day, [])
        day_snaps = snapshots_by_day.get(day, [])
        
        for a in day_analysis:
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30) or ts.hour >= 14:
                continue
            
            spot = a['spot_price']
            atm = round(spot / 50) * 50
            verdict = a['verdict']
            confidence = a['signal_confidence'] or 0
            vix = a.get('vix', 0) or 0
            iv_skew = a.get('iv_skew', 0) or 0
            max_pain = a.get('max_pain', 0) or 0
            
            # Rich context
            oi_ctx = get_oi_context(analysis_by_day, day, ts)
            spot_30m = get_spot_trend(analysis_by_day, day, ts, 30)
            spot_60m = get_spot_trend(analysis_by_day, day, ts, 60)
            recent_pats = get_recent_patterns(patterns_by_day, day, ts, 60)
            pm = get_pm_near(pm_by_day, day, ts, 30)
            early_pats = get_patterns_before_market(patterns_by_day, day)
            
            pat_types = [p['pattern_type'] for p in recent_pats]
            has_reversal = any('REVERSAL' in p for p in pat_types)
            has_strong_reversal = any('STRONG_REVERSAL' in p for p in pat_types)
            has_crossed = any('CROSSED' in p for p in pat_types)
            has_recovering = any('RECOVERING' in p for p in pat_types)
            has_any_pm_pattern = has_reversal or has_crossed or has_recovering
            
            for direction in ['CALL', 'PUT']:
                option_type = 'CE' if direction == 'CALL' else 'PE'
                strike = atm
                
                is_bearish_v = 'Bear' in verdict
                is_bullish_v = 'Bull' in verdict
                is_aligned = (direction == 'CALL' and is_bullish_v) or (direction == 'PUT' and is_bearish_v)
                is_contra = (direction == 'CALL' and is_bearish_v) or (direction == 'PUT' and is_bullish_v)
                
                result = simulate_trade(ts, strike, option_type, day_snaps)
                if not result:
                    continue
                
                exit_reason, pnl, entry_p, exit_p, exit_t, max_p, min_p = result
                won = exit_reason == 'TARGET'
                drawdown = (min_p - entry_p) / entry_p * 100
                
                below_mp = strike < max_pain if max_pain else False
                at_mp = strike == max_pain if max_pain else False
                mp_dist = (strike - max_pain) / 50 if max_pain else 0
                
                entry = {
                    'day': day, 'ts': ts, 'hour': ts.hour, 'minute': ts.minute,
                    'direction': f'BUY_{direction}', 'strike': strike,
                    'entry_p': entry_p, 'exit_p': exit_p, 'pnl': pnl,
                    'exit_reason': exit_reason, 'won': won, 'exit_time': exit_t,
                    'max_drawdown': drawdown,
                    # Market
                    'verdict': verdict, 'aligned': is_aligned, 'contra': is_contra,
                    'confidence': confidence, 'vix': vix, 'iv_skew': iv_skew,
                    # OI
                    'pcr': oi_ctx.get('pcr', 0),
                    'pe_buildup': oi_ctx.get('pe_buildup', False),
                    'ce_buildup': oi_ctx.get('ce_buildup', False),
                    'put_oi_dominant': oi_ctx.get('put_oi_dominant', False),
                    'atm_pe_chg': oi_ctx.get('atm_pe_oi_chg', 0),
                    'atm_ce_chg': oi_ctx.get('atm_ce_oi_chg', 0),
                    # Max pain
                    'below_mp': below_mp, 'at_mp': at_mp, 'mp_dist': mp_dist,
                    # Spot
                    'spot_down_30m': spot_30m.get('trending_down', False),
                    'spot_up_30m': not spot_30m.get('trending_down', True),
                    'spot_move_30m': spot_30m.get('move_pct', 0),
                    'high_range_30m': spot_30m.get('high_range', False),
                    'spot_move_60m': spot_60m.get('move_pct', 0),
                    # PM patterns
                    'has_reversal': has_reversal,
                    'has_strong_reversal': has_strong_reversal,
                    'has_crossed': has_crossed,
                    'has_recovering': has_recovering,
                    'has_any_pm_pattern': has_any_pm_pattern,
                    'pm_score': pm['pm_score'] if pm else None,
                    'n_recent_patterns': len(recent_pats),
                    'n_early_patterns': len(early_pats),
                    # Futures
                    'fut_oi': a.get('futures_oi_change', 0) or 0,
                    'basis': a.get('futures_basis', 0) or 0,
                }
                all_entries.append(entry)
    
    return all_entries


def test_filter(entries, filter_fn, name=""):
    """Pick first qualifying entry per day, return stats"""
    by_day = defaultdict(list)
    for e in entries:
        if filter_fn(e):
            by_day[e['day']].append(e)
    
    trades = []
    for day in sorted(by_day.keys()):
        candidates = sorted(by_day[day], key=lambda x: x['ts'])
        trades.append(candidates[0])
    
    if not trades:
        return None
    
    wins = sum(1 for t in trades if t['won'])
    total = len(trades)
    wr = wins / total * 100
    pnl = sum(t['pnl'] for t in trades)
    
    return {'name': name, 'wins': wins, 'total': total, 'wr': wr, 'pnl': pnl, 'trades': trades}


if __name__ == '__main__':
    print("Loading data...")
    analysis, snapshots, pm_data, patterns = get_all_data()
    analysis_by_day = group_by_day(analysis)
    snapshots_by_day = group_by_day(snapshots)
    pm_by_day = group_by_day(pm_data)
    patterns_by_day = group_by_day(patterns)
    
    days = sorted(d for d in analysis_by_day.keys() if d >= '2026-02-01' and d != '2026-02-16')
    print(f"Days: {len(days)}")
    
    print("\nBuilding enriched entries...")
    entries = build_enriched_entries(analysis_by_day, snapshots_by_day, pm_by_day, patterns_by_day, days)
    winners = [e for e in entries if e['won']]
    print(f"Total entries: {len(entries)}, Winners: {len(winners)}")
    
    # ================================================================
    # EXHAUSTIVE FILTER SEARCH
    # ================================================================
    
    # Define atomic filters
    filters = {
        # Direction
        'PUT': lambda e: e['direction'] == 'BUY_PUT',
        'CALL': lambda e: e['direction'] == 'BUY_CALL',
        
        # Alignment
        'aligned': lambda e: e['aligned'],
        'contra': lambda e: e['contra'],
        
        # VIX
        'vix<11.5': lambda e: e['vix'] < 11.5,
        'vix<12': lambda e: e['vix'] < 12,
        'vix>12': lambda e: e['vix'] > 12,
        'vix>13': lambda e: e['vix'] > 13,
        'vix>14': lambda e: e['vix'] > 14,
        
        # IV Skew
        'ivskew<0': lambda e: e['iv_skew'] < 0,
        'ivskew<1': lambda e: e['iv_skew'] < 1,
        'ivskew>2': lambda e: e['iv_skew'] > 2,
        
        # Confidence
        'conf<50': lambda e: e['confidence'] < 50,
        'conf<65': lambda e: e['confidence'] < 65,
        'conf<80': lambda e: e['confidence'] < 80,
        'conf>=65': lambda e: e['confidence'] >= 65,
        'conf>=80': lambda e: e['confidence'] >= 80,
        
        # Premium
        'prem<100': lambda e: e['entry_p'] < 100,
        'prem50-100': lambda e: 50 <= e['entry_p'] < 100,
        'prem50-150': lambda e: 50 <= e['entry_p'] <= 150,
        'prem100+': lambda e: e['entry_p'] >= 100,
        'prem75-125': lambda e: 75 <= e['entry_p'] <= 125,
        
        # Max pain
        'below_mp': lambda e: e['below_mp'],
        'at_or_below_mp': lambda e: e['below_mp'] or e['at_mp'],
        'mp_dist<=-1': lambda e: e['mp_dist'] <= -1,
        
        # OI
        'pe_buildup': lambda e: e['pe_buildup'],
        'ce_buildup': lambda e: e['ce_buildup'],
        'put_oi_dom': lambda e: e['put_oi_dominant'],
        
        # Time windows
        'h09': lambda e: e['hour'] == 9,
        'h10': lambda e: e['hour'] == 10,
        'h11': lambda e: e['hour'] == 11,
        'h12': lambda e: e['hour'] == 12,
        'h13': lambda e: e['hour'] == 13,
        '9:30-10:30': lambda e: (e['hour'] == 9 and e['minute'] >= 30) or (e['hour'] == 10 and e['minute'] < 30),
        '10-12': lambda e: 10 <= e['hour'] < 12,
        '10-11': lambda e: e['hour'] == 10,
        '11-13': lambda e: 11 <= e['hour'] < 13,
        '12-14': lambda e: 12 <= e['hour'] < 14,
        'after10:30': lambda e: e['hour'] > 10 or (e['hour'] == 10 and e['minute'] >= 30),
        
        # Spot movement
        'spot_down_30m': lambda e: e['spot_down_30m'],
        'spot_up_30m': lambda e: e['spot_up_30m'],
        'spot_big_move': lambda e: abs(e['spot_move_30m']) > 0.2,
        'spot_flat_30m': lambda e: abs(e['spot_move_30m']) < 0.1,
        
        # PM patterns
        'has_pm_reversal': lambda e: e['has_reversal'],
        'has_pm_any': lambda e: e['has_any_pm_pattern'],
        'has_strong_rev': lambda e: e['has_strong_reversal'],
        'no_pm_pattern': lambda e: not e['has_any_pm_pattern'],
        'early_patterns': lambda e: e['n_early_patterns'] > 0,
        
        # Futures
        'fut_oi_neg': lambda e: e['fut_oi'] < 0,
        'fut_oi_pos': lambda e: e['fut_oi'] > 0,
        'basis>50': lambda e: e['basis'] > 50,
        
        # Verdict types
        'v_slightly': lambda e: 'Slightly' in e['verdict'],
        'v_strong': lambda e: 'Winning' in e['verdict'] or 'Strongly' in e['verdict'],
        'v_bearish_any': lambda e: 'Bear' in e['verdict'],
        'v_bullish_any': lambda e: 'Bull' in e['verdict'],
    }
    
    filter_names = list(filters.keys())
    
    # ================================================================
    # 2-FILTER COMBOS
    # ================================================================
    print(f"\n{'='*100}")
    print("2-FILTER COMBOS (WR >= 60%, trades >= 3)")
    print(f"{'='*100}")
    
    combo2_results = []
    for i in range(len(filter_names)):
        for j in range(i+1, len(filter_names)):
            f1, f2 = filter_names[i], filter_names[j]
            combined = lambda e, a=filters[f1], b=filters[f2]: a(e) and b(e)
            r = test_filter(entries, combined, f"{f1} + {f2}")
            if r and r['total'] >= 3 and r['wr'] >= 60:
                combo2_results.append(r)
    
    combo2_results.sort(key=lambda x: (-x['wr'], -x['pnl']))
    print(f"\n  {'Filters':<55} {'T':>3} {'W':>3} {'WR':>6} {'P&L':>8}")
    print(f"  {'-'*80}")
    for r in combo2_results[:25]:
        print(f"  {r['name']:<55} {r['total']:>3} {r['wins']:>3} {r['wr']:>5.1f}% {r['pnl']:>+7.1f}%")
    
    # ================================================================
    # 3-FILTER COMBOS
    # ================================================================
    print(f"\n{'='*100}")
    print("3-FILTER COMBOS (WR >= 67%, trades >= 3)")
    print(f"{'='*100}")
    
    combo3_results = []
    for i in range(len(filter_names)):
        for j in range(i+1, len(filter_names)):
            for k in range(j+1, len(filter_names)):
                f1, f2, f3 = filter_names[i], filter_names[j], filter_names[k]
                combined = lambda e, a=filters[f1], b=filters[f2], c=filters[f3]: a(e) and b(e) and c(e)
                r = test_filter(entries, combined, f"{f1} + {f2} + {f3}")
                if r and r['total'] >= 3 and r['wr'] >= 67:
                    combo3_results.append(r)
    
    combo3_results.sort(key=lambda x: (-x['wr'], -x['pnl']))
    print(f"\n  {'Filters':<70} {'T':>3} {'W':>3} {'WR':>6} {'P&L':>8}")
    print(f"  {'-'*95}")
    for r in combo3_results[:40]:
        print(f"  {r['name']:<70} {r['total']:>3} {r['wins']:>3} {r['wr']:>5.1f}% {r['pnl']:>+7.1f}%")
    
    # ================================================================
    # 4-FILTER COMBOS (only among top 3-filter combos' filters)
    # ================================================================
    print(f"\n{'='*100}")
    print("4-FILTER COMBOS (WR >= 75%, trades >= 3)")
    print(f"{'='*100}")
    
    # Get most promising filter names from 3-combos
    promising = set()
    for r in combo3_results[:20]:
        for f in r['name'].split(' + '):
            promising.add(f)
    promising = sorted(promising)
    
    combo4_results = []
    for i in range(len(promising)):
        for j in range(i+1, len(promising)):
            for k in range(j+1, len(promising)):
                for l in range(k+1, len(promising)):
                    f1, f2, f3, f4 = promising[i], promising[j], promising[k], promising[l]
                    if f1 not in filters or f2 not in filters or f3 not in filters or f4 not in filters:
                        continue
                    combined = lambda e, a=filters[f1], b=filters[f2], c=filters[f3], d=filters[f4]: a(e) and b(e) and c(e) and d(e)
                    r = test_filter(entries, combined, f"{f1} + {f2} + {f3} + {f4}")
                    if r and r['total'] >= 3 and r['wr'] >= 75:
                        combo4_results.append(r)
    
    combo4_results.sort(key=lambda x: (-x['wr'], -x['pnl']))
    print(f"\n  {'Filters':<85} {'T':>3} {'W':>3} {'WR':>6} {'P&L':>8}")
    print(f"  {'-'*110}")
    for r in combo4_results[:30]:
        print(f"  {r['name']:<85} {r['total']:>3} {r['wins']:>3} {r['wr']:>5.1f}% {r['pnl']:>+7.1f}%")
    
    # ================================================================
    # SHOW BEST RESULTS IN DETAIL
    # ================================================================
    # Collect all results with WR >= 75%
    best_all = []
    for r in combo2_results + combo3_results + combo4_results:
        if r['wr'] >= 75 and r['total'] >= 3:
            best_all.append(r)
    
    best_all.sort(key=lambda x: (-x['wr'], -x['pnl']))
    best_all = best_all[:10]  # Top 10
    
    print(f"\n\n{'#'*100}")
    print("TOP 10 FILTERS (WR >= 75%, >= 3 trades) — DETAILED")
    print(f"{'#'*100}")
    
    for r in best_all:
        print(f"\n  {'='*90}")
        print(f"  {r['name']}")
        print(f"  WR: {r['wr']:.1f}% | Trades: {r['total']} | Wins: {r['wins']} | P&L: {r['pnl']:+.1f}%")
        print(f"  {'='*90}")
        for t in r['trades']:
            status = "WIN " if t['won'] else "LOSS"
            print(f"    [{status}] {t['day']} {t['ts'].strftime('%H:%M')} {t['direction']:<10} {t['strike']} "
                  f"@ Rs{t['entry_p']:.2f} -> {t['exit_reason']:<6} P&L={t['pnl']:+.1f}%")
            print(f"           v={t['verdict']:<22} conf={t['confidence']:.0f}% VIX={t['vix']:.1f} "
                  f"ivskew={t['iv_skew']:.1f} prem={t['entry_p']:.0f}")
            print(f"           mp_dist={t['mp_dist']:.0f} pe_build={t['pe_buildup']} "
                  f"pm_rev={t['has_reversal']} pm_any={t['has_any_pm_pattern']} "
                  f"spot30m={t['spot_move_30m']:.3f}%")
