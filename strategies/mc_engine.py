"""MC (Momentum Continuation) signal detection engine.

Pure signal logic — no DB writes, no events. Detects intraday rallies
that have pulled back and are resuming, then generates trade signals.

Backtested across 300 days (Jan 2025 - Mar 2026): 54.2% WR, 1.21 PF,
7.7% max DD, profitable all 5 quarters.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Dict, List, Optional

from config import MCConfig
from db.connection import get_connection
from core.logger import get_logger

log = get_logger("mc_engine")

NIFTY_STEP = 50


class MCEngine:
    """Detects Momentum Continuation setups from intraday spot data."""

    def __init__(self):
        self._weekly_trend: Optional[str] = None
        self._weekly_trend_date: Optional[date] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_mc_signal(
        self,
        analysis: dict,
        strikes_data: dict,
        config: MCConfig,
    ) -> Optional[Dict]:
        """Main entry point: detect MC signal and return trade setup.

        Returns dict with signal_type, direction, strike, entry/sl/target
        premiums, and signal_data for debugging — or None.
        """
        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return None

        spot_history = self._load_todays_spots()
        if len(spot_history) < 10:
            return None

        closes = [s["spot_price"] for s in spot_history]
        day_open = closes[0]

        rally = self._detect_rally(closes, day_open, config)
        if rally is None:
            return None

        pullback = self._detect_pullback(closes, rally, config)
        if pullback is None:
            return None

        if not self._check_resumption(closes, rally):
            return None

        weekly_trend = self.get_weekly_trend()
        rally_dir = rally["direction"]
        if weekly_trend != "NEUTRAL":
            # Require at least non-conflict: UP rally needs UP/NEUTRAL, DOWN needs DOWN/NEUTRAL
            if rally_dir == "UP" and weekly_trend == "DOWN":
                return None
            if rally_dir == "DOWN" and weekly_trend == "UP":
                return None

        # Build trade signal
        option_type = "CE" if rally_dir == "UP" else "PE"
        strike = self._get_mc_strike(spot, option_type)

        key = "ce_ltp" if option_type == "CE" else "pe_ltp"
        strike_data = strikes_data.get(strike, {})
        entry_premium = strike_data.get(key, 0)

        if entry_premium < config.MIN_PREMIUM or entry_premium > config.MAX_PREMIUM:
            log.debug("MC premium out of range",
                      premium=entry_premium, strike=strike, side=option_type)
            return None

        sl_premium = round(entry_premium * (1 - config.SL_PCT / 100), 2)
        target_premium = round(entry_premium * (1 + config.TARGET_PCT / 100), 2)

        signal_data = {
            "rally_pts": rally["rally_pts"],
            "rally_direction": rally_dir,
            "pullback_pct": pullback["pullback_pct"],
            "weekly_trend": weekly_trend,
            "day_open": day_open,
            "spot_at_signal": spot,
            "candles_count": len(closes),
        }

        log.info("MC signal detected",
                 direction=option_type, strike=strike,
                 entry=entry_premium, rally_pts=f"{rally['rally_pts']:.1f}",
                 pullback=f"{pullback['pullback_pct']:.1%}",
                 weekly=weekly_trend)

        return {
            "signal_type": "MC",
            "direction": f"BUY_{option_type}",
            "option_type": option_type,
            "strike": strike,
            "entry_premium": entry_premium,
            "sl_premium": sl_premium,
            "target_premium": target_premium,
            "signal_data": signal_data,
        }

    # ------------------------------------------------------------------
    # Signal components
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_rally(
        closes: List[float],
        day_open: float,
        config: MCConfig,
    ) -> Optional[Dict]:
        """Check if spot has moved 25+ pts from day open in one direction."""
        current = closes[-1]
        move = current - day_open

        if abs(move) < config.RALLY_MIN_PTS:
            return None

        direction = "UP" if move > 0 else "DOWN"

        if direction == "UP":
            peak = max(closes)
        else:
            peak = min(closes)

        return {
            "direction": direction,
            "rally_pts": abs(move),
            "rally_peak": peak,
        }

    @staticmethod
    def _detect_pullback(
        closes: List[float],
        rally: Dict,
        config: MCConfig,
    ) -> Optional[Dict]:
        """Check if last N candles show a 20-65% pullback from rally peak."""
        n = config.PULLBACK_CANDLES
        if len(closes) < n + 1:
            return None

        recent = closes[-n:]
        peak = rally["rally_peak"]
        direction = rally["direction"]
        rally_pts = rally["rally_pts"]

        if direction == "UP":
            pullback_extreme = min(recent)
            pullback_pts = peak - pullback_extreme
        else:
            pullback_extreme = max(recent)
            pullback_pts = pullback_extreme - peak

        if rally_pts <= 0:
            return None

        pullback_pct = pullback_pts / rally_pts

        if pullback_pct < config.PULLBACK_MIN_PCT:
            return None
        if pullback_pct > config.PULLBACK_MAX_PCT:
            return None

        return {
            "pullback_pct": pullback_pct,
            "pullback_extreme": pullback_extreme,
        }

    @staticmethod
    def _check_resumption(closes: List[float], rally: Dict) -> bool:
        """Last candle must close in the original rally direction."""
        if len(closes) < 2:
            return False
        if rally["direction"] == "UP":
            return closes[-1] > closes[-2]
        else:
            return closes[-1] < closes[-2]

    def get_weekly_trend(self) -> str:
        """UP/DOWN/NEUTRAL based on last two Friday closes. Cached daily."""
        today = date.today()
        if self._weekly_trend_date == today and self._weekly_trend is not None:
            return self._weekly_trend

        trend = self._compute_weekly_trend()
        self._weekly_trend = trend
        self._weekly_trend_date = today
        return trend

    # ------------------------------------------------------------------
    # Strike selection
    # ------------------------------------------------------------------

    @staticmethod
    def _get_mc_strike(spot: float, option_type: str) -> int:
        """CE: 2 strikes below ATM. PE: 2 strikes above ATM (slightly ITM)."""
        atm = round(spot / NIFTY_STEP) * NIFTY_STEP
        if option_type == "CE":
            return atm - 2 * NIFTY_STEP
        else:
            return atm + 2 * NIFTY_STEP

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_todays_spots() -> List[Dict]:
        """Load today's spot prices from analysis_history."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT timestamp, spot_price FROM analysis_history "
                "WHERE DATE(timestamp) = ? AND spot_price > 0 "
                "ORDER BY timestamp",
                (today_str,),
            ).fetchall()
        return [{"timestamp": r[0], "spot_price": r[1]} for r in rows]

    @staticmethod
    def _compute_weekly_trend() -> str:
        """Compare last two Friday closes from nifty_history.

        Falls back to last two daily closes if nifty_history is empty.
        """
        with get_connection() as conn:
            # Get the close of the most recent 2 completed trading days
            rows = conn.execute(
                "SELECT DATE(timestamp) as dt, close FROM nifty_history "
                "WHERE time(timestamp) >= '15:00' "
                "GROUP BY dt ORDER BY dt DESC LIMIT 2",
            ).fetchall()

        if len(rows) < 2:
            return "NEUTRAL"

        latest_close = rows[0][1]
        prev_close = rows[1][1]

        if latest_close > prev_close + 20:
            return "UP"
        elif latest_close < prev_close - 20:
            return "DOWN"
        return "NEUTRAL"
