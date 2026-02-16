"""
Backtest: Take 2 selling trades on the SAME signal at different OTM levels.
First signal fires -> enter OTM-1 AND OTM-2 simultaneously.
Same quality signal, different strikes.

Also test: OTM-1 + OTM-3, and varying SL/Target for each leg.
"""

import sqlite3
import os
from datetime import datetime
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
NIFTY_STEP = 50
TIME_START = 11
TIME_END = 14
MIN_CONFIDENCE = 65
VALID_VERDICTS = ['Slightly Bullish', 'Slightly Bearish']
MIN_PREMIUM = 5


def get_analysis_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence FROM analysis_history ORDER BY timestamp")
    return [dict(r) for r in c.fetchall()]


def get_option_prices_for_day(date_str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp
        FROM oi_snapshots WHERE DATE(timestamp) = ? AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price""", (date_str,))
    return [dict(r) for r in c.fetchall()]


def get_otm_strike(spot, direction, offset):
    atm = round(spot / NIFTY_STEP) * NIFTY_STEP
    if direction == "SELL_PUT":
        return atm - (NIFTY_STEP * offset)
    else:
        return atm + (NIFTY_STEP * offset)


def get_premium_at_time(snapshots, target_time, strike, option_type):
    best = None
    best_diff = float('inf')
    for snap in snapshots:
        if snap['strike_price'] == strike:
            t = datetime.fromisoformat(snap['timestamp'])
            diff = abs((t - target_time).total_seconds())
            if diff < best_diff:
                price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
                if price and price > 0:
                    best = price
                    best_diff = diff
    return best


def simulate_sell(day_snapshots, entry_time, strike, option_type, entry_premium, sl_pct, target_pct):
    sl_premium = entry_premium * (1 + sl_pct / 100)
    target_premium = entry_premium * (1 - target_pct / 100)

    for snap in day_snapshots:
        snap_time = datetime.fromisoformat(snap['timestamp'])
        if snap_time <= entry_time or snap['strike_price'] != strike:
            continue
        price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
        if not price or price <= 0:
            continue

        if price >= sl_premium:
            pnl = ((entry_premium - price) / entry_premium) * 100
            return {'pnl_pct': pnl, 'exit_reason': 'SL', 'exit_premium': price,
                    'won': False, 'exit_time': snap_time}
        if price <= target_premium:
            pnl = ((entry_premium - price) / entry_premium) * 100
            return {'pnl_pct': pnl, 'exit_reason': 'TARGET', 'exit_premium': price,
                    'won': True, 'exit_time': snap_time}
        if snap_time.hour == 15 and snap_time.minute >= 20:
            pnl = ((entry_premium - price) / entry_premium) * 100
            return {'pnl_pct': pnl, 'exit_reason': 'EOD', 'exit_premium': price,
                    'won': pnl > 0, 'exit_time': snap_time}

    # Fallback EOD
    for snap in reversed(day_snapshots):
        if snap['strike_price'] == strike:
            price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
            if price and price > 0:
                pnl = ((entry_premium - price) / entry_premium) * 100
                return {'pnl_pct': pnl, 'exit_reason': 'EOD', 'exit_premium': price,
                        'won': pnl > 0, 'exit_time': datetime.fromisoformat(snap['timestamp'])}
    return None


def run_backtest():
    analysis = get_analysis_data()
    by_day = defaultdict(list)
    for a in analysis:
        by_day[a['timestamp'][:10]].append(a)

    # Test configs: (leg1_offset, leg1_sl, leg1_tgt, leg2_offset, leg2_sl, leg2_tgt)
    configs = [
        ("OTM-1 + OTM-2 (both 25/25)", 1, 25, 25, 2, 25, 25),
        ("OTM-1 + OTM-2 (25/25 + 20/20)", 1, 25, 25, 2, 20, 20),
        ("OTM-1 + OTM-3 (both 25/25)", 1, 25, 25, 3, 25, 25),
        ("OTM-1(25/25) + OTM-2(30/30)", 1, 25, 25, 2, 30, 30),
        ("OTM-2 + OTM-3 (both 25/25)", 2, 25, 25, 3, 25, 25),
    ]

    # Also test single-strike baseline for comparison
    all_results = {}

    for config_name, off1, sl1, tgt1, off2, sl2, tgt2 in configs:
        leg1_trades = []
        leg2_trades = []

        for day_str in sorted(by_day.keys()):
            if day_str == '2026-02-16':
                continue
            day_analysis = by_day[day_str]
            day_snaps = get_option_prices_for_day(day_str)
            if not day_snaps:
                continue

            # Find first valid signal
            for a in day_analysis:
                ts = datetime.fromisoformat(a['timestamp'])
                if ts.hour < TIME_START or ts.hour >= TIME_END:
                    continue
                if a['verdict'] not in VALID_VERDICTS:
                    continue
                if (a['signal_confidence'] or 0) < MIN_CONFIDENCE:
                    continue

                spot = a['spot_price']
                if 'Bullish' in a['verdict']:
                    direction = "SELL_PUT"
                    opt = "PE"
                else:
                    direction = "SELL_CALL"
                    opt = "CE"

                # Leg 1
                s1 = get_otm_strike(spot, direction, off1)
                p1 = get_premium_at_time(day_snaps, ts, s1, opt)

                # Leg 2
                s2 = get_otm_strike(spot, direction, off2)
                p2 = get_premium_at_time(day_snaps, ts, s2, opt)

                if p1 and p1 >= MIN_PREMIUM:
                    r1 = simulate_sell(day_snaps, ts, s1, opt, p1, sl1, tgt1)
                    if r1:
                        r1.update({'day': day_str, 'direction': direction, 'strike': s1,
                                   'option_type': opt, 'entry_premium': p1, 'verdict': a['verdict'],
                                   'confidence': a['signal_confidence'], 'spot': spot,
                                   'entry_time': ts, 'leg': 'Leg1'})
                        leg1_trades.append(r1)

                if p2 and p2 >= MIN_PREMIUM:
                    r2 = simulate_sell(day_snaps, ts, s2, opt, p2, sl2, tgt2)
                    if r2:
                        r2.update({'day': day_str, 'direction': direction, 'strike': s2,
                                   'option_type': opt, 'entry_premium': p2, 'verdict': a['verdict'],
                                   'confidence': a['signal_confidence'], 'spot': spot,
                                   'entry_time': ts, 'leg': 'Leg2'})
                        leg2_trades.append(r2)
                break  # One signal per day

        all_results[config_name] = (leg1_trades, leg2_trades)

    # Print results
    print("=" * 100)
    print("DUAL-STRIKE SELLING BACKTEST -- Same signal, two OTM levels")
    print("=" * 100)

    summary_rows = []

    for config_name, (l1, l2) in all_results.items():
        combined = l1 + l2

        print(f"\n{'=' * 90}")
        print(f"CONFIG: {config_name}")
        print(f"{'=' * 90}")

        for label, trades in [("Leg 1", l1), ("Leg 2", l2), ("Combined", combined)]:
            if not trades:
                print(f"\n  {label}: NO TRADES")
                continue
            wins = [t for t in trades if t['won']]
            losses = [t for t in trades if not t['won']]
            wr = len(wins) / len(trades) * 100
            total = sum(t['pnl_pct'] for t in trades)
            aw = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
            al = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
            pf = abs(sum(t['pnl_pct'] for t in wins)) / abs(sum(t['pnl_pct'] for t in losses)) if losses else float('inf')
            tgt = len([t for t in trades if t['exit_reason'] == 'TARGET'])
            sl = len([t for t in trades if t['exit_reason'] == 'SL'])
            eod = len([t for t in trades if t['exit_reason'] == 'EOD'])

            print(f"\n  {label}: {len(trades)} trades | {len(wins)}W-{len(losses)}L | WR: {wr:.1f}% | P&L: {total:+.1f}% | PF: {pf:.2f}")
            print(f"  Avg Win: {aw:+.1f}% | Avg Loss: {al:+.1f}% | Exits: T:{tgt} SL:{sl} EOD:{eod}")

            if label == "Combined":
                summary_rows.append((config_name, len(trades), len(wins), len(losses),
                                     wr, total, pf))

        # Per-day detail
        print(f"\n  {'Day':<12} {'Leg':<5} {'Dir':<10} {'Strike':<7} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Reason':<7}")
        print(f"  {'-'*75}")
        all_trades = sorted(l1 + l2, key=lambda t: (t['day'], t['leg']))
        for t in all_trades:
            print(f"  {t['day']:<12} {t['leg']:<5} {t['direction']:<10} {t['strike']:<7} "
                  f"Rs{t['entry_premium']:>5.1f} Rs{t['exit_premium']:>5.1f} {t['pnl_pct']:>+7.1f}% {t['exit_reason']:<7}")

    # Summary
    print(f"\n\n{'=' * 100}")
    print("SUMMARY COMPARISON")
    print(f"{'=' * 100}")
    print(f"{'Config':<40} {'Trades':>6} {'Wins':>5} {'WR%':>6} {'P&L':>9} {'PF':>6}")
    print("-" * 75)
    # Add single-trade baseline
    print(f"{'Baseline: OTM-1 only (25/25)':<40} {'12':>6} {'10':>5} {'83.3%':>6} {'+201.3%':>9} {'5.63':>6}")
    for name, trades, wins, losses, wr, pnl, pf in summary_rows:
        print(f"{name:<40} {trades:>6} {wins:>5} {wr:>5.1f}% {pnl:>+8.1f}% {pf:>6.2f}")


if __name__ == '__main__':
    run_backtest()
