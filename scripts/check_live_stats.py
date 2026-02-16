import sqlite3
conn = sqlite3.connect('oi_tracker.db')
c = conn.cursor()

# Only count WON/LOST (not EXPIRED/CANCELLED which are non-trades)
for status_set, label in [("('WON','LOST')", "WON/LOST only"), ("('WON','LOST','EXPIRED','CANCELLED')", "All resolved")]:
    c.execute(f"SELECT COUNT(*), SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) FROM trade_setups WHERE status IN {status_set}")
    total, wins = c.fetchone()
    losses = total - (wins or 0)
    wr = (wins or 0)/total*100 if total else 0
    print(f"BUYING ({label}) - Total: {total}, Wins: {wins}, Losses: {losses}, WR: {wr:.1f}%")

print(f"\n{'Date':<12} {'Dir':<10} {'Strike':<7} {'Status':<10} {'P&L':>8} {'Entry':>7} {'Activ':>7} {'Exit':>7} {'HitSL':>5} {'HitTgt':>6}")
c.execute("SELECT DATE(created_at), direction, strike, status, profit_loss_pct, entry_premium, activation_premium, exit_premium, hit_sl, hit_target FROM trade_setups WHERE status IN ('WON','LOST','EXPIRED','CANCELLED') ORDER BY created_at")
for r in c.fetchall():
    print(f"{r[0]:<12} {r[1]:<10} {r[2]:<7} {r[3]:<10} {r[4] or 0:>+7.1f}% {r[5] or 0:>7.2f} {r[6] or 0:>7.2f} {r[7] or 0:>7.2f} {r[8] or 0:>5} {r[9] or 0:>6}")

# Count by status
print("\nBy status:")
c.execute("SELECT status, COUNT(*) FROM trade_setups GROUP BY status ORDER BY COUNT(*) DESC")
for r in c.fetchall(): print(f"  {r[0]}: {r[1]}")

conn.close()
