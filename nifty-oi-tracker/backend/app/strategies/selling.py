"""
Selling strategy — Options SELLING (dual T1/T2).

Entry: Slightly Bullish/Bearish verdict, confidence >= 65%, 11:00-14:00 IST.
SL: +25% premium rise | T1: -25% drop (notify) | T2: -50% drop (auto-exit) | EOD: 15:20.
One trade per day, OTM-1 strike.
"""

from __future__ import annotations

from datetime import datetime

from app.core.constants import (
    FORCE_CLOSE_TIME,
    NIFTY_STEP,
    SELLING_MIN_CONFIDENCE,
    SELLING_MIN_PREMIUM,
    SELLING_OTM_OFFSET,
    SELLING_SL_PCT,
    SELLING_TARGET1_PCT,
    SELLING_TARGET2_PCT,
    SELLING_TIME_END,
    SELLING_TIME_START,
)
from app.strategies.base import TradingStrategy


def _get_otm_strike(spot_price: float, direction: str) -> int:
    """Get OTM-1 strike for selling."""
    atm = round(spot_price / NIFTY_STEP) * NIFTY_STEP
    if direction == "SELL_PUT":
        return atm - (NIFTY_STEP * SELLING_OTM_OFFSET)
    else:  # SELL_CALL
        return atm + (NIFTY_STEP * SELLING_OTM_OFFSET)


class SellingStrategy(TradingStrategy):
    """Options selling — dual T1/T2 with EOD exit."""

    def should_enter(self, analysis: dict, strikes_data: dict, **kwargs) -> dict | None:
        already_traded_today: bool = kwargs.get("already_traded_today", False)
        has_active_trade: bool = kwargs.get("has_active_trade", False)

        if already_traded_today or has_active_trade:
            return None

        now_time = datetime.now().time()
        if now_time < SELLING_TIME_START or now_time > SELLING_TIME_END:
            return None

        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)

        if "Slightly" not in verdict:
            return None
        if confidence < SELLING_MIN_CONFIDENCE:
            return None

        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return None

        # Direction
        if "Bullish" in verdict:
            direction = "SELL_PUT"
            option_type = "PE"
        else:
            direction = "SELL_CALL"
            option_type = "CE"

        strike = _get_otm_strike(spot, direction)

        # Get premium
        strike_data = strikes_data.get(strike, {})
        entry_premium = strike_data.get(
            "pe_ltp" if option_type == "PE" else "ce_ltp", 0
        )
        if not entry_premium or entry_premium < SELLING_MIN_PREMIUM:
            return None

        iv = strike_data.get("pe_iv" if option_type == "PE" else "ce_iv", 0)

        # Selling targets (inverted: SL = premium rises, target = premium drops)
        sl_premium = round(entry_premium * (1 + SELLING_SL_PCT / 100), 2)
        target_premium = round(entry_premium * (1 - SELLING_TARGET1_PCT / 100), 2)
        target2_premium = round(entry_premium * (1 - SELLING_TARGET2_PCT / 100), 2)

        return {
            "direction": direction,
            "strike": strike,
            "option_type": option_type,
            "entry_premium": entry_premium,
            "sl_premium": sl_premium,
            "target_premium": target_premium,
            "target2_premium": target2_premium,
            "spot_at_creation": spot,
            "verdict_at_creation": verdict,
            "signal_confidence": confidence,
            "iv_at_creation": iv,
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

        # SL hit (premium RISES for seller = loss)
        if current_premium >= trade["sl_premium"]:
            pnl = self.compute_pnl_pct(entry, current_premium, is_selling=True)
            return {
                "status": "LOST",
                "resolved_at": now,
                "exit_premium": current_premium,
                "exit_reason": "SL",
                "profit_loss_pct": pnl,
                "max_premium_reached": max_p,
                "min_premium_reached": min_p,
            }

        # T1 hit (premium drops 25%) — notify but stay active
        t1_hit = bool(trade.get("t1_hit"))
        t1_update = {}
        if not t1_hit and current_premium <= trade["target_premium"]:
            t1_update = {"t1_hit": True, "t1_hit_at": now, "_event": "T1_HIT"}

        # T2 hit (premium drops 50%) — auto-exit
        t2 = trade.get("target2_premium") or trade["target_premium"]
        if current_premium <= t2:
            pnl = self.compute_pnl_pct(entry, current_premium, is_selling=True)
            return {
                "status": "WON",
                "resolved_at": now,
                "exit_premium": current_premium,
                "exit_reason": "TARGET2",
                "profit_loss_pct": pnl,
                "max_premium_reached": max_p,
                "min_premium_reached": min_p,
                **t1_update,
            }

        # Tracking update (with possible T1)
        return {
            "status": "ACTIVE",
            "max_premium_reached": max_p,
            "min_premium_reached": min_p,
            **t1_update,
        }

    def should_force_close(self, trade: dict, now: datetime) -> bool:
        return now.time() >= FORCE_CLOSE_TIME and trade["status"] == "ACTIVE"

    def force_close(self, trade: dict, current_premium: float, now: datetime) -> dict:
        entry = trade["entry_premium"]
        pnl = self.compute_pnl_pct(entry, current_premium, is_selling=True)
        return {
            "status": "WON" if pnl > 0 else "LOST",
            "resolved_at": now,
            "exit_premium": current_premium,
            "exit_reason": "EOD",
            "profit_loss_pct": pnl,
        }
