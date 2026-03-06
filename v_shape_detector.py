"""
V-Shape Recovery Detector — Detects intraday V-shape reversals.

Monitors spot drawdown from day high and evaluates 7 conditions across 3 tiers.

Signal Levels (state machine):
  FORMING   -> 2+ conditions met during drawdown (silent)
  LIKELY    -> 3+ conditions met during drawdown (Telegram alert)
  CONFIRMED -> Spot recovered 0.25% from day low after FORMING/LIKELY (Telegram alert)
  RESOLVED  -> V-shape completed with outcome tracking:
               V_SUCCEEDED — spot made new day high
               V_PARTIAL   — recovery held 50-80% after 30 min
               V_FAILED    — retraced >61.8% of recovery or broke below V-low
               V_EXPIRED   — 30 min elapsed, going sideways

After resolution, 45-min cooldown before next V-shape can fire.

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
DRAWDOWN_THRESHOLD_PCT = 0.35       # Min 0.35% spot decline from day high
FUTURES_BASIS_POSITIVE = 30.0       # Tier 1: basis > +30
PCR_EXTREME_HIGH = 1.5
PCR_EXTREME_LOW = 0.6
COMBINED_SCORE_NEAR_ZERO = 5.0      # |score| < 5
CAPITULATION_SCORE = -40.0          # score < -40
LOW_CONFIDENCE_THRESHOLD = 70.0
PM_REVERSAL_WINDOW_MIN = 60
V_FORMING_MIN = 2                   # conditions for FORMING
V_LIKELY_MIN = 3                    # conditions for LIKELY
SPOT_REVERSAL_PCT = 0.25            # recovery for CONFIRMED

# Resolution constants
RETRACE_FAILURE_PCT = 0.618         # 61.8% retracement = FAILED
RESOLUTION_TIMEOUT_MIN = 30         # max time before auto-resolve
COOLDOWN_AFTER_RESOLUTION_MIN = 45  # cooldown before next V-shape

# Duration guards
MIN_DRAWDOWN_DURATION_MIN = 9       # must be in drawdown 9 min before LIKELY
MIN_LIKELY_TO_CONFIRMED_MIN = 6     # min gap between LIKELY and CONFIRMED

# Time windows
V_SHAPE_TIME_START = time(9, 15)
V_SHAPE_TIME_END = time(15, 0)
V_SHAPE_EARLY_CUTOFF = time(9, 45)  # stricter before this
V_LIKELY_MIN_EARLY = 4              # need 4+ conditions before 9:45

# Expiry-day scaling (NIFTY weekly expiry = Tuesday)
EXPIRY_THRESHOLD_MULTIPLIER = 1.3

# Frontend auto-dismiss durations (minutes)
DISMISS_SUCCEEDED_MIN = 5
DISMISS_PARTIAL_MIN = 5
DISMISS_FAILED_MIN = 10


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
                resolution_type TEXT,
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

        # Migrate: add resolution_type column if missing
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(v_shape_signals)").fetchall()]
        if "resolution_type" not in cols:
            cursor.execute("ALTER TABLE v_shape_signals ADD COLUMN resolution_type TEXT")
            conn.commit()
            log.info("Migrated v_shape_signals: added resolution_type column")


def _is_expiry_day() -> bool:
    """Check if today is Tuesday (NIFTY weekly expiry)."""
    return datetime.now().weekday() == 1  # 0=Mon, 1=Tue


def _get_thresholds() -> tuple:
    """Return (drawdown_threshold, recovery_threshold) scaled for expiry days."""
    dd = DRAWDOWN_THRESHOLD_PCT
    rec = SPOT_REVERSAL_PCT
    if _is_expiry_day():
        dd *= EXPIRY_THRESHOLD_MULTIPLIER
        rec *= EXPIRY_THRESHOLD_MULTIPLIER
    return dd, rec


class VShapeDetector:
    """Detects intraday V-shape recovery setups from OI analysis snapshots."""

    def __init__(self):
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

        # Drawdown duration tracking
        self._drawdown_start_time = None

        # Confirmation tracking (for resolution)
        self._confirmed_at = None
        self._confirmed_spot = None
        self._v_low = None  # day low at time of confirmation

        # Resolution tracking
        self._resolved_at = None
        self._resolution_type = None

    def _ensure_day(self):
        """Reset state if a new trading day has started."""
        today = datetime.now().date()
        if self._current_date != today:
            self._reset_daily_state()
            self._current_date = today

    def _in_cooldown(self, now: datetime) -> bool:
        """Check if we're in post-resolution cooldown."""
        if not self._resolved_at:
            return False
        elapsed = (now - self._resolved_at).total_seconds() / 60
        return elapsed < COOLDOWN_AFTER_RESOLUTION_MIN

    def evaluate(self, analysis: dict, futures_basis: float = 0,
                 futures_oi: float = 0, futures_oi_change: float = 0) -> Optional[Dict]:
        """
        Main entry point -- called every 3 minutes by the scheduler.

        Returns signal dict if new signal detected or resolution occurred, None otherwise.
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

        # If in cooldown after resolution, do nothing
        if self._in_cooldown(now):
            return None

        # If CONFIRMED, check resolution before anything else
        if self._last_signal_level == "CONFIRMED":
            resolution = self._check_resolution(spot, now)
            if resolution:
                return resolution
            return None  # Still in CONFIRMED, waiting for resolution

        dd_threshold, rec_threshold = _get_thresholds()

        # Calculate drawdown from day high
        drawdown_pct = self._calculate_drawdown(spot)

        # Track drawdown duration
        if drawdown_pct >= dd_threshold:
            if self._drawdown_start_time is None:
                self._drawdown_start_time = now
        else:
            self._drawdown_start_time = None

        # If NOT in drawdown, check for CONFIRMED (recovery after previous signal)
        if drawdown_pct < dd_threshold:
            confirmed = self._check_confirmation(spot, now, rec_threshold)
            if confirmed:
                return confirmed
            return None

        # We are in drawdown -- evaluate 7 conditions
        conditions_met = []

        pcr = analysis.get("pcr", 0) or 0
        combined_score = analysis.get("combined_score", 0) or 0
        confidence = analysis.get("signal_confidence", 0) or 0
        bear_trap = analysis.get("bear_trap", False)

        # ----- Tier 1 -----
        if futures_basis > FUTURES_BASIS_POSITIVE:
            conditions_met.append("futures_basis_positive")

        if self._is_pcr_extreme_reversing(pcr):
            conditions_met.append("pcr_extreme_reversing")

        # ----- Tier 2 -----
        if abs(combined_score) < COMBINED_SCORE_NEAR_ZERO:
            conditions_met.append("score_near_zero")

        if self._is_capitulation_snapback(combined_score):
            conditions_met.append("capitulation_snapback")

        if 0 < confidence < LOW_CONFIDENCE_THRESHOLD:
            conditions_met.append("low_confidence")

        pm_count = self._has_recent_pm_reversals()
        if pm_count > 0:
            conditions_met.append("pm_reversal_cluster")

        # ----- Tier 3 -----
        if futures_oi_change > 0:
            conditions_met.append("futures_oi_rising")

        if bear_trap:
            conditions_met.append("bear_trap")

        conditions_count = len(conditions_met)

        # Time-of-day gating: before 9:45 require more conditions
        likely_min = V_LIKELY_MIN_EARLY if now.time() < V_SHAPE_EARLY_CUTOFF else V_LIKELY_MIN

        # Determine signal level
        if conditions_count >= likely_min:
            level = "LIKELY"
        elif conditions_count >= V_FORMING_MIN:
            level = "FORMING"
        else:
            return None

        # Drawdown duration guard: must be in drawdown >= 9 min before LIKELY
        if level == "LIKELY" and self._drawdown_start_time:
            drawdown_duration = (now - self._drawdown_start_time).total_seconds() / 60
            if drawdown_duration < MIN_DRAWDOWN_DURATION_MIN:
                level = "FORMING"  # Downgrade to FORMING until duration met

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

        if level == "LIKELY":
            self._send_alert(signal)

        log.info("V-shape signal detected",
                 level=level, drawdown=f"{drawdown_pct:.2f}%",
                 conditions=conditions_count, met=", ".join(conditions_met))

        return signal

    # ===== STATE UPDATES =====

    def _update_state(self, now: datetime, spot: float, futures_basis: float,
                      analysis: dict):
        if self._day_open is None:
            self._day_open = spot

        if self._day_high is None or spot > self._day_high:
            self._day_high = spot
        if self._day_low is None or spot < self._day_low:
            self._day_low = spot

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
        if not self._day_high or self._day_high <= 0:
            return 0.0
        return (self._day_high - spot) / self._day_high * 100

    # ===== CONDITION CHECKS =====

    def _is_pcr_extreme_reversing(self, current_pcr: float) -> bool:
        if len(self._pcr_history) < 6:
            return False

        recent = self._pcr_history[-6:]
        had_extreme = False
        for val in recent[:-1]:
            if val > PCR_EXTREME_HIGH or val < PCR_EXTREME_LOW:
                had_extreme = True
                break

        if not had_extreme:
            return False

        if current_pcr <= 0:
            return False
        return abs(current_pcr - 1.0) < abs(recent[0] - 1.0)

    def _is_capitulation_snapback(self, current_score: float) -> bool:
        if len(self._score_history) < 5:
            return False

        recent = self._score_history[-5:]
        min_score = min(recent[:-1])

        if min_score >= CAPITULATION_SCORE:
            return False

        return current_score - min_score >= 15

    def _has_recent_pm_reversals(self) -> int:
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

    # ===== CONFIRMATION =====

    def _check_confirmation(self, spot: float, now: datetime,
                            rec_threshold: float) -> Optional[Dict]:
        """Check if spot recovered enough from day low to CONFIRM a previous signal."""
        if not self._last_signal_level:
            return None
        if self._last_signal_level not in ("FORMING", "LIKELY"):
            return None
        if not self._day_low or self._day_low <= 0:
            return None

        # Min gap: LIKELY must have been at least 6 min ago
        if self._last_signal_time:
            gap_min = (now - self._last_signal_time).total_seconds() / 60
            if gap_min < MIN_LIKELY_TO_CONFIRMED_MIN:
                return None

        recovery_pct = (spot - self._day_low) / self._day_low * 100

        if recovery_pct < rec_threshold:
            return None

        # CONFIRMED
        self._confirmed_at = now
        self._confirmed_spot = spot
        self._v_low = self._day_low

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

    # ===== RESOLUTION =====

    def _check_resolution(self, spot: float, now: datetime) -> Optional[Dict]:
        """
        Check resolution conditions for a CONFIRMED V-shape.

        Returns resolution signal dict if resolved, None if still pending.
        """
        if not self._confirmed_at or not self._confirmed_spot or not self._v_low:
            return None

        elapsed_min = (now - self._confirmed_at).total_seconds() / 60
        recovery_range = self._confirmed_spot - self._v_low

        # Prevent division by zero
        if recovery_range <= 0:
            return self._resolve(spot, now, "V_EXPIRED", "Zero recovery range")

        # 1. Spot makes new day high -> V_SUCCEEDED
        if spot > self._day_high:
            # day_high was already updated in _update_state, check against pre-V high
            # Actually _day_high gets updated every candle, so we check if spot exceeded
            # the high that was set before the drawdown. Since _day_high tracks running max,
            # if current spot > _day_high at confirmation time, it's a new high.
            pass
        # Check against the day_high recorded at confirmation time
        confirmed_day_high = self._day_high  # _day_high keeps updating
        # We need the pre-V high. At confirmation, day_high was stored in the signal.
        # Let's use a simpler check: spot > day_high as tracked (which updates)
        # means spot literally just made a new session high
        # Actually since _update_state runs before this, _day_high already includes
        # current spot. So spot >= _day_high means spot IS the new high.
        if spot >= self._day_high and spot > self._confirmed_spot:
            return self._resolve(spot, now, "V_SUCCEEDED",
                                 f"New day high {spot:.2f}")

        # 2. Spot dropped below V-low -> V_FAILED
        if spot < self._v_low:
            return self._resolve(spot, now, "V_FAILED",
                                 f"Broke below V-low {self._v_low:.2f}")

        # 3. Spot retraced > 61.8% of recovery -> V_FAILED
        retrace_level = self._confirmed_spot - (RETRACE_FAILURE_PCT * recovery_range)
        if spot < retrace_level:
            retrace_pct = (self._confirmed_spot - spot) / recovery_range * 100
            return self._resolve(spot, now, "V_FAILED",
                                 f"Retraced {retrace_pct:.0f}% of recovery")

        # 4. Time-based resolution after 30 min
        if elapsed_min >= RESOLUTION_TIMEOUT_MIN:
            # Determine partial vs expired based on where spot is
            hold_pct = (spot - self._v_low) / recovery_range * 100 if recovery_range > 0 else 0

            if hold_pct >= 50:
                return self._resolve(spot, now, "V_PARTIAL",
                                     f"Holding {hold_pct:.0f}% after {elapsed_min:.0f}min")
            else:
                return self._resolve(spot, now, "V_EXPIRED",
                                     f"Sideways at {hold_pct:.0f}% after {elapsed_min:.0f}min")

        return None

    def _resolve(self, spot: float, now: datetime, resolution_type: str,
                 notes: str) -> Dict:
        """Mark the current V-shape as resolved and reset for potential next one."""
        self._resolved_at = now
        self._resolution_type = resolution_type

        # Update the CONFIRMED signal in DB with resolution info
        self._update_resolution_in_db(spot, now, resolution_type, notes)

        signal = {
            "signal_level": resolution_type,
            "spot_price": spot,
            "day_open": self._day_open,
            "day_high": self._day_high,
            "day_low": self._day_low,
            "drawdown_pct": 0.0,
            "conditions_met": [resolution_type.lower()],
            "conditions_count": 0,
            "futures_basis": self._basis_history[-1] if self._basis_history else 0,
            "pcr": self._pcr_history[-1] if self._pcr_history else 0,
            "combined_score": self._score_history[-1] if self._score_history else 0,
            "signal_confidence": self._confidence_history[-1] if self._confidence_history else 0,
            "pm_reversal_count": 0,
            "futures_oi_change": 0,
            "trap_warning": 0,
            "recovery_pct": 0,
            "detected_at": now.isoformat(),
            "resolved_at": now.isoformat(),
            "resolution_type": resolution_type,
            "notes": notes,
        }

        # Reset signal tracking so new V-shapes can form after cooldown
        self._last_signal_level = None
        self._last_signal_time = None
        self._confirmed_at = None
        self._confirmed_spot = None
        self._v_low = None
        self._drawdown_start_time = None

        # Send Telegram for meaningful resolutions
        if resolution_type in ("V_SUCCEEDED", "V_FAILED"):
            self._send_resolution_alert(signal, notes)

        log.info("V-shape resolved", type=resolution_type, spot=spot, notes=notes)

        return signal

    def _update_resolution_in_db(self, spot: float, now: datetime,
                                 resolution_type: str, notes: str):
        """Update the most recent CONFIRMED signal with resolution data."""
        try:
            today = now.strftime('%Y-%m-%d')
            was_correct = 1 if resolution_type == "V_SUCCEEDED" else (
                0 if resolution_type == "V_FAILED" else None
            )
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE v_shape_signals
                    SET spot_at_resolution = ?, resolved_at = ?,
                        resolution_type = ?, was_correct = ?, notes = ?
                    WHERE DATE(detected_at) = ? AND signal_level = 'CONFIRMED'
                    AND resolved_at IS NULL
                    ORDER BY detected_at DESC LIMIT 1
                """, (spot, now.strftime('%Y-%m-%d %H:%M:%S'),
                      resolution_type, was_correct, notes, today))
                conn.commit()
        except Exception as e:
            log.error("Failed to update V-shape resolution", error=str(e))

    # ===== PERSISTENCE =====

    def _save_signal(self, now: datetime, signal: dict):
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO v_shape_signals (
                        detected_at, signal_level, spot_price, day_open, day_high,
                        day_low, drawdown_pct, conditions_met, conditions_count,
                        futures_basis, pcr, combined_score, signal_confidence,
                        pm_reversal_count, futures_oi_change, trap_warning,
                        recovery_pct, resolution_type, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    signal.get("resolution_type"),
                    signal.get("notes"),
                ))
                conn.commit()
        except Exception as e:
            log.error("Failed to save V-shape signal", error=str(e))

    # ===== ALERTS =====

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
                headline = "V-SHAPE CONFIRMED"
                detail = (
                    f"Spot recovered <code>{recovery:.2f}%</code> from day low "
                    f"<code>{signal.get('day_low', 0):.2f}</code>"
                )
            else:
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
                f"<b>{headline}</b>\n\n"
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

    def _send_resolution_alert(self, signal: dict, notes: str):
        """Send Telegram alert for V_SUCCEEDED or V_FAILED resolution."""
        try:
            from alerts import send_telegram

            res_type = signal["resolution_type"]
            spot = signal["spot_price"]

            if res_type == "V_SUCCEEDED":
                emoji = "\u2705"
                headline = "V-SHAPE SUCCEEDED"
            else:
                emoji = "\u274c"
                headline = "V-SHAPE FAILED"

            message = (
                f"<b>{emoji} {headline}</b>\n\n"
                f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
                f"<b>Detail:</b> {notes}\n\n"
                f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
            )
            send_telegram(message)
        except Exception as e:
            log.error("Failed to send V-shape resolution alert", error=str(e))


# ===== MODULE-LEVEL QUERY FUNCTIONS =====

def get_v_shape_status() -> Optional[Dict]:
    """
    Get the current V-shape display status for today.

    Returns the latest signal with a 'display' field indicating whether
    the frontend should show/hide the banner.
    """
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
            if not row:
                return None

            result = dict(row)

            # Parse conditions_met JSON
            if result.get("conditions_met"):
                try:
                    result["conditions_met"] = json.loads(result["conditions_met"])
                except (json.JSONDecodeError, TypeError):
                    pass

            # Determine display visibility for frontend
            level = result.get("signal_level", "")
            resolved_at = result.get("resolved_at")

            if level in ("FORMING", "LIKELY", "CONFIRMED"):
                result["display"] = True
            elif level in ("V_SUCCEEDED", "V_PARTIAL", "V_FAILED", "V_EXPIRED"):
                if level == "V_EXPIRED":
                    result["display"] = False
                elif resolved_at:
                    # Auto-dismiss after configured duration
                    try:
                        res_time = datetime.strptime(resolved_at, '%Y-%m-%d %H:%M:%S')
                        elapsed = (datetime.now() - res_time).total_seconds() / 60
                        dismiss_min = {
                            "V_SUCCEEDED": DISMISS_SUCCEEDED_MIN,
                            "V_PARTIAL": DISMISS_PARTIAL_MIN,
                            "V_FAILED": DISMISS_FAILED_MIN,
                        }.get(level, 5)
                        result["display"] = elapsed < dismiss_min
                    except (ValueError, TypeError):
                        result["display"] = False
                else:
                    result["display"] = False
            else:
                result["display"] = False

            return result
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
    """Aggregate stats by signal level and resolution type."""
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

            # Resolution stats
            cursor.execute("""
                SELECT resolution_type,
                       COUNT(*) as total,
                       AVG(recovery_pct) as avg_recovery
                FROM v_shape_signals
                WHERE resolution_type IS NOT NULL
                GROUP BY resolution_type
            """)
            by_resolution = {}
            for row in cursor.fetchall():
                r = dict(row)
                by_resolution[r["resolution_type"]] = r

            # Overall confirmed stats
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
                "by_resolution": by_resolution,
                "confirmed": overall,
                "top_conditions": dict(
                    sorted(condition_counts.items(), key=lambda x: x[1], reverse=True)[:7]
                ),
            }
    except Exception as e:
        log.error("Failed to get V-shape stats", error=str(e))
        return {"by_level": {}, "by_resolution": {}, "confirmed": {}, "top_conditions": {}}


# Initialize tables at import time
init_v_shape_tables()
