"""Migrate v1 SQLite data to v2 PostgreSQL.

Usage:
    uv run python scripts/migrate_sqlite.py --sqlite-path ../../oi_tracker.db
    uv run python scripts/migrate_sqlite.py --sqlite-path ../../oi_tracker.db --pg-url postgresql://nifty:pass@localhost/nifty_oi
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

IST = timezone(timedelta(hours=5, minutes=30))
BATCH_SIZE = 1000


def _add_tz(dt_str: str | None) -> str | None:
    """Convert a naive datetime string to IST-aware ISO format."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.isoformat()
    except (ValueError, TypeError):
        return dt_str


def _parse_json(text: str | None) -> dict | None:
    """Safely parse a JSON string, returning None on failure."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _bool(val) -> bool:
    """Convert SQLite boolean (0/1/None) to Python bool."""
    return bool(val) if val is not None else False


def _get_sqlite_columns(cursor: sqlite3.Cursor, table: str) -> list[str]:
    """Get column names for a SQLite table."""
    cursor.execute(f"PRAGMA table_info({table})")  # noqa: S608
    return [row[1] for row in cursor.fetchall()]


def migrate_oi_snapshots(src: sqlite3.Cursor, dst, count: int) -> int:
    """Migrate oi_snapshots table."""
    cols = _get_sqlite_columns(src, "oi_snapshots")
    src.execute("SELECT * FROM oi_snapshots ORDER BY id")
    migrated = 0

    while True:
        rows = src.fetchmany(BATCH_SIZE)
        if not rows:
            break
        values = []
        for row in rows:
            r = dict(zip(cols, row))
            values.append((
                _add_tz(r.get("timestamp")),
                r.get("spot_price", 0),
                r.get("strike_price", 0),
                r.get("ce_oi", 0),
                r.get("ce_oi_change", 0),
                r.get("pe_oi", 0),
                r.get("pe_oi_change", 0),
                r.get("ce_volume", 0),
                r.get("pe_volume", 0),
                r.get("ce_iv", 0),
                r.get("pe_iv", 0),
                r.get("ce_ltp", 0),
                r.get("pe_ltp", 0),
                r.get("expiry_date", ""),
            ))
        psycopg2.extras.execute_values(
            dst,
            """INSERT INTO oi_snapshots
               (timestamp, spot_price, strike_price,
                ce_oi, ce_oi_change, pe_oi, pe_oi_change,
                ce_volume, pe_volume, ce_iv, pe_iv, ce_ltp, pe_ltp,
                expiry_date)
               VALUES %s""",
            values,
        )
        migrated += len(values)
        print(f"  oi_snapshots: {migrated}/{count}")

    return migrated


def migrate_analysis_history(src: sqlite3.Cursor, dst, count: int) -> int:
    """Migrate analysis_history table (analysis_json TEXT -> analysis_blob JSON)."""
    cols = _get_sqlite_columns(src, "analysis_history")
    src.execute("SELECT * FROM analysis_history ORDER BY id")
    migrated = 0

    while True:
        rows = src.fetchmany(BATCH_SIZE)
        if not rows:
            break
        values = []
        for row in rows:
            r = dict(zip(cols, row))
            values.append((
                _add_tz(r.get("timestamp")),
                r.get("spot_price", 0),
                r.get("atm_strike", 0),
                r.get("total_call_oi", 0),
                r.get("total_put_oi", 0),
                r.get("call_oi_change", 0),
                r.get("put_oi_change", 0),
                r.get("atm_call_oi_change", 0),
                r.get("atm_put_oi_change", 0),
                r.get("itm_call_oi_change", 0),
                r.get("itm_put_oi_change", 0),
                r.get("verdict", ""),
                r.get("prev_verdict"),
                r.get("expiry_date", ""),
                r.get("vix", 0),
                r.get("iv_skew", 0),
                r.get("max_pain", 0),
                r.get("signal_confidence", 0),
                r.get("futures_oi", 0),
                r.get("futures_oi_change", 0),
                r.get("futures_basis", 0),
                json.dumps(_parse_json(r.get("analysis_json"))),
            ))
        psycopg2.extras.execute_values(
            dst,
            """INSERT INTO analysis_history
               (timestamp, spot_price, atm_strike,
                total_call_oi, total_put_oi, call_oi_change, put_oi_change,
                atm_call_oi_change, atm_put_oi_change,
                itm_call_oi_change, itm_put_oi_change,
                verdict, prev_verdict, expiry_date,
                vix, iv_skew, max_pain, signal_confidence,
                futures_oi, futures_oi_change, futures_basis,
                analysis_blob)
               VALUES %s""",
            values,
        )
        migrated += len(values)
        print(f"  analysis_history: {migrated}/{count}")

    return migrated


def migrate_trade_setups(src: sqlite3.Cursor, dst, count: int) -> int:
    """Migrate trade_setups -> iron_pulse_trades."""
    cols = _get_sqlite_columns(src, "trade_setups")
    src.execute("SELECT * FROM trade_setups ORDER BY id")
    migrated = 0

    while True:
        rows = src.fetchmany(BATCH_SIZE)
        if not rows:
            break
        values = []
        for row in rows:
            r = dict(zip(cols, row))
            created = _add_tz(r.get("created_at"))
            values.append((
                created, created,  # created_at, updated_at
                r.get("direction", ""),
                r.get("strike", 0),
                r.get("option_type", ""),
                r.get("moneyness", ""),
                r.get("entry_premium", 0),
                r.get("sl_premium", 0),
                r.get("target1_premium", 0),
                r.get("target2_premium"),
                r.get("risk_pct", 0),
                r.get("spot_at_creation", 0),
                r.get("verdict_at_creation", ""),
                r.get("signal_confidence"),
                r.get("iv_at_creation"),
                r.get("expiry_date", ""),
                r.get("status", "ACTIVE"),
                _add_tz(r.get("activated_at")),
                r.get("activation_premium"),
                _add_tz(r.get("resolved_at")),
                r.get("exit_premium"),
                r.get("exit_reason"),
                _bool(r.get("hit_sl")),
                _bool(r.get("hit_target")),
                r.get("profit_loss_pct"),
                r.get("profit_loss_points"),
                r.get("max_premium_reached"),
                r.get("min_premium_reached"),
                _add_tz(r.get("last_checked_at")),
                r.get("last_premium"),
                _bool(r.get("t1_hit")),
                _add_tz(r.get("t1_hit_at")),
                r.get("t1_premium"),
                r.get("peak_premium"),
                r.get("trailing_sl"),
                r.get("call_oi_change_at_creation", 0),
                r.get("put_oi_change_at_creation", 0),
                r.get("pcr_at_creation", 0),
                r.get("max_pain_at_creation", 0),
                r.get("support_at_creation", 0),
                r.get("resistance_at_creation", 0),
                r.get("trade_reasoning", ""),
            ))
        psycopg2.extras.execute_values(
            dst,
            """INSERT INTO iron_pulse_trades
               (created_at, updated_at,
                direction, strike, option_type, moneyness,
                entry_premium, sl_premium, target1_premium, target2_premium,
                risk_pct, spot_at_creation, verdict_at_creation,
                signal_confidence, iv_at_creation, expiry_date,
                status, activated_at, activation_premium,
                resolved_at, exit_premium, exit_reason,
                hit_sl, hit_target,
                profit_loss_pct, profit_loss_points,
                max_premium_reached, min_premium_reached,
                last_checked_at, last_premium,
                t1_hit, t1_hit_at, t1_premium, peak_premium, trailing_sl,
                call_oi_change_at_creation, put_oi_change_at_creation,
                pcr_at_creation, max_pain_at_creation,
                support_at_creation, resistance_at_creation,
                trade_reasoning)
               VALUES %s""",
            values,
        )
        migrated += len(values)
        print(f"  iron_pulse_trades: {migrated}/{count}")

    return migrated


def migrate_sell_trade_setups(src: sqlite3.Cursor, dst, count: int) -> int:
    """Migrate sell_trade_setups -> selling_trades."""
    cols = _get_sqlite_columns(src, "sell_trade_setups")
    src.execute("SELECT * FROM sell_trade_setups ORDER BY id")
    migrated = 0

    while True:
        rows = src.fetchmany(BATCH_SIZE)
        if not rows:
            break
        values = []
        for row in rows:
            r = dict(zip(cols, row))
            created = _add_tz(r.get("created_at"))
            values.append((
                created, created,  # created_at, updated_at
                r.get("direction", ""),
                r.get("strike", 0),
                r.get("option_type", ""),
                r.get("entry_premium", 0),
                r.get("sl_premium", 0),
                r.get("target_premium", 0),
                r.get("target2_premium"),
                r.get("spot_at_creation", 0),
                r.get("verdict_at_creation", ""),
                r.get("signal_confidence"),
                r.get("iv_at_creation"),
                r.get("status", "ACTIVE"),
                _add_tz(r.get("resolved_at")),
                r.get("exit_premium"),
                r.get("exit_reason"),
                r.get("profit_loss_pct"),
                r.get("max_premium_reached"),
                r.get("min_premium_reached"),
                _add_tz(r.get("last_checked_at")),
                r.get("last_premium"),
                _bool(r.get("t1_hit")),
                _add_tz(r.get("t1_hit_at")),
            ))
        psycopg2.extras.execute_values(
            dst,
            """INSERT INTO selling_trades
               (created_at, updated_at,
                direction, strike, option_type,
                entry_premium, sl_premium, target_premium, target2_premium,
                spot_at_creation, verdict_at_creation,
                signal_confidence, iv_at_creation,
                status, resolved_at, exit_premium, exit_reason,
                profit_loss_pct, max_premium_reached, min_premium_reached,
                last_checked_at, last_premium,
                t1_hit, t1_hit_at)
               VALUES %s""",
            values,
        )
        migrated += len(values)
        print(f"  selling_trades: {migrated}/{count}")

    return migrated


def migrate_dessert_trades(src: sqlite3.Cursor, dst, count: int) -> int:
    """Migrate dessert_trades (direct mapping)."""
    cols = _get_sqlite_columns(src, "dessert_trades")
    src.execute("SELECT * FROM dessert_trades ORDER BY id")
    migrated = 0

    while True:
        rows = src.fetchmany(BATCH_SIZE)
        if not rows:
            break
        values = []
        for row in rows:
            r = dict(zip(cols, row))
            created = _add_tz(r.get("created_at"))
            values.append((
                created, created,  # created_at, updated_at
                r.get("strategy_name", ""),
                r.get("direction", ""),
                r.get("strike", 0),
                r.get("option_type", ""),
                r.get("entry_premium", 0),
                r.get("sl_premium", 0),
                r.get("target_premium", 0),
                r.get("spot_at_creation", 0),
                r.get("verdict_at_creation", ""),
                r.get("signal_confidence"),
                r.get("iv_skew_at_creation"),
                r.get("vix_at_creation"),
                r.get("max_pain_at_creation"),
                r.get("spot_move_30m"),
                r.get("status", "ACTIVE"),
                _add_tz(r.get("resolved_at")),
                r.get("exit_premium"),
                r.get("exit_reason"),
                r.get("profit_loss_pct"),
                r.get("max_premium_reached"),
                r.get("min_premium_reached"),
                _add_tz(r.get("last_checked_at")),
                r.get("last_premium"),
            ))
        psycopg2.extras.execute_values(
            dst,
            """INSERT INTO dessert_trades
               (created_at, updated_at,
                strategy_name, direction, strike, option_type,
                entry_premium, sl_premium, target_premium,
                spot_at_creation, verdict_at_creation,
                signal_confidence, iv_skew_at_creation,
                vix_at_creation, max_pain_at_creation, spot_move_30m,
                status, resolved_at, exit_premium, exit_reason,
                profit_loss_pct, max_premium_reached, min_premium_reached,
                last_checked_at, last_premium)
               VALUES %s""",
            values,
        )
        migrated += len(values)
        print(f"  dessert_trades: {migrated}/{count}")

    return migrated


def migrate_momentum_trades(src: sqlite3.Cursor, dst, count: int) -> int:
    """Migrate momentum_trades (direct mapping)."""
    cols = _get_sqlite_columns(src, "momentum_trades")
    src.execute("SELECT * FROM momentum_trades ORDER BY id")
    migrated = 0

    while True:
        rows = src.fetchmany(BATCH_SIZE)
        if not rows:
            break
        values = []
        for row in rows:
            r = dict(zip(cols, row))
            created = _add_tz(r.get("created_at"))
            values.append((
                created, created,  # created_at, updated_at
                r.get("strategy_name", "Momentum"),
                r.get("direction", ""),
                r.get("strike", 0),
                r.get("option_type", ""),
                r.get("entry_premium", 0),
                r.get("sl_premium", 0),
                r.get("target_premium", 0),
                r.get("spot_at_creation", 0),
                r.get("verdict_at_creation", ""),
                r.get("signal_confidence"),
                r.get("iv_skew_at_creation"),
                r.get("vix_at_creation"),
                r.get("combined_score"),
                r.get("confirmation_status"),
                r.get("status", "ACTIVE"),
                _add_tz(r.get("resolved_at")),
                r.get("exit_premium"),
                r.get("exit_reason"),
                r.get("profit_loss_pct"),
                r.get("max_premium_reached"),
                r.get("min_premium_reached"),
                _add_tz(r.get("last_checked_at")),
                r.get("last_premium"),
            ))
        psycopg2.extras.execute_values(
            dst,
            """INSERT INTO momentum_trades
               (created_at, updated_at,
                strategy_name, direction, strike, option_type,
                entry_premium, sl_premium, target_premium,
                spot_at_creation, verdict_at_creation,
                signal_confidence, iv_skew_at_creation,
                vix_at_creation, combined_score, confirmation_status,
                status, resolved_at, exit_premium, exit_reason,
                profit_loss_pct, max_premium_reached, min_premium_reached,
                last_checked_at, last_premium)
               VALUES %s""",
            values,
        )
        migrated += len(values)
        print(f"  momentum_trades: {migrated}/{count}")

    return migrated


def migrate_system_logs(src: sqlite3.Cursor, dst, count: int) -> int:
    """Migrate system_logs (details TEXT -> JSON)."""
    cols = _get_sqlite_columns(src, "system_logs")
    src.execute("SELECT * FROM system_logs ORDER BY id")
    migrated = 0

    while True:
        rows = src.fetchmany(BATCH_SIZE)
        if not rows:
            break
        values = []
        for row in rows:
            r = dict(zip(cols, row))
            details = _parse_json(r.get("details"))
            values.append((
                _add_tz(r.get("timestamp")),
                r.get("level", "INFO"),
                r.get("component", ""),
                r.get("message", ""),
                json.dumps(details) if details else None,
                r.get("session_id"),
            ))
        psycopg2.extras.execute_values(
            dst,
            """INSERT INTO system_logs
               (timestamp, level, component, message, details, session_id)
               VALUES %s""",
            values,
        )
        migrated += len(values)
        print(f"  system_logs: {migrated}/{count}")

    return migrated


TABLE_MIGRATIONS = [
    ("oi_snapshots", "oi_snapshots", migrate_oi_snapshots),
    ("analysis_history", "analysis_history", migrate_analysis_history),
    ("trade_setups", "iron_pulse_trades", migrate_trade_setups),
    ("sell_trade_setups", "selling_trades", migrate_sell_trade_setups),
    ("dessert_trades", "dessert_trades", migrate_dessert_trades),
    ("momentum_trades", "momentum_trades", migrate_momentum_trades),
    ("system_logs", "system_logs", migrate_system_logs),
]


def main():
    parser = argparse.ArgumentParser(description="Migrate v1 SQLite -> v2 PostgreSQL")
    parser.add_argument(
        "--sqlite-path",
        default="../../oi_tracker.db",
        help="Path to v1 SQLite database",
    )
    parser.add_argument(
        "--pg-url",
        default=os.environ.get("DATABASE_URL", "").replace("+asyncpg", ""),
        help="PostgreSQL connection URL (sync, no asyncpg)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.sqlite_path):
        print(f"ERROR: SQLite file not found: {args.sqlite_path}")
        sys.exit(1)

    pg_url = args.pg_url
    if not pg_url:
        print("ERROR: --pg-url or DATABASE_URL env var required")
        sys.exit(1)

    # Strip asyncpg driver prefix if present
    pg_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")

    print(f"Source: {args.sqlite_path}")
    print(f"Target: {pg_url.split('@')[1] if '@' in pg_url else pg_url}")
    print()

    src_conn = sqlite3.connect(args.sqlite_path)
    src = src_conn.cursor()

    dst_conn = psycopg2.connect(pg_url)
    dst = dst_conn.cursor()

    total_migrated = 0

    for v1_table, v2_table, migrate_fn in TABLE_MIGRATIONS:
        # Check if v1 table exists
        src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (v1_table,),
        )
        if not src.fetchone():
            print(f"SKIP: {v1_table} (not found in SQLite)")
            continue

        src.execute(f"SELECT COUNT(*) FROM {v1_table}")  # noqa: S608
        count = src.fetchone()[0]
        if count == 0:
            print(f"SKIP: {v1_table} (empty)")
            continue

        print(f"Migrating {v1_table} -> {v2_table} ({count} rows)...")
        try:
            dst.execute(f"TRUNCATE TABLE {v2_table} RESTART IDENTITY CASCADE")  # noqa: S608
            migrated = migrate_fn(src, dst, count)
            dst_conn.commit()
            total_migrated += migrated
            print(f"  Done: {migrated} rows")
        except Exception as e:
            dst_conn.rollback()
            print(f"  ERROR: {e}")
            print(f"  Skipping {v1_table}")

    print(f"\nMigration complete. Total rows migrated: {total_migrated}")

    src_conn.close()
    dst_conn.close()


if __name__ == "__main__":
    main()
