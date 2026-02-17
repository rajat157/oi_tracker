"""
Backtest: Iron Pulse with trailing SL after T1 hit.
After T1 (+22%) is hit, instead of booking, trail the SL.
Test various trailing methods:
1. Move SL to entry (breakeven)
2. Move SL to T1 level (lock +22%)
3. Trail X% below peak premium
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
c.execute("""SELECT timestamp, strike_price, ce_ltp, pe_ltp FROM oi_snapshots
    WHERE DATE(timestamp) >= '2026-02-01' AND (ce_ltp > 0 OR pe_ltp > 0)
    ORDER BY timestamp, strike_price""")
snapshots = [dict(r) for r in c.fetchall()]
conn.close()

def group(items):
    d = defaultdict(list)
    for i in items: d[i['timestamp'][:10]].append(i)
    return d
aby = group(analysis); sby = group(snapshots)

def sim_trail(ts, strike, otype, day_s, sl_pct=20, t1_pct=22, trail_mode='breakeven', trail_pct=15):
    """
    Simulate with trailing SL after T1 hit.
    trail_mode:
      'book_t1' - book at T1 (baseline)
      'breakeven' - after T1, move SL to entry
      'lock_t1' - after T1, move SL to T1 level
      'trail_X' - after T1, trail X% below peak
    """
    bp = None; bd = 1e9
    for s in day_s:
        if s['strike_price'] == strike:
            st = datetime.fromisoformat(s['timestamp'])
            d = abs((st - ts).total_seconds())
            if d < bd: bd = d; bp = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
    if not bp or bp < 5: return None

    sl_p = bp * (1 - sl_pct / 100)
    t1_p = bp * (1 + t1_pct / 100)

    t1_hit = False
    peak = bp
    trailing_sl = sl_p

    for s in day_s:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= ts or s['strike_price'] != strike: continue
        p = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
        if not p or p <= 0: continue

        peak = max(peak, p)

        # Before T1: normal SL
        if not t1_hit:
            if p <= sl_p:
                pnl = (p - bp) / bp * 100
                return {'pnl': pnl, 'reason': 'SL', 'ep': bp, 'xp': p, 't1_hit': False, 'peak': peak, 'xt': st}
            if p >= t1_p:
                t1_hit = True
                if trail_mode == 'book_t1':
                    pnl = (p - bp) / bp * 100
                    return {'pnl': pnl, 'reason': 'T1', 'ep': bp, 'xp': p, 't1_hit': True, 'peak': peak, 'xt': st}
                elif trail_mode == 'breakeven':
                    trailing_sl = bp  # move SL to entry
                elif trail_mode == 'lock_t1':
                    trailing_sl = t1_p * 0.95  # lock just below T1
                elif trail_mode.startswith('trail'):
                    trailing_sl = peak * (1 - trail_pct / 100)
        else:
            # After T1: update trailing SL
            if trail_mode.startswith('trail'):
                trailing_sl = peak * (1 - trail_pct / 100)
            
            # Check trailing SL
            if p <= trailing_sl:
                pnl = (p - bp) / bp * 100
                return {'pnl': pnl, 'reason': 'TRAIL', 'ep': bp, 'xp': p, 't1_hit': True, 'peak': peak, 'xt': st}

        # EOD
        if st.hour == 15 and st.minute >= 20:
            pnl = (p - bp) / bp * 100
            return {'pnl': pnl, 'reason': 'EOD', 'ep': bp, 'xp': p, 't1_hit': t1_hit, 'peak': peak, 'xt': st}
    return None

def run_strategy(mode, trail_pct=15):
    trades = []
    for day in sorted(aby.keys()):
        done = False
        for a in aby[day]:
            if done: break
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < 11 or ts.hour >= 14: continue
            v = a.get('verdict', '') or ''; co = a.get('signal_confidence', 0) or 0
            if co < 65: continue
            spot = a['spot_price']; atm = round(spot / 50) * 50
            if 'Bear' in v: ot = 'PE'
            elif 'Bull' in v: ot = 'CE'
            else: continue
            r = sim_trail(ts, atm, ot, sby.get(day, []), trail_mode=mode, trail_pct=trail_pct)
            if not r: continue
            done = True
            r['day'] = day; r['ot'] = ot; r['v'] = v; r['co'] = co
            trades.append(r)
    return trades

# Run all strategies
strategies = [
    ('book_t1', 0, 'Always book at T1 (+22%)'),
    ('breakeven', 0, 'After T1: SL to breakeven'),
    ('lock_t1', 0, 'After T1: SL locked at T1'),
    ('trail', 10, 'After T1: trail 10% below peak'),
    ('trail', 15, 'After T1: trail 15% below peak'),
    ('trail', 20, 'After T1: trail 20% below peak'),
    ('trail', 25, 'After T1: trail 25% below peak'),
]

print("=" * 100)
print("IRON PULSE — Trailing SL Backtest")
print("After T1 (+22%) is hit, what if we trail the stop instead of booking?")
print("=" * 100)

# Detailed results for each
for mode, tpct, label in strategies:
    trades = run_strategy(mode, tpct)
    wins = sum(1 for t in trades if t['pnl'] > 0)
    losses = len(trades) - wins
    total_pnl = sum(t['pnl'] for t in trades)
    n = len(trades)
    wr = wins / n * 100 if n > 0 else 0
    
    win_pnls = [t['pnl'] for t in trades if t['pnl'] > 0]
    loss_pnls = [t['pnl'] for t in trades if t['pnl'] <= 0]
    avg_w = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_l = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    pf = abs(sum(win_pnls)) / abs(sum(loss_pnls)) if loss_pnls and sum(loss_pnls) != 0 else 999
    
    print(f"\n--- {label} ---")
    print(f"  {wins}W/{losses}L ({wr:.1f}% WR) | P&L: {total_pnl:+.1f}% | Avg Win: {avg_w:+.1f}% | Avg Loss: {avg_l:+.1f}% | PF: {pf:.2f}")
    
    print(f"  {'Day':<12} {'Dir':<8} {'Entry':>6} {'Peak':>6} {'Exit':>6} {'P&L':>8} {'Reason':<7}")
    print(f"  {'-'*60}")
    for t in trades:
        pk_pct = (t['peak'] - t['ep']) / t['ep'] * 100
        e = "W" if t['pnl'] > 0 else "L"
        print(f"  {t['day']:<12} BUY_{t['ot']:<3} {t['ep']:>6.1f} {t['peak']:>6.1f} {t['xp']:>6.1f} {t['pnl']:>+7.1f}% {t['reason']:<7} {e}")

# Summary comparison
print("\n" + "=" * 100)
print("SUMMARY COMPARISON")
print("=" * 100)
print(f"{'Strategy':<35} {'W':>3} {'L':>3} {'WR':>7} {'P&L':>9} {'Avg W':>8} {'Avg L':>8} {'PF':>6}")
print("-" * 80)

for mode, tpct, label in strategies:
    trades = run_strategy(mode, tpct)
    wins = sum(1 for t in trades if t['pnl'] > 0)
    losses = len(trades) - wins
    total_pnl = sum(t['pnl'] for t in trades)
    n = len(trades)
    wr = wins / n * 100 if n > 0 else 0
    win_pnls = [t['pnl'] for t in trades if t['pnl'] > 0]
    loss_pnls = [t['pnl'] for t in trades if t['pnl'] <= 0]
    avg_w = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_l = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    pf = abs(sum(win_pnls)) / abs(sum(loss_pnls)) if loss_pnls and sum(loss_pnls) != 0 else 999
    print(f"  {label:<33} {wins:>3} {losses:>3} {wr:>6.1f}% {total_pnl:>+8.1f}% {avg_w:>+7.1f}% {avg_l:>+7.1f}% {pf:>5.2f}")
