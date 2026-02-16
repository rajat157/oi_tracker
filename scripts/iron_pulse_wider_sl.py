"""
Test Iron Pulse with wider SL but maintaining 1:2 RR.
SL 15/30, 20/40, 25/50, 30/60, 35/70, 40/80
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

def sim(ts, strike, otype, day_s, sl_pct, tgt_pct):
    bp = None; bd = 1e9
    for s in day_s:
        if s['strike_price'] == strike:
            st = datetime.fromisoformat(s['timestamp'])
            d = abs((st - ts).total_seconds())
            if d < bd:
                bd = d
                bp = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
    if not bp or bp < 5: return None
    sl_p = bp * (1 - sl_pct/100)
    tgt_p = bp * (1 + tgt_pct/100)
    mx = bp; mn = bp
    for s in day_s:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= ts or s['strike_price'] != strike: continue
        p = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
        if not p or p <= 0: continue
        mx = max(mx, p); mn = min(mn, p)
        if p <= sl_p:
            return {'r': 'SL', 'pnl': (p-bp)/bp*100, 'ep': bp, 'xp': p, 'xt': st, 'mx': mx, 'mn': mn}
        if p >= tgt_p:
            return {'r': 'TGT', 'pnl': (p-bp)/bp*100, 'ep': bp, 'xp': p, 'xt': st, 'mx': mx, 'mn': mn}
        if st.hour == 15 and st.minute >= 20:
            return {'r': 'EOD', 'pnl': (p-bp)/bp*100, 'ep': bp, 'xp': p, 'xt': st, 'mx': mx, 'mn': mn}
    return None

def run_backtest(sl_pct, tgt_pct):
    trades = []
    for day in sorted(aby.keys()):
        done = False
        for a in aby[day]:
            if done: break
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < 11 or ts.hour >= 14: continue
            v = a.get('verdict','') or ''
            co = a.get('signal_confidence',0) or 0
            if co < 65: continue
            spot = a['spot_price']; atm = round(spot/50)*50
            if 'Bear' in v: d='BUY_PUT'; ot='PE'
            elif 'Bull' in v: d='BUY_CALL'; ot='CE'
            else: continue
            r = sim(ts, atm, ot, sby.get(day,[]), sl_pct, tgt_pct)
            if not r: continue
            done = True
            r['day'] = day; r['d'] = d; r['ts'] = ts; r['v'] = v; r['co'] = co
            trades.append(r)
    return trades

# Current Iron Pulse (1:1.1)
print("=" * 100)
print("IRON PULSE — SL/Target Sweep (all maintain 1:2 RR except current)")
print("=" * 100)

configs = [
    (20, 22, "Current (1:1.1)"),
    (15, 30, "1:2"),
    (20, 40, "1:2"),
    (25, 50, "1:2"),
    (30, 60, "1:2"),
    (35, 70, "1:2"),
    (40, 80, "1:2"),
]

print(f"\n{'Config':<22} {'SL':>4} {'TP':>4} {'RR':>5} {'Trades':>7} {'W':>3} {'L':>3} {'EOD+':>5} {'EOD-':>5} {'WR':>7} {'P&L':>8} {'PF':>6} {'Avg Win':>8} {'Avg Loss':>9}")
print("-" * 110)

all_results = {}

for sl, tp, label in configs:
    trades = run_backtest(sl, tp)
    w = sum(1 for t in trades if t['r'] == 'TGT')
    sl_l = sum(1 for t in trades if t['r'] == 'SL')
    eod_p = sum(1 for t in trades if t['r'] == 'EOD' and t['pnl'] >= 0)
    eod_n = sum(1 for t in trades if t['r'] == 'EOD' and t['pnl'] < 0)
    
    wins = w + eod_p
    losses = sl_l + eod_n
    total = len(trades)
    wr = wins / total * 100 if total > 0 else 0
    pnl = sum(t['pnl'] for t in trades)
    
    win_pnls = [t['pnl'] for t in trades if t['r'] == 'TGT' or (t['r'] == 'EOD' and t['pnl'] >= 0)]
    loss_pnls = [t['pnl'] for t in trades if t['r'] == 'SL' or (t['r'] == 'EOD' and t['pnl'] < 0)]
    
    avg_w = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_l = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
    
    gross_w = sum(win_pnls) if win_pnls else 0
    gross_l = abs(sum(loss_pnls)) if loss_pnls else 0.01
    pf = gross_w / gross_l if gross_l > 0 else 999
    
    rr = f"1:{tp/sl:.1f}"
    print(f"  SL {sl}% TP {tp}% {label:<8} {rr:>5} {total:>7} {w:>3} {sl_l:>3} {eod_p:>5} {eod_n:>5} {wr:>6.1f}% {pnl:>+7.1f}% {pf:>5.1f}x {avg_w:>+7.1f}% {avg_l:>+8.1f}%")
    
    all_results[(sl, tp)] = trades

# Detailed trade-by-trade for most interesting configs
for sl, tp in [(25, 50), (30, 60), (35, 70)]:
    trades = all_results[(sl, tp)]
    print(f"\n{'='*90}")
    print(f"  SL {sl}% / TP {tp}% (1:2 RR) — Trade Details")
    print(f"{'='*90}")
    print(f"  {'Day':<12} {'Dir':<10} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Result':<5} {'Dur':>6} {'Verdict':<22} {'Conf':>5}")
    print(f"  {'-'*82}")
    for t in trades:
        dur = (t['xt'] - t['ts']).total_seconds() / 60
        won = t['r'] == 'TGT' or (t['r'] == 'EOD' and t['pnl'] >= 0)
        emoji = "W" if won else "L"
        print(f"  {t['day']:<12} {t['d']:<10} {t['ep']:>7.1f} {t['xp']:>7.1f} {t['pnl']:>+7.1f}% {t['r']:<5} {dur:>5.0f}m {t['v']:<22} {t['co']:>4.0f}%")
