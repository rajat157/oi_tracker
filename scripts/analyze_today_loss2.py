"""Compare OI dynamics between winning and losing Contra Sniper trades."""
import sqlite3
from datetime import datetime

DB = 'oi_tracker.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

days = {'2026-02-02': 'WIN', '2026-02-13': 'WIN', '2026-02-16': 'LOSS'}
for day, result in days.items():
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, spot_price, verdict, signal_confidence, 
               call_oi_change, put_oi_change, total_call_oi, total_put_oi,
               max_pain, iv_skew, vix, futures_basis
        FROM analysis_history WHERE DATE(timestamp) = ? ORDER BY timestamp
    """, (day,))
    rows = [dict(r) for r in c.fetchall()]
    
    print(f"\n{'='*70}")
    print(f"  {day} — {result}")
    print(f"{'='*70}")
    
    # Key: what happened to spot AFTER entry?
    # Did it drop (good for PUT) or keep rising (bad)?
    
    # Get full day spot range
    spots = [r['spot_price'] for r in rows if r['spot_price']]
    if spots:
        print(f"  Day range: {min(spots):.2f} — {max(spots):.2f} (range: {max(spots)-min(spots):.0f} pts)")
    
    # Put OI vs Call OI buildup
    put_ois = [r['put_oi_change'] for r in rows if r['put_oi_change']]
    call_ois = [r['call_oi_change'] for r in rows if r['call_oi_change']]
    if put_ois and call_ois:
        print(f"  Put OI changes: {min(put_ois):,.0f} to {max(put_ois):,.0f}")
        print(f"  Call OI changes: {min(call_ois):,.0f} to {max(call_ois):,.0f}")
        print(f"  Put/Call OI ratio: {max(put_ois)/max(call_ois):.2f}x")
    
    # How much did spot actually fall from entry?
    entry_idx = None
    for i, r in enumerate(rows):
        ts = datetime.fromisoformat(r['timestamp'])
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30):
            continue
        v = r['verdict'] or ''
        ivs = r['iv_skew'] or 0
        mp = r['max_pain'] or 0
        atm = round(r['spot_price'] / 50) * 50
        if 'Bull' in v and ivs < 1 and mp > 0 and atm < mp:
            entry_idx = i
            entry_spot = r['spot_price']
            break
    
    if entry_idx is not None:
        after = rows[entry_idx+1:]
        if after:
            post_spots = [r['spot_price'] for r in after]
            max_drop = min((s - entry_spot) / entry_spot * 100 for s in post_spots)
            max_rise = max((s - entry_spot) / entry_spot * 100 for s in post_spots)
            eod_spot = post_spots[-1]
            eod_move = (eod_spot - entry_spot) / entry_spot * 100
            print(f"\n  Entry spot: {entry_spot:.2f}")
            print(f"  Max drop after entry: {max_drop:+.3f}%")
            print(f"  Max rise after entry: {max_rise:+.3f}%")
            print(f"  EOD move: {eod_move:+.3f}%")
            print(f"  Verdict: {'SPOT FELL (good for PUT)' if eod_move < -0.1 else 'SPOT HELD/ROSE (bad for PUT)'}")

conn.close()
