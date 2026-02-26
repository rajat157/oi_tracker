"""
Prediction Engine V2 — Hypothesis-Based Directional Predictor.

Makes ONE specific directional bet before each candle, validates it
forward-looking (did price actually move in that direction?), and builds
conviction only through consecutive correct predictions.

State machine: OBSERVING → HYPOTHESIS → SIGNAL
One wrong prediction = immediate break back to OBSERVING.
"""

import json
import math
from datetime import datetime, date, timedelta
from typing import Optional

from database import get_connection
from logger import get_logger

log = get_logger("prediction")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CDS thresholds
CDS_PREDICT_THRESHOLD = 15       # |CDS| must exceed this to make a prediction
CDS_FLIP_THRESHOLD = 22.5        # |CDS| must exceed this to flip direction (1.5x)

# CDS EMA smoothing
CDS_EMA_ALPHA = 0.3              # Lower = more stable

# Directional lock: min candles before direction can flip
DIRECTION_LOCK_CANDLES = 2

# Validation: spot must move more than this to count
VALIDATION_THRESHOLD_PTS = 2.0   # points

# Signal promotion: need this many consecutive correct + conviction
SIGNAL_MIN_STREAK = 3
SIGNAL_MIN_CONVICTION = 55

# Conviction cap
CONVICTION_MAX = 95

# CDS component weights
W_OI_FLOW = 0.30
W_STRENGTH = 0.20
W_PREMIUM_FLOW = 0.15
W_FUTURES = 0.15
W_STRUCTURAL = 0.10
W_REGIME = 0.10

# Stale node threshold (seconds)
STALE_NODE_SECONDS = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

def _ensure_v2_tables():
    """Create v2 prediction tables if they don't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prediction_paths_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'OBSERVING',
                hypothesis_direction TEXT,
                hypothesis_started_at TEXT,
                hypothesis_start_prediction INTEGER,
                consecutive_correct INTEGER NOT NULL DEFAULT 0,
                consecutive_wrong INTEGER NOT NULL DEFAULT 0,
                total_predictions INTEGER NOT NULL DEFAULT 0,
                correct_predictions INTEGER NOT NULL DEFAULT 0,
                inconclusive_predictions INTEGER NOT NULL DEFAULT 0,
                accuracy_pct REAL NOT NULL DEFAULT 0.0,
                conviction_pct REAL NOT NULL DEFAULT 0.0,
                current_cds REAL NOT NULL DEFAULT 0.0,
                cds_ema REAL NOT NULL DEFAULT 0.0,
                signal_direction TEXT,
                signal_target REAL,
                signal_emitted_at TEXT,
                last_prediction_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prediction_nodes_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                predicted_direction TEXT,
                predicted_cds REAL,
                predicted_confidence REAL,
                predicted_target REAL,
                spot_at_prediction REAL,
                cds_components TEXT,
                actual_spot REAL,
                actual_direction TEXT,
                validation_result TEXT NOT NULL DEFAULT 'PENDING',
                validated_at TEXT,
                combined_score REAL,
                verdict TEXT,
                pcr REAL,
                iv_skew REAL,
                vix REAL,
                FOREIGN KEY (path_id) REFERENCES prediction_paths_v2(id)
            )
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Public API: get_prediction_state (used by app.py endpoint)
# ---------------------------------------------------------------------------

def get_prediction_state() -> Optional[dict]:
    """Return the current prediction tree state for the API endpoint."""
    _ensure_v2_tables()

    with get_connection() as conn:
        cursor = conn.cursor()

        # Active path (today, not BROKEN/EXPIRED)
        cursor.execute("""
            SELECT * FROM prediction_paths_v2
            WHERE status IN ('OBSERVING', 'HYPOTHESIS', 'SIGNAL')
            ORDER BY id DESC LIMIT 1
        """)
        path_row = cursor.fetchone()
        if not path_row:
            return None

        path = dict(path_row)

        # Latest node on that path
        cursor.execute("""
            SELECT * FROM prediction_nodes_v2
            WHERE path_id = ? ORDER BY id DESC LIMIT 1
        """, (path["id"],))
        node_row = cursor.fetchone()
        node = dict(node_row) if node_row else None

        # Deserialise cds_components JSON
        if node and node.get("cds_components"):
            try:
                node["cds_components"] = json.loads(node["cds_components"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Build signal dict if in SIGNAL state
        signal = None
        if path["status"] == "SIGNAL" and path.get("signal_direction"):
            sig_dir = path["signal_direction"]
            sig_target = path.get("signal_target", 0)
            conviction = path["conviction_pct"]

            for_prob = conviction / 100.0
            against_prob = 1.0 - for_prob
            against_direction = "BEARISH" if sig_dir == "BULLISH" else "BULLISH"

            spot = node.get("spot_at_prediction", 0) if node else 0
            against_target = round(2 * spot - sig_target, 2) if spot else sig_target

            signal = {
                "for": {
                    "direction": sig_dir,
                    "probability": round(for_prob * 100, 1),
                    "target": sig_target,
                },
                "against": {
                    "direction": against_direction,
                    "probability": round(against_prob * 100, 1),
                    "target": against_target,
                },
                "recommended": "FOR" if for_prob >= 0.5 else "AGAINST",
                "conviction": conviction,
                "path_direction": sig_dir,
            }

        # Last validated node (for "Last Prediction" display)
        cursor.execute("""
            SELECT predicted_direction, validation_result, predicted_cds,
                   spot_at_prediction, actual_spot
            FROM prediction_nodes_v2
            WHERE path_id = ? AND validation_result != 'PENDING'
            ORDER BY id DESC LIMIT 1
        """, (path["id"],))
        last_validated_row = cursor.fetchone()
        last_validated = dict(last_validated_row) if last_validated_row else None

        # Recent history (last 10 validated nodes)
        cursor.execute("""
            SELECT id, predicted_direction, predicted_cds, validation_result,
                   spot_at_prediction, actual_spot, created_at, validated_at
            FROM prediction_nodes_v2
            WHERE path_id = ? AND validation_result != 'PENDING'
            ORDER BY id DESC LIMIT 10
        """, (path["id"],))
        history = [dict(r) for r in cursor.fetchall()]

        # Map to frontend-compatible format
        direction = path.get("hypothesis_direction") or "OBSERVING"
        status = path["status"]

        return {
            "path": {
                "id": path["id"],
                "depth": path["consecutive_correct"],
                "max_depth": path["consecutive_correct"],
                "direction": direction,
                "conviction": path["conviction_pct"],
                "consecutive_matches": path["consecutive_correct"],
                "consecutive_misses": path["consecutive_wrong"],
                "contrarian_weight": path["cds_ema"],
                "accuracy": path["accuracy_pct"],
                "total": path["total_predictions"],
                "correct": path["correct_predictions"],
                "status": status,
            },
            "latest_node": node,
            "last_matched": last_validated,
            "signal": signal,
            "history": history,
        }


# ---------------------------------------------------------------------------
# Public API: get_active_prediction_path
# ---------------------------------------------------------------------------

def get_active_prediction_path() -> Optional[dict]:
    """Return the currently active prediction path row, or None."""
    _ensure_v2_tables()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM prediction_paths_v2
            WHERE status IN ('OBSERVING', 'HYPOTHESIS', 'SIGNAL')
            ORDER BY id DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# PredictionEngine class
# ---------------------------------------------------------------------------

class PredictionEngine:
    """Hypothesis-based directional prediction engine."""

    def __init__(self):
        _ensure_v2_tables()
        self._active_path_id: Optional[int] = None
        self._pending_node_id: Optional[int] = None
        self._last_reset_date: Optional[date] = None
        self._load_active_state()

    def _load_active_state(self):
        """Load active path and pending node from DB on startup."""
        with get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id FROM prediction_paths_v2
                WHERE status IN ('OBSERVING', 'HYPOTHESIS', 'SIGNAL')
                ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                self._active_path_id = row["id"]

                # Find pending node
                cursor.execute("""
                    SELECT id FROM prediction_nodes_v2
                    WHERE path_id = ? AND validation_result = 'PENDING'
                    ORDER BY id DESC LIMIT 1
                """, (self._active_path_id,))
                node_row = cursor.fetchone()
                if node_row:
                    self._pending_node_id = node_row["id"]

    # ------------------------------------------------------------------
    # Day reset
    # ------------------------------------------------------------------

    def _check_day_reset(self):
        """Reset path at the start of each new trading day."""
        today = datetime.now().date()
        if self._last_reset_date == today:
            return

        self._last_reset_date = today

        if self._active_path_id is not None:
            with get_connection() as conn:
                # Check if active path is from a previous day
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT started_at FROM prediction_paths_v2 WHERE id = ?",
                    (self._active_path_id,)
                )
                row = cursor.fetchone()
                if row:
                    started = datetime.fromisoformat(row["started_at"]).date()
                    if started < today:
                        conn.execute("""
                            UPDATE prediction_paths_v2
                            SET status = 'EXPIRED', ended_at = ?
                            WHERE id = ?
                        """, (datetime.now().isoformat(), self._active_path_id))
                        conn.commit()
                        self._active_path_id = None
                        self._pending_node_id = None
                        log.info("Path expired (new day)")

    # ------------------------------------------------------------------
    # CDS computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_cds(analysis: dict) -> tuple[float, dict]:
        """
        Compute Composite Directional Score from analysis data.

        Returns (cds_value, components_dict).
        """
        # 1. OI Flow (combined_score is already -100 to +100)
        oi_flow = analysis.get("combined_score", 0)

        # 2. Strength (net_strength is -100 to +100)
        strength = analysis.get("strength_analysis", {}).get("net_strength", 0)

        # 3. Premium Flow
        pm = analysis.get("premium_momentum", {})
        call_pm = pm.get("call_premium_change_pct", 0)
        put_pm = pm.get("put_premium_change_pct", 0)
        premium_flow = max(-100, min(100, (call_pm - put_pm) * 5))

        # 4. Futures component
        futures_oi_change = analysis.get("futures_oi_change", 0)
        futures_basis = analysis.get("futures_basis", 0) if "futures_basis" in analysis else 0
        # Normalize: typical futures OI change is ±500K, basis ±50pts
        norm_foi = max(-100, min(100, futures_oi_change / 5000 * 100)) if futures_oi_change else 0
        norm_basis = max(-100, min(100, futures_basis / 50 * 100)) if futures_basis else 0
        futures = 0.6 * norm_foi + 0.4 * norm_basis

        # 5. Structural (max pain magnet + OI wall proximity)
        spot = analysis.get("spot_price", 0)
        max_pain = analysis.get("max_pain", 0)
        clusters = analysis.get("oi_clusters", {})
        structural = 0.0
        if spot and max_pain:
            # Max pain acts as magnet: positive if spot below max_pain (bullish pull)
            mp_pull = (max_pain - spot) / spot * 1000 if spot else 0  # scale
            mp_pull = max(-50, min(50, mp_pull))

            # OI wall proximity: nearest resistance above vs support below
            nearest_res = None
            for r in (clusters.get("resistance") or []):
                s = r.get("strike", 0)
                if s > spot:
                    nearest_res = s
                    break
            nearest_sup = None
            for s_item in (clusters.get("support") or []):
                s = s_item.get("strike", 0)
                if s < spot:
                    nearest_sup = s
                    break

            wall_bias = 0.0
            if nearest_res and nearest_sup:
                dist_res = nearest_res - spot
                dist_sup = spot - nearest_sup
                # Closer to support = more bullish (support holds), closer to resistance = more bearish
                if dist_res + dist_sup > 0:
                    wall_bias = ((dist_res - dist_sup) / (dist_res + dist_sup)) * 50

            structural = max(-100, min(100, mp_pull + wall_bias))

        # 6. Regime score
        regime = analysis.get("market_regime", {}).get("regime", "range_bound")
        phase = analysis.get("oi_acceleration", {}).get("phase", "stable")
        vix = analysis.get("vix", 0)

        phase_scores = {
            "accumulation": 40,    # bullish accumulation
            "distribution": -40,   # bearish distribution
            "unwinding": 0,
            "stable": 0,
        }
        phase_score = phase_scores.get(phase, 0)

        # Regime direction modifier
        if regime == "trending_up":
            phase_score = abs(phase_score) if phase_score >= 0 else phase_score * 0.5
        elif regime == "trending_down":
            phase_score = -abs(phase_score) if phase_score <= 0 else phase_score * 0.5

        # VIX dampener: high VIX = less confident in direction
        vix_dampener = 1.0
        if vix > 20:
            vix_dampener = max(0.5, 1.0 - (vix - 20) / 40)

        regime_score = max(-100, min(100, phase_score * vix_dampener))

        # Composite
        cds = (
            W_OI_FLOW * oi_flow
            + W_STRENGTH * strength
            + W_PREMIUM_FLOW * premium_flow
            + W_FUTURES * futures
            + W_STRUCTURAL * structural
            + W_REGIME * regime_score
        )
        cds = max(-100, min(100, cds))

        components = {
            "oi_flow": round(oi_flow, 1),
            "strength": round(strength, 1),
            "premium_flow": round(premium_flow, 1),
            "futures": round(futures, 1),
            "structural": round(structural, 1),
            "regime": round(regime_score, 1),
        }

        return round(cds, 2), components

    # ------------------------------------------------------------------
    # Smart targets (OI-derived)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_target(direction: str, analysis: dict) -> float:
        """Compute OI-derived target for a directional hypothesis."""
        spot = analysis.get("spot_price", 0)
        if not spot:
            return 0

        max_pain = analysis.get("max_pain", 0)
        clusters = analysis.get("oi_clusters", {})
        vix = analysis.get("vix", 13)

        # VIX-implied 3-minute move (annualized vol → 3 min)
        # Trading minutes per year ≈ 375 * 252 = 94500
        vix_move = spot * (vix / 100) * math.sqrt(3 / 94500) if vix > 0 else spot * 0.001

        if direction == "BULLISH":
            # Find nearest resistance above spot
            candidates = []
            for r in (clusters.get("resistance") or []):
                s = r.get("strike", 0)
                if s > spot:
                    candidates.append(s)
            if max_pain and max_pain > spot:
                candidates.append(max_pain)

            if candidates:
                target = min(candidates)
                # Cap to 2-sigma of VIX-implied move
                max_target = spot + 2 * vix_move
                target = min(target, max_target)
            else:
                target = spot + vix_move * 0.7
        else:
            # BEARISH: find nearest support below spot
            candidates = []
            for s_item in (clusters.get("support") or []):
                s = s_item.get("strike", 0)
                if s < spot:
                    candidates.append(s)
            if max_pain and max_pain < spot:
                candidates.append(max_pain)

            if candidates:
                target = max(candidates)
                min_target = spot - 2 * vix_move
                target = max(target, min_target)
            else:
                target = spot - vix_move * 0.7

        return round(target, 2)

    # ------------------------------------------------------------------
    # Conviction formula
    # ------------------------------------------------------------------

    def _compute_conviction(self, path: dict, cds: float, cds_ema: float) -> float:
        """
        Multi-factor conviction score.

        streak_factor:      min(1, streak/5) with diminishing returns
        cds_factor:         min(1, |CDS|/60)
        consistency_factor: CDS EMA same sign as current CDS
        accuracy_factor:    accuracy_pct (needs ≥3 predictions)
        """
        streak = path.get("consecutive_correct", 0)
        total = path.get("total_predictions", 0)
        correct = path.get("correct_predictions", 0)
        accuracy = (correct / total * 100) if total >= 3 else 50.0

        # Streak factor with diminishing returns
        streak_raw = min(1.0, streak / 5)
        streak_factor = streak_raw * (1.0 - 0.1 * max(0, streak - 5))  # diminish after 5
        streak_factor = max(0, streak_factor)

        # CDS magnitude factor
        cds_factor = min(1.0, abs(cds) / 60)

        # Consistency: EMA agrees with current direction
        if cds != 0 and cds_ema != 0:
            same_sign = (cds > 0) == (cds_ema > 0)
            consistency_factor = 1.0 if same_sign else 0.0
        else:
            consistency_factor = 0.5

        # Accuracy factor (only meaningful with ≥3 predictions)
        accuracy_factor = accuracy / 100.0 if total >= 3 else 0.5

        conviction = (
            0.35 * streak_factor
            + 0.25 * cds_factor
            + 0.25 * consistency_factor
            + 0.15 * accuracy_factor
        ) * 100

        return min(CONVICTION_MAX, round(conviction, 1))

    # ------------------------------------------------------------------
    # Direction flip logic (stability)
    # ------------------------------------------------------------------

    def _should_flip_direction(self, path: dict, cds: float, cds_ema: float) -> bool:
        """
        Check if direction should flip. Requires ALL:
        1. CDS EMA has changed sign vs current hypothesis
        2. |CDS| > FLIP_THRESHOLD in new direction
        3. At least DIRECTION_LOCK_CANDLES since hypothesis started
        """
        hyp_dir = path.get("hypothesis_direction")
        if not hyp_dir:
            return True  # no hypothesis yet, free to set

        # Check EMA sign vs hypothesis
        ema_bullish = cds_ema > 0
        hyp_bullish = hyp_dir == "BULLISH"
        if ema_bullish == hyp_bullish:
            return False  # EMA still agrees with hypothesis

        # Check CDS magnitude
        if abs(cds) < CDS_FLIP_THRESHOLD:
            return False  # not strong enough

        # Check CDS direction is opposite to hypothesis
        cds_bullish = cds > 0
        if cds_bullish == hyp_bullish:
            return False  # CDS still same direction

        # Check directional lock
        hyp_started = path.get("hypothesis_started_at")
        hyp_start_pred = path.get("hypothesis_start_prediction", 0)
        total_preds = path.get("total_predictions", 0)
        candles_since = total_preds - hyp_start_pred if hyp_start_pred else total_preds

        if candles_since < DIRECTION_LOCK_CANDLES:
            return False  # too soon

        return True

    # ------------------------------------------------------------------
    # Path management
    # ------------------------------------------------------------------

    def _create_path(self, now: datetime) -> int:
        """Create a new OBSERVING path."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO prediction_paths_v2
                (started_at, status, consecutive_correct, consecutive_wrong,
                 total_predictions, correct_predictions, inconclusive_predictions,
                 accuracy_pct, conviction_pct, current_cds, cds_ema)
                VALUES (?, 'OBSERVING', 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0)
            """, (now.isoformat(),))
            conn.commit()
            path_id = cursor.lastrowid
            self._active_path_id = path_id
            self._pending_node_id = None
            log.info("New path created", path_id=path_id)
            return path_id

    def _get_path(self) -> Optional[dict]:
        """Get current active path."""
        if not self._active_path_id:
            return None
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM prediction_paths_v2 WHERE id = ?",
                (self._active_path_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _break_path(self, reason: str = "WRONG"):
        """Break current path → OBSERVING reset."""
        if not self._active_path_id:
            return
        with get_connection() as conn:
            now = datetime.now().isoformat()
            conn.execute("""
                UPDATE prediction_paths_v2
                SET status = 'BROKEN', ended_at = ?
                WHERE id = ?
            """, (now, self._active_path_id))
            conn.commit()
        log.info("Path broken", path_id=self._active_path_id, reason=reason)
        self._active_path_id = None
        self._pending_node_id = None

    # ------------------------------------------------------------------
    # Node management
    # ------------------------------------------------------------------

    def _save_node(self, path_id: int, created_at: datetime,
                   predicted_direction: Optional[str], predicted_cds: float,
                   predicted_confidence: float, predicted_target: float,
                   spot: float, cds_components: dict,
                   analysis: dict) -> int:
        """Save a new prediction node."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO prediction_nodes_v2
                (path_id, created_at, predicted_direction, predicted_cds,
                 predicted_confidence, predicted_target, spot_at_prediction,
                 cds_components, validation_result,
                 combined_score, verdict, pcr, iv_skew, vix)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)
            """, (
                path_id,
                created_at.isoformat(),
                predicted_direction,
                predicted_cds,
                predicted_confidence,
                predicted_target,
                spot,
                json.dumps(cds_components),
                analysis.get("combined_score", 0),
                analysis.get("verdict", ""),
                analysis.get("pcr", 0),
                analysis.get("iv_skew", 0),
                analysis.get("vix", 0),
            ))
            conn.commit()
            return cursor.lastrowid

    def _validate_pending_node(self, analysis: dict) -> Optional[str]:
        """
        Validate the pending prediction node against actual data.

        Returns: 'CORRECT', 'WRONG', 'INCONCLUSIVE', or None if no pending.
        """
        if not self._pending_node_id:
            return None

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM prediction_nodes_v2 WHERE id = ?",
                (self._pending_node_id,)
            )
            node = cursor.fetchone()
            if not node:
                self._pending_node_id = None
                return None

            node = dict(node)
            predicted_dir = node.get("predicted_direction")
            spot_at_pred = node.get("spot_at_prediction", 0)
            actual_spot = analysis.get("spot_price", 0)

            # No prediction was made (OBSERVING state)
            if not predicted_dir or predicted_dir == "OBSERVING":
                conn.execute("""
                    UPDATE prediction_nodes_v2
                    SET actual_spot = ?, actual_direction = 'NONE',
                        validation_result = 'INCONCLUSIVE', validated_at = ?
                    WHERE id = ?
                """, (actual_spot, datetime.now().isoformat(), self._pending_node_id))
                conn.commit()
                self._pending_node_id = None
                return "INCONCLUSIVE"

            # Check stale node
            created = datetime.fromisoformat(node["created_at"])
            elapsed = (datetime.now() - created).total_seconds()
            if elapsed > STALE_NODE_SECONDS:
                conn.execute("""
                    UPDATE prediction_nodes_v2
                    SET actual_spot = ?, actual_direction = 'STALE',
                        validation_result = 'INCONCLUSIVE', validated_at = ?
                    WHERE id = ?
                """, (actual_spot, datetime.now().isoformat(), self._pending_node_id))
                conn.commit()
                self._pending_node_id = None
                return "INCONCLUSIVE"

            # Determine actual direction
            move = actual_spot - spot_at_pred
            if abs(move) <= VALIDATION_THRESHOLD_PTS:
                actual_dir = "FLAT"
                result = "INCONCLUSIVE"
            elif move > 0:
                actual_dir = "BULLISH"
                result = "CORRECT" if predicted_dir == "BULLISH" else "WRONG"
            else:
                actual_dir = "BEARISH"
                result = "CORRECT" if predicted_dir == "BEARISH" else "WRONG"

            conn.execute("""
                UPDATE prediction_nodes_v2
                SET actual_spot = ?, actual_direction = ?,
                    validation_result = ?, validated_at = ?
                WHERE id = ?
            """, (actual_spot, actual_dir, result,
                  datetime.now().isoformat(), self._pending_node_id))
            conn.commit()

            self._pending_node_id = None
            return result

    # ------------------------------------------------------------------
    # State machine transitions
    # ------------------------------------------------------------------

    def _update_path_after_validation(self, path: dict, result: str,
                                       cds: float, cds_ema: float,
                                       conviction: float):
        """Update path state based on validation result."""
        status = path["status"]
        streak = path["consecutive_correct"]
        total = path["total_predictions"]
        correct = path["correct_predictions"]
        inconclusive = path["inconclusive_predictions"]

        if result == "CORRECT":
            streak += 1
            total += 1
            correct += 1
            accuracy = (correct / total * 100) if total > 0 else 0

            # Determine new status
            if status == "OBSERVING":
                new_status = "HYPOTHESIS"
            elif status == "HYPOTHESIS":
                if streak >= SIGNAL_MIN_STREAK and conviction >= SIGNAL_MIN_CONVICTION:
                    new_status = "SIGNAL"
                else:
                    new_status = "HYPOTHESIS"
            else:  # SIGNAL
                new_status = "SIGNAL"

        elif result == "WRONG":
            total += 1
            accuracy = (correct / total * 100) if total > 0 else 0

            if status in ("HYPOTHESIS", "SIGNAL"):
                # Break: wrong prediction kills hypothesis/signal
                with get_connection() as conn:
                    conn.execute("""
                        UPDATE prediction_paths_v2
                        SET status = 'BROKEN', ended_at = ?,
                            consecutive_correct = 0, consecutive_wrong = ?,
                            total_predictions = ?, correct_predictions = ?,
                            accuracy_pct = ?, conviction_pct = 0,
                            current_cds = ?, cds_ema = ?
                        WHERE id = ?
                    """, (
                        datetime.now().isoformat(),
                        path["consecutive_wrong"] + 1,
                        total, correct, accuracy, cds, cds_ema,
                        self._active_path_id
                    ))
                    conn.commit()
                log.info("Hypothesis broken (WRONG)", path_id=self._active_path_id, streak_was=path["consecutive_correct"])
                self._active_path_id = None
                self._pending_node_id = None
                return
            else:
                # OBSERVING: just record the miss
                new_status = "OBSERVING"
                streak = 0

        else:  # INCONCLUSIVE
            inconclusive += 1
            accuracy = (correct / total * 100) if total > 0 else 0
            new_status = status  # unchanged

        # Signal management
        signal_dir = path.get("signal_direction")
        signal_target = path.get("signal_target")
        signal_emitted = path.get("signal_emitted_at")

        if new_status == "SIGNAL" and not signal_emitted:
            signal_dir = path.get("hypothesis_direction")
            signal_emitted = datetime.now().isoformat()

        with get_connection() as conn:
            conn.execute("""
                UPDATE prediction_paths_v2
                SET status = ?, consecutive_correct = ?,
                    total_predictions = ?, correct_predictions = ?,
                    inconclusive_predictions = ?, accuracy_pct = ?,
                    conviction_pct = ?, current_cds = ?, cds_ema = ?,
                    signal_direction = ?, signal_target = ?,
                    signal_emitted_at = ?, last_prediction_at = ?
                WHERE id = ?
            """, (
                new_status, streak, total, correct, inconclusive,
                round(accuracy, 1), conviction, cds, cds_ema,
                signal_dir, signal_target, signal_emitted,
                datetime.now().isoformat(),
                self._active_path_id
            ))
            conn.commit()

        if new_status != status:
            log.info("Path state transition", old=status, new=new_status,
                     streak=streak, conviction=f"{conviction:.0f}%")

    # ------------------------------------------------------------------
    # Main processing
    # ------------------------------------------------------------------

    def process_candle(self, analysis: dict) -> Optional[dict]:
        """
        Process a new candle's analysis data.

        1. Day-reset check
        2. Validate pending prediction against actual data
        3. Compute CDS
        4. Update CDS EMA + check direction flip
        5. Make new prediction (or abstain)
        6. Update path state
        7. Return summary for dashboard

        Returns a summary dict attached to analysis["prediction_tree"].
        """
        self._check_day_reset()
        now = datetime.now()
        spot = analysis.get("spot_price", 0)

        # --- Step 1: Validate pending prediction ---
        validation_result = self._validate_pending_node(analysis)

        # --- Step 2: Compute CDS ---
        cds, components = self._compute_cds(analysis)

        # --- Step 3: Ensure active path ---
        if self._active_path_id is None:
            self._create_path(now)

        path = self._get_path()
        if not path:
            self._create_path(now)
            path = self._get_path()

        # --- Step 4: Update CDS EMA ---
        old_ema = path.get("cds_ema", 0)
        cds_ema = CDS_EMA_ALPHA * cds + (1 - CDS_EMA_ALPHA) * old_ema

        # --- Step 5: Check direction flip ---
        current_hyp_dir = path.get("hypothesis_direction")

        if current_hyp_dir and self._should_flip_direction(path, cds, cds_ema):
            # Direction flip → break path and start fresh
            log.info("Direction flip detected", old=current_hyp_dir,
                     cds=f"{cds:.1f}", cds_ema=f"{cds_ema:.1f}")
            self._break_path("DIRECTION_FLIP")
            self._create_path(now)
            path = self._get_path()
            cds_ema = cds  # reset EMA
            validation_result = None  # don't apply old validation to new path

        # --- Step 6: Compute conviction ---
        conviction = self._compute_conviction(path, cds, cds_ema)

        # --- Step 7: Apply validation result to path ---
        if validation_result:
            self._update_path_after_validation(path, validation_result, cds, cds_ema, conviction)
            # Re-fetch path (may have been broken)
            if self._active_path_id is None:
                self._create_path(now)
                path = self._get_path()
                cds_ema = cds
                conviction = self._compute_conviction(path, cds, cds_ema)
            else:
                path = self._get_path()

        # --- Step 8: Make new prediction ---
        if abs(cds) >= CDS_PREDICT_THRESHOLD:
            predicted_dir = "BULLISH" if cds > 0 else "BEARISH"
            target = self._compute_target(predicted_dir, analysis)

            # Set hypothesis direction if not set
            if not path.get("hypothesis_direction"):
                with get_connection() as conn:
                    conn.execute("""
                        UPDATE prediction_paths_v2
                        SET hypothesis_direction = ?, hypothesis_started_at = ?,
                            hypothesis_start_prediction = ?, status = ?
                        WHERE id = ?
                    """, (
                        predicted_dir,
                        now.isoformat(),
                        path.get("total_predictions", 0),
                        "HYPOTHESIS" if path["status"] == "OBSERVING" else path["status"],
                        self._active_path_id
                    ))
                    conn.commit()
                path = self._get_path()
        else:
            predicted_dir = None
            target = 0

        # Update signal target if in SIGNAL state
        if path and path["status"] == "SIGNAL" and predicted_dir and target:
            with get_connection() as conn:
                conn.execute("""
                    UPDATE prediction_paths_v2
                    SET signal_target = ?, current_cds = ?, cds_ema = ?
                    WHERE id = ?
                """, (target, cds, cds_ema, self._active_path_id))
                conn.commit()
        elif path:
            with get_connection() as conn:
                conn.execute("""
                    UPDATE prediction_paths_v2
                    SET current_cds = ?, cds_ema = ?
                    WHERE id = ?
                """, (cds, cds_ema, self._active_path_id))
                conn.commit()

        # --- Step 9: Save new prediction node ---
        new_node_id = self._save_node(
            path_id=self._active_path_id,
            created_at=now,
            predicted_direction=predicted_dir or "OBSERVING",
            predicted_cds=cds,
            predicted_confidence=conviction,
            predicted_target=target,
            spot=spot,
            cds_components=components,
            analysis=analysis,
        )
        self._pending_node_id = new_node_id

        # --- Build return summary ---
        path = self._get_path()
        status = path["status"] if path else "OBSERVING"
        direction = path.get("hypothesis_direction") or "OBSERVING"
        depth = path.get("consecutive_correct", 0) if path else 0

        signal = None
        if status == "SIGNAL" and path.get("signal_direction"):
            signal = {
                "for": {
                    "direction": path["signal_direction"],
                    "probability": round(conviction, 1),
                    "target": path.get("signal_target", 0),
                },
                "against": {
                    "direction": "BEARISH" if path["signal_direction"] == "BULLISH" else "BULLISH",
                    "probability": round(100 - conviction, 1),
                    "target": round(2 * spot - (path.get("signal_target", 0) or spot), 2),
                },
                "recommended": "FOR",
                "conviction": conviction,
                "path_direction": direction,
            }

        log.info("Prediction engine updated",
                 status=status,
                 direction=direction or "OBS",
                 depth=depth,
                 conviction=f"{conviction:.0f}%",
                 cds=f"{cds:.1f}",
                 validation=validation_result or "FIRST",
                 predicted=predicted_dir or "ABSTAIN")

        return {
            "depth": depth,
            "conviction": conviction,
            "direction": direction,
            "matched_scenario": validation_result or "FIRST",
            "match_score": round(abs(cds) / 100, 3),
            "signal": signal,
            "contrarian_weight": round(cds_ema, 3),
        }

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_prediction_stats(self) -> dict:
        """Aggregate accuracy stats across all v2 paths."""
        with get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as total_paths,
                    AVG(accuracy_pct) as avg_accuracy,
                    MAX(consecutive_correct) as best_streak,
                    AVG(consecutive_correct) as avg_streak,
                    SUM(total_predictions) as total_predictions,
                    SUM(correct_predictions) as total_correct
                FROM prediction_paths_v2
                WHERE total_predictions > 0
            """)
            row = cursor.fetchone()
            stats = dict(row) if row else {}

            # Today's stats
            today_str = datetime.now().strftime("%Y-%m-%d")
            cursor.execute("""
                SELECT
                    COUNT(*) as paths_today,
                    AVG(accuracy_pct) as accuracy_today,
                    MAX(consecutive_correct) as best_streak_today
                FROM prediction_paths_v2
                WHERE started_at >= ?
            """, (today_str,))
            today_row = cursor.fetchone()
            stats["today"] = dict(today_row) if today_row else {}

            # Validation breakdown
            cursor.execute("""
                SELECT
                    validation_result,
                    COUNT(*) as count
                FROM prediction_nodes_v2
                WHERE validation_result != 'PENDING'
                GROUP BY validation_result
            """)
            breakdown = {}
            for r in cursor.fetchall():
                breakdown[r["validation_result"]] = r["count"]
            stats["validation_breakdown"] = breakdown

            return stats
