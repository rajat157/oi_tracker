"""
Momentum strategy — High-conviction trend-following (1:2 RR).

Entry: "Bulls/Bears Winning/Strongly Winning" verdict, confidence >= 85%,
       CONFIRMED status, 12:00-14:00 IST.
SL: -25% | Target: +50% (1:2 RR) | EOD: 15:20.
One trade per day.
"""

from __future__ import annotations

from datetime import datetime, time

from app.core.constants import (
    FORCE_CLOSE_TIME,
    MOMENTUM_MIN_CONFIDENCE,
    NIFTY_STEP,
)
from app.strategies.base import TradingStrategy

MOMENTUM_TIME_START = time(12, 0)
MOMENTUM_TIME_END = time(14, 0)
MOMENTUM_SL_PCT = 25.0
MOMENTUM_TARGET_PCT = 50.0
MOMENTUM_MIN_PREMIUM = 5.0

BEARISH_VERDICTS = ("Bears Winning", "Bears Strongly Winning")
BULLISH_VERDICTS = ("Bulls Winning", "Bulls Strongly Winning")


class MomentumStrategy(TradingStrategy):
    """High-conviction trend-following strategy."""

    @staticmethod
    def _get_confirmation(analysis: dict) -> str:
        """Extract confirmation_status from analysis blob or dict."""
        blob = analysis.get("analysis_blob") or analysis.get("analysis_json")
        if isinstance(blob, str):
            import json

            try:
                blob = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                blob = {}
        if isinstance(blob, dict):
            val = blob.get("confirmation_status", "")
            if val:
                return val
        return analysis.get("confirmation_status", "")

    def should_enter(self, analysis: dict, strikes_data: dict, **kwargs) -> dict | None:
        already_traded_today: bool = kwargs.get("already_traded_today", False)
        has_active_trade: bool = kwargs.get("has_active_trade", False)

        if already_traded_today or has_active_trade:
            return None

        now_time = datetime.now().time()
        if now_time < MOMENTUM_TIME_START or now_time > MOMENTUM_TIME_END:
            return None

        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0) or 0

        if confidence < MOMENTUM_MIN_CONFIDENCE:
            return None

        confirmation = self._get_confirmation(analysis)
        if confirmation != "CONFIRMED":
            return None

        # Direction
        if verdict in BEARISH_VERDICTS:
            direction = "BUY_PUT"
            option_type = "PE"
        elif verdict in BULLISH_VERDICTS:
            direction = "BUY_CALL"
            option_type = "CE"
        else:
            return None

        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return None

        strike = round(spot / NIFTY_STEP) * NIFTY_STEP
        strike_data = strikes_data.get(strike, {})
        entry_premium = strike_data.get(
            "pe_ltp" if option_type == "PE" else "ce_ltp", 0
        )

        if not entry_premium or entry_premium < MOMENTUM_MIN_PREMIUM:
            return None

        sl_premium = round(entry_premium * (1 - MOMENTUM_SL_PCT / 100), 2)
        target_premium = round(entry_premium * (1 + MOMENTUM_TARGET_PCT / 100), 2)

        # Extract combined_score
        blob = analysis.get("analysis_blob") or {}
        if isinstance(blob, str):
            import json

            try:
                blob = json.loads(blob)
            except Exception:
                blob = {}
        combined_score = blob.get("combined_score") or analysis.get("combined_score")

        return {
            "strategy_name": "Momentum",
            "direction": direction,
            "strike": strike,
            "option_type": option_type,
            "entry_premium": entry_premium,
            "sl_premium": sl_premium,
            "target_premium": target_premium,
            "spot_at_creation": spot,
            "verdict_at_creation": verdict,
            "signal_confidence": confidence,
            "iv_skew_at_creation": analysis.get("iv_skew", 0),
            "vix_at_creation": analysis.get("vix", 0),
            "combined_score": combined_score,
            "confirmation_status": confirmation,
            "status": "ACTIVE",
        }

    def check_exit(
        self,
        trade: dict,
        current_premium: float,
        now: datetime,
    ) -> dict | None:
        if trade["status"] != "ACTIVE":
            return None

        entry = trade["entry_premium"]
        max_p = max(trade.get("max_premium_reached") or entry, current_premium)
        min_p = min(trade.get("min_premium_reached") or entry, current_premium)

        # SL
        if current_premium <= trade["sl_premium"]:
            pnl = self.compute_pnl_pct(entry, current_premium)
            return {
                "status": "LOST",
                "resolved_at": now,
                "exit_premium": current_premium,
                "exit_reason": "SL",
                "profit_loss_pct": pnl,
                "max_premium_reached": max_p,
                "min_premium_reached": min_p,
            }

        # Target
        if current_premium >= trade["target_premium"]:
            pnl = self.compute_pnl_pct(entry, current_premium)
            return {
                "status": "WON",
                "resolved_at": now,
                "exit_premium": current_premium,
                "exit_reason": "TARGET",
                "profit_loss_pct": pnl,
                "max_premium_reached": max_p,
                "min_premium_reached": min_p,
            }

        return {
            "status": "ACTIVE",
            "max_premium_reached": max_p,
            "min_premium_reached": min_p,
        }

    def should_force_close(self, trade: dict, now: datetime) -> bool:
        return now.time() >= FORCE_CLOSE_TIME and trade["status"] == "ACTIVE"

    def force_close(self, trade: dict, current_premium: float, now: datetime) -> dict:
        entry = trade["entry_premium"]
        pnl = self.compute_pnl_pct(entry, current_premium)
        return {
            "status": "WON" if pnl > 0 else "LOST",
            "resolved_at": now,
            "exit_premium": current_premium,
            "exit_reason": "EOD",
            "profit_loss_pct": pnl,
        }
