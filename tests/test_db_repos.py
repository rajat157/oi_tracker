"""Tests for db/ repositories using in-memory SQLite."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime

import pytest

from db.base_repo import BaseRepository
from db.trade_repo import TradeRepository
from db.schema import (
    SCALP_TRADES_DDL,
    ALL_TRADE_SCHEMAS,
)


# --- In-memory DB fixture ---

def _create_test_schema(conn: sqlite3.Connection):
    """Create a minimal trade table for testing."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS test_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            direction TEXT,
            strike INTEGER,
            option_type TEXT,
            entry_premium REAL,
            sl_premium REAL,
            target_premium REAL,
            status TEXT DEFAULT 'ACTIVE',
            profit_loss_pct REAL,
            resolved_at TEXT,
            exit_premium REAL,
            exit_reason TEXT
        )
    """)
    conn.commit()


@pytest.fixture
def mem_conn():
    """Yield a shared in-memory connection wrapped in a factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_test_schema(conn)

    @contextmanager
    def factory():
        yield conn

    yield factory
    conn.close()


# --- BaseRepository tests ---

class TestBaseRepository:
    def test_execute_and_fetch(self, mem_conn):
        repo = BaseRepository(conn_factory=mem_conn)
        repo._execute(
            "INSERT INTO test_trades (created_at, direction, strike, status) VALUES (?, ?, ?, ?)",
            ("2025-01-01 12:00:00", "BUY_CALL", 24500, "ACTIVE"),
        )
        row = repo._fetch_one("SELECT * FROM test_trades WHERE strike = ?", (24500,))
        assert row["direction"] == "BUY_CALL"

    def test_fetch_all(self, mem_conn):
        repo = BaseRepository(conn_factory=mem_conn)
        repo._execute(
            "INSERT INTO test_trades (created_at, direction, status) VALUES (?, ?, ?)",
            ("2025-01-01", "BUY_CALL", "WON"),
        )
        repo._execute(
            "INSERT INTO test_trades (created_at, direction, status) VALUES (?, ?, ?)",
            ("2025-01-01", "BUY_PUT", "LOST"),
        )
        rows = repo._fetch_all("SELECT * FROM test_trades")
        assert len(rows) == 2

    def test_fetch_one_none(self, mem_conn):
        repo = BaseRepository(conn_factory=mem_conn)
        assert repo._fetch_one("SELECT * FROM test_trades WHERE id = 999") is None

    def test_execute_returning_id(self, mem_conn):
        repo = BaseRepository(conn_factory=mem_conn)
        rid = repo._execute_returning_id(
            "INSERT INTO test_trades (created_at, direction, status) VALUES (?, ?, ?)",
            ("2025-01-01", "BUY_CALL", "ACTIVE"),
        )
        assert isinstance(rid, int)
        assert rid > 0


# --- TradeRepository tests ---

class TestTradeRepository:
    def _insert_trade(self, repo, mem_conn, status="ACTIVE", date="2025-01-15",
                      pnl=None):
        repo._execute(
            """INSERT INTO test_trades
               (created_at, direction, strike, option_type, entry_premium,
                sl_premium, target_premium, status, profit_loss_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"{date} 12:00:00", "BUY_CALL", 24500, "CE", 150.0, 120.0, 183.0,
             status, pnl),
        )

    def test_get_active(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn, status="ACTIVE")
        active = repo.get_active("test_trades")
        assert active is not None
        assert active["status"] == "ACTIVE"

    def test_get_active_none(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        assert repo.get_active("test_trades") is None

    def test_get_pending(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn, status="PENDING")
        pending = repo.get_pending("test_trades")
        assert pending is not None
        assert pending["status"] == "PENDING"

    def test_get_todays_trades(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn, date="2025-03-10")
        self._insert_trade(repo, mem_conn, date="2025-03-09")
        trades = repo.get_todays_trades("test_trades", "2025-03-10")
        assert len(trades) == 1

    def test_update_trade(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn)
        active = repo.get_active("test_trades")
        repo.update_trade("test_trades", active["id"],
                          status="WON", profit_loss_pct=22.0,
                          exit_reason="TARGET_HIT")
        updated = repo._fetch_one("SELECT * FROM test_trades WHERE id = ?",
                                  (active["id"],))
        assert updated["status"] == "WON"
        assert updated["profit_loss_pct"] == 22.0

    def test_get_stats(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        self._insert_trade(repo, mem_conn, status="WON", pnl=22.0, date=today)
        self._insert_trade(repo, mem_conn, status="WON", pnl=18.0, date=today)
        self._insert_trade(repo, mem_conn, status="LOST", pnl=-20.0, date=today)
        stats = repo.get_stats("test_trades")
        assert stats["total"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["win_rate"] == pytest.approx(66.7, abs=0.1)
        assert stats["total_pnl"] == 20.0

    def test_get_stats_empty(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        stats = repo.get_stats("test_trades")
        assert stats["total"] == 0
        assert stats["win_rate"] == 0

    def test_get_history(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn, status="WON", pnl=10.0)
        self._insert_trade(repo, mem_conn, status="LOST", pnl=-15.0)
        history = repo.get_history("test_trades", limit=10)
        assert len(history) == 2
        # newest first
        assert history[0]["id"] > history[1]["id"]

    def test_update_empty_kwargs(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn)
        # Should not raise
        repo.update_trade("test_trades", 1)

    def test_init_table(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        ddl = """
        CREATE TABLE IF NOT EXISTS new_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
        """
        repo.init_table(ddl)
        # Table should exist and be writable
        repo._execute("INSERT INTO new_table (name) VALUES (?)", ("test",))
        row = repo._fetch_one("SELECT * FROM new_table WHERE name = 'test'")
        assert row is not None

    def test_init_table_with_indexes(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        ddl = """
        CREATE TABLE IF NOT EXISTS idx_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT
        )
        """
        indexes = ["CREATE INDEX IF NOT EXISTS idx_status ON idx_table(status)"]
        repo.init_table(ddl, indexes)
        repo._execute("INSERT INTO idx_table (status) VALUES (?)", ("ACTIVE",))
        row = repo._fetch_one("SELECT * FROM idx_table WHERE status = 'ACTIVE'")
        assert row is not None

    def test_insert_trade(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        trade_id = repo.insert_trade(
            "test_trades",
            created_at="2025-01-15 12:00:00",
            direction="BUY_CALL",
            strike=24500,
            option_type="CE",
            entry_premium=150.0,
            sl_premium=120.0,
            target_premium=183.0,
            status="ACTIVE",
        )
        assert isinstance(trade_id, int)
        assert trade_id > 0
        row = repo._fetch_one("SELECT * FROM test_trades WHERE id = ?", (trade_id,))
        assert row["direction"] == "BUY_CALL"
        assert row["strike"] == 24500

    def test_get_active_or_pending_active(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn, status="ACTIVE")
        result = repo.get_active_or_pending("test_trades")
        assert result is not None
        assert result["status"] == "ACTIVE"

    def test_get_active_or_pending_pending(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn, status="PENDING")
        result = repo.get_active_or_pending("test_trades")
        assert result is not None
        assert result["status"] == "PENDING"

    def test_get_active_or_pending_none(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn, status="WON")
        assert repo.get_active_or_pending("test_trades") is None

    def test_get_last_resolved(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        repo._execute(
            """INSERT INTO test_trades
               (created_at, direction, status, resolved_at, profit_loss_pct)
               VALUES (?, ?, ?, ?, ?)""",
            ("2025-01-15 12:00:00", "BUY_CALL", "WON",
             "2025-01-15 13:00:00", 22.0),
        )
        repo._execute(
            """INSERT INTO test_trades
               (created_at, direction, status, resolved_at, profit_loss_pct)
               VALUES (?, ?, ?, ?, ?)""",
            ("2025-01-15 12:30:00", "BUY_PUT", "LOST",
             "2025-01-15 13:30:00", -20.0),
        )
        last = repo.get_last_resolved("test_trades")
        assert last is not None
        assert last["direction"] == "BUY_PUT"  # most recent resolved_at

    def test_get_last_resolved_none(self, mem_conn):
        repo = TradeRepository(conn_factory=mem_conn)
        self._insert_trade(repo, mem_conn, status="ACTIVE")
        assert repo.get_last_resolved("test_trades") is None


class TestSchema:
    """Verify all DDL constants from db/schema.py create valid tables."""

    @pytest.mark.parametrize("tracker_type", list(ALL_TRADE_SCHEMAS.keys()))
    def test_all_schemas_create_valid_tables(self, tracker_type):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        @contextmanager
        def factory():
            yield conn

        repo = TradeRepository(conn_factory=factory)
        ddl, indexes = ALL_TRADE_SCHEMAS[tracker_type]
        repo.init_table(ddl, indexes)

        # Verify the table is queryable
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert len(tables) > 0

        conn.close()

    def test_insert_into_schema_table(self):
        """Verify insert_trade works with a real DDL-created table."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        @contextmanager
        def factory():
            yield conn

        repo = TradeRepository(conn_factory=factory)
        repo.init_table(SCALP_TRADES_DDL)

        trade_id = repo.insert_trade(
            "scalp_trades",
            created_at="2025-01-15 12:00:00",
            direction="BUY_CALL",
            strike=24500,
            option_type="CE",
            entry_premium=150.0,
            sl_premium=112.5,
            target_premium=225.0,
            spot_at_creation=24480.0,
            verdict_at_creation="Bulls Winning",
        )
        assert trade_id == 1
        row = repo.get_active("scalp_trades")
        assert row is not None
        assert row["strike"] == 24500

        conn.close()
