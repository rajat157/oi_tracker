"""
Backtest Config B selling strategy with 2 trades per day.
Allows overlapping trades - second signal can fire while first is active.
Uses OTM-1, 25%/25% SL/Target (best config from single-trade backtest).
"""

import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
NIFTY_STEP = 50
SL_PCT = 25
TARGET_PCT = 25
OTM_OFFSET = 1
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
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_option_prices_for_day(date_str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp
        FROM oi_snapshots WHERE DATE(timestamp) = ? AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price""", (date_str,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_otm_strike(spot, direction):
    atm = round(spot / NIFTY_STEP) * NIFTY_STEP
    if direction == "SELL_PUT":
        return atm - (NIFTY_STEP * OTM_OFFSET)
    else:
        return atm + (NIFTY_STEP * OTM_OFFSET)


def get_premium_at_time(snapshots, target_time, strike, option_type):
    best_premium = None
    best_diff = float('inf')
    for snap in snapshots:
        if snap['strike_price'] == strike:
            snap_time = datetime.fromisoformat(snap['timestamp'])
            diff = abs((snap_time - target_time).total_seconds())
            if diff < best_diff:
                best_diff = diff
                price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
                if price and price > 0:
                    best_premium = price
                    best_diff = diff
    return best_premium


def simulate_sell_trade(day_snapshots, entry_time, strike, option_type, entry_premium):
    sl_premium = entry_premium * (1 + SL_PCT / 100)
    target_premium = entry_premium * (1 - TARGET_PCT / 100)
    
    exit_premium = None
    exit_reason = None
    exit_time = None
    
    for snap in day_snapshots:
        snap_time = datetime.fromisoformat(snap['timestamp'])
        if snap_time <= entry_time:
            continue
        if snap['strike_price'] != strike:
            continue
        price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
        if not price or price <= 0:
            continue
        
        if price >= sl_premium:
            exit_premium = price
            exit_reason = "SL"
            exit_time = snap_time
            break
        if price <= target_premium:
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
    
    pnl_pct = ((entry_premium - exit_premium) / entry_premium) * 100
    return {
        'entry_premium': entry_premium,
        'exit_premium': exit_premium,
        'sl_premium': sl_premium,
        'target_premium': target_premium,
        'pnl_pct': pnl_pct,
        'exit_reason': exit_reason,
        'exit_time': exit_time,
        'won': exit_reason == "TARGET" or (exit_reason == "EOD" and pnl_pct > 0)
    }


def run_backtest():
    analysis = get_analysis_data()
    by_day = defaultdict(list)
    for a in analysis:
        day = a['timestamp'][:10]
        by_day[day].append(a)
    
    max_trades_configs = [1, 2, 3]
    
    for max_trades in max_trades_configs:
        trades = []
        
        for day_str in sorted(by_day.keys()):
            if day_str == '2026-02-16':
                continue
            
            day_analysis = by_day[day_str]
            day_snapshots = get_option_prices_for_day(day_str)
            if not day_snapshots:
                continue
            
            # Track active trades for this day
            active_trades = []  # list of (strike, option_type, sl_premium, target_premium, resolved)
            trade_count = 0
            
            for a in day_analysis:
                ts = datetime.fromisoformat(a['timestamp'])
                hour = ts.hour
                
                if hour < TIME_START or hour >= TIME_END:
                    continue
                
                if trade_count >= max_trades:
                    continue
                
                verdict = a['verdict']
                confidence = a['signal_confidence'] or 0
                
                if verdict not in VALID_VERDICTS:
                    continue
                if confidence < MIN_CONFIDENCE:
                    continue
                
                spot = a['spot_price']
                if verdict == 'Slightly Bullish':
                    direction = "SELL_PUT"
                    option_type = "PE"
                else:
                    direction = "SELL_CALL"
                    option_type = "CE"
                
                strike = get_otm_strike(spot, direction)
                
                # Check if we already have a trade on this exact strike+type
                already_has = any(t['strike'] == strike and t['option_type'] == option_type 
                                  for t in trades if t['day'] == day_str)
                if already_has:
                    continue
                
                entry_premium = get_premium_at_time(day_snapshots, ts, strike, option_type)
                if not entry_premium or entry_premium < MIN_PREMIUM:
                    continue
                
                result = simulate_sell_trade(day_snapshots, ts, strike, option_type, entry_premium)
                if result:
                    result['day'] = day_str
                    result['direction'] = direction
                    result['strike'] = strike
                    result['option_type'] = option_type
                    result['verdict'] = verdict
                    result['confidence'] = confidence
                    result['spot'] = spot
                    result['entry_time'] = ts
                    trades.append(result)
                    trade_count += 1
        
        # Print results
        wins = [t for t in trades if t['won']]
        losses = [t for t in trades if not t['won']]
        wr = len(wins) / len(trades) * 100 if trades else 0
        total_pnl = sum(t['pnl_pct'] for t in trades)
        avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
        pf = abs(sum(t['pnl_pct'] for t in wins)) / abs(sum(t['pnl_pct'] for t in losses)) if losses else float('inf')
        
        target_exits = len([t for t in trades if t['exit_reason'] == 'TARGET'])
        sl_exits = len([t for t in trades if t['exit_reason'] == 'SL'])
        eod_exits = len([t for t in trades if t['exit_reason'] == 'EOD'])
        
        print(f"\n{'=' * 90}")
        print(f"MAX {max_trades} TRADE(S) PER DAY | OTM-1 | 25%/25% SL/Target")
        print(f"{'=' * 90}")
        print(f"Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {wr:.1f}%")
        print(f"Total P&L: {total_pnl:+.2f}% | Avg Win: {avg_win:+.2f}% | Avg Loss: {avg_loss:+.2f}%")
        print(f"Exits -- Target: {target_exits} | SL: {sl_exits} | EOD: {eod_exits}")
        print(f"Profit Factor: {pf:.2f}")
        
        # Per-day breakdown
        print(f"\n{'Day':<12} {'#':>2} {'Dir':<10} {'Strike':<8} {'Spot':<8} {'Verdict':<20} {'Conf':<6} {'Entry':>8} {'Exit':>8} {'P&L':>8} {'Reason':<8} {'Time':<6}")
        print("-" * 110)
        
        for t in trades:
            time_str = t['entry_time'].strftime('%H:%M')
            print(f"{t['day']:<12} {'':>2} {t['direction']:<10} {t['strike']:<8} {t['spot']:<8.0f} {t['verdict']:<20} {t['confidence']:<6.0f} "
                  f"Rs{t['entry_premium']:>6.2f} Rs{t['exit_premium']:>6.2f} {t['pnl_pct']:>+7.2f}% {t['exit_reason']:<8} {time_str}")
        
        # Per-day summary
        print(f"\nPer-day breakdown:")
        days_traded = sorted(set(t['day'] for t in trades))
        for d in days_traded:
            day_trades = [t for t in trades if t['day'] == d]
            day_wins = len([t for t in day_trades if t['won']])
            day_pnl = sum(t['pnl_pct'] for t in day_trades)
            print(f"  {d}: {len(day_trades)} trades, {day_wins}W-{len(day_trades)-day_wins}L, P&L: {day_pnl:+.2f}%")
    
    # Summary comparison
    print(f"\n\n{'=' * 90}")
    print("SUMMARY: 1 vs 2 vs 3 trades per day")
    print(f"{'=' * 90}")


if __name__ == '__main__':
    run_backtest()
