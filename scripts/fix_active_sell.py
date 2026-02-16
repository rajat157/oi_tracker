import sqlite3
conn = sqlite3.connect('oi_tracker.db')
c = conn.cursor()

# Migration: add columns if missing
for col, defn in [("target2_premium", "REAL"), ("t1_hit", "INTEGER DEFAULT 0"), ("t1_hit_at", "DATETIME")]:
    try:
        c.execute(f"ALTER TABLE sell_trade_setups ADD COLUMN {col} {defn}")
        print(f"Added column {col}")
    except:
        print(f"Column {col} already exists")

# Update active trade's target2
c.execute("SELECT id, entry_premium, target_premium, target2_premium FROM sell_trade_setups WHERE status='ACTIVE'")
row = c.fetchone()
if row:
    t2 = round(row[1] * 0.5, 2)
    c.execute("UPDATE sell_trade_setups SET target2_premium = ? WHERE id = ?", (t2, row[0]))
    conn.commit()
    print(f"Active trade id={row[0]}: entry={row[1]}, T1={row[2]}, T2={t2}")
else:
    print("No active sell trade")
conn.close()
