"""
V-Shape Recovery Detector — Standalone module for detecting V-shape reversals.

Detects intraday V-shape recovery setups by monitoring spot drawdown from day high
and evaluating a set of 7 conditions across 3 tiers to determine signal strength.

Signal Levels:
  FORMING  — 2+ conditions met during drawdown (watch-list)
  LIKELY   — 3+ conditions met during drawdown (Telegram alert)
  CONFIRMED — Spot recovered 0.15% from day low after FORMING/LIKELY (Telegram alert)

Conditions (7 total across 3 tiers):
  Tier 1: Futures basis positive, PCR extreme then reversing
  Tier 2: Score near zero OR capitulation snapback, low confidence, PM reversal cluster
  Tier 3: Futures OI rising during decline, bear trap detected
"""

import json
from datetime import datetime, time, timedelta
from typing import Optional, Dict, List

from database import get_connection
from logger import get_logger

log = get_logger("v_shape_detector")

# ===== CONSTANTS =====
DRAWDOWN_THRESHOLD_PCT = 0.30       # Min 0.3% spot decline from day high
FUTURES_BASIS_POSITIVE = 30.0       # Tier 1: basis > +30
PCR_EXTREME_HIGH = 1.5
PCR_EXTREME_LOW = 0.6
COMBINED_SCORE_NEAR_ZERO = 5.0      # |score| < 5
CAPITULATION_SCORE = -40.0          # score < -40
LOW_CONFIDENCE_THRESHOLD = 70.0
PM_REVERSAL_WINDOW_MIN = 60
V_FORMING_MIN = 2                   # conditions for FORMING
V_LIKELY_MIN = 3                    # conditions for LIKELY
SPOT_REVERSAL_PCT = 0.15            # recovery for CONFIRMED

# Time window
V_SHAPE_TIME_START = time(9, 30)
V_SHAPE_TIME_END = time(15, 0)


def init_v_shape_tables():
    """Create v_shape_signals table if it doesn't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS v_shape_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at DATETIME NOT NULL,
                signal_level TEXT NOT NULL,
                spot_price REAL NOT NULL,
                day_open REAL,
                day_high REAL,
                day_low REAL,
                drawdown_pct REAL,
                conditions_met TEXT,
                conditions_count INTEGER,
                futures_basis REAL,
                pcr REAL,
                combined_score REAL,
                signal_confidence REAL,
                pm_reversal_count INTEGER DEFAULT 0,
                futures_oi_change REAL DEFAULT 0,
                trap_warning INTEGER DEFAULT 0,
                spot_at_resolution REAL,
                resolved_at DATETIME,
                recovery_pct REAL,
                was_correct INTEGER,
                notes TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_v_shape_detected
            ON v_shape_signals(detected_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_v_shape_level
            ON v_shape_signals(signal_level, detected_at)
        """)
        conn.commit()
        log.info("V-shape tables initialized")


class VShapeDetector:
    """Detects intraday V-shape recovery setups from OI analysis snapshots."""

    def __init__(self):
        """Initialize detector and daily state."""
        init_v_shape_tables()
        self._reset_daily_state()

    def _reset_daily_state(self):
        """Reset all state for a new trading day."""
        self._current_date = None
        self._day_open = None
        self._day_high = None
        self._day_low = None

        # Rolling histories (keep last 20 candles)
        self._spot_history = []
        self._basis_history = []
        self._score_history = []
        self._pcr_history = []
        self._confidence_history = []

        # Signal tracking
        self._last_signal_level = None
        self._last_signal_time = None

    def _ensure_day(self):
        """Reset state if a new trading day has started."""
        today = datetime.now().date()
        if self._current_date != today:
            self._reset_daily_state()
            self._current_date = today

    def evaluate(self, analysis: dict, futures_basis: float = 0,
                 futures_oi: float = 0, futures_oi_change: float = 0) -> Optional[Dict]:
        """
        Main entry point — called every 3 minutes by the scheduler.

        Args:
            analysis: OI analysis dict from analyze_tug_of_war()
            futures_basis: Current futures basis (futures - spot)
            futures_oi: Current futures OI
            futures_oi_change: Change in futures OI from previous snapshot

        Returns:
            Signal dict if new signal detected, None otherwise
        """
        self._ensure_day()
        now = datetime.now()

        # Time guard
        if now.time() < V_SHAPE_TIME_START or now.time() > V_SHAPE_TIME_END:
            return None

        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return None

        # Update rolling state
        self._update_state(now, spot, futures_basis, analysis)

        # Calculate drawdown from day high
        drawdown_pct = self._calculate_drawdown(spot)

        # If NOT in drawdown, check for CONFIRMED (recovery after previous signal)
        if drawdown_pct < DRAWDOWN_THRESHOLD_PCT:
            confirmed = self._check_confirmation(spot, now)
            if confirmed:
                return confirmed
            return None

        # We are in drawdown (>= 0.30%) — evaluate 7 conditions
        conditions_met = []

        pcr = analysis.get("pcr", 0) or 0
        combined_score = analysis.get("combined_score", 0) or 0
        confidence = analysis.get("signal_confidence", 0) or 0
        bear_trap = analysis.get("bear_trap", False)

        # ----- Tier 1 -----
        # Condition 1: Futures basis positive during decline
        if futures_basis > FUTURES_BASIS_POSITIVE:
            conditions_met.append("futures_basis_positive")

        # Condition 2: PCR extreme then reversing
        if self._is_pcr_extreme_reversing(pcr):
            conditions_met.append("pcr_extreme_reversing")

        # ----- Tier 2 -----
        # Condition 3: Score near zero (indecision)
        if abs(combined_score) < COMBINED_SCORE_NEAR_ZERO:
            conditions_met.append("score_near_zero")

        # Condition 4: Capitulation snapback (score was < -40 then recovered 15+)
        if self._is_capitulation_snapback(combined_score):
            conditions_met.append("capitulation_snapback")

        # Condition 5: Low confidence (uncertainty = reversal potential)
        if 0 < confidence < LOW_CONFIDENCE_THRESHOLD:
            conditions_met.append("low_confidence")

        # Condition 6: PM reversal cluster in last 60 min
        pm_count = self._has_recent_pm_reversals()
        if pm_count > 0:
            conditions_met.append("pm_reversal_cluster")

        # ----- Tier 3 -----
        # Condition 7a: Futures OI rising during decline (smart money accumulating)
        if futures_oi_change > 0:
            conditions_met.append("futures_oi_rising")

        # Condition 7b: Bear trap detected by analyzer
        if bear_trap:
            conditions_met.append("bear_trap")

        conditions_count = len(conditions_met)

        # Determine signal level
        if conditions_count >= V_LIKELY_MIN:
            level = "LIKELY"
        elif conditions_count >= V_FORMING_MIN:
            level = "FORMING"
        else:
            return None

        # Don't downgrade or re-emit same level
        level_rank = {"FORMING": 1, "LIKELY": 2, "CONFIRMED": 3}
        if self._last_signal_level:
            if level_rank.get(level, 0) <= level_rank.get(self._last_signal_level, 0):
                return None

        # Save and alert
        signal = {
            "signal_level": level,
            "spot_price": spot,
            "day_open": self._day_open,
            "day_high": self._day_high,
            "day_low": self._day_low,
            "drawdown_pct": round(drawdown_pct, 3),
            "conditions_met": conditions_met,
            "conditions_count": conditions_count,
            "futures_basis": futures_basis,
            "pcr": pcr,
            "combined_score": combined_score,
            "signal_confidence": confidence,
            "pm_reversal_count": pm_count if pm_count else 0,
            "futures_oi_change": futures_oi_change,
            "trap_warning": 1 if bear_trap else 0,
            "detected_at": now.isoformat(),
        }

        self._save_signal(now, signal)
        self._last_signal_level = level
        self._last_signal_time = now

        # Send Telegram for LIKELY (FORMING is silent)
        if level == "LIKELY":
            self._send_alert(signal)

        log.info("V-shape signal detected",
                 level=level, drawdown=f"{drawdown_pct:.2f}%",
                 conditions=conditions_count, met=", ".join(conditions_met))

        return signal

    def _update_state(self, now: datetime, spot: float, futures_basis: float,
                      analysis: dict):
        """Update rolling histories and day extremes."""
        # Day open (first spot of the day)
        if self._day_open is None:
            self._day_open = spot

        # Track day high/low
        if self._day_high is None or spot > self._day_high:
            self._day_high = spot
        if self._day_low is None or spot < self._day_low:
            self._day_low = spot

        # Append to rolling histories (keep last 20)
        self._spot_history.append(spot)
        self._basis_history.append(futures_basis)
        self._score_history.append(analysis.get("combined_score", 0) or 0)
        self._pcr_history.append(analysis.get("pcr", 0) or 0)
        self._confidence_history.append(analysis.get("signal_confidence", 0) or 0)

        for history in [self._spot_history, self._basis_history,
                        self._score_history, self._pcr_history,
                        self._confidence_history]:
            if len(history) > 20:
                history.pop(0)

    def _calculate_drawdown(self, spot: float) -> float:
        """Calculate percentage drawdown from day high."""
        if not self._day_high or self._day_high <= 0:
            return 0.0
        return (self._day_high - spot) / self._day_high * 100

    def _is_pcr_extreme_reversing(self, current_pcr: float) -> bool:
        """
        Check if PCR went to extreme and is now reversing.
        Looks at last 6 PCR values for extreme (>1.5 or <0.6) then move back toward 1.0.
        """
        if len(self._pcr_history) < 6:
            return False

        recent = self._pcr_history[-6:]
        had_extreme = False
        for val in recent[:-1]:  # Exclude current
            if val > PCR_EXTREME_HIGH or val < PCR_EXTREME_LOW:
                had_extreme = True
                break

        if not had_extreme:
            return False

        # Check if current is closer to 1.0 than the extreme
        if current_pcr <= 0:
            return False
        return abs(current_pcr - 1.0) < abs(recent[0] - 1.0)

    def _is_capitulation_snapback(self, current_score: float) -> bool:
        """
        Check if score was below -40 (capitulation) and has recovered by 15+.
        Looks at last 5 score values.
        """
        if len(self._score_history) < 5:
            return False

        recent = self._score_history[-5:]
        min_score = min(recent[:-1])  # Exclude current

        if min_score >= CAPITULATION_SCORE:
            return False

        # Recovery of at least 15 points from the capitulation low
        return current_score - min_score >= 15

    def _has_recent_pm_reversals(self) -> int:
        """
        Query detected_patterns for PM reversal patterns in the last 60 minutes.
        Returns count of PM reversal patterns found.
        """
        try:
            cutoff = datetime.now() - timedelta(minutes=PM_REVERSAL_WINDOW_MIN)
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM detected_patterns
                    WHERE pattern_type LIKE 'PM_%REVERSAL%'
                    AND detected_at >= ?
                """, (cutoff.strftime('%Y-%m-%d %H:%M:%S'),))
                row = cursor.fetchone()
                return row["cnt"] if row else 0
        except Exception as e:
            log.debug("PM reversal query failed", error=str(e))
            return 0

    def _check_confirmation(self, spot: float, now: datetime) -> Optional[Dict]:
        """
        Check if spot has recovered enough from day low to CONFIRM a previous signal.
        Requires a prior FORMING or LIKELY signal, and spot up 0.15% from day low.
        """
        if not self._last_signal_level:
            return None
        if self._last_signal_level == "CONFIRMED":
            return None
        if not self._day_low or self._day_low <= 0:
            return None

        recovery_pct = (spot - self._day_low) / self._day_low * 100

        if recovery_pct < SPOT_REVERSAL_PCT:
            return None

        # CONFIRMED
        signal = {
            "signal_level": "CONFIRMED",
            "spot_price": spot,
            "day_open": self._day_open,
            "day_high": self._day_high,
            "day_low": self._day_low,
            "drawdown_pct": 0.0,
            "conditions_met": ["recovery_confirmed"],
            "conditions_count": 0,
            "futures_basis": self._basis_history[-1] if self._basis_history else 0,
            "pcr": self._pcr_history[-1] if self._pcr_history else 0,
            "combined_score": self._score_history[-1] if self._score_history else 0,
            "signal_confidence": self._confidence_history[-1] if self._confidence_history else 0,
            "pm_reversal_count": 0,
            "futures_oi_change": 0,
            "trap_warning": 0,
            "recovery_pct": round(recovery_pct, 3),
            "detected_at": now.isoformat(),
        }

        self._save_signal(now, signal)
        self._last_signal_level = "CONFIRMED"
        self._last_signal_time = now

        self._send_alert(signal)

        log.info("V-shape CONFIRMED", recovery=f"{recovery_pct:.2f}%",
                 day_low=self._day_low, spot=spot)

        return signal

    def _save_signal(self, now: datetime, signal: dict):
        """Insert a new signal into v_shape_signals."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO v_shape_signals (
                        detected_at, signal_level, spot_price, day_open, day_high,
                        day_low, drawdown_pct, conditions_met, conditions_count,
                        futures_basis, pcr, combined_score, signal_confidence,
                        pm_reversal_count, futures_oi_change, trap_warning,
                        recovery_pct, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now.strftime('%Y-%m-%d %H:%M:%S'),
                    signal["signal_level"],
                    signal["spot_price"],
                    signal.get("day_open"),
                    signal.get("day_high"),
                    signal.get("day_low"),
                    signal.get("drawdown_pct", 0),
                    json.dumps(signal.get("conditions_met", [])),
                    signal.get("conditions_count", 0),
                    signal.get("futures_basis", 0),
                    signal.get("pcr", 0),
                    signal.get("combined_score", 0),
                    signal.get("signal_confidence", 0),
                    signal.get("pm_reversal_count", 0),
                    signal.get("futures_oi_change", 0),
                    signal.get("trap_warning", 0),
                    signal.get("recovery_pct"),
                    signal.get("notes"),
                ))
                conn.commit()
        except Exception as e:
            log.error("Failed to save V-shape signal", error=str(e))

    def _send_alert(self, signal: dict):
        """Send Telegram alert for LIKELY or CONFIRMED signals."""
        try:
            from alerts import send_telegram

            level = signal["signal_level"]
            spot = signal["spot_price"]
            drawdown = signal.get("drawdown_pct", 0)
            conditions = signal.get("conditions_met", [])
            conditions_count = signal.get("conditions_count", 0)
            recovery = signal.get("recovery_pct", 0)

            if level == "CONFIRMED":
                emoji = "\u2705"
                headline = "V-SHAPE CONFIRMED"
                detail = (
                    f"Spot recovered <code>{recovery:.2f}%</code> from day low "
                    f"<code>{signal.get('day_low', 0):.2f}</code>"
                )
            else:
                emoji = "\u26a0\ufe0f"
                headline = "V-SHAPE LIKELY"
                cond_text = "\n".join(f"  - {c}" for c in conditions)
                detail = (
                    f"Drawdown: <code>{drawdown:.2f}%</code> from day high\n"
                    f"Conditions ({conditions_count}):\n{cond_text}"
                )

            basis = signal.get("futures_basis", 0)
            pcr = signal.get("pcr", 0)
            score = signal.get("combined_score", 0)
            conf = signal.get("signal_confidence", 0)

            message = (
                f"<b>{emoji} {headline}</b>\n\n"
                f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
                f"<b>Day High:</b> <code>{signal.get('day_high', 0):.2f}</code>\n"
                f"<b>Day Low:</b> <code>{signal.get('day_low', 0):.2f}</code>\n\n"
                f"{detail}\n\n"
                f"<b>Basis:</b> <code>{basis:+.1f}</code> | "
                f"<b>PCR:</b> <code>{pcr:.2f}</code>\n"
                f"<b>Score:</b> <code>{score:+.1f}</code> | "
                f"<b>Conf:</b> <code>{conf:.0f}%</code>\n\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send V-shape alert", error=str(e))


# ===== MODULE-LEVEL QUERY FUNCTIONS =====

def get_v_shape_status() -> Optional[Dict]:
    """Get the latest V-shape signal from today."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM v_shape_signals
                WHERE DATE(detected_at) = ?
                ORDER BY detected_at DESC LIMIT 1
            """, (today,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                # Parse conditions_met JSON
                if result.get("conditions_met"):
                    try:
                        result["conditions_met"] = json.loads(result["conditions_met"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                return result
            return None
    except Exception as e:
        log.error("Failed to get V-shape status", error=str(e))
        return None


def is_v_shape_forming() -> bool:
    """Quick check: is a V-shape FORMING or LIKELY today?"""
    status = get_v_shape_status()
    if not status:
        return False
    return status.get("signal_level") in ("FORMING", "LIKELY")


def get_v_shape_signals(days: int = 30) -> List[Dict]:
    """Get historical V-shape signals."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM v_shape_signals
                WHERE detected_at >= datetime('now', ?)
                ORDER BY detected_at DESC
            """, (f"-{days} days",))
            results = []
            for row in cursor.fetchall():
                r = dict(row)
                if r.get("conditions_met"):
                    try:
                        r["conditions_met"] = json.loads(r["conditions_met"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append(r)
            return results
    except Exception as e:
        log.error("Failed to get V-shape signals", error=str(e))
        return []


def get_v_shape_stats() -> Dict:
    """Aggregate stats by signal level."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            # Counts by level
            cursor.execute("""
                SELECT signal_level,
                       COUNT(*) as total,
                       AVG(drawdown_pct) as avg_drawdown,
                       AVG(conditions_count) as avg_conditions,
                       SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct,
                       SUM(CASE WHEN was_correct = 0 THEN 1 ELSE 0 END) as incorrect
                FROM v_shape_signals
                GROUP BY signal_level
            """)
            by_level = {}
            for row in cursor.fetchall():
                r = dict(row)
                by_level[r["signal_level"]] = r

            # Overall
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct,
                       SUM(CASE WHEN was_correct = 0 THEN 1 ELSE 0 END) as incorrect,
                       AVG(recovery_pct) as avg_recovery
                FROM v_shape_signals
                WHERE signal_level = 'CONFIRMED'
            """)
            overall = dict(cursor.fetchone())

            # Most common conditions
            cursor.execute("""
                SELECT conditions_met FROM v_shape_signals
                WHERE conditions_met IS NOT NULL
            """)
            condition_counts = {}
            for row in cursor.fetchall():
                try:
                    conds = json.loads(row["conditions_met"])
                    for c in conds:
                        condition_counts[c] = condition_counts.get(c, 0) + 1
                except (json.JSONDecodeError, TypeError):
                    pass

            return {
                "by_level": by_level,
                "confirmed": overall,
                "top_conditions": dict(
                    sorted(condition_counts.items(), key=lambda x: x[1], reverse=True)[:7]
                ),
            }
    except Exception as e:
        log.error("Failed to get V-shape stats", error=str(e))
        return {"by_level": {}, "confirmed": {}, "top_conditions": {}}


# Initialize tables at import time
init_v_shape_tables()
