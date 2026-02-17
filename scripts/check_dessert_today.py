import sqlite3, os
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("""SELECT timestamp, verdict, signal_confidence, iv_skew, max_pain, spot_price
    FROM analysis_history WHERE DATE(timestamp) = '2026-02-17' ORDER BY timestamp""")
rows = [dict(r) for r in c.fetchall()]
print(f"Total analysis rows today: {len(rows)}\n")

print("=== Dessert Filter Check ===")
print("Contra Sniper: Bullish verdict + IV skew < 1 + spot below max pain")
print("Phantom PUT: conf < 50% + IV skew < 0\n")

matches = []
for r in rows:
    v = r['verdict'] or ''
    conf = r['signal_confidence'] or 0
    ivs = r['iv_skew']
    mp = r['max_pain']
    spot = r['spot_price']
    
    contra = 'Bull' in v and ivs is not None and ivs < 1 and mp is not None and spot < mp
    phantom = conf < 50 and ivs is not None and ivs < 0
    
    if contra or phantom:
        tag = 'CONTRA' if contra else 'PHANTOM'
        matches.append(r['timestamp'])
        print(f"  {r['timestamp'][:16]} | {v} ({conf}%) | IV Skew: {ivs} | Spot: {spot:.0f} vs MP: {mp} | ** {tag} MATCH **")

if not matches:
    print("  NO MATCHES FOUND\n")

print(f"\n=== All rows summary ===")
print(f"{'Time':<17} {'Verdict':<22} {'Conf':>5} {'IVSkew':>7} {'Spot':>8} {'MaxPain':>8} {'vs MP':<6}")
print("-" * 80)
for r in rows:
    v = r['verdict'] or ''
    conf = r['signal_confidence'] or 0
    ivs = r['iv_skew']
    mp = r['max_pain'] or 0
    spot = r['spot_price']
    above = 'ABOVE' if spot > mp else 'BELOW'
    print(f"{r['timestamp'][:16]:<17} {v:<22} {conf:>5.1f} {str(ivs):>7} {spot:>8.0f} {mp:>8} {above:<6}")

# Also check dessert trades table
c.execute("SELECT * FROM dessert_trades WHERE DATE(created_at) = '2026-02-17'")
dt = c.fetchall()
print(f"\nDessert trades today: {len(dt)}")
for t in dt:
    print(f"  {dict(t)}")
conn.close()
