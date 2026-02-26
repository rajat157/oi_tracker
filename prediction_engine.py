"""
Prediction Tree Engine — Proactive multi-scenario prediction system.

Generates 3 scenario predictions before each candle, validates them against
actual data, and builds conviction through consecutive correct predictions.
Only when a prediction path is deep enough do we emit a directional signal —
and even then, we provide both FOR (continuation) and AGAINST (reversal)
outcomes with probabilities.

Replaces the reactive "signal → trade" flow with a proactive
"predict → validate → predict deeper → trade with conviction" flow.
"""

import json
from datetime import datetime, date, timedelta
from typing import Optional

from database import get_connection
from logger import get_logger

log = get_logger("prediction")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Conviction lookup by consecutive-match depth
CONVICTION_MAP = {
    0: 0,
    1: 30,
    2: 55,
    3: 75,
    4: 85,
}
CONVICTION_MAX = 90  # depth >= 5

# Minimum weighted score (0-1) for a scenario to count as a match
MATCH_THRESHOLD = 0.40

# Spot step used to build expected ranges (fraction of spot)
SPOT_STEP_PCT = 0.05  # 0.05%
SCORE_STEP = 8        # combined_score band half-width
PCR_STEP = 0.10       # PCR band half-width

# Signal emission requires at least this conviction
SIGNAL_CONVICTION_THRESHOLD = 60

# Contrarian weight bounds
CONTRARIAN_WEIGHT_MIN = 0.10
CONTRARIAN_WEIGHT_MAX = 0.80
CONTRARIAN_WEIGHT_DEFAULT = 0.30

# Stale node threshold (seconds) — if pending node older than this, expire it
STALE_NODE_SECONDS = 360  # 6 minutes

# Strong signal threshold for contrarian boost
STRONG_SIGNAL_THRESHOLD = 50

# Metric weights for scoring (must sum to 1.0)
METRIC_WEIGHTS = {
    "spot":       0.25,
    "score":      0.20,
    "oi_dir":     0.15,
    "prem_dir":   0.15,
    "pcr":        0.08,
    "iv_skew":    0.07,
    "phase":      0.05,
    "contrarian": 0.05,
}


# ---------------------------------------------------------------------------
# DB Initialisation
# ---------------------------------------------------------------------------

def init_prediction_tables():
    """Create prediction_nodes and prediction_paths tables if they don't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME NOT NULL,
                parent_node_id INTEGER,
                path_id INTEGER,
                depth INTEGER DEFAULT 0,
                scenario_a TEXT,
                scenario_b TEXT,
                scenario_c TEXT,
                matched_scenario TEXT,
                match_score_a REAL DEFAULT 0,
                match_score_b REAL DEFAULT 0,
                match_score_c REAL DEFAULT 0,
                spot_price REAL,
                combined_score REAL,
                verdict TEXT,
                pcr REAL,
                iv_skew REAL,
                vix REAL,
                status TEXT DEFAULT 'PENDING',
                FOREIGN KEY (path_id) REFERENCES prediction_paths(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at DATETIME NOT NULL,
                ended_at DATETIME,
                current_depth INTEGER DEFAULT 0,
                max_depth_reached INTEGER DEFAULT 0,
                current_direction TEXT DEFAULT 'UNKNOWN',
                conviction_pct REAL DEFAULT 0,
                consecutive_matches INTEGER DEFAULT 0,
                consecutive_misses INTEGER DEFAULT 0,
                contrarian_weight REAL DEFAULT 0.30,
                signal_emitted INTEGER DEFAULT 0,
                signal_direction TEXT,
                signal_target REAL,
                signal_result TEXT,
                total_predictions INTEGER DEFAULT 0,
                correct_predictions INTEGER DEFAULT 0,
                accuracy_pct REAL DEFAULT 0,
                status TEXT DEFAULT 'ACTIVE'
            )
        """)

        conn.commit()


# ---------------------------------------------------------------------------
# API helpers (module-level, importable)
# ---------------------------------------------------------------------------

def get_prediction_state() -> Optional[dict]:
    """Return the current prediction tree state for the API endpoint."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Active path
        cursor.execute("""
            SELECT * FROM prediction_paths
            WHERE status = 'ACTIVE'
            ORDER BY id DESC LIMIT 1
        """)
        path_row = cursor.fetchone()
        if not path_row:
            return None

        path = dict(path_row)

        # Latest node on that path
        cursor.execute("""
            SELECT * FROM prediction_nodes
            WHERE path_id = ? ORDER BY id DESC LIMIT 1
        """, (path["id"],))
        node_row = cursor.fetchone()
        node = dict(node_row) if node_row else None

        # Deserialise scenario JSON blobs in node
        if node:
            for key in ("scenario_a", "scenario_b", "scenario_c"):
                if node.get(key):
                    try:
                        node[key] = json.loads(node[key])
                    except (json.JSONDecodeError, TypeError):
                        pass

        # Build signal info
        signal = None
        conviction = path.get("conviction_pct", 0)
        if conviction >= SIGNAL_CONVICTION_THRESHOLD and path.get("signal_emitted"):
            signal = {
                "direction": path.get("signal_direction"),
                "target": path.get("signal_target"),
                "result": path.get("signal_result"),
                "conviction": conviction,
            }

        # Recent history (last 10 nodes on this path)
        cursor.execute("""
            SELECT id, depth, matched_scenario, match_score_a, match_score_b,
                   match_score_c, status, created_at
            FROM prediction_nodes
            WHERE path_id = ?
            ORDER BY id DESC LIMIT 10
        """, (path["id"],))
        history = [dict(r) for r in cursor.fetchall()]

        return {
            "path": {
                "id": path["id"],
                "depth": path["current_depth"],
                "max_depth": path["max_depth_reached"],
                "direction": path["current_direction"],
                "conviction": conviction,
                "consecutive_matches": path["consecutive_matches"],
                "consecutive_misses": path["consecutive_misses"],
                "contrarian_weight": path["contrarian_weight"],
                "accuracy": path["accuracy_pct"],
                "total": path["total_predictions"],
                "correct": path["correct_predictions"],
                "status": path["status"],
            },
            "latest_node": node,
            "signal": signal,
            "history": history,
        }


def get_active_prediction_path() -> Optional[dict]:
    """Return the currently active prediction path row, or None."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM prediction_paths
            WHERE status = 'ACTIVE'
            ORDER BY id DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# PredictionEngine
# ---------------------------------------------------------------------------

class PredictionEngine:
    """
    Core prediction tree engine.

    Called once per 3-minute candle via ``process_candle(analysis)``.
    """

    def __init__(self):
        init_prediction_tables()
        self._active_path_id: Optional[int] = None
        self._pending_node_id: Optional[int] = None
        self._contrarian_weight: float = CONTRARIAN_WEIGHT_DEFAULT
        self._today: Optional[date] = None
        self._load_state()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _load_state(self):
        """Recover state from DB after restart."""
        with get_connection() as conn:
            cursor = conn.cursor()

            # Find active path
            cursor.execute("""
                SELECT * FROM prediction_paths
                WHERE status = 'ACTIVE'
                ORDER BY id DESC LIMIT 1
            """)
            path_row = cursor.fetchone()
            if path_row:
                path = dict(path_row)
                self._active_path_id = path["id"]
                self._contrarian_weight = path.get("contrarian_weight", CONTRARIAN_WEIGHT_DEFAULT)

                # Find last pending node on this path
                cursor.execute("""
                    SELECT * FROM prediction_nodes
                    WHERE path_id = ? AND status = 'PENDING'
                    ORDER BY id DESC LIMIT 1
                """, (self._active_path_id,))
                node_row = cursor.fetchone()
                if node_row:
                    node = dict(node_row)
                    # Check staleness
                    created = datetime.fromisoformat(node["created_at"])
                    age = (datetime.now() - created).total_seconds()
                    if age > STALE_NODE_SECONDS:
                        # Expire stale node
                        cursor.execute(
                            "UPDATE prediction_nodes SET status='EXPIRED' WHERE id=?",
                            (node["id"],)
                        )
                        conn.commit()
                        self._pending_node_id = None
                        log.info("Expired stale pending node", node_id=node["id"], age_s=f"{age:.0f}")
                    else:
                        self._pending_node_id = node["id"]

                log.info("Loaded prediction state",
                         path_id=self._active_path_id,
                         pending_node=self._pending_node_id,
                         contrarian_w=f"{self._contrarian_weight:.2f}")
            else:
                log.info("No active prediction path found — will create on first candle")

    def _check_day_reset(self):
        """Expire old paths when a new trading day starts. Carry contrarian weight forward."""
        today = datetime.now().date()
        if self._today == today:
            return
        self._today = today

        with get_connection() as conn:
            cursor = conn.cursor()

            # Find any active paths from previous days
            cursor.execute("""
                SELECT id, contrarian_weight FROM prediction_paths
                WHERE status = 'ACTIVE'
            """)
            rows = cursor.fetchall()
            for row in rows:
                old_path = dict(row)
                # Carry contrarian weight forward
                self._contrarian_weight = old_path.get("contrarian_weight", CONTRARIAN_WEIGHT_DEFAULT)
                cursor.execute("""
                    UPDATE prediction_paths
                    SET status = 'EXPIRED', ended_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), old_path["id"]))

            # Expire any stale pending nodes
            cursor.execute("""
                UPDATE prediction_nodes SET status = 'EXPIRED'
                WHERE status = 'PENDING' AND created_at < ?
            """, ((datetime.now() - timedelta(hours=12)).isoformat(),))

            conn.commit()

        self._active_path_id = None
        self._pending_node_id = None
        if rows:
            log.info("Day reset — expired old paths",
                     count=len(rows),
                     carry_contrarian=f"{self._contrarian_weight:.2f}")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process_candle(self, analysis: dict) -> Optional[dict]:
        """
        Process a new candle's analysis data.

        1. Day-reset check
        2. If pending node exists → match against actual data
        3. Update path (deepen or degrade)
        4. Generate new predictions for next candle
        5. If conviction >= threshold → emit bifurcated signal

        Returns a summary dict (attached to ``analysis["prediction_tree"]``) or None.
        """
        self._check_day_reset()

        now = datetime.now()
        spot = analysis.get("spot_price", 0)
        score = analysis.get("combined_score", 0)
        verdict = analysis.get("verdict", "")
        pcr = analysis.get("pcr", 0)
        iv_skew = analysis.get("iv_skew", 0)
        vix = analysis.get("vix", 0)

        matched_scenario = None
        best_score = 0.0

        # --- Step 1: Match pending predictions against this candle ---
        if self._pending_node_id:
            matched_scenario, best_score = self._match_predictions(
                self._pending_node_id, analysis
            )

        # --- Step 2: Update path ---
        if self._active_path_id is None:
            self._create_path(now)

        if matched_scenario and matched_scenario != "NONE":
            self._extend_path(matched_scenario, best_score, analysis)
        else:
            self._degrade_path()

        # --- Step 3: Generate new predictions ---
        scenarios = self._generate_predictions(analysis)
        parent_node = self._pending_node_id  # previous node becomes parent
        new_node_id = self._save_node(
            created_at=now,
            parent_node_id=parent_node,
            path_id=self._active_path_id,
            depth=self._get_current_depth(),
            scenarios=scenarios,
            analysis=analysis,
        )
        self._pending_node_id = new_node_id

        # --- Step 4: Check signal ---
        signal = None
        path = self._get_path()
        conviction = path["conviction_pct"] if path else 0
        if conviction >= SIGNAL_CONVICTION_THRESHOLD and path:
            signal = self._generate_bifurcated_signal(path, analysis)

        depth = path["current_depth"] if path else 0
        direction = path["current_direction"] if path else "UNKNOWN"

        log.info("Prediction tree updated",
                 depth=depth,
                 conviction=f"{conviction:.0f}%",
                 matched=matched_scenario or "FIRST",
                 match_score=f"{best_score:.2f}",
                 direction=direction)

        return {
            "depth": depth,
            "conviction": conviction,
            "direction": direction,
            "matched_scenario": matched_scenario,
            "match_score": round(best_score, 3),
            "signal": signal,
            "contrarian_weight": round(self._contrarian_weight, 3),
        }

    # ------------------------------------------------------------------
    # Prediction generation
    # ------------------------------------------------------------------

    def _generate_predictions(self, analysis: dict) -> dict:
        """
        Generate 3 scenarios for the NEXT candle.

        Returns dict with keys 'A', 'B', 'C', each a dict of expected ranges.
        """
        spot = analysis.get("spot_price", 0)
        score = analysis.get("combined_score", 0)
        pcr = analysis.get("pcr", 1.0)
        iv_skew = analysis.get("iv_skew", 0)
        verdict = analysis.get("verdict", "")
        phase = analysis.get("oi_acceleration", {}).get("phase", "stable")

        is_bullish = score > 0
        spot_step = spot * SPOT_STEP_PCT / 100  # absolute points

        # Premium momentum direction
        pm = analysis.get("premium_momentum", {})
        call_pm = pm.get("call_premium_change_pct", 0)
        put_pm = pm.get("put_premium_change_pct", 0)

        # OI direction from changes
        ce_change = analysis.get("call_oi_change", 0)
        pe_change = analysis.get("put_oi_change", 0)

        # Contrarian boost for strong signals
        is_strong = abs(score) > STRONG_SIGNAL_THRESHOLD
        c_boost = self._contrarian_weight if is_strong else 0.0

        # --- Scenario A: Continuation ---
        if is_bullish:
            a_spot = (spot, spot + 2 * spot_step)
            a_score = (score, score + SCORE_STEP)
            a_oi_dir = "PE_RISING"
            a_prem_dir = "CALL_UP"
            a_pcr = (pcr, pcr + PCR_STEP)
            a_iv_skew = "NEGATIVE"  # call IV higher = bullish
            a_phase = "accumulation"
        else:
            a_spot = (spot - 2 * spot_step, spot)
            a_score = (score - SCORE_STEP, score)
            a_oi_dir = "CE_RISING"
            a_prem_dir = "PUT_UP"
            a_pcr = (pcr - PCR_STEP, pcr)
            a_iv_skew = "POSITIVE"  # put IV higher = bearish
            a_phase = "distribution"

        scenario_a = {
            "label": "Continuation",
            "spot_range": [round(a_spot[0], 2), round(a_spot[1], 2)],
            "score_range": [round(a_score[0], 1), round(a_score[1], 1)],
            "oi_direction": a_oi_dir,
            "premium_direction": a_prem_dir,
            "pcr_range": [round(a_pcr[0], 2), round(a_pcr[1], 2)],
            "iv_skew_direction": a_iv_skew,
            "phase_expected": a_phase,
            "contrarian_boost": 0.0,
        }

        # --- Scenario B: Reversal ---
        if is_bullish:
            b_spot = (spot - 2 * spot_step, spot)
            b_score = (score - 2 * SCORE_STEP, max(score - SCORE_STEP, -100))
            b_oi_dir = "CE_RISING"
            b_prem_dir = "PUT_UP"
            b_pcr = (pcr - PCR_STEP, pcr)
            b_iv_skew = "POSITIVE"
            b_phase = "distribution"
        else:
            b_spot = (spot, spot + 2 * spot_step)
            b_score = (min(score + SCORE_STEP, 100), score + 2 * SCORE_STEP)
            b_oi_dir = "PE_RISING"
            b_prem_dir = "CALL_UP"
            b_pcr = (pcr, pcr + PCR_STEP)
            b_iv_skew = "NEGATIVE"
            b_phase = "accumulation"

        scenario_b = {
            "label": "Reversal",
            "spot_range": [round(b_spot[0], 2), round(b_spot[1], 2)],
            "score_range": [round(b_score[0], 1), round(b_score[1], 1)],
            "oi_direction": b_oi_dir,
            "premium_direction": b_prem_dir,
            "pcr_range": [round(b_pcr[0], 2), round(b_pcr[1], 2)],
            "iv_skew_direction": b_iv_skew,
            "phase_expected": b_phase,
            "contrarian_boost": round(c_boost, 3),
        }

        # --- Scenario C: Consolidation ---
        scenario_c = {
            "label": "Consolidation",
            "spot_range": [round(spot - spot_step, 2), round(spot + spot_step, 2)],
            "score_range": [round(score - SCORE_STEP / 2, 1), round(score + SCORE_STEP / 2, 1)],
            "oi_direction": "BOTH",
            "premium_direction": "FLAT",
            "pcr_range": [round(pcr - PCR_STEP / 2, 2), round(pcr + PCR_STEP / 2, 2)],
            "iv_skew_direction": "FLAT",
            "phase_expected": "stable",
            "contrarian_boost": 0.0,
        }

        return {"A": scenario_a, "B": scenario_b, "C": scenario_c}

    # ------------------------------------------------------------------
    # Matching logic
    # ------------------------------------------------------------------

    def _match_predictions(self, node_id: int, actual: dict) -> tuple:
        """
        Score each pending scenario against actual candle data.

        Returns (best_scenario_letter, best_score) or ("NONE", 0.0).
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prediction_nodes WHERE id = ?", (node_id,))
            row = cursor.fetchone()
            if not row:
                return ("NONE", 0.0)
            node = dict(row)

        if node["status"] != "PENDING":
            return ("NONE", 0.0)

        scores = {}
        for letter in ("A", "B", "C"):
            blob = node.get(f"scenario_{letter.lower()}")
            if not blob:
                scores[letter] = 0.0
                continue
            try:
                scenario = json.loads(blob) if isinstance(blob, str) else blob
            except (json.JSONDecodeError, TypeError):
                scores[letter] = 0.0
                continue
            scores[letter] = self._score_scenario(scenario, actual)

        # Pick best
        best_letter = max(scores, key=scores.get)
        best_score = scores[best_letter]

        # Update node in DB
        status = "MATCHED" if best_score >= MATCH_THRESHOLD else "BROKEN"
        matched = best_letter if best_score >= MATCH_THRESHOLD else "NONE"

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE prediction_nodes
                SET matched_scenario = ?, match_score_a = ?, match_score_b = ?,
                    match_score_c = ?, status = ?
                WHERE id = ?
            """, (matched, scores["A"], scores["B"], scores["C"], status, node_id))
            conn.commit()

        # Update contrarian weight learning
        if matched != "NONE":
            was_strong = abs(node.get("combined_score", 0)) > STRONG_SIGNAL_THRESHOLD
            self._update_contrarian_weight(matched, was_strong)

        return (matched, best_score)

    def _score_scenario(self, scenario: dict, actual: dict) -> float:
        """
        Score a single scenario against actual data.

        Each metric scored 0.0–1.0, then weighted.
        """
        total = 0.0

        # 1. Spot range
        spot = actual.get("spot_price", 0)
        sr = scenario.get("spot_range", [0, 0])
        total += METRIC_WEIGHTS["spot"] * self._range_score(spot, sr[0], sr[1])

        # 2. Combined score range
        score = actual.get("combined_score", 0)
        scr = scenario.get("score_range", [0, 0])
        total += METRIC_WEIGHTS["score"] * self._range_score(score, scr[0], scr[1])

        # 3. OI direction
        ce_change = actual.get("call_oi_change", 0)
        pe_change = actual.get("put_oi_change", 0)
        actual_oi_dir = self._classify_oi_direction(ce_change, pe_change)
        expected_oi_dir = scenario.get("oi_direction", "BOTH")
        total += METRIC_WEIGHTS["oi_dir"] * self._categorical_score(actual_oi_dir, expected_oi_dir)

        # 4. Premium direction
        pm = actual.get("premium_momentum", {})
        call_pm = pm.get("call_premium_change_pct", 0)
        put_pm = pm.get("put_premium_change_pct", 0)
        actual_prem_dir = self._classify_premium_direction(call_pm, put_pm)
        expected_prem_dir = scenario.get("premium_direction", "FLAT")
        total += METRIC_WEIGHTS["prem_dir"] * self._categorical_score(actual_prem_dir, expected_prem_dir)

        # 5. PCR range
        pcr = actual.get("pcr", 1.0)
        pcr_r = scenario.get("pcr_range", [0.8, 1.2])
        total += METRIC_WEIGHTS["pcr"] * self._range_score(pcr, pcr_r[0], pcr_r[1])

        # 6. IV skew direction
        iv_skew = actual.get("iv_skew", 0)
        actual_iv_dir = self._classify_iv_skew(iv_skew)
        expected_iv_dir = scenario.get("iv_skew_direction", "FLAT")
        total += METRIC_WEIGHTS["iv_skew"] * self._categorical_score(actual_iv_dir, expected_iv_dir)

        # 7. Phase
        actual_phase = actual.get("oi_acceleration", {}).get("phase", "stable")
        expected_phase = scenario.get("phase_expected", "stable")
        total += METRIC_WEIGHTS["phase"] * (1.0 if actual_phase == expected_phase else 0.0)

        # 8. Contrarian boost (added to scenario B's score when applicable)
        c_boost = scenario.get("contrarian_boost", 0.0)
        total += METRIC_WEIGHTS["contrarian"] * c_boost

        return round(total, 4)

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _range_score(value: float, lo: float, hi: float) -> float:
        """Score 1.0 if value in [lo, hi], partial decay outside."""
        if lo <= value <= hi:
            return 1.0
        band = max(hi - lo, 0.001)
        distance = min(abs(value - lo), abs(value - hi))
        # Linear decay: 0.0 at 2x band width away
        return max(0.0, 1.0 - distance / (2 * band))

    @staticmethod
    def _classify_oi_direction(ce_change: float, pe_change: float) -> str:
        if ce_change > 0 and pe_change <= 0:
            return "CE_RISING"
        if pe_change > 0 and ce_change <= 0:
            return "PE_RISING"
        if ce_change > 0 and pe_change > 0:
            return "BOTH"
        return "NEITHER"

    @staticmethod
    def _classify_premium_direction(call_pm: float, put_pm: float) -> str:
        if call_pm > 1.0 and call_pm > put_pm:
            return "CALL_UP"
        if put_pm > 1.0 and put_pm > call_pm:
            return "PUT_UP"
        return "FLAT"

    @staticmethod
    def _classify_iv_skew(iv_skew: float) -> str:
        if iv_skew > 2.0:
            return "POSITIVE"
        if iv_skew < -2.0:
            return "NEGATIVE"
        return "FLAT"

    @staticmethod
    def _categorical_score(actual: str, expected: str) -> float:
        if actual == expected:
            return 1.0
        # Partial credit for related categories
        if expected == "BOTH" or actual == "BOTH":
            return 0.4
        if expected == "FLAT" or actual == "FLAT":
            return 0.3
        return 0.0

    # ------------------------------------------------------------------
    # Path management
    # ------------------------------------------------------------------

    def _create_path(self, now: datetime):
        """Start a new prediction path."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO prediction_paths
                    (started_at, contrarian_weight, status)
                VALUES (?, ?, 'ACTIVE')
            """, (now.isoformat(), self._contrarian_weight))
            conn.commit()
            self._active_path_id = cursor.lastrowid

        log.info("New prediction path created",
                 path_id=self._active_path_id,
                 contrarian_w=f"{self._contrarian_weight:.2f}")

    def _extend_path(self, matched: str, score: float, analysis: dict):
        """Deepen path on a successful match."""
        if not self._active_path_id:
            return

        # Determine direction from matched scenario
        direction_map = {"A": "CONTINUATION", "B": "REVERSAL", "C": "CONSOLIDATION"}
        direction = direction_map.get(matched, "UNKNOWN")

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prediction_paths WHERE id = ?", (self._active_path_id,))
            row = cursor.fetchone()
            if not row:
                return
            path = dict(row)

            new_depth = path["current_depth"] + 1
            new_consec = path["consecutive_matches"] + 1
            new_max = max(path["max_depth_reached"], new_depth)
            total = path["total_predictions"] + 1
            correct = path["correct_predictions"] + 1
            accuracy = (correct / total * 100) if total > 0 else 0
            conviction = self._get_conviction(new_depth)

            cursor.execute("""
                UPDATE prediction_paths
                SET current_depth = ?, max_depth_reached = ?,
                    current_direction = ?, conviction_pct = ?,
                    consecutive_matches = ?, consecutive_misses = 0,
                    total_predictions = ?, correct_predictions = ?,
                    accuracy_pct = ?, contrarian_weight = ?
                WHERE id = ?
            """, (new_depth, new_max, direction, conviction,
                  new_consec, total, correct, accuracy,
                  self._contrarian_weight, self._active_path_id))
            conn.commit()

    def _degrade_path(self):
        """Handle a miss: tolerate 1 miss, break on 2 consecutive."""
        if not self._active_path_id:
            return

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prediction_paths WHERE id = ?", (self._active_path_id,))
            row = cursor.fetchone()
            if not row:
                return
            path = dict(row)

            consec_misses = path["consecutive_misses"] + 1
            total = path["total_predictions"] + 1
            accuracy = (path["correct_predictions"] / total * 100) if total > 0 else 0

            if consec_misses >= 2:
                # Break path
                self._break_path(path, total, accuracy)
            else:
                # Tolerate single miss — drop conviction by 10
                new_conviction = max(0, path["conviction_pct"] - 10)
                cursor.execute("""
                    UPDATE prediction_paths
                    SET consecutive_misses = ?, conviction_pct = ?,
                        total_predictions = ?, accuracy_pct = ?
                    WHERE id = ?
                """, (consec_misses, new_conviction, total, accuracy,
                      self._active_path_id))
                conn.commit()
                log.info("Prediction miss tolerated",
                         misses=consec_misses,
                         conviction=f"{new_conviction:.0f}%")

    def _break_path(self, path: Optional[dict] = None, total: int = 0, accuracy: float = 0):
        """Break the active path and start fresh next cycle."""
        if not self._active_path_id:
            return

        with get_connection() as conn:
            cursor = conn.cursor()
            if not path:
                cursor.execute("SELECT * FROM prediction_paths WHERE id = ?", (self._active_path_id,))
                row = cursor.fetchone()
                if row:
                    path = dict(row)
                    total = path["total_predictions"]
                    accuracy = path["accuracy_pct"]

            cursor.execute("""
                UPDATE prediction_paths
                SET status = 'BROKEN', ended_at = ?,
                    total_predictions = ?, accuracy_pct = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), total, accuracy,
                  self._active_path_id))

            # Expire any lingering pending nodes
            cursor.execute("""
                UPDATE prediction_nodes SET status = 'EXPIRED'
                WHERE path_id = ? AND status = 'PENDING'
            """, (self._active_path_id,))

            conn.commit()

        old_id = self._active_path_id
        max_depth = path["max_depth_reached"] if path else 0
        log.info("Prediction path broken",
                 path_id=old_id,
                 max_depth=max_depth,
                 accuracy=f"{accuracy:.1f}%")

        self._active_path_id = None
        self._pending_node_id = None

    def _get_path(self) -> Optional[dict]:
        """Get the active path dict."""
        if not self._active_path_id:
            return None
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prediction_paths WHERE id = ?", (self._active_path_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def _get_current_depth(self) -> int:
        path = self._get_path()
        return path["current_depth"] if path else 0

    @staticmethod
    def _get_conviction(depth: int) -> float:
        if depth >= 5:
            return CONVICTION_MAX
        return CONVICTION_MAP.get(depth, 0)

    # ------------------------------------------------------------------
    # Contrarian weight learning
    # ------------------------------------------------------------------

    def _get_learned_contrarian_weight(self) -> float:
        return self._contrarian_weight

    def _update_contrarian_weight(self, matched_scenario: str, was_strong: bool):
        """
        Learn the contrarian pattern over time.

        If B (reversal) matches on a strong signal → increase weight.
        If A (continuation) matches on a strong signal → decrease weight.
        """
        if not was_strong:
            return

        if matched_scenario == "B":
            self._contrarian_weight = min(
                CONTRARIAN_WEIGHT_MAX,
                self._contrarian_weight + 0.05
            )
        elif matched_scenario == "A":
            self._contrarian_weight = max(
                CONTRARIAN_WEIGHT_MIN,
                self._contrarian_weight - 0.03
            )

        # Persist to active path
        if self._active_path_id:
            with get_connection() as conn:
                conn.execute("""
                    UPDATE prediction_paths SET contrarian_weight = ?
                    WHERE id = ?
                """, (self._contrarian_weight, self._active_path_id))
                conn.commit()

    # ------------------------------------------------------------------
    # Bifurcated FOR/AGAINST signal
    # ------------------------------------------------------------------

    def _generate_bifurcated_signal(self, path: dict, analysis: dict) -> Optional[dict]:
        """
        When conviction is high enough, generate both FOR and AGAINST predictions.
        """
        conviction = path.get("conviction_pct", 0)
        direction = path.get("current_direction", "UNKNOWN")
        spot = analysis.get("spot_price", 0)

        if conviction < SIGNAL_CONVICTION_THRESHOLD:
            return None

        spot_step = spot * SPOT_STEP_PCT / 100

        # Base probabilities from conviction
        # Higher conviction = higher probability for the path direction
        for_prob = conviction / 100.0
        against_prob = 1.0 - for_prob

        # Adjust by contrarian weight for reversal scenarios
        if direction == "REVERSAL":
            # Reversal path: contrarian weight boosts the reversal probability
            for_prob = min(0.95, for_prob + self._contrarian_weight * 0.1)
            against_prob = 1.0 - for_prob

        # Determine actual market direction from path
        score = analysis.get("combined_score", 0)
        if direction == "CONTINUATION":
            for_direction = "BULLISH" if score > 0 else "BEARISH"
        elif direction == "REVERSAL":
            for_direction = "BEARISH" if score > 0 else "BULLISH"
        else:
            for_direction = "NEUTRAL"

        against_direction = "BEARISH" if for_direction == "BULLISH" else "BULLISH"
        if for_direction == "NEUTRAL":
            against_direction = "NEUTRAL"

        # Spot targets
        if for_direction == "BULLISH":
            for_target = round(spot + 3 * spot_step, 2)
            against_target = round(spot - 3 * spot_step, 2)
        elif for_direction == "BEARISH":
            for_target = round(spot - 3 * spot_step, 2)
            against_target = round(spot + 3 * spot_step, 2)
        else:
            for_target = round(spot, 2)
            against_target = round(spot, 2)

        # Recommend the higher probability side
        recommended = "FOR" if for_prob >= against_prob else "AGAINST"

        signal = {
            "for": {
                "direction": for_direction,
                "probability": round(for_prob * 100, 1),
                "target": for_target,
            },
            "against": {
                "direction": against_direction,
                "probability": round(against_prob * 100, 1),
                "target": against_target,
            },
            "recommended": recommended,
            "conviction": conviction,
            "path_direction": direction,
        }

        # Persist signal to path
        with get_connection() as conn:
            rec_dir = signal["for"]["direction"] if recommended == "FOR" else signal["against"]["direction"]
            rec_target = signal["for"]["target"] if recommended == "FOR" else signal["against"]["target"]
            conn.execute("""
                UPDATE prediction_paths
                SET signal_emitted = 1, signal_direction = ?, signal_target = ?
                WHERE id = ?
            """, (rec_dir, rec_target, self._active_path_id))
            conn.commit()

        log.info("Bifurcated signal emitted",
                 recommended=recommended,
                 for_dir=signal["for"]["direction"],
                 for_prob=f"{signal['for']['probability']:.0f}%",
                 against_dir=signal["against"]["direction"],
                 against_prob=f"{signal['against']['probability']:.0f}%")

        return signal

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    def _save_node(self, created_at: datetime, parent_node_id: Optional[int],
                   path_id: Optional[int], depth: int,
                   scenarios: dict, analysis: dict) -> int:
        """Save a new prediction node and return its ID."""
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO prediction_nodes
                    (created_at, parent_node_id, path_id, depth,
                     scenario_a, scenario_b, scenario_c,
                     spot_price, combined_score, verdict, pcr, iv_skew, vix,
                     status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """, (
                created_at.isoformat(),
                parent_node_id,
                path_id,
                depth,
                json.dumps(scenarios.get("A", {})),
                json.dumps(scenarios.get("B", {})),
                json.dumps(scenarios.get("C", {})),
                analysis.get("spot_price", 0),
                analysis.get("combined_score", 0),
                analysis.get("verdict", ""),
                analysis.get("pcr", 0),
                analysis.get("iv_skew", 0),
                analysis.get("vix", 0),
            ))
            conn.commit()
            return cursor.lastrowid

    # ------------------------------------------------------------------
    # Stats for API
    # ------------------------------------------------------------------

    def get_prediction_stats(self) -> dict:
        """Aggregate accuracy stats across all paths."""
        with get_connection() as conn:
            cursor = conn.cursor()

            # Overall stats
            cursor.execute("""
                SELECT
                    COUNT(*) as total_paths,
                    AVG(accuracy_pct) as avg_accuracy,
                    MAX(max_depth_reached) as best_depth,
                    AVG(max_depth_reached) as avg_depth,
                    SUM(total_predictions) as total_predictions,
                    SUM(correct_predictions) as total_correct
                FROM prediction_paths
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
                    MAX(max_depth_reached) as best_depth_today
                FROM prediction_paths
                WHERE started_at >= ?
            """, (today_str,))
            today_row = cursor.fetchone()
            stats["today"] = dict(today_row) if today_row else {}

            # Contrarian effectiveness
            cursor.execute("""
                SELECT
                    COUNT(*) as total_strong_matches,
                    SUM(CASE WHEN matched_scenario = 'B' THEN 1 ELSE 0 END) as reversal_matches,
                    SUM(CASE WHEN matched_scenario = 'A' THEN 1 ELSE 0 END) as continuation_matches
                FROM prediction_nodes
                WHERE status = 'MATCHED'
                  AND ABS(combined_score) > ?
            """, (STRONG_SIGNAL_THRESHOLD,))
            contrarian_row = cursor.fetchone()
            stats["contrarian"] = dict(contrarian_row) if contrarian_row else {}
            stats["contrarian_weight"] = self._contrarian_weight

            return stats
