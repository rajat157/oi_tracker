"""
Comprehensive 1:2 RR backtest for OPTIONS BUYING.
Mirrors the selling backtest dimensions.

Buying logic (Config B):
- Slightly Bullish -> BUY ATM CALL
- Slightly Bearish -> BUY ATM PUT
- Profit when premium RISES, Loss when premium DROPS
"""

import sqlite3
import os
from datetime import datetime
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

def get_analysis_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               futures_oi_change, futures_basis, vix, iv_skew, prev_verdict
        FROM analysis_history ORDER BY timestamp
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_option_prices_for_day(date_str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp
        FROM oi_snapshots
        WHERE DATE(timestamp) = ? AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price
    """, (date_str,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_premium_at_time(snapshots, target_time, strike, option_type):
    best_premium = None
    best_diff = float('inf')
    for snap in snapshots:
        if snap['strike_price'] == strike:
            snap_time = datetime.fromisoformat(snap['timestamp'])
            diff = abs((snap_time - target_time).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_premium = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
    return best_premium if best_premium and best_premium > 0 else None

def simulate_buying_trade(day_snapshots, entry_time, strike, option_type, entry_premium, sl_pct, target_pct):
    """For buyers: profit when premium RISES, loss when it DROPS."""
    sl_premium = entry_premium * (1 - sl_pct / 100)
    target_premium = entry_premium * (1 + target_pct / 100)
    
    exit_premium = None
    exit_reason = None
    exit_time = None
    max_prem = entry_premium
    min_prem = entry_premium
    
    for snap in day_snapshots:
        snap_time = datetime.fromisoformat(snap['timestamp'])
        if snap_time <= entry_time:
            continue
        if snap['strike_price'] != strike:
            continue
        price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
        if not price or price <= 0:
            continue
        
        max_prem = max(max_prem, price)
        min_prem = min(min_prem, price)
        
        if price <= sl_premium:
            exit_premium = price
            exit_reason = "SL"
            exit_time = snap_time
            break
        if price >= target_premium:
            exit_premium = price
            exit_reason = "TARGET"
            exit_time = snap_time
            break
        if snap_time.hour == 15 and snap_time.minute >= 20:
            exit_premium = price
            exit_reason = "EOD"
            exit_time = snap_time
            break
    
    if exit_premium is None:
        for snap in reversed(day_snapshots):
            if snap['strike_price'] == strike:
                price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
                if price and price > 0:
                    exit_premium = price
                    exit_reason = "EOD"
                    exit_time = datetime.fromisoformat(snap['timestamp'])
                    break
    
    if exit_premium is None:
        return None
    
    pnl_pct = ((exit_premium - entry_premium) / entry_premium) * 100
    return {
        'entry_premium': entry_premium,
        'exit_premium': exit_premium,
        'pnl_pct': pnl_pct,
        'exit_reason': exit_reason,
        'exit_time': exit_time,
        'won': exit_reason == "TARGET" or (exit_reason == "EOD" and pnl_pct > 0),
        'max_premium': max_prem,
        'min_premium': min_prem,
    }


def run_config(analysis_by_day, snapshots_cache, config):
    trades = []
    for day_str in sorted(analysis_by_day.keys()):
        if day_str == '2026-02-16':
            continue
        day_analysis = analysis_by_day[day_str]
        if day_str not in snapshots_cache:
            snapshots_cache[day_str] = get_option_prices_for_day(day_str)
        day_snapshots = snapshots_cache[day_str]
        if not day_snapshots:
            continue
        
        traded = False
        for a in day_analysis:
            if traded:
                break
            ts = datetime.fromisoformat(a['timestamp'])
            
            t_start = config.get('time_start', 11)
            t_end = config.get('time_end', 14)
            if ts.hour < t_start or ts.hour >= t_end:
                continue
            
            verdict = a['verdict']
            valid_verdicts = config.get('verdicts', ['Slightly Bullish', 'Slightly Bearish'])
            if verdict not in valid_verdicts:
                continue
            
            confidence = a['signal_confidence'] or 0
            if confidence < config.get('min_confidence', 65):
                continue
            
            vix = a.get('vix', 0) or 0
            if vix < config.get('vix_min', 0) or vix > config.get('vix_max', 100):
                continue
            
            if config.get('futures_aligned', False):
                fut_oi = a.get('futures_oi_change', 0) or 0
                if 'Bullish' in verdict and fut_oi < 0:
                    continue
                if 'Bearish' in verdict and fut_oi > 0:
                    continue
            
            iv_skew = a.get('iv_skew', 0) or 0
            if iv_skew < config.get('iv_skew_min', -100) or iv_skew > config.get('iv_skew_max', 100):
                continue
            
            if config.get('consistent_verdict', False):
                prev = a.get('prev_verdict', '')
                if prev and 'Bullish' in verdict and 'Bullish' not in (prev or ''):
                    continue
                if prev and 'Bearish' in verdict and 'Bearish' not in (prev or ''):
                    continue
            
            spot = a['spot_price']
            step = 50
            atm = round(spot / step) * step
            
            if 'Bullish' in verdict:
                direction = "BUY_CALL"
                option_type = "CE"
            else:
                direction = "BUY_PUT"
                option_type = "PE"
            
            # Strike selection
            strike_mode = config.get('strike', 'ATM')
            if strike_mode == 'ATM':
                strike = atm
            elif strike_mode == 'ITM1':
                strike = atm - step if option_type == 'CE' else atm + step
            elif strike_mode == 'OTM1':
                strike = atm + step if option_type == 'CE' else atm - step
            else:
                strike = atm
            
            entry_premium = get_premium_at_time(day_snapshots, ts, strike, option_type)
            if not entry_premium or entry_premium < 5:
                continue
            
            result = simulate_buying_trade(
                day_snapshots, ts, strike, option_type, entry_premium,
                config['sl_pct'], config['target_pct']
            )
            
            if result:
                result['day'] = day_str
                result['direction'] = direction
                result['strike'] = strike
                result['option_type'] = option_type
                result['verdict'] = verdict
                result['confidence'] = confidence
                result['spot'] = spot
                result['entry_time'] = ts
                result['vix'] = vix
                result['futures_oi_change'] = a.get('futures_oi_change', 0)
                result['iv_skew'] = iv_skew
                trades.append(result)
                traded = True
    return trades


def summarize(trades, name=""):
    if not trades:
        return {'name': name, 'trades': 0, 'wins': 0, 'losses': 0, 'wr': 0, 'pnl': 0, 'pf': 0, 'avg_win': 0, 'avg_loss': 0, 'targets': 0, 'sls': 0, 'eods': 0}
    wins = [t for t in trades if t['won']]
    losses = [t for t in trades if not t['won']]
    wr = len(wins)/len(trades)*100
    total_pnl = sum(t['pnl_pct'] for t in trades)
    avg_win = sum(t['pnl_pct'] for t in wins)/len(wins) if wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses)/len(losses) if losses else 0
    pf = abs(sum(t['pnl_pct'] for t in wins))/abs(sum(t['pnl_pct'] for t in losses)) if losses and sum(t['pnl_pct'] for t in losses) != 0 else float('inf')
    return {
        'name': name, 'trades': len(trades), 'wins': len(wins), 'losses': len(losses),
        'wr': wr, 'pnl': total_pnl, 'pf': pf, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'targets': len([t for t in trades if t['exit_reason'] == 'TARGET']),
        'sls': len([t for t in trades if t['exit_reason'] == 'SL']),
        'eods': len([t for t in trades if t['exit_reason'] == 'EOD']),
    }


def print_summary_table(results):
    print(f"\n{'Config':<55} {'#':>3} {'W':>3} {'L':>3} {'WR%':>6} {'P&L':>9} {'AvgW':>7} {'AvgL':>7} {'PF':>6} {'T/S/E':>7}")
    print("-" * 115)
    for r in sorted(results, key=lambda x: -x['pnl']):
        if r['trades'] == 0:
            continue
        tse = f"{r['targets']}/{r['sls']}/{r['eods']}"
        print(f"{r['name']:<55} {r['trades']:>3} {r['wins']:>3} {r['losses']:>3} {r['wr']:>5.1f}% {r['pnl']:>+8.1f}% {r['avg_win']:>+6.1f}% {r['avg_loss']:>+6.1f}% {r['pf']:>6.2f} {tse:>7}")


def print_trades(trades, name=""):
    if not trades:
        return
    print(f"\n--- {name} ---")
    print(f"{'Day':<12} {'Dir':<10} {'Strike':<7} {'Spot':<8} {'Verdict':<20} {'Conf':>4} {'VIX':>5} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Reason':<6}")
    for t in trades:
        print(f"{t['day']:<12} {t['direction']:<10} {t['strike']:<7} {t['spot']:<8.0f} {t['verdict']:<20} {t['confidence']:>4.0f} {t['vix']:>5.1f} {t['entry_premium']:>7.2f} {t['exit_premium']:>7.2f} {t['pnl_pct']:>+7.1f}% {t['exit_reason']:<6}")


if __name__ == '__main__':
    print("Loading analysis data...")
    analysis = get_analysis_data()
    by_day = defaultdict(list)
    for a in analysis:
        by_day[a['timestamp'][:10]].append(a)
    
    snapshots_cache = {}
    trading_days = [d for d in sorted(by_day.keys()) if d != '2026-02-16']
    print(f"Trading days: {len(trading_days)} ({trading_days[0]} to {trading_days[-1]})")
    
    all_results = []  # (summary, trades) for detailed printing
    
    # ============================================================
    # SECTION 1: Basic 1:2 RR combos
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 1: Basic 1:2 RR SL/Target Combos (ATM)")
    print("="*100)
    
    sec1 = []
    for sl, tgt in [(10,20), (15,30), (20,40), (25,50), (30,60)]:
        cfg = {"name": f"BUY 1:2 ({sl}%/{tgt}%) ATM", "sl_pct": sl, "target_pct": tgt}
        trades = run_config(by_day, snapshots_cache, cfg)
        s = summarize(trades, cfg['name'])
        sec1.append(s)
        all_results.append((s, trades))
    # Reference: current setup
    cfg = {"name": "CURRENT BUY (20%/22%) ATM", "sl_pct": 20, "target_pct": 22}
    trades = run_config(by_day, snapshots_cache, cfg)
    s = summarize(trades, cfg['name'])
    sec1.append(s)
    all_results.append((s, trades))
    print_summary_table(sec1)
    
    # ============================================================
    # SECTION 2: Strike selection (ATM vs ITM1 vs OTM1)
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 2: Strike Selection (1:2 RR)")
    print("="*100)
    
    sec2 = []
    for sl, tgt in [(15,30), (20,40), (25,50)]:
        for strike_mode in ['ATM', 'ITM1', 'OTM1']:
            cfg = {"name": f"BUY 1:2 ({sl}/{tgt}) {strike_mode}", "sl_pct": sl, "target_pct": tgt, "strike": strike_mode}
            trades = run_config(by_day, snapshots_cache, cfg)
            s = summarize(trades, cfg['name'])
            sec2.append(s)
            all_results.append((s, trades))
    print_summary_table(sec2)
    
    # ============================================================
    # SECTION 3: Confidence threshold sweep
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 3: Confidence Threshold Sweep (1:2 RR)")
    print("="*100)
    
    sec3 = []
    for sl, tgt in [(15,30), (20,40), (25,50)]:
        for conf in [60, 65, 70, 75, 80, 85]:
            cfg = {"name": f"BUY 1:2 ({sl}/{tgt}) conf>={conf}", "sl_pct": sl, "target_pct": tgt, "min_confidence": conf}
            trades = run_config(by_day, snapshots_cache, cfg)
            sec3.append(summarize(trades, cfg['name']))
    print_summary_table(sec3)
    
    # ============================================================
    # SECTION 4: VIX filter
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 4: VIX Filter (1:2 RR)")
    print("="*100)
    
    sec4 = []
    for sl, tgt in [(15,30), (20,40), (25,50)]:
        for label, vmin, vmax in [("Low<12", 0, 12), ("Mid 12-13.5", 12, 13.5), ("High>13.5", 13.5, 100), ("VIX<13", 0, 13)]:
            cfg = {"name": f"BUY 1:2 ({sl}/{tgt}) {label}", "sl_pct": sl, "target_pct": tgt, "vix_min": vmin, "vix_max": vmax}
            trades = run_config(by_day, snapshots_cache, cfg)
            sec4.append(summarize(trades, cfg['name']))
    print_summary_table(sec4)
    
    # ============================================================
    # SECTION 5: Futures OI alignment
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 5: Futures OI Alignment (1:2 RR)")
    print("="*100)
    
    sec5 = []
    for sl, tgt in [(15,30), (20,40), (25,50)]:
        for aligned in [False, True]:
            label = "FutAligned" if aligned else "NoFilter"
            cfg = {"name": f"BUY 1:2 ({sl}/{tgt}) {label}", "sl_pct": sl, "target_pct": tgt, "futures_aligned": aligned}
            trades = run_config(by_day, snapshots_cache, cfg)
            sec5.append(summarize(trades, cfg['name']))
    print_summary_table(sec5)
    
    # ============================================================
    # SECTION 6: Time windows
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 6: Time Window Variations (1:2 RR)")
    print("="*100)
    
    sec6 = []
    for sl, tgt in [(15,30), (20,40)]:
        for label, ts, te in [("11-14", 11, 14), ("11-13", 11, 13), ("11-12", 11, 12), ("12-14", 12, 14), ("10-14", 10, 14)]:
            cfg = {"name": f"BUY 1:2 ({sl}/{tgt}) {label}", "sl_pct": sl, "target_pct": tgt, "time_start": ts, "time_end": te}
            trades = run_config(by_day, snapshots_cache, cfg)
            sec6.append(summarize(trades, cfg['name']))
    print_summary_table(sec6)
    
    # ============================================================
    # SECTION 7: Verdict expansion
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 7: Verdict Expansion (1:2 RR)")
    print("="*100)
    
    sec7 = []
    for sl, tgt in [(15,30), (20,40)]:
        for label, vlist in [
            ("Slightly", ['Slightly Bullish', 'Slightly Bearish']),
            ("+Winners", ['Slightly Bullish', 'Slightly Bearish', 'Bulls Winning', 'Bears Winning']),
            ("+Strong", ['Slightly Bullish', 'Slightly Bearish', 'Bulls Winning', 'Bears Winning', 'Bulls Strongly Winning', 'Bears Strongly Winning']),
        ]:
            cfg = {"name": f"BUY 1:2 ({sl}/{tgt}) {label}", "sl_pct": sl, "target_pct": tgt, "verdicts": vlist}
            trades = run_config(by_day, snapshots_cache, cfg)
            sec7.append(summarize(trades, cfg['name']))
    print_summary_table(sec7)
    
    # ============================================================
    # SECTION 8: Verdict consistency
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 8: Verdict Consistency (1:2 RR)")
    print("="*100)
    
    sec8 = []
    for sl, tgt in [(15,30), (20,40), (25,50)]:
        for consistent in [False, True]:
            label = "Consistent" if consistent else "Any"
            cfg = {"name": f"BUY 1:2 ({sl}/{tgt}) {label}", "sl_pct": sl, "target_pct": tgt, "consistent_verdict": consistent}
            trades = run_config(by_day, snapshots_cache, cfg)
            sec8.append(summarize(trades, cfg['name']))
    print_summary_table(sec8)
    
    # ============================================================
    # SECTION 9: IV Skew filter
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 9: IV Skew Filter (1:2 RR)")
    print("="*100)
    
    sec9 = []
    for sl, tgt in [(15,30), (20,40)]:
        for label, smin, smax in [("Any", -100, 100), ("Positive>0", 0, 100), ("Negative<0", -100, 0), ("Low|<3|", -3, 3), ("High>3", 3, 100)]:
            cfg = {"name": f"BUY 1:2 ({sl}/{tgt}) IVSkew {label}", "sl_pct": sl, "target_pct": tgt, "iv_skew_min": smin, "iv_skew_max": smax}
            trades = run_config(by_day, snapshots_cache, cfg)
            sec9.append(summarize(trades, cfg['name']))
    print_summary_table(sec9)
    
    # ============================================================
    # SECTION 10: Combined best filters
    # ============================================================
    print("\n" + "="*100)
    print("SECTION 10: Combined Filters (Cherry-Picking Best)")
    print("="*100)
    
    sec10 = []
    combos = [
        {"name": "COMBO-A: 20/40 conf75 VIX<13", "sl_pct": 20, "target_pct": 40, "min_confidence": 75, "vix_max": 13},
        {"name": "COMBO-B: 25/50 conf75 VIX<13", "sl_pct": 25, "target_pct": 50, "min_confidence": 75, "vix_max": 13},
        {"name": "COMBO-C: 20/40 conf75 FutAlign", "sl_pct": 20, "target_pct": 40, "min_confidence": 75, "futures_aligned": True},
        {"name": "COMBO-D: 25/50 conf75 FutAlign", "sl_pct": 25, "target_pct": 50, "min_confidence": 75, "futures_aligned": True},
        {"name": "COMBO-E: 20/40 conf80 11-13", "sl_pct": 20, "target_pct": 40, "min_confidence": 80, "time_start": 11, "time_end": 13},
        {"name": "COMBO-F: 25/50 conf80 11-13", "sl_pct": 25, "target_pct": 50, "min_confidence": 80, "time_start": 11, "time_end": 13},
        {"name": "COMBO-G: 20/40 conf75 Consistent", "sl_pct": 20, "target_pct": 40, "min_confidence": 75, "consistent_verdict": True},
        {"name": "COMBO-H: 25/50 FutAlign VIX<13", "sl_pct": 25, "target_pct": 50, "futures_aligned": True, "vix_max": 13},
        {"name": "COMBO-I: 20/40 FutAlign VIX<13", "sl_pct": 20, "target_pct": 40, "futures_aligned": True, "vix_max": 13},
        {"name": "COMBO-J: 15/30 conf75 FutAlign VIX<13", "sl_pct": 15, "target_pct": 30, "min_confidence": 75, "futures_aligned": True, "vix_max": 13},
        {"name": "COMBO-K: 20/40 ITM1 conf75", "sl_pct": 20, "target_pct": 40, "strike": "ITM1", "min_confidence": 75},
        {"name": "COMBO-L: 25/50 ITM1 conf75", "sl_pct": 25, "target_pct": 50, "strike": "ITM1", "min_confidence": 75},
        {"name": "COMBO-M: 20/40 ITM1 FutAlign", "sl_pct": 20, "target_pct": 40, "strike": "ITM1", "futures_aligned": True},
        {"name": "COMBO-N: 25/50 Consistent VIX<13", "sl_pct": 25, "target_pct": 50, "consistent_verdict": True, "vix_max": 13},
        {"name": "COMBO-O: 20/40 IVSkew<0 conf70", "sl_pct": 20, "target_pct": 40, "iv_skew_max": 0, "min_confidence": 70},
    ]
    
    for cfg in combos:
        trades = run_config(by_day, snapshots_cache, cfg)
        s = summarize(trades, cfg['name'])
        sec10.append(s)
        all_results.append((s, trades))
    print_summary_table(sec10)
    
    # ============================================================
    # FINAL: Detailed trade breakdown for top configs
    # ============================================================
    print("\n" + "="*100)
    print("TOP CONFIGS — Detailed Trade Breakdown (WR>=60% and trades>=5)")
    print("="*100)
    
    for s, trades in all_results:
        if s['trades'] >= 5 and s['wr'] >= 60:
            print_trades(trades, f"{s['name']} | WR={s['wr']:.0f}% PF={s['pf']:.2f} P&L={s['pnl']:+.1f}%")
    
    print("\n\nDone!")
