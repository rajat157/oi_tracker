"""
Momentum Backtest V2:
1. Both sides: Momentum PUT (bearish) + Momentum CALL (bullish)
2. Trailing stop variants
3. Combined results
"""

import sqlite3
import json
import os
from datetime import datetime, time, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
NIFTY_STEP = 50


def get_analysis_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               futures_oi_change, futures_basis, vix, iv_skew, prev_verdict,
               analysis_json
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


def parse_analysis_json(analysis_json_str):
    if not analysis_json_str:
        return {}
    try:
        data = json.loads(analysis_json_str)
        return {
            'confirmation_status': data.get('confirmation_status', ''),
            'combined_score': data.get('combined_score', 0),
            'price_change_pct': data.get('price_change_pct', 0),
            'strength': data.get('strength', ''),
        }
    except (json.JSONDecodeError, TypeError):
        return {}


def simulate_trade(day_snapshots, entry_time, strike, option_type, entry_premium,
                   sl_pct, target_pct, trailing_config=None):
    """
    Simulate buying trade with optional trailing stop.
    trailing_config: None or dict with:
      - 'activate_pct': % gain to activate trailing (e.g. 20 means +20%)
      - 'trail_pct': % pullback from peak to exit (e.g. 10 means exit if drops 10% from peak)
    """
    sl_premium = entry_premium * (1 - sl_pct / 100)
    target_premium = entry_premium * (1 + target_pct / 100)

    exit_premium = None
    exit_reason = None
    exit_time = None
    max_prem = entry_premium
    min_prem = entry_premium
    trailing_active = False
    trailing_sl = None

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

        # Check trailing stop
        if trailing_config:
            gain_pct = ((price - entry_premium) / entry_premium) * 100
            if not trailing_active and gain_pct >= trailing_config['activate_pct']:
                trailing_active = True
            if trailing_active:
                trailing_sl = max_prem * (1 - trailing_config['trail_pct'] / 100)
                if price <= trailing_sl:
                    exit_premium = price
                    exit_reason = "TRAIL"
                    exit_time = snap_time
                    break

        # Check hard SL
        if price <= sl_premium:
            exit_premium = price
            exit_reason = "SL"
            exit_time = snap_time
            break

        # Check target
        if price >= target_premium:
            exit_premium = price
            exit_reason = "TARGET"
            exit_time = snap_time
            break

        # EOD
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
        'sl_premium': sl_premium,
        'target_premium': target_premium,
        'pnl_pct': pnl_pct,
        'exit_reason': exit_reason,
        'exit_time': exit_time,
        'won': pnl_pct > 0,
        'max_premium': max_prem,
        'min_premium': min_prem,
        'max_potential_pct': ((max_prem - entry_premium) / entry_premium) * 100,
    }


def run_momentum_backtest(analysis_by_day, snapshots_cache, config):
    trades = []
    side = config.get('side', 'bearish')  # 'bearish', 'bullish', 'both'

    for day_str in sorted(analysis_by_day.keys()):
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

            t_start = config.get('time_start', 12)
            t_end = config.get('time_end', 14)
            if ts.hour < t_start or ts.hour >= t_end:
                continue

            verdict = a['verdict']
            confidence = a['signal_confidence'] or 0
            if confidence < config.get('min_confidence', 85):
                continue

            aj = parse_analysis_json(a.get('analysis_json', ''))

            if config.get('require_confirmed', True):
                if aj.get('confirmation_status') != 'CONFIRMED':
                    continue

            # Determine direction
            is_bearish = False
            is_bullish = False
            min_verdict = config.get('min_verdict', 'Winning')

            if min_verdict == 'Strongly Winning':
                is_bearish = verdict == 'Bears Strongly Winning'
                is_bullish = verdict == 'Bulls Strongly Winning'
            elif min_verdict == 'Winning':
                is_bearish = verdict in ('Bears Winning', 'Bears Strongly Winning')
                is_bullish = verdict in ('Bulls Winning', 'Bulls Strongly Winning')
            elif min_verdict == 'Slightly':
                is_bearish = 'Bear' in verdict
                is_bullish = 'Bull' in verdict

            if side == 'bearish' and not is_bearish:
                continue
            elif side == 'bullish' and not is_bullish:
                continue
            elif side == 'both' and not (is_bearish or is_bullish):
                continue

            spot = a['spot_price']
            atm = round(spot / NIFTY_STEP) * NIFTY_STEP

            if is_bearish:
                direction = "BUY_PUT"
                option_type = "PE"
                strike_mode = config.get('strike', 'ATM')
                if strike_mode == 'ATM':
                    strike = atm
                elif strike_mode == 'ITM1':
                    strike = atm + NIFTY_STEP
                elif strike_mode == 'OTM1':
                    strike = atm - NIFTY_STEP
                else:
                    strike = atm
            else:
                direction = "BUY_CALL"
                option_type = "CE"
                strike_mode = config.get('strike', 'ATM')
                if strike_mode == 'ATM':
                    strike = atm
                elif strike_mode == 'ITM1':
                    strike = atm - NIFTY_STEP
                elif strike_mode == 'OTM1':
                    strike = atm + NIFTY_STEP
                else:
                    strike = atm

            entry_premium = get_premium_at_time(day_snapshots, ts, strike, option_type)
            if not entry_premium or entry_premium < 5:
                continue

            trailing_config = config.get('trailing', None)

            result = simulate_trade(
                day_snapshots, ts, strike, option_type, entry_premium,
                config['sl_pct'], config['target_pct'], trailing_config
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
                result['vix'] = a.get('vix', 0) or 0
                result['iv_skew'] = a.get('iv_skew', 0) or 0
                result['combined_score'] = aj.get('combined_score', 0)
                result['confirmation'] = aj.get('confirmation_status', '?')
                trades.append(result)
                traded = True

    return trades


def summarize(trades, name=""):
    if not trades:
        return {'name': name, 'trades': 0, 'wins': 0, 'losses': 0, 'wr': 0,
                'pnl': 0, 'pf': 0, 'avg_win': 0, 'avg_loss': 0,
                'targets': 0, 'sls': 0, 'eods': 0, 'trails': 0,
                'expectancy': 0, 'avg_max_potential': 0}
    wins = [t for t in trades if t['won']]
    losses = [t for t in trades if not t['won']]
    wr = len(wins) / len(trades) * 100
    total_pnl = sum(t['pnl_pct'] for t in trades)
    avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
    pf = abs(sum(t['pnl_pct'] for t in wins)) / abs(sum(t['pnl_pct'] for t in losses)) if losses and sum(t['pnl_pct'] for t in losses) != 0 else float('inf')
    expectancy = (wr / 100 * avg_win) + ((1 - wr / 100) * avg_loss)
    avg_max = sum(t.get('max_potential_pct', 0) for t in trades) / len(trades)
    return {
        'name': name, 'trades': len(trades), 'wins': len(wins), 'losses': len(losses),
        'wr': wr, 'pnl': total_pnl, 'pf': pf, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'targets': len([t for t in trades if t['exit_reason'] == 'TARGET']),
        'sls': len([t for t in trades if t['exit_reason'] == 'SL']),
        'eods': len([t for t in trades if t['exit_reason'] == 'EOD']),
        'trails': len([t for t in trades if t['exit_reason'] == 'TRAIL']),
        'expectancy': expectancy, 'avg_max_potential': avg_max,
    }


def print_summary_table(results):
    print(f"\n{'Config':<60} {'#':>3} {'W':>3} {'L':>3} {'WR%':>6} {'P&L':>9} {'AvgW':>7} {'AvgL':>7} {'PF':>6} {'Exp':>7} {'MaxPot':>7} {'T/S/E/Tr':>9}")
    print("-" * 140)
    for r in sorted(results, key=lambda x: -x['pnl']):
        if r['trades'] == 0:
            continue
        tse = f"{r['targets']}/{r['sls']}/{r['eods']}/{r['trails']}"
        print(f"{r['name']:<60} {r['trades']:>3} {r['wins']:>3} {r['losses']:>3} {r['wr']:>5.1f}% {r['pnl']:>+8.1f}% {r['avg_win']:>+6.1f}% {r['avg_loss']:>+6.1f}% {r['pf']:>6.2f} {r['expectancy']:>+6.1f}% {r['avg_max_potential']:>+6.1f}% {tse:>9}")


def print_trades(trades, name=""):
    if not trades:
        return
    print(f"\n--- {name} ---")
    print(f"{'Day':<12} {'Time':<6} {'Dir':<10} {'Strike':<7} {'Spot':<9} {'Verdict':<25} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Why':<6} {'MaxP':>7} {'MaxPot':>7}")
    for t in trades:
        time_str = t['entry_time'].strftime('%H:%M') if isinstance(t['entry_time'], datetime) else str(t['entry_time'])[11:16]
        print(f"{t['day']:<12} {time_str:<6} {t['direction']:<10} {t['strike']:<7} {t['spot']:<9.1f} {t['verdict']:<25} {t['entry_premium']:>7.2f} {t['exit_premium']:>7.2f} {t['pnl_pct']:>+7.1f}% {t['exit_reason']:<6} {t['max_premium']:>7.2f} {t.get('max_potential_pct',0):>+6.1f}%")


if __name__ == '__main__':
    print("=" * 140)
    print("MOMENTUM BACKTEST V2 — Bullish + Bearish + Trailing Stops")
    print("=" * 140)

    print("\nLoading analysis data...")
    analysis = get_analysis_data()
    by_day = defaultdict(list)
    for a in analysis:
        by_day[a['timestamp'][:10]].append(a)

    snapshots_cache = {}
    trading_days = sorted(by_day.keys())
    print(f"Trading days: {len(trading_days)} ({trading_days[0]} to {trading_days[-1]})")

    # Count days by direction
    bull_days = bear_days = 0
    for day_str, day_data in by_day.items():
        has_bull = has_bear = False
        for a in day_data:
            ts = datetime.fromisoformat(a['timestamp'])
            if 12 <= ts.hour < 14:
                aj = parse_analysis_json(a.get('analysis_json', ''))
                if aj.get('confirmation_status') == 'CONFIRMED':
                    if 'Bull' in a['verdict'] and 'Winning' in a['verdict']:
                        has_bull = True
                    if 'Bear' in a['verdict'] and 'Winning' in a['verdict']:
                        has_bear = True
        if has_bull:
            bull_days += 1
        if has_bear:
            bear_days += 1
    print(f"Bullish CONFIRMED days (12-14): {bull_days}")
    print(f"Bearish CONFIRMED days (12-14): {bear_days}")

    all_results = []

    # ============================================================
    # SECTION 1: PUT side (bearish) — baseline from V1
    # ============================================================
    print("\n" + "=" * 140)
    print("SECTION 1: PUT SIDE (bearish momentum) — Best config from V1")
    print("=" * 140)

    sec1 = []
    base_bear = {"side": "bearish", "min_verdict": "Winning", "require_confirmed": True,
                 "sl_pct": 25, "target_pct": 50, "time_start": 12, "time_end": 14}
    trades = run_momentum_backtest(by_day, snapshots_cache, {**base_bear, "name": "PUT 25/50 CONF 12-14 (baseline)"})
    s = summarize(trades, "PUT 25/50 CONF 12-14 (baseline)")
    sec1.append(s)
    all_results.append((s, trades))
    print_summary_table(sec1)
    print_trades(trades, s['name'])

    # ============================================================
    # SECTION 2: CALL side (bullish) — mirror
    # ============================================================
    print("\n" + "=" * 140)
    print("SECTION 2: CALL SIDE (bullish momentum)")
    print("=" * 140)

    sec2 = []
    for sl, tgt in [(20, 40), (25, 50), (30, 60)]:
        for t_start, t_end, label in [(11, 14, "11-14"), (12, 14, "12-14"), (11, 13, "11-13")]:
            cfg = {
                "name": f"CALL {sl}/{tgt} CONF {label}",
                "side": "bullish", "min_verdict": "Winning",
                "require_confirmed": True,
                "sl_pct": sl, "target_pct": tgt,
                "time_start": t_start, "time_end": t_end,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            s = summarize(trades, cfg['name'])
            sec2.append(s)
            all_results.append((s, trades))
    print_summary_table(sec2)

    # Print detailed trades for best CALL config
    best_call = max(sec2, key=lambda x: x['pnl'] if x['trades'] > 0 else -999)
    for s, trades in all_results:
        if s['name'] == best_call['name']:
            print_trades(trades, f"{s['name']} (BEST CALL)")
            break

    # ============================================================
    # SECTION 3: BOTH sides combined
    # ============================================================
    print("\n" + "=" * 140)
    print("SECTION 3: BOTH SIDES combined (PUT + CALL)")
    print("=" * 140)

    sec3 = []
    for sl, tgt in [(20, 40), (25, 50), (30, 60)]:
        for t_start, t_end, label in [(11, 14, "11-14"), (12, 14, "12-14"), (11, 13, "11-13")]:
            cfg = {
                "name": f"BOTH {sl}/{tgt} CONF {label}",
                "side": "both", "min_verdict": "Winning",
                "require_confirmed": True,
                "sl_pct": sl, "target_pct": tgt,
                "time_start": t_start, "time_end": t_end,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            s = summarize(trades, cfg['name'])
            sec3.append(s)
            all_results.append((s, trades))
    print_summary_table(sec3)

    # Print detailed for best BOTH
    best_both = max(sec3, key=lambda x: x['pnl'] if x['trades'] > 0 else -999)
    for s, trades in all_results:
        if s['name'] == best_both['name']:
            print_trades(trades, f"{s['name']} (BEST BOTH)")
            break

    # ============================================================
    # SECTION 4: TRAILING STOP variants (PUT side, best baseline)
    # ============================================================
    print("\n" + "=" * 140)
    print("SECTION 4: TRAILING STOP variants (PUT side)")
    print("=" * 140)

    sec4 = []
    # No trailing (baseline for comparison)
    cfg = {**base_bear, "name": "PUT 25/50 NO TRAIL (baseline)"}
    trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
    s = summarize(trades, cfg['name'])
    sec4.append(s)
    all_results.append((s, trades))

    # Various trailing configs
    for activate in [15, 20, 25, 30]:
        for trail in [8, 10, 12, 15]:
            cfg = {
                **base_bear,
                "name": f"PUT 25/50 TRAIL act{activate}% tr{trail}%",
                "trailing": {"activate_pct": activate, "trail_pct": trail},
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            s = summarize(trades, cfg['name'])
            sec4.append(s)
            all_results.append((s, trades))
    print_summary_table(sec4)

    # Print detailed for top trailing configs
    top_trail = sorted([s for s in sec4 if 'TRAIL' in s['name'] and s['trades'] > 0], key=lambda x: -x['pnl'])[:3]
    for ts in top_trail:
        for s, trades in all_results:
            if s['name'] == ts['name']:
                print_trades(trades, f"{s['name']}")
                break

    # ============================================================
    # SECTION 5: TRAILING STOP on BOTH sides
    # ============================================================
    print("\n" + "=" * 140)
    print("SECTION 5: TRAILING STOP on BOTH sides")
    print("=" * 140)

    sec5 = []
    # Best trailing configs from sec4 applied to both sides
    for activate, trail in [(15, 10), (20, 10), (20, 12), (25, 10), (25, 12), (30, 12), (30, 15)]:
        for sl, tgt in [(25, 50), (30, 60)]:
            cfg = {
                "name": f"BOTH {sl}/{tgt} CONF 12-14 trail({activate}/{trail})",
                "side": "both", "min_verdict": "Winning",
                "require_confirmed": True,
                "sl_pct": sl, "target_pct": tgt,
                "time_start": 12, "time_end": 14,
                "trailing": {"activate_pct": activate, "trail_pct": trail},
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            s = summarize(trades, cfg['name'])
            sec5.append(s)
            all_results.append((s, trades))

    # Also no-trail BOTH baseline
    cfg = {
        "name": "BOTH 25/50 CONF 12-14 NO TRAIL",
        "side": "both", "min_verdict": "Winning",
        "require_confirmed": True,
        "sl_pct": 25, "target_pct": 50,
        "time_start": 12, "time_end": 14,
    }
    trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
    s = summarize(trades, cfg['name'])
    sec5.append(s)
    all_results.append((s, trades))

    print_summary_table(sec5)

    # Print top 3 from sec5
    top5 = sorted([s for s in sec5 if s['trades'] > 0], key=lambda x: -x['pnl'])[:3]
    for ts in top5:
        for s, trades in all_results:
            if s['name'] == ts['name']:
                print_trades(trades, f"{s['name']}")
                break

    # ============================================================
    # SECTION 6: FINAL RECOMMENDATION
    # ============================================================
    print("\n" + "=" * 140)
    print("SECTION 6: FINAL COMPARISON — Top configs across all sections")
    print("=" * 140)

    # Collect all with trades >= 3
    final = []
    seen = set()
    for s, trades in sorted(all_results, key=lambda x: -x[0]['pnl']):
        if s['trades'] >= 3 and s['name'] not in seen:
            final.append(s)
            seen.add(s['name'])
    print_summary_table(final[:20])

    # Print the absolute best config details
    if final:
        best = final[0]
        for s, trades in all_results:
            if s['name'] == best['name']:
                print_trades(trades, f"*** BEST OVERALL: {s['name']} ***")
                break

    print("\n\nDone!")
