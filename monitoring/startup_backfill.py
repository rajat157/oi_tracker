"""Startup backfill for instrument_history.

Runs once at app startup, AFTER Kite authentication succeeds. For each of
the 5 IntradayHunter instruments (NIFTY, BANKNIFTY, SENSEX, HDFCBANK,
KOTAKBANK), it finds the most recent stored 1-min timestamp and fetches
any missing candles up to *yesterday* (we never backfill today — live
candles flow through CandleBuilder).

The IH engine's E2/E3 detectors require yesterday's NIFTY 1-min session,
and the constituent confluence filter needs HDFC/KOTAK as well. Without
this backfill, signals can't fire on the first day after a long gap.

Idempotent: skips instruments that are already up-to-date.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

from core.logger import get_logger
from db.connection import DB_PATH
from db.settings_repo import get_setting

log = get_logger("startup_backfill")

# Mirror the instrument set from scripts/fetch_instrument_history.py
IH_BACKFILL_INSTRUMENTS = {
    "NIFTY":      256265,
    "BANKNIFTY":  260105,
    "SENSEX":     265,
    "HDFCBANK":   341249,
    "KOTAKBANK":  492033,
}

# How many calendar days back to attempt as a hard ceiling. Even if the
# table is completely empty, we won't try to fetch more than this in one
# startup. Use the standalone backfill script for full historical seeding.
MAX_BACKFILL_DAYS = 14

# Kite rate-limit pacing (the historical_data endpoint is the most rate-
# limited; ~3 req/sec is safe).
REQUEST_DELAY = 0.35


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instrument_history (
            label     TEXT NOT NULL,
            interval  TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open      REAL NOT NULL,
            high      REAL NOT NULL,
            low       REAL NOT NULL,
            close     REAL NOT NULL,
            volume    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (label, interval, timestamp)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_inst_hist_lookup
        ON instrument_history(label, interval, timestamp)
    """)
    conn.commit()


def _last_stored_date(conn: sqlite3.Connection, label: str) -> Optional[datetime]:
    row = conn.execute(
        "SELECT MAX(timestamp) FROM instrument_history "
        "WHERE label = ? AND interval = '1min'",
        (label,),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _insert_rows(conn: sqlite3.Connection, label: str, rows: list) -> int:
    if not rows:
        return 0
    inserted = 0
    cur = conn.cursor()
    for row in rows:
        ts = row["date"].strftime("%Y-%m-%d %H:%M:%S")
        try:
            cur.execute(
                "INSERT OR IGNORE INTO instrument_history "
                "(label, interval, timestamp, open, high, low, close, volume) "
                "VALUES (?, '1min', ?, ?, ?, ?, ?, ?)",
                (label, ts, row["open"], row["high"], row["low"],
                 row["close"], row.get("volume", 0)),
            )
            if cur.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return inserted


def backfill_recent_history() -> dict:
    """Fetch missing 1-min instrument_history from the last stored row → yesterday.

    Caps the gap at MAX_BACKFILL_DAYS to keep startup fast. For longer gaps
    use scripts/fetch_instrument_history.py manually.

    Returns:
        Per-label summary dict: {label: {inserted: int, from: str, to: str}}.
    """
    api_key = os.environ.get("KITE_API_KEY", "")
    if not api_key:
        log.error("KITE_API_KEY missing — cannot backfill")
        return {}

    token = get_setting("kite_access_token")
    if not token:
        log.error("No Kite access token in DB — auth must run first")
        return {}

    # Lazy import so this module doesn't pull in kiteconnect at import time
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token)

    today = datetime.now().date()
    yesterday_end = datetime.combine(today, datetime.min.time()) - timedelta(seconds=1)
    floor = datetime.combine(today - timedelta(days=MAX_BACKFILL_DAYS), datetime.min.time())

    summary: dict = {}
    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_table(conn)

        for label, kite_token in IH_BACKFILL_INSTRUMENTS.items():
            last_ts = _last_stored_date(conn, label)
            if last_ts is None:
                from_dt = floor
                log.info(f"{label}: empty table, seeding {MAX_BACKFILL_DAYS}d")
            else:
                from_dt = last_ts + timedelta(seconds=1)
                if from_dt < floor:
                    from_dt = floor

            if from_dt >= yesterday_end:
                log.info(f"{label}: up to date (last={last_ts})")
                summary[label] = {"inserted": 0, "from": None, "to": None}
                continue

            try:
                rows = kite.historical_data(
                    kite_token, from_dt, yesterday_end, "minute"
                )
                inserted = _insert_rows(conn, label, rows)
                summary[label] = {
                    "inserted": inserted,
                    "from": from_dt.strftime("%Y-%m-%d %H:%M"),
                    "to": yesterday_end.strftime("%Y-%m-%d %H:%M"),
                }
                log.info(
                    f"{label}: backfilled",
                    fetched=len(rows), inserted=inserted,
                    from_dt=from_dt.strftime("%Y-%m-%d"),
                    to_dt=yesterday_end.strftime("%Y-%m-%d"),
                )
            except Exception as e:
                log.error(f"{label}: backfill failed", error=str(e))
                summary[label] = {"inserted": 0, "error": str(e)}

            time.sleep(REQUEST_DELAY)
    finally:
        conn.close()

    log.info("instrument_history backfill complete",
             total_inserted=sum(s.get("inserted", 0) for s in summary.values()))
    return summary
