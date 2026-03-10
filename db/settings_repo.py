"""Repository for key-value settings (settings table)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from db.connection import get_connection


def get_setting(key: str) -> Optional[str]:
    """Get a setting value by key. Returns None if not found."""
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else None


def set_setting(key: str, value: str) -> None:
    """Set a setting value (upsert)."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, value, datetime.now().isoformat()))
        conn.commit()
