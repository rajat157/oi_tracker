"""
Backtest Config B BUYING strategy with different R:R ratios.
SL stays at -20%. Target varies: 22% (current), 30% (1.5RR), 40% (2RR).
"""
import sqlite3, os
from datetime import datetime
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')

def get_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT timestamp, spot_price, verdict, signal_confidence FROM analysis_history ORDER BY timestamp")
    analysis = [dict(r) for r in c.fetchall()]
    return analysis, conn

def get_snaps(conn, date_str):
    c = conn.cursor()
    c.execute("""SELECT timestamp, strike_price, ce_ltp, pe_ltp FROM oi_snapshots 
        WHERE DATE(timestamp) = ? AND (ce_ltp > 0 OR pe_ltp > 0)
        ORDER BY timestamp, strike_price""", (date_str,))
    return [dict(r) for r in c.fetchall()]

def get_premium(snaps, ts, strike, opt):
    best, best_diff = None, float('inf')
    for s in snaps:
        if s['strike_price'] == strike:
            t = datetime.fromisoformat(s['timestamp'])
            diff = abs((t - ts).total_seconds())
            if diff < best_diff:
                p = s['ce_ltp'] if opt == 'CE' else s['pe_ltp']
                if p and p > 0:
                    best, best_diff = p, diff
    return best

def simulate_buy(snaps, entry_time, strike, opt, entry, sl_pct, tgt_pct):
    sl = entry * (1 - sl_pct / 100)
    target = entry * (1 + tgt_pct / 100)
    
    for s in snaps:
        t = datetime.fromisoformat(s['timestamp'])
        if t <= entry_time or s['strike_price'] != strike:
            continue
        p = s['ce_ltp'] if opt == 'CE' else s['pe_ltp']
        if not p or p <= 0:
            continue
        if p <= sl:
            pnl = ((p - entry) / entry) * 100
            return {'pnl_pct': pnl, 'exit_reason': 'SL', 'won': False, 'exit': p}
        if p >= target:
            pnl = ((p - entry) / entry) * 100
            return {'pnl_pct': pnl, 'exit_reason': 'TARGET', 'won': True, 'exit': p}
        if t.hour == 15 and t.minute >= 20:
            pnl = ((p - entry) / entry) * 100
            return {'pnl_pct': pnl, 'exit_reason': 'EOD', 'won': pnl > 0, 'exit': p}
    
    for s in reversed(snaps):
        if s['strike_price'] == strike:
            p = s['ce_ltp'] if opt == 'CE' else s['pe_ltp']
            if p and p > 0:
                pnl = ((p - entry) / entry) * 100
                return {'pnl_pct': pnl, 'exit_reason': 'EOD', 'won': pnl > 0, 'exit': p}
    return None

def run():
    analysis, conn = get_data()
    by_day = defaultdict(list)
    for a in analysis:
        by_day[a['timestamp'][:10]].append(a)
    
    # Current Config B uses trade_setup from analyzer which picks strike dynamically
    # For simplicity, use ATM strike like the analyzer does
    configs = [
        ("1:1.1 (SL 20% / T 22%)", 20, 22),    # Current
        ("1:1.5 (SL 20% / T 30%)", 20, 30),
        ("1:2   (SL 20% / T 40%)", 20, 40),
        ("1:2.5 (SL 20% / T 50%)", 20, 50),
    ]
    
    for name, sl_pct, tgt_pct in configs:
        trades = []
        for day_str in sorted(by_day.keys()):
            if day_str == '2026-02-16':
                continue
            day_a = by_day[day_str]
            day_snaps = get_snaps(conn, day_str)
            if not day_snaps:
                continue
            
            for a in day_a:
                ts = datetime.fromisoformat(a['timestamp'])
                if ts.hour < 11 or ts.hour >= 14:
                    continue
                if 'Slightly' not in (a['verdict'] or ''):
                    continue
                if (a['signal_confidence'] or 0) < 65:
                    continue
                
                spot = a['spot_price']
                step = 50
                atm = round(spot / step) * step
                
                if 'Bullish' in a['verdict']:
                    opt = 'CE'
                    strike = atm  # ATM call
                else:
                    opt = 'PE'
                    strike = atm  # ATM put
                
                entry = get_premium(day_snaps, ts, strike, opt)
                if not entry or entry < 5:
                    continue
                
                r = simulate_buy(day_snaps, ts, strike, opt, entry, sl_pct, tgt_pct)
                if r:
                    r['day'] = day_str
                    r['strike'] = strike
                    r['opt'] = opt
                    r['entry'] = entry
                    r['verdict'] = a['verdict']
                    r['conf'] = a['signal_confidence']
                    r['spot'] = spot
                    r['time'] = ts.strftime('%H:%M')
                    trades.append(r)
                break
        
        wins = [t for t in trades if t['won']]
        losses = [t for t in trades if not t['won']]
        wr = len(wins) / len(trades) * 100 if trades else 0
        total = sum(t['pnl_pct'] for t in trades)
        aw = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
        al = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
        pf = abs(sum(t['pnl_pct'] for t in wins)) / abs(sum(t['pnl_pct'] for t in losses)) if losses else float('inf')
        tgt_exits = len([t for t in trades if t['exit_reason'] == 'TARGET'])
        sl_exits = len([t for t in trades if t['exit_reason'] == 'SL'])
        eod_exits = len([t for t in trades if t['exit_reason'] == 'EOD'])
        
        print(f"\n{'='*85}")
        print(f"CONFIG: {name}")
        print(f"{'='*85}")
        print(f"Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)} | WR: {wr:.1f}%")
        print(f"Total P&L: {total:+.2f}% | Avg Win: {aw:+.2f}% | Avg Loss: {al:+.2f}% | PF: {pf:.2f}")
        print(f"Exits -- Target: {tgt_exits} | SL: {sl_exits} | EOD: {eod_exits}")
        
        print(f"\n{'Day':<12} {'Time':<6} {'Dir':<4} {'Strike':<7} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Reason':<7}")
        print("-" * 65)
        for t in trades:
            d = 'CE' if t['opt'] == 'CE' else 'PE'
            print(f"{t['day']:<12} {t['time']:<6} {d:<4} {t['strike']:<7} Rs{t['entry']:>5.1f} Rs{t['exit']:>5.1f} {t['pnl_pct']:>+7.2f}% {t['exit_reason']:<7}")
    
    conn.close()

if __name__ == '__main__':
    run()
