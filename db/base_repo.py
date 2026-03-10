"""Base repository with common DB helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Callable, Generator, List, Optional

from db.connection import get_connection as _default_conn


class BaseRepository:
    """Provides shared _execute / _fetch helpers.

    Parameters
    ----------
    conn_factory : callable | None
        A context-manager factory returning a sqlite3.Connection.
        Defaults to db.connection.get_connection.  Pass a custom one
        in tests (e.g. returning an in-memory connection).
    """

    def __init__(self, conn_factory: Callable | None = None) -> None:
        self._conn_factory = conn_factory or _default_conn

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        with self._conn_factory() as conn:
            yield conn

    def _execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a write statement (INSERT / UPDATE / DELETE)."""
        with self._connection() as conn:
            conn.execute(sql, params)
            conn.commit()

    def _execute_returning_id(self, sql: str, params: tuple = ()) -> int:
        """Execute an INSERT and return lastrowid."""
        with self._connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid

    def _fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Fetch a single row as dict, or None."""
        with self._connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _fetch_all(self, sql: str, params: tuple = ()) -> List[dict]:
        """Fetch all rows as list of dicts."""
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
