"""
Fresh 1:2 RR Buying Backtest — No Config B constraints.
Explore what ACTUALLY works for 1:2 trades.

Key hypotheses from data:
1. Earlier window (9:30-10:30?) captures best entries
2. BUY_PUT has 2x better WR than BUY_CALL
3. High VIX (>13) = 30% WR vs low VIX = 11.5%
4. Negative IV skew = 29.8% WR
5. Verdict alignment barely matters for 1:2

Test multiple configs independently of Config B.
"""
import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

def get_data():
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
        SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp
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

def simulate_trade(entry_time, strike, option_type, day_snapshots, sl_pct, target_pct):
    """Simulate a single trade. Returns (exit_reason, pnl_pct, entry_prem, exit_prem, exit_time)"""
    # Find entry premium
    best_prem = None
    best_diff = float('inf')
    for snap in day_snapshots:
        if snap['strike_price'] == strike:
            snap_time = datetime.fromisoformat(snap['timestamp'])
            diff = abs((snap_time - entry_time).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_prem = snap['ce_ltp'] if option_type == 'CE' else snap['pe_ltp']
    
    if not best_prem or best_prem < 5:
        return None
    
    sl_p = best_prem * (1 - sl_pct / 100)
    tgt_p = best_prem * (1 + target_pct / 100)
    
    for snap in day_snapshots:
        snap_time = datetime.fromisoformat(snap['timestamp'])
        if snap_time <= entry_time or snap['strike_price'] != strike:
            continue
        price = snap['ce_ltp'] if option_type == 'CE' else snap['pe_ltp']
        if not price or price <= 0:
            continue
        
        if price <= sl_p:
            return ('SL', -sl_pct, best_prem, price, snap_time)
        if price >= tgt_p:
            return ('TARGET', target_pct, best_prem, price, snap_time)
        if snap_time.hour == 15 and snap_time.minute >= 20:
            pnl = ((price - best_prem) / best_prem) * 100
            return ('EOD', pnl, best_prem, price, snap_time)
    
    return None

def backtest_config(analysis_by_day, snapshots_by_day, days, config):
    """
    Run backtest with given config. Takes FIRST valid signal per day.
    Returns list of trades.
    """
    name = config['name']
    window_start_h = config.get('window_start_h', 9)
    window_start_m = config.get('window_start_m', 30)
    window_end_h = config.get('window_end_h', 14)
    window_end_m = config.get('window_end_m', 0)
    sl_pct = config['sl_pct']
    target_pct = config['target_pct']
    min_confidence = config.get('min_confidence', 0)
    min_vix = config.get('min_vix', 0)
    max_vix = config.get('max_vix', 100)
    direction_filter = config.get('direction', None)  # 'PUT', 'CALL', or None for both
    otm_offset = config.get('otm_offset', 0)  # 0=ATM, 1=OTM-1
    require_aligned = config.get('require_aligned', False)
    require_contra = config.get('require_contra', False)  # Trade AGAINST verdict
    iv_skew_max = config.get('iv_skew_max', 100)
    iv_skew_min = config.get('iv_skew_min', -100)
    verdict_filter = config.get('verdict_filter', None)  # list of verdict strings
    
    trades = []
    
    for day in days:
        day_analysis = analysis_by_day.get(day, [])
        day_snaps = snapshots_by_day.get(day, [])
        traded = False
        
        for a in day_analysis:
            if traded:
                break
            
            ts = datetime.fromisoformat(a['timestamp'])
            
            # Window filter
            t_mins = ts.hour * 60 + ts.minute
            start_mins = window_start_h * 60 + window_start_m
            end_mins = window_end_h * 60 + window_end_m
            if t_mins < start_mins or t_mins >= end_mins:
                continue
            
            spot = a['spot_price']
            atm = round(spot / 50) * 50
            verdict = a['verdict']
            confidence = a['signal_confidence'] or 0
            vix = a.get('vix', 0) or 0
            iv_skew = a.get('iv_skew', 0) or 0
            
            # Filters
            if confidence < min_confidence:
                continue
            if vix < min_vix or vix > max_vix:
                continue
            if iv_skew < iv_skew_min or iv_skew > iv_skew_max:
                continue
            if verdict_filter and verdict not in verdict_filter:
                continue
            
            # Determine direction(s) to try
            if direction_filter:
                dirs = [direction_filter]
            else:
                # Default: trade based on verdict
                if 'Bearish' in verdict or 'Bears' in verdict:
                    dirs = ['PUT']
                elif 'Bullish' in verdict or 'Bulls' in verdict:
                    dirs = ['CALL']
                else:
                    continue
            
            for d in dirs:
                if traded:
                    break
                
                is_aligned = (
                    (d == 'CALL' and ('Bullish' in verdict or 'Bulls' in verdict)) or
                    (d == 'PUT' and ('Bearish' in verdict or 'Bears' in verdict))
                )
                
                if require_aligned and not is_aligned:
                    continue
                if require_contra and is_aligned:
                    continue
                
                option_type = 'CE' if d == 'CALL' else 'PE'
                
                # OTM offset
                if otm_offset > 0:
                    if d == 'CALL':
                        strike = atm + (otm_offset * 50)
                    else:
                        strike = atm - (otm_offset * 50)
                else:
                    strike = atm
                
                result = simulate_trade(ts, strike, option_type, day_snaps, sl_pct, target_pct)
                if result:
                    exit_reason, pnl, entry_p, exit_p, exit_t = result
                    trades.append({
                        'day': day,
                        'entry_time': ts,
                        'direction': f'BUY_{d}',
                        'strike': strike,
                        'entry_prem': entry_p,
                        'exit_prem': exit_p,
                        'pnl_pct': pnl,
                        'exit_reason': exit_reason,
                        'exit_time': exit_t,
                        'won': exit_reason == 'TARGET',
                        'verdict': verdict,
                        'confidence': confidence,
                        'vix': vix,
                        'iv_skew': iv_skew,
                        'aligned': is_aligned,
                    })
                    traded = True
    
    return trades


def print_results(name, trades, total_days):
    wins = [t for t in trades if t['won']]
    losses = [t for t in trades if not t['won']]
    traded_days = len(trades)
    no_trade_days = total_days - traded_days
    
    if not trades:
        print(f"\n  {name}: NO TRADES")
        return {'name': name, 'wr': 0, 'pnl': 0, 'pf': 0, 'trades': 0}
    
    wr = len(wins) / traded_days * 100
    total_pnl = sum(t['pnl_pct'] for t in trades)
    gross_profit = sum(t['pnl_pct'] for t in wins) if wins else 0
    gross_loss = abs(sum(t['pnl_pct'] for t in losses)) if losses else 0.01
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    print(f"\n  {'='*85}")
    print(f"  {name}")
    print(f"  {'='*85}")
    print(f"  Trades: {traded_days}/{total_days} days | Wins: {len(wins)} | Losses: {len(losses)} | No-trade: {no_trade_days}")
    print(f"  WR: {wr:.1f}% | P&L: {total_pnl:+.1f}% | PF: {pf:.2f}")
    
    for t in trades:
        status = "W" if t['won'] else "L"
        print(f"    {status} {t['day']} {t['entry_time'].strftime('%H:%M')} {t['direction']:<10} "
              f"{t['strike']} @ Rs{t['entry_prem']:.2f} -> {t['exit_reason']:<6} P&L={t['pnl_pct']:+.1f}% "
              f"v={t['verdict']:<20} VIX={t['vix']:.1f} iv_skew={t['iv_skew']:.1f} conf={t['confidence']:.0f}%")
    
    return {'name': name, 'wr': wr, 'pnl': total_pnl, 'pf': pf, 'trades': traded_days}


if __name__ == '__main__':
    print("Loading data...")
    analysis, snapshots = get_data()
    analysis_by_day = group_by_day(analysis)
    snapshots_by_day = group_by_day(snapshots)
    days = sorted(d for d in analysis_by_day.keys() if d != '2026-02-16')
    
    # Exclude Jan 30 (no VIX data)
    days = [d for d in days if d >= '2026-02-01']
    print(f"Testing on {len(days)} trading days: {days[0]} to {days[-1]}")
    
    configs = [
        # ===== BASELINE: Current Config B for reference =====
        {
            'name': 'BASELINE: Config B (11:00-14:00, 20/22, aligned)',
            'window_start_h': 11, 'window_start_m': 0,
            'window_end_h': 14, 'window_end_m': 0,
            'sl_pct': 22, 'target_pct': 22,
            'min_confidence': 65,
            'require_aligned': True,
        },
        
        # ===== EARLY WINDOW TESTS =====
        {
            'name': 'EARLY-1: 9:30-11:00, 25/50, PUT-only, aligned',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'require_aligned': True,
        },
        {
            'name': 'EARLY-2: 9:30-11:00, 25/50, PUT-only, any verdict',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
        },
        {
            'name': 'EARLY-3: 9:30-11:00, 25/50, both dirs, aligned',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'require_aligned': True,
        },
        {
            'name': 'EARLY-4: 9:45-10:30, 25/50, PUT-only (sweet spot?)',
            'window_start_h': 9, 'window_start_m': 45,
            'window_end_h': 10, 'window_end_m': 30,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
        },
        
        # ===== WIDE WINDOW =====
        {
            'name': 'WIDE-1: 9:30-14:00, 25/50, aligned, conf>=65',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 14, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'require_aligned': True,
            'min_confidence': 65,
        },
        {
            'name': 'WIDE-2: 9:30-14:00, 25/50, PUT-only, conf>=50',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 14, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'min_confidence': 50,
        },
        
        # ===== VIX FILTER =====
        {
            'name': 'VIX-1: 9:30-14:00, 25/50, VIX>12, PUT-only',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 14, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'min_vix': 12,
        },
        {
            'name': 'VIX-2: 9:30-14:00, 25/50, VIX>13, PUT-only',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 14, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'min_vix': 13,
        },
        
        # ===== CONTRA-TREND (trade AGAINST verdict) =====
        {
            'name': 'CONTRA-1: 9:30-11:00, 25/50, CALL when Bearish',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'CALL',
            'require_contra': True,
        },
        {
            'name': 'CONTRA-2: 10:00-12:00, 25/50, CALL when Bearish',
            'window_start_h': 10, 'window_start_m': 0,
            'window_end_h': 12, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'CALL',
            'require_contra': True,
        },
        
        # ===== IV SKEW FILTER =====
        {
            'name': 'IVSKEW-1: 9:30-14:00, 25/50, neg IV skew (<0)',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 14, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'iv_skew_max': 0,
        },
        
        # ===== DIFFERENT SL/TP RATIOS =====
        {
            'name': 'RATIO-1: 9:30-11:00, 20/40, PUT-only',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 20, 'target_pct': 40,
            'direction': 'PUT',
        },
        {
            'name': 'RATIO-2: 9:30-11:00, 30/60, PUT-only',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 30, 'target_pct': 60,
            'direction': 'PUT',
        },
        {
            'name': 'RATIO-3: 9:30-11:00, 15/30, PUT-only (tight)',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 15, 'target_pct': 30,
            'direction': 'PUT',
        },
        
        # ===== OTM TESTS =====
        {
            'name': 'OTM-1: 9:30-11:00, 25/50, PUT OTM-1',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'otm_offset': 1,
        },
        
        # ===== COMBINED BEST FILTERS =====
        {
            'name': 'COMBO-1: Early+PUT+VIX>12, 25/50',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'min_vix': 12,
        },
        {
            'name': 'COMBO-2: Early+PUT+negIVskew, 25/50',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 11, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'iv_skew_max': 0,
        },
        {
            'name': 'COMBO-3: Wide+PUT+VIX>12+aligned, 25/50',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 14, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'min_vix': 12,
            'require_aligned': True,
        },
        {
            'name': 'COMBO-4: 9:30-12:00, 25/50, PUT, conf<75 (low conf paradox)',
            'window_start_h': 9, 'window_start_m': 30,
            'window_end_h': 12, 'window_end_m': 0,
            'sl_pct': 25, 'target_pct': 50,
            'direction': 'PUT',
            'min_confidence': 0,
        },
    ]
    
    print(f"\n{'#'*100}")
    print(f"  FRESH 1:2 RR BUYING BACKTEST — {len(configs)} CONFIGS")
    print(f"{'#'*100}")
    
    results = []
    for config in configs:
        trades = backtest_config(analysis_by_day, snapshots_by_day, days, config)
        r = print_results(config['name'], trades, len(days))
        results.append(r)
    
    # Summary table
    print(f"\n\n{'#'*100}")
    print(f"  SUMMARY — RANKED BY P&L")
    print(f"{'#'*100}")
    print(f"\n  {'Config':<55} {'Trades':>6} {'WR':>6} {'P&L':>8} {'PF':>6}")
    print(f"  {'-'*85}")
    
    for r in sorted(results, key=lambda x: x['pnl'], reverse=True):
        print(f"  {r['name']:<55} {r['trades']:>6} {r['wr']:>5.1f}% {r['pnl']:>+7.1f}% {r['pf']:>5.2f}")
