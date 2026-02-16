"""Check Config B buying WR across different date windows."""
from backtest_buying_1to2 import get_analysis_data, get_option_prices_for_day, get_premium_at_time
from collections import defaultdict
from datetime import datetime

analysis = get_analysis_data()
by_day = defaultdict(list)
for a in analysis:
    by_day[a['timestamp'][:10]].append(a)

snapshots_cache = {}

def run_buying_window(by_day, sc, sl_pct, target_pct, exclude_days=None):
    exclude_days = exclude_days or set()
    trades = []
    for day_str in sorted(by_day.keys()):
        if day_str == '2026-02-16' or day_str in exclude_days:
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
            option_type = 'CE' if 'Bullish' in verdict else 'PE'
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
                    exit_premium = price; exit_reason = 'SL'; break
                if price >= target_premium:
                    exit_premium = price; exit_reason = 'TARGET'; break
                if snap_time.hour == 15 and snap_time.minute >= 20:
                    exit_premium = price; exit_reason = 'EOD'; break
            
            if not exit_premium:
                for snap in reversed(day_snapshots):
                    if snap['strike_price'] == strike:
                        price = snap['pe_ltp'] if option_type == 'PE' else snap['ce_ltp']
                        if price and price > 0:
                            exit_premium = price; exit_reason = 'EOD'; break
            
            if exit_premium:
                pnl = ((exit_premium - entry_premium) / entry_premium) * 100
                trades.append({
                    'day': day_str, 'won': exit_reason == 'TARGET' or (exit_reason == 'EOD' and pnl > 0),
                    'pnl_pct': pnl, 'exit_reason': exit_reason
                })
                traded = True
    return trades

# Full 12 days
trades = run_buying_window(by_day, snapshots_cache, 20, 22)
wins = len([t for t in trades if t['won']])
print(f"Full 12 days: {len(trades)} trades, {wins}W/{len(trades)-wins}L, WR={wins/len(trades)*100:.1f}%")

# First 8 days only (Jan 30 - Feb 6)
first_8_days = sorted([d for d in by_day.keys() if d != '2026-02-16'])[:8]
last_days = sorted([d for d in by_day.keys() if d != '2026-02-16'])[8:]
trades8 = run_buying_window(by_day, snapshots_cache, 20, 22, exclude_days=set(last_days))
wins8 = len([t for t in trades8 if t['won']])
print(f"First 8 days ({first_8_days[0]} to {first_8_days[-1]}): {len(trades8)} trades, {wins8}W/{len(trades8)-wins8}L, WR={wins8/len(trades8)*100:.1f}%")
for t in trades8:
    print(f"  {t['day']} {'W' if t['won'] else 'L'} {t['pnl_pct']:+.1f}% {t['exit_reason']}")

# Feb 3-13 (the 8 days mentioned in that message = after Jan 30 weekend)
feb_window = [d for d in sorted(by_day.keys()) if '2026-02-01' <= d <= '2026-02-13']
exclude = set(sorted(by_day.keys())) - set(feb_window)
trades_feb = run_buying_window(by_day, snapshots_cache, 20, 22, exclude_days=exclude)
wins_feb = len([t for t in trades_feb if t['won']])
if trades_feb:
    print(f"\nFeb 1-13 ({len(feb_window)} days): {len(trades_feb)} trades, {wins_feb}W/{len(trades_feb)-wins_feb}L, WR={wins_feb/len(trades_feb)*100:.1f}%")

# Excluding Jan 30 (no VIX data)
trades_no30 = run_buying_window(by_day, snapshots_cache, 20, 22, exclude_days={'2026-01-30'})
wins_no30 = len([t for t in trades_no30 if t['won']])
print(f"\nExcluding Jan 30: {len(trades_no30)} trades, {wins_no30}W/{len(trades_no30)-wins_no30}L, WR={wins_no30/len(trades_no30)*100:.1f}%")

# Check which days have no VIX (VIX=0)
print("\nDays with VIX=0:")
for day in sorted(by_day.keys()):
    if day == '2026-02-16':
        continue
    vix_vals = [a.get('vix', 0) or 0 for a in by_day[day]]
    if max(vix_vals) == 0:
        print(f"  {day}")

# Feb 2-13 only (maybe 8 trading days with data)
feb2_13 = [d for d in sorted(by_day.keys()) if '2026-02-02' <= d <= '2026-02-13']
exclude2 = set(sorted(by_day.keys())) - set(feb2_13)
trades_f2 = run_buying_window(by_day, snapshots_cache, 20, 22, exclude_days=exclude2)
wins_f2 = len([t for t in trades_f2 if t['won']])
if trades_f2:
    print(f"\nFeb 2-13 ({len(feb2_13)} days, {len(trades_f2)} trades): {wins_f2}W/{len(trades_f2)-wins_f2}L, WR={wins_f2/len(trades_f2)*100:.1f}%")
    for t in trades_f2:
        print(f"  {t['day']} {'W' if t['won'] else 'L'} {t['pnl_pct']:+.1f}% {t['exit_reason']}")
