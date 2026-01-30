"""
SQLite Database for storing OI snapshots
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager


DB_PATH = "oi_tracker.db"


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the database with required tables."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Table for OI snapshots per strike
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oi_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                spot_price REAL NOT NULL,
                strike_price INTEGER NOT NULL,
                ce_oi INTEGER DEFAULT 0,
                ce_oi_change INTEGER DEFAULT 0,
                pe_oi INTEGER DEFAULT 0,
                pe_oi_change INTEGER DEFAULT 0,
                expiry_date TEXT NOT NULL
            )
        """)

        # Table for analysis results
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                spot_price REAL NOT NULL,
                atm_strike INTEGER NOT NULL,
                total_call_oi INTEGER DEFAULT 0,
                total_put_oi INTEGER DEFAULT 0,
                call_oi_change INTEGER DEFAULT 0,
                put_oi_change INTEGER DEFAULT 0,
                verdict TEXT NOT NULL,
                expiry_date TEXT NOT NULL
            )
        """)

        # Index for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
            ON oi_snapshots(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_analysis_timestamp
            ON analysis_history(timestamp)
        """)

        # Table for tracking signal outcomes (self-learning)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_timestamp DATETIME NOT NULL,
                verdict TEXT NOT NULL,
                strength TEXT NOT NULL,
                combined_score REAL NOT NULL,
                entry_price REAL NOT NULL,
                sl_price REAL,
                target1_price REAL,
                target2_price REAL,
                max_pain INTEGER,
                signal_confidence REAL DEFAULT 0.0,
                ema_accuracy_at_signal REAL DEFAULT 0.5,
                -- Filled after outcome is known
                outcome_timestamp DATETIME,
                actual_exit_price REAL,
                hit_target BOOLEAN,
                hit_sl BOOLEAN,
                profit_loss_pct REAL,
                was_correct BOOLEAN
            )
        """)

        # Table for tracking component accuracy over time
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS component_accuracy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE NOT NULL,
                component TEXT NOT NULL,
                accuracy_30min REAL DEFAULT 0.5,
                accuracy_1hour REAL DEFAULT 0.5,
                sample_count INTEGER DEFAULT 0,
                UNIQUE(date, component)
            )
        """)

        # Table for storing learned weights
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS learned_weights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                otm_weight REAL DEFAULT 0.60,
                atm_weight REAL DEFAULT 0.25,
                itm_weight REAL DEFAULT 0.15,
                momentum_weight REAL DEFAULT 0.20,
                strong_threshold REAL DEFAULT 40.0,
                moderate_threshold REAL DEFAULT 15.0,
                weak_threshold REAL DEFAULT 0.0,
                ema_accuracy REAL DEFAULT 0.5,
                consecutive_errors INTEGER DEFAULT 0,
                is_paused BOOLEAN DEFAULT 0
            )
        """)

        # Index for signal outcomes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_outcomes_timestamp
            ON signal_outcomes(signal_timestamp)
        """)

        # Add migration for ATM/ITM columns if they don't exist
        cursor.execute("""
            SELECT COUNT(*) as count FROM pragma_table_info('analysis_history')
            WHERE name='atm_call_oi_change'
        """)
        if cursor.fetchone()['count'] == 0:
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN atm_call_oi_change INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN atm_put_oi_change INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN itm_call_oi_change INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN itm_put_oi_change INTEGER DEFAULT 0")
            print("Added ATM/ITM columns to analysis_history table")

        # Add migration for volume columns if they don't exist
        cursor.execute("""
            SELECT COUNT(*) as count FROM pragma_table_info('oi_snapshots')
            WHERE name='ce_volume'
        """)
        if cursor.fetchone()['count'] == 0:
            cursor.execute("ALTER TABLE oi_snapshots ADD COLUMN ce_volume INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE oi_snapshots ADD COLUMN pe_volume INTEGER DEFAULT 0")
            print("Added volume columns to oi_snapshots table")

        # Add migration for IV columns if they don't exist
        cursor.execute("""
            SELECT COUNT(*) as count FROM pragma_table_info('oi_snapshots')
            WHERE name='ce_iv'
        """)
        if cursor.fetchone()['count'] == 0:
            cursor.execute("ALTER TABLE oi_snapshots ADD COLUMN ce_iv REAL DEFAULT 0.0")
            cursor.execute("ALTER TABLE oi_snapshots ADD COLUMN pe_iv REAL DEFAULT 0.0")
            print("Added IV columns to oi_snapshots table")

        # Add migration for VIX and other analysis columns if they don't exist
        cursor.execute("""
            SELECT COUNT(*) as count FROM pragma_table_info('analysis_history')
            WHERE name='vix'
        """)
        if cursor.fetchone()['count'] == 0:
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN vix REAL DEFAULT 0.0")
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN iv_skew REAL DEFAULT 0.0")
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN max_pain INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN signal_confidence REAL DEFAULT 0.0")
            print("Added VIX, IV skew, max pain, and confidence columns to analysis_history table")

        conn.commit()


def save_snapshot(timestamp: datetime, spot_price: float, strikes_data: dict,
                  expiry_date: str):
    """
    Save OI snapshot for all strikes.

    Args:
        timestamp: When the data was fetched
        spot_price: Current spot price
        strikes_data: Dict of strike -> {ce_oi, ce_oi_change, pe_oi, pe_oi_change, ce_iv, pe_iv}
        expiry_date: The expiry date for these options
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        for strike, data in strikes_data.items():
            cursor.execute("""
                INSERT INTO oi_snapshots
                (timestamp, spot_price, strike_price, ce_oi, ce_oi_change,
                 pe_oi, pe_oi_change, ce_volume, pe_volume, ce_iv, pe_iv, expiry_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp.isoformat(),
                spot_price,
                strike,
                data.get("ce_oi", 0),
                data.get("ce_oi_change", 0),
                data.get("pe_oi", 0),
                data.get("pe_oi_change", 0),
                data.get("ce_volume", 0),
                data.get("pe_volume", 0),
                data.get("ce_iv", 0.0),
                data.get("pe_iv", 0.0),
                expiry_date
            ))

        conn.commit()


def save_analysis(timestamp: datetime, spot_price: float, atm_strike: int,
                  total_call_oi: int, total_put_oi: int,
                  call_oi_change: int, put_oi_change: int,
                  verdict: str, expiry_date: str,
                  atm_call_oi_change: int = 0, atm_put_oi_change: int = 0,
                  itm_call_oi_change: int = 0, itm_put_oi_change: int = 0,
                  vix: float = 0.0, iv_skew: float = 0.0, max_pain: int = 0,
                  signal_confidence: float = 0.0):
    """Save analysis result to history."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO analysis_history
            (timestamp, spot_price, atm_strike, total_call_oi, total_put_oi,
             call_oi_change, put_oi_change, verdict, expiry_date,
             atm_call_oi_change, atm_put_oi_change, itm_call_oi_change, itm_put_oi_change,
             vix, iv_skew, max_pain, signal_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp.isoformat(),
            spot_price,
            atm_strike,
            total_call_oi,
            total_put_oi,
            call_oi_change,
            put_oi_change,
            verdict,
            expiry_date,
            atm_call_oi_change,
            atm_put_oi_change,
            itm_call_oi_change,
            itm_put_oi_change,
            vix,
            iv_skew,
            max_pain,
            signal_confidence
        ))
        conn.commit()


def get_latest_snapshot() -> Optional[dict]:
    """Get the most recent snapshot data."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Get latest timestamp
        cursor.execute("""
            SELECT DISTINCT timestamp, spot_price, expiry_date
            FROM oi_snapshots
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()

        if not row:
            return None

        timestamp = row["timestamp"]

        # Get all strikes for that timestamp
        cursor.execute("""
            SELECT strike_price, ce_oi, ce_oi_change, pe_oi, pe_oi_change,
                   ce_volume, pe_volume, ce_iv, pe_iv
            FROM oi_snapshots
            WHERE timestamp = ?
            ORDER BY strike_price
        """, (timestamp,))

        strikes = {}
        for strike_row in cursor.fetchall():
            # Handle columns that may not exist in old data
            try:
                ce_volume = strike_row["ce_volume"]
            except (KeyError, IndexError):
                ce_volume = 0

            try:
                pe_volume = strike_row["pe_volume"]
            except (KeyError, IndexError):
                pe_volume = 0

            try:
                ce_iv = strike_row["ce_iv"]
            except (KeyError, IndexError):
                ce_iv = 0.0

            try:
                pe_iv = strike_row["pe_iv"]
            except (KeyError, IndexError):
                pe_iv = 0.0

            strikes[strike_row["strike_price"]] = {
                "ce_oi": strike_row["ce_oi"],
                "ce_oi_change": strike_row["ce_oi_change"],
                "ce_volume": ce_volume,
                "ce_iv": ce_iv,
                "pe_oi": strike_row["pe_oi"],
                "pe_oi_change": strike_row["pe_oi_change"],
                "pe_volume": pe_volume,
                "pe_iv": pe_iv,
            }

        return {
            "timestamp": timestamp,
            "spot_price": row["spot_price"],
            "expiry_date": row["expiry_date"],
            "strikes": strikes
        }


def get_latest_analysis() -> Optional[dict]:
    """Get the most recent analysis result."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM analysis_history
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()

        if row:
            return dict(row)
        return None


def get_analysis_history(limit: int = 50) -> list:
    """Get historical analysis results for charting."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, spot_price, total_call_oi, total_put_oi,
                   call_oi_change, put_oi_change, verdict,
                   atm_call_oi_change, atm_put_oi_change,
                   itm_call_oi_change, itm_put_oi_change
            FROM analysis_history
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        return [dict(row) for row in reversed(rows)]  # Chronological order


def get_recent_price_trend(lookback_minutes: int = 9) -> list:
    """
    Get recent price history for momentum calculation.

    Args:
        lookback_minutes: Number of minutes to look back (default 9 = 3 data points at 3-min intervals)

    Returns:
        List of dicts with 'timestamp' and 'spot_price' from recent history
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, spot_price
            FROM analysis_history
            ORDER BY timestamp DESC
            LIMIT ?
        """, (lookback_minutes // 3 + 1,))  # Get enough points for the lookback period

        rows = cursor.fetchall()
        return [dict(row) for row in reversed(rows)]  # Chronological order


def get_strikes_for_timestamp(timestamp: str) -> dict:
    """Get all strike data for a specific timestamp."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT strike_price, ce_oi, ce_oi_change, pe_oi, pe_oi_change,
                   ce_volume, pe_volume, ce_iv, pe_iv
            FROM oi_snapshots
            WHERE timestamp = ?
            ORDER BY strike_price
        """, (timestamp,))

        strikes = {}
        for row in cursor.fetchall():
            # Handle columns that may not exist in old data
            try:
                ce_volume = row["ce_volume"]
            except (KeyError, IndexError):
                ce_volume = 0

            try:
                pe_volume = row["pe_volume"]
            except (KeyError, IndexError):
                pe_volume = 0

            try:
                ce_iv = row["ce_iv"]
            except (KeyError, IndexError):
                ce_iv = 0.0

            try:
                pe_iv = row["pe_iv"]
            except (KeyError, IndexError):
                pe_iv = 0.0

            strikes[row["strike_price"]] = {
                "ce_oi": row["ce_oi"],
                "ce_oi_change": row["ce_oi_change"],
                "ce_volume": ce_volume,
                "ce_iv": ce_iv,
                "pe_oi": row["pe_oi"],
                "pe_oi_change": row["pe_oi_change"],
                "pe_volume": pe_volume,
                "pe_iv": pe_iv,
            }
        return strikes


def get_last_data_date():
    """Get the date of the most recent data in the database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp FROM oi_snapshots
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()

        if row:
            # Parse the timestamp and return just the date
            timestamp_str = row["timestamp"]
            try:
                dt = datetime.fromisoformat(timestamp_str)
                return dt.date()
            except:
                return None
        return None


def purge_old_data(keep_from_date):
    """
    Delete all data older than the specified date.

    Args:
        keep_from_date: Date object - data from this date onwards is kept
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # Convert date to datetime string for comparison
        cutoff = datetime.combine(keep_from_date, datetime.min.time()).isoformat()

        # Delete old snapshots
        cursor.execute("""
            DELETE FROM oi_snapshots
            WHERE timestamp < ?
        """, (cutoff,))
        snapshots_deleted = cursor.rowcount

        # Delete old analysis history
        cursor.execute("""
            DELETE FROM analysis_history
            WHERE timestamp < ?
        """, (cutoff,))
        analysis_deleted = cursor.rowcount

        conn.commit()

        print(f"Purged {snapshots_deleted} snapshot records and {analysis_deleted} analysis records")
        return snapshots_deleted, analysis_deleted


def purge_all_data():
    """Delete all data from the database (for testing/reset)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM oi_snapshots")
        cursor.execute("DELETE FROM analysis_history")
        conn.commit()
        print("All data purged from database")


def save_signal_outcome(signal_timestamp: datetime, verdict: str, strength: str,
                        combined_score: float, entry_price: float,
                        sl_price: float = None, target1_price: float = None,
                        target2_price: float = None, max_pain: int = None,
                        signal_confidence: float = 0.0, ema_accuracy: float = 0.5):
    """Save a new signal for tracking outcomes."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO signal_outcomes
            (signal_timestamp, verdict, strength, combined_score, entry_price,
             sl_price, target1_price, target2_price, max_pain,
             signal_confidence, ema_accuracy_at_signal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_timestamp.isoformat(),
            verdict,
            strength,
            combined_score,
            entry_price,
            sl_price,
            target1_price,
            target2_price,
            max_pain,
            signal_confidence,
            ema_accuracy
        ))
        conn.commit()
        return cursor.lastrowid


def update_signal_outcome(signal_id: int, outcome_timestamp: datetime,
                          actual_exit_price: float, hit_target: bool,
                          hit_sl: bool, profit_loss_pct: float, was_correct: bool):
    """Update a signal with its outcome."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE signal_outcomes
            SET outcome_timestamp = ?, actual_exit_price = ?, hit_target = ?,
                hit_sl = ?, profit_loss_pct = ?, was_correct = ?
            WHERE id = ?
        """, (
            outcome_timestamp.isoformat(),
            actual_exit_price,
            hit_target,
            hit_sl,
            profit_loss_pct,
            was_correct,
            signal_id
        ))
        conn.commit()


def get_pending_signals():
    """Get signals that haven't been resolved yet."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM signal_outcomes
            WHERE outcome_timestamp IS NULL
            ORDER BY signal_timestamp ASC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_signal_accuracy(lookback_days: int = 30) -> dict:
    """Calculate signal accuracy over recent period."""
    with get_connection() as conn:
        cursor = conn.cursor()

        cutoff = datetime.now() - timedelta(days=lookback_days)
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct,
                AVG(profit_loss_pct) as avg_profit_loss,
                strength
            FROM signal_outcomes
            WHERE outcome_timestamp IS NOT NULL
              AND signal_timestamp >= ?
            GROUP BY strength
        """, (cutoff.isoformat(),))

        results = {}
        for row in cursor.fetchall():
            results[row["strength"]] = {
                "total": row["total"],
                "correct": row["correct"],
                "accuracy": row["correct"] / row["total"] if row["total"] > 0 else 0.5,
                "avg_profit_loss": row["avg_profit_loss"] or 0
            }

        # Overall accuracy
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct
            FROM signal_outcomes
            WHERE outcome_timestamp IS NOT NULL
              AND signal_timestamp >= ?
        """, (cutoff.isoformat(),))
        row = cursor.fetchone()
        results["overall"] = {
            "total": row["total"],
            "correct": row["correct"],
            "accuracy": row["correct"] / row["total"] if row["total"] > 0 else 0.5
        }

        return results


def save_learned_weights(otm_weight: float, atm_weight: float, itm_weight: float,
                         momentum_weight: float, strong_threshold: float,
                         moderate_threshold: float, weak_threshold: float,
                         ema_accuracy: float, consecutive_errors: int, is_paused: bool):
    """Save current learned weights."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO learned_weights
            (timestamp, otm_weight, atm_weight, itm_weight, momentum_weight,
             strong_threshold, moderate_threshold, weak_threshold,
             ema_accuracy, consecutive_errors, is_paused)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            otm_weight, atm_weight, itm_weight, momentum_weight,
            strong_threshold, moderate_threshold, weak_threshold,
            ema_accuracy, consecutive_errors, is_paused
        ))
        conn.commit()


def get_latest_learned_weights() -> Optional[dict]:
    """Get the most recent learned weights."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM learned_weights
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def save_component_accuracy(date, component: str, accuracy_30min: float,
                           accuracy_1hour: float, sample_count: int):
    """Save/update component accuracy for a given date."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO component_accuracy
            (date, component, accuracy_30min, accuracy_1hour, sample_count)
            VALUES (?, ?, ?, ?, ?)
        """, (
            date if isinstance(date, str) else date.isoformat(),
            component,
            accuracy_30min,
            accuracy_1hour,
            sample_count
        ))
        conn.commit()


def get_component_accuracy(lookback_days: int = 30) -> dict:
    """Get average component accuracy over recent period."""
    with get_connection() as conn:
        cursor = conn.cursor()

        cutoff = datetime.now().date() - timedelta(days=lookback_days)
        cursor.execute("""
            SELECT
                component,
                AVG(accuracy_30min) as avg_accuracy_30min,
                AVG(accuracy_1hour) as avg_accuracy_1hour,
                SUM(sample_count) as total_samples
            FROM component_accuracy
            WHERE date >= ?
            GROUP BY component
        """, (cutoff.isoformat(),))

        results = {}
        for row in cursor.fetchall():
            results[row["component"]] = {
                "accuracy_30min": row["avg_accuracy_30min"] or 0.5,
                "accuracy_1hour": row["avg_accuracy_1hour"] or 0.5,
                "total_samples": row["total_samples"] or 0
            }

        return results


# Initialize database on import
init_db()


if __name__ == "__main__":
    # Test database operations
    print("Testing database...")

    # Test save
    test_strikes = {
        24000: {"ce_oi": 100000, "ce_oi_change": 5000, "pe_oi": 80000, "pe_oi_change": 3000},
        24050: {"ce_oi": 90000, "ce_oi_change": 4000, "pe_oi": 70000, "pe_oi_change": 2000},
    }

    save_snapshot(datetime.now(), 24025.50, test_strikes, "2024-01-25")
    save_analysis(datetime.now(), 24025.50, 24050, 190000, 150000, 9000, 5000,
                  "Bears Winning", "2024-01-25")

    # Test retrieve
    latest = get_latest_snapshot()
    print(f"Latest snapshot: {latest}")

    analysis = get_latest_analysis()
    print(f"Latest analysis: {analysis}")

    history = get_analysis_history(10)
    print(f"History count: {len(history)}")
