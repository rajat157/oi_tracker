"""
Backtest Config B strategy for OPTIONS SELLING (intraday).

Config B (Buying):
- Time: 11:00 - 14:00 IST
- Verdict: "Slightly Bullish" or "Slightly Bearish" only
- Confidence: >= 65%
- One trade per day (first valid signal)
- SL: -20%, Target: +22%

For SELLING adaptation:
- Slightly Bullish signal -> SELL OTM PUT (bullish = puts lose value)
- Slightly Bearish signal -> SELL OTM CALL (bearish = calls lose value)
- 1:1 RR as Mason requested
- Track premium decay (seller profits when premium drops)

For sellers: profit = entry_premium - exit_premium (premium collected upfront)
- Target: premium drops by X% (seller keeps decay)
- SL: premium rises by X% (seller loses)
"""

import sqlite3
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

def get_analysis_data():
    """Get all analysis records with verdicts."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, atm_strike, verdict, signal_confidence,
               futures_oi_change, analysis_json
        FROM analysis_history
        ORDER BY timestamp
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_option_prices_for_day(date_str):
    """Get all option price snapshots for a given day."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, strike_price, ce_ltp, pe_ltp, ce_iv, pe_iv
        FROM oi_snapshots
        WHERE DATE(timestamp) = ? AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price
    """, (date_str,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def find_otm_strike(spot_price, direction, offset=1):
    """
    Find OTM strike for selling.
    - Selling PUT: go below spot (OTM put = strike below spot)
    - Selling CALL: go above spot (OTM call = strike above spot)
    offset: how many strikes OTM (1 = first OTM, 2 = second OTM)
    """
    step = 50  # NIFTY strike gap
    atm = round(spot_price / step) * step
    
    if direction == "SELL_PUT":
        # OTM puts are below spot
        return atm - (step * offset)
    else:  # SELL_CALL
        # OTM calls are above spot
        return atm + (step * offset)

def get_premium_at_time(snapshots_by_time, target_time, strike, option_type):
    """Get premium for a specific strike at closest time."""
    best_premium = None
    best_diff = float('inf')
    
    for snap in snapshots_by_time:
        if snap['strike_price'] == strike:
            snap_time = datetime.fromisoformat(snap['timestamp'])
            diff = abs((snap_time - target_time).total_seconds())
            if diff < best_diff:
                best_diff = diff
                if option_type == 'PE':
                    best_premium = snap['pe_ltp']
                else:
                    best_premium = snap['ce_ltp']
    
    return best_premium if best_premium and best_premium > 0 else None

def simulate_selling_trade(day_snapshots, entry_time, strike, option_type, entry_premium, sl_pct, target_pct):
    """
    Simulate an options selling trade.
    
    For sellers:
    - Profit when premium DROPS (we sold high, buy back low)
    - Loss when premium RISES (we sold low, have to buy back high)
    - SL: premium rises by sl_pct% from entry (loss for seller)
    - Target: premium drops by target_pct% from entry (profit for seller)
    """
    sl_premium = entry_premium * (1 + sl_pct / 100)  # Premium rises = loss
    target_premium = entry_premium * (1 - target_pct / 100)  # Premium drops = profit
    
    # Track price through the day
    max_premium = entry_premium
    min_premium = entry_premium
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
        
        max_premium = max(max_premium, price)
        min_premium = min(min_premium, price)
        
        # Check SL (premium rises above threshold = loss for seller)
        if price >= sl_premium:
            exit_premium = price
            exit_reason = "SL"
            exit_time = snap_time
            break
        
        # Check target (premium drops below threshold = profit for seller)
        if price <= target_premium:
            exit_premium = price
            exit_reason = "TARGET"
            exit_time = snap_time
            break
        
        # EOD exit at 15:20
        if snap_time.hour == 15 and snap_time.minute >= 20:
            exit_premium = price
            exit_reason = "EOD"
            exit_time = snap_time
            break
    
    if exit_premium is None:
        # Use last available price
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
    
    # P&L for seller = entry - exit (we sold at entry, buy back at exit)
    pnl_pct = ((entry_premium - exit_premium) / entry_premium) * 100
    
    return {
        'entry_premium': entry_premium,
        'exit_premium': exit_premium,
        'sl_premium': sl_premium,
        'target_premium': target_premium,
        'pnl_pct': pnl_pct,
        'exit_reason': exit_reason,
        'exit_time': exit_time,
        'max_premium': max_premium,
        'min_premium': min_premium,
        'won': exit_reason == "TARGET" or (exit_reason == "EOD" and pnl_pct > 0)
    }

def run_backtest():
    """Run Config B backtest for options selling."""
    analysis = get_analysis_data()
    
    # Group analysis by day
    by_day = defaultdict(list)
    for a in analysis:
        day = a['timestamp'][:10]
        by_day[day] = by_day.get(day, [])
        by_day[day].append(a)
    
    # Config B parameters
    TIME_START = 11  # 11:00
    TIME_END = 14    # 14:00
    MIN_CONFIDENCE = 65
    VALID_VERDICTS = ['Slightly Bullish', 'Slightly Bearish']
    
    # Selling parameters - test multiple SL/Target combos
    configs = [
        {"name": "1:1 (20%/20%)", "sl_pct": 20, "target_pct": 20},
        {"name": "1:1 (15%/15%)", "sl_pct": 15, "target_pct": 15},
        {"name": "1:1 (25%/25%)", "sl_pct": 25, "target_pct": 25},
        {"name": "1:1.5 (20%/30%)", "sl_pct": 20, "target_pct": 30},
    ]
    
    # OTM offsets to test
    otm_offsets = [1, 2]  # 1 strike OTM, 2 strikes OTM
    
    all_results = {}
    
    for offset in otm_offsets:
        for config in configs:
            key = f"OTM-{offset} | {config['name']}"
            trades = []
            
            for day_str in sorted(by_day.keys()):
                if day_str == '2026-02-16':  # Skip today (incomplete)
                    continue
                    
                day_analysis = by_day[day_str]
                day_snapshots = get_option_prices_for_day(day_str)
                
                if not day_snapshots:
                    continue
                
                # Find first valid Config B signal
                traded = False
                for a in day_analysis:
                    if traded:
                        break
                    
                    ts = datetime.fromisoformat(a['timestamp'])
                    hour = ts.hour
                    
                    if hour < TIME_START or hour >= TIME_END:
                        continue
                    
                    verdict = a['verdict']
                    confidence = a['signal_confidence'] or 0
                    
                    if verdict not in VALID_VERDICTS:
                        continue
                    if confidence < MIN_CONFIDENCE:
                        continue
                    
                    # Determine selling direction
                    spot = a['spot_price']
                    if verdict == 'Slightly Bullish':
                        # Bullish = sell puts (puts will lose value)
                        direction = "SELL_PUT"
                        option_type = "PE"
                    else:
                        # Bearish = sell calls (calls will lose value)
                        direction = "SELL_CALL"
                        option_type = "CE"
                    
                    strike = find_otm_strike(spot, direction, offset)
                    
                    # Get entry premium
                    entry_premium = get_premium_at_time(day_snapshots, ts, strike, option_type)
                    
                    if not entry_premium or entry_premium < 5:  # Skip very low premiums
                        continue
                    
                    # Simulate trade
                    result = simulate_selling_trade(
                        day_snapshots, ts, strike, option_type,
                        entry_premium, config['sl_pct'], config['target_pct']
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
                        trades.append(result)
                        traded = True
            
            all_results[key] = trades
    
    # Print results
    print("=" * 100)
    print("OPTIONS SELLING BACKTEST — Config B Signals")
    print("=" * 100)
    print(f"Data: Jan 30 - Feb 13, 2026 ({len(by_day)-1} trading days)")
    print(f"Signal: Slightly Bullish -> Sell OTM Put | Slightly Bearish -> Sell OTM Call")
    print(f"Time: 11:00-14:00 | Confidence: >=65% | One trade/day")
    print()
    
    for key in sorted(all_results.keys()):
        trades = all_results[key]
        if not trades:
            print(f"\n{key}: NO TRADES")
            continue
        
        wins = [t for t in trades if t['won']]
        losses = [t for t in trades if not t['won']]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        total_pnl = sum(t['pnl_pct'] for t in trades)
        avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
        
        target_exits = len([t for t in trades if t['exit_reason'] == 'TARGET'])
        sl_exits = len([t for t in trades if t['exit_reason'] == 'SL'])
        eod_exits = len([t for t in trades if t['exit_reason'] == 'EOD'])
        
        print(f"\n{'=' * 80}")
        print(f"[RESULT] {key}")
        print(f"{'=' * 80}")
        print(f"Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {win_rate:.1f}%")
        print(f"Total P&L: {total_pnl:+.2f}% | Avg Win: {avg_win:+.2f}% | Avg Loss: {avg_loss:+.2f}%")
        print(f"Exits — Target: {target_exits} | SL: {sl_exits} | EOD: {eod_exits}")
        
        if wins and losses:
            profit_factor = abs(sum(t['pnl_pct'] for t in wins)) / abs(sum(t['pnl_pct'] for t in losses))
            print(f"Profit Factor: {profit_factor:.2f}")
        
        print(f"\n{'Day':<12} {'Dir':<10} {'Strike':<8} {'Spot':<8} {'Verdict':<20} {'Conf':<6} {'Entry':<8} {'Exit':<8} {'P&L':>8} {'Reason':<8}")
        print("-" * 100)
        for t in trades:
            print(f"{t['day']:<12} {t['direction']:<10} {t['strike']:<8} {t['spot']:<8.0f} {t['verdict']:<20} {t['confidence']:<6.0f} "
                  f"Rs{t['entry_premium']:<7.2f} Rs{t['exit_premium']:<7.2f} {t['pnl_pct']:>+7.2f}% {t['exit_reason']:<8}")
    
    # Summary comparison
    print(f"\n\n{'=' * 100}")
    print("SUMMARY COMPARISON")
    print(f"{'=' * 100}")
    print(f"\n{'Config':<30} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'Total P&L':>10} {'Avg Win':>9} {'Avg Loss':>9} {'PF':>6}")
    print("-" * 90)
    for key in sorted(all_results.keys()):
        trades = all_results[key]
        if not trades:
            continue
        wins = [t for t in trades if t['won']]
        losses = [t for t in trades if not t['won']]
        wr = len(wins)/len(trades)*100 if trades else 0
        total = sum(t['pnl_pct'] for t in trades)
        aw = sum(t['pnl_pct'] for t in wins)/len(wins) if wins else 0
        al = sum(t['pnl_pct'] for t in losses)/len(losses) if losses else 0
        pf = abs(sum(t['pnl_pct'] for t in wins))/abs(sum(t['pnl_pct'] for t in losses)) if losses else float('inf')
        print(f"{key:<30} {len(trades):>7} {len(wins):>6} {wr:>6.1f}% {total:>+9.2f}% {aw:>+8.2f}% {al:>+8.2f}% {pf:>6.2f}")

if __name__ == '__main__':
    run_backtest()
