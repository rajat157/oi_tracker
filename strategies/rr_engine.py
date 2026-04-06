"""Rally Rider (RR) signal detection engine.

Pure signal logic — no DB writes, no events. Classifies market regime from
recent nifty_history + vix_history, detects 3 signal types (MC/MOM/VWAP),
and selects strikes for the RR strategy.

Backtested across 300 days (Jan 2025 - Mar 2026): 727 trades, 60.2% WR,
1.90 PF, passes WR>51% + PF>1 + DD<Rs10K in ALL 15 months.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Dict, List, Optional

from config import RRConfig, RR_REGIME_PARAMS
from db.connection import get_connection
from core.logger import get_logger

log = get_logger("rr_engine")

NIFTY_STEP = 50


class RREngine:
    """Detects Rally Rider signals and classifies market regime."""

    def __init__(self, fetcher=None):
        """Args:
            fetcher: Optional data fetcher exposing fetch_option_candles(strike, option_type)
                and fetch_nifty_candles(interval). When None, PMOM and NMOM are
                silently disabled (used in unit tests without a live Kite connection).
        """
        self._fetcher = fetcher
        self._regime: Optional[str] = None
        self._regime_date: Optional[date] = None
        self._weekly_trend: Optional[str] = None
        self._weekly_trend_date: Optional[date] = None

    # ------------------------------------------------------------------
    # Tick rounding (second decimal must be multiple of 5)
    # ------------------------------------------------------------------

    @staticmethod
    def round_to_tick(value: float) -> float:
        """Round to nearest 0.05 tick.

        230.03 -> 230.05, 230.07 -> 230.05, 230.12 -> 230.10, 230.18 -> 230.20
        """
        return round(round(value * 20) / 20, 2)

    # ------------------------------------------------------------------
    # Regime classification (cached daily)
    # ------------------------------------------------------------------

    def classify_regime(self, config: RRConfig) -> str:
        """Classify current market regime. Cached for the trading day."""
        today = date.today()
        if self._regime_date == today and self._regime is not None:
            return self._regime

        regime = self._compute_regime(config)
        self._regime = regime
        self._regime_date = today
        log.info("Regime classified", regime=regime)
        return regime

    def _compute_regime(self, config: RRConfig) -> str:
        """Compute regime from nifty_history + vix_history lookback."""
        lookback = config.REGIME_LOOKBACK_DAYS

        with get_connection() as conn:
            # Daily stats from nifty_history
            rows = conn.execute(
                "SELECT DATE(timestamp) as dt, "
                "MAX(high) - MIN(low) as range_pts, "
                "MAX(CASE WHEN time(timestamp) >= '15:00' THEN close END) as day_close "
                "FROM nifty_history "
                "WHERE DATE(timestamp) < DATE('now') "
                "GROUP BY dt ORDER BY dt DESC LIMIT ?",
                (lookback + 1,),
            ).fetchall()

            # Average VIX
            vix_row = conn.execute(
                "SELECT AVG(close) FROM vix_history "
                "WHERE DATE(timestamp) >= DATE('now', ? || ' days') "
                "AND DATE(timestamp) < DATE('now')",
                (f"-{lookback}",),
            ).fetchone()

        if len(rows) < 2:
            return "NORMAL"

        daily_ranges = [r[1] for r in rows if r[1] is not None]
        closes = [r[2] for r in rows if r[2] is not None]
        avg_vix = vix_row[0] if vix_row and vix_row[0] else 13.5

        if len(daily_ranges) < 2 or len(closes) < 2:
            return "NORMAL"

        avg_range = sum(daily_ranges) / len(daily_ranges)
        daily_returns = [closes[i] - closes[i + 1] for i in range(len(closes) - 1)]
        net_return = sum(daily_returns)
        avg_abs_return = sum(abs(r) for r in daily_returns) / len(daily_returns) if daily_returns else 1
        trend_strength = abs(net_return) / (avg_abs_return * len(daily_returns) + 1e-9)

        if avg_vix > 16 or avg_range > 250:
            if net_return > 100:
                return "HIGH_VOL_UP"
            elif net_return < -100:
                return "HIGH_VOL_DOWN"
            return "HIGH_VOL_DOWN"  # default high vol to DOWN (conservative)
        if avg_vix < 12 or avg_range < 120:
            return "LOW_VOL"
        if trend_strength > 0.15 and net_return > 150:
            return "TRENDING_UP"
        if trend_strength > 0.15 and net_return < -150:
            return "TRENDING_DOWN"
        return "NORMAL"

    def get_regime_params(self, regime: str) -> Dict:
        """Get regime-specific parameters. Falls back to NORMAL."""
        return RR_REGIME_PARAMS.get(regime, RR_REGIME_PARAMS["NORMAL"])

    # ------------------------------------------------------------------
    # Signal detection
    # ------------------------------------------------------------------

    def detect_signals(
        self,
        analysis: dict,
        regime_config: Dict,
    ) -> List[Dict]:
        """Detect all RR signals for current market state.

        Returns list of signal dicts: {signal_type, direction, signal_data}
        """
        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return []

        spot_history = self._load_todays_spots()
        if len(spot_history) < 10:
            return []

        closes = [s["spot_price"] for s in spot_history]
        day_open = closes[0]
        allowed_signals = regime_config.get("signals", set())
        direction_filter = regime_config.get("direction", "BOTH")

        signals = []

        # MC signal
        if "MC" in allowed_signals:
            mc = self._detect_mc_signal(closes, day_open)
            if mc:
                signals.append(mc)

        # MOM signal (spot-based, 3-min from analysis_history)
        if "MOM" in allowed_signals:
            mom = self._detect_mom_signal(closes)
            if mom:
                signals.append(mom)

        # PMOM signal (option premium 3-min OHLC, fires before spot MOM)
        if "PMOM" in allowed_signals:
            pmom = self._detect_premium_mom_signal(spot)
            if pmom:
                signals.append(pmom)

        # NMOM signal (NIFTY 1-min OHLC, fastest momentum detector)
        if "NMOM" in allowed_signals and self._fetcher is not None:
            nifty_1min = self._fetcher.fetch_nifty_candles(
                interval="minute", lookback_minutes=30)
            nmom = self._detect_nifty_mom_signal(nifty_1min)
            if nmom:
                signals.append(nmom)

        # VWAP signal
        if "VWAP" in allowed_signals:
            vwap = self._detect_vwap_signal(closes)
            if vwap:
                signals.append(vwap)

        # Filter by direction
        if direction_filter != "BOTH":
            allowed_dir = "CE" if direction_filter == "CE_ONLY" else "PE"
            signals = [s for s in signals if s["direction"] == f"BUY_{allowed_dir}"]

        return signals

    def _detect_mc_signal(self, closes: List[float], day_open: float) -> Optional[Dict]:
        """MC: 25+ pt rally from open, 20-65% pullback in last 8 candles, resumption."""
        current = closes[-1]
        move = current - day_open

        if abs(move) < 25:
            return None

        rally_dir = "UP" if move > 0 else "DOWN"
        if rally_dir == "UP":
            peak = max(closes)
        else:
            peak = min(closes)

        rally_pts = abs(peak - day_open)
        if rally_pts <= 0:
            return None

        # Pullback in last 8 candles
        n = min(8, len(closes) - 1)
        recent = closes[-n:]
        if rally_dir == "UP":
            pb_pts = peak - min(recent)
        else:
            pb_pts = max(recent) - peak

        pb_pct = pb_pts / rally_pts
        if pb_pct < 0.15 or pb_pct > 0.70:
            return None

        # Resumption: last candle in rally direction
        if len(closes) < 2:
            return None
        if rally_dir == "UP" and closes[-1] <= closes[-2]:
            return None
        if rally_dir == "DOWN" and closes[-1] >= closes[-2]:
            return None

        option_type = "CE" if rally_dir == "UP" else "PE"
        return {
            "signal_type": "MC",
            "direction": f"BUY_{option_type}",
            "option_type": option_type,
            "signal_data": {
                "rally_pts": rally_pts,
                "rally_direction": rally_dir,
                "pullback_pct": pb_pct,
                "day_open": day_open,
            },
        }

    def _detect_mom_signal(self, closes: List[float]) -> Optional[Dict]:
        """MOM: 4 consecutive higher closes (CE) or lower closes (PE)."""
        if len(closes) < 5:
            return None

        # Check for 4 consecutive higher closes
        if all(closes[-(i)] > closes[-(i + 1)] for i in range(1, 5)):
            return {
                "signal_type": "MOM",
                "direction": "BUY_CE",
                "option_type": "CE",
                "signal_data": {
                    "consecutive_higher": 4,
                    "momentum": closes[-1] - closes[-5],
                },
            }

        # Check for 4 consecutive lower closes
        if all(closes[-(i)] < closes[-(i + 1)] for i in range(1, 5)):
            return {
                "signal_type": "MOM",
                "direction": "BUY_PE",
                "option_type": "PE",
                "signal_data": {
                    "consecutive_lower": 4,
                    "momentum": closes[-5] - closes[-1],
                },
            }

        return None

    def _detect_nifty_mom_signal(self, candles_1min: List[Dict]) -> Optional[Dict]:
        """NMOM: 4 consecutive higher/lower 1-min NIFTY closes.

        Fires earlier than MOM (spot 3-min) and PMOM (option 3-min) when
        price moves decisively within a few minutes. The caller provides
        the 1-min NIFTY candles (typically via KiteDataFetcher.fetch_nifty_candles).
        """
        if not candles_1min or len(candles_1min) < 5:
            return None

        closes = [c["close"] for c in candles_1min]

        # 4 consecutive higher 1-min closes → BUY_CE
        if all(closes[-(i)] > closes[-(i + 1)] for i in range(1, 5)):
            return {
                "signal_type": "NMOM",
                "direction": "BUY_CE",
                "option_type": "CE",
                "signal_data": {
                    "consecutive_higher": 4,
                    "momentum": round(closes[-1] - closes[-5], 2),
                    "timeframe": "1min",
                },
            }

        # 4 consecutive lower 1-min closes → BUY_PE
        if all(closes[-(i)] < closes[-(i + 1)] for i in range(1, 5)):
            return {
                "signal_type": "NMOM",
                "direction": "BUY_PE",
                "option_type": "PE",
                "signal_data": {
                    "consecutive_lower": 4,
                    "momentum": round(closes[-5] - closes[-1], 2),
                    "timeframe": "1min",
                },
            }

        return None

    def _detect_premium_mom_signal(self, spot: float) -> Optional[Dict]:
        """PMOM: 4 consecutive higher 3-min option closes — fires before spot MOM.

        Uses Kite historical_data 3-min OHLC closes (not oi_snapshots LTP polls)
        so the engine sees exactly what the broker chart shows. Checks CE premium
        (rising → BUY_CE) and PE premium (rising → BUY_PE) at the current
        trading strikes (ATM-100 for CE, ATM+100 for PE).
        """
        if self._fetcher is None:
            return None

        ce_strike = self.get_rr_strike(spot, "CE")
        pe_strike = self.get_rr_strike(spot, "PE")

        # CE premium momentum (rising CE close → BUY_CE)
        ce_candles = self._fetcher.fetch_option_candles(ce_strike, "CE")
        if len(ce_candles) >= 5:
            ce_closes = [c["close"] for c in ce_candles]
            if all(ce_closes[-(i)] > ce_closes[-(i + 1)] for i in range(1, 5)):
                return {
                    "signal_type": "PMOM",
                    "direction": "BUY_CE",
                    "option_type": "CE",
                    "signal_data": {
                        "consecutive_higher": 4,
                        "premium_momentum": round(ce_closes[-1] - ce_closes[-5], 2),
                        "strike_monitored": ce_strike,
                    },
                }

        # PE premium momentum (rising PE close → BUY_PE)
        pe_candles = self._fetcher.fetch_option_candles(pe_strike, "PE")
        if len(pe_candles) >= 5:
            pe_closes = [c["close"] for c in pe_candles]
            if all(pe_closes[-(i)] > pe_closes[-(i + 1)] for i in range(1, 5)):
                return {
                    "signal_type": "PMOM",
                    "direction": "BUY_PE",
                    "option_type": "PE",
                    "signal_data": {
                        "consecutive_higher": 4,
                        "premium_momentum": round(pe_closes[-1] - pe_closes[-5], 2),
                        "strike_monitored": pe_strike,
                    },
                }

        return None

    def _detect_vwap_signal(self, closes: List[float]) -> Optional[Dict]:
        """VWAP: Spot crosses VWAP with 3+ pt separation."""
        if len(closes) < 3:
            return None

        # Compute cumulative VWAP (simple average since we don't have volume)
        n = len(closes)
        cum_sum = sum(closes)
        vwap_current = cum_sum / n
        vwap_prev = (cum_sum - closes[-1]) / (n - 1)

        current = closes[-1]
        prev = closes[-2]

        # Cross above VWAP
        if prev < vwap_prev and current > vwap_current and (current - vwap_current) > 3:
            return {
                "signal_type": "VWAP",
                "direction": "BUY_CE",
                "option_type": "CE",
                "signal_data": {
                    "cross": "ABOVE",
                    "separation": round(current - vwap_current, 2),
                    "vwap": round(vwap_current, 2),
                },
            }

        # Cross below VWAP
        if prev > vwap_prev and current < vwap_current and (vwap_current - current) > 3:
            return {
                "signal_type": "VWAP",
                "direction": "BUY_PE",
                "option_type": "PE",
                "signal_data": {
                    "cross": "BELOW",
                    "separation": round(vwap_current - current, 2),
                    "vwap": round(vwap_current, 2),
                },
            }

        return None

    # ------------------------------------------------------------------
    # Strike selection
    # ------------------------------------------------------------------

    @staticmethod
    def get_rr_strike(spot: float, option_type: str) -> int:
        """CE: ATM - 100 (2 ITM). PE: ATM + 100 (2 ITM)."""
        atm = round(spot / NIFTY_STEP) * NIFTY_STEP
        if option_type == "CE":
            return atm - 2 * NIFTY_STEP
        else:
            return atm + 2 * NIFTY_STEP

    # ------------------------------------------------------------------
    # Weekly trend (cached daily)
    # ------------------------------------------------------------------

    def get_weekly_trend(self) -> str:
        """UP/DOWN/NEUTRAL based on last two daily closes. Cached daily."""
        today = date.today()
        if self._weekly_trend_date == today and self._weekly_trend is not None:
            return self._weekly_trend

        trend = self._compute_weekly_trend()
        self._weekly_trend = trend
        self._weekly_trend_date = today
        return trend

    @staticmethod
    def _compute_weekly_trend() -> str:
        """Compare last two daily closes from nifty_history."""
        with get_connection() as conn:
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
    def pick_best_signal(signals: List[Dict]) -> Optional[Dict]:
        """Pick best signal by priority: MC > MOM > PMOM > NMOM > VWAP.

        Fastest/noisiest signals have lowest priority; when a slower signal
        also fires, it wins because it has more confirmation behind it.
        """
        if not signals:
            return None
        priority = {"MC": 0, "MOM": 1, "PMOM": 2, "NMOM": 3, "VWAP": 4}
        signals.sort(key=lambda s: priority.get(s.get("signal_type", ""), 99))
        return signals[0]
