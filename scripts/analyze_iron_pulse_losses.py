"""
Analyze Iron Pulse (Config B) losses to find potential 90% WR filters.
Config B: 11:00-14:00, verdict-aligned, confidence >= 65%, 1 trade/day, SL 20%, Target 22%
"""
import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Get all analysis data
c = conn.cursor()
c.execute("""
    SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
           vix, iv_skew, max_pain, call_oi_change, put_oi_change,
           futures_oi_change, futures_basis, total_call_oi, total_put_oi,
           atm_call_oi_change, atm_put_oi_change, prev_verdict
    FROM analysis_history WHERE DATE(timestamp) >= '2026-02-01' ORDER BY timestamp
""")
analysis = [dict(r) for r in c.fetchall()]

c.execute("""
    SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp
    FROM oi_snapshots WHERE DATE(timestamp) >= '2026-02-01' AND (ce_ltp > 0 OR pe_ltp > 0)
    ORDER BY timestamp, strike_price
""")
snapshots = [dict(r) for r in c.fetchall()]
conn.close()

# Group by day
def group_by_day(items):
    by_day = defaultdict(list)
    for item in items:
        by_day[item['timestamp'][:10]].append(item)
    return by_day

analysis_by_day = group_by_day(analysis)
snaps_by_day = group_by_day(snapshots)

def get_spot_trend(day_a, ts, mins=30):
    prices = []
    for a in day_a:
        a_ts = datetime.fromisoformat(a['timestamp'])
        diff = (ts - a_ts).total_seconds()
        if 0 <= diff <= mins * 60:
            prices.append(a['spot_price'])
    if len(prices) < 2:
        return 0
    return (prices[-1] - prices[0]) / prices[0] * 100

def simulate_trade(ts, strike, option_type, day_snaps, sl_pct=20, target_pct=22):
    """Simulate Iron Pulse trade."""
    best_prem = None
    best_diff = float('inf')
    for snap in day_snaps:
        if snap['strike_price'] == strike:
            snap_time = datetime.fromisoformat(snap['timestamp'])
            diff = abs((snap_time - ts).total_seconds())
            if diff < best_diff:
                best_diff = diff
                key = 'ce_ltp' if option_type == 'CE' else 'pe_ltp'
                best_prem = snap[key]
    if not best_prem or best_prem < 5:
        return None
    sl_p = best_prem * (1 - sl_pct / 100)
    tgt_p = best_prem * (1 + target_pct / 100)
    
    max_prem = best_prem
    min_prem = best_prem
    
    for snap in day_snaps:
        snap_time = datetime.fromisoformat(snap['timestamp'])
        if snap_time <= ts or snap['strike_price'] != strike:
            continue
        key = 'ce_ltp' if option_type == 'CE' else 'pe_ltp'
        price = snap[key]
        if not price or price <= 0:
            continue
        max_prem = max(max_prem, price)
        min_prem = min(min_prem, price)
        if price <= sl_p:
            pnl = (price - best_prem) / best_prem * 100
            return ('SL', pnl, best_prem, price, snap_time, max_prem, min_prem)
        if price >= tgt_p:
            pnl = (price - best_prem) / best_prem * 100
            return ('TARGET', pnl, best_prem, price, snap_time, max_prem, min_prem)
        if snap_time.hour == 15 and snap_time.minute >= 20:
            pnl = (price - best_prem) / best_prem * 100
            return ('EOD', pnl, best_prem, price, snap_time, max_prem, min_prem)
    return None

# Run Config B simulation - find all trades
days = sorted(analysis_by_day.keys())
trades = []

for day in days:
    day_a = analysis_by_day.get(day, [])
    day_s = snaps_by_day.get(day, [])
    traded = False
    
    for a in day_a:
        if traded:
            break
        ts = datetime.fromisoformat(a['timestamp'])
        # 11:00 - 14:00 window
        if ts.hour < 11 or ts.hour >= 14:
            continue
        
        verdict = a.get('verdict', '') or ''
        conf = a.get('signal_confidence', 0) or 0
        
        # Config B filters: aligned, confidence >= 65%
        if conf < 65:
            continue
        if 'Slightly' not in verdict and 'Winning' not in verdict and 'Strongly' not in verdict:
            continue
        
        spot = a['spot_price']
        atm = round(spot / 50) * 50
        
        # Determine direction (aligned)
        if 'Bear' in verdict:
            direction = 'BUY_PUT'
            otype = 'PE'
        elif 'Bull' in verdict:
            direction = 'BUY_CALL'
            otype = 'CE'
        else:
            continue
        
        result = simulate_trade(ts, atm, otype, day_s)
        if not result:
            continue
        
        reason, pnl, entry_p, exit_p, exit_ts, max_p, min_p = result
        traded = True
        
        spot_30m = get_spot_trend(day_a, ts, 30)
        spot_60m = get_spot_trend(day_a, ts, 60)
        
        trades.append({
            'day': day,
            'time': ts.strftime('%H:%M'),
            'direction': direction,
            'strike': atm,
            'otype': otype,
            'entry': entry_p,
            'exit': exit_p,
            'pnl': pnl,
            'reason': reason,
            'won': reason == 'TARGET',
            'exit_time': exit_ts.strftime('%H:%M'),
            'duration': (exit_ts - ts).total_seconds() / 60,
            'max_p': max_p,
            'min_p': min_p,
            'verdict': verdict,
            'confidence': conf,
            'vix': a.get('vix', 0) or 0,
            'iv_skew': a.get('iv_skew', 0) or 0,
            'max_pain': a.get('max_pain', 0) or 0,
            'spot': spot,
            'call_oi_chg': a.get('call_oi_change', 0) or 0,
            'put_oi_chg': a.get('put_oi_change', 0) or 0,
            'futures_oi_chg': a.get('futures_oi_change', 0) or 0,
            'futures_basis': a.get('futures_basis', 0) or 0,
            'spot_30m': spot_30m,
            'spot_60m': spot_60m,
            'prev_verdict': a.get('prev_verdict', '') or '',
            'atm_vs_mp': atm - (a.get('max_pain', 0) or 0),
        })

# Print all trades
print("=" * 100)
print("IRON PULSE (Config B) — All Trades Forward Test")
print("=" * 100)
print(f"{'Day':<12} {'Result':<6} {'Dir':<10} {'Entry':>6} {'Time':<6} {'Exit':>6} {'P&L':>8} {'Dur':>6} {'Verdict':<22} {'Conf':>5} {'VIX':>5} {'IVSkew':>7} {'Spot30m':>8}")
print("-" * 100)

for t in trades:
    emoji = "WIN" if t['won'] else ("LOSS" if t['reason'] == 'SL' else "EOD")
    print(f"{t['day']:<12} {emoji:<6} {t['direction']:<10} {t['entry']:>6.1f} {t['time']:<6} {t['exit']:>6.1f} {t['pnl']:>+7.1f}% {t['duration']:>5.0f}m {t['verdict']:<22} {t['confidence']:>4.0f}% {t['vix']:>5.1f} {t['iv_skew']:>7.2f} {t['spot_30m']:>+7.3f}%")

wins = sum(1 for t in trades if t['won'])
losses = len(trades) - wins
total_pnl = sum(t['pnl'] for t in trades)
print(f"\nOverall: {wins}W/{losses}L ({wins/len(trades)*100:.1f}% WR), Total P&L: {total_pnl:+.1f}%")

# Analyze losses
print("\n" + "=" * 100)
print("LOSS ANALYSIS")
print("=" * 100)
loss_trades = [t for t in trades if not t['won']]
win_trades = [t for t in trades if t['won']]

for t in loss_trades:
    print(f"\n--- {t['day']} LOSS ---")
    print(f"  {t['direction']} {t['strike']} {t['otype']} @ Rs {t['entry']:.2f}")
    print(f"  Verdict: {t['verdict']} ({t['confidence']:.0f}%)")
    print(f"  VIX: {t['vix']:.1f} | IV Skew: {t['iv_skew']:.2f}")
    print(f"  Max Pain: {t['max_pain']:.0f} | ATM vs MP: {t['atm_vs_mp']:+.0f}")
    print(f"  Spot 30m: {t['spot_30m']:+.3f}% | Spot 60m: {t['spot_60m']:+.3f}%")
    print(f"  Call OI: {t['call_oi_chg']:,.0f} | Put OI: {t['put_oi_chg']:,.0f}")
    print(f"  Futures OI: {t['futures_oi_chg']:,.0f} | Basis: {t['futures_basis']:.2f}")
    print(f"  Peak: Rs {t['max_p']:.2f} ({(t['max_p']-t['entry'])/t['entry']*100:+.1f}%) | Low: Rs {t['min_p']:.2f} ({(t['min_p']-t['entry'])/t['entry']*100:+.1f}%)")
    print(f"  Duration to SL: {t['duration']:.0f} min")

# Feature comparison: losses vs wins
print("\n" + "=" * 100)
print("FEATURE COMPARISON: WINS vs LOSSES")
print("=" * 100)

features = ['confidence', 'vix', 'iv_skew', 'spot_30m', 'spot_60m', 'atm_vs_mp', 'futures_basis', 'duration']
for f in features:
    w_vals = [t[f] for t in win_trades if t[f] is not None]
    l_vals = [t[f] for t in loss_trades if t[f] is not None]
    if w_vals and l_vals:
        w_avg = sum(w_vals) / len(w_vals)
        l_avg = sum(l_vals) / len(l_vals)
        print(f"  {f:<16} Wins avg: {w_avg:>8.2f}  |  Losses avg: {l_avg:>8.2f}")

# Test potential filters
print("\n" + "=" * 100)
print("POTENTIAL FILTERS TO HIT 90%+")
print("=" * 100)

filters = [
    ("Conf >= 70", lambda t: t['confidence'] >= 70),
    ("Conf >= 75", lambda t: t['confidence'] >= 75),
    ("Conf >= 80", lambda t: t['confidence'] >= 80),
    ("VIX < 14", lambda t: t['vix'] < 14),
    ("VIX < 13", lambda t: t['vix'] < 13),
    ("IV Skew > -2", lambda t: t['iv_skew'] > -2),
    ("IV Skew > -1", lambda t: t['iv_skew'] > -1),
    ("Spot 30m aligned (not contra)", lambda t: (t['spot_30m'] < 0 and 'PUT' in t['direction']) or (t['spot_30m'] > 0 and 'CALL' in t['direction'])),
    ("ATM at or below MP", lambda t: t['atm_vs_mp'] <= 0),
    ("ATM above MP", lambda t: t['atm_vs_mp'] > 0),
    ("Futures basis > 40", lambda t: t['futures_basis'] > 40),
    ("Not Strongly Winning", lambda t: 'Strongly' not in t['verdict']),
    ("Only Slightly", lambda t: 'Slightly' in t['verdict']),
    ("Conf 65-80", lambda t: 65 <= t['confidence'] <= 80),
    ("Spot 30m < 0.1 (not surging)", lambda t: t['spot_30m'] < 0.1),
    ("Duration filter: skip if prev loss same day", lambda t: True),  # placeholder
    ("Put OI > Call OI", lambda t: t['put_oi_chg'] > t['call_oi_chg']),
    ("Call OI > Put OI", lambda t: t['call_oi_chg'] > t['put_oi_chg']),
]

for name, fn in filters:
    filtered = [t for t in trades if fn(t)]
    if not filtered:
        continue
    fw = sum(1 for t in filtered if t['won'])
    fl = len(filtered) - fw
    wr = fw / len(filtered) * 100
    pnl = sum(t['pnl'] for t in filtered)
    flag = " <-- 90%+" if wr >= 90 else ""
    print(f"  {name:<40} {fw}W/{fl}L ({wr:5.1f}% WR) P&L: {pnl:+7.1f}%  trades: {len(filtered)}{flag}")

# Test filter combos
print("\n--- Filter Combinations ---")
combos = [
    ("Conf >= 70 + VIX < 14", lambda t: t['confidence'] >= 70 and t['vix'] < 14),
    ("Conf >= 70 + IV Skew > -2", lambda t: t['confidence'] >= 70 and t['iv_skew'] > -2),
    ("Only Slightly + Conf >= 65", lambda t: 'Slightly' in t['verdict'] and t['confidence'] >= 65),
    ("Not Strongly + VIX < 14", lambda t: 'Strongly' not in t['verdict'] and t['vix'] < 14),
    ("Conf >= 70 + Not Strongly", lambda t: t['confidence'] >= 70 and 'Strongly' not in t['verdict']),
    ("Spot 30m aligned + Conf >= 65", lambda t: t['confidence'] >= 65 and ((t['spot_30m'] < 0 and 'PUT' in t['direction']) or (t['spot_30m'] > 0 and 'CALL' in t['direction']))),
    ("VIX < 14 + IV Skew > -2", lambda t: t['vix'] < 14 and t['iv_skew'] > -2),
    ("Conf 65-85 (exclude very high)", lambda t: 65 <= t['confidence'] <= 85),
]

for name, fn in combos:
    filtered = [t for t in trades if fn(t)]
    if not filtered:
        continue
    fw = sum(1 for t in filtered if t['won'])
    fl = len(filtered) - fw
    wr = fw / len(filtered) * 100
    pnl = sum(t['pnl'] for t in filtered)
    flag = " <-- 90%+" if wr >= 90 else ""
    print(f"  {name:<45} {fw}W/{fl}L ({wr:5.1f}% WR) P&L: {pnl:+7.1f}%  trades: {len(filtered)}{flag}")
