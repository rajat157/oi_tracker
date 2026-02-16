"""Quick Config B buying backtest - current 20/22 setup."""
from backtest_buying_1to2 import get_analysis_data, get_option_prices_for_day, get_premium_at_time, summarize
from collections import defaultdict
from datetime import datetime

analysis = get_analysis_data()
by_day = defaultdict(list)
for a in analysis:
    by_day[a['timestamp'][:10]].append(a)

snapshots_cache = {}

def run_buying(by_day, sc, sl_pct, target_pct):
    trades = []
    for day_str in sorted(by_day.keys()):
        if day_str == '2026-02-16':
            continue
        day_analysis = by_day[day_str]
        if day_str not in sc:
            sc[day_str] = get_option_prices_for_day(day_str)
        day_snapshots = sc[day_str]
        if not day_snapshots:
            continue
        
        traded = False
        for a in day_analysis:
            if traded:
                break
            ts = datetime.fromisoformat(a['timestamp'])
            if ts.hour < 11 or ts.hour >= 14:
                continue
            verdict = a['verdict']
            if verdict not in ['Slightly Bullish', 'Slightly Bearish']:
                continue
            confidence = a['signal_confidence'] or 0
            if confidence < 65:
                continue
            
            spot = a['spot_price']
            atm = round(spot / 50) * 50
            
            if 'Bullish' in verdict:
                option_type = 'CE'
            else:
                option_type = 'PE'
            strike = atm
            
            entry_premium = get_premium_at_time(day_snapshots, ts, strike, option_type)
            if not entry_premium or entry_premium < 5:
                continue
            
            sl_premium = entry_premium * (1 - sl_pct / 100)
            target_premium = entry_premium * (1 + target_pct / 100)
            
            exit_premium = None
            exit_reason = None
            
            for snap in day_snapshots:
                snap_time = datetime.fromisoformat(snap['timestamp'])
                if snap_time <= ts:
                    continue
                if snap['strike_price'] != strike:
                    continue
                price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
                if not price or price <= 0:
                    continue
                if price <= sl_premium:
                    exit_premium = price
                    exit_reason = 'SL'
                    break
                if price >= target_premium:
                    exit_premium = price
                    exit_reason = 'TARGET'
                    break
                if snap_time.hour == 15 and snap_time.minute >= 20:
                    exit_premium = price
                    exit_reason = 'EOD'
                    break
            
            if not exit_premium:
                for snap in reversed(day_snapshots):
                    if snap['strike_price'] == strike:
                        price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
                        if price and price > 0:
                            exit_premium = price
                            exit_reason = 'EOD'
                            break
            
            if exit_premium:
                pnl = ((exit_premium - entry_premium) / entry_premium) * 100
                trades.append({
                    'day': day_str, 'direction': 'BUY_CALL' if option_type == 'CE' else 'BUY_PUT',
                    'strike': strike, 'option_type': option_type,
                    'entry_premium': entry_premium, 'exit_premium': exit_premium,
                    'pnl_pct': pnl, 'exit_reason': exit_reason,
                    'won': exit_reason == 'TARGET' or (exit_reason == 'EOD' and pnl > 0),
                    'verdict': verdict, 'confidence': confidence, 'spot': spot, 'vix': a.get('vix', 0) or 0,
                })
                traded = True
    return trades

# Config B current: 20/22
trades = run_buying(by_day, snapshots_cache, 20, 22)
wins = [t for t in trades if t['won']]
losses = [t for t in trades if not t['won']]
print(f"Config B (20% SL / 22% TGT) — 1 trade/day, 11-14, Slightly, conf>=65")
print(f"Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)} | WR: {len(wins)/len(trades)*100:.1f}%")
print()
print(f"{'Day':<12} {'Dir':<10} {'Strike':<7} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Reason':<7} {'Verdict':<20} {'Conf':>4}")
for t in trades:
    marker = "W" if t['won'] else "L"
    print(f"{t['day']:<12} {t['direction']:<10} {t['strike']:<7} {t['entry_premium']:>7.2f} {t['exit_premium']:>7.2f} {t['pnl_pct']:>+7.1f}% {t['exit_reason']:<7} {t['verdict']:<20} {t['confidence']:>4.0f} {marker}")
