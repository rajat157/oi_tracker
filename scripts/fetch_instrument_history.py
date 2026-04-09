"""
Generic instrument historical data fetcher for IntradayHunter backtest.

Fetches 1-minute OHLC candles for any instrument (index or stock) from Kite
Connect and stores them in a single `instrument_history` table keyed by
(label, interval, timestamp).

Default instruments (used by IntradayHunter strategy):
  - NIFTY 50      (token 256265, NSE INDICES) -- primary index
  - NIFTY BANK    (token 260105, NSE INDICES) -- primary index
  - SENSEX        (token 265,    BSE INDICES) -- primary index
  - HDFCBANK      (token 341249, NSE EQ)      -- comfort-rule constituent
  - KOTAKBANK     (token 492033, NSE EQ)      -- comfort-rule constituent

Usage:
    uv run python scripts/fetch_instrument_history.py
    uv run python scripts/fetch_instrument_history.py --start 2024-01-01 --end 2026-04-08
    uv run python scripts/fetch_instrument_history.py --interval 3minute --instruments NIFTY,BANKNIFTY
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import List

from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from kiteconnect import KiteConnect

from db.connection import DB_PATH
from db.settings_repo import get_setting

# ── Configuration ─────────────────────────────────────────────────────────

# Default instrument set for IntradayHunter strategy
DEFAULT_INSTRUMENTS = {
    # label:        (token, kite_interval, label_for_db)
    "NIFTY":      256265,   # NIFTY 50 spot
    "BANKNIFTY":  260105,   # NIFTY BANK spot
    "SENSEX":     265,      # BSE SENSEX spot
    "HDFCBANK":   341249,   # HDFC Bank stock
    "KOTAKBANK":  492033,   # Kotak Mahindra Bank stock
}

# 1-minute data: Kite limits to ~60 days/request. Use 55-day chunks.
# 3-minute data: Kite limits to ~100 days/request. Use 90-day chunks.
CHUNK_DAYS_BY_INTERVAL = {
    "minute": 55,
    "3minute": 90,
    "5minute": 90,
    "15minute": 200,
    "day": 2000,
}

REQUEST_DELAY = 0.35  # Seconds between API calls (Kite rate limit ~3 req/sec)


# ── Database setup ────────────────────────────────────────────────────────

def create_table(conn: sqlite3.Connection):
    """Create the generic instrument_history table if missing.

    Schema:
        label      TEXT  -- 'NIFTY' / 'BANKNIFTY' / 'SENSEX' / 'HDFCBANK' / etc.
        interval   TEXT  -- '1min' / '3min' / '5min' / '15min' / 'day'
        timestamp  TEXT  -- 'YYYY-MM-DD HH:MM:SS' (no timezone, IST assumed)
        open/high/low/close  REAL
        volume     INTEGER (0 for indices)

    Composite PK on (label, interval, timestamp) prevents duplicates and
    makes the lookup fast for the backtester's day-by-day replay loop.
    """
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
    print("instrument_history table ready.")


# ── Kite auth ─────────────────────────────────────────────────────────────

def init_kite() -> KiteConnect:
    """Initialise KiteConnect using the access token stored in DB."""
    api_key = os.environ.get("KITE_API_KEY", "")
    if not api_key:
        print("ERROR: KITE_API_KEY not set in .env")
        sys.exit(1)

    token = get_setting("kite_access_token")
    token_date = get_setting("kite_token_date")
    if not token:
        print("ERROR: No access token in DB. Run scripts/exchange_token.py first.")
        sys.exit(1)

    print(f"API Key:    {api_key[:8]}...")
    print(f"Token date: {token_date}")
    print(f"Token:      {token[:15]}...")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token)

    # Verify auth with a cheap call
    try:
        ltp = kite.ltp("NSE:NIFTY 50")["NSE:NIFTY 50"]["last_price"]
        print(f"Auth OK. NIFTY 50 LTP: {ltp}")
    except Exception as e:
        print(f"Auth FAILED: {e}")
        sys.exit(1)

    return kite


# ── Fetching ──────────────────────────────────────────────────────────────

def fetch_chunked(
    kite: KiteConnect,
    label: str,
    token: int,
    start: datetime,
    end: datetime,
    kite_interval: str,
) -> list:
    """Fetch historical_data in chunks to respect Kite's per-request limit.

    Returns a list of dicts (Kite's native format) covering the full range.
    Failures on individual chunks are logged but do not abort the batch.
    """
    chunk_days = CHUNK_DAYS_BY_INTERVAL.get(kite_interval, 55)
    all_data = []
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end)
        try:
            data = kite.historical_data(token, chunk_start, chunk_end, kite_interval)
            all_data.extend(data)
            print(
                f"  [{label}] {chunk_start.strftime('%Y-%m-%d')} -> "
                f"{chunk_end.strftime('%Y-%m-%d')}: {len(data):,} rows"
            )
        except Exception as e:
            print(
                f"  [{label}] ERROR chunk "
                f"{chunk_start.strftime('%Y-%m-%d')}: {e}"
            )
        chunk_start = chunk_end + timedelta(seconds=1)
        time.sleep(REQUEST_DELAY)

    return all_data


def insert_data(
    conn: sqlite3.Connection,
    label: str,
    db_interval: str,
    rows: list,
) -> int:
    """Bulk-insert OHLC rows into instrument_history. Skips duplicates."""
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
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    label,
                    db_interval,
                    ts,
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row.get("volume", 0),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return inserted


# ── Verification ──────────────────────────────────────────────────────────

def verify(conn: sqlite3.Connection, label: str, db_interval: str):
    row = conn.execute(
        "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) "
        "FROM instrument_history WHERE label=? AND interval=?",
        (label, db_interval),
    ).fetchone()
    total, mn, mx = row
    if not total:
        print(f"  [{label} {db_interval}] EMPTY")
        return
    days = conn.execute(
        "SELECT COUNT(DISTINCT substr(timestamp, 1, 10)) "
        "FROM instrument_history WHERE label=? AND interval=?",
        (label, db_interval),
    ).fetchone()[0]
    print(
        f"  [{label} {db_interval}] {total:>8,} rows | "
        f"{days:>3} trading days | {mn} -> {mx}"
    )


# ── Main ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--start",
        default="2024-01-01",
        help="Start date YYYY-MM-DD (default: 2024-01-01)",
    )
    p.add_argument(
        "--end",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="End date YYYY-MM-DD (default: today)",
    )
    p.add_argument(
        "--interval",
        default="minute",
        choices=["minute", "3minute", "5minute", "15minute", "day"],
        help="Kite candle interval (default: minute = 1-min)",
    )
    p.add_argument(
        "--instruments",
        default=",".join(DEFAULT_INSTRUMENTS.keys()),
        help="Comma-separated labels (default: all 5 IntradayHunter instruments)",
    )
    return p.parse_args()


def main():
    args = parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    labels = [s.strip() for s in args.instruments.split(",") if s.strip()]
    db_interval = "1min" if args.interval == "minute" else args.interval.replace("minute", "min")

    print("=" * 70)
    print("  IntradayHunter -- Historical Data Fetcher")
    print("=" * 70)
    print(f"  Period:      {args.start} -> {args.end}")
    print(f"  Interval:    {args.interval} (db label: {db_interval})")
    print(f"  Instruments: {', '.join(labels)}")
    print(f"  DB:          {DB_PATH}")
    print()

    kite = init_kite()
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)

    for i, label in enumerate(labels, 1):
        if label not in DEFAULT_INSTRUMENTS:
            print(f"\n[{i}/{len(labels)}] {label}: UNKNOWN -- skipping")
            continue
        token = DEFAULT_INSTRUMENTS[label]
        print(f"\n[{i}/{len(labels)}] Fetching {label} (token {token}, {args.interval})...")
        rows = fetch_chunked(kite, label, token, start, end, args.interval)
        print(f"  Total fetched: {len(rows):,}")
        n = insert_data(conn, label, db_interval, rows)
        print(f"  Inserted:      {n:,} (rest were duplicates)")

    print("\n" + "=" * 70)
    print("  Verification")
    print("=" * 70)
    for label in labels:
        verify(conn, label, db_interval)

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
