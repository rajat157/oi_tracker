"""The real difference: spot behavior after entry."""
import sqlite3
from datetime import datetime

DB = 'oi_tracker.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

days = {'2026-02-02': 'WIN', '2026-02-13': 'WIN', '2026-02-16': 'LOSS'}

for day, result in days.items():
    c = conn.cursor()
    # Get all spot data
    c.execute("""
        SELECT timestamp, spot_price, verdict, signal_confidence, max_pain
        FROM analysis_history WHERE DATE(timestamp) = ? ORDER BY timestamp
    """, (day,))
    rows = [dict(r) for r in c.fetchall()]
    
    print(f"\n{'='*60}")
    print(f"  {day} ({result})")
    print(f"{'='*60}")
    
    # Track where spot went relative to max pain through the day
    for r in rows:
        ts = datetime.fromisoformat(r['timestamp'])
        if ts.hour < 9:
            continue
        if ts.minute % 30 != 0 and ts.hour != 15:
            continue
        mp = r['max_pain'] or 0
        dist = r['spot_price'] - mp
        bar = '+' * int(max(0, dist/10)) + '-' * int(max(0, -dist/10))
        print(f"  {ts.strftime('%H:%M')} spot={r['spot_price']:.0f} mp={mp:.0f} gap={dist:+.0f} {bar}")

conn.close()
