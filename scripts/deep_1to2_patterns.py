"""
Deep analysis of 1:2 winning days vs no-win days.
Goal: Find conditions present on ALL winning days but absent on no-win days.
"""
import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

def q(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(sql, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_prem(snaps, ts, strike, ot):
    best, bd = None, float('inf')
    for s in snaps:
        if s['strike_price'] == strike:
            d = abs((datetime.fromisoformat(s['timestamp']) - ts).total_seconds())
            if d < bd:
                bd = d
                best = s['ce_ltp'] if ot == 'CE' else s['pe_ltp']
    return best if best and best > 0 else None

def sim_buy(snaps, entry_time, strike, ot, entry_p, sl_pct=25, tgt_pct=50):
    sl = entry_p * (1 - sl_pct/100)
    tgt = entry_p * (1 + tgt_pct/100)
    for s in snaps:
        st = datetime.fromisoformat(s['timestamp'])
        if st <= entry_time or s['strike_price'] != strike:
            continue
        p = s['ce_ltp'] if ot == 'CE' else s['pe_ltp']
        if not p or p <= 0:
            continue
        if p <= sl: return p, 'SL', st
        if p >= tgt: return p, 'TARGET', st
        if st.hour == 15 and st.minute >= 20: return p, 'EOD', st
    return None, None, None

# Load all data
analysis_data = q("""
    SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
           futures_oi_change, futures_basis, vix, iv_skew, prev_verdict,
           call_oi_change, put_oi_change, total_call_oi, total_put_oi
    FROM analysis_history
    WHERE DATE(timestamp) >= '2026-02-01' AND DATE(timestamp) < '2026-02-16'
    ORDER BY timestamp
""")
snapshots_data = q("""
    SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp, ce_iv, pe_iv
    FROM oi_snapshots
    WHERE DATE(timestamp) >= '2026-02-01' AND DATE(timestamp) < '2026-02-16'
        AND (ce_ltp > 0 OR pe_ltp > 0)
    ORDER BY timestamp, strike_price
""")

a_by_day = defaultdict(list)
for a in analysis_data: a_by_day[a['timestamp'][:10]].append(a)
s_by_day = defaultdict(list)
for s in snapshots_data: s_by_day[s['timestamp'][:10]].append(s)

days = sorted(d for d in a_by_day.keys())

# ============================================================
# For each day, compute comprehensive market profile
# ============================================================
print("="*120)
print("COMPREHENSIVE DAY PROFILES — Winning vs No-Win Days")
print("="*120)

day_profiles = []

for day in days:
    da = a_by_day[day]
    ds = s_by_day.get(day, [])
    
    spots = [a['spot_price'] for a in da]
    spot_open = spots[0] if spots else 0
    spot_high = max(spots) if spots else 0
    spot_low = min(spots) if spots else 0
    spot_close = spots[-1] if spots else 0
    spot_range = spot_high - spot_low
    spot_direction = spot_close - spot_open  # positive = up day
    
    vix_vals = [a.get('vix',0) or 0 for a in da if (a.get('vix',0) or 0) > 0]
    avg_vix = sum(vix_vals)/len(vix_vals) if vix_vals else 0
    
    conf_vals = [a.get('signal_confidence',0) or 0 for a in da]
    avg_conf = sum(conf_vals)/len(conf_vals) if conf_vals else 0
    
    # Verdict distribution
    v_counts = defaultdict(int)
    for a in da: v_counts[a['verdict']] += 1
    dominant_verdict = max(v_counts.items(), key=lambda x: x[1])[0] if v_counts else ''
    slightly_bearish_pct = v_counts.get('Slightly Bearish', 0) / len(da) * 100 if da else 0
    slightly_bullish_pct = v_counts.get('Slightly Bullish', 0) / len(da) * 100 if da else 0
    bears_winning_pct = v_counts.get('Bears Winning', 0) / len(da) * 100 if da else 0
    bulls_winning_pct = v_counts.get('Bulls Winning', 0) / len(da) * 100 if da else 0
    
    # Futures OI
    fut_oi_vals = [a.get('futures_oi_change',0) or 0 for a in da]
    avg_fut_oi = sum(fut_oi_vals)/len(fut_oi_vals) if fut_oi_vals else 0
    
    # IV skew
    iv_vals = [a.get('iv_skew',0) or 0 for a in da]
    avg_iv_skew = sum(iv_vals)/len(iv_vals) if iv_vals else 0
    
    # Basis
    basis_vals = [a.get('futures_basis',0) or 0 for a in da]
    avg_basis = sum(basis_vals)/len(basis_vals) if basis_vals else 0
    
    # OI changes
    call_oi_vals = [a.get('call_oi_change',0) or 0 for a in da]
    put_oi_vals = [a.get('put_oi_change',0) or 0 for a in da]
    avg_call_oi_chg = sum(call_oi_vals)/len(call_oi_vals) if call_oi_vals else 0
    avg_put_oi_chg = sum(put_oi_vals)/len(put_oi_vals) if put_oi_vals else 0
    
    # Morning conditions (9:30-11:00) — what does the market look like early?
    morning = [a for a in da if datetime.fromisoformat(a['timestamp']).hour < 11]
    morning_spots = [a['spot_price'] for a in morning]
    morning_range = (max(morning_spots) - min(morning_spots)) if len(morning_spots) > 1 else 0
    morning_direction = (morning_spots[-1] - morning_spots[0]) if len(morning_spots) > 1 else 0
    morning_verdicts = [a['verdict'] for a in morning]
    morning_bearish = sum(1 for v in morning_verdicts if 'Bearish' in v)
    morning_bullish = sum(1 for v in morning_verdicts if 'Bullish' in v)
    
    # Check if 1:2 trade exists
    has_winner = False
    best_trade = None
    for a in da:
        if has_winner: break
        ts = datetime.fromisoformat(a['timestamp'])
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30) or ts.hour >= 14:
            continue
        spot = a['spot_price']
        atm = round(spot / 50) * 50
        for ot in ['CE', 'PE']:
            ep = get_prem(ds, ts, atm, ot)
            if not ep or ep < 10: continue
            exit_p, reason, exit_t = sim_buy(ds, ts, atm, ot, ep)
            if reason == 'TARGET':
                aligned = ('Bullish' in a['verdict'] and ot == 'CE') or ('Bearish' in a['verdict'] and ot == 'PE')
                has_winner = True
                best_trade = {
                    'time': ts.strftime('%H:%M'),
                    'dir': f"BUY_{'CALL' if ot=='CE' else 'PUT'}",
                    'strike': atm,
                    'entry': ep,
                    'pnl': ((exit_p - ep)/ep)*100,
                    'aligned': aligned,
                    'verdict': a['verdict'],
                    'conf': a['signal_confidence'] or 0,
                }
                break
    
    profile = {
        'day': day,
        'has_winner': has_winner,
        'best_trade': best_trade,
        'spot_range': spot_range,
        'spot_direction': spot_direction,
        'morning_range': morning_range,
        'morning_direction': morning_direction,
        'morning_bearish': morning_bearish,
        'morning_bullish': morning_bullish,
        'avg_vix': avg_vix,
        'avg_conf': avg_conf,
        'dominant_verdict': dominant_verdict,
        'sl_bearish_pct': slightly_bearish_pct,
        'sl_bullish_pct': slightly_bullish_pct,
        'bears_winning_pct': bears_winning_pct,
        'bulls_winning_pct': bulls_winning_pct,
        'avg_fut_oi': avg_fut_oi,
        'avg_iv_skew': avg_iv_skew,
        'avg_basis': avg_basis,
        'avg_call_oi_chg': avg_call_oi_chg,
        'avg_put_oi_chg': avg_put_oi_chg,
    }
    day_profiles.append(profile)

# Print profiles
win_days = [p for p in day_profiles if p['has_winner']]
nowin_days = [p for p in day_profiles if not p['has_winner']]

print(f"\n{'Day':<12} {'Win':>3} {'SpotRng':>8} {'SpotDir':>8} {'MornRng':>8} {'MornDir':>8} "
      f"{'VIX':>5} {'AvgConf':>7} {'Dominant':<22} {'SlBear%':>7} {'SlBull%':>7} "
      f"{'FutOI':>7} {'IVSkew':>6} {'Basis':>6}")
print("-" * 150)
for p in day_profiles:
    w = 'YES' if p['has_winner'] else 'NO'
    print(f"{p['day']:<12} {w:>3} {p['spot_range']:>8.0f} {p['spot_direction']:>+8.0f} "
          f"{p['morning_range']:>8.0f} {p['morning_direction']:>+8.0f} "
          f"{p['avg_vix']:>5.1f} {p['avg_conf']:>7.0f} {p['dominant_verdict']:<22} "
          f"{p['sl_bearish_pct']:>6.0f}% {p['sl_bullish_pct']:>6.0f}% "
          f"{p['avg_fut_oi']:>7.0f} {p['avg_iv_skew']:>6.1f} {p['avg_basis']:>6.1f}")

# Best trade details
print(f"\nBest 1:2 trades on winning days:")
for p in win_days:
    t = p['best_trade']
    print(f"  {p['day']}: {t['time']} {t['dir']:<10} {t['strike']} @ {t['entry']:.2f} "
          f"P&L={t['pnl']:+.1f}% aligned={t['aligned']} v={t['verdict']} conf={t['conf']:.0f}%")

# ============================================================
# Statistical comparison
# ============================================================
print(f"\n\n{'='*120}")
print("STATISTICAL COMPARISON: Winning Days vs No-Win Days")
print("="*120)

def compare(label, win_vals, nowin_vals):
    w_avg = sum(win_vals)/len(win_vals) if win_vals else 0
    nw_avg = sum(nowin_vals)/len(nowin_vals) if nowin_vals else 0
    w_min = min(win_vals) if win_vals else 0
    w_max = max(win_vals) if win_vals else 0
    nw_min = min(nowin_vals) if nowin_vals else 0
    nw_max = max(nowin_vals) if nowin_vals else 0
    diff = w_avg - nw_avg
    print(f"  {label:<25} WIN avg={w_avg:>8.1f} (range {w_min:.1f}-{w_max:.1f})  |  "
          f"NOWIN avg={nw_avg:>8.1f} (range {nw_min:.1f}-{nw_max:.1f})  |  diff={diff:>+8.1f}")

compare("Spot Range (pts)", [p['spot_range'] for p in win_days], [p['spot_range'] for p in nowin_days])
compare("Spot Direction", [p['spot_direction'] for p in win_days], [p['spot_direction'] for p in nowin_days])
compare("Morning Range", [p['morning_range'] for p in win_days], [p['morning_range'] for p in nowin_days])
compare("Morning Direction", [p['morning_direction'] for p in win_days], [p['morning_direction'] for p in nowin_days])
compare("VIX", [p['avg_vix'] for p in win_days], [p['avg_vix'] for p in nowin_days])
compare("Avg Confidence", [p['avg_conf'] for p in win_days], [p['avg_conf'] for p in nowin_days])
compare("Sl.Bearish %", [p['sl_bearish_pct'] for p in win_days], [p['sl_bearish_pct'] for p in nowin_days])
compare("Sl.Bullish %", [p['sl_bullish_pct'] for p in win_days], [p['sl_bullish_pct'] for p in nowin_days])
compare("Bears Winning %", [p['bears_winning_pct'] for p in win_days], [p['bears_winning_pct'] for p in nowin_days])
compare("Avg Futures OI Chg", [p['avg_fut_oi'] for p in win_days], [p['avg_fut_oi'] for p in nowin_days])
compare("Avg IV Skew", [p['avg_iv_skew'] for p in win_days], [p['avg_iv_skew'] for p in nowin_days])
compare("Avg Basis", [p['avg_basis'] for p in win_days], [p['avg_basis'] for p in nowin_days])
compare("Avg Call OI Chg", [p['avg_call_oi_chg'] for p in win_days], [p['avg_call_oi_chg'] for p in nowin_days])
compare("Avg Put OI Chg", [p['avg_put_oi_chg'] for p in win_days], [p['avg_put_oi_chg'] for p in nowin_days])
compare("Morn Bearish Count", [p['morning_bearish'] for p in win_days], [p['morning_bearish'] for p in nowin_days])
compare("Morn Bullish Count", [p['morning_bullish'] for p in win_days], [p['morning_bullish'] for p in nowin_days])

# ============================================================
# THRESHOLD ANALYSIS: Can we find a filter?
# ============================================================
print(f"\n\n{'='*120}")
print("THRESHOLD ANALYSIS: Testing filters that separate WIN from NO-WIN days")
print("="*120)

def test_filter(label, fn):
    win_pass = sum(1 for p in win_days if fn(p))
    nowin_pass = sum(1 for p in nowin_days if fn(p))
    win_total = len(win_days)
    nowin_total = len(nowin_days)
    # We want: high win_pass, low nowin_pass
    precision = win_pass / (win_pass + nowin_pass) * 100 if (win_pass + nowin_pass) > 0 else 0
    recall = win_pass / win_total * 100 if win_total else 0
    print(f"  {label:<50} WIN: {win_pass}/{win_total}  NOWIN: {nowin_pass}/{nowin_total}  "
          f"Precision={precision:.0f}% Recall={recall:.0f}%")

print("\n--- Spot Range Filters ---")
for thresh in [100, 150, 200, 250, 300]:
    test_filter(f"spot_range > {thresh}", lambda p, t=thresh: p['spot_range'] > t)

print("\n--- Morning Range Filters ---")
for thresh in [30, 50, 75, 100, 150]:
    test_filter(f"morning_range > {thresh}", lambda p, t=thresh: p['morning_range'] > t)

print("\n--- Morning Direction Filters ---")
for thresh in [-50, -30, -20, 0, 20]:
    test_filter(f"morning_direction < {thresh}", lambda p, t=thresh: p['morning_direction'] < t)
for thresh in [20, 50]:
    test_filter(f"abs(morning_direction) > {thresh}", lambda p, t=thresh: abs(p['morning_direction']) > t)

print("\n--- VIX Filters ---")
for thresh in [11, 11.5, 12, 12.5, 13, 14]:
    test_filter(f"VIX > {thresh}", lambda p, t=thresh: p['avg_vix'] > t)

print("\n--- Verdict Mix Filters ---")
test_filter("sl_bearish > 30%", lambda p: p['sl_bearish_pct'] > 30)
test_filter("sl_bearish > 40%", lambda p: p['sl_bearish_pct'] > 40)
test_filter("bears_winning < 30%", lambda p: p['bears_winning_pct'] < 30)
test_filter("dominant = Sl.Bearish", lambda p: p['dominant_verdict'] == 'Slightly Bearish')
test_filter("NOT dominant Sl.Bullish", lambda p: p['dominant_verdict'] != 'Slightly Bullish')

print("\n--- Basis Filters ---")
for thresh in [30, 40, 50, 60, 70]:
    test_filter(f"basis > {thresh}", lambda p, t=thresh: p['avg_basis'] > t)
for thresh in [30, 40, 50]:
    test_filter(f"basis < {thresh}", lambda p, t=thresh: p['avg_basis'] < t)

print("\n--- IV Skew Filters ---")
for thresh in [0, 1, 2, 3]:
    test_filter(f"iv_skew < {thresh}", lambda p, t=thresh: p['avg_iv_skew'] < t)

print("\n--- Futures OI Filters ---")
test_filter("avg_fut_oi < 0", lambda p: p['avg_fut_oi'] < 0)
test_filter("avg_fut_oi < -50", lambda p: p['avg_fut_oi'] < -50)
test_filter("avg_fut_oi > 0", lambda p: p['avg_fut_oi'] > 0)

print("\n--- OI Change Filters ---")
test_filter("put_oi_chg > call_oi_chg", lambda p: p['avg_put_oi_chg'] > p['avg_call_oi_chg'])
test_filter("put_oi_chg > 0", lambda p: p['avg_put_oi_chg'] > 0)

print("\n--- Combination Filters ---")
test_filter("morning_range>50 AND NOT dom=Sl.Bullish",
            lambda p: p['morning_range'] > 50 and p['dominant_verdict'] != 'Slightly Bullish')
test_filter("morning_range>75 AND sl_bearish>30%",
            lambda p: p['morning_range'] > 75 and p['sl_bearish_pct'] > 30)
test_filter("abs(morning_dir)>20 AND NOT dom=Sl.Bullish",
            lambda p: abs(p['morning_direction']) > 20 and p['dominant_verdict'] != 'Slightly Bullish')
test_filter("spot_range>100 AND sl_bearish>30%",
            lambda p: p['spot_range'] > 100 and p['sl_bearish_pct'] > 30)
test_filter("morning_range>50 AND basis<60",
            lambda p: p['morning_range'] > 50 and p['avg_basis'] < 60)
test_filter("NOT(dom=Sl.Bullish) AND NOT(dom=Bears Winning) AND morn_range>30",
            lambda p: p['dominant_verdict'] not in ['Slightly Bullish', 'Bears Winning'] and p['morning_range'] > 30)
test_filter("morning_range>50 AND iv_skew<2",
            lambda p: p['morning_range'] > 50 and p['avg_iv_skew'] < 2)
test_filter("spot_range>150 AND NOT dom=Sl.Bullish",
            lambda p: p['spot_range'] > 150 and p['dominant_verdict'] != 'Slightly Bullish')

# ============================================================
# NOW: For the best filters, simulate actual 1:2 trades
# ============================================================
print(f"\n\n{'='*120}")
print("SIMULATED STRATEGY: Using best filters to pick 1 trade/day")
print("="*120)
print("\nNote: These use MORNING data only (available before entry) as filters.")
print("Entry: first verdict-aligned Slightly Bearish -> BUY PUT at ATM, 11:00-14:00\n")

# Since we can't use full-day spot range at entry time, let's use morning data
# Approach: at 11:00, check morning conditions, then enter first qualifying signal

def calc_morning_range(da):
    morning = [a for a in da if datetime.fromisoformat(a['timestamp']).hour < 11]
    if len(morning) < 2: return 0
    spots = [a['spot_price'] for a in morning]
    return max(spots) - min(spots)

def calc_morning_dir(da):
    morning = [a for a in da if datetime.fromisoformat(a['timestamp']).hour < 11]
    if len(morning) < 2: return 0
    spots = [a['spot_price'] for a in morning]
    return spots[-1] - spots[0]

for filter_name, filter_fn in [
    ("Baseline (Config B, no extra filter)", lambda da, ds: True),
    ("Morning range > 50pts", lambda da, ds: calc_morning_range(da) > 50),
    ("Morning range > 75pts", lambda da, ds: calc_morning_range(da) > 75),
    ("Morning range > 100pts", lambda da, ds: calc_morning_range(da) > 100),
    ("Morning drop > 30pts (bearish morning)", lambda da, ds: calc_morning_dir(da) < -30),
    ("Morning move > 50pts (volatile)", lambda da, ds: abs(calc_morning_dir(da)) > 50),
]:
    trades = []
    skipped = 0
    for day in days:
        da = a_by_day[day]
        ds = s_by_day.get(day, [])
        if not ds: continue
        
        if not filter_fn(da, ds):
            skipped += 1
            continue
        
        # Enter first Slightly Bearish -> BUY PUT in 11:00-14:00 with conf>=65
        traded = False
        for a in da:
            if traded: break
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < 11 or ts.hour >= 14: continue
            if a['verdict'] != 'Slightly Bearish': continue
            if (a['signal_confidence'] or 0) < 65: continue
            
            spot = a['spot_price']
            atm = round(spot / 50) * 50
            ep = get_prem(ds, ts, atm, 'PE')
            if not ep or ep < 10: continue
            
            exit_p, reason, exit_t = sim_buy(ds, ts, atm, 'PE', ep)
            if exit_p:
                pnl = ((exit_p - ep)/ep) * 100
                trades.append({
                    'day': day, 'pnl': pnl, 'won': reason == 'TARGET',
                    'reason': reason, 'entry': ep, 'exit': exit_p,
                    'time': ts.strftime('%H:%M'), 'conf': a['signal_confidence'],
                })
                traded = True
    
    if trades:
        wins = [t for t in trades if t['won']]
        losses = [t for t in trades if not t['won']]
        wr = len(wins)/len(trades)*100
        pnl = sum(t['pnl'] for t in trades)
        print(f"\n  [{filter_name}]")
        print(f"  Trades: {len(trades)} | Skipped: {skipped} | Wins: {len(wins)} | WR: {wr:.1f}% | P&L: {pnl:+.1f}%")
        for t in trades:
            m = 'W' if t['won'] else 'L'
            print(f"    {t['day']} {t['time']} PUT Rs{t['entry']:.2f}->Rs{t['exit']:.2f} {t['pnl']:+.1f}% {t['reason']} {m}")

def calc_morning_range(da):
    morning = [a for a in da if datetime.fromisoformat(a['timestamp']).hour < 11]
    if len(morning) < 2: return 0
    spots = [a['spot_price'] for a in morning]
    return max(spots) - min(spots)

def calc_morning_dir(da):
    morning = [a for a in da if datetime.fromisoformat(a['timestamp']).hour < 11]
    if len(morning) < 2: return 0
    spots = [a['spot_price'] for a in morning]
    return spots[-1] - spots[0]
