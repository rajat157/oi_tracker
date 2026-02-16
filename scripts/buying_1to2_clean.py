"""Buying 1:2 RR backtest on clean data (excluding Jan 30)."""
from backtest_buying_1to2 import get_analysis_data, get_option_prices_for_day, get_premium_at_time
from collections import defaultdict
from datetime import datetime

analysis = get_analysis_data()
by_day = defaultdict(list)
for a in analysis:
    day = a['timestamp'][:10]
    if day != '2026-01-30' and day != '2026-02-16':
        by_day[day].append(a)

snapshots_cache = {}

def simulate_buy(day_snapshots, entry_time, strike, option_type, entry_premium, sl_pct, target_pct):
    sl_p = entry_premium * (1 - sl_pct / 100)
    tgt_p = entry_premium * (1 + target_pct / 100)
    for snap in day_snapshots:
        st = datetime.fromisoformat(snap['timestamp'])
        if st <= entry_time or snap['strike_price'] != strike:
            continue
        price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
        if not price or price <= 0:
            continue
        if price <= sl_p:
            return price, 'SL'
        if price >= tgt_p:
            return price, 'TARGET'
        if st.hour == 15 and st.minute >= 20:
            return price, 'EOD'
    for snap in reversed(day_snapshots):
        if snap['strike_price'] == strike:
            price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
            if price and price > 0:
                return price, 'EOD'
    return None, None

def run(sl_pct, target_pct, min_conf=65, time_start=11, time_end=14, futures_aligned=False, vix_max=100, consistent=False, strike_mode='ATM'):
    trades = []
    for day_str in sorted(by_day.keys()):
        if day_str not in snapshots_cache:
            snapshots_cache[day_str] = get_option_prices_for_day(day_str)
        snaps = snapshots_cache[day_str]
        if not snaps:
            continue
        traded = False
        for a in by_day[day_str]:
            if traded: break
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < time_start or ts.hour >= time_end: continue
            v = a['verdict']
            if v not in ['Slightly Bullish', 'Slightly Bearish']: continue
            c = a['signal_confidence'] or 0
            if c < min_conf: continue
            vix = a.get('vix', 0) or 0
            if vix > vix_max: continue
            if futures_aligned:
                foi = a.get('futures_oi_change', 0) or 0
                if 'Bullish' in v and foi < 0: continue
                if 'Bearish' in v and foi > 0: continue
            if consistent:
                pv = a.get('prev_verdict', '') or ''
                if 'Bullish' in v and 'Bullish' not in pv: continue
                if 'Bearish' in v and 'Bearish' not in pv: continue
            
            spot = a['spot_price']
            atm = round(spot / 50) * 50
            ot = 'CE' if 'Bullish' in v else 'PE'
            if strike_mode == 'ATM': strike = atm
            elif strike_mode == 'ITM1': strike = atm - 50 if ot == 'CE' else atm + 50
            else: strike = atm + 50 if ot == 'CE' else atm - 50
            
            ep = get_premium_at_time(snaps, ts, strike, ot)
            if not ep or ep < 5: continue
            
            exit_p, reason = simulate_buy(snaps, ts, strike, ot, ep, sl_pct, target_pct)
            if exit_p:
                pnl = ((exit_p - ep) / ep) * 100
                trades.append({'day': day_str, 'dir': 'BUY_CALL' if ot=='CE' else 'BUY_PUT',
                    'strike': strike, 'entry': ep, 'exit': exit_p, 'pnl': pnl,
                    'reason': reason, 'won': reason=='TARGET' or (reason=='EOD' and pnl>0),
                    'verdict': v, 'conf': c, 'vix': vix, 'spot': spot})
                traded = True
    return trades

def report(name, trades):
    if not trades:
        return {'name': name, 'n': 0}
    w = [t for t in trades if t['won']]
    l = [t for t in trades if not t['won']]
    pnl = sum(t['pnl'] for t in trades)
    aw = sum(t['pnl'] for t in w)/len(w) if w else 0
    al = sum(t['pnl'] for t in l)/len(l) if l else 0
    pf = abs(sum(t['pnl'] for t in w))/abs(sum(t['pnl'] for t in l)) if l and sum(t['pnl'] for t in l)!=0 else float('inf')
    tgt = len([t for t in trades if t['reason']=='TARGET'])
    sl = len([t for t in trades if t['reason']=='SL'])
    eod = len([t for t in trades if t['reason']=='EOD'])
    return {'name': name, 'n': len(trades), 'w': len(w), 'l': len(l), 'wr': len(w)/len(trades)*100,
            'pnl': pnl, 'aw': aw, 'al': al, 'pf': pf, 'tgt': tgt, 'sl': sl, 'eod': eod}

def table(results):
    print(f"\n{'Config':<55} {'#':>3} {'W':>3} {'L':>3} {'WR%':>6} {'P&L':>9} {'AvgW':>7} {'AvgL':>7} {'PF':>6} {'T/S/E':>7}")
    print("-" * 115)
    for r in sorted(results, key=lambda x: -x.get('pnl',0)):
        if r['n']==0: continue
        print(f"{r['name']:<55} {r['n']:>3} {r['w']:>3} {r['l']:>3} {r['wr']:>5.1f}% {r['pnl']:>+8.1f}% {r['aw']:>+6.1f}% {r['al']:>+6.1f}% {r['pf']:>6.2f} {r['tgt']}/{r['sl']}/{r['eod']:>5}")

def details(name, trades):
    print(f"\n--- {name} ---")
    print(f"{'Day':<12} {'Dir':<10} {'Strike':<7} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Reason':<7} {'Verdict':<20} {'Conf':>4}")
    for t in trades:
        m = 'W' if t['won'] else 'L'
        print(f"{t['day']:<12} {t['dir']:<10} {t['strike']:<7} {t['entry']:>7.2f} {t['exit']:>7.2f} {t['pnl']:>+7.1f}% {t['reason']:<7} {t['verdict']:<20} {t['conf']:>4.0f} {m}")

days = sorted(by_day.keys())
print(f"Clean data: {len(days)} trading days ({days[0]} to {days[-1]}), excluding Jan 30\n")

# SECTION 1: Basic combos
print("=" * 100)
print("SECTION 1: Basic 1:2 RR (Clean Data)")
print("=" * 100)
res = []
for sl, tgt in [(10,20),(15,30),(20,40),(25,50),(30,60)]:
    t = run(sl, tgt)
    res.append(report(f"BUY 1:2 ({sl}/{tgt}) ATM", t))
t = run(20, 22)
res.append(report("CURRENT (20/22) ATM", t))
table(res)
details("CURRENT (20/22)", run(20, 22))

# SECTION 2: Confidence
print("\n" + "=" * 100)
print("SECTION 2: Confidence Sweep (Clean Data)")
print("=" * 100)
res = []
for sl, tgt in [(20,40),(25,50)]:
    for conf in [60,65,70,75,80]:
        t = run(sl, tgt, min_conf=conf)
        res.append(report(f"BUY 1:2 ({sl}/{tgt}) conf>={conf}", t))
table(res)

# SECTION 3: VIX
print("\n" + "=" * 100)
print("SECTION 3: VIX Filter (Clean Data)")
print("=" * 100)
res = []
for sl, tgt in [(20,40),(25,50)]:
    for label, vm in [("VIX<13", 13), ("VIX<12.5", 12.5), ("Any", 100)]:
        t = run(sl, tgt, vix_max=vm)
        res.append(report(f"BUY 1:2 ({sl}/{tgt}) {label}", t))
table(res)

# SECTION 4: Futures alignment
print("\n" + "=" * 100)
print("SECTION 4: Futures OI Alignment (Clean Data)")
print("=" * 100)
res = []
for sl, tgt in [(20,40),(25,50)]:
    for fa in [False, True]:
        t = run(sl, tgt, futures_aligned=fa)
        res.append(report(f"BUY 1:2 ({sl}/{tgt}) {'FutAlign' if fa else 'NoFilter'}", t))
table(res)

# SECTION 5: Time windows
print("\n" + "=" * 100)
print("SECTION 5: Time Windows (Clean Data)")
print("=" * 100)
res = []
for sl, tgt in [(20,40),(25,50)]:
    for label, ts, te in [("11-14",11,14),("11-13",11,13),("12-14",12,14)]:
        t = run(sl, tgt, time_start=ts, time_end=te)
        res.append(report(f"BUY 1:2 ({sl}/{tgt}) {label}", t))
table(res)

# SECTION 6: Strike selection
print("\n" + "=" * 100)
print("SECTION 6: Strike Selection (Clean Data)")
print("=" * 100)
res = []
for sl, tgt in [(20,40),(25,50)]:
    for sm in ['ATM','ITM1','OTM1']:
        t = run(sl, tgt, strike_mode=sm)
        res.append(report(f"BUY 1:2 ({sl}/{tgt}) {sm}", t))
table(res)

# SECTION 7: Best combos
print("\n" + "=" * 100)
print("SECTION 7: Best Combos (Clean Data)")
print("=" * 100)
res = []
combos = [
    ("25/50 VIX<13", 25, 50, 65, 100, 13, False, False, 'ATM'),
    ("25/50 conf75", 25, 50, 75, 100, 100, False, False, 'ATM'),
    ("25/50 conf75 VIX<13", 25, 50, 75, 100, 13, False, False, 'ATM'),
    ("25/50 FutAlign", 25, 50, 65, 100, 100, True, False, 'ATM'),
    ("25/50 Consistent", 25, 50, 65, 100, 100, False, True, 'ATM'),
    ("25/50 Consistent VIX<13", 25, 50, 65, 100, 13, False, True, 'ATM'),
    ("20/40 12-14", 20, 40, 65, 12, 14, False, False, 'ATM'),
    ("20/40 VIX<13", 20, 40, 65, 100, 13, False, False, 'ATM'),
    ("20/40 conf75 VIX<13", 20, 40, 75, 100, 13, False, False, 'ATM'),
    ("25/50 12-14", 25, 50, 65, 12, 14, False, False, 'ATM'),
    ("20/40 Consistent", 20, 40, 65, 100, 100, False, True, 'ATM'),
]
for name, sl, tgt, conf, ts, te_or_vix, fa, cons, sm in combos:
    # Hack: if ts<20 it's time_start, te_or_vix is time_end; else te_or_vix is vix_max
    if ts < 20:  # time params
        t = run(sl, tgt, min_conf=conf, time_start=ts, time_end=te_or_vix, futures_aligned=fa, consistent=cons, strike_mode=sm)
    else:
        t = run(sl, tgt, min_conf=conf, vix_max=te_or_vix, futures_aligned=fa, consistent=cons, strike_mode=sm)
    r = report(f"COMBO: {name}", t)
    res.append(r)
    if r['n'] >= 5 and r['wr'] >= 70:
        details(f"COMBO: {name} | WR={r['wr']:.0f}% PF={r['pf']:.2f}", t)
table(res)
