"""
Pattern Tracker - Tracks entry timing patterns for high RR setups

This module detects and logs patterns that may indicate optimal entry points:
1. PM Reversal - Premium momentum turning up from extreme negative
2. Shakeout Detection - Price drops significantly then starts recovering
3. Failed Entry Recovery - Price recovers after hitting SL

Usage:
    Called from scheduler.py after each data fetch to detect patterns.
    Run report: uv run python pattern_tracker.py --report
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List
from logger import get_logger

DB_PATH = Path(__file__).parent / "oi_tracker.db"
log = get_logger("pattern_tracker")


def init_pattern_tables():
    """Initialize pattern tracking tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Table for tracking PM history (rolling window)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pm_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            pm_score REAL NOT NULL,
            spot_price REAL NOT NULL,
            verdict TEXT,
            confidence REAL
        )
    """)

    # Table for detected patterns
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detected_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at DATETIME NOT NULL,
            pattern_type TEXT NOT NULL,
            -- Context at detection
            spot_price REAL,
            pm_score REAL,
            pm_prev REAL,
            pm_change REAL,
            confidence REAL,
            verdict TEXT,
            confirmation_status TEXT,
            strike INTEGER,
            option_type TEXT,
            premium_at_detection REAL,
            -- Tracking fields (updated later)
            premium_after_30min REAL,
            premium_after_60min REAL,
            premium_max_2hr REAL,
            premium_min_2hr REAL,
            outcome TEXT,
            outcome_notes TEXT,
            tracked_until DATETIME
        )
    """)

    # Table for tracking failed entries (SL hit, then recovered)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS failed_entry_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_timestamp DATETIME NOT NULL,
            sl_hit_timestamp DATETIME NOT NULL,
            strike INTEGER NOT NULL,
            option_type TEXT NOT NULL,
            entry_premium REAL NOT NULL,
            sl_premium REAL NOT NULL,
            premium_at_sl REAL NOT NULL,
            -- Recovery tracking
            recovered INTEGER DEFAULT 0,
            recovery_timestamp DATETIME,
            recovery_premium REAL,
            max_premium_after_sl REAL,
            would_have_hit_target INTEGER DEFAULT 0,
            target_premium REAL,
            notes TEXT
        )
    """)

    # Index for faster queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pm_history_timestamp
        ON pm_history(timestamp)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_patterns_type
        ON detected_patterns(pattern_type, detected_at)
    """)

    conn.commit()
    conn.close()
    log.info("Pattern tracking tables initialized")


def record_pm_history(timestamp: datetime, pm_score: float, spot_price: float,
                      verdict: str = None, confidence: float = None):
    """Record PM score for history tracking."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO pm_history (timestamp, pm_score, spot_price, verdict, confidence)
        VALUES (?, ?, ?, ?, ?)
    """, (timestamp.isoformat(), pm_score, spot_price, verdict, confidence))

    # Keep only last 100 records to prevent bloat
    cursor.execute("""
        DELETE FROM pm_history
        WHERE id NOT IN (
            SELECT id FROM pm_history ORDER BY timestamp DESC LIMIT 100
        )
    """)

    conn.commit()
    conn.close()


def get_pm_history(limit: int = 10) -> List[Dict]:
    """Get recent PM history."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM pm_history
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def detect_pm_reversal(current_pm: float, pm_history: List[Dict]) -> Optional[Dict]:
    """
    Detect PM reversal pattern.

    Reversal detected when:
    1. PM was very negative (< -50)
    2. PM is now rising (current > previous)
    3. PM has crossed above a threshold (e.g., -50 to -30)

    Returns pattern info if detected, None otherwise.
    """
    if len(pm_history) < 3:
        return None

    # Get last 3 PM values (most recent first)
    pm_values = [h["pm_score"] for h in pm_history[:3]]

    prev_pm = pm_values[1] if len(pm_values) > 1 else 0
    prev_prev_pm = pm_values[2] if len(pm_values) > 2 else 0

    # Pattern 1: PM was falling, now rising from extreme negative
    was_falling = prev_pm < prev_prev_pm
    now_rising = current_pm > prev_pm
    was_extreme_negative = prev_pm < -50

    if was_extreme_negative and was_falling and now_rising:
        return {
            "pattern": "PM_REVERSAL_FROM_EXTREME",
            "description": f"PM turning up from {prev_pm:.1f} to {current_pm:.1f}",
            "pm_prev": prev_pm,
            "pm_change": current_pm - prev_pm,
        }

    # Pattern 2: PM crossing above -50 from below
    crossed_above_50 = prev_pm < -50 and current_pm >= -50
    if crossed_above_50:
        return {
            "pattern": "PM_CROSSED_ABOVE_MINUS50",
            "description": f"PM crossed -50 threshold: {prev_pm:.1f} -> {current_pm:.1f}",
            "pm_prev": prev_pm,
            "pm_change": current_pm - prev_pm,
        }

    # Pattern 3: PM going from very negative to neutral
    recovering_to_neutral = prev_pm < -30 and current_pm > -10
    if recovering_to_neutral:
        return {
            "pattern": "PM_RECOVERING_TO_NEUTRAL",
            "description": f"PM recovering: {prev_pm:.1f} -> {current_pm:.1f}",
            "pm_prev": prev_pm,
            "pm_change": current_pm - prev_pm,
        }

    return None


def detect_shakeout_pattern(analysis: dict, price_history: List[Dict]) -> Optional[Dict]:
    """
    Detect potential shakeout completion.

    Shakeout pattern:
    1. Price dropped significantly (> 15% from recent high)
    2. Price is now recovering (current > recent low)
    3. OI pattern suggests accumulation (put OI increasing)
    """
    if len(price_history) < 5:
        return None

    trade_setup = analysis.get("trade_setup", {})
    if not trade_setup:
        return None

    # This is a placeholder - would need actual price tracking
    # to implement properly
    return None


def log_pattern(pattern_type: str, analysis: dict, pattern_info: dict):
    """Log a detected pattern to the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    trade_setup = analysis.get("trade_setup", {})
    pm = analysis.get("premium_momentum", {})
    pm_score = pm.get("premium_momentum_score", 0) if isinstance(pm, dict) else 0

    cursor.execute("""
        INSERT INTO detected_patterns (
            detected_at, pattern_type, spot_price, pm_score, pm_prev, pm_change,
            confidence, verdict, confirmation_status, strike, option_type,
            premium_at_detection
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        pattern_type,
        analysis.get("spot_price", 0),
        pm_score,
        pattern_info.get("pm_prev", 0),
        pattern_info.get("pm_change", 0),
        analysis.get("signal_confidence", 0),
        analysis.get("verdict", ""),
        analysis.get("confirmation_status", ""),
        trade_setup.get("strike", 0),
        trade_setup.get("option_type", ""),
        trade_setup.get("entry_premium", 0),
    ))

    conn.commit()
    conn.close()

    log.info("Pattern detected", pattern_type=pattern_type,
             description=pattern_info.get("description", ""))


def log_failed_entry(entry_timestamp: str, sl_hit_timestamp: str, strike: int,
                     option_type: str, entry_premium: float, sl_premium: float,
                     premium_at_sl: float, target_premium: float):
    """Log when a trade entry hits SL for recovery tracking."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO failed_entry_tracking (
            entry_timestamp, sl_hit_timestamp, strike, option_type,
            entry_premium, sl_premium, premium_at_sl, target_premium
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry_timestamp, sl_hit_timestamp, strike, option_type,
        entry_premium, sl_premium, premium_at_sl, target_premium
    ))

    conn.commit()
    conn.close()

    log.info("Failed entry logged for recovery tracking",
             strike=strike, entry_premium=entry_premium)


def update_failed_entry_recovery(strike: int, option_type: str, current_premium: float,
                                 timestamp: str):
    """Check and update if a failed entry has recovered."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get recent failed entries for this strike that haven't recovered
    cursor.execute("""
        SELECT * FROM failed_entry_tracking
        WHERE strike = ? AND option_type = ? AND recovered = 0
        ORDER BY sl_hit_timestamp DESC
        LIMIT 1
    """, (strike, option_type))

    row = cursor.fetchone()
    if not row:
        conn.close()
        return

    entry = dict(row)

    # Update max premium tracking
    max_after_sl = entry.get("max_premium_after_sl") or 0
    if current_premium > max_after_sl:
        cursor.execute("""
            UPDATE failed_entry_tracking
            SET max_premium_after_sl = ?
            WHERE id = ?
        """, (current_premium, entry["id"]))

    # Check if recovered (back to entry price)
    if current_premium >= entry["entry_premium"] and not entry["recovered"]:
        cursor.execute("""
            UPDATE failed_entry_tracking
            SET recovered = 1, recovery_timestamp = ?, recovery_premium = ?
            WHERE id = ?
        """, (timestamp, current_premium, entry["id"]))
        log.info("Failed entry RECOVERED!", strike=strike,
                 entry_premium=entry["entry_premium"], recovery_premium=current_premium)

    # Check if would have hit target
    if current_premium >= entry["target_premium"] and not entry["would_have_hit_target"]:
        cursor.execute("""
            UPDATE failed_entry_tracking
            SET would_have_hit_target = 1,
                notes = 'Would have hit target after SL'
            WHERE id = ?
        """, (entry["id"],))
        log.info("Failed entry would have HIT TARGET!",
                 strike=strike, target=entry["target_premium"])

    conn.commit()
    conn.close()


def check_patterns(analysis: dict):
    """
    Main function called after each data fetch to detect patterns.

    Args:
        analysis: The current analysis dict from OI analyzer
    """
    # Get PM score
    pm = analysis.get("premium_momentum", {})
    pm_score = pm.get("premium_momentum_score", 0) if isinstance(pm, dict) else 0

    # Record PM history
    record_pm_history(
        timestamp=datetime.now(),
        pm_score=pm_score,
        spot_price=analysis.get("spot_price", 0),
        verdict=analysis.get("verdict", ""),
        confidence=analysis.get("signal_confidence", 0)
    )

    # Get PM history for pattern detection
    pm_history = get_pm_history(limit=10)

    # Check for PM reversal (basic detection for logging)
    reversal = detect_pm_reversal(pm_score, pm_history)
    if reversal:
        log_pattern(reversal["pattern"], analysis, reversal)

    # ========================================
    # STRONG PM REVERSAL ALERT (PM > 50)
    # ========================================
    # This is our backtested profitable signal:
    # - PM was recently very negative (< -30)
    # - PM is now strongly positive (> 50)
    # - This catches the CONFIRMED reversal, not the early signal
    
    check_strong_pm_reversal_alert(pm_score, pm_history, analysis)

    # Update any failed entry recoveries
    trade_setup = analysis.get("trade_setup", {})
    if trade_setup:
        strike = trade_setup.get("strike", 0)
        option_type = trade_setup.get("option_type", "CE")
        entry_premium = trade_setup.get("entry_premium", 0)

        if strike and entry_premium:
            update_failed_entry_recovery(
                strike, option_type, entry_premium,
                datetime.now().isoformat()
            )


def check_strong_pm_reversal_alert(pm_score: float, pm_history: List[Dict], analysis: dict) -> Optional[Dict]:
    """
    Check for STRONG PM reversal (PM > 50) and return alert data.
    
    Criteria:
    1. Current PM > 50 (strong bullish momentum)
    2. Recent PM was < -30 (was bearish recently)
    
    This signal has 75% win rate with +12.5 pts avg in backtest.
    
    Returns:
        Alert dict if signal detected, None otherwise
    """
    # Threshold configuration
    PM_STRONG_THRESHOLD = 50
    PM_WAS_NEGATIVE_THRESHOLD = -30
    
    # Check current PM is strong positive
    if pm_score < PM_STRONG_THRESHOLD:
        return None
    
    # Check if PM was negative recently (within last 5 readings)
    was_negative_recently = False
    pm_was = 0
    for h in pm_history[:5]:
        if h.get("pm_score", 0) < PM_WAS_NEGATIVE_THRESHOLD:
            was_negative_recently = True
            pm_was = h.get("pm_score", 0)
            break
    
    if not was_negative_recently:
        return None
    
    # We have a strong PM reversal!
    log.info("Strong PM reversal detected!", pm_score=pm_score, was_negative=pm_was)
    
    # Log the pattern
    pattern_info = {
        "pattern": "PM_STRONG_REVERSAL_ALERT",
        "description": f"PM reversed from {pm_was:.1f} to {pm_score:.1f} (strong bullish)",
        "pm_prev": pm_was,
        "pm_change": pm_score - pm_was,
    }
    log_pattern("PM_STRONG_REVERSAL_ALERT", analysis, pattern_info)
    
    # Build alert data for dashboard
    trade_setup = analysis.get("trade_setup", {})
    spot_price = analysis.get("spot_price", 0)
    confidence = analysis.get("signal_confidence", 0)
    
    # For CALL entry, calculate ATM CE strike
    atm_strike = int(round(spot_price / 50) * 50)
    
    # Estimate CE premium (rough approximation)
    ce_entry = trade_setup.get("entry_premium", 100)
    ce_target = ce_entry * 1.4  # +40% target
    ce_sl = ce_entry * 0.65  # -35% SL
    
    alert_data = {
        "active": True,
        "type": "PM_STRONG_REVERSAL",
        "direction": "CALL",
        "timestamp": datetime.now().isoformat(),
        "spot_price": spot_price,
        "pm_score": pm_score,
        "pm_was": pm_was,
        "pm_change": pm_score - pm_was,
        "confidence": confidence,
        "strike": atm_strike,
        "entry_premium": round(ce_entry, 2),
        "target_premium": round(ce_target, 2),
        "sl_premium": round(ce_sl, 2),
        "message": f"PM Reversal: {pm_was:.0f} → {pm_score:.0f}",
        "backtest_stats": {
            "win_rate": 75,
            "avg_profit": 12.5,
            "sample_size": 4
        }
    }
    
    return alert_data


def get_active_alert(analysis: dict) -> Optional[Dict]:
    """
    Get active alert for dashboard display.
    Called from scheduler after check_patterns.
    
    Returns alert data if active, None otherwise.
    """
    pm = analysis.get("premium_momentum", {})
    pm_score = pm.get("premium_momentum_score", 0) if isinstance(pm, dict) else 0
    
    pm_history = get_pm_history(limit=10)
    
    return check_strong_pm_reversal_alert(pm_score, pm_history, analysis)


def get_pattern_stats() -> Dict:
    """Get statistics on detected patterns."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Count patterns by type
    cursor.execute("""
        SELECT pattern_type, COUNT(*) as count
        FROM detected_patterns
        GROUP BY pattern_type
    """)
    pattern_counts = {row["pattern_type"]: row["count"] for row in cursor.fetchall()}

    # Get failed entry recovery stats
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(recovered) as recovered,
            SUM(would_have_hit_target) as would_hit_target
        FROM failed_entry_tracking
    """)
    row = cursor.fetchone()
    failed_entry_stats = dict(row) if row else {}

    conn.close()

    return {
        "pattern_counts": pattern_counts,
        "failed_entry_stats": failed_entry_stats,
    }


def generate_report():
    """Generate a report of detected patterns and their outcomes."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("=" * 100)
    print("PATTERN TRACKING REPORT")
    print("=" * 100)

    # PM History
    print("\n--- RECENT PM HISTORY ---")
    cursor.execute("""
        SELECT * FROM pm_history
        ORDER BY timestamp DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()

    if rows:
        print(f"{'Timestamp':<20} | {'PM Score':>10} | {'Spot':>10} | {'Verdict':<20}")
        print("-" * 70)
        for row in rows:
            ts = row["timestamp"][11:19] if row["timestamp"] else "N/A"
            print(f"{ts:<20} | {row['pm_score']:>+10.1f} | {row['spot_price']:>10.2f} | "
                  f"{(row['verdict'] or '')[:20]:<20}")
    else:
        print("No PM history recorded yet.")

    # Detected Patterns
    print("\n--- DETECTED PATTERNS ---")
    cursor.execute("""
        SELECT * FROM detected_patterns
        ORDER BY detected_at DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()

    if rows:
        print(f"{'Time':<10} | {'Pattern':<30} | {'PM':>8} | {'PM Chg':>8} | {'Conf':>6} | {'Strike':>8}")
        print("-" * 90)
        for row in rows:
            ts = row["detected_at"][11:19] if row["detected_at"] else "N/A"
            print(f"{ts:<10} | {row['pattern_type']:<30} | {row['pm_score']:>+8.1f} | "
                  f"{row['pm_change']:>+8.1f} | {row['confidence']:>5.0f}% | {row['strike']:>8}")
    else:
        print("No patterns detected yet. Patterns will be logged as data comes in.")

    # Failed Entry Tracking
    print("\n--- FAILED ENTRY RECOVERY TRACKING ---")
    cursor.execute("""
        SELECT * FROM failed_entry_tracking
        ORDER BY sl_hit_timestamp DESC
        LIMIT 10
    """)
    rows = cursor.fetchall()

    if rows:
        print(f"{'SL Hit Time':<12} | {'Strike':>8} | {'Entry':>8} | {'SL':>8} | "
              f"{'Max After':>10} | {'Recovered':>10} | {'Hit Target':>10}")
        print("-" * 90)
        for row in rows:
            ts = row["sl_hit_timestamp"][11:19] if row["sl_hit_timestamp"] else "N/A"
            recovered = "YES" if row["recovered"] else "NO"
            hit_target = "YES" if row["would_have_hit_target"] else "NO"
            max_after = row["max_premium_after_sl"] or 0
            print(f"{ts:<12} | {row['strike']:>8} | {row['entry_premium']:>8.2f} | "
                  f"{row['sl_premium']:>8.2f} | {max_after:>10.2f} | {recovered:>10} | {hit_target:>10}")
    else:
        print("No failed entries tracked yet.")

    # Summary Stats
    print("\n--- SUMMARY STATISTICS ---")
    stats = get_pattern_stats()

    print("\nPattern Counts:")
    if stats["pattern_counts"]:
        for pattern, count in stats["pattern_counts"].items():
            print(f"  {pattern}: {count}")
    else:
        print("  No patterns detected yet")

    print("\nFailed Entry Recovery:")
    fe_stats = stats["failed_entry_stats"]
    if fe_stats.get("total"):
        total = fe_stats["total"]
        recovered = fe_stats.get("recovered") or 0
        hit_target = fe_stats.get("would_hit_target") or 0
        print(f"  Total failed entries: {total}")
        print(f"  Recovered to entry: {recovered} ({recovered/total*100:.0f}%)")
        print(f"  Would have hit target: {hit_target} ({hit_target/total*100:.0f}%)")
    else:
        print("  No failed entries tracked yet")

    # What to watch for
    print("\n" + "=" * 100)
    print("WHAT TO WATCH FOR")
    print("=" * 100)
    print("""
The pattern tracker is monitoring for:

1. PM REVERSAL PATTERNS:
   - PM_REVERSAL_FROM_EXTREME: PM turning up from < -50
   - PM_CROSSED_ABOVE_MINUS50: PM crossing the -50 threshold
   - PM_RECOVERING_TO_NEUTRAL: PM going from very negative to neutral

2. FAILED ENTRY RECOVERY:
   - Tracks when trades hit SL
   - Monitors if price later recovers
   - Records if target would have been hit after SL

As more data comes in, we'll see:
- How often PM reversals lead to good entries
- What % of failed entries recover
- Whether waiting for PM reversal improves entry timing
""")

    conn.close()


# Initialize tables on import
init_pattern_tables()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pattern Tracker")
    parser.add_argument("--report", action="store_true", help="Generate pattern report")
    args = parser.parse_args()

    if args.report:
        generate_report()
    else:
        print("Pattern Tracker initialized.")
        print("Run with --report to see detected patterns.")
        print("Patterns are automatically detected when scheduler runs.")
