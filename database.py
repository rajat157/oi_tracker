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

        # Table for persistent trade setups with lifecycle tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_setups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                -- Creation
                created_at DATETIME NOT NULL,
                direction TEXT NOT NULL,
                strike INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                moneyness TEXT NOT NULL,
                entry_premium REAL NOT NULL,
                sl_premium REAL NOT NULL,
                target1_premium REAL NOT NULL,
                target2_premium REAL,
                risk_pct REAL NOT NULL,
                spot_at_creation REAL NOT NULL,
                verdict_at_creation TEXT NOT NULL,
                signal_confidence REAL NOT NULL,
                iv_at_creation REAL DEFAULT 0.0,
                expiry_date TEXT NOT NULL,
                -- Status
                status TEXT NOT NULL DEFAULT 'PENDING',
                -- Activation
                activated_at DATETIME,
                activation_premium REAL,
                -- Resolution
                resolved_at DATETIME,
                exit_premium REAL,
                hit_sl BOOLEAN DEFAULT 0,
                hit_target BOOLEAN DEFAULT 0,
                profit_loss_pct REAL,
                profit_loss_points REAL,
                -- Tracking
                max_premium_reached REAL,
                min_premium_reached REAL,
                last_checked_at DATETIME,
                last_premium REAL
            )
        """)

        # Index for trade setups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trade_setups_status
            ON trade_setups(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trade_setups_created
            ON trade_setups(created_at)
        """)

        # Add migration for technical analysis context columns in trade_setups
        trade_setup_columns_to_add = [
            ("call_oi_change_at_creation", "REAL DEFAULT 0"),
            ("put_oi_change_at_creation", "REAL DEFAULT 0"),
            ("pcr_at_creation", "REAL DEFAULT 0"),
            ("max_pain_at_creation", "INTEGER DEFAULT 0"),
            ("support_at_creation", "INTEGER DEFAULT 0"),
            ("resistance_at_creation", "INTEGER DEFAULT 0"),
            ("trade_reasoning", "TEXT DEFAULT ''"),
        ]
        for col_name, col_def in trade_setup_columns_to_add:
            try:
                cursor.execute(f"ALTER TABLE trade_setups ADD COLUMN {col_name} {col_def}")
                print(f"Added {col_name} column to trade_setups table")
            except:
                pass  # Column already exists

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

        # Add migration for futures OI columns if they don't exist
        cursor.execute("""
            SELECT COUNT(*) as count FROM pragma_table_info('analysis_history')
            WHERE name='futures_oi'
        """)
        if cursor.fetchone()['count'] == 0:
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN futures_oi INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN futures_oi_change INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN futures_basis REAL DEFAULT 0.0")
            print("Added futures OI columns to analysis_history table")

        # Add migration for LTP columns if they don't exist
        cursor.execute("""
            SELECT COUNT(*) as count FROM pragma_table_info('oi_snapshots')
            WHERE name='ce_ltp'
        """)
        if cursor.fetchone()['count'] == 0:
            cursor.execute("ALTER TABLE oi_snapshots ADD COLUMN ce_ltp REAL DEFAULT 0.0")
            cursor.execute("ALTER TABLE oi_snapshots ADD COLUMN pe_ltp REAL DEFAULT 0.0")
            print("Added LTP columns to oi_snapshots table")

        # Add migration for analysis_json column to store complete analysis
        cursor.execute("""
            SELECT COUNT(*) as count FROM pragma_table_info('analysis_history')
            WHERE name='analysis_json'
        """)
        if cursor.fetchone()['count'] == 0:
            cursor.execute("ALTER TABLE analysis_history ADD COLUMN analysis_json TEXT")
            print("Added analysis_json column to analysis_history table")

        conn.commit()


def save_snapshot(timestamp: datetime, spot_price: float, strikes_data: dict,
                  expiry_date: str):
    """
    Save OI snapshot for all strikes.

    Args:
        timestamp: When the data was fetched
        spot_price: Current spot price
        strikes_data: Dict of strike -> {ce_oi, ce_oi_change, pe_oi, pe_oi_change, ce_iv, pe_iv, ce_ltp, pe_ltp}
        expiry_date: The expiry date for these options
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        for strike, data in strikes_data.items():
            cursor.execute("""
                INSERT INTO oi_snapshots
                (timestamp, spot_price, strike_price, ce_oi, ce_oi_change,
                 pe_oi, pe_oi_change, ce_volume, pe_volume, ce_iv, pe_iv,
                 ce_ltp, pe_ltp, expiry_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                data.get("ce_ltp", 0.0),
                data.get("pe_ltp", 0.0),
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
                  signal_confidence: float = 0.0,
                  futures_oi: int = 0, futures_oi_change: int = 0, futures_basis: float = 0.0,
                  analysis_json: str = None):
    """Save analysis result to history including full JSON blob."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO analysis_history
            (timestamp, spot_price, atm_strike, total_call_oi, total_put_oi,
             call_oi_change, put_oi_change, verdict, expiry_date,
             atm_call_oi_change, atm_put_oi_change, itm_call_oi_change, itm_put_oi_change,
             vix, iv_skew, max_pain, signal_confidence,
             futures_oi, futures_oi_change, futures_basis, analysis_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            signal_confidence,
            futures_oi,
            futures_oi_change,
            futures_basis,
            analysis_json
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
                   ce_volume, pe_volume, ce_iv, pe_iv, ce_ltp, pe_ltp
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

            try:
                ce_ltp = strike_row["ce_ltp"]
            except (KeyError, IndexError):
                ce_ltp = 0.0

            try:
                pe_ltp = strike_row["pe_ltp"]
            except (KeyError, IndexError):
                pe_ltp = 0.0

            strikes[strike_row["strike_price"]] = {
                "ce_oi": strike_row["ce_oi"],
                "ce_oi_change": strike_row["ce_oi_change"],
                "ce_volume": ce_volume,
                "ce_iv": ce_iv,
                "ce_ltp": ce_ltp,
                "pe_oi": strike_row["pe_oi"],
                "pe_oi_change": strike_row["pe_oi_change"],
                "pe_volume": pe_volume,
                "pe_iv": pe_iv,
                "pe_ltp": pe_ltp,
            }

        return {
            "timestamp": timestamp,
            "spot_price": row["spot_price"],
            "expiry_date": row["expiry_date"],
            "strikes": strikes
        }


def get_latest_analysis() -> Optional[dict]:
    """Get the most recent analysis result with full data."""
    import json as json_module
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM analysis_history
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()

        if row:
            result = dict(row)
            # Parse JSON blob if present and return complete analysis
            if result.get('analysis_json'):
                try:
                    full_analysis = json_module.loads(result['analysis_json'])
                    return full_analysis  # Return complete analysis
                except (json_module.JSONDecodeError, TypeError):
                    pass  # Fallback to basic fields
            return result  # Fallback to basic fields for old data
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


def get_recent_oi_changes(lookback: int = 3) -> list:
    """
    Get recent OI changes for acceleration calculation.

    Args:
        lookback: Number of data points to retrieve (default 3 = 9 minutes at 3-min intervals)

    Returns:
        List of (call_oi_change, put_oi_change) tuples, oldest first
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT call_oi_change, put_oi_change
            FROM analysis_history
            ORDER BY timestamp DESC
            LIMIT ?
        """, (lookback,))

        rows = cursor.fetchall()
        # Reverse to get oldest first
        return [(row["call_oi_change"], row["put_oi_change"]) for row in reversed(rows)]


def get_previous_futures_oi() -> int:
    """
    Get the previous futures OI from the most recent analysis.

    Returns:
        Previous futures OI value, or 0 if no previous data
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT futures_oi FROM analysis_history
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row and row["futures_oi"]:
            return row["futures_oi"]
        return 0


def get_previous_strikes_data() -> Optional[dict]:
    """
    Get the previous snapshot's strikes data for premium momentum calculation.

    Returns:
        Dict of strike -> {ce_ltp, pe_ltp, ...} or None if no previous data
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        # Get the second most recent timestamp (skip current)
        cursor.execute("""
            SELECT DISTINCT timestamp FROM oi_snapshots
            ORDER BY timestamp DESC
            LIMIT 1 OFFSET 1
        """)
        row = cursor.fetchone()
        if not row:
            return None

        prev_timestamp = row["timestamp"]
        return get_strikes_for_timestamp(prev_timestamp)


def get_strikes_for_timestamp(timestamp: str) -> dict:
    """Get all strike data for a specific timestamp."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT strike_price, ce_oi, ce_oi_change, pe_oi, pe_oi_change,
                   ce_volume, pe_volume, ce_iv, pe_iv, ce_ltp, pe_ltp
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

            try:
                ce_ltp = row["ce_ltp"]
            except (KeyError, IndexError):
                ce_ltp = 0.0

            try:
                pe_ltp = row["pe_ltp"]
            except (KeyError, IndexError):
                pe_ltp = 0.0

            strikes[row["strike_price"]] = {
                "ce_oi": row["ce_oi"],
                "ce_oi_change": row["ce_oi_change"],
                "ce_volume": ce_volume,
                "ce_iv": ce_iv,
                "ce_ltp": ce_ltp,
                "pe_oi": row["pe_oi"],
                "pe_oi_change": row["pe_oi_change"],
                "pe_volume": pe_volume,
                "pe_iv": pe_iv,
                "pe_ltp": pe_ltp,
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


# ===== Trade Setup Functions =====

def save_trade_setup(created_at: datetime, direction: str, strike: int,
                     option_type: str, moneyness: str, entry_premium: float,
                     sl_premium: float, target1_premium: float, target2_premium: float,
                     risk_pct: float, spot_at_creation: float, verdict_at_creation: str,
                     signal_confidence: float, iv_at_creation: float, expiry_date: str,
                     call_oi_change_at_creation: float = 0, put_oi_change_at_creation: float = 0,
                     pcr_at_creation: float = 0, max_pain_at_creation: int = 0,
                     support_at_creation: int = 0, resistance_at_creation: int = 0,
                     trade_reasoning: str = "") -> int:
    """
    Save a new trade setup with PENDING status.

    Args:
        created_at: Timestamp when trade was created
        direction: BUY_CALL or BUY_PUT
        strike: Strike price
        option_type: CE or PE
        moneyness: ATM, ITM, or OTM
        entry_premium: Entry premium price
        sl_premium: Stop loss premium
        target1_premium: Target 1 premium
        target2_premium: Target 2 premium
        risk_pct: Risk percentage
        spot_at_creation: Spot price at creation
        verdict_at_creation: Market verdict at creation
        signal_confidence: Signal confidence percentage
        iv_at_creation: IV at strike
        expiry_date: Option expiry date
        call_oi_change_at_creation: Call OI change when trade created
        put_oi_change_at_creation: Put OI change when trade created
        pcr_at_creation: Put-Call Ratio at creation
        max_pain_at_creation: Max pain strike at creation
        support_at_creation: Support level from OI clusters
        resistance_at_creation: Resistance level from OI clusters
        trade_reasoning: Human-readable summary of why trade was taken

    Returns:
        The ID of the created setup
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trade_setups
            (created_at, direction, strike, option_type, moneyness,
             entry_premium, sl_premium, target1_premium, target2_premium,
             risk_pct, spot_at_creation, verdict_at_creation,
             signal_confidence, iv_at_creation, expiry_date, status,
             call_oi_change_at_creation, put_oi_change_at_creation,
             pcr_at_creation, max_pain_at_creation, support_at_creation,
             resistance_at_creation, trade_reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING',
                    ?, ?, ?, ?, ?, ?, ?)
        """, (
            created_at.isoformat(),
            direction,
            strike,
            option_type,
            moneyness,
            entry_premium,
            sl_premium,
            target1_premium,
            target2_premium,
            risk_pct,
            spot_at_creation,
            verdict_at_creation,
            signal_confidence,
            iv_at_creation,
            expiry_date,
            call_oi_change_at_creation,
            put_oi_change_at_creation,
            pcr_at_creation,
            max_pain_at_creation,
            support_at_creation or 0,
            resistance_at_creation or 0,
            trade_reasoning
        ))
        conn.commit()
        return cursor.lastrowid


def get_active_trade_setup() -> Optional[dict]:
    """
    Get the current PENDING or ACTIVE trade setup.

    Returns:
        Dict with setup details or None if no active setup
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM trade_setups
            WHERE status IN ('PENDING', 'ACTIVE')
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def update_trade_setup_status(setup_id: int, status: str, **kwargs):
    """
    Update a trade setup's status and optional fields.

    Args:
        setup_id: The setup ID to update
        status: New status (PENDING, ACTIVE, WON, LOST, EXPIRED, CANCELLED)
        **kwargs: Additional fields to update (e.g., activated_at, exit_premium, etc.)
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # Build dynamic update query
        fields = ["status = ?"]
        values = [status]

        for key, value in kwargs.items():
            fields.append(f"{key} = ?")
            if isinstance(value, datetime):
                values.append(value.isoformat())
            else:
                values.append(value)

        values.append(setup_id)

        query = f"UPDATE trade_setups SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(query, values)
        conn.commit()


def get_trade_setup_stats(lookback_days: int = 30) -> dict:
    """
    Get trade setup win rate statistics.

    Args:
        lookback_days: Number of days to look back

    Returns:
        Dict with total, wins, losses, win_rate, avg_profit_loss
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        cutoff = datetime.now() - timedelta(days=lookback_days)

        # Get overall stats for resolved trades
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN status = 'LOST' THEN 1 ELSE 0 END) as losses,
                AVG(profit_loss_pct) as avg_profit_loss,
                AVG(CASE WHEN status = 'WON' THEN profit_loss_pct END) as avg_win,
                AVG(CASE WHEN status = 'LOST' THEN profit_loss_pct END) as avg_loss
            FROM trade_setups
            WHERE status IN ('WON', 'LOST')
              AND resolved_at IS NOT NULL
              AND created_at >= ?
        """, (cutoff.isoformat(),))

        row = cursor.fetchone()

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        win_rate = (wins / total * 100) if total > 0 else 0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "avg_profit_loss": round(row["avg_profit_loss"] or 0, 2),
            "avg_win": round(row["avg_win"] or 0, 2),
            "avg_loss": round(row["avg_loss"] or 0, 2)
        }


def get_recent_trade_setups(limit: int = 10) -> list:
    """Get recent trade setups for history display."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM trade_setups
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_trade_history(limit: int = 50, offset: int = 0, days: int = 30,
                      status_filter: str = None, direction_filter: str = None) -> list:
    """
    Get historical trade setups with pagination and filters.

    Args:
        limit: Maximum number of trades to return
        offset: Number of trades to skip (for pagination)
        days: Number of days to look back
        status_filter: Filter by status (WON, LOST, EXPIRED, CANCELLED)
        direction_filter: Filter by direction (BUY_CALL, BUY_PUT)

    Returns:
        List of trade setup dicts ordered by resolved_at DESC (most recent first)
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # Build query with filters
        conditions = ["status IN ('WON', 'LOST', 'EXPIRED', 'CANCELLED')"]
        params = []

        # Date filter
        cutoff = datetime.now() - timedelta(days=days)
        conditions.append("created_at >= ?")
        params.append(cutoff.isoformat())

        # Status filter
        if status_filter:
            conditions.append("status = ?")
            params.append(status_filter)

        # Direction filter
        if direction_filter:
            conditions.append("direction = ?")
            params.append(direction_filter)

        where_clause = " AND ".join(conditions)
        params.extend([limit, offset])

        cursor.execute(f"""
            SELECT * FROM trade_setups
            WHERE {where_clause}
            ORDER BY COALESCE(resolved_at, created_at) DESC
            LIMIT ? OFFSET ?
        """, params)

        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_last_resolved_trade() -> Optional[dict]:
    """
    Get the most recent resolved trade (WON, LOST, CANCELLED, or EXPIRED).

    Returns:
        Dict with trade setup details or None if no resolved trades
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM trade_setups
            WHERE status IN ('WON', 'LOST', 'CANCELLED', 'EXPIRED')
              AND resolved_at IS NOT NULL
            ORDER BY resolved_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


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
