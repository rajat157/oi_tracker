"""Check scalper trade history."""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# List tables
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("Tables:", tables)

# Check scalp_trades
if 'scalp_trades' in tables:
    c.execute("SELECT * FROM scalp_trades ORDER BY created_at")
    rows = c.fetchall()
    print(f"\nscalp_trades: {len(rows)} total")
    print(f"\n{'ID':<5} {'Date':<20} {'Dir':<10} {'Strike':>7} {'Entry':>7} {'SL':>7} {'Target':>7} {'Status':<12} {'P&L':>8}")
    print("-" * 100)
    for r in rows:
        d = dict(r)
        date = d.get('created_at', '')[:19]
        direction = d.get('direction', '')
        strike = d.get('strike', 0)
        entry = d.get('entry_premium', 0) or 0
        sl = d.get('sl_premium', 0) or 0
        target = d.get('target_premium', 0) or 0
        status = d.get('status', '')
        pnl = d.get('profit_loss_pct', 0) or 0
        tid = d.get('id', '')
        print(f"{tid:<5} {date:<20} {direction:<10} {strike:>7} {entry:>7.1f} {sl:>7.1f} {target:>7.1f} {status:<12} {pnl:>+7.1f}%")

conn.close()
