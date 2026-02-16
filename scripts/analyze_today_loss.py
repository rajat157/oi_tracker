"""Analyze why Feb 16 Contra Sniper lost vs Feb 2 and Feb 13 wins."""
import sqlite3
from datetime import datetime

DB = 'oi_tracker.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

days = ['2026-02-02', '2026-02-13', '2026-02-16']
for day in days:
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, verdict, signal_confidence, vix, iv_skew, 
               max_pain, call_oi_change, put_oi_change, futures_oi_change, futures_basis
        FROM analysis_history WHERE DATE(timestamp) = ? ORDER BY timestamp
    """, (day,))
    rows = [dict(r) for r in c.fetchall()]
    
    for r in rows:
        ts = datetime.fromisoformat(r['timestamp'])
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30):
            continue
        v = r['verdict'] or ''
        ivs = r['iv_skew'] or 0
        mp = r['max_pain'] or 0
        atm = round(r['spot_price'] / 50) * 50
        if 'Bull' in v and ivs < 1 and mp > 0 and atm < mp:
            result = 'WIN' if day != '2026-02-16' else 'LOSS'
            print(f"\n{'='*70}")
            print(f"  {day} ({result})")
            print(f"{'='*70}")
            print(f"  Entry: {ts.strftime('%H:%M')}")
            print(f"  Verdict: {v} ({r['signal_confidence']:.0f}%)")
            print(f"  Spot: {r['spot_price']:.2f} | ATM: {atm} | Max Pain: {mp:.0f}")
            print(f"  IV Skew: {ivs:.2f} | VIX: {r['vix']:.1f}")
            print(f"  Call OI chg: {r['call_oi_change']:,.0f} | Put OI chg: {r['put_oi_change']:,.0f}")
            print(f"  Futures OI chg: {r['futures_oi_change']:,.0f} | Basis: {r['futures_basis']:.2f}")
            
            # Spot trajectory 
            later = [x for x in rows if datetime.fromisoformat(x['timestamp']) > ts]
            if later:
                print(f"  \n  Spot after entry:")
                for x in later[:12]:
                    xt = datetime.fromisoformat(x['timestamp']).strftime('%H:%M')
                    mv = (x['spot_price'] - r['spot_price']) / r['spot_price'] * 100
                    print(f"    {xt}: {x['spot_price']:.2f} ({mv:+.3f}%) -- {x['verdict']} ({x['signal_confidence']:.0f}%)")
            
            # Check: did verdict STRENGTHEN after entry?
            bull_count = sum(1 for x in later[:6] if 'Bull' in (x['verdict'] or ''))
            print(f"\n  Bulls in next 6 readings: {bull_count}/6")
            
            # Spot direction at entry
            prior = [x for x in rows if datetime.fromisoformat(x['timestamp']) < ts]
            if len(prior) >= 3:
                recent = prior[-3:]
                spot_30m_trend = (recent[-1]['spot_price'] - recent[0]['spot_price']) / recent[0]['spot_price'] * 100
                print(f"  Spot trend pre-entry (last 3 readings): {spot_30m_trend:+.3f}%")
            break

conn.close()
