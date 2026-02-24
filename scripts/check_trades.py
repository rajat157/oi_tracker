"""Check actual trade history and compare with backtest."""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# List tables
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("Tables:", tables)

# Check trade_setups
if 'trade_setups' in tables:
    c.execute("PRAGMA table_info(trade_setups)")
    cols = [r[1] for r in c.fetchall()]
    print(f"\ntrade_setups columns: {cols}")
    
    c.execute("SELECT * FROM trade_setups ORDER BY created_at")
    rows = c.fetchall()
    print(f"\ntrade_setups: {len(rows)} total")
    print(f"\n{'ID':<5} {'Date':<20} {'Dir':<10} {'Strike':>7} {'Entry':>7} {'SL':>7} {'T1':>7} {'Status':<12} {'Result':<8} {'P&L':>8}")
    print("-" * 110)
    for r in rows:
        d = dict(r)
        date = d.get('created_at', '')[:19]
        direction = d.get('direction', '')
        strike = d.get('strike_price', 0)
        entry = d.get('entry_premium', 0) or 0
        sl = d.get('sl_premium', 0) or 0
        t1 = d.get('target1_premium', 0) or d.get('target_premium', 0) or 0
        status = d.get('status', '')
        result = d.get('result', '') or ''
        pnl = d.get('pnl_pct', 0) or 0
        tid = d.get('id', '')
        print(f"{tid:<5} {date:<20} {direction:<10} {strike:>7} {entry:>7.1f} {sl:>7.1f} {t1:>7.1f} {status:<12} {result:<8} {pnl:>+7.1f}%")

# Also check if there's a separate iron_pulse or strategy table
for t in tables:
    if 'iron' in t.lower() or 'strategy' in t.lower() or 'dessert' in t.lower():
        c.execute(f"SELECT COUNT(*) FROM [{t}]")
        print(f"\n{t}: {c.fetchone()[0]} rows")

conn.close()
