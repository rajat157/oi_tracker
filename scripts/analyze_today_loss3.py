"""Check Put OI / Call OI ratio at entry for Contra Sniper trades."""
import sqlite3
from datetime import datetime

DB = 'oi_tracker.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

days = {'2026-02-02': 'WIN', '2026-02-13': 'WIN', '2026-02-16': 'LOSS'}

print(f"{'Day':<14} {'Result':<6} {'PutOI':>10} {'CallOI':>10} {'P/C Ratio':>10} {'Spot->MP':>10} {'IV Skew':>8}")
print("-" * 70)

for day, result in days.items():
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, verdict, signal_confidence,
               call_oi_change, put_oi_change, max_pain, iv_skew
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
            poi = r['put_oi_change'] or 0
            coi = r['call_oi_change'] or 0
            ratio = poi / coi if coi > 0 else 999
            dist = (atm - mp)
            print(f"{day:<14} {result:<6} {poi:>10,.0f} {coi:>10,.0f} {ratio:>10.2f} {dist:>10.0f} {ivs:>8.2f}")
            break

conn.close()

print("\n--- Key Insight ---")
print("Feb 16 had MASSIVE Put OI buildup (2.12x) at entry = PUT writers were very active")
print("When Put writers dominate, they're SELLING puts = bullish positioning")
print("Contra Sniper bets AGAINST bulls, but this time the OI confirmed bulls were RIGHT")
print("Potential filter: Put/Call OI ratio at entry < 1.5 (or negative IV skew required)")
