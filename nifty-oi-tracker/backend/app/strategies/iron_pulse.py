"""
Iron Pulse strategy — Options BUYING (1:1 RR with trailing SL after T1).

Entry: Slightly Bullish/Bearish verdict, confidence >= 65%, 11:00-14:00 IST.
SL: -20% from entry | T1: +22% (notify, start trailing) | Trailing SL: 15% below peak.
One trade per day.
"""

from __future__ import annotations

from datetime import datetime, time

from app.core.constants import (
    FORCE_CLOSE_TIME,
    IRON_PULSE_MIN_CONFIDENCE,
    IRON_PULSE_SL_PCT,
    IRON_PULSE_TARGET_PCT,
    IRON_PULSE_TIME_END,
    IRON_PULSE_TIME_START,
    IRON_PULSE_TRAILING_SL_PCT,
    NIFTY_STEP,
)
from app.strategies.base import TradingStrategy

MIN_PREMIUM_PCT = 0.20  # Premium must be >= 0.20% of spot
ENTRY_TOLERANCE = 0.02  # 2% slippage tolerance for PENDING activation
MAX_CHASE_PCT = 0.10  # Don't chase premium > 10% above entry


class IronPulseStrategy(TradingStrategy):
    """Iron Pulse — bread-and-butter buying strategy."""

    # ── Entry ──────────────────────────────────────────────

    def should_enter(self, analysis: dict, strikes_data: dict, **kwargs) -> dict | None:
        """
        Evaluate Iron Pulse entry conditions.

        Returns trade params dict or None.
        """
        already_traded_today: bool = kwargs.get("already_traded_today", False)
        has_active_trade: bool = kwargs.get("has_active_trade", False)
        price_history: list[dict] = kwargs.get("price_history", [])

        if already_traded_today or has_active_trade:
            return None

        now_time = datetime.now().time()
        if now_time < IRON_PULSE_TIME_START or now_time > IRON_PULSE_TIME_END:
            return None

        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)

        if "Slightly" not in verdict:
            return None
        if confidence < IRON_PULSE_MIN_CONFIDENCE:
            return None

        trade_setup = analysis.get("trade_setup")
        if not trade_setup:
            return None

        entry_premium = trade_setup.get("entry_premium", 0)
        spot_price = analysis.get("spot_price", 0)

        # Premium % of spot filter
        if spot_price > 0 and entry_premium > 0:
            if (entry_premium / spot_price) * 100 < MIN_PREMIUM_PCT:
                return None

        return {
            "direction": trade_setup["direction"],
            "strike": trade_setup["strike"],
            "option_type": trade_setup["option_type"],
            "moneyness": trade_setup["moneyness"],
            "entry_premium": entry_premium,
            "sl_premium": trade_setup["sl_premium"],
            "target1_premium": trade_setup["target1_premium"],
            "target2_premium": trade_setup.get("target2_premium"),
            "risk_pct": trade_setup["risk_pct"],
            "spot_at_creation": spot_price,
            "verdict_at_creation": verdict,
            "signal_confidence": confidence,
            "iv_at_creation": trade_setup.get("iv_at_strike", 0),
            "expiry_date": kwargs.get("expiry_date", ""),
            "status": "PENDING",
        }

    # ── Pending activation ─────────────────────────────────

    def check_pending_activation(
        self, trade: dict, current_premium: float, now: datetime
    ) -> dict | None:
        """Check if a PENDING trade should be activated."""
        entry = trade["entry_premium"]
        upper_threshold = entry * (1 + MAX_CHASE_PCT)

        if current_premium <= upper_threshold:
            return {
                "status": "ACTIVE",
                "activated_at": now,
                "activation_premium": current_premium,
                "max_premium_reached": current_premium,
                "min_premium_reached": current_premium,
            }
        return None  # Don't chase

    # ── Exit / update ──────────────────────────────────────

    def check_exit(
        self,
        trade: dict,
        current_premium: float,
        now: datetime,
    ) -> dict | None:
        """
        Check SL / T1 / trailing SL for an ACTIVE Iron Pulse trade.

        Returns update dict or None to stay.
        """
        if trade["status"] == "PENDING":
            return self.check_pending_activation(trade, current_premium, now)

        if trade["status"] != "ACTIVE":
            return None

        activation = trade.get("activation_premium") or trade["entry_premium"]
        sl_premium = trade["sl_premium"]
        target1 = trade["target1_premium"]
        t1_hit = bool(trade.get("t1_hit"))

        max_reached = max(trade.get("max_premium_reached") or current_premium, current_premium)
        min_reached = min(trade.get("min_premium_reached") or current_premium, current_premium)
        peak = max(trade.get("peak_premium") or current_premium, current_premium)

        # ── Phase 1: Before T1 ────────────────────────────
        if not t1_hit:
            # SL hit
            if current_premium <= sl_premium:
                pnl = self.compute_pnl_pct(activation, current_premium)
                return {
                    "status": "LOST",
                    "resolved_at": now,
                    "exit_premium": current_premium,
                    "exit_reason": "SL",
                    "hit_sl": True,
                    "profit_loss_pct": pnl,
                    "profit_loss_points": current_premium - activation,
                    "max_premium_reached": max_reached,
                    "min_premium_reached": min_reached,
                }

            # T1 hit — don't close, start trailing
            if current_premium >= target1:
                trailing_sl = peak * (1 - IRON_PULSE_TRAILING_SL_PCT / 100)
                return {
                    "status": "ACTIVE",
                    "t1_hit": True,
                    "t1_hit_at": now,
                    "t1_premium": current_premium,
                    "peak_premium": peak,
                    "trailing_sl": trailing_sl,
                    "hit_target": True,
                    "max_premium_reached": max_reached,
                    "min_premium_reached": min_reached,
                    "_event": "T1_HIT",
                }

            # Just tracking
            return {
                "status": "ACTIVE",
                "peak_premium": peak,
                "max_premium_reached": max_reached,
                "min_premium_reached": min_reached,
            }

        # ── Phase 2: After T1 — trailing SL ──────────────
        trailing_sl = peak * (1 - IRON_PULSE_TRAILING_SL_PCT / 100)

        if current_premium <= trailing_sl:
            pnl = self.compute_pnl_pct(activation, current_premium)
            return {
                "status": "WON" if pnl > 0 else "LOST",
                "resolved_at": now,
                "exit_premium": current_premium,
                "exit_reason": "TRAILING_SL",
                "hit_target": True,
                "profit_loss_pct": pnl,
                "profit_loss_points": current_premium - activation,
                "peak_premium": peak,
                "trailing_sl": trailing_sl,
                "max_premium_reached": max_reached,
                "min_premium_reached": min_reached,
            }

        # Update tracking
        return {
            "status": "ACTIVE",
            "peak_premium": peak,
            "trailing_sl": trailing_sl,
            "max_premium_reached": max_reached,
            "min_premium_reached": min_reached,
        }

    # ── Force close ────────────────────────────────────────

    def should_force_close(self, trade: dict, now: datetime) -> bool:
        return now.time() >= FORCE_CLOSE_TIME and trade["status"] == "ACTIVE"

    def force_close(self, trade: dict, current_premium: float, now: datetime) -> dict:
        """Build force-close update dict."""
        activation = trade.get("activation_premium") or trade["entry_premium"]
        pnl = self.compute_pnl_pct(activation, current_premium)
        return {
            "status": "WON" if pnl > 0 else "LOST",
            "resolved_at": now,
            "exit_premium": current_premium,
            "exit_reason": "EOD",
            "profit_loss_pct": pnl,
            "profit_loss_points": current_premium - activation,
        }
