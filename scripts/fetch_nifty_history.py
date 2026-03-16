"""
Fetch NIFTY 50 historical candle data from Kite Connect API.

Fetches:
  1. NIFTY 50 index — 3-minute candles (Jan 1 2025 to today)
  2. NIFTY futures — continuous daily candles with OI
  3. India VIX — 3-minute candles

Data is stored in SQLite tables: nifty_history, nifty_futures_history, vix_history.

Usage:
    uv run python scripts/fetch_nifty_history.py
"""

import os
import sys
import sqlite3
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from kiteconnect import KiteConnect
from db.settings_repo import get_setting
from db.connection import DB_PATH

# ── Configuration ─────────────────────────────────────────────────────────

NIFTY_TOKEN = 256265        # NSE:NIFTY 50
VIX_TOKEN = 264969          # NSE:INDIA VIX
CHUNK_DAYS = 55             # Max days per request (API limit ~60 for minute data)
REQUEST_DELAY = 0.35        # Seconds between API calls to avoid rate limits

START_DATE = datetime(2025, 1, 1)
END_DATE = datetime(2026, 3, 16, 23, 59, 59)


# ── Database setup ────────────────────────────────────────────────────────

def create_tables(conn: sqlite3.Connection):
    """Create history tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nifty_history (
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (timestamp)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_nifty_history_ts
        ON nifty_history(timestamp)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS nifty_futures_history (
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL DEFAULT 0,
            oi INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (timestamp)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_nifty_futures_history_ts
        ON nifty_futures_history(timestamp)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vix_history (
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (timestamp)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_vix_history_ts
        ON vix_history(timestamp)
    """)
    conn.commit()
    print("Tables created/verified.")


# ── Data fetching ─────────────────────────────────────────────────────────

def init_kite() -> KiteConnect:
    """Initialize KiteConnect with stored access token."""
    api_key = os.environ.get('KITE_API_KEY', '')
    if not api_key:
        print("ERROR: KITE_API_KEY not set in .env")
        sys.exit(1)

    token = get_setting('kite_access_token')
    token_date = get_setting('kite_token_date')
    if not token:
        print("ERROR: No access token found in database. Run exchange_token.py first.")
        sys.exit(1)

    print(f"API Key: {api_key[:8]}...")
    print(f"Token date: {token_date}")
    print(f"Token: {token[:15]}...")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token)

    # Verify auth
    try:
        resp = kite.ltp("NSE:NIFTY 50")
        ltp = resp["NSE:NIFTY 50"]["last_price"]
        print(f"Auth OK. NIFTY 50 LTP: {ltp}")
    except Exception as e:
        print(f"Auth FAILED: {e}")
        sys.exit(1)

    return kite


def fetch_chunked(kite: KiteConnect, instrument_token: int, start: datetime,
                   end: datetime, interval: str, oi: bool = False,
                   continuous: bool = False) -> list:
    """
    Fetch historical data in chunks to respect API limits.

    For minute-level data, Kite allows max ~60 days per request.
    We use 55-day chunks with a small delay between requests.
    """
    all_data = []
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end)

        try:
            data = kite.historical_data(
                instrument_token,
                chunk_start,
                chunk_end,
                interval,
                continuous=continuous,
                oi=oi,
            )
            all_data.extend(data)
            date_range = f"{chunk_start.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}"
            print(f"  Chunk {date_range}: {len(data)} records")
        except Exception as e:
            print(f"  ERROR chunk {chunk_start.strftime('%Y-%m-%d')}: {e}")

        chunk_start = chunk_end + timedelta(seconds=1)
        time.sleep(REQUEST_DELAY)

    return all_data


def insert_data(conn: sqlite3.Connection, table: str, data: list, has_oi: bool = False):
    """Insert fetched data into SQLite table, skipping duplicates."""
    if not data:
        print(f"  No data to insert into {table}")
        return 0

    inserted = 0
    for row in data:
        ts = row['date'].strftime('%Y-%m-%d %H:%M:%S')
        try:
            if has_oi:
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} (timestamp, open, high, low, close, volume, oi) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ts, row['open'], row['high'], row['low'], row['close'],
                     row['volume'], row.get('oi', 0))
                )
            else:
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} (timestamp, open, high, low, close, volume) "
                    f"VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, row['open'], row['high'], row['low'], row['close'], row['volume'])
                )
            inserted += 1
        except sqlite3.IntegrityError:
            pass  # Duplicate — skip

    conn.commit()
    return inserted


# ── Verification ──────────────────────────────────────────────────────────

def verify_table(conn: sqlite3.Connection, table: str, label: str):
    """Print verification stats for a table."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
    total = row[0]
    print(f"  Total rows: {total:,}")

    if total == 0:
        print("  (empty)")
        return

    row = conn.execute(f"SELECT MIN(timestamp) as mn, MAX(timestamp) as mx FROM {table}").fetchone()
    print(f"  Date range: {row[0]} to {row[1]}")

    # Trading days
    row = conn.execute(
        f"SELECT COUNT(DISTINCT substr(timestamp, 1, 10)) as days FROM {table}"
    ).fetchone()
    trading_days = row[0]
    print(f"  Trading days: {trading_days}")
    if trading_days > 0:
        print(f"  Avg candles/day: {total / trading_days:.1f}")

    # First 5
    print("\n  First 5 rows:")
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY timestamp ASC LIMIT 5").fetchall()
    for r in rows:
        cols = [str(r[i]) for i in range(len(r))]
        print(f"    {' | '.join(cols)}")

    # Last 5
    print("\n  Last 5 rows:")
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT 5").fetchall()
    for r in reversed(rows):
        cols = [str(r[i]) for i in range(len(r))]
        print(f"    {' | '.join(cols)}")


# ── NIFTY futures: build contract list ────────────────────────────────────

def get_futures_contracts(kite: KiteConnect) -> list:
    """
    Build list of NIFTY futures contracts with their instrument tokens.

    For historical data we need the per-contract tokens. The instruments API
    only shows currently active contracts. For the current month, we fetch
    3-minute data. For older months we fall back to continuous daily.

    Returns list of dicts: {token, tradingsymbol, expiry, start, end}
    """
    instruments = kite.instruments('NFO')
    nifty_futs = [
        i for i in instruments
        if i['name'] == 'NIFTY' and i['instrument_type'] == 'FUT'
    ]
    nifty_futs.sort(key=lambda x: x['expiry'])

    contracts = []
    for f in nifty_futs:
        expiry = f['expiry']  # datetime.date object
        # Contract typically becomes near-month ~1 month before expiry
        # and is tradeable until expiry day
        start_dt = datetime(expiry.year, expiry.month, expiry.day) - timedelta(days=90)
        end_dt = datetime(expiry.year, expiry.month, expiry.day, 23, 59, 59)
        contracts.append({
            'token': f['instrument_token'],
            'symbol': f['tradingsymbol'],
            'expiry': expiry,
            'start': max(start_dt, START_DATE),
            'end': min(end_dt, END_DATE),
        })
        print(f"  Future contract: {f['tradingsymbol']} token={f['instrument_token']} expiry={expiry}")

    return contracts


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  NIFTY Historical Data Fetcher")
    print("=" * 60)
    print(f"  Period: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print(f"  Chunk size: {CHUNK_DAYS} days")
    print(f"  DB: {DB_PATH}")
    print()

    kite = init_kite()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    create_tables(conn)

    # ── 1. NIFTY 50 Index — 3-minute candles ─────────────────────────────
    print("\n[1/3] Fetching NIFTY 50 index (3-minute candles)...")
    nifty_data = fetch_chunked(kite, NIFTY_TOKEN, START_DATE, END_DATE, '3minute')
    print(f"  Total fetched: {len(nifty_data):,} records")
    inserted = insert_data(conn, 'nifty_history', nifty_data)
    print(f"  Inserted: {inserted:,} records")

    # ── 2. NIFTY Futures — continuous daily + current contract 3-min ─────
    print("\n[2/3] Fetching NIFTY futures...")

    # 2a. Continuous daily with OI (full period)
    print("  2a. Continuous daily candles with OI...")
    fut_daily = fetch_chunked(
        kite, 13238786,  # Use any current NIFTY FUT token for continuous
        START_DATE, END_DATE, 'day',
        continuous=True, oi=True
    )
    print(f"  Daily records fetched: {len(fut_daily):,}")
    # Store daily in futures table with timestamp as date only
    inserted = insert_data(conn, 'nifty_futures_history', fut_daily, has_oi=True)
    print(f"  Inserted: {inserted:,} records")

    # 2b. Active contracts — 3-minute data (only for contracts we have tokens for)
    print("\n  2b. Active contract 3-minute candles with OI...")
    contracts = get_futures_contracts(kite)
    for c in contracts:
        print(f"\n  Fetching {c['symbol']} ({c['start'].strftime('%Y-%m-%d')} to {c['end'].strftime('%Y-%m-%d')})...")
        try:
            fut_3m = fetch_chunked(
                kite, c['token'], c['start'], c['end'], '3minute', oi=True
            )
            if fut_3m:
                inserted = insert_data(conn, 'nifty_futures_history', fut_3m, has_oi=True)
                print(f"  {c['symbol']}: {len(fut_3m):,} fetched, {inserted:,} inserted")
            else:
                print(f"  {c['symbol']}: no data")
        except Exception as e:
            print(f"  {c['symbol']} ERROR: {e}")

    # ── 3. India VIX — 3-minute candles ──────────────────────────────────
    print("\n[3/3] Fetching India VIX (3-minute candles)...")
    vix_data = fetch_chunked(kite, VIX_TOKEN, START_DATE, END_DATE, '3minute')
    print(f"  Total fetched: {len(vix_data):,} records")
    inserted = insert_data(conn, 'vix_history', vix_data)
    print(f"  Inserted: {inserted:,} records")

    # ── Verification ─────────────────────────────────────────────────────
    verify_table(conn, 'nifty_history', 'NIFTY 50 Index (3-min)')
    verify_table(conn, 'nifty_futures_history', 'NIFTY Futures (daily + 3-min)')
    verify_table(conn, 'vix_history', 'India VIX (3-min)')

    conn.close()
    print("\nDone!")


if __name__ == '__main__':
    main()
