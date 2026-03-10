"""Database connection management.

Provides get_connection() context manager and init_db() for schema setup.
These are thin wrappers — init_db() delegates to the existing database.init_db()
until the full migration is complete.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

DB_PATH = "oi_tracker.db"


@contextmanager
def get_connection(db_path: str | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a sqlite3 Connection with Row factory.

    Parameters
    ----------
    db_path : str | None
        Override path (useful for tests with `:memory:`).
    """
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Initialize the database schema.

    Delegates to the existing database.init_db() to avoid duplication.
    When Phase 4 removes database.py, the full DDL will live here.
    """
    from database import init_db as _legacy_init
    _legacy_init()
