"""Generic trade repository — works across all 6 trade tables."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from db.base_repo import BaseRepository


class TradeRepository(BaseRepository):
    """Generic CRUD that operates on any trade table by name.

    This avoids duplicating the same SQL patterns across 6 tracker modules.
    """

    def init_table(self, ddl: str, indexes: List[str] | None = None) -> None:
        """Execute CREATE TABLE DDL (and optional indexes)."""
        with self._connection() as conn:
            conn.executescript(ddl)
            for idx in indexes or []:
                conn.execute(idx)
            conn.commit()

    def insert_trade(self, table: str, **columns) -> int:
        """Dynamic-column INSERT into *table*. Returns lastrowid."""
        cols = ", ".join(columns.keys())
        placeholders = ", ".join("?" for _ in columns)
        return self._execute_returning_id(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
            tuple(columns.values()),
        )

    def get_active(self, table: str) -> Optional[dict]:
        """Return the currently ACTIVE trade from *table*, or None."""
        return self._fetch_one(
            f"SELECT * FROM {table} WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1"
        )

    def get_pending(self, table: str) -> Optional[dict]:
        """Return the currently PENDING trade from *table*, or None."""
        return self._fetch_one(
            f"SELECT * FROM {table} WHERE status = 'PENDING' ORDER BY id DESC LIMIT 1"
        )

    def get_todays_trades(self, table: str, date_str: Optional[str] = None) -> List[dict]:
        """Return all trades created today (or on *date_str*)."""
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        return self._fetch_all(
            f"SELECT * FROM {table} WHERE DATE(created_at) = ? ORDER BY id DESC",
            (date_str,),
        )

    def update_trade(self, table: str, trade_id: int, **kwargs) -> None:
        """Update arbitrary columns on a trade row."""
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = tuple(kwargs.values()) + (trade_id,)
        self._execute(
            f"UPDATE {table} SET {set_clause} WHERE id = ?",
            values,
        )

    def get_stats(self, table: str, lookback_days: int = 30) -> Dict:
        """Generic stats: total, wins, losses, win_rate, avg_win, avg_loss, total_pnl."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = self._fetch_all(
            f"""SELECT status, profit_loss_pct FROM {table}
                WHERE status IN ('WON', 'LOST', 'EXPIRED')
                AND created_at >= ?""",
            (cutoff,),
        )
        wins = [r for r in rows if r["status"] == "WON"]
        losses = [r for r in rows if r["status"] in ("LOST", "EXPIRED")]
        total = len(rows)
        return {
            "total": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / total * 100, 1) if total else 0,
            "avg_win": round(sum(r["profit_loss_pct"] or 0 for r in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(r["profit_loss_pct"] or 0 for r in losses) / len(losses), 2) if losses else 0,
            "total_pnl": round(sum(r["profit_loss_pct"] or 0 for r in rows), 2),
        }

    def get_active_or_pending(self, table: str) -> Optional[dict]:
        """Return the ACTIVE or PENDING trade (for Iron Pulse lifecycle)."""
        return self._fetch_one(
            f"SELECT * FROM {table} WHERE status IN ('ACTIVE', 'PENDING') "
            f"ORDER BY id DESC LIMIT 1"
        )

    def get_last_resolved(self, table: str) -> Optional[dict]:
        """Return the most recently resolved trade (for cooldown checks)."""
        return self._fetch_one(
            f"SELECT * FROM {table} "
            f"WHERE status IN ('WON', 'LOST', 'EXPIRED', 'CANCELLED') "
            f"ORDER BY resolved_at DESC LIMIT 1"
        )

    def get_history(self, table: str, limit: int = 50, offset: int = 0) -> List[dict]:
        """Return trade history ordered by newest first."""
        return self._fetch_all(
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
