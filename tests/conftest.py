"""Global test fixtures.

Two leak protections live here:

1. _clean_global_event_bus: clears the EventBus after every test so leaked
   subscribers (e.g. AlertBroker) can't trigger real Telegram alerts in
   unrelated tests.

2. _isolate_test_db: redirects ALL DB writes to a temporary file for the
   entire test session. Without this, every log line a test produces
   (via core.logger.OILogger._save_to_db → db.legacy.save_log) writes to
   the production oi_tracker.db. A single full test run was observed to
   add ~1000 polluted rows to system_logs.

   The codebase has TWO independent DB_PATH constants — db.connection.DB_PATH
   (the new one) and db.legacy.DB_PATH (the legacy one with its own
   get_connection). Both must be patched. We do it at session start, before
   any test code runs, so connections opened during test imports also see
   the patched path.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from core.events import event_bus


@pytest.fixture(scope="session", autouse=True)
def _isolate_test_db(tmp_path_factory):
    """Point all production DB writes at a temp file for the test session.

    Patches:
        db.connection.DB_PATH       (used by db/*_repo.py via db.connection.get_connection)
        db.legacy.DB_PATH           (used by db.legacy.save_log + ~30 other legacy fns)

    The legacy schema is initialized in the temp file so save_log() doesn't
    fail with 'no such table: system_logs'. Tests that need an empty trade
    table use their own in-memory connection via the mem_conn fixture.
    """
    test_db = tmp_path_factory.mktemp("oi_test_db") / "test_oi_tracker.db"
    test_db_str = str(test_db)

    # Patch BOTH module-level DB_PATH constants. Each module's
    # get_connection() reads its own module-global at call time, so the
    # patch propagates without further intervention.
    import db.connection
    import db.legacy
    original_conn_db_path = db.connection.DB_PATH
    original_legacy_db_path = db.legacy.DB_PATH
    db.connection.DB_PATH = test_db_str
    db.legacy.DB_PATH = test_db_str

    # Materialize the production schema in the temp file so logger writes
    # and any test that exercises legacy SQL functions don't fail with
    # missing-table errors.
    try:
        db.legacy.init_db()
    except Exception as e:
        # Best-effort — some legacy init paths may fail in a fresh DB.
        # Tests that need a specific table will still fail loudly.
        print(f"[conftest] Warning: db.legacy.init_db() raised: {e}")

    yield test_db_str

    # Restore for safety even though the process is about to exit
    db.connection.DB_PATH = original_conn_db_path
    db.legacy.DB_PATH = original_legacy_db_path


@pytest.fixture(autouse=True)
def _clean_global_event_bus():
    """Clear global event_bus after every test to prevent cross-test leaks."""
    yield
    event_bus.clear()
