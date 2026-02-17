import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_connection
from datetime import datetime
with get_connection() as conn:
    c = conn.cursor()
    c.execute("""UPDATE trade_setups SET 
        status = 'WON',
        resolved_at = ?,
        exit_premium = 105.35,
        hit_target = 1,
        hit_sl = 0,
        profit_loss_pct = 22.0,
        profit_loss_points = 19.0
        WHERE id = 20 AND status = 'ACTIVE'
    """, (datetime.now().isoformat(),))
    conn.commit()
    print(f"Updated {c.rowcount} row(s) — Trade 20 marked as WON")
