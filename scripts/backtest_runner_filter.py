"""
Backtest: Iron Pulse with runner detection.
If runner signal at entry (IV Skew < 2 + ATM below max pain) → hold to T2 (+50%)
Otherwise → book at T1 (+22%)
Compare vs always booking at T1.
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

def sim_dual(ts, strike, otype, day_s, sl_pct=20, t1_pct=22, t2_pct=50):
    """Simulate with T1 and T2 targets."""
    bp = None; bd = 1e9
    for s in day_s:
        if s['strike_price'] == strike:
            st = datetime.fromisoformat(s['timestamp'])
            d = abs((st - ts).total_seconds())
            if d < bd: bd = d; bp = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
    if not bp or bp < 5: return None
    
    sl_p = bp * (1 - sl_pct/100)
    t1_p = bp * (1 + t1_pct/100)
    t2_p = bp * (1 + t2_pct/100)
    
    t1_hit = False; t1_time = None; t1_prem = None
    mx = bp
    
    for s in day_s:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= ts or s['strike_price'] != strike: continue
        p = s['ce_ltp' if otype == 'CE' else 'pe_ltp']
        if not p or p <= 0: continue
        mx = max(mx, p)
        
        # SL check (always active)
        if p <= sl_p:
            pnl = (p - bp) / bp * 100
            # If T1 was already hit, T1 P&L is the booking profit, not SL
            pnl_t1 = (t1_prem - bp) / bp * 100 if t1_hit else pnl
            return {'t1_hit': t1_hit, 'reason': 'SL', 'pnl_t1': pnl_t1, 'pnl_actual': pnl, 
                    'ep': bp, 'xp': p, 'mx': mx, 'xt': st}
        
        # T1 check
        if not t1_hit and p >= t1_p:
            t1_hit = True; t1_time = st; t1_prem = p
        
        # T2 check (only matters if we're holding)
        if p >= t2_p:
            pnl_t2 = (p - bp) / bp * 100
            pnl_t1 = (t1_prem - bp) / bp * 100 if t1_hit else pnl_t2
            return {'t1_hit': True, 'reason': 'T2', 'pnl_t1': pnl_t1, 'pnl_actual': pnl_t2,
                    'ep': bp, 'xp': p, 'mx': mx, 'xt': st}
        
        # EOD
        if st.hour == 15 and st.minute >= 20:
            pnl = (p - bp) / bp * 100
            pnl_t1 = (t1_prem - bp) / bp * 100 if t1_hit else pnl
            return {'t1_hit': t1_hit, 'reason': 'EOD', 'pnl_t1': pnl_t1 if t1_hit else pnl, 
                    'pnl_actual': pnl, 'ep': bp, 'xp': p, 'mx': mx, 'xt': st}
    return None

# Run backtest with three modes:
# 1. Always T1 (current Iron Pulse)
# 2. Always T2 (hold everything to 50%)
# 3. Smart: T2 when runner signal, T1 otherwise

print("=" * 100)
print("IRON PULSE — Runner Detection Backtest")
print("Runner signal: IV Skew < 2 AND ATM below Max Pain")
print("=" * 100)

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
        
        r = sim_dual(ts, atm, ot, sby.get(day, []))
        if not r: continue
        done = True
        
        ivs = a.get('iv_skew', 0) or 0
        mp = a.get('max_pain', 0) or 0
        is_runner_signal = ivs < 2 and atm < mp
        
        r['day'] = day; r['v'] = v; r['co'] = co; r['ivs'] = ivs
        r['mp'] = mp; r['atm'] = atm; r['ot'] = ot
        r['runner_signal'] = is_runner_signal
        trades.append(r)

# Print trade-by-trade
print(f"\n{'Day':<12} {'Dir':<8} {'Entry':>6} {'Runner?':<8} {'T1 Hit':<7} {'Exit':>6} {'T1 P&L':>8} {'Hold P&L':>9} {'Max%':>7} {'Reason':<6} {'IVSkew':>7} {'vMP':>5}")
print("-" * 100)

for t in trades:
    mx_pct = (t['mx'] - t['ep']) / t['ep'] * 100
    sig = "YES" if t['runner_signal'] else "no"
    t1h = "YES" if t['t1_hit'] else "no"
    vmp = "-" if t['atm'] < t['mp'] else "+"
    print(f"{t['day']:<12} BUY_{t['ot']:<3} {t['ep']:>6.1f} {sig:<8} {t1h:<7} {t['xp']:>6.1f} {t['pnl_t1']:>+7.1f}% {t['pnl_actual']:>+8.1f}% {mx_pct:>+6.1f}% {t['reason']:<6} {t['ivs']:>7.2f} {vmp:>5}")

# Strategy comparison
print("\n" + "=" * 100)
print("STRATEGY COMPARISON")
print("=" * 100)

# Mode 1: Always T1
t1_pnl = sum(t['pnl_t1'] for t in trades)
t1_wins = sum(1 for t in trades if t['pnl_t1'] > 0)
t1_losses = len(trades) - t1_wins

# Mode 2: Always hold to T2/EOD
t2_pnl = sum(t['pnl_actual'] for t in trades)
t2_wins = sum(1 for t in trades if t['pnl_actual'] > 0)
t2_losses = len(trades) - t2_wins

# Mode 3: Smart — T2 when runner signal, T1 otherwise
smart_pnl = 0
smart_wins = 0
smart_trades_detail = []
for t in trades:
    if t['runner_signal']:
        # Hold for T2
        pnl = t['pnl_actual']
    else:
        # Book at T1
        pnl = t['pnl_t1']
    smart_pnl += pnl
    if pnl > 0: smart_wins += 1
    smart_trades_detail.append((t['day'], t['runner_signal'], pnl, t['pnl_t1'], t['pnl_actual']))

smart_losses = len(trades) - smart_wins

n = len(trades)
print(f"\n{'Strategy':<30} {'W':>3} {'L':>3} {'WR':>7} {'Total P&L':>10} {'Avg':>8}")
print("-" * 65)
print(f"{'Always T1 (+22%)':<30} {t1_wins:>3} {t1_losses:>3} {t1_wins/n*100:>6.1f}% {t1_pnl:>+9.1f}% {t1_pnl/n:>+7.1f}%")
print(f"{'Always hold to T2/EOD':<30} {t2_wins:>3} {t2_losses:>3} {t2_wins/n*100:>6.1f}% {t2_pnl:>+9.1f}% {t2_pnl/n:>+7.1f}%")
print(f"{'Smart (runner=T2, else=T1)':<30} {smart_wins:>3} {smart_losses:>3} {smart_wins/n*100:>6.1f}% {smart_pnl:>+9.1f}% {smart_pnl/n:>+7.1f}%")

print(f"\n{'Smart strategy uplift over Always T1:':<40} {smart_pnl - t1_pnl:>+.1f}% total ({(smart_pnl - t1_pnl)/abs(t1_pnl)*100:>+.0f}% improvement)")

# Smart detail
print(f"\n--- Smart Strategy Detail ---")
print(f"{'Day':<12} {'Signal':<8} {'Action':<12} {'P&L':>8} {'vs T1':>8}")
print("-" * 50)
for day, sig, pnl, t1p, t2p in smart_trades_detail:
    action = "HOLD to T2" if sig else "BOOK at T1"
    diff = pnl - t1p
    print(f"{day:<12} {'RUNNER' if sig else 'normal':<8} {action:<12} {pnl:>+7.1f}% {diff:>+7.1f}%")

# Test other runner signal definitions
print("\n" + "=" * 100)
print("RUNNER SIGNAL VARIANTS")
print("=" * 100)

signals = [
    ("IV Skew < 2 + below MP", lambda t: t['ivs'] < 2 and t['atm'] < t['mp']),
    ("IV Skew < 1.5 + below MP", lambda t: t['ivs'] < 1.5 and t['atm'] < t['mp']),
    ("IV Skew < 1", lambda t: t['ivs'] < 1),
    ("IV Skew < 1.5", lambda t: t['ivs'] < 1.5),
    ("IV Skew < 2", lambda t: t['ivs'] < 2),
    ("Below Max Pain", lambda t: t['atm'] < t['mp']),
    ("Conf < 75", lambda t: t['co'] < 75),
    ("Conf < 70 + IV Skew < 2", lambda t: t['co'] < 70 and t['ivs'] < 2),
    ("IV Skew < 1.5 + Conf < 80", lambda t: t['ivs'] < 1.5 and t['co'] < 80),
]

print(f"\n{'Signal':<35} {'Triggers':>8} {'Smart P&L':>10} {'vs T1':>8} {'Smart WR':>9}")
print("-" * 75)
for name, fn in signals:
    sp = 0; sw = 0
    for t in trades:
        if fn(t):
            pnl = t['pnl_actual']  # hold
        else:
            pnl = t['pnl_t1']  # book
        sp += pnl
        if pnl > 0: sw += 1
    triggers = sum(1 for t in trades if fn(t))
    print(f"  {name:<33} {triggers:>8} {sp:>+9.1f}% {sp-t1_pnl:>+7.1f}% {sw/n*100:>8.1f}%")
