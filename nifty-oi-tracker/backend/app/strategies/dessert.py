"""
Dessert strategy — Premium 1:2 RR buying (Contra Sniper + Phantom PUT).

Two sub-strategies, first to trigger wins (one per day):
- Contra Sniper: BUY PUT when Bullish verdict + IV Skew < 1 + below max pain.
- Phantom PUT: BUY PUT when confidence < 50% + IV Skew < 0 + spot rising 30m.

SL: -25% | Target: +50% (1:2 RR) | EOD: 15:20.
"""

from __future__ import annotations

from datetime import datetime

from app.core.constants import (
    DESSERT_MIN_PREMIUM,
    DESSERT_SL_PCT,
    DESSERT_TARGET_PCT,
    DESSERT_TIME_END,
    DESSERT_TIME_START,
    FORCE_CLOSE_TIME,
    NIFTY_STEP,
)
from app.strategies.base import TradingStrategy

CONTRA_SNIPER = "Contra Sniper"
PHANTOM_PUT = "Phantom PUT"


class DessertStrategy(TradingStrategy):
    """Premium 1:2 RR buying — two contrarian sub-strategies."""

    def _check_contra_sniper(self, analysis: dict) -> bool:
        verdict = analysis.get("verdict", "")
        iv_skew = analysis.get("iv_skew", 0) or 0
        spot = analysis.get("spot_price", 0)
        max_pain = analysis.get("max_pain", 0) or 0

        if "Bull" not in verdict:
            return False
        if iv_skew >= 1:
            return False
        atm = round(spot / NIFTY_STEP) * NIFTY_STEP
        if max_pain <= 0 or atm >= max_pain:
            return False
        return True

    def _check_phantom_put(self, analysis: dict, spot_move_30m: float | None) -> bool:
        confidence = analysis.get("signal_confidence", 0) or 0
        iv_skew = analysis.get("iv_skew", 0) or 0

        if confidence >= 50:
            return False
        if iv_skew >= 0:
            return False
        if spot_move_30m is None or spot_move_30m <= 0.05:
            return False
        return True

    def should_enter(self, analysis: dict, strikes_data: dict, **kwargs) -> dict | None:
        already_traded_today: bool = kwargs.get("already_traded_today", False)
        has_active_trade: bool = kwargs.get("has_active_trade", False)
        spot_move_30m: float | None = kwargs.get("spot_move_30m")

        if already_traded_today or has_active_trade:
            return None

        now_time = datetime.now().time()
        if now_time < DESSERT_TIME_START or now_time > DESSERT_TIME_END:
            return None

        # Check sub-strategies in priority order
        strategy_name = None
        if self._check_contra_sniper(analysis):
            strategy_name = CONTRA_SNIPER
        elif self._check_phantom_put(analysis, spot_move_30m):
            strategy_name = PHANTOM_PUT
        else:
            return None

        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return None

        # Always BUY PUT at ATM
        strike = round(spot / NIFTY_STEP) * NIFTY_STEP
        strike_data = strikes_data.get(strike, {})
        entry_premium = strike_data.get("pe_ltp", 0)

        if not entry_premium or entry_premium < DESSERT_MIN_PREMIUM:
            return None

        sl_premium = round(entry_premium * (1 - DESSERT_SL_PCT / 100), 2)
        target_premium = round(entry_premium * (1 + DESSERT_TARGET_PCT / 100), 2)

        return {
            "strategy_name": strategy_name,
            "direction": "BUY_PUT",
            "strike": strike,
            "option_type": "PE",
            "entry_premium": entry_premium,
            "sl_premium": sl_premium,
            "target_premium": target_premium,
            "spot_at_creation": spot,
            "verdict_at_creation": analysis.get("verdict", ""),
            "signal_confidence": analysis.get("signal_confidence", 0),
            "iv_skew_at_creation": analysis.get("iv_skew", 0),
            "vix_at_creation": analysis.get("vix", 0),
            "max_pain_at_creation": analysis.get("max_pain", 0),
            "spot_move_30m": spot_move_30m or 0,
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

        # SL (premium drops for buyer = loss)
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

        # Target (premium rises for buyer = win)
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

        # Tracking
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
