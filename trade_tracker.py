"""
Trade Tracker - Manages trade setup lifecycle and win rate tracking

Trade Lifecycle:
    PENDING -> ACTIVE -> WON/LOST
    PENDING -> CANCELLED (if direction flips)
    PENDING -> EXPIRED (if market closes)
"""

from datetime import datetime, time, timedelta
from typing import Optional, List

from database import (
    save_trade_setup,
    get_active_trade_setup,
    update_trade_setup_status,
    get_trade_setup_stats,
    get_last_resolved_trade,
)
from self_learner import get_self_learner
from logger import get_logger

log = get_logger("trade_tracker")


# Market timing constants
MARKET_CLOSE = time(15, 25)  # Expire PENDING setups 5 mins before market close
TRADE_SETUP_START = time(9, 30)   # Only create setups after 9:30 AM
TRADE_SETUP_END = time(15, 15)    # Stop creating setups at 3:15 PM
FORCE_CLOSE_TIME = time(15, 20)   # Force close ACTIVE trades at 3:20 PM


class TradeTracker:
    """Manages persistent trade setups with lifecycle tracking."""

    def __init__(self):
        """Initialize the trade tracker."""
        # Default thresholds (will be overridden by learned values)
        self._default_confidence_min = 50.0
        self._default_confidence_max = 90.0

        self.entry_tolerance = 0.02  # 2% tolerance for entry activation
        self.cooldown_minutes = 12  # Cooldown after trade resolution (4 fetch cycles - quality over quantity)
        self.move_threshold_pct = 0.8  # Skip if spot moved 0.8%+ in direction (widened from 0.5%)
        self.bounce_threshold_pct = 0.3  # Skip PUT if bounced 0.3%+ from low
        self.self_learner = get_self_learner()
        self.direction_flip_cooldown_minutes = 15  # 5 fetch cycles
        self.last_suggested_direction = None
        self.last_suggestion_time = None
        self.cancellation_cooldown_minutes = 30  # CRITICAL: No new trades for 30min after cancellation
        self.last_cancelled_time = None

    @property
    def confidence_threshold(self) -> float:
        """Get minimum confidence threshold (learned or default)."""
        learned = self.self_learner.get_learned_confidence_thresholds()
        return learned.get("min_threshold", self._default_confidence_min)

    @property
    def confidence_max(self) -> float:
        """Get maximum confidence threshold (learned or default)."""
        learned = self.self_learner.get_learned_confidence_thresholds()
        return learned.get("max_threshold", self._default_confidence_max)

    @property
    def confidence_exclude_ranges(self) -> list:
        """Get confidence ranges to exclude (learned)."""
        learned = self.self_learner.get_learned_confidence_thresholds()
        return learned.get("exclude_ranges", [])

    def _count_confirmations(self, analysis: dict) -> int:
        """Count aligned confirmation signals."""
        confirmations = 0
        verdict = analysis.get("verdict", "").lower()
        is_bullish = "bull" in verdict

        # 1. OI-Price alignment
        if analysis.get("confirmation_status") == "CONFIRMED":
            confirmations += 1

        # 2. Market regime alignment
        regime = analysis.get("market_regime", {}).get("regime", "range_bound")
        if (is_bullish and regime == "trending_up") or \
           (not is_bullish and regime == "trending_down"):
            confirmations += 1

        # 3. Premium momentum alignment
        pm = analysis.get("premium_momentum", {})
        pm_score = pm.get("premium_momentum_score", 0)
        if (is_bullish and pm_score > 10) or (not is_bullish and pm_score < -10):
            confirmations += 1

        # 4. IV skew alignment
        iv_skew = analysis.get("iv_skew", {})
        skew_score = iv_skew.get("skew_score", 0) if isinstance(iv_skew, dict) else 0
        if (is_bullish and skew_score < -5) or (not is_bullish and skew_score > 5):
            confirmations += 1

        return confirmations

    def _is_in_cancellation_cooldown(self) -> bool:
        """
        Check if we're in cooldown after cancelling a trade.

        CRITICAL: After cancelling a PENDING trade (due to verdict flip, self-learner pause, etc),
        we must NOT immediately create a new one with different prices. This would be dangerous
        for users who placed limit orders based on the cancelled trade.

        Returns:
            True if still in cooldown (should skip trade creation)
        """
        if not self.last_cancelled_time:
            return False

        time_since_cancellation = datetime.now() - self.last_cancelled_time
        cooldown_seconds = self.cancellation_cooldown_minutes * 60

        if time_since_cancellation.total_seconds() < cooldown_seconds:
            minutes_since = int(time_since_cancellation.total_seconds() / 60)
            minutes_remaining = self.cancellation_cooldown_minutes - minutes_since
            log.warning("Skipping: Cancellation cooldown", minutes_since=minutes_since,
                        minutes_remaining=minutes_remaining, reason="prevents_shifting_trades")
            return True

        return False

    def _is_direction_flip_cooldown(self, current_direction: str) -> bool:
        """Prevent rapid direction flips between CALL and PUT."""
        if not self.last_suggestion_time or not self.last_suggested_direction:
            return False

        # Same direction is always OK
        if current_direction == self.last_suggested_direction:
            return False

        # Check cooldown for opposite direction
        time_since_last = datetime.now() - self.last_suggestion_time
        cooldown_seconds = self.direction_flip_cooldown_minutes * 60

        if time_since_last.total_seconds() < cooldown_seconds:
            minutes_since = int(time_since_last.total_seconds() / 60)
            log.warning("Skipping: Direction flip cooldown", minutes_since=minutes_since,
                        required_minutes=self.direction_flip_cooldown_minutes)
            return True

        return False

    def should_create_new_setup(self, analysis: dict, price_history: List[dict] = None) -> bool:
        """
        Check if conditions are met to create a new trade setup.

        Rules:
        - Within allowed trading hours (9:30 AM - 3:15 PM)
        - No existing PENDING or ACTIVE setup
        - Self-learner says trading is allowed
        - Signal confidence in optimal range (60-85%)
        - Not in cooldown period after last trade
        - No direction flip cooldown (prevents CALL↔PUT oscillation)
        - Move hasn't already happened in signal direction
        - No bounce in progress (for PUT trades)
        - Trade setup exists in analysis

        Args:
            analysis: Current analysis dict from OI analyzer
            price_history: Recent price history for timing checks

        Returns:
            True if new setup should be created
        """
        # 0. Check trading hours window (9:30 AM - 3:15 PM)
        current_time = datetime.now().time()
        if current_time < TRADE_SETUP_START or current_time > TRADE_SETUP_END:
            return False

        # 1. Check if there's already an active setup
        existing = get_active_trade_setup()
        if existing:
            return False

        # 2. Check if self-learner says to pause trading
        if not self.self_learner.signal_tracker.ema_tracker.should_trade():
            # Cancel any active trade setup since we're paused
            active_setup = get_active_trade_setup()
            if active_setup:
                update_trade_setup_status(
                    active_setup["id"],
                    status="CANCELLED",
                    resolved_at=datetime.now()
                )
                # CRITICAL: Set cancellation cooldown
                self.last_cancelled_time = datetime.now()
                log.warning("Cancelled active setup due to self-learner pause",
                            setup_id=active_setup['id'], cooldown_minutes=30)
            else:
                log.warning("Skipping: Self-learner paused trading")
            return False

        # 3. Check confidence using LEARNED thresholds (dynamic, not hardcoded)
        confidence = analysis.get("signal_confidence", 0)
        verdict = analysis.get("verdict", "")

        # Use unified self-learner check (confidence range + verdict filter)
        should_trade, reason = self.self_learner.should_trade(confidence, verdict)
        if not should_trade:
            log.warning("Skipping trade", reason=reason)
            return False

        # Additional explicit confidence checks as fallback
        if confidence < self.confidence_threshold:
            log.warning("Skipping: Confidence below min threshold",
                        confidence=f"{confidence:.0f}%", min_threshold=f"{self.confidence_threshold:.0f}%")
            return False
        if confidence > self.confidence_max:
            log.warning("Skipping: Confidence above max threshold",
                        confidence=f"{confidence:.0f}%", max_threshold=f"{self.confidence_max:.0f}%")
            return False

        # Check learned exclusion zones
        for low, high in self.confidence_exclude_ranges:
            if low <= confidence < high:
                log.warning("Skipping: Confidence in exclude zone",
                            confidence=f"{confidence:.0f}%", exclude_zone=f"[{low:.0f}-{high:.0f}%]")
                return False

        # 4. Check cooldown period after last resolved trade
        if self._is_in_cooldown():
            log.warning("Skipping: In cooldown period after recent trade")
            return False

        # 4a. CRITICAL: Check cancellation cooldown (prevents shifting trades)
        # After cancelling a trade, wait 30 minutes before creating new one
        # This protects users who placed limit orders based on cancelled trade
        if self._is_in_cancellation_cooldown():
            return False

        # 4b. Check direction flip cooldown (prevents rapid CALL↔PUT switching)
        trade_setup = analysis.get("trade_setup")
        if trade_setup:
            current_direction = trade_setup.get("direction")
            if self._is_direction_flip_cooldown(current_direction):
                return False

        # 5. Check if move already happened in signal direction
        if price_history and self._is_move_already_happened(analysis, price_history):
            log.warning("Skipping: Move already happened in signal direction")
            return False

        # 6. Check for bounce in progress (bad for PUT trades)
        if price_history and self._is_bounce_in_progress(analysis, price_history):
            log.warning("Skipping: Bounce in progress - bad for PUT entry")
            return False

        # 7. Check if trade setup was generated
        trade_setup = analysis.get("trade_setup")
        if not trade_setup:
            return False

        # 8. VERDICT FILTER: Use LEARNED verdict accuracy (dynamic, not hardcoded)
        # The self-learner's should_trade() already checked verdict via VerdictAnalyzer
        # This section provides additional granular control

        # Check if this specific verdict should be skipped based on learned performance
        skip_verdict, verdict_reason = self.self_learner.verdict_analyzer.should_skip_verdict(verdict)
        if skip_verdict:
            log.warning("Skipping trade", reason=verdict_reason)
            return False

        # NOTE: The hardcoded PUT disable and "slightly" filters have been REMOVED
        # The system now LEARNS which verdicts work and which don't
        # If PUT trades are bad, VerdictAnalyzer will learn this and skip them
        # If "strongly" verdicts are bad, VerdictAnalyzer will learn this too

        # 9. MARKET REGIME FILTER: Adjust requirements based on market conditions
        market_regime = analysis.get("market_regime", {})
        regime = market_regime.get("regime", "range_bound")

        if regime == "range_bound":
            # In sideways markets, OTM options have poor performance - need higher delta
            if trade_setup.get("moneyness") == "OTM":
                log.warning("Skipping: OTM trade in sideways market has poor win rate")
                return False
            # Tighter SL required in sideways (max 15%)
            if trade_setup.get("risk_pct", 20) > 15:
                log.warning("Skipping: SL too wide for sideways market",
                            risk_pct=f"{trade_setup.get('risk_pct')}%", max_allowed="15%")
                return False

        # 10. SYMMETRIC CONFIRMATION FOR BOTH DIRECTIONS
        direction = trade_setup.get("direction")
        confirmation_status = analysis.get("confirmation_status", "")

        # Both CALL and PUT need CONFIRMED status
        if confirmation_status not in ["CONFIRMED", "REVERSAL_ALERT"]:
            log.warning("Skipping: Needs confirmation", current_status=confirmation_status)
            return False

        # Both need regime alignment
        if direction == "BUY_CALL" and regime != "trending_up":
            log.warning("Skipping CALL: Needs trending_up", current_regime=regime)
            return False
        elif direction == "BUY_PUT" and regime != "trending_down":
            log.warning("Skipping PUT: Needs trending_down", current_regime=regime)
            return False

        # 11. MULTI-FACTOR CONFIRMATION (require 3 out of 4 aligned signals)
        REQUIRED_CONFIRMATIONS = 3
        confirmations = self._count_confirmations(analysis)
        if confirmations < REQUIRED_CONFIRMATIONS:
            log.warning("Skipping: Insufficient confirmations",
                        confirmations=confirmations, required=REQUIRED_CONFIRMATIONS)
            return False

        return True

    def _is_in_cooldown(self) -> bool:
        """
        Check if we're in cooldown period after last resolved trade.

        Returns:
            True if still in cooldown (should skip trade creation)
        """
        last_trade = get_last_resolved_trade()
        if not last_trade:
            return False

        resolved_at_str = last_trade.get("resolved_at")
        if not resolved_at_str:
            return False

        try:
            resolved_at = datetime.fromisoformat(resolved_at_str)
        except (ValueError, TypeError):
            return False

        time_since_resolution = datetime.now() - resolved_at

        if time_since_resolution < timedelta(minutes=self.cooldown_minutes):
            return True

        return False

    def _is_move_already_happened(self, analysis: dict, price_history: List[dict]) -> bool:
        """
        Check if spot already moved significantly in signal direction.

        If bullish signal but price already up 0.5%+, or bearish signal
        but price already down 0.5%+, the move may be exhausted.

        Args:
            analysis: Current analysis dict
            price_history: Recent price history (oldest first)

        Returns:
            True if move already happened (should skip trade)
        """
        if not price_history or len(price_history) < 2:
            return False

        current_spot = analysis.get("spot_price", 0)
        past_spot = price_history[0].get("spot_price", 0)

        if past_spot <= 0 or current_spot <= 0:
            return False

        move_pct = ((current_spot - past_spot) / past_spot) * 100

        verdict = analysis.get("verdict", "").lower()
        is_bullish = "bull" in verdict

        # If bullish signal but price already moved up significantly
        if is_bullish and move_pct > self.move_threshold_pct:
            return True

        # If bearish signal but price already moved down significantly
        if not is_bullish and move_pct < -self.move_threshold_pct:
            return True

        return False

    def _is_bounce_in_progress(self, analysis: dict, price_history: List[dict]) -> bool:
        """
        Check if price bounced from recent low (bad for PUT trades).

        For bearish/PUT trades, if price has bounced 0.3%+ from the recent
        low within our lookback window, the bounce momentum may hit our SL.

        Args:
            analysis: Current analysis dict
            price_history: Recent price history (oldest first)

        Returns:
            True if bounce in progress (should skip PUT trade)
        """
        if not price_history or len(price_history) < 2:
            return False

        verdict = analysis.get("verdict", "").lower()
        is_bearish = "bear" in verdict

        # Only check for PUT/bearish trades
        if not is_bearish:
            return False

        current_spot = analysis.get("spot_price", 0)
        if current_spot <= 0:
            return False

        # Find recent low from price history
        prices = [p.get("spot_price", 0) for p in price_history if p.get("spot_price", 0) > 0]
        if not prices:
            return False

        recent_low = min(prices)

        if recent_low <= 0:
            return False

        bounce_pct = ((current_spot - recent_low) / recent_low) * 100

        # If bounced significantly from low, skip PUT entry
        if bounce_pct > self.bounce_threshold_pct:
            return True

        return False

    def _generate_trade_reasoning(self, analysis: dict, trade_setup: dict) -> str:
        """
        Generate human-readable summary of why the trade was taken.

        Args:
            analysis: Analysis dict with market context
            trade_setup: Trade setup dict

        Returns:
            Human-readable trade reasoning string
        """
        direction = "BUY PUT" if trade_setup["direction"] == "BUY_PUT" else "BUY CALL"
        verdict = analysis.get("verdict", "Unknown")
        confidence = analysis.get("signal_confidence", 0)
        call_change = analysis.get("call_oi_change", 0)
        put_change = analysis.get("put_oi_change", 0)
        spot = analysis.get("spot_price", 0)
        max_pain = analysis.get("max_pain", 0)
        strike = trade_setup["strike"]
        moneyness = trade_setup["moneyness"]
        risk = trade_setup["risk_pct"]
        iv = trade_setup.get("iv_at_strike", 0)

        # Format OI changes (in lakhs for readability)
        call_change_lakh = call_change / 100000
        put_change_lakh = put_change / 100000

        # Determine spot vs max pain relationship
        spot_vs_mp = "below" if spot < max_pain else "above"

        reasoning = (
            f"{direction}: {verdict} ({confidence:.0f}% confidence). "
            f"Call OI {call_change_lakh:+.1f}L vs Put OI {put_change_lakh:+.1f}L. "
            f"Spot {spot:.0f} {spot_vs_mp} max pain {max_pain}. "
            f"Selected {strike} {trade_setup['option_type']} ({moneyness}) with {risk:.0f}% risk."
        )

        if iv > 0:
            reasoning += f" IV: {iv:.1f}%"

        return reasoning

    def create_setup(self, analysis: dict, timestamp: datetime) -> Optional[int]:
        """
        Create a new PENDING trade setup from analysis.

        Args:
            analysis: Current analysis dict
            timestamp: Current timestamp

        Returns:
            Setup ID if created, None otherwise
        """
        trade_setup = analysis.get("trade_setup")
        if not trade_setup:
            return None

        # Generate trade reasoning
        trade_reasoning = self._generate_trade_reasoning(analysis, trade_setup)

        # Extract OI clusters for support/resistance
        oi_clusters = analysis.get("oi_clusters", {})
        support = oi_clusters.get("strongest_support") or trade_setup.get("support_ref") or 0
        resistance = oi_clusters.get("strongest_resistance") or trade_setup.get("resistance_ref") or 0

        setup_id = save_trade_setup(
            created_at=timestamp,
            direction=trade_setup["direction"],
            strike=trade_setup["strike"],
            option_type=trade_setup["option_type"],
            moneyness=trade_setup["moneyness"],
            entry_premium=trade_setup["entry_premium"],
            sl_premium=trade_setup["sl_premium"],
            target1_premium=trade_setup["target1_premium"],
            target2_premium=trade_setup.get("target2_premium"),
            risk_pct=trade_setup["risk_pct"],
            spot_at_creation=analysis["spot_price"],
            verdict_at_creation=analysis["verdict"],
            signal_confidence=analysis["signal_confidence"],
            iv_at_creation=trade_setup.get("iv_at_strike", 0),
            expiry_date=analysis.get("expiry_date", ""),
            # New technical analysis context
            call_oi_change_at_creation=analysis.get("call_oi_change", 0),
            put_oi_change_at_creation=analysis.get("put_oi_change", 0),
            pcr_at_creation=analysis.get("pcr", 0),
            max_pain_at_creation=analysis.get("max_pain", 0),
            support_at_creation=support,
            resistance_at_creation=resistance,
            trade_reasoning=trade_reasoning
        )

        log.info("Created PENDING setup", setup_id=setup_id, direction=trade_setup['direction'],
                 strike=trade_setup['strike'], option_type=trade_setup['option_type'],
                 entry_premium=trade_setup['entry_premium'])

        # Update tracking for direction flip cooldown
        self.last_suggested_direction = trade_setup["direction"]
        self.last_suggestion_time = timestamp

        return setup_id

    def check_and_update_setup(self, strikes_data: dict, timestamp: datetime) -> Optional[dict]:
        """
        Check and update the status of an active trade setup.

        Handles:
        - PENDING: Check if entry hit -> activate
        - ACTIVE: Check if SL/T1 hit -> resolve

        Args:
            strikes_data: Current option chain data
            timestamp: Current timestamp

        Returns:
            Dict with update info if status changed, None otherwise
        """
        setup = get_active_trade_setup()
        if not setup:
            return None

        strike = setup["strike"]
        option_type = setup["option_type"]

        # Get current premium for this strike
        strike_data = strikes_data.get(strike, {})
        current_premium = strike_data.get(
            "ce_ltp" if option_type == "CE" else "pe_ltp", 0
        )

        if current_premium <= 0:
            return None

        status = setup["status"]

        if status == "PENDING":
            return self._check_pending_activation(setup, current_premium, timestamp)
        elif status == "ACTIVE":
            return self._check_active_resolution(setup, current_premium, timestamp)

        return None

    def _check_pending_activation(self, setup: dict, current_premium: float,
                                   timestamp: datetime) -> Optional[dict]:
        """
        Check if a PENDING setup should be activated.

        For option BUYING, activate when:
        1. Premium is at or below entry (getting same or better price), OR
        2. Premium hasn't moved too far above entry (favorable move, still reasonable to enter)

        This handles both scenarios:
        - Limit order style: wait for premium to drop to entry
        - Market moved favorably: premium went up but trade is still valid
        """
        entry_premium = setup["entry_premium"]

        # Lower threshold: at or below entry (with 2% tolerance for slippage)
        lower_threshold = entry_premium * (1 + self.entry_tolerance)

        # Upper threshold: don't chase if premium moved too far up (10% max above entry)
        upper_threshold = entry_premium * 1.10

        # Activate if premium is within acceptable range
        # Either at/below entry OR hasn't moved too far above entry
        if current_premium <= upper_threshold:
            # Activate the trade
            update_trade_setup_status(
                setup["id"],
                status="ACTIVE",
                activated_at=timestamp,
                activation_premium=current_premium,
                max_premium_reached=current_premium,
                min_premium_reached=current_premium,
                last_checked_at=timestamp,
                last_premium=current_premium
            )

            slippage_pct = ((current_premium - entry_premium) / entry_premium) * 100
            log.info("Setup ACTIVATED", setup_id=setup['id'], current_premium=f"{current_premium:.2f}",
                     entry_premium=f"{entry_premium:.2f}", slippage=f"{slippage_pct:+.1f}%")

            return {
                "setup_id": setup["id"],
                "previous_status": "PENDING",
                "new_status": "ACTIVE",
                "activation_premium": current_premium
            }

        # Premium moved too far above entry - don't chase
        move_pct = ((current_premium - entry_premium) / entry_premium) * 100
        log.debug("Setup NOT activated - premium moved too far", setup_id=setup['id'],
                  current_premium=f"{current_premium:.2f}", entry_premium=f"{entry_premium:.2f}",
                  move=f"{move_pct:+.1f}%", max_allowed="+10%")

        # Update tracking even if not activated
        update_trade_setup_status(
            setup["id"],
            status="PENDING",
            last_checked_at=timestamp,
            last_premium=current_premium
        )

        return None

    def _check_active_resolution(self, setup: dict, current_premium: float,
                                  timestamp: datetime) -> Optional[dict]:
        """
        Check if an ACTIVE setup should be resolved (WON or LOST).

        WON: current_premium >= target1_premium
        LOST: current_premium <= sl_premium
        """
        sl_premium = setup["sl_premium"]
        target1_premium = setup["target1_premium"]
        activation_premium = setup["activation_premium"] or setup["entry_premium"]

        # Track max/min premiums
        max_reached = max(setup.get("max_premium_reached") or current_premium, current_premium)
        min_reached = min(setup.get("min_premium_reached") or current_premium, current_premium)

        # Check for stop loss hit
        if current_premium <= sl_premium:
            profit_loss_pct = ((current_premium - activation_premium) / activation_premium) * 100
            profit_loss_points = current_premium - activation_premium

            update_trade_setup_status(
                setup["id"],
                status="LOST",
                resolved_at=timestamp,
                exit_premium=current_premium,
                hit_sl=True,
                hit_target=False,
                profit_loss_pct=profit_loss_pct,
                profit_loss_points=profit_loss_points,
                max_premium_reached=max_reached,
                min_premium_reached=min_reached,
                last_checked_at=timestamp,
                last_premium=current_premium
            )

            log.warning("Setup LOST - SL hit", setup_id=setup['id'],
                        exit_premium=f"{current_premium:.2f}", pnl=f"{profit_loss_pct:.1f}%")

            return {
                "setup_id": setup["id"],
                "previous_status": "ACTIVE",
                "new_status": "LOST",
                "exit_premium": current_premium,
                "profit_loss_pct": profit_loss_pct
            }

        # Check for target hit
        if current_premium >= target1_premium:
            profit_loss_pct = ((current_premium - activation_premium) / activation_premium) * 100
            profit_loss_points = current_premium - activation_premium

            update_trade_setup_status(
                setup["id"],
                status="WON",
                resolved_at=timestamp,
                exit_premium=current_premium,
                hit_sl=False,
                hit_target=True,
                profit_loss_pct=profit_loss_pct,
                profit_loss_points=profit_loss_points,
                max_premium_reached=max_reached,
                min_premium_reached=min_reached,
                last_checked_at=timestamp,
                last_premium=current_premium
            )

            log.info("Setup WON - Target hit", setup_id=setup['id'],
                     exit_premium=f"{current_premium:.2f}", pnl=f"{profit_loss_pct:.1f}%")

            return {
                "setup_id": setup["id"],
                "previous_status": "ACTIVE",
                "new_status": "WON",
                "exit_premium": current_premium,
                "profit_loss_pct": profit_loss_pct
            }

        # Update tracking stats
        update_trade_setup_status(
            setup["id"],
            status="ACTIVE",
            max_premium_reached=max_reached,
            min_premium_reached=min_reached,
            last_checked_at=timestamp,
            last_premium=current_premium
        )

        return None

    def cancel_on_direction_change(self, current_verdict: str, timestamp: datetime) -> bool:
        """
        Cancel a PENDING setup if the OI direction has flipped.

        Only cancels PENDING setups, not ACTIVE ones.

        Args:
            current_verdict: Current OI verdict
            timestamp: Current timestamp

        Returns:
            True if a setup was cancelled
        """
        setup = get_active_trade_setup()
        if not setup or setup["status"] != "PENDING":
            return False

        # Determine if direction has changed
        setup_direction = setup["direction"]  # BUY_CALL or BUY_PUT
        setup_is_bullish = setup_direction == "BUY_CALL"

        current_is_bullish = "bull" in current_verdict.lower()
        current_is_bearish = "bear" in current_verdict.lower()

        # Check for direction flip
        direction_flipped = (
            (setup_is_bullish and current_is_bearish) or
            (not setup_is_bullish and current_is_bullish)
        )

        if direction_flipped:
            update_trade_setup_status(
                setup["id"],
                status="CANCELLED",
                resolved_at=timestamp
            )

            # CRITICAL: Set cancellation cooldown to prevent immediate recreation with different prices
            self.last_cancelled_time = timestamp

            log.warning("Setup CANCELLED - Direction flipped", setup_id=setup['id'],
                        from_direction='BULLISH' if setup_is_bullish else 'BEARISH',
                        to_direction='BULLISH' if current_is_bullish else 'BEARISH',
                        cooldown_minutes=30)

            return True

        return False

    def expire_pending_setups(self, timestamp: datetime) -> bool:
        """
        Expire PENDING setups at market close.

        Args:
            timestamp: Current timestamp

        Returns:
            True if a setup was expired
        """
        # Check if it's near market close
        current_time = timestamp.time()
        if current_time < MARKET_CLOSE:
            return False

        setup = get_active_trade_setup()
        if not setup or setup["status"] != "PENDING":
            return False

        update_trade_setup_status(
            setup["id"],
            status="EXPIRED",
            resolved_at=timestamp
        )

        log.info("Setup EXPIRED at market close", setup_id=setup['id'])

        return True

    def force_close_active_trades(self, timestamp: datetime, strikes_data: dict) -> bool:
        """
        Force close ACTIVE trades at market close (3:20 PM).

        Args:
            timestamp: Current timestamp
            strikes_data: Current strikes data for premium lookup

        Returns:
            True if a trade was force-closed
        """
        current_time = timestamp.time()
        if current_time < FORCE_CLOSE_TIME:
            return False

        setup = get_active_trade_setup()
        if not setup or setup["status"] != "ACTIVE":
            return False

        # Get current premium for P/L calculation
        current_premium = self._get_current_premium(setup, strikes_data)
        activation_premium = setup.get("activation_premium", setup["entry_premium"])

        if activation_premium and activation_premium > 0:
            profit_loss_pct = ((current_premium - activation_premium) / activation_premium) * 100
            profit_loss_points = current_premium - activation_premium
        else:
            profit_loss_pct = 0
            profit_loss_points = 0

        # Determine if it's a win or loss
        status = "WON" if profit_loss_pct > 0 else "LOST"

        update_trade_setup_status(
            setup["id"],
            status=status,
            resolved_at=timestamp,
            exit_premium=current_premium,
            profit_loss_pct=profit_loss_pct,
            profit_loss_points=profit_loss_points
        )

        log.info("Setup FORCE CLOSED at market end", setup_id=setup['id'],
                 status=status, pnl=f"{profit_loss_pct:+.2f}%")

        return True

    def _get_current_premium(self, setup: dict, strikes_data: dict) -> float:
        """Get current premium for a setup from strikes data."""
        strike = setup["strike"]
        option_type = setup.get("option_type", "CE" if setup["direction"] == "BUY_CALL" else "PE")
        strike_data = strikes_data.get(strike, {})
        return strike_data.get("ce_ltp" if option_type == "CE" else "pe_ltp", 0)

    def get_stats(self) -> dict:
        """
        Get trade setup statistics for dashboard display.

        Returns:
            Dict with win rate stats and current setup info
        """
        stats = get_trade_setup_stats(lookback_days=30)
        active_setup = get_active_trade_setup()

        return {
            "stats": stats,
            "has_active_setup": active_setup is not None,
            "active_setup": active_setup
        }

    def get_active_setup_with_pnl(self, strikes_data: dict) -> Optional[dict]:
        """
        Get active setup with current P/L calculated.

        Args:
            strikes_data: Current option chain data

        Returns:
            Setup dict with live P/L fields, or None
        """
        setup = get_active_trade_setup()
        if not setup:
            return None

        # Get current premium
        strike = setup["strike"]
        option_type = setup["option_type"]
        strike_data = strikes_data.get(strike, {})
        current_premium = strike_data.get(
            "ce_ltp" if option_type == "CE" else "pe_ltp", 0
        )

        # Calculate live P/L
        if setup["status"] == "ACTIVE" and setup.get("activation_premium"):
            activation_premium = setup["activation_premium"]
            live_pnl_pct = ((current_premium - activation_premium) / activation_premium) * 100
            live_pnl_points = current_premium - activation_premium
        elif setup["status"] == "PENDING":
            # For pending, show distance from entry
            entry_premium = setup["entry_premium"]
            live_pnl_pct = ((current_premium - entry_premium) / entry_premium) * 100
            live_pnl_points = current_premium - entry_premium
        else:
            live_pnl_pct = 0
            live_pnl_points = 0

        return {
            **setup,
            "current_premium": round(current_premium, 2),
            "live_pnl_pct": round(live_pnl_pct, 2),
            "live_pnl_points": round(live_pnl_points, 2),
            # Map database field names to frontend expected names
            "support_ref": setup.get("support_at_creation"),
            "resistance_ref": setup.get("resistance_at_creation"),
            "max_pain": setup.get("max_pain_at_creation"),
        }


# Singleton instance
_trade_tracker = None


def get_trade_tracker() -> TradeTracker:
    """Get or create the singleton TradeTracker instance."""
    global _trade_tracker
    if _trade_tracker is None:
        _trade_tracker = TradeTracker()
    return _trade_tracker


if __name__ == "__main__":
    # Test the trade tracker
    log.info("Testing Trade Tracker")

    tracker = get_trade_tracker()

    # Check stats
    stats = tracker.get_stats()
    log.info("Current stats", stats=stats)

    # Check for active setup
    active = get_active_trade_setup()
    log.info("Active setup", setup=active)
