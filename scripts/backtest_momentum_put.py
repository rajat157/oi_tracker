"""
Backtest: Momentum PUT — Trend-following 1:2 RR buying on high-conviction bearish days.

The idea: When OI, verdict, and price all agree (bearish + confirmed + falling),
buy PUT for 1:2 RR. This fills the gap where Iron Pulse only captures 1:1 and
dessert strategies are contrarian-only.

Tests multiple parameter combinations across:
- Verdict strength (Bears Winning vs Bears Strongly Winning)
- Confirmation status (CONFIRMED required or not)
- Combined score thresholds
- SL/Target percentages
- Strike selection (ATM, ITM1, OTM1)
- Time windows
- Additional filters (IV skew, VIX, spot momentum)
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
    """Extract key fields from analysis_json."""
    if not analysis_json_str:
        return {}
    try:
        data = json.loads(analysis_json_str)
        return {
            'confirmation_status': data.get('confirmation_status', ''),
            'confirmation_message': data.get('confirmation_message', ''),
            'combined_score': data.get('combined_score', 0),
            'price_change_pct': data.get('price_change_pct', 0),
            'strength': data.get('strength', ''),
            'pcr': data.get('pcr', 0),
            'momentum_score': data.get('momentum_score', 0),
            'premium_momentum_score': data.get('premium_momentum', {}).get('premium_momentum_score', 0) if isinstance(data.get('premium_momentum'), dict) else 0,
            'oi_phase': data.get('oi_acceleration', {}).get('phase', '') if isinstance(data.get('oi_acceleration'), dict) else '',
            'trade_setup': data.get('trade_setup', {}),
        }
    except (json.JSONDecodeError, TypeError):
        return {}


def simulate_buying_trade(day_snapshots, entry_time, strike, option_type, entry_premium, sl_pct, target_pct):
    """Simulate buying trade: profit when premium RISES, loss when DROPS."""
    sl_premium = entry_premium * (1 - sl_pct / 100)
    target_premium = entry_premium * (1 + target_pct / 100)

    exit_premium = None
    exit_reason = None
    exit_time = None
    max_prem = entry_premium
    min_prem = entry_premium

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

        if price <= sl_premium:
            exit_premium = price
            exit_reason = "SL"
            exit_time = snap_time
            break
        if price >= target_premium:
            exit_premium = price
            exit_reason = "TARGET"
            exit_time = snap_time
            break
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
        'won': exit_reason == "TARGET" or (exit_reason == "EOD" and pnl_pct > 0),
        'max_premium': max_prem,
        'min_premium': min_prem,
    }


def run_momentum_backtest(analysis_by_day, snapshots_cache, config):
    """Run momentum PUT backtest with given config."""
    trades = []

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

            # Time window
            t_start = config.get('time_start', 11)
            t_end = config.get('time_end', 14)
            if ts.hour < t_start or ts.hour >= t_end:
                continue

            verdict = a['verdict']

            # Verdict filter — must be bearish
            min_verdict = config.get('min_verdict', 'Bears Winning')
            if min_verdict == 'Bears Strongly Winning':
                if verdict != 'Bears Strongly Winning':
                    continue
            elif min_verdict == 'Bears Winning':
                if verdict not in ('Bears Winning', 'Bears Strongly Winning'):
                    continue
            elif min_verdict == 'Slightly Bearish':
                if verdict not in ('Slightly Bearish', 'Bears Winning', 'Bears Strongly Winning'):
                    continue
            else:
                if 'Bear' not in verdict:
                    continue

            # Confidence filter
            confidence = a['signal_confidence'] or 0
            if confidence < config.get('min_confidence', 85):
                continue

            # Parse analysis JSON for deeper filters
            aj = parse_analysis_json(a.get('analysis_json', ''))

            # Confirmation status filter
            if config.get('require_confirmed', False):
                if aj.get('confirmation_status') != 'CONFIRMED':
                    continue

            # Combined score filter
            combined_score = aj.get('combined_score', 0)
            if combined_score > config.get('max_combined_score', 0):
                # combined_score is negative for bearish, more negative = more bearish
                continue

            # Price change filter (spot must be falling)
            if config.get('require_falling', False):
                price_change = aj.get('price_change_pct', 0)
                if price_change >= 0:
                    continue

            # Strength filter
            if config.get('require_strong', False):
                strength = aj.get('strength', '')
                if strength not in ('strong', 'moderate'):
                    continue

            # VIX filter
            vix = a.get('vix', 0) or 0
            if vix < config.get('vix_min', 0) or vix > config.get('vix_max', 100):
                continue

            # IV Skew filter
            iv_skew = a.get('iv_skew', 0) or 0
            if iv_skew < config.get('iv_skew_min', -100) or iv_skew > config.get('iv_skew_max', 100):
                continue

            # Consistent verdict
            if config.get('consistent_verdict', False):
                prev = a.get('prev_verdict', '')
                if prev and 'Bear' not in (prev or ''):
                    continue

            # Premium momentum filter (negative = bearish premium momentum)
            if config.get('require_bearish_premium', False):
                pm_score = aj.get('premium_momentum_score', 0)
                if pm_score > 0:  # positive means bullish premium
                    continue

            # Premium as % of spot filter
            spot = a['spot_price']
            step = NIFTY_STEP
            atm = round(spot / step) * step

            # Strike selection
            strike_mode = config.get('strike', 'ATM')
            if strike_mode == 'ATM':
                strike = atm
            elif strike_mode == 'ITM1':
                strike = atm + step  # For PUT, ITM = higher strike
            elif strike_mode == 'OTM1':
                strike = atm - step  # For PUT, OTM = lower strike
            else:
                strike = atm

            entry_premium = get_premium_at_time(day_snapshots, ts, strike, 'PE')
            if not entry_premium or entry_premium < 5:
                continue

            # Premium % of spot filter
            prem_pct = (entry_premium / spot) * 100
            if prem_pct < config.get('min_prem_pct', 0):
                continue

            result = simulate_buying_trade(
                day_snapshots, ts, strike, 'PE', entry_premium,
                config['sl_pct'], config['target_pct']
            )

            if result:
                result['day'] = day_str
                result['direction'] = 'BUY_PUT'
                result['strike'] = strike
                result['option_type'] = 'PE'
                result['verdict'] = verdict
                result['confidence'] = confidence
                result['spot'] = spot
                result['entry_time'] = ts
                result['vix'] = vix
                result['iv_skew'] = iv_skew
                result['combined_score'] = combined_score
                result['confirmation'] = aj.get('confirmation_status', '?')
                result['strength'] = aj.get('strength', '?')
                result['price_change_pct'] = aj.get('price_change_pct', 0)
                trades.append(result)
                traded = True

    return trades


def summarize(trades, name=""):
    if not trades:
        return {'name': name, 'trades': 0, 'wins': 0, 'losses': 0, 'wr': 0,
                'pnl': 0, 'pf': 0, 'avg_win': 0, 'avg_loss': 0,
                'targets': 0, 'sls': 0, 'eods': 0, 'expectancy': 0}
    wins = [t for t in trades if t['won']]
    losses = [t for t in trades if not t['won']]
    wr = len(wins) / len(trades) * 100
    total_pnl = sum(t['pnl_pct'] for t in trades)
    avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
    pf = abs(sum(t['pnl_pct'] for t in wins)) / abs(sum(t['pnl_pct'] for t in losses)) if losses and sum(t['pnl_pct'] for t in losses) != 0 else float('inf')
    expectancy = (wr/100 * avg_win) + ((1 - wr/100) * avg_loss)
    return {
        'name': name, 'trades': len(trades), 'wins': len(wins), 'losses': len(losses),
        'wr': wr, 'pnl': total_pnl, 'pf': pf, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'targets': len([t for t in trades if t['exit_reason'] == 'TARGET']),
        'sls': len([t for t in trades if t['exit_reason'] == 'SL']),
        'eods': len([t for t in trades if t['exit_reason'] == 'EOD']),
        'expectancy': expectancy,
    }


def print_summary_table(results):
    print(f"\n{'Config':<60} {'#':>3} {'W':>3} {'L':>3} {'WR%':>6} {'P&L':>9} {'AvgW':>7} {'AvgL':>7} {'PF':>6} {'Exp':>7} {'T/S/E':>7}")
    print("-" * 130)
    for r in sorted(results, key=lambda x: -x['pnl']):
        if r['trades'] == 0:
            continue
        tse = f"{r['targets']}/{r['sls']}/{r['eods']}"
        print(f"{r['name']:<60} {r['trades']:>3} {r['wins']:>3} {r['losses']:>3} {r['wr']:>5.1f}% {r['pnl']:>+8.1f}% {r['avg_win']:>+6.1f}% {r['avg_loss']:>+6.1f}% {r['pf']:>6.2f} {r['expectancy']:>+6.1f}% {tse:>7}")


def print_trades(trades, name=""):
    if not trades:
        return
    print(f"\n--- {name} ---")
    print(f"{'Day':<12} {'Time':<6} {'Strike':<7} {'Spot':<9} {'Verdict':<25} {'Cnf':>3} {'Score':>6} {'Conf':>9} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Why':<6} {'MaxP':>7}")
    for t in trades:
        time_str = t['entry_time'].strftime('%H:%M') if isinstance(t['entry_time'], datetime) else str(t['entry_time'])[11:16]
        print(f"{t['day']:<12} {time_str:<6} {t['strike']:<7} {t['spot']:<9.1f} {t['verdict']:<25} {t['confidence']:>3.0f} {t['combined_score']:>+5.1f} {t['confirmation']:>9} {t['entry_premium']:>7.2f} {t['exit_premium']:>7.2f} {t['pnl_pct']:>+7.1f}% {t['exit_reason']:<6} {t['max_premium']:>7.2f}")


if __name__ == '__main__':
    import sys

    print("=" * 130)
    print("MOMENTUM PUT BACKTEST — Trend-following 1:2 RR buying on bearish days")
    print("=" * 130)

    print("\nLoading analysis data...")
    analysis = get_analysis_data()
    by_day = defaultdict(list)
    for a in analysis:
        by_day[a['timestamp'][:10]].append(a)

    snapshots_cache = {}
    trading_days = sorted(by_day.keys())
    print(f"Trading days: {len(trading_days)} ({trading_days[0]} to {trading_days[-1]})")

    # Count bearish days for context
    bearish_days = 0
    for day_str, day_data in by_day.items():
        for a in day_data:
            ts = datetime.fromisoformat(a['timestamp'])
            if 11 <= ts.hour < 14 and 'Bear' in a['verdict']:
                bearish_days += 1
                break
    print(f"Days with bearish signals in 11-14 window: {bearish_days}")

    all_results = []

    # ============================================================
    # SECTION 1: Baseline — Bears Winning/Strongly, various SL/Target
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 1: Baseline — Verdict + SL/Target combos (no extra filters)")
    print("=" * 130)

    sec1 = []
    for verdict_level in ['Bears Winning', 'Bears Strongly Winning']:
        for sl, tgt in [(15, 30), (20, 40), (25, 50), (30, 60)]:
            vl = "BW" if verdict_level == 'Bears Winning' else "BSW"
            cfg = {
                "name": f"{vl} SL{sl}/T{tgt} ATM",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": verdict_level,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            s = summarize(trades, cfg['name'])
            sec1.append(s)
            all_results.append((s, trades))
    print_summary_table(sec1)

    # ============================================================
    # SECTION 2: + Confirmation Status = CONFIRMED
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 2: + Confirmation CONFIRMED (OI + price aligned)")
    print("=" * 130)

    sec2 = []
    for verdict_level in ['Bears Winning', 'Bears Strongly Winning']:
        for sl, tgt in [(15, 30), (20, 40), (25, 50), (30, 60)]:
            vl = "BW" if verdict_level == 'Bears Winning' else "BSW"
            cfg = {
                "name": f"{vl} SL{sl}/T{tgt} CONFIRMED",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": verdict_level,
                "require_confirmed": True,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            s = summarize(trades, cfg['name'])
            sec2.append(s)
            all_results.append((s, trades))
    print_summary_table(sec2)

    # ============================================================
    # SECTION 3: + Confidence sweep
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 3: Confidence sweep (BW + CONFIRMED)")
    print("=" * 130)

    sec3 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for conf in [75, 85, 90, 95, 100]:
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF conf>={conf}",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "min_confidence": conf,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            sec3.append(summarize(trades, cfg['name']))
    print_summary_table(sec3)

    # ============================================================
    # SECTION 4: Combined score thresholds
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 4: Combined score threshold (more negative = more bearish)")
    print("=" * 130)

    sec4 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for max_score in [0, -20, -30, -40, -50, -60]:
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF score<={max_score}",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "max_combined_score": max_score,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            sec4.append(summarize(trades, cfg['name']))
    print_summary_table(sec4)

    # ============================================================
    # SECTION 5: Strike selection
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 5: Strike selection (ATM vs ITM1 vs OTM1)")
    print("=" * 130)

    sec5 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for strike_mode in ['ATM', 'ITM1', 'OTM1']:
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF {strike_mode}",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "strike": strike_mode,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            s = summarize(trades, cfg['name'])
            sec5.append(s)
            all_results.append((s, trades))
    print_summary_table(sec5)

    # ============================================================
    # SECTION 6: Time windows
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 6: Time windows")
    print("=" * 130)

    sec6 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for label, ts, te in [("11-14", 11, 14), ("11-13", 11, 13), ("11-12", 11, 12),
                               ("12-14", 12, 14), ("12-13", 12, 13), ("13-14", 13, 14)]:
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF {label}",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "time_start": ts, "time_end": te,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            sec6.append(summarize(trades, cfg['name']))
    print_summary_table(sec6)

    # ============================================================
    # SECTION 7: Consistent verdict
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 7: Consistent verdict (prev also bearish)")
    print("=" * 130)

    sec7 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for consistent in [False, True]:
            label = "Consistent" if consistent else "Any"
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF {label}",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "consistent_verdict": consistent,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            sec7.append(summarize(trades, cfg['name']))
    print_summary_table(sec7)

    # ============================================================
    # SECTION 8: Require strength = strong
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 8: Strength filter")
    print("=" * 130)

    sec8 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for require_strong in [False, True]:
            label = "StrongOnly" if require_strong else "AnyStrength"
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF {label}",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "require_strong": require_strong,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            sec8.append(summarize(trades, cfg['name']))
    print_summary_table(sec8)

    # ============================================================
    # SECTION 9: Require falling price
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 9: Require price falling")
    print("=" * 130)

    sec9 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for falling in [False, True]:
            label = "PriceFalling" if falling else "AnyPrice"
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF {label}",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "require_falling": falling,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            sec9.append(summarize(trades, cfg['name']))
    print_summary_table(sec9)

    # ============================================================
    # SECTION 10: Premium % of spot filter
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 10: Premium as % of spot filter")
    print("=" * 130)

    sec10 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for min_prem in [0, 0.15, 0.20, 0.25, 0.30]:
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF prem>={min_prem}%",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "min_prem_pct": min_prem,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            sec10.append(summarize(trades, cfg['name']))
    print_summary_table(sec10)

    # ============================================================
    # SECTION 11: Bearish premium momentum
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 11: Bearish premium momentum filter")
    print("=" * 130)

    sec11 = []
    for sl, tgt in [(20, 40), (25, 50)]:
        for bp in [False, True]:
            label = "BearPremMom" if bp else "AnyPremMom"
            cfg = {
                "name": f"BW SL{sl}/T{tgt} CONF {label}",
                "sl_pct": sl, "target_pct": tgt,
                "min_verdict": 'Bears Winning',
                "require_confirmed": True,
                "require_bearish_premium": bp,
            }
            trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
            sec11.append(summarize(trades, cfg['name']))
    print_summary_table(sec11)

    # ============================================================
    # SECTION 12: BEST COMBOS
    # ============================================================
    print("\n" + "=" * 130)
    print("SECTION 12: COMBINED BEST — Cherry-picked combos")
    print("=" * 130)

    sec12 = []
    combos = [
        # Core: BW + CONFIRMED + various optimizations
        {"name": "COMBO-A: BW 20/40 CONF Consistent", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Winning", "require_confirmed": True, "consistent_verdict": True},
        {"name": "COMBO-B: BW 25/50 CONF Consistent", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Winning", "require_confirmed": True, "consistent_verdict": True},
        {"name": "COMBO-C: BSW 20/40 CONF", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Strongly Winning", "require_confirmed": True},
        {"name": "COMBO-D: BSW 25/50 CONF", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Strongly Winning", "require_confirmed": True},
        {"name": "COMBO-E: BW 20/40 CONF score<=-30", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Winning", "require_confirmed": True, "max_combined_score": -30},
        {"name": "COMBO-F: BW 25/50 CONF score<=-30", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Winning", "require_confirmed": True, "max_combined_score": -30},
        {"name": "COMBO-G: BW 20/40 CONF Falling Consistent", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Winning", "require_confirmed": True, "require_falling": True, "consistent_verdict": True},
        {"name": "COMBO-H: BW 25/50 CONF Falling Consistent", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Winning", "require_confirmed": True, "require_falling": True, "consistent_verdict": True},
        {"name": "COMBO-I: BSW 20/40 CONF Consistent", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Strongly Winning", "require_confirmed": True, "consistent_verdict": True},
        {"name": "COMBO-J: BSW 25/50 CONF Consistent", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Strongly Winning", "require_confirmed": True, "consistent_verdict": True},
        {"name": "COMBO-K: BW 20/40 CONF Strong prem>=0.20", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Winning", "require_confirmed": True, "require_strong": True, "min_prem_pct": 0.20},
        {"name": "COMBO-L: BW 25/50 CONF Strong prem>=0.20", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Winning", "require_confirmed": True, "require_strong": True, "min_prem_pct": 0.20},
        {"name": "COMBO-M: BW 20/40 CONF BearPrem Consistent", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Winning", "require_confirmed": True, "require_bearish_premium": True, "consistent_verdict": True},
        {"name": "COMBO-N: BSW 25/50 CONF Strong Consistent", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Strongly Winning", "require_confirmed": True, "require_strong": True, "consistent_verdict": True},
        {"name": "COMBO-O: BW 20/40 CONF ITM1 Consistent", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Winning", "require_confirmed": True, "strike": "ITM1", "consistent_verdict": True},
        {"name": "COMBO-P: BW 25/50 CONF ITM1 Consistent", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Winning", "require_confirmed": True, "strike": "ITM1", "consistent_verdict": True},
        {"name": "COMBO-Q: BW 20/40 CONF score<=-40 Consistent", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Winning", "require_confirmed": True, "max_combined_score": -40, "consistent_verdict": True},
        {"name": "COMBO-R: BW 25/50 CONF score<=-40 Consistent", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Winning", "require_confirmed": True, "max_combined_score": -40, "consistent_verdict": True},
        {"name": "COMBO-S: BW 20/40 CONF 12-14", "sl_pct": 20, "target_pct": 40,
         "min_verdict": "Bears Winning", "require_confirmed": True, "time_start": 12, "time_end": 14},
        {"name": "COMBO-T: BW 25/50 CONF 12-14", "sl_pct": 25, "target_pct": 50,
         "min_verdict": "Bears Winning", "require_confirmed": True, "time_start": 12, "time_end": 14},
    ]

    for cfg in combos:
        trades = run_momentum_backtest(by_day, snapshots_cache, cfg)
        s = summarize(trades, cfg['name'])
        sec12.append(s)
        all_results.append((s, trades))
    print_summary_table(sec12)

    # ============================================================
    # FINAL: Detailed trades for top configs
    # ============================================================
    print("\n" + "=" * 130)
    print("DETAILED TRADES — Top configs (WR>=50% and trades>=3)")
    print("=" * 130)

    printed = set()
    for s, trades in sorted(all_results, key=lambda x: -x[0]['pnl']):
        if s['trades'] >= 3 and s['wr'] >= 50 and s['name'] not in printed:
            print_trades(trades, f"{s['name']} | WR={s['wr']:.0f}% PF={s['pf']:.2f} P&L={s['pnl']:+.1f}% Exp={s['expectancy']:+.1f}%")
            printed.add(s['name'])
            if len(printed) >= 10:
                break

    print("\n\nDone!")
