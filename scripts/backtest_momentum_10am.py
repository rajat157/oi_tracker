"""Quick backtest: Momentum strategy with 10:00 AM start windows."""

import sqlite3
import json
import os
from datetime import datetime
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
NIFTY_STEP = 50

def get_analysis_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               vix, iv_skew, prev_verdict, analysis_json
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

def parse_analysis_json(s):
    if not s:
        return {}
    try:
        d = json.loads(s)
        return {'confirmation_status': d.get('confirmation_status', ''), 'combined_score': d.get('combined_score', 0)}
    except:
        return {}

def simulate_trade(day_snapshots, entry_time, strike, option_type, entry_premium, sl_pct, target_pct):
    sl_premium = entry_premium * (1 - sl_pct / 100)
    target_premium = entry_premium * (1 + target_pct / 100)
    exit_premium = None
    exit_reason = None
    max_prem = entry_premium

    for snap in day_snapshots:
        snap_time = datetime.fromisoformat(snap['timestamp'])
        if snap_time <= entry_time or snap['strike_price'] != strike:
            continue
        price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
        if not price or price <= 0:
            continue
        max_prem = max(max_prem, price)
        if price <= sl_premium:
            return price, "SL", max_prem
        if price >= target_premium:
            return price, "TARGET", max_prem
        if snap_time.hour == 15 and snap_time.minute >= 20:
            return price, "EOD", max_prem
        exit_premium = price

    if exit_premium:
        return exit_premium, "EOD", max_prem
    return None, None, max_prem

def run_backtest(by_day, snapshots_cache, config):
    trades = []
    for day_str in sorted(by_day.keys()):
        day_analysis = by_day[day_str]
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
            if ts.hour < config['t_start'] or ts.hour >= config['t_end']:
                continue

            verdict = a['verdict']
            is_bearish = verdict in ('Bears Winning', 'Bears Strongly Winning')
            is_bullish = verdict in ('Bulls Winning', 'Bulls Strongly Winning')
            if not (is_bearish or is_bullish):
                continue

            confidence = a['signal_confidence'] or 0
            if confidence < config.get('min_conf', 85):
                continue

            aj = parse_analysis_json(a.get('analysis_json', ''))
            if config.get('require_confirmed', True) and aj.get('confirmation_status') != 'CONFIRMED':
                continue

            spot = a['spot_price']
            atm = round(spot / NIFTY_STEP) * NIFTY_STEP
            option_type = 'PE' if is_bearish else 'CE'
            direction = 'BUY_PUT' if is_bearish else 'BUY_CALL'
            strike = atm

            entry_premium = get_premium_at_time(day_snapshots, ts, strike, option_type)
            if not entry_premium or entry_premium < 5:
                continue

            exit_prem, reason, max_prem = simulate_trade(
                day_snapshots, ts, strike, option_type, entry_premium,
                config['sl_pct'], config['target_pct']
            )
            if exit_prem is None:
                continue

            pnl = ((exit_prem - entry_premium) / entry_premium) * 100
            max_pot = ((max_prem - entry_premium) / entry_premium) * 100
            trades.append({
                'day': day_str, 'time': ts.strftime('%H:%M'), 'dir': direction,
                'strike': strike, 'spot': spot, 'verdict': verdict,
                'conf': confidence, 'entry': entry_premium, 'exit': exit_prem,
                'pnl': pnl, 'reason': reason, 'max_prem': max_prem, 'max_pot': max_pot,
                'won': pnl > 0,
            })
            traded = True
    return trades

def print_results(trades, name):
    if not trades:
        print(f"\n{name}: NO TRADES")
        return
    wins = [t for t in trades if t['won']]
    losses = [t for t in trades if not t['won']]
    wr = len(wins)/len(trades)*100
    total_pnl = sum(t['pnl'] for t in trades)
    avg_win = sum(t['pnl'] for t in wins)/len(wins) if wins else 0
    avg_loss = sum(t['pnl'] for t in losses)/len(losses) if losses else 0
    pf = abs(sum(t['pnl'] for t in wins))/abs(sum(t['pnl'] for t in losses)) if losses and sum(t['pnl'] for t in losses) != 0 else float('inf')

    print(f"\n{'='*120}")
    print(f"{name}")
    print(f"Trades: {len(trades)} | W: {len(wins)} L: {len(losses)} | WR: {wr:.1f}% | P&L: {total_pnl:+.1f}% | AvgW: {avg_win:+.1f}% | AvgL: {avg_loss:+.1f}% | PF: {pf:.2f}")
    print(f"{'='*120}")
    print(f"{'Day':<12} {'Time':<6} {'Dir':<10} {'Strike':<7} {'Spot':<9} {'Verdict':<25} {'Cnf':>4} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Why':<7} {'MaxPot':>7}")
    print("-"*120)
    for t in trades:
        print(f"{t['day']:<12} {t['time']:<6} {t['dir']:<10} {t['strike']:<7} {t['spot']:<9.1f} {t['verdict']:<25} {t['conf']:>4.0f} {t['entry']:>7.2f} {t['exit']:>7.2f} {t['pnl']:>+7.1f}% {t['reason']:<7} {t['max_pot']:>+6.1f}%")

if __name__ == '__main__':
    print("MOMENTUM BACKTEST — 10 AM Window Analysis\n")
    analysis = get_analysis_data()
    by_day = defaultdict(list)
    for a in analysis:
        by_day[a['timestamp'][:10]].append(a)
    snapshots_cache = {}
    print(f"Trading days: {len(by_day)}")

    # First: show what CONFIRMED signals exist in 10:00-11:00 window
    print("\n" + "="*120)
    print("CONFIRMED SIGNALS IN 10:00-11:00 WINDOW (what's available?)")
    print("="*120)
    for day_str in sorted(by_day.keys()):
        for a in by_day[day_str]:
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour != 10:
                continue
            verdict = a['verdict']
            if 'Winning' not in verdict:
                continue
            aj = parse_analysis_json(a.get('analysis_json', ''))
            if aj.get('confirmation_status') == 'CONFIRMED':
                print(f"  {day_str} {ts.strftime('%H:%M')} | {verdict:<25} | conf={a['signal_confidence']:.0f} | score={aj.get('combined_score',0):+.1f}")
                break  # first per day

    # Test various windows
    windows = [
        ("10:00-14:00", 10, 14),
        ("10:00-12:00", 10, 12),
        ("10:00-11:00", 10, 11),
        ("10:30-14:00 (approx)", 10, 14),  # we'll handle 10:30 separately
        ("11:00-14:00", 11, 14),
        ("12:00-14:00", 12, 14),
    ]

    for sl, tgt in [(25, 50)]:
        for label, t_start, t_end in windows:
            cfg = {'t_start': t_start, 't_end': t_end, 'sl_pct': sl, 'target_pct': tgt, 'require_confirmed': True}
            trades = run_backtest(by_day, snapshots_cache, cfg)
            print_results(trades, f"BOTH {sl}/{tgt} CONF {label}")

    # Without CONFIRMED (just verdict + confidence)
    print("\n\n" + "#"*120)
    print("WITHOUT CONFIRMED FILTER (just Winning verdict + conf>=85)")
    print("#"*120)
    for label, t_start, t_end in [("10:00-14:00", 10, 14), ("10:00-12:00", 10, 12), ("12:00-14:00", 12, 14)]:
        cfg = {'t_start': t_start, 't_end': t_end, 'sl_pct': 25, 'target_pct': 50, 'require_confirmed': False}
        trades = run_backtest(by_day, snapshots_cache, cfg)
        print_results(trades, f"BOTH 25/50 NO-CONF {label}")

    print("\n\nDone!")
