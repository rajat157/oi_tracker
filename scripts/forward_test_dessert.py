"""
Forward Test: Replay dessert strategies on historical data.
Shows trade-by-trade lifecycle — entry, premium movement, time to exit.
One trade per day (first to trigger), exactly as the live system would fire.
"""
import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
NIFTY_STEP = 50
SL_PCT = 25
TARGET_PCT = 50

def get_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               vix, iv_skew, max_pain
        FROM analysis_history WHERE DATE(timestamp) >= '2026-02-01' ORDER BY timestamp
    """)
    analysis = [dict(r) for r in c.fetchall()]
    c.execute("""
        SELECT timestamp, spot_price, strike_price, pe_ltp
        FROM oi_snapshots
        WHERE DATE(timestamp) >= '2026-02-01' AND pe_ltp > 0
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

def get_spot_move_30m(day_analysis, ts):
    """Calculate spot movement over prior 30 minutes."""
    prices = []
    for a in day_analysis:
        a_ts = datetime.fromisoformat(a['timestamp'])
        diff = (ts - a_ts).total_seconds()
        if 0 <= diff <= 1800:  # 30 min
            prices.append(a['spot_price'])
    if len(prices) < 2:
        return None
    return (prices[-1] - prices[0]) / prices[0] * 100

def check_contra_sniper(a, atm, max_pain):
    """BUY_PUT + Bullish verdict + IV skew < 1 + below max pain."""
    verdict = a.get('verdict', '') or ''
    iv_skew = a.get('iv_skew', 0) or 0
    if 'Bull' not in verdict:
        return False
    if iv_skew >= 1:
        return False
    if not max_pain or max_pain <= 0 or atm >= max_pain:
        return False
    return True

def check_phantom_put(a, spot_move):
    """BUY_PUT + conf < 50% + IV skew < 0 + spot rising."""
    conf = a.get('signal_confidence', 0) or 0
    iv_skew = a.get('iv_skew', 0) or 0
    if conf >= 50:
        return False
    if iv_skew >= 0:
        return False
    if spot_move is None or spot_move <= 0.05:
        return False
    return True

def get_premium_at(day_snaps, strike, ts):
    """Get PE premium closest to timestamp for given strike."""
    best = None
    best_diff = float('inf')
    for snap in day_snaps:
        if snap['strike_price'] != strike:
            continue
        snap_ts = datetime.fromisoformat(snap['timestamp'])
        diff = abs((snap_ts - ts).total_seconds())
        if diff < best_diff:
            best_diff = diff
            best = snap['pe_ltp']
    return best if best and best > 0 else None

def track_trade(day_snaps, strike, entry_ts, entry_premium):
    """Track a trade tick-by-tick from entry to exit."""
    sl_price = entry_premium * (1 - SL_PCT / 100)
    target_price = entry_premium * (1 + TARGET_PCT / 100)
    
    ticks = []
    max_prem = entry_premium
    min_prem = entry_premium
    exit_info = None
    
    for snap in day_snaps:
        if snap['strike_price'] != strike:
            continue
        snap_ts = datetime.fromisoformat(snap['timestamp'])
        if snap_ts <= entry_ts:
            continue
        
        price = snap['pe_ltp']
        if not price or price <= 0:
            continue
        
        max_prem = max(max_prem, price)
        min_prem = min(min_prem, price)
        pnl = (price - entry_premium) / entry_premium * 100
        elapsed = (snap_ts - entry_ts).total_seconds() / 60  # minutes
        
        ticks.append({
            'time': snap_ts.strftime('%H:%M'),
            'premium': price,
            'pnl': pnl,
            'elapsed_min': elapsed
        })
        
        # Check SL
        if price <= sl_price and not exit_info:
            exit_info = {
                'reason': 'SL',
                'exit_time': snap_ts,
                'exit_premium': price,
                'pnl': pnl,
                'elapsed_min': elapsed
            }
            break
        
        # Check Target
        if price >= target_price and not exit_info:
            exit_info = {
                'reason': 'TARGET',
                'exit_time': snap_ts,
                'exit_premium': price,
                'pnl': pnl,
                'elapsed_min': elapsed
            }
            break
        
        # EOD
        if snap_ts.hour == 15 and snap_ts.minute >= 20 and not exit_info:
            exit_info = {
                'reason': 'EOD',
                'exit_time': snap_ts,
                'exit_premium': price,
                'pnl': pnl,
                'elapsed_min': elapsed
            }
            break
    
    return ticks, exit_info, max_prem, min_prem


def run_forward_test():
    analysis, snapshots = get_data()
    analysis_by_day = group_by_day(analysis)
    snaps_by_day = group_by_day(snapshots)
    
    days = sorted(analysis_by_day.keys())
    
    print("=" * 90)
    print("🍰 DESSERT FORWARD TEST — Trade-by-Trade Replay")
    print("=" * 90)
    print(f"Strategies: 🎯 Contra Sniper | 🔮 Phantom PUT")
    print(f"Risk: SL {SL_PCT}% / Target {TARGET_PCT}% (1:2 RR)")
    print(f"Window: 9:30–14:00 | One trade/day (first to trigger)")
    print(f"Data: {days[0]} to {days[-1]} ({len(days)} days)")
    print("=" * 90)
    
    trades = []
    total_pnl = 0
    wins = 0
    losses = 0
    
    for day in days:
        day_a = analysis_by_day.get(day, [])
        day_s = snaps_by_day.get(day, [])
        traded = False
        
        for a in day_a:
            if traded:
                break
            
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30) or ts.hour >= 14:
                continue
            
            spot = a['spot_price']
            atm = round(spot / NIFTY_STEP) * NIFTY_STEP
            max_pain = a.get('max_pain', 0) or 0
            spot_move = get_spot_move_30m(day_a, ts)
            
            strategy = None
            
            # Check Contra Sniper first (higher priority)
            if check_contra_sniper(a, atm, max_pain):
                strategy = "Contra Sniper"
                emoji = "🎯"
            elif check_phantom_put(a, spot_move):
                strategy = "Phantom PUT"
                emoji = "🔮"
            
            if not strategy:
                continue
            
            # Get entry premium
            entry_prem = get_premium_at(day_s, atm, ts)
            if not entry_prem or entry_prem < 5:
                continue
            
            # Track the trade
            ticks, exit_info, max_p, min_p = track_trade(day_s, atm, ts, entry_prem)
            
            if not exit_info:
                continue  # No data after entry
            
            traded = True
            pnl = exit_info['pnl']
            won = pnl > 0
            total_pnl += pnl
            if won:
                wins += 1
            else:
                losses += 1
            
            result_emoji = "✅" if won else "❌"
            
            print(f"\n{'─' * 90}")
            print(f"{result_emoji} {emoji} {strategy} — {day} ({ts.strftime('%H:%M')})")
            print(f"{'─' * 90}")
            print(f"  Strike: {atm} PE | Entry: Rs {entry_prem:.2f}")
            print(f"  SL: Rs {entry_prem * (1 - SL_PCT/100):.2f} | Target: Rs {entry_prem * (1 + TARGET_PCT/100):.2f}")
            print(f"  Verdict: {a['verdict']} ({a.get('signal_confidence', 0):.0f}%)")
            print(f"  VIX: {a.get('vix', 0):.1f} | IV Skew: {a.get('iv_skew', 0):.2f} | Max Pain: {max_pain:.0f}")
            if spot_move is not None:
                print(f"  Spot 30m: {spot_move:+.3f}%")
            
            print(f"\n  📈 Trade Timeline:")
            # Show key ticks (entry, ~25%, ~50%, peak, trough, exit)
            if ticks:
                # Sample: show every ~10 ticks or key moments
                step = max(1, len(ticks) // 8)
                shown = set()
                key_indices = list(range(0, len(ticks), step)) + [len(ticks) - 1]
                for i in sorted(set(key_indices)):
                    t = ticks[i]
                    bar_len = int(abs(t['pnl']) / 2)
                    bar = ('█' * bar_len) if t['pnl'] >= 0 else ('░' * bar_len)
                    direction = '+' if t['pnl'] >= 0 else ''
                    print(f"    {t['time']}  Rs {t['premium']:7.2f}  {direction}{t['pnl']:6.1f}%  {bar}")
            
            print(f"\n  📊 Result: {exit_info['reason']} at {exit_info['exit_time'].strftime('%H:%M')}")
            print(f"     Exit: Rs {exit_info['exit_premium']:.2f} | P&L: {pnl:+.1f}%")
            print(f"     Duration: {exit_info['elapsed_min']:.0f} min")
            print(f"     Peak: Rs {max_p:.2f} ({(max_p - entry_prem)/entry_prem*100:+.1f}%) | Low: Rs {min_p:.2f} ({(min_p - entry_prem)/entry_prem*100:+.1f}%)")
            
            trades.append({
                'day': day, 'strategy': strategy, 'entry_time': ts.strftime('%H:%M'),
                'strike': atm, 'entry': entry_prem, 'exit': exit_info['exit_premium'],
                'pnl': pnl, 'reason': exit_info['reason'], 'duration': exit_info['elapsed_min'],
                'won': won, 'max': max_p, 'min': min_p
            })
        
        if not traded:
            pass  # No dessert signal this day
    
    # Summary
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    avg_win = sum(t['pnl'] for t in trades if t['won']) / wins if wins > 0 else 0
    avg_loss = sum(t['pnl'] for t in trades if not t['won']) / losses if losses > 0 else 0
    avg_dur = sum(t['duration'] for t in trades) / len(trades) if trades else 0
    avg_win_dur = sum(t['duration'] for t in trades if t['won']) / wins if wins > 0 else 0
    avg_loss_dur = sum(t['duration'] for t in trades if not t['won']) / losses if losses > 0 else 0
    
    print(f"\n{'=' * 90}")
    print(f"📊 FORWARD TEST SUMMARY")
    print(f"{'=' * 90}")
    print(f"  Total Trades: {total} / {len(days)} days ({len(days) - total} days no signal)")
    print(f"  Win Rate: {wr:.1f}% ({wins}W / {losses}L)")
    print(f"  Total P&L: {total_pnl:+.1f}%")
    print(f"  Avg Win: {avg_win:+.1f}% | Avg Loss: {avg_loss:+.1f}%")
    if wins > 0 and losses > 0:
        pf = abs(sum(t['pnl'] for t in trades if t['won'])) / abs(sum(t['pnl'] for t in trades if not t['won']))
        print(f"  Profit Factor: {pf:.2f}")
    print(f"\n  ⏱  Avg Duration: {avg_dur:.0f} min")
    print(f"     Avg Win Duration: {avg_win_dur:.0f} min")
    print(f"     Avg Loss Duration: {avg_loss_dur:.0f} min")
    
    # Per-strategy breakdown
    for strat in ["Contra Sniper", "Phantom PUT"]:
        st = [t for t in trades if t['strategy'] == strat]
        if not st:
            continue
        sw = sum(1 for t in st if t['won'])
        sl = len(st) - sw
        sp = sum(t['pnl'] for t in st)
        emoji = "🎯" if strat == "Contra Sniper" else "🔮"
        print(f"\n  {emoji} {strat}: {sw}W/{sl}L ({sw/(sw+sl)*100:.0f}% WR), P&L: {sp:+.1f}%")
    
    print(f"\n{'=' * 90}")
    print(f"  Trade Log:")
    print(f"  {'Day':<12} {'Strategy':<16} {'Entry':>6} {'Strike':>6} {'Premium':>8} → {'Exit':>8} {'P&L':>8} {'Dur':>6} {'Result':<8}")
    print(f"  {'─'*84}")
    for t in trades:
        emoji = "🎯" if t['strategy'] == "Contra Sniper" else "🔮"
        result = "✅ WIN" if t['won'] else "❌ LOSS"
        print(f"  {t['day']:<12} {emoji} {t['strategy']:<13} {t['entry_time']:>6} {t['strike']:>6} Rs {t['entry']:>6.2f} → Rs {t['exit']:>6.2f} {t['pnl']:>+7.1f}% {t['duration']:>4.0f}m  {result}")
    print(f"{'=' * 90}")


if __name__ == '__main__':
    run_forward_test()
