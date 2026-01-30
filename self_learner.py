"""
Self-Learning Module for OI Signal Optimization

This module implements:
1. EMA Accuracy Tracker - Tracks rolling accuracy with auto-pause
2. Adaptive Weights - Adjusts component weights based on accuracy
3. Outcome Tracking - Records signal outcomes for learning
4. Dynamic Thresholds - Adjusts thresholds based on performance
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List
from database import (
    save_signal_outcome, update_signal_outcome, get_pending_signals,
    get_signal_accuracy, save_learned_weights, get_latest_learned_weights,
    save_component_accuracy, get_component_accuracy, get_analysis_history
)


class EMAAccuracyTracker:
    """
    Exponential Moving Average tracker for signal accuracy.

    Uses EMA to weight recent accuracy more heavily than older results.
    Implements conservative auto-pause after consecutive errors or low accuracy.
    """

    def __init__(self, alpha: float = 0.3, pause_threshold: float = 0.5,
                 max_consecutive_errors: int = 3):
        """
        Initialize the EMA tracker.

        Args:
            alpha: EMA smoothing factor (higher = more recent weight)
            pause_threshold: Pause signals if accuracy drops below this
            max_consecutive_errors: Pause after this many consecutive errors
        """
        self.alpha = alpha
        self.pause_threshold = pause_threshold
        self.max_consecutive_errors = max_consecutive_errors

        # Load saved state or initialize
        saved = get_latest_learned_weights()
        if saved:
            self.ema_accuracy = saved.get("ema_accuracy", 0.5)
            self.consecutive_errors = saved.get("consecutive_errors", 0)
            self.is_paused = saved.get("is_paused", False)
        else:
            self.ema_accuracy = 0.5  # Start neutral
            self.consecutive_errors = 0
            self.is_paused = False

    def update(self, was_correct: bool) -> None:
        """
        Update EMA accuracy with new outcome.

        Args:
            was_correct: Whether the signal was correct
        """
        new_value = 1.0 if was_correct else 0.0
        self.ema_accuracy = self.alpha * new_value + (1 - self.alpha) * self.ema_accuracy

        if not was_correct:
            self.consecutive_errors += 1
        else:
            self.consecutive_errors = 0

        # Check if should pause
        self._check_pause_conditions()

    def _check_pause_conditions(self) -> None:
        """Check if signals should be paused."""
        if self.consecutive_errors >= self.max_consecutive_errors:
            self.is_paused = True
            print(f"[SelfLearner] PAUSED: {self.consecutive_errors} consecutive errors")
        elif self.ema_accuracy < self.pause_threshold:
            self.is_paused = True
            print(f"[SelfLearner] PAUSED: Accuracy {self.ema_accuracy:.1%} below threshold")
        else:
            self.is_paused = False

    def should_trade(self) -> bool:
        """
        Check if trading should be enabled.

        Returns:
            True if accuracy is acceptable and no consecutive error streak
        """
        return not self.is_paused

    def get_status(self) -> dict:
        """Get current tracker status."""
        return {
            "ema_accuracy": round(self.ema_accuracy, 3),
            "consecutive_errors": self.consecutive_errors,
            "is_paused": self.is_paused,
            "should_trade": self.should_trade()
        }

    def reset_pause(self) -> None:
        """Manually reset the pause state (e.g., at start of new day)."""
        self.is_paused = False
        self.consecutive_errors = 0
        print("[SelfLearner] Pause reset")


class AdaptiveWeights:
    """
    Dynamically adjusts component weights based on accuracy.

    Components with higher accuracy get higher weights.
    """

    def __init__(self):
        """Initialize with default weights."""
        saved = get_latest_learned_weights()
        if saved:
            self.weights = {
                "otm": saved.get("otm_weight", 0.60),
                "atm": saved.get("atm_weight", 0.25),
                "itm": saved.get("itm_weight", 0.15),
                "momentum": saved.get("momentum_weight", 0.20)
            }
            self.thresholds = {
                "strong": saved.get("strong_threshold", 40.0),
                "moderate": saved.get("moderate_threshold", 15.0),
                "weak": saved.get("weak_threshold", 0.0)
            }
        else:
            self.weights = {"otm": 0.60, "atm": 0.25, "itm": 0.15, "momentum": 0.20}
            self.thresholds = {"strong": 40.0, "moderate": 15.0, "weak": 0.0}

    def update_weights(self) -> None:
        """
        Update weights based on component accuracy.

        Higher accuracy = higher weight (normalized to sum to 1.0)
        """
        accuracies = get_component_accuracy(lookback_days=30)

        if not accuracies:
            return  # No data yet

        # Get accuracy for each component (use 30-min as primary)
        component_acc = {}
        for comp in ["otm", "atm", "itm", "momentum"]:
            if comp in accuracies:
                component_acc[comp] = accuracies[comp].get("accuracy_30min", 0.5)
            else:
                component_acc[comp] = 0.5  # Default neutral

        # Normalize to sum to 1.0 (excluding momentum which has its own weight)
        total = sum(component_acc.values())
        if total > 0:
            # Scale weights proportionally to accuracy
            # Use square of accuracy to emphasize differences
            squared_acc = {k: v**2 for k, v in component_acc.items()}
            total_squared = sum(squared_acc.values())

            if total_squared > 0:
                self.weights = {k: v / total_squared for k, v in squared_acc.items()}

        print(f"[SelfLearner] Updated weights: {self.weights}")

    def update_thresholds(self) -> None:
        """
        Adjust signal strength thresholds based on performance.

        If strong signals are very accurate, lower threshold to trade more.
        If strong signals are inaccurate, raise threshold to be more selective.
        """
        accuracy = get_signal_accuracy(lookback_days=30)

        if not accuracy:
            return

        for strength in ["strong", "moderate", "weak"]:
            if strength in accuracy and accuracy[strength]["total"] >= 10:
                acc = accuracy[strength]["accuracy"]
                base = {"strong": 40.0, "moderate": 15.0, "weak": 0.0}[strength]

                # Adjust by up to +/- 10 points based on accuracy
                adjustment = (acc - 0.5) * 20  # -10 to +10 range
                self.thresholds[strength] = base - adjustment  # Lower if accurate

        print(f"[SelfLearner] Updated thresholds: {self.thresholds}")

    def get_adjusted_weights(self, include_atm: bool, include_itm: bool,
                             has_momentum: bool) -> dict:
        """
        Get weights adjusted for current analysis configuration.

        Args:
            include_atm: Whether ATM is enabled
            include_itm: Whether ITM is enabled
            has_momentum: Whether momentum is available

        Returns:
            Normalized weights for current config
        """
        active_weights = {}

        active_weights["otm"] = self.weights["otm"]

        if include_atm:
            active_weights["atm"] = self.weights["atm"]
        else:
            active_weights["atm"] = 0.0

        if include_itm:
            active_weights["itm"] = self.weights["itm"]
        else:
            active_weights["itm"] = 0.0

        if has_momentum:
            active_weights["momentum"] = self.weights["momentum"]
        else:
            active_weights["momentum"] = 0.0

        # Normalize to sum to 1.0
        total = sum(active_weights.values())
        if total > 0:
            active_weights = {k: v / total for k, v in active_weights.items()}

        return active_weights

    def get_threshold(self, strength: str) -> float:
        """Get threshold for a signal strength level."""
        return self.thresholds.get(strength, 0.0)


class SignalTracker:
    """
    Tracks signals and their outcomes for self-learning.

    Records each signal with entry/SL/target and checks outcomes later.
    """

    def __init__(self, lookback_minutes: int = 30):
        """
        Initialize the signal tracker.

        Args:
            lookback_minutes: How long to wait before checking outcome
        """
        self.lookback_minutes = lookback_minutes
        self.ema_tracker = EMAAccuracyTracker()

    def record_signal(self, timestamp: datetime, verdict: str, strength: str,
                      combined_score: float, spot_price: float,
                      trade_setup: Optional[dict] = None,
                      signal_confidence: float = 0.0) -> Optional[int]:
        """
        Record a new signal for tracking.

        Args:
            timestamp: When signal was generated
            verdict: Signal verdict
            strength: Signal strength
            combined_score: Combined analysis score
            spot_price: Current spot price
            trade_setup: Optional trade setup with SL/targets
            signal_confidence: Calculated confidence score

        Returns:
            Signal ID or None if signal shouldn't be tracked
        """
        # Only track non-neutral signals with sufficient confidence
        if "neutral" in verdict.lower():
            return None

        if signal_confidence < 40:
            return None  # Don't track low-confidence signals

        sl_price = None
        target1 = None
        target2 = None
        max_pain = None

        if trade_setup:
            sl_price = trade_setup.get("sl")
            target1 = trade_setup.get("target1")
            target2 = trade_setup.get("target2")
            max_pain = trade_setup.get("max_pain")

        signal_id = save_signal_outcome(
            signal_timestamp=timestamp,
            verdict=verdict,
            strength=strength,
            combined_score=combined_score,
            entry_price=spot_price,
            sl_price=sl_price,
            target1_price=target1,
            target2_price=target2,
            max_pain=max_pain,
            signal_confidence=signal_confidence,
            ema_accuracy=self.ema_tracker.ema_accuracy
        )

        return signal_id

    def check_pending_signals(self, current_price: float, current_time: datetime) -> List[dict]:
        """
        Check pending signals and update outcomes.

        Args:
            current_price: Current spot price
            current_time: Current timestamp

        Returns:
            List of resolved signals with outcomes
        """
        pending = get_pending_signals()
        resolved = []

        for signal in pending:
            signal_time = datetime.fromisoformat(signal["signal_timestamp"])
            elapsed = (current_time - signal_time).total_seconds() / 60

            # Only check after lookback period
            if elapsed < self.lookback_minutes:
                continue

            # Determine outcome
            entry_price = signal["entry_price"]
            sl_price = signal.get("sl_price")
            target1 = signal.get("target1_price")
            target2 = signal.get("target2_price")

            verdict = signal["verdict"].lower()
            is_bullish = "bull" in verdict

            # Check price movement
            price_change = current_price - entry_price
            price_change_pct = (price_change / entry_price) * 100

            # Determine correctness
            was_correct = (is_bullish and price_change > 0) or \
                         (not is_bullish and price_change < 0)

            # Check if hit SL or target
            hit_sl = False
            hit_target = False

            if sl_price:
                if is_bullish and current_price <= sl_price:
                    hit_sl = True
                elif not is_bullish and current_price >= sl_price:
                    hit_sl = True

            if target1:
                if is_bullish and current_price >= target1:
                    hit_target = True
                elif not is_bullish and current_price <= target1:
                    hit_target = True

            # Update signal outcome
            update_signal_outcome(
                signal_id=signal["id"],
                outcome_timestamp=current_time,
                actual_exit_price=current_price,
                hit_target=hit_target,
                hit_sl=hit_sl,
                profit_loss_pct=price_change_pct,
                was_correct=was_correct
            )

            # Update EMA accuracy
            self.ema_tracker.update(was_correct)

            resolved.append({
                "signal_id": signal["id"],
                "verdict": signal["verdict"],
                "entry_price": entry_price,
                "exit_price": current_price,
                "was_correct": was_correct,
                "profit_loss_pct": round(price_change_pct, 2),
                "hit_target": hit_target,
                "hit_sl": hit_sl
            })

        return resolved

    def get_status(self) -> dict:
        """Get tracker status including accuracy metrics."""
        accuracy = get_signal_accuracy(lookback_days=30)

        return {
            "ema_tracker": self.ema_tracker.get_status(),
            "accuracy_by_strength": accuracy,
            "should_trade": self.ema_tracker.should_trade()
        }


class SelfLearner:
    """
    Main self-learning orchestrator.

    Coordinates all learning components and provides unified interface.
    """

    def __init__(self):
        """Initialize all learning components."""
        self.signal_tracker = SignalTracker()
        self.adaptive_weights = AdaptiveWeights()

    def process_new_signal(self, timestamp: datetime, analysis: dict) -> dict:
        """
        Process a new signal from OI analysis.

        Args:
            timestamp: Signal timestamp
            analysis: Full analysis result from analyze_tug_of_war

        Returns:
            Dict with signal_id, should_trade, confidence
        """
        # Check if we should be trading
        should_trade = self.signal_tracker.ema_tracker.should_trade()

        # Get signal details
        verdict = analysis.get("verdict", "Neutral")
        strength = analysis.get("strength", "none")
        combined_score = analysis.get("combined_score", 0)
        spot_price = analysis.get("spot_price", 0)
        trade_setup = analysis.get("trade_setup")
        signal_confidence = analysis.get("signal_confidence", 0)

        # Record signal if trading enabled
        signal_id = None
        if should_trade and signal_confidence >= 40:
            signal_id = self.signal_tracker.record_signal(
                timestamp=timestamp,
                verdict=verdict,
                strength=strength,
                combined_score=combined_score,
                spot_price=spot_price,
                trade_setup=trade_setup,
                signal_confidence=signal_confidence
            )

        return {
            "signal_id": signal_id,
            "should_trade": should_trade,
            "confidence": signal_confidence,
            "is_paused": not should_trade,
            "ema_accuracy": self.signal_tracker.ema_tracker.ema_accuracy,
            "consecutive_errors": self.signal_tracker.ema_tracker.consecutive_errors
        }

    def check_outcomes(self, current_price: float, current_time: datetime) -> List[dict]:
        """Check and update pending signal outcomes."""
        return self.signal_tracker.check_pending_signals(current_price, current_time)

    def update_learning(self) -> None:
        """
        Run periodic learning updates.

        Should be called daily (e.g., at market close).
        """
        # Update component weights based on accuracy
        self.adaptive_weights.update_weights()

        # Update thresholds based on accuracy by strength
        self.adaptive_weights.update_thresholds()

        # Save current state
        self._save_state()

        print("[SelfLearner] Learning update complete")

    def _save_state(self) -> None:
        """Save current learned state to database."""
        ema_status = self.signal_tracker.ema_tracker.get_status()
        weights = self.adaptive_weights.weights
        thresholds = self.adaptive_weights.thresholds

        save_learned_weights(
            otm_weight=weights.get("otm", 0.6),
            atm_weight=weights.get("atm", 0.25),
            itm_weight=weights.get("itm", 0.15),
            momentum_weight=weights.get("momentum", 0.2),
            strong_threshold=thresholds.get("strong", 40),
            moderate_threshold=thresholds.get("moderate", 15),
            weak_threshold=thresholds.get("weak", 0),
            ema_accuracy=ema_status["ema_accuracy"],
            consecutive_errors=ema_status["consecutive_errors"],
            is_paused=ema_status["is_paused"]
        )

    def get_adjusted_weights(self, include_atm: bool, include_itm: bool,
                             has_momentum: bool) -> dict:
        """Get current adaptive weights for analysis."""
        return self.adaptive_weights.get_adjusted_weights(
            include_atm, include_itm, has_momentum
        )

    def get_status(self) -> dict:
        """Get full self-learning status."""
        return {
            "signal_tracker": self.signal_tracker.get_status(),
            "weights": self.adaptive_weights.weights,
            "thresholds": self.adaptive_weights.thresholds
        }

    def reset_for_new_day(self) -> None:
        """Reset pause state at start of new trading day."""
        self.signal_tracker.ema_tracker.reset_pause()


# Singleton instance
_self_learner = None

def get_self_learner() -> SelfLearner:
    """Get the singleton SelfLearner instance."""
    global _self_learner
    if _self_learner is None:
        _self_learner = SelfLearner()
    return _self_learner


if __name__ == "__main__":
    # Test the self-learner
    print("Testing Self-Learner...")

    learner = get_self_learner()

    # Show initial status
    print(f"\nInitial Status:")
    print(f"  EMA Accuracy: {learner.signal_tracker.ema_tracker.ema_accuracy:.1%}")
    print(f"  Should Trade: {learner.signal_tracker.ema_tracker.should_trade()}")
    print(f"  Weights: {learner.adaptive_weights.weights}")
    print(f"  Thresholds: {learner.adaptive_weights.thresholds}")

    # Simulate some outcomes
    print("\nSimulating outcomes...")
    learner.signal_tracker.ema_tracker.update(True)  # Correct
    learner.signal_tracker.ema_tracker.update(True)  # Correct
    learner.signal_tracker.ema_tracker.update(False) # Wrong

    print(f"\nAfter 2 correct, 1 wrong:")
    print(f"  EMA Accuracy: {learner.signal_tracker.ema_tracker.ema_accuracy:.1%}")
    print(f"  Consecutive Errors: {learner.signal_tracker.ema_tracker.consecutive_errors}")
    print(f"  Should Trade: {learner.signal_tracker.ema_tracker.should_trade()}")

    # Simulate consecutive errors
    print("\nSimulating 3 consecutive errors...")
    learner.signal_tracker.ema_tracker.update(False)
    learner.signal_tracker.ema_tracker.update(False)
    learner.signal_tracker.ema_tracker.update(False)

    print(f"\nAfter 3 consecutive errors:")
    print(f"  EMA Accuracy: {learner.signal_tracker.ema_tracker.ema_accuracy:.1%}")
    print(f"  Consecutive Errors: {learner.signal_tracker.ema_tracker.consecutive_errors}")
    print(f"  Is Paused: {learner.signal_tracker.ema_tracker.is_paused}")
    print(f"  Should Trade: {learner.signal_tracker.ema_tracker.should_trade()}")

    print("\nTest complete.")
