"""
Scalper Engine -- Technical analysis on 3-minute option premium charts.

Builds premium time-series from oi_snapshots, computes VWAP, support/resistance,
swing highs/lows, breakouts, and momentum for the scalper agent.

Strikes: 2 below ATM for CE (slightly ITM), 2 above ATM for PE (slightly ITM).
These have higher delta and better liquidity for quick scalps.
"""

from datetime import datetime, date
from typing import Optional, Dict, List, Tuple
from db.connection import get_connection
from core.logger import get_logger

log = get_logger("scalper_engine")

NIFTY_STEP = 50
STRIKES_OFFSET = 2  # 2 strikes from ATM
SWING_LOOKBACK = 2  # candles on each side for swing detection
SR_CLUSTER_PCT = 2.0  # % tolerance for clustering S/R levels
MOMENTUM_PERIODS = 3  # candles for momentum calculation


class ScalperEngine:
    """Builds premium charts and computes technical indicators for scalping."""

    def get_scalp_strikes(self, spot_price: float) -> Dict[str, int]:
        """Return CE and PE scalping strikes (2 below / 2 above ATM)."""
        atm = round(spot_price / NIFTY_STEP) * NIFTY_STEP
        return {
            "ce_strike": atm - (NIFTY_STEP * STRIKES_OFFSET),
            "pe_strike": atm + (NIFTY_STEP * STRIKES_OFFSET),
            "atm": atm,
        }

    def build_premium_chart(self, spot_price: float,
                            date_str: str = None,
                            ce_strike: int = None,
                            pe_strike: int = None) -> Optional[Dict]:
        """
        Build full-day premium chart for CE and PE scalping strikes.

        Queries oi_snapshots for today's data (or specified date).
        Returns dict with candle arrays and current spot.
        """
        if not date_str:
            date_str = date.today().strftime("%Y-%m-%d")

        if not ce_strike or not pe_strike:
            strikes = self.get_scalp_strikes(spot_price)
            ce_strike = strikes["ce_strike"]
            pe_strike = strikes["pe_strike"]

        with get_connection() as conn:
            cursor = conn.cursor()
            # Get CE candles
            cursor.execute("""
                SELECT timestamp, spot_price, ce_ltp, ce_volume, ce_iv, ce_oi
                FROM oi_snapshots
                WHERE DATE(timestamp) = ? AND strike_price = ? AND ce_ltp > 0
                ORDER BY timestamp
            """, (date_str, ce_strike))
            ce_rows = cursor.fetchall()

            # Get PE candles
            cursor.execute("""
                SELECT timestamp, spot_price, pe_ltp, pe_volume, pe_iv, pe_oi
                FROM oi_snapshots
                WHERE DATE(timestamp) = ? AND strike_price = ? AND pe_ltp > 0
                ORDER BY timestamp
            """, (date_str, pe_strike))
            pe_rows = cursor.fetchall()

        if not ce_rows and not pe_rows:
            return None

        ce_candles = []
        for row in ce_rows:
            ce_candles.append({
                "ts": row["timestamp"],
                "ltp": row["ce_ltp"],
                "volume": row["ce_volume"] or 0,
                "iv": row["ce_iv"] or 0,
                "oi": row["ce_oi"] or 0,
                "spot": row["spot_price"],
            })

        pe_candles = []
        for row in pe_rows:
            pe_candles.append({
                "ts": row["timestamp"],
                "ltp": row["pe_ltp"],
                "volume": row["pe_volume"] or 0,
                "iv": row["pe_iv"] or 0,
                "oi": row["pe_oi"] or 0,
                "spot": row["spot_price"],
            })

        return {
            "ce_strike": ce_strike,
            "pe_strike": pe_strike,
            "spot_price": spot_price,
            "date": date_str,
            "ce_candles": ce_candles,
            "pe_candles": pe_candles,
        }

    def compute_vwap(self, candles: List[Dict]) -> List[float]:
        """
        Compute VWAP on premium data using volume.
        Returns list of VWAP values parallel to candles.
        If volume is 0 for all, falls back to simple average.
        """
        if not candles:
            return []

        vwap_values = []
        cum_vol_price = 0.0
        cum_vol = 0

        has_volume = any(c["volume"] > 0 for c in candles)

        if has_volume:
            for c in candles:
                vol = max(c["volume"], 1)  # avoid zero
                cum_vol_price += c["ltp"] * vol
                cum_vol += vol
                vwap_values.append(round(cum_vol_price / cum_vol, 2))
        else:
            # Fallback: cumulative simple average
            cum_sum = 0.0
            for i, c in enumerate(candles):
                cum_sum += c["ltp"]
                vwap_values.append(round(cum_sum / (i + 1), 2))

        return vwap_values

    def detect_swing_points(self, candles: List[Dict],
                            lookback: int = SWING_LOOKBACK) -> Dict:
        """
        Detect swing highs and lows on premium series.
        A swing high at i: ltp[i] > all neighbours within lookback.
        Returns {'swing_highs': [(idx, price)], 'swing_lows': [(idx, price)]}.
        """
        highs = []
        lows = []
        n = len(candles)

        if n < 2 * lookback + 1:
            return {"swing_highs": highs, "swing_lows": lows}

        for i in range(lookback, n - lookback):
            price = candles[i]["ltp"]
            window = [candles[j]["ltp"] for j in range(i - lookback, i + lookback + 1) if j != i]

            if price > max(window):
                highs.append((i, price))
            elif price < min(window):
                lows.append((i, price))

        return {"swing_highs": highs, "swing_lows": lows}

    def detect_support_resistance(self, candles: List[Dict],
                                  swings: Dict) -> Dict:
        """
        Cluster swing points at similar levels into S/R zones.
        Multiple touches at similar levels = stronger S/R.
        Returns {'support': [(level, touches)], 'resistance': [(level, touches)]}.
        """
        def cluster_levels(points: List[Tuple[int, float]],
                           tolerance_pct: float = SR_CLUSTER_PCT) -> List[Tuple[float, int]]:
            if not points:
                return []
            levels = sorted(points, key=lambda x: x[1])
            clusters = []
            used = set()
            for i, (_, price_i) in enumerate(levels):
                if i in used:
                    continue
                cluster = [price_i]
                used.add(i)
                for j, (_, price_j) in enumerate(levels):
                    if j in used:
                        continue
                    if abs(price_j - price_i) / price_i * 100 < tolerance_pct:
                        cluster.append(price_j)
                        used.add(j)
                avg_level = round(sum(cluster) / len(cluster), 2)
                clusters.append((avg_level, len(cluster)))
            # Sort by touches (strongest first)
            clusters.sort(key=lambda x: -x[1])
            return clusters

        support = cluster_levels(swings["swing_lows"])
        resistance = cluster_levels(swings["swing_highs"])
        return {"support": support, "resistance": resistance}

    def compute_momentum(self, candles: List[Dict],
                         periods: int = MOMENTUM_PERIODS) -> Optional[float]:
        """Premium rate of change over last N candles (%)."""
        if len(candles) < periods + 1:
            return None
        start = candles[-(periods + 1)]["ltp"]
        end = candles[-1]["ltp"]
        if start <= 0:
            return None
        return round((end - start) / start * 100, 2)

    def detect_last_candles_direction(self, candles: List[Dict],
                                      n: int = 3) -> str:
        """Describe last N candles: e.g., 'UP UP DOWN' or 'HH' (higher highs)."""
        if len(candles) < n + 1:
            return "N/A"
        recent = candles[-(n + 1):]
        dirs = []
        for i in range(1, len(recent)):
            if recent[i]["ltp"] > recent[i-1]["ltp"]:
                dirs.append("UP")
            elif recent[i]["ltp"] < recent[i-1]["ltp"]:
                dirs.append("DN")
            else:
                dirs.append("FLAT")
        return " ".join(dirs)

    def detect_breakout(self, candles: List[Dict],
                        sr: Dict) -> Optional[Dict]:
        """
        Check if the latest candle broke above resistance or below support.
        Returns breakout info dict or None.
        """
        if len(candles) < 3:
            return None

        current = candles[-1]["ltp"]
        prev = candles[-2]["ltp"]

        # Check resistance breakout
        for level, touches in sr.get("resistance", []):
            if prev < level and current > level:
                return {
                    "type": "BREAKOUT_UP",
                    "level": level,
                    "touches": touches,
                    "premium": current,
                    "overshoot_pct": round((current - level) / level * 100, 2),
                }

        # Check support breakdown
        for level, touches in sr.get("support", []):
            if prev > level and current < level:
                return {
                    "type": "BREAKOUT_DOWN",
                    "level": level,
                    "touches": touches,
                    "premium": current,
                    "overshoot_pct": round((level - current) / level * 100, 2),
                }

        return None

    def compute_iv_trend(self, candles: List[Dict], periods: int = 5) -> str:
        """IV trend over last N candles: RISING, FALLING, or FLAT."""
        if len(candles) < periods + 1:
            return "N/A"
        recent_iv = [c["iv"] for c in candles[-(periods + 1):]]
        if not any(iv > 0 for iv in recent_iv):
            return "N/A"
        start_iv = recent_iv[0]
        end_iv = recent_iv[-1]
        if start_iv <= 0:
            return "N/A"
        change_pct = (end_iv - start_iv) / start_iv * 100
        if change_pct > 2:
            return "RISING"
        elif change_pct < -2:
            return "FALLING"
        return "FLAT"

    def has_potential_setup(self, candles: List[Dict], vwap: List[float],
                           sr: Dict, momentum: Optional[float]) -> bool:
        """
        Pre-filter: does this side have any potential setup?
        Used to avoid calling Claude when no setup is forming.

        Conditions (any one is enough):
        1. VWAP breakout: premium just crossed above VWAP
        2. Support bounce: premium near support and last candle is UP
        3. Momentum burst: 3+ consecutive higher closes
        4. Resistance breakout detected
        """
        if len(candles) < 4 or len(vwap) < 4:
            return False

        current = candles[-1]["ltp"]
        prev = candles[-2]["ltp"]
        current_vwap = vwap[-1]
        prev_vwap = vwap[-2]

        # 1. VWAP crossover (from below to above)
        if prev < prev_vwap and current > current_vwap:
            return True

        # 2. Support bounce
        for level, touches in sr.get("support", []):
            if touches >= 2:
                dist_pct = abs(current - level) / level * 100
                if dist_pct < 3 and current > prev:
                    return True

        # 3. Momentum burst (3 consecutive higher closes)
        if len(candles) >= 4:
            if all(candles[-(i)]["ltp"] > candles[-(i+1)]["ltp"] for i in range(1, 4)):
                return True

        # 4. Breakout
        breakout = self.detect_breakout(candles, sr)
        if breakout and breakout["type"] == "BREAKOUT_UP":
            return True

        return False

    def format_chart_for_prompt(self, chart_data: Dict) -> str:
        """
        Format full premium chart + indicators as text for Claude prompt.
        Returns a multi-section string with tables and technical summaries.
        """
        if not chart_data:
            return "No chart data available."

        sections = []

        for side in ("ce", "pe"):
            candles = chart_data[f"{side}_candles"]
            strike = chart_data[f"{side}_strike"]

            if not candles:
                sections.append(f"## {side.upper()} Strike {strike}\nNo data available.\n")
                continue

            vwap = self.compute_vwap(candles)
            swings = self.detect_swing_points(candles)
            sr = self.detect_support_resistance(candles, swings)
            momentum = self.compute_momentum(candles)
            direction = self.detect_last_candles_direction(candles)
            breakout = self.detect_breakout(candles, sr)
            iv_trend = self.compute_iv_trend(candles)

            label = "CE (Call)" if side == "ce" else "PE (Put)"
            itm_label = "Slightly ITM" if side == "ce" else "Slightly ITM"

            # Chart table (last 40 candles max to keep prompt manageable)
            display_candles = candles[-40:]
            display_vwap = vwap[-40:]

            lines = [f"## {label} Premium Chart -- Strike {strike} ({itm_label})"]
            lines.append(f"| # | Time  | LTP    | Vol     | VWAP   | IV    | Spot    |")
            lines.append(f"|---|-------|--------|---------|--------|-------|---------|")

            for i, (c, v) in enumerate(zip(display_candles, display_vwap)):
                ts = c["ts"]
                if isinstance(ts, str) and "T" in ts:
                    ts = ts.split("T")[1][:5]
                elif isinstance(ts, str):
                    ts = ts[-8:-3]
                lines.append(
                    f"| {i+1:2d} | {ts} | {c['ltp']:7.2f} | {c['volume']:7d} | "
                    f"{v:7.2f} | {c['iv']:5.1f} | {c['spot']:8.2f} |"
                )

            lines.append("")

            # Technical summary
            current_ltp = candles[-1]["ltp"]
            current_vwap = vwap[-1]
            vs_vwap_pct = round((current_ltp - current_vwap) / current_vwap * 100, 2) if current_vwap > 0 else 0

            lines.append(f"### {side.upper()} Technical Summary")
            lines.append(f"- Current LTP: {current_ltp:.2f}")
            lines.append(f"- VWAP: {current_vwap:.2f} | Premium vs VWAP: {vs_vwap_pct:+.2f}%")

            # S/R levels
            sup_str = ", ".join(f"{l:.2f} ({t}x)" for l, t in sr.get("support", [])[:3])
            res_str = ", ".join(f"{l:.2f} ({t}x)" for l, t in sr.get("resistance", [])[:3])
            lines.append(f"- Support: {sup_str or 'None detected'}")
            lines.append(f"- Resistance: {res_str or 'None detected'}")

            # Momentum & direction
            lines.append(f"- Last 3 candles: {direction}")
            if momentum is not None:
                lines.append(f"- Momentum (3-candle): {momentum:+.2f}%")
            lines.append(f"- IV: {candles[-1]['iv']:.1f} (trend: {iv_trend})")

            # Breakout
            if breakout:
                lines.append(f"- **BREAKOUT: {breakout['type']}** at level {breakout['level']:.2f} ({breakout['touches']}x touch)")

            # Swing annotations
            if swings["swing_highs"]:
                recent_high = swings["swing_highs"][-1]
                lines.append(f"- Last swing high: {recent_high[1]:.2f} (candle #{recent_high[0]+1})")
            if swings["swing_lows"]:
                recent_low = swings["swing_lows"][-1]
                lines.append(f"- Last swing low: {recent_low[1]:.2f} (candle #{recent_low[0]+1})")

            lines.append("")
            sections.append("\n".join(lines))

        return "\n".join(sections)

    def analyze_side(self, candles: List[Dict]) -> Dict:
        """Run full technical analysis on one side (CE or PE). Returns analysis dict."""
        if not candles:
            return {"has_setup": False}

        vwap = self.compute_vwap(candles)
        swings = self.detect_swing_points(candles)
        sr = self.detect_support_resistance(candles, swings)
        momentum = self.compute_momentum(candles)
        has_setup = self.has_potential_setup(candles, vwap, sr, momentum)
        breakout = self.detect_breakout(candles, sr)

        return {
            "vwap": vwap,
            "swings": swings,
            "sr": sr,
            "momentum": momentum,
            "has_setup": has_setup,
            "breakout": breakout,
            "current_ltp": candles[-1]["ltp"] if candles else 0,
            "current_vwap": vwap[-1] if vwap else 0,
        }
