import sqlite3, json
conn = sqlite3.connect('oi_tracker.db')
conn.row_factory = sqlite3.Row

# Check today's buying trades
c = conn.cursor()
c.execute("SELECT * FROM trade_setups WHERE DATE(created_at) = DATE('now', 'localtime') ORDER BY created_at DESC")
buys = [dict(r) for r in c.fetchall()]
print("=== BUYING TRADES TODAY ===")
for t in buys:
    print(f"  {t['created_at']} | {t['direction']} {t['strike']} {t['option_type']} | Status: {t['status']}")
    print(f"  Entry: Rs {t['entry_premium']:.2f} | SL: Rs {t['sl_premium']:.2f} | Target: Rs {t['target1_premium']:.2f}")
    print(f"  Verdict: {t['verdict_at_creation']} | Conf: {t['signal_confidence']}%")
    print(f"  Spot: {t['spot_at_creation']} | IV: {t['iv_at_creation']}")
    if t['profit_loss_pct'] is not None:
        print(f"  P&L: {t['profit_loss_pct']:+.2f}%")
    print()

# Check today's selling trades
c.execute("SELECT * FROM sell_trade_setups WHERE DATE(created_at) = DATE('now', 'localtime') ORDER BY created_at DESC")
sells = [dict(r) for r in c.fetchall()]
print("=== SELLING TRADES TODAY ===")
for t in sells:
    print(f"  {t['created_at']} | {t['direction']} {t['strike']} {t['option_type']} | Status: {t['status']}")
    print(f"  Entry: Rs {t['entry_premium']:.2f} | SL: Rs {t['sl_premium']:.2f} | Target: Rs {t['target_premium']:.2f}")
    print(f"  Verdict: {t['verdict_at_creation']} | Conf: {t['signal_confidence']}%")
    print(f"  Spot: {t['spot_at_creation']}")
    if t['profit_loss_pct'] is not None:
        print(f"  P&L: {t['profit_loss_pct']:+.2f}%")
    print()

if not buys and not sells:
    print("No trades today.")

conn.close()
