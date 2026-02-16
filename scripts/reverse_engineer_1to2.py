"""
Reverse-engineer 1:2 RR winning trades.
For EVERY possible entry across all days, simulate 25/50 trade.
Then deep-analyze winners vs losers: what made winners win?

Goal: Find a filter that gives 80%+ WR even if it means fewer trades.
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
    
    # Analysis history
    c.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               futures_oi_change, futures_basis, vix, iv_skew, prev_verdict,
               total_call_oi, total_put_oi, call_oi_change, put_oi_change,
               atm_call_oi_change, atm_put_oi_change, max_pain
        FROM analysis_history
        WHERE DATE(timestamp) >= '2026-02-01'
        ORDER BY timestamp
    """)
    analysis = [dict(r) for r in c.fetchall()]
    
    # OI snapshots
    c.execute("""
        SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp, ce_oi, pe_oi,
               ce_volume, pe_volume, ce_iv, pe_iv
        FROM oi_snapshots
        WHERE DATE(timestamp) >= '2026-02-01' AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price
    """)
    snapshots = [dict(r) for r in c.fetchall()]
    
    # PM tracker data
    c.execute("""
        SELECT timestamp, pm_score, spot_price as pm_spot, verdict as pm_verdict,
               confidence as pm_confidence
        FROM pm_history
        WHERE DATE(timestamp) >= '2026-02-01'
        ORDER BY timestamp
    """)
    pm_data = [dict(r) for r in c.fetchall()]
    
    # Detected patterns
    c.execute("""
        SELECT detected_at as timestamp, pattern_type, spot_price, pm_score as pat_pm_score,
               confidence as pat_confidence, verdict as pat_verdict, outcome
        FROM detected_patterns
        WHERE DATE(detected_at) >= '2026-02-01'
        ORDER BY detected_at
    """)
    patterns = [dict(r) for r in c.fetchall()]
    
    # Trade setups (buying) - to see what Config B actually did
    c.execute("""
        SELECT * FROM trade_setups
        WHERE DATE(created_at) >= '2026-02-01'
        ORDER BY created_at
    """)
    existing_trades = [dict(r) for r in c.fetchall()]
    
    conn.close()
    return analysis, snapshots, pm_data, patterns, existing_trades

def group_by_day(items, key='timestamp'):
    by_day = defaultdict(list)
    for item in items:
        by_day[item[key][:10]].append(item)
    return by_day

def get_nearest_pm(pm_by_day, day, ts):
    """Get nearest PM data point to a timestamp"""
    if day not in pm_by_day:
        return None
    best = None
    best_diff = float('inf')
    for pm in pm_by_day[day]:
        pm_ts = datetime.fromisoformat(pm['timestamp'])
        diff = abs((pm_ts - ts).total_seconds())
        if diff < best_diff:
            best_diff = diff
            best = pm
    return best if best_diff < 600 else None  # within 10 min

def get_recent_patterns(patterns_by_day, day, ts, lookback_mins=30):
    """Get patterns detected in the last N minutes before entry"""
    if day not in patterns_by_day:
        return []
    result = []
    for p in patterns_by_day[day]:
        p_ts = datetime.fromisoformat(p['timestamp'])
        diff = (ts - p_ts).total_seconds()
        if 0 <= diff <= lookback_mins * 60:
            result.append(p)
    return result

def get_spot_movement(analysis_by_day, day, ts, lookback_mins=30):
    """Get spot price movement in last N minutes"""
    if day not in analysis_by_day:
        return None, None
    prices = []
    for a in analysis_by_day[day]:
        a_ts = datetime.fromisoformat(a['timestamp'])
        diff = (ts - a_ts).total_seconds()
        if 0 <= diff <= lookback_mins * 60:
            prices.append((a_ts, a['spot_price']))
    if len(prices) < 2:
        return None, None
    prices.sort()
    move = prices[-1][1] - prices[0][1]
    pct = (move / prices[0][1]) * 100
    return move, pct

def get_verdict_streak(analysis_by_day, day, ts, lookback_count=5):
    """Count consecutive same-direction verdicts before this point"""
    if day not in analysis_by_day:
        return 0, None
    prev = []
    for a in analysis_by_day[day]:
        a_ts = datetime.fromisoformat(a['timestamp'])
        if a_ts < ts:
            prev.append(a['verdict'])
    if not prev:
        return 0, None
    # Count streak from end
    last = prev[-1]
    direction = 'bearish' if 'Bear' in last else 'bullish' if 'Bull' in last else 'neutral'
    count = 0
    for v in reversed(prev):
        v_dir = 'bearish' if 'Bear' in v else 'bullish' if 'Bull' in v else 'neutral'
        if v_dir == direction:
            count += 1
        else:
            break
    return count, direction

def get_oi_buildup(snapshots_by_day, day, ts, strike, lookback_mins=30):
    """Get OI changes around the strike in last N minutes"""
    if day not in snapshots_by_day:
        return {}
    snaps_at_strike = []
    for s in snapshots_by_day[day]:
        if s['strike_price'] == strike:
            s_ts = datetime.fromisoformat(s['timestamp'])
            diff = (ts - s_ts).total_seconds()
            if 0 <= diff <= lookback_mins * 60:
                snaps_at_strike.append(s)
    if len(snaps_at_strike) < 2:
        return {}
    first = snaps_at_strike[0]
    last = snaps_at_strike[-1]
    return {
        'ce_oi_change': (last.get('ce_oi') or 0) - (first.get('ce_oi') or 0),
        'pe_oi_change': (last.get('pe_oi') or 0) - (first.get('pe_oi') or 0),
        'ce_vol': last.get('ce_volume') or 0,
        'pe_vol': last.get('pe_volume') or 0,
        'ce_iv': last.get('ce_iv') or 0,
        'pe_iv': last.get('pe_iv') or 0,
    }

def simulate_all_entries(analysis_by_day, snapshots_by_day, pm_by_day, patterns_by_day, days,
                          sl_pct=25, target_pct=50):
    """
    For every analysis snapshot, try BOTH directions.
    Collect rich feature set for each trade.
    """
    all_trades = []
    
    for day in days:
        day_analysis = analysis_by_day.get(day, [])
        day_snaps = snapshots_by_day.get(day, [])
        
        for a in day_analysis:
            ts = datetime.fromisoformat(a['timestamp'])
            # Full trading day 9:30-14:00
            if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30) or ts.hour >= 14:
                continue
            
            spot = a['spot_price']
            atm = round(spot / 50) * 50
            verdict = a['verdict']
            confidence = a['signal_confidence'] or 0
            vix = a.get('vix', 0) or 0
            iv_skew = a.get('iv_skew', 0) or 0
            fut_oi = a.get('futures_oi_change', 0) or 0
            basis = a.get('futures_basis', 0) or 0
            atm_call_oi_chg = a.get('atm_call_oi_change', 0) or 0
            atm_put_oi_chg = a.get('atm_put_oi_change', 0) or 0
            max_pain = a.get('max_pain', 0) or 0
            call_oi_chg = a.get('call_oi_change', 0) or 0
            put_oi_chg = a.get('put_oi_change', 0) or 0
            
            # PM data
            pm = get_nearest_pm(pm_by_day, day, ts)
            pm_score = pm['pm_score'] if pm else None
            pm_regime = None
            pm_trend = None
            pm_momentum = None
            
            # Recent patterns
            recent_pats = get_recent_patterns(patterns_by_day, day, ts, 30)
            pattern_types = [p['pattern_type'] for p in recent_pats]
            
            # Spot movement
            spot_move, spot_move_pct = get_spot_movement(analysis_by_day, day, ts, 30)
            
            # Verdict streak
            streak_count, streak_dir = get_verdict_streak(analysis_by_day, day, ts)
            
            for direction in ['CALL', 'PUT']:
                option_type = 'CE' if direction == 'CALL' else 'PE'
                strike = atm
                
                is_aligned = (
                    (direction == 'CALL' and ('Bullish' in verdict or 'Bulls' in verdict)) or
                    (direction == 'PUT' and ('Bearish' in verdict or 'Bears' in verdict))
                )
                
                # Get entry premium
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
                    continue
                
                sl_p = best_prem * (1 - sl_pct / 100)
                tgt_p = best_prem * (1 + target_pct / 100)
                
                exit_prem = None
                exit_reason = None
                exit_time = None
                max_prem = best_prem
                min_prem = best_prem
                time_to_exit = None
                
                for snap in day_snaps:
                    snap_time = datetime.fromisoformat(snap['timestamp'])
                    if snap_time <= ts or snap['strike_price'] != strike:
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
                if exit_time:
                    time_to_exit = (exit_time - ts).total_seconds() / 60
                
                # OI buildup at strike
                oi_info = get_oi_buildup(snapshots_by_day, day, ts, strike, 30)
                
                max_drawdown = ((min_prem - best_prem) / best_prem) * 100
                max_runup = ((max_prem - best_prem) / best_prem) * 100
                
                trade = {
                    'day': day,
                    'entry_time': ts,
                    'hour': ts.hour,
                    'minute': ts.minute,
                    'time_bucket': f"{ts.hour:02d}:{'00' if ts.minute < 30 else '30'}-{ts.hour:02d}:{'30' if ts.minute < 30 else '59'}",
                    'direction': f'BUY_{direction}',
                    'strike': strike,
                    'entry_premium': best_prem,
                    'exit_premium': exit_prem,
                    'pnl_pct': pnl,
                    'exit_reason': exit_reason,
                    'won': won,
                    'exit_time': exit_time,
                    'time_to_exit_mins': time_to_exit,
                    'max_drawdown_pct': max_drawdown,
                    'max_runup_pct': max_runup,
                    # Market context
                    'verdict': verdict,
                    'verdict_aligned': is_aligned,
                    'confidence': confidence,
                    'vix': vix,
                    'iv_skew': iv_skew,
                    'futures_oi_change': fut_oi,
                    'futures_basis': basis,
                    'atm_call_oi_chg': atm_call_oi_chg,
                    'atm_put_oi_chg': atm_put_oi_chg,
                    'max_pain': max_pain,
                    'call_oi_change': call_oi_chg,
                    'put_oi_change': put_oi_chg,
                    # PM data
                    'pm_score': pm_score,
                    'pm_regime': pm_regime,
                    'pm_trend': pm_trend,
                    'pm_momentum': pm_momentum,
                    # Patterns
                    'recent_patterns': pattern_types,
                    'has_pm_reversal': any('REVERSAL' in p for p in pattern_types),
                    'has_shakeout': any('SHAKEOUT' in p for p in pattern_types),
                    # Spot movement
                    'spot_move_30m': spot_move,
                    'spot_move_pct_30m': spot_move_pct,
                    # Streak
                    'verdict_streak': streak_count,
                    'streak_direction': streak_dir,
                    # Strike OI
                    'strike_ce_oi_chg': oi_info.get('ce_oi_change', 0),
                    'strike_pe_oi_chg': oi_info.get('pe_oi_change', 0),
                    'strike_ce_iv': oi_info.get('ce_iv', 0),
                    'strike_pe_iv': oi_info.get('pe_iv', 0),
                }
                all_trades.append(trade)
    
    return all_trades


def analyze_feature(trades, feature_name, bucketize_fn, label=""):
    """Analyze win rate by a bucketed feature"""
    buckets = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0})
    for t in trades:
        val = bucketize_fn(t)
        if val is None:
            continue
        buckets[val]['total'] += 1
        if t['won']:
            buckets[val]['wins'] += 1
        else:
            buckets[val]['losses'] += 1
    
    print(f"\n  --- {label or feature_name} ---")
    for bucket in sorted(buckets.keys()):
        d = buckets[bucket]
        wr = d['wins'] / d['total'] * 100 if d['total'] > 0 else 0
        bar = '#' * int(wr / 2)
        print(f"    {str(bucket):<25} W={d['wins']:<4} L={d['losses']:<4} T={d['total']:<5} WR={wr:>5.1f}%  {bar}")


def find_best_daily_trade(all_trades, filter_fn, name=""):
    """
    Given a filter, pick FIRST qualifying entry per day.
    Returns trades and stats.
    """
    by_day = defaultdict(list)
    for t in all_trades:
        if filter_fn(t):
            by_day[t['day']].append(t)
    
    trades = []
    for day in sorted(by_day.keys()):
        candidates = sorted(by_day[day], key=lambda x: x['entry_time'])
        if candidates:
            trades.append(candidates[0])
    
    wins = sum(1 for t in trades if t['won'])
    total = len(trades)
    wr = wins / total * 100 if total else 0
    pnl = sum(t['pnl_pct'] for t in trades)
    
    return trades, wins, total, wr, pnl


if __name__ == '__main__':
    print("Loading ALL data (analysis, snapshots, PM, patterns, trades)...")
    analysis, snapshots, pm_data, patterns, existing = get_all_data()
    
    analysis_by_day = group_by_day(analysis)
    snapshots_by_day = group_by_day(snapshots)
    pm_by_day = group_by_day(pm_data)
    patterns_by_day = group_by_day(patterns)
    
    days = sorted(d for d in analysis_by_day.keys() if d >= '2026-02-01' and d != '2026-02-16')
    print(f"Days: {len(days)} ({days[0]} to {days[-1]})")
    print(f"Analysis records: {len(analysis)}, Snapshots: {len(snapshots)}")
    print(f"PM records: {len(pm_data)}, Patterns: {len(patterns)}")
    
    print("\nSimulating ALL possible 1:2 entries (25/50)...")
    all_trades = simulate_all_entries(analysis_by_day, snapshots_by_day, pm_by_day, patterns_by_day, days)
    
    winners = [t for t in all_trades if t['won']]
    losers = [t for t in all_trades if not t['won']]
    print(f"\nTotal entries simulated: {len(all_trades)}")
    print(f"Winners: {len(winners)} ({len(winners)/len(all_trades)*100:.1f}%)")
    print(f"Losers: {len(losers)} ({len(losers)/len(all_trades)*100:.1f}%)")
    
    # Days breakdown
    win_days = set(t['day'] for t in winners)
    print(f"\nDays with at least 1 winning entry: {len(win_days)}/{len(days)}")
    print(f"Days with NO winning entry: {sorted(set(days) - win_days)}")
    
    # ================================================================
    # DEEP FEATURE ANALYSIS — WINNERS VS LOSERS
    # ================================================================
    print(f"\n{'='*100}")
    print("FEATURE ANALYSIS — What separates winners from losers?")
    print(f"{'='*100}")
    
    # Time
    analyze_feature(all_trades, 'hour', lambda t: f"{t['hour']:02d}:00", "Entry Hour")
    
    # Direction  
    analyze_feature(all_trades, 'direction', lambda t: t['direction'], "Direction")
    
    # Verdict alignment
    analyze_feature(all_trades, 'aligned', lambda t: 'Aligned' if t['verdict_aligned'] else 'Contra', "Verdict Alignment")
    
    # Verdict
    analyze_feature(all_trades, 'verdict', lambda t: t['verdict'], "Verdict")
    
    # VIX buckets
    analyze_feature(all_trades, 'vix', 
        lambda t: '<11' if t['vix'] < 11 else '11-11.5' if t['vix'] < 11.5 else '11.5-12' if t['vix'] < 12 else '12-12.5' if t['vix'] < 12.5 else '12.5-13' if t['vix'] < 13 else '13-14' if t['vix'] < 14 else '14-15' if t['vix'] < 15 else '15+',
        "VIX Range")
    
    # Confidence
    analyze_feature(all_trades, 'confidence',
        lambda t: '<30' if t['confidence'] < 30 else '30-50' if t['confidence'] < 50 else '50-65' if t['confidence'] < 65 else '65-80' if t['confidence'] < 80 else '80-90' if t['confidence'] < 90 else '90+',
        "Confidence")
    
    # IV Skew
    analyze_feature(all_trades, 'iv_skew',
        lambda t: '<-2' if t['iv_skew'] < -2 else '-2 to 0' if t['iv_skew'] < 0 else '0 to 1' if t['iv_skew'] < 1 else '1 to 3' if t['iv_skew'] < 3 else '3 to 5' if t['iv_skew'] < 5 else '5+',
        "IV Skew")
    
    # ATM OI changes
    analyze_feature(all_trades, 'atm_oi',
        lambda t: 'CE buildup' if t['atm_call_oi_chg'] > t['atm_put_oi_chg'] else 'PE buildup' if t['atm_put_oi_chg'] > t['atm_call_oi_chg'] else 'Neutral',
        "ATM OI Buildup")
    
    # Max pain distance
    analyze_feature(all_trades, 'max_pain_dist',
        lambda t: 'N/A' if not t['max_pain'] else 'Below MP' if t['strike'] < t['max_pain'] else 'At MP' if t['strike'] == t['max_pain'] else 'Above MP',
        "Strike vs Max Pain")
    
    # PM Score
    analyze_feature(all_trades, 'pm_score',
        lambda t: 'N/A' if t['pm_score'] is None else '<-50' if t['pm_score'] < -50 else '-50 to -25' if t['pm_score'] < -25 else '-25 to 0' if t['pm_score'] < 0 else '0 to 25' if t['pm_score'] < 25 else '25 to 50' if t['pm_score'] < 50 else '50+',
        "PM Score")
    
    # PM Regime
    analyze_feature(all_trades, 'pm_regime', lambda t: t['pm_regime'] or 'N/A', "PM Regime")
    
    # PM Trend
    analyze_feature(all_trades, 'pm_trend', lambda t: t['pm_trend'] or 'N/A', "PM Trend")
    
    # Recent patterns
    analyze_feature(all_trades, 'patterns',
        lambda t: 'Has Reversal' if t['has_pm_reversal'] else 'Has Shakeout' if t['has_shakeout'] else 'No Pattern',
        "Recent Patterns (30min)")
    
    # Spot movement (30min)
    analyze_feature(all_trades, 'spot_move',
        lambda t: 'N/A' if t['spot_move_pct_30m'] is None else 'Down >0.3%' if t['spot_move_pct_30m'] < -0.3 else 'Down 0.1-0.3%' if t['spot_move_pct_30m'] < -0.1 else 'Flat' if t['spot_move_pct_30m'] < 0.1 else 'Up 0.1-0.3%' if t['spot_move_pct_30m'] < 0.3 else 'Up >0.3%',
        "Spot Move (30min prior)")
    
    # Verdict streak
    analyze_feature(all_trades, 'streak',
        lambda t: f"{t['verdict_streak']}+ same" if t['verdict_streak'] >= 5 else f"{t['verdict_streak']} same" if t['verdict_streak'] >= 3 else 'Short (<3)',
        "Verdict Streak")
    
    # Futures OI
    analyze_feature(all_trades, 'fut_oi',
        lambda t: 'Big neg (<-500)' if (t['futures_oi_change'] or 0) < -500 else 'Neg (-500 to 0)' if t['futures_oi_change'] < 0 else 'Small pos (0-500)' if t['futures_oi_change'] < 500 else 'Big pos (>500)',
        "Futures OI Change")
    
    # Entry premium range
    analyze_feature(all_trades, 'entry_prem',
        lambda t: '<50' if t['entry_premium'] < 50 else '50-100' if t['entry_premium'] < 100 else '100-150' if t['entry_premium'] < 150 else '150-200' if t['entry_premium'] < 200 else '200+',
        "Entry Premium (Rs)")
    
    # Time to exit for winners
    print(f"\n  --- Time to Exit (Winners only) ---")
    for w in sorted(winners, key=lambda x: x['time_to_exit_mins'] or 0):
        if w['time_to_exit_mins']:
            print(f"    {w['day']} {w['entry_time'].strftime('%H:%M')} {w['direction']:<10} -> exit in {w['time_to_exit_mins']:.0f} min  "
                  f"v={w['verdict']:<20} VIX={w['vix']:.1f} conf={w['confidence']:.0f}% pm={w['pm_score']}")
    
    # ================================================================
    # WINNING ENTRY DEEP DIVE — TRADE BY TRADE
    # ================================================================
    print(f"\n\n{'='*100}")
    print("WINNING ENTRIES — DEEP DIVE (first winner per day)")
    print(f"{'='*100}")
    
    # Get first winner per day
    first_winners = {}
    for w in sorted(winners, key=lambda x: x['entry_time']):
        if w['day'] not in first_winners:
            first_winners[w['day']] = w
    
    for day in sorted(first_winners.keys()):
        w = first_winners[day]
        print(f"\n  [{w['day']}] {w['entry_time'].strftime('%H:%M')} {w['direction']} {w['strike']} @ Rs{w['entry_premium']:.2f}")
        print(f"    Exit: {w['exit_time'].strftime('%H:%M')} ({w['time_to_exit_mins']:.0f}min) P&L={w['pnl_pct']:+.1f}%")
        print(f"    Max drawdown: {w['max_drawdown_pct']:.1f}% | Max runup: {w['max_runup_pct']:.1f}%")
        print(f"    Verdict: {w['verdict']} (aligned={w['verdict_aligned']}) | Conf: {w['confidence']:.0f}%")
        print(f"    VIX: {w['vix']:.1f} | IV Skew: {w['iv_skew']:.1f} | Max Pain: {w['max_pain']}")
        print(f"    Futures OI: {w['futures_oi_change']} | Basis: {w['futures_basis']}")
        print(f"    ATM CE OI chg: {w['atm_call_oi_chg']} | ATM PE OI chg: {w['atm_put_oi_chg']}")
        print(f"    PM Score: {w['pm_score']} | Regime: {w['pm_regime']} | Trend: {w['pm_trend']} | Mom: {w['pm_momentum']}")
        print(f"    Spot move (30m): {w['spot_move_pct_30m']:.3f}%" if w['spot_move_pct_30m'] else "    Spot move: N/A")
        print(f"    Verdict streak: {w['verdict_streak']} ({w['streak_direction']})")
        print(f"    Recent patterns: {w['recent_patterns']}")
        print(f"    Strike CE OI chg: {w['strike_ce_oi_chg']} | PE OI chg: {w['strike_pe_oi_chg']}")
    
    # ================================================================
    # FILTER DISCOVERY — TRY COMBINATIONS
    # ================================================================
    print(f"\n\n{'='*100}")
    print("FILTER DISCOVERY — Testing combinations for 80%+ WR")
    print(f"{'='*100}")
    
    # Build filter combos
    filters = {
        'PUT only': lambda t: t['direction'] == 'BUY_PUT',
        'CALL only': lambda t: t['direction'] == 'BUY_CALL',
        'Aligned': lambda t: t['verdict_aligned'],
        'Contra': lambda t: not t['verdict_aligned'],
        'VIX>13': lambda t: t['vix'] > 13,
        'VIX>12': lambda t: t['vix'] > 12,
        'VIX<12': lambda t: t['vix'] < 12,
        'IVskew<0': lambda t: t['iv_skew'] < 0,
        'IVskew<1': lambda t: t['iv_skew'] < 1,
        'Conf<65': lambda t: t['confidence'] < 65,
        'Conf>=65': lambda t: t['confidence'] >= 65,
        '9:30-10:30': lambda t: 9 <= t['hour'] < 10 or (t['hour'] == 10 and t['minute'] < 30),
        '10:00-11:00': lambda t: 10 <= t['hour'] < 11,
        '10:30-12:00': lambda t: (t['hour'] == 10 and t['minute'] >= 30) or t['hour'] == 11,
        '11:00-13:00': lambda t: 11 <= t['hour'] < 13,
        '12:00-14:00': lambda t: 12 <= t['hour'] < 14,
        'PM_score<-25': lambda t: t['pm_score'] is not None and t['pm_score'] < -25,
        'PM_score>25': lambda t: t['pm_score'] is not None and t['pm_score'] > 25,
        'Has_reversal': lambda t: t['has_pm_reversal'],
        'Spot_down_30m': lambda t: t['spot_move_pct_30m'] is not None and t['spot_move_pct_30m'] < -0.1,
        'Spot_up_30m': lambda t: t['spot_move_pct_30m'] is not None and t['spot_move_pct_30m'] > 0.1,
        'Streak>=5': lambda t: t['verdict_streak'] >= 5,
        'FutOI_neg': lambda t: (t['futures_oi_change'] or 0) < 0,
        'Prem_50-150': lambda t: 50 <= t['entry_premium'] <= 150,
        'Prem_100+': lambda t: t['entry_premium'] >= 100,
    }
    
    print(f"\n  Single filters (first entry per day):")
    print(f"  {'Filter':<25} {'Days':>5} {'Wins':>5} {'WR':>6} {'P&L':>8}")
    print(f"  {'-'*55}")
    
    single_results = []
    for fname, fn in filters.items():
        trades, wins, total, wr, pnl = find_best_daily_trade(all_trades, fn, fname)
        single_results.append((fname, wins, total, wr, pnl))
        if total >= 3:
            print(f"  {fname:<25} {total:>5} {wins:>5} {wr:>5.1f}% {pnl:>+7.1f}%")
    
    # Test all 2-filter combinations
    print(f"\n  Top 2-filter combos (WR >= 60%, trades >= 3):")
    print(f"  {'Filters':<50} {'Days':>5} {'Wins':>5} {'WR':>6} {'P&L':>8}")
    print(f"  {'-'*75}")
    
    combo_results = []
    filter_names = list(filters.keys())
    for i in range(len(filter_names)):
        for j in range(i+1, len(filter_names)):
            f1, f2 = filter_names[i], filter_names[j]
            fn1, fn2 = filters[f1], filters[f2]
            combined = lambda t, a=fn1, b=fn2: a(t) and b(t)
            trades, wins, total, wr, pnl = find_best_daily_trade(all_trades, combined)
            if total >= 3 and wr >= 60:
                combo_results.append((f"{f1} + {f2}", wins, total, wr, pnl))
    
    combo_results.sort(key=lambda x: (-x[3], -x[4]))
    for name, wins, total, wr, pnl in combo_results[:30]:
        print(f"  {name:<50} {total:>5} {wins:>5} {wr:>5.1f}% {pnl:>+7.1f}%")
    
    # Test 3-filter combinations for the BEST ones
    print(f"\n  Top 3-filter combos (WR >= 70%, trades >= 3):")
    print(f"  {'Filters':<65} {'Days':>5} {'Wins':>5} {'WR':>6} {'P&L':>8}")
    print(f"  {'-'*90}")
    
    triple_results = []
    for i in range(len(filter_names)):
        for j in range(i+1, len(filter_names)):
            for k in range(j+1, len(filter_names)):
                f1, f2, f3 = filter_names[i], filter_names[j], filter_names[k]
                fn1, fn2, fn3 = filters[f1], filters[f2], filters[f3]
                combined = lambda t, a=fn1, b=fn2, c=fn3: a(t) and b(t) and c(t)
                trades, wins, total, wr, pnl = find_best_daily_trade(all_trades, combined)
                if total >= 3 and wr >= 70:
                    triple_results.append((f"{f1} + {f2} + {f3}", wins, total, wr, pnl, trades))
    
    triple_results.sort(key=lambda x: (-x[3], -x[4]))
    for name, wins, total, wr, pnl, trades in triple_results[:30]:
        print(f"  {name:<65} {total:>5} {wins:>5} {wr:>5.1f}% {pnl:>+7.1f}%")
    
    # Show the BEST filter's trades in detail
    if triple_results:
        best = triple_results[0]
        print(f"\n\n  BEST FILTER DETAIL: {best[0]}")
        print(f"  WR: {best[3]:.1f}% | Trades: {best[2]} | P&L: {best[4]:+.1f}%")
        for t in best[5]:
            status = "WIN" if t['won'] else "LOSS"
            print(f"    [{status}] {t['day']} {t['entry_time'].strftime('%H:%M')} {t['direction']:<10} {t['strike']} "
                  f"@ Rs{t['entry_premium']:.2f} -> {t['exit_reason']} P&L={t['pnl_pct']:+.1f}% "
                  f"v={t['verdict']} VIX={t['vix']:.1f} pm={t['pm_score']}")
