"""
OI Analyzer - Tug-of-War Analysis Logic
Analyzes option chain OI to determine market sentiment with self-learning capabilities
"""

from typing import Tuple, Optional, List, Dict


def find_atm_strike(spot_price: float, strikes: list) -> int:
    """
    Find the ATM (At The Money) strike closest to spot price.

    Args:
        spot_price: Current underlying spot price
        strikes: List of available strike prices

    Returns:
        The strike price closest to spot
    """
    if not strikes:
        return 0
    return min(strikes, key=lambda x: abs(x - spot_price))


def get_otm_strikes(atm_strike: int, all_strikes: list, num_strikes: int = 3) -> Tuple[list, list]:
    """
    Get OTM strikes on both sides of ATM.

    For the tug-of-war analysis:
    - Call OTM: Strikes ABOVE spot (bears writing calls here)
    - Put OTM: Strikes BELOW spot (bulls writing puts here)

    Args:
        atm_strike: The ATM strike price
        all_strikes: Sorted list of all available strikes
        num_strikes: Number of OTM strikes to consider on each side

    Returns:
        Tuple of (otm_call_strikes, otm_put_strikes)
    """
    sorted_strikes = sorted(all_strikes)

    try:
        atm_idx = sorted_strikes.index(atm_strike)
    except ValueError:
        # ATM not in list, find closest index
        atm_idx = min(range(len(sorted_strikes)),
                     key=lambda i: abs(sorted_strikes[i] - atm_strike))

    # OTM Calls: strikes ABOVE ATM (indices after ATM)
    otm_calls = sorted_strikes[atm_idx + 1 : atm_idx + 1 + num_strikes]

    # OTM Puts: strikes BELOW ATM (indices before ATM)
    start_idx = max(0, atm_idx - num_strikes)
    otm_puts = sorted_strikes[start_idx : atm_idx]

    return otm_calls, otm_puts


def calculate_price_momentum(price_history: List[dict]) -> float:
    """
    Calculate price momentum score based on recent price movement.

    Args:
        price_history: List of dicts with 'spot_price' (recent history, chronological order)

    Returns:
        Momentum score from -100 (strong bearish) to +100 (strong bullish)
    """
    if not price_history or len(price_history) < 2:
        return 0.0

    # Compare current price to oldest price in history
    current_price = price_history[-1]['spot_price']
    past_price = price_history[0]['spot_price']

    if past_price == 0:
        return 0.0

    # Calculate percentage change
    price_change_pct = ((current_price - past_price) / past_price) * 100

    # Amplify the signal: +1% = +20, -1% = -20
    # This makes momentum more visible in the combined score
    momentum_score = price_change_pct * 20

    # Cap at ±100
    return max(-100, min(100, momentum_score))


def detect_market_regime(price_history: List[dict], momentum_score: float) -> dict:
    """
    Detect if market is trending or range-bound.

    Market regime affects OI interpretation:
    - Trending: Where positions are trapped matters more (absolute OI)
    - Range-bound: Fresh activity matters more (OI change)

    Args:
        price_history: List of recent price data
        momentum_score: Current momentum score

    Returns:
        {
            "regime": str,  # "trending_up", "trending_down", "range_bound"
            "description": str,
            "oi_change_weight": float,  # Weight for OI change component
            "oi_total_weight": float    # Weight for absolute OI component
        }
    """
    if not price_history or len(price_history) < 3:
        return {
            "regime": "range_bound",
            "description": "Insufficient data - assuming range-bound",
            "oi_change_weight": 0.70,
            "oi_total_weight": 0.30
        }

    prices = [p.get("spot_price", 0) for p in price_history if p.get("spot_price", 0) > 0]
    if len(prices) < 2:
        return {
            "regime": "range_bound",
            "description": "Insufficient price data",
            "oi_change_weight": 0.70,
            "oi_total_weight": 0.30
        }

    high = max(prices)
    low = min(prices)
    current = prices[-1]

    # Calculate range as percentage
    range_pct = ((high - low) / current) * 100 if current > 0 else 0

    # Check if price is near high/low
    near_high = (high - current) / current < 0.001 if current > 0 else False  # Within 0.1%
    near_low = (current - low) / current < 0.001 if current > 0 else False

    # Determine regime
    if near_high and momentum_score > 25 and range_pct > 0.2:
        return {
            "regime": "trending_up",
            "description": f"Bullish trend - at highs with {momentum_score:.0f} momentum",
            "oi_change_weight": 0.30,  # In trends, fresh activity less important
            "oi_total_weight": 0.70    # Trapped positions more important
        }
    elif near_low and momentum_score < -25 and range_pct > 0.2:
        return {
            "regime": "trending_down",
            "description": f"Bearish trend - at lows with {momentum_score:.0f} momentum",
            "oi_change_weight": 0.30,
            "oi_total_weight": 0.70
        }
    else:
        return {
            "regime": "range_bound",
            "description": "Range-bound market - fresh activity signals important",
            "oi_change_weight": 0.70,  # Fresh activity more important
            "oi_total_weight": 0.30
        }


def calculate_market_trend(analysis_history: List[dict], lookback: int = 10) -> dict:
    """
    Calculate market trend based on recent analysis history.

    Uses weighted combination of 4 signals:
    - Score Slope (40%): Linear regression of combined_score over time
    - Score Average (30%): Mean combined_score value
    - Regime Consistency (20%): Majority regime type
    - Momentum Direction (10%): Sign consistency of momentum scores

    Args:
        analysis_history: List of recent analysis dicts (newest first from DB)
        lookback: Number of data points to consider (default 10 = 30 min)

    Returns:
        {
            "trend": "upward" | "sideways" | "downward",
            "strength": "strong" | "moderate" | "weak",
            "display": "Upward ↑" | "Sideways →" | "Downward ↓",
            "confidence": 0-100,
            "description": "Human-readable explanation"
        }
    """
    default_result = {
        "trend": "sideways",
        "strength": "weak",
        "display": "Sideways →",
        "confidence": 0,
        "description": "Insufficient data for trend analysis"
    }

    if not analysis_history or len(analysis_history) < 3:
        return default_result

    # Take the most recent 'lookback' entries and reverse to chronological order (oldest first)
    recent = analysis_history[:lookback][::-1]

    # Extract combined_score values
    scores = []
    for entry in recent:
        score = entry.get("combined_score")
        if score is not None:
            scores.append(float(score))

    if len(scores) < 3:
        return default_result

    # === Signal 1: Score Slope (40% weight) ===
    # Simple linear regression slope
    n = len(scores)
    x_mean = (n - 1) / 2
    y_mean = sum(scores) / n

    numerator = sum((i - x_mean) * (scores[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    slope = numerator / denominator if denominator != 0 else 0

    # Slope thresholds: > +2 = upward, < -2 = downward
    if slope > 2:
        slope_signal = 1  # Upward
    elif slope < -2:
        slope_signal = -1  # Downward
    else:
        slope_signal = 0  # Sideways

    # === Signal 2: Score Average (30% weight) ===
    avg_score = y_mean

    # Average thresholds: > +15 = upward, < -15 = downward
    if avg_score > 15:
        avg_signal = 1
    elif avg_score < -15:
        avg_signal = -1
    else:
        avg_signal = 0

    # === Signal 3: Regime Consistency (20% weight) ===
    regime_counts = {"trending_up": 0, "trending_down": 0, "range_bound": 0}
    for entry in recent:
        regime_info = entry.get("market_regime", {})
        regime = regime_info.get("regime", "range_bound") if isinstance(regime_info, dict) else "range_bound"
        if regime in regime_counts:
            regime_counts[regime] += 1

    majority_regime = max(regime_counts, key=regime_counts.get)
    if majority_regime == "trending_up":
        regime_signal = 1
    elif majority_regime == "trending_down":
        regime_signal = -1
    else:
        regime_signal = 0

    # === Signal 4: Momentum Direction (10% weight) ===
    momentum_scores = []
    for entry in recent:
        m = entry.get("momentum_score")
        if m is not None:
            momentum_scores.append(float(m))

    if momentum_scores:
        positive_count = sum(1 for m in momentum_scores if m > 0)
        negative_count = sum(1 for m in momentum_scores if m < 0)
        total = len(momentum_scores)

        # > 60% positive = upward, > 60% negative = downward
        if positive_count / total > 0.6:
            momentum_signal = 1
        elif negative_count / total > 0.6:
            momentum_signal = -1
        else:
            momentum_signal = 0
    else:
        momentum_signal = 0

    # === Weighted Combination ===
    weighted_score = (
        slope_signal * 0.40 +
        avg_signal * 0.30 +
        regime_signal * 0.20 +
        momentum_signal * 0.10
    )

    # Count how many signals agree
    signals = [slope_signal, avg_signal, regime_signal, momentum_signal]
    upward_count = sum(1 for s in signals if s == 1)
    downward_count = sum(1 for s in signals if s == -1)

    # Determine trend direction
    if weighted_score > 0.25:
        trend = "upward"
        display = "Upward ↑"
    elif weighted_score < -0.25:
        trend = "downward"
        display = "Downward ↓"
    else:
        trend = "sideways"
        display = "Sideways →"

    # Determine strength based on signal agreement
    max_agreement = max(upward_count, downward_count)
    if max_agreement >= 3:
        strength = "strong"
    elif max_agreement == 2:
        strength = "moderate"
    else:
        strength = "weak"

    # Calculate confidence (0-100)
    confidence = abs(weighted_score) * 100
    confidence = min(100, max(0, confidence))

    # Build description
    descriptions = []
    if slope_signal != 0:
        descriptions.append(f"score {'rising' if slope_signal > 0 else 'falling'}")
    if avg_signal != 0:
        descriptions.append(f"avg {'positive' if avg_signal > 0 else 'negative'}")
    if momentum_signal != 0:
        descriptions.append(f"{'positive' if momentum_signal > 0 else 'negative'} momentum")

    if descriptions:
        description = f"{strength.capitalize()} {trend} trend - {', '.join(descriptions)}"
    else:
        description = f"{strength.capitalize()} {trend} trend - mixed signals"

    return {
        "trend": trend,
        "strength": strength,
        "display": display,
        "confidence": round(confidence, 1),
        "description": description
    }


def calculate_conviction_multiplier(volume: int, oi_change: int) -> float:
    """
    Calculate conviction multiplier based on volume-to-OI turnover ratio.

    High volume relative to OI change = Fresh positions = High conviction
    Low volume relative to OI change = Stale positions = Low conviction

    Args:
        volume: Total traded volume for this strike today
        oi_change: Change in open interest (absolute value will be used)

    Returns:
        Multiplier: 1.5 (high conviction), 1.0 (normal), 0.5 (low conviction)
    """
    # Ignore negligible OI changes
    if abs(oi_change) < 100:
        return 0.5

    # Calculate turnover ratio
    turnover_ratio = volume / abs(oi_change)

    # Conviction scoring
    if turnover_ratio > 0.5:
        # >50% turnover = Fresh, high conviction
        return 1.5
    elif turnover_ratio > 0.2:
        # 20-50% turnover = Moderate conviction
        return 1.0
    else:
        # <20% turnover = Stale, low conviction
        return 0.5


def calculate_dynamic_sl_pct(strikes_data: dict, strike: int, option_type: str) -> float:
    """
    Calculate IV-based stop loss percentage (15-25% range).

    Lower IV = tighter stop loss (less premium decay expected)
    Higher IV = wider stop loss (more volatility expected)

    Widened from 10-18% to 15-25% to reduce quick SL hits during
    normal intraday volatility (38% of trades were hitting SL within 6 min).

    Args:
        strikes_data: Dict of strike -> {..., ce_iv, pe_iv}
        strike: The strike price to check IV for
        option_type: 'CE' or 'PE'

    Returns:
        SL percentage as decimal (0.15 to 0.25)
    """
    strike_data = strikes_data.get(strike, {})
    iv = strike_data.get('ce_iv' if option_type == 'CE' else 'pe_iv', 0)

    if iv <= 0:
        return 0.20  # Default 20% if no IV data

    # IV-based SL percentage (widened range)
    if iv < 12:
        return 0.15  # Low IV = tighter SL
    elif iv < 15:
        return 0.18
    elif iv < 18:
        return 0.20
    elif iv < 22:
        return 0.22
    else:
        return 0.25  # High IV = wider SL (max 25%)


def calculate_oi_acceleration(prev_oi_changes: List[tuple], current_call_change: float,
                               current_put_change: float) -> dict:
    """
    Calculate OI change acceleration over last N data points.

    Acceleration helps distinguish:
    - Fresh position building (accelerating) vs unwinding (decelerating)
    - Short covering rally vs genuine bullish buying
    - Profit booking vs trend reversal

    Args:
        prev_oi_changes: List of (call_oi_change, put_oi_change) tuples, oldest first
        current_call_change: Current snapshot's call OI change
        current_put_change: Current snapshot's put OI change

    Returns:
        {
            "call_acceleration": float,  # +ve = accelerating bearish pressure
            "put_acceleration": float,   # +ve = accelerating bullish pressure
            "net_acceleration": float,   # put - call (positive = bullish acceleration)
            "phase": str,                # "accumulation", "distribution", "unwinding", "stable"
            "phase_description": str     # Human-readable explanation
        }
    """
    if not prev_oi_changes or len(prev_oi_changes) < 1:
        return {
            "call_acceleration": 0.0,
            "put_acceleration": 0.0,
            "net_acceleration": 0.0,
            "phase": "stable",
            "phase_description": "Insufficient data for acceleration"
        }

    # Get previous period's changes (most recent in history)
    prev_call_change, prev_put_change = prev_oi_changes[-1]

    # Calculate velocity (change in OI change)
    call_acceleration = current_call_change - prev_call_change
    put_acceleration = current_put_change - prev_put_change

    # Net acceleration: positive = bullish momentum building
    net_acceleration = put_acceleration - call_acceleration

    # Thresholds for phase detection (adjust based on typical OI values)
    accel_threshold = 5000  # Minimum acceleration to be significant

    # Determine market phase
    phase = "stable"
    phase_description = "OI changes are stable"

    # Check for accumulation (bullish pressure building)
    if net_acceleration > accel_threshold:
        if current_put_change > 0 and put_acceleration > 0:
            phase = "accumulation"
            phase_description = "Bullish accumulation - Put writing accelerating"
        elif current_call_change < prev_call_change:
            phase = "accumulation"
            phase_description = "Short covering - Call OI addition slowing"

    # Check for distribution (bearish pressure building)
    elif net_acceleration < -accel_threshold:
        if current_call_change > 0 and call_acceleration > 0:
            phase = "distribution"
            phase_description = "Bearish distribution - Call writing accelerating"
        elif current_put_change < prev_put_change:
            phase = "distribution"
            phase_description = "Long unwinding - Put OI addition slowing"

    # Check for unwinding (both sides decelerating)
    elif (abs(current_call_change) < abs(prev_call_change) and
          abs(current_put_change) < abs(prev_put_change)):
        phase = "unwinding"
        phase_description = "Position unwinding - OI activity decreasing"

    return {
        "call_acceleration": round(call_acceleration, 0),
        "put_acceleration": round(put_acceleration, 0),
        "net_acceleration": round(net_acceleration, 0),
        "phase": phase,
        "phase_description": phase_description
    }


def calculate_premium_momentum(current_strikes: dict, prev_strikes: Optional[dict],
                               spot_price: float) -> dict:
    """
    Calculate premium momentum for ATM strikes.

    Premium momentum tracks buying pressure:
    - Call premiums rising despite bearish OI = buyers overpowering writers
    - Put premiums rising despite bullish OI = sellers overpowering writers

    Args:
        current_strikes: Current strike data with LTP
        prev_strikes: Previous snapshot's strike data with LTP
        spot_price: Current spot price

    Returns:
        {
            "call_premium_change_pct": float,
            "put_premium_change_pct": float,
            "premium_momentum_score": float,  # -100 to +100
            "interpretation": str
        }
    """
    if not prev_strikes or not current_strikes:
        return {
            "call_premium_change_pct": 0.0,
            "put_premium_change_pct": 0.0,
            "premium_momentum_score": 0.0,
            "interpretation": "No previous data for premium comparison"
        }

    all_strikes = sorted(current_strikes.keys())
    if not all_strikes:
        return {
            "call_premium_change_pct": 0.0,
            "put_premium_change_pct": 0.0,
            "premium_momentum_score": 0.0,
            "interpretation": "No strikes available"
        }

    atm_strike = find_atm_strike(spot_price, all_strikes)

    # Get current and previous LTP for ATM strike
    curr_data = current_strikes.get(atm_strike, {})
    prev_data = prev_strikes.get(atm_strike, {})

    curr_ce_ltp = curr_data.get("ce_ltp", 0)
    prev_ce_ltp = prev_data.get("ce_ltp", curr_ce_ltp)
    curr_pe_ltp = curr_data.get("pe_ltp", 0)
    prev_pe_ltp = prev_data.get("pe_ltp", curr_pe_ltp)

    # Calculate premium change percentages
    call_change_pct = 0.0
    put_change_pct = 0.0

    if prev_ce_ltp > 0:
        call_change_pct = ((curr_ce_ltp - prev_ce_ltp) / prev_ce_ltp) * 100
    if prev_pe_ltp > 0:
        put_change_pct = ((curr_pe_ltp - prev_pe_ltp) / prev_pe_ltp) * 100

    # Premium momentum score: call rising = bullish pressure, put rising = bearish
    # Scale to -100 to +100
    premium_momentum = (call_change_pct - put_change_pct) * 5
    premium_momentum = max(-100, min(100, premium_momentum))

    # Interpretation
    if premium_momentum > 20:
        interpretation = "Bullish premium momentum - call buyers aggressive"
    elif premium_momentum < -20:
        interpretation = "Bearish premium momentum - put buyers aggressive"
    elif abs(call_change_pct) > 2 or abs(put_change_pct) > 2:
        interpretation = "Mixed premium activity"
    else:
        interpretation = "Premium momentum neutral"

    return {
        "call_premium_change_pct": round(call_change_pct, 2),
        "put_premium_change_pct": round(put_change_pct, 2),
        "premium_momentum_score": round(premium_momentum, 1),
        "interpretation": interpretation,
        "atm_ce_ltp": round(curr_ce_ltp, 2),
        "atm_pe_ltp": round(curr_pe_ltp, 2)
    }


def calculate_iv_skew(strikes_data: dict, spot_price: float, num_strikes: int = 3) -> float:
    """
    Calculate IV Skew = avg(Put IV) - avg(Call IV) for OTM strikes.

    Positive skew = Market pricing more downside risk = Bearish bias
    Negative skew = Market pricing more upside risk = Bullish bias

    Args:
        strikes_data: Dict of strike -> {..., ce_iv, pe_iv}
        spot_price: Current spot price
        num_strikes: Number of OTM strikes to consider

    Returns:
        IV skew value (positive = bearish, negative = bullish)
    """
    all_strikes = sorted(strikes_data.keys())
    if not all_strikes:
        return 0.0

    atm_strike = find_atm_strike(spot_price, all_strikes)
    atm_idx = all_strikes.index(atm_strike) if atm_strike in all_strikes else 0

    # OTM Calls: strikes ABOVE ATM
    otm_call_strikes = all_strikes[atm_idx + 1:atm_idx + 1 + num_strikes]
    # OTM Puts: strikes BELOW ATM
    start_idx = max(0, atm_idx - num_strikes)
    otm_put_strikes = all_strikes[start_idx:atm_idx]

    # Calculate average IVs
    call_ivs = [strikes_data[s].get("ce_iv", 0) for s in otm_call_strikes if strikes_data[s].get("ce_iv", 0) > 0]
    put_ivs = [strikes_data[s].get("pe_iv", 0) for s in otm_put_strikes if strikes_data[s].get("pe_iv", 0) > 0]

    avg_call_iv = sum(call_ivs) / len(call_ivs) if call_ivs else 0
    avg_put_iv = sum(put_ivs) / len(put_ivs) if put_ivs else 0

    return avg_put_iv - avg_call_iv


def calculate_max_pain(strikes_data: dict) -> int:
    """
    Calculate Max Pain strike - where total option intrinsic value is minimized.

    This is the strike where option writers (sellers) profit the most,
    and price tends to gravitate toward this level near expiry.

    Args:
        strikes_data: Dict of strike -> {ce_oi, pe_oi, ...}

    Returns:
        Max Pain strike price
    """
    all_strikes = sorted(strikes_data.keys())
    if not all_strikes:
        return 0

    min_pain = float('inf')
    max_pain_strike = all_strikes[len(all_strikes) // 2]

    for test_strike in all_strikes:
        total_pain = 0

        for strike in all_strikes:
            data = strikes_data[strike]
            ce_oi = data.get("ce_oi", 0)
            pe_oi = data.get("pe_oi", 0)

            # Call intrinsic value (if price above strike)
            if test_strike > strike:
                call_intrinsic = (test_strike - strike) * ce_oi
                total_pain += call_intrinsic

            # Put intrinsic value (if price below strike)
            if test_strike < strike:
                put_intrinsic = (strike - test_strike) * pe_oi
                total_pain += put_intrinsic

        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = test_strike

    return max_pain_strike


def find_oi_clusters(strikes_data: dict, spot_price: float,
                     percentile_threshold: float = 75) -> Dict[str, dict]:
    """
    Find OI clusters (support/resistance levels) based on high OI concentration.

    Returns strikes where OI is significantly higher than average,
    which act as natural support (put OI) and resistance (call OI) levels.

    Args:
        strikes_data: Dict of strike -> {ce_oi, pe_oi, ...}
        spot_price: Current spot price
        percentile_threshold: OI must be above this percentile to be a cluster

    Returns:
        Dict with 'resistance' (call clusters) and 'support' (put clusters)
    """
    all_strikes = sorted(strikes_data.keys())
    if not all_strikes:
        return {"resistance": [], "support": [], "strongest_resistance": None, "strongest_support": None}

    # Collect all OI values
    call_ois = [(s, strikes_data[s].get("ce_oi", 0)) for s in all_strikes if s > spot_price]
    put_ois = [(s, strikes_data[s].get("pe_oi", 0)) for s in all_strikes if s < spot_price]

    # Calculate thresholds
    call_values = [oi for _, oi in call_ois]
    put_values = [oi for _, oi in put_ois]

    call_threshold = sorted(call_values)[int(len(call_values) * percentile_threshold / 100)] if call_values else 0
    put_threshold = sorted(put_values)[int(len(put_values) * percentile_threshold / 100)] if put_values else 0

    # Find clusters
    resistance_clusters = [(s, oi) for s, oi in call_ois if oi >= call_threshold]
    support_clusters = [(s, oi) for s, oi in put_ois if oi >= put_threshold]

    # Sort by OI (strongest first)
    resistance_clusters.sort(key=lambda x: x[1], reverse=True)
    support_clusters.sort(key=lambda x: x[1], reverse=True)

    return {
        "resistance": [{"strike": s, "oi": oi} for s, oi in resistance_clusters[:5]],
        "support": [{"strike": s, "oi": oi} for s, oi in support_clusters[:5]],
        "strongest_resistance": resistance_clusters[0][0] if resistance_clusters else None,
        "strongest_support": support_clusters[0][0] if support_clusters else None
    }


def calculate_trade_setup(strikes_data: dict, spot_price: float, verdict: str,
                          max_pain: int = None,
                          price_history: Optional[List[dict]] = None) -> Optional[dict]:
    """
    Calculate option buyer trade setup with premium-based entry/SL/targets.

    For OPTIONS BUYERS:
    - Strike selection priority: ITM > ATM > OTM (for better delta/intrinsic value)
    - Entry: Current premium (LTP)
    - SL: 20% below entry premium
    - T1: Entry + risk (1:1 R:R)
    - T2: Entry + 2*risk (1:2 R:R)

    Args:
        strikes_data: Dict of strike -> {ce_oi, pe_oi, ce_ltp, pe_ltp, ...}
        spot_price: Current spot price
        verdict: Current market verdict (bullish/bearish)
        max_pain: Pre-calculated max pain strike
        price_history: List of recent price data [{spot_price: ...}, ...]

    Returns:
        Dict with option buyer setup or None if no valid strike found
    """
    clusters = find_oi_clusters(strikes_data, spot_price)

    support = clusters.get("strongest_support")
    resistance = clusters.get("strongest_resistance")

    if max_pain is None:
        max_pain = calculate_max_pain(strikes_data)

    is_bullish = "bull" in verdict.lower()

    # Calculate swing high/low from price history (for spot reference)
    swing_low = spot_price
    swing_high = spot_price

    if price_history and len(price_history) >= 2:
        prices = [p.get('spot_price', spot_price) for p in price_history]
        swing_low = min(prices)
        swing_high = max(prices)

    # Find ATM strike and nearby strikes for option selection
    all_strikes = sorted(strikes_data.keys())
    if not all_strikes:
        return None

    atm_strike = find_atm_strike(spot_price, all_strikes)
    try:
        atm_idx = all_strikes.index(atm_strike)
    except ValueError:
        atm_idx = len(all_strikes) // 2

    # Select option to buy with priority: ITM > ATM > OTM
    strike_to_buy = None
    premium = 0.0
    strike_moneyness = None
    option_type = None

    if is_bullish:
        option_type = "CE"
        # For calls: ITM = below spot, OTM = above spot
        itm_strike = all_strikes[atm_idx - 1] if atm_idx > 0 else None
        otm_strike = all_strikes[atm_idx + 1] if atm_idx < len(all_strikes) - 1 else None

        # Try ITM first, then ATM, then OTM
        candidates = [(itm_strike, "ITM"), (atm_strike, "ATM"), (otm_strike, "OTM")]
        for strike, moneyness in candidates:
            if strike and strikes_data.get(strike, {}).get("ce_ltp", 0) > 0:
                strike_to_buy = strike
                premium = strikes_data[strike]["ce_ltp"]
                strike_moneyness = moneyness
                break
    else:
        option_type = "PE"
        # For puts: ITM = above spot, OTM = below spot
        itm_strike = all_strikes[atm_idx + 1] if atm_idx < len(all_strikes) - 1 else None
        otm_strike = all_strikes[atm_idx - 1] if atm_idx > 0 else None

        # Try ITM first, then ATM, then OTM
        candidates = [(itm_strike, "ITM"), (atm_strike, "ATM"), (otm_strike, "OTM")]
        for strike, moneyness in candidates:
            if strike and strikes_data.get(strike, {}).get("pe_ltp", 0) > 0:
                strike_to_buy = strike
                premium = strikes_data[strike]["pe_ltp"]
                strike_moneyness = moneyness
                break

    # If no valid strike found, return None
    if strike_to_buy is None or premium <= 0:
        return None

    # Calculate IV-based dynamic SL percentage (10-18% range)
    sl_pct = calculate_dynamic_sl_pct(strikes_data, strike_to_buy, option_type)
    risk = premium * sl_pct
    sl_premium = premium - risk
    t1_premium = premium + risk        # 1:1 R:R
    t2_premium = premium + (risk * 2)  # 1:2 R:R

    # Get IV for the selected strike (for tracking)
    strike_iv = strikes_data.get(strike_to_buy, {}).get(
        'ce_iv' if option_type == 'CE' else 'pe_iv', 0
    )

    return {
        # Option buyer setup
        "direction": "BUY_CALL" if is_bullish else "BUY_PUT",
        "strike": strike_to_buy,
        "option_type": option_type,
        "moneyness": strike_moneyness,
        "entry_premium": round(premium, 2),
        "sl_premium": round(sl_premium, 2),
        "target1_premium": round(t1_premium, 2),
        "target2_premium": round(t2_premium, 2),
        "risk_points": round(risk, 2),
        "risk_pct": round(sl_pct * 100, 1),
        "risk_reward_t1": 1.0,
        "risk_reward_t2": 2.0,
        "iv_at_strike": round(strike_iv, 2),
        # Spot reference (for context)
        "spot_price": round(spot_price, 2),
        "support_ref": support,
        "resistance_ref": resistance,
        "max_pain": max_pain,
        "swing_low": round(swing_low, 2),
        "swing_high": round(swing_high, 2)
    }


def calculate_signal_confidence(combined_score: float, iv_skew: float,
                                volume_pcr: float, oi_pcr: float,
                                max_pain: int, spot_price: float,
                                confirmation_status: str,
                                vix: float = 0.0,
                                futures_oi_change: float = 0.0) -> float:
    """
    Calculate overall signal confidence based on multiple factors.

    Confidence score from 0 to 100:
    - 0-40: Low confidence (skip trading)
    - 40-60: Moderate confidence
    - 60-80: Good confidence
    - 80-100: High confidence

    Args:
        combined_score: Main OI analysis score
        iv_skew: IV skew value
        volume_pcr: Volume PCR
        oi_pcr: OI PCR
        max_pain: Max pain strike
        spot_price: Current spot
        confirmation_status: Price-OI confirmation status
        vix: India VIX value (volatility index)
        futures_oi_change: Futures OI change for cross-validation

    Returns:
        Confidence score 0-100
    """
    confidence = 50.0  # Start neutral

    # 1. Signal strength (25 points max)
    abs_score = abs(combined_score)
    if abs_score >= 40:
        confidence += 25
    elif abs_score >= 25:
        confidence += 15
    elif abs_score >= 10:
        confidence += 5
    else:
        confidence -= 10  # Weak signal penalty

    # 2. IV Skew alignment (15 points max)
    is_bullish_signal = combined_score > 0
    iv_bearish = iv_skew > 2  # Put IV significantly higher
    iv_bullish = iv_skew < -2  # Call IV significantly higher

    if (is_bullish_signal and iv_bullish) or (not is_bullish_signal and iv_bearish):
        confidence += 15  # IV confirms OI signal
    elif (is_bullish_signal and iv_bearish) or (not is_bullish_signal and iv_bullish):
        confidence -= 10  # IV contradicts OI signal

    # 3. Volume PCR alignment (10 points max)
    # High volume PCR (>1.2) = More put trading = Bullish (puts being sold)
    vol_bullish = volume_pcr > 1.2
    vol_bearish = volume_pcr < 0.8

    if (is_bullish_signal and vol_bullish) or (not is_bullish_signal and vol_bearish):
        confidence += 10
    elif (is_bullish_signal and vol_bearish) or (not is_bullish_signal and vol_bullish):
        confidence -= 5

    # 4. Max Pain proximity (10 points max)
    # Price gravitates toward max pain near expiry
    distance_to_max_pain = abs(spot_price - max_pain)
    distance_pct = (distance_to_max_pain / spot_price) * 100

    if distance_pct < 0.5:
        confidence += 10  # Very close to max pain
    elif distance_pct < 1.0:
        confidence += 5
    elif distance_pct > 2.0:
        confidence -= 5  # Far from max pain

    # 5. Confirmation status (15 points max)
    if confirmation_status == "CONFIRMED":
        confidence += 15
    elif confirmation_status == "REVERSAL_ALERT":
        confidence += 10  # Strong divergence is actionable
    elif confirmation_status == "CONFLICT":
        confidence -= 15
    # NEUTRAL: no change

    # 6. VIX adjustment (±20 points)
    # High VIX = volatile market = less reliable OI signals
    if vix > 0:
        if vix > 25:
            confidence -= 20  # Very high volatility - unreliable signals
        elif vix > 20:
            confidence -= 10  # High volatility - reduce confidence
        elif vix < 12:
            confidence += 5   # Low VIX = stable = more reliable signals

    # 7. Futures OI confirmation (±15 points)
    # Futures OI change in same direction as options signal = strong confirmation
    if futures_oi_change != 0:
        futures_bullish = futures_oi_change > 0  # Rising futures OI = bullish
        if (is_bullish_signal and futures_bullish) or (not is_bullish_signal and not futures_bullish):
            confidence += 15  # Strong confirmation from futures
        elif (is_bullish_signal and not futures_bullish) or (not is_bullish_signal and futures_bullish):
            confidence -= 10  # Futures disagree with options signal

    # Clamp to 0-100
    return max(0, min(100, confidence))


def detect_trap(strikes_data: dict, spot_price: float, price_direction: str,
                oi_direction: str, strength: str) -> Optional[dict]:
    """
    Detect potential bull trap or bear trap conditions.

    Bull Trap: Price breaks up but smart money is selling (adding calls above)
    Bear Trap: Price breaks down but smart money is buying (adding puts below)

    Args:
        strikes_data: OI data
        spot_price: Current spot
        price_direction: "rising" or "falling"
        oi_direction: "bullish" or "bearish" from OI analysis
        strength: Signal strength

    Returns:
        Dict with trap type and confidence or None
    """
    clusters = find_oi_clusters(strikes_data, spot_price)

    # Bull trap: Price rising but OI is bearish (call OI building above)
    if price_direction == "rising" and oi_direction == "bearish" and strength == "strong":
        # Check if call OI is building aggressively above current price
        call_clusters = clusters.get("resistance", [])
        if call_clusters:
            highest_call_cluster = call_clusters[0]
            if highest_call_cluster["strike"] < spot_price * 1.01:  # Close to spot
                return {
                    "type": "BULL_TRAP",
                    "confidence": 70,
                    "message": "Price rising but heavy call writing above - potential bull trap",
                    "key_strike": highest_call_cluster["strike"]
                }

    # Bear trap: Price falling but OI is bullish (put OI building below)
    if price_direction == "falling" and oi_direction == "bullish" and strength == "strong":
        put_clusters = clusters.get("support", [])
        if put_clusters:
            highest_put_cluster = put_clusters[0]
            if highest_put_cluster["strike"] > spot_price * 0.99:  # Close to spot
                return {
                    "type": "BEAR_TRAP",
                    "confidence": 70,
                    "message": "Price falling but heavy put writing below - potential bear trap",
                    "key_strike": highest_put_cluster["strike"]
                }

    return None


def get_itm_strikes(atm_strike: int, all_strikes: list, num_strikes: int = 3) -> Tuple[list, list]:
    """
    Get ITM strikes on both sides of ATM.

    For ITM options:
    - ITM Calls: Strikes BELOW spot (calls with intrinsic value)
    - ITM Puts: Strikes ABOVE spot (puts with intrinsic value)

    ITM Writer Dynamics (Tug-of-War "Pulling" Force):
    - ITM Call writers (below spot) want price to fall back → Bearish pressure
      (They sold calls that are now losing money; want to minimize loss)
    - ITM Put writers (above spot) want price to rise back → Bullish pressure
      (They sold puts that are now losing money; want to minimize loss)

    Args:
        atm_strike: The ATM strike price
        all_strikes: Sorted list of all available strikes
        num_strikes: Number of ITM strikes to consider on each side

    Returns:
        Tuple of (itm_call_strikes, itm_put_strikes)
    """
    sorted_strikes = sorted(all_strikes)

    try:
        atm_idx = sorted_strikes.index(atm_strike)
    except ValueError:
        atm_idx = min(range(len(sorted_strikes)),
                     key=lambda i: abs(sorted_strikes[i] - atm_strike))

    # ITM Calls: strikes BELOW ATM (indices before ATM)
    start_idx = max(0, atm_idx - num_strikes)
    itm_calls = sorted_strikes[start_idx:atm_idx]

    # ITM Puts: strikes ABOVE ATM (indices after ATM)
    itm_puts = sorted_strikes[atm_idx + 1:atm_idx + 1 + num_strikes]

    return itm_calls, itm_puts


def analyze_tug_of_war(strikes_data: dict, spot_price: float,
                        num_strikes: int = 3,
                        momentum_score: Optional[float] = None,
                        price_history: Optional[List[dict]] = None,
                        vix: float = 0.0,
                        futures_oi_change: float = 0.0,
                        prev_oi_changes: Optional[List[tuple]] = None,
                        prev_strikes_data: Optional[dict] = None,
                        total_oi_weight: float = 0.15) -> dict:
    """
    Perform enhanced tug-of-war analysis with OTM/ITM zone separation.

    Enhanced Analysis Logic:
    - 4 Zones: OTM Puts (below), ITM Calls (below), OTM Calls (above), ITM Puts (above)
    - Force = (0.85 × OI Change) + (0.15 × Total OI / scale_factor)
    - Put Strength = OTM Put Force / (ITM Call Force + OTM Call Force)
    - Call Strength = OTM Call Force / (ITM Put Force + OTM Put Force)
    - Market Direction based on relative strength comparison

    Args:
        strikes_data: Dict of strike -> {ce_oi, ce_oi_change, pe_oi, pe_oi_change}
        spot_price: Current underlying spot price
        num_strikes: Number of strikes to analyze on each side
        momentum_score: Pre-calculated momentum score (-100 to +100)
        price_history: List of recent price data for momentum calculation
        vix: India VIX value for volatility context
        futures_oi_change: NIFTY futures OI change for cross-validation
        prev_oi_changes: List of (call_oi_change, put_oi_change) tuples for acceleration
        prev_strikes_data: Previous snapshot's strikes data for premium momentum
        total_oi_weight: Weight for total OI in force calculation (default 0.15)

    Returns:
        dict with analysis results including verdict and zone-separated data
    """
    all_strikes = sorted(strikes_data.keys())

    if not all_strikes:
        return {
            "error": "No strike data available",
            "verdict": "No Data"
        }

    # Find ATM strike
    atm_strike = find_atm_strike(spot_price, all_strikes)
    try:
        atm_idx = all_strikes.index(atm_strike)
    except ValueError:
        atm_idx = min(range(len(all_strikes)),
                     key=lambda i: abs(all_strikes[i] - atm_strike))

    # Get strikes below and above spot
    below_spot_strikes = all_strikes[max(0, atm_idx - num_strikes):atm_idx]
    above_spot_strikes = all_strikes[atm_idx + 1:atm_idx + 1 + num_strikes]

    # Calculate scale factor for Total OI (normalize to OI change magnitude)
    # Use average OI change as baseline to scale Total OI contribution
    all_oi_changes = []
    all_total_oi = []
    for strike in below_spot_strikes + above_spot_strikes:
        data = strikes_data.get(strike, {})
        all_oi_changes.extend([abs(data.get("pe_oi_change", 0)), abs(data.get("ce_oi_change", 0))])
        all_total_oi.extend([data.get("pe_oi", 0), data.get("ce_oi", 0)])

    avg_oi_change = sum(all_oi_changes) / len(all_oi_changes) if all_oi_changes else 1
    avg_total_oi = sum(all_total_oi) / len(all_total_oi) if all_total_oi else 1
    scale_factor = avg_total_oi / avg_oi_change if avg_oi_change > 0 else 1

    oi_change_weight = 1.0 - total_oi_weight

    # Helper function to calculate force with Total OI weighting
    def calculate_force(oi_change: float, total_oi: float, conviction: float) -> float:
        """Calculate force = conviction × ((1-w) × OI Change + w × Total OI / scale)"""
        weighted_change = oi_change * oi_change_weight
        weighted_total = (total_oi / scale_factor) * total_oi_weight if scale_factor > 0 else 0
        return conviction * (weighted_change + weighted_total)

    # ========================================
    # OTM PUTS - Below Spot (Support Zone)
    # Put writers selling puts = bullish support
    # ========================================
    otm_puts_data = []
    otm_puts_total_oi = 0
    otm_puts_total_oi_change = 0
    otm_puts_total_force = 0

    for strike in below_spot_strikes:
        data = strikes_data.get(strike, {})
        pe_oi = data.get("pe_oi", 0)
        pe_oi_change = data.get("pe_oi_change", 0)
        pe_volume = data.get("pe_volume", 0)

        put_conviction = calculate_conviction_multiplier(pe_volume, pe_oi_change)
        put_force = calculate_force(pe_oi_change, pe_oi, put_conviction)

        otm_puts_total_oi += pe_oi
        otm_puts_total_oi_change += pe_oi_change
        otm_puts_total_force += put_force

        otm_puts_data.append({
            "strike": strike,
            "put_oi": pe_oi,
            "put_oi_change": pe_oi_change,
            "put_force": round(put_force, 0),
            "conviction": put_conviction
        })

    # ========================================
    # ITM CALLS - Below Spot (Same strikes as OTM Puts)
    # Trapped longs (calls in the money)
    # ========================================
    itm_calls_data = []
    itm_calls_total_oi = 0
    itm_calls_total_oi_change = 0
    itm_calls_total_force = 0

    for strike in below_spot_strikes:
        data = strikes_data.get(strike, {})
        ce_oi = data.get("ce_oi", 0)
        ce_oi_change = data.get("ce_oi_change", 0)
        ce_volume = data.get("ce_volume", 0)

        call_conviction = calculate_conviction_multiplier(ce_volume, ce_oi_change)
        call_force = calculate_force(ce_oi_change, ce_oi, call_conviction)

        itm_calls_total_oi += ce_oi
        itm_calls_total_oi_change += ce_oi_change
        itm_calls_total_force += call_force

        itm_calls_data.append({
            "strike": strike,
            "call_oi": ce_oi,
            "call_oi_change": ce_oi_change,
            "call_force": round(call_force, 0),
            "conviction": call_conviction
        })

    # ========================================
    # OTM CALLS - Above Spot (Resistance Zone)
    # Call writers selling calls = bearish resistance
    # ========================================
    otm_calls_data = []
    otm_calls_total_oi = 0
    otm_calls_total_oi_change = 0
    otm_calls_total_force = 0

    for strike in above_spot_strikes:
        data = strikes_data.get(strike, {})
        ce_oi = data.get("ce_oi", 0)
        ce_oi_change = data.get("ce_oi_change", 0)
        ce_volume = data.get("ce_volume", 0)

        call_conviction = calculate_conviction_multiplier(ce_volume, ce_oi_change)
        call_force = calculate_force(ce_oi_change, ce_oi, call_conviction)

        otm_calls_total_oi += ce_oi
        otm_calls_total_oi_change += ce_oi_change
        otm_calls_total_force += call_force

        otm_calls_data.append({
            "strike": strike,
            "call_oi": ce_oi,
            "call_oi_change": ce_oi_change,
            "call_force": round(call_force, 0),
            "conviction": call_conviction
        })

    # ========================================
    # ITM PUTS - Above Spot (Same strikes as OTM Calls)
    # Trapped shorts (puts in the money)
    # ========================================
    itm_puts_data = []
    itm_puts_total_oi = 0
    itm_puts_total_oi_change = 0
    itm_puts_total_force = 0

    for strike in above_spot_strikes:
        data = strikes_data.get(strike, {})
        pe_oi = data.get("pe_oi", 0)
        pe_oi_change = data.get("pe_oi_change", 0)
        pe_volume = data.get("pe_volume", 0)

        put_conviction = calculate_conviction_multiplier(pe_volume, pe_oi_change)
        put_force = calculate_force(pe_oi_change, pe_oi, put_conviction)

        itm_puts_total_oi += pe_oi
        itm_puts_total_oi_change += pe_oi_change
        itm_puts_total_force += put_force

        itm_puts_data.append({
            "strike": strike,
            "put_oi": pe_oi,
            "put_oi_change": pe_oi_change,
            "put_force": round(put_force, 0),
            "conviction": put_conviction
        })

    # ========================================
    # STRENGTH CALCULATIONS
    # Put Strength = OTM Put Force / (ITM Call Force + OTM Call Force)
    # Call Strength = OTM Call Force / (ITM Put Force + OTM Put Force)
    # ========================================

    # Put Strength (Support Power)
    put_numerator = abs(otm_puts_total_force)
    put_denominator = abs(itm_calls_total_force) + abs(otm_calls_total_force)
    put_ratio = put_numerator / put_denominator if put_denominator > 0 else 1.0
    # Normalize to -100 to +100 score (ratio > 1 = positive, < 1 = negative)
    put_strength_score = (put_ratio - 1) * 50  # 2:1 ratio = +50, 0.5:1 = -25
    put_strength_score = max(-100, min(100, put_strength_score))

    # Call Strength (Resistance Power)
    call_numerator = abs(otm_calls_total_force)
    call_denominator = abs(itm_puts_total_force) + abs(otm_puts_total_force)
    call_ratio = call_numerator / call_denominator if call_denominator > 0 else 1.0
    call_strength_score = (call_ratio - 1) * 50
    call_strength_score = max(-100, min(100, call_strength_score))

    # Net Strength: positive = bullish (put strength > call strength)
    net_strength = put_strength_score - call_strength_score

    # Determine direction from strength comparison
    if net_strength > 15:
        strength_direction = "BULLISH"
    elif net_strength < -15:
        strength_direction = "BEARISH"
    else:
        strength_direction = "NEUTRAL"

    # ========================================
    # BELOW/ABOVE SPOT ZONE DATA (for backward compatibility)
    # ========================================
    below_spot_data = []
    total_below_bullish = 0
    total_below_bearish = 0
    total_below_put_oi = 0
    total_below_call_oi = 0
    total_below_volume = 0

    for strike in below_spot_strikes:
        data = strikes_data.get(strike, {})
        pe_oi = data.get("pe_oi", 0)
        pe_oi_change = data.get("pe_oi_change", 0)
        ce_oi = data.get("ce_oi", 0)
        ce_oi_change = data.get("ce_oi_change", 0)
        pe_volume = data.get("pe_volume", 0)
        ce_volume = data.get("ce_volume", 0)

        put_conviction = calculate_conviction_multiplier(pe_volume, pe_oi_change)
        call_conviction = calculate_conviction_multiplier(ce_volume, ce_oi_change)

        weighted_put_force = calculate_force(pe_oi_change, pe_oi, put_conviction)
        weighted_call_force = calculate_force(ce_oi_change, ce_oi, call_conviction)
        net_force = weighted_put_force - weighted_call_force

        total_below_bullish += weighted_put_force
        total_below_bearish += weighted_call_force
        total_below_put_oi += pe_oi
        total_below_call_oi += ce_oi
        total_below_volume += pe_volume + ce_volume

        below_spot_data.append({
            "strike": strike,
            "put_oi": pe_oi,
            "put_oi_change": pe_oi_change,
            "call_oi": ce_oi,
            "call_oi_change": ce_oi_change,
            "bullish_force": round(weighted_put_force, 0),
            "bearish_force": round(weighted_call_force, 0),
            "net_force": round(net_force, 0),
            "put_conviction": put_conviction,
            "call_conviction": call_conviction
        })

    below_net_change = total_below_bullish - total_below_bearish
    max_below = max(abs(total_below_bullish), abs(total_below_bearish), 1)
    below_spot_score = (below_net_change / max_below) * 100

    above_spot_data = []
    total_above_bullish = 0
    total_above_bearish = 0
    total_above_put_oi = 0
    total_above_call_oi = 0
    total_above_volume = 0

    for strike in above_spot_strikes:
        data = strikes_data.get(strike, {})
        pe_oi = data.get("pe_oi", 0)
        pe_oi_change = data.get("pe_oi_change", 0)
        ce_oi = data.get("ce_oi", 0)
        ce_oi_change = data.get("ce_oi_change", 0)
        pe_volume = data.get("pe_volume", 0)
        ce_volume = data.get("ce_volume", 0)

        put_conviction = calculate_conviction_multiplier(pe_volume, pe_oi_change)
        call_conviction = calculate_conviction_multiplier(ce_volume, ce_oi_change)

        weighted_put_force = calculate_force(pe_oi_change, pe_oi, put_conviction)
        weighted_call_force = calculate_force(ce_oi_change, ce_oi, call_conviction)
        net_force = weighted_put_force - weighted_call_force

        total_above_bullish += weighted_put_force
        total_above_bearish += weighted_call_force
        total_above_put_oi += pe_oi
        total_above_call_oi += ce_oi
        total_above_volume += pe_volume + ce_volume

        above_spot_data.append({
            "strike": strike,
            "put_oi": pe_oi,
            "put_oi_change": pe_oi_change,
            "call_oi": ce_oi,
            "call_oi_change": ce_oi_change,
            "bullish_force": round(weighted_put_force, 0),
            "bearish_force": round(weighted_call_force, 0),
            "net_force": round(net_force, 0),
            "put_conviction": put_conviction,
            "call_conviction": call_conviction
        })

    above_net_change = total_above_bullish - total_above_bearish
    max_above = max(abs(total_above_bullish), abs(total_above_bearish), 1)
    above_spot_score = (above_net_change / max_above) * 100

    # ========================================
    # COMBINED SCORE CALCULATION
    # ========================================

    # Calculate momentum if price_history provided
    if momentum_score is None and price_history:
        momentum_score = calculate_price_momentum(price_history)
    elif momentum_score is None:
        momentum_score = 0.0

    # Calculate price direction from history
    price_change_pct = 0.0
    if price_history and len(price_history) >= 2:
        current = price_history[-1]['spot_price']
        past = price_history[0]['spot_price']
        if past > 0:
            price_change_pct = ((current - past) / past) * 100

    # Preliminary combined score - blend zone average with net strength
    zone_average = (below_spot_score + above_spot_score) / 2
    # Weight the net_strength into the combined score (30% weight)
    zone_average = (zone_average * 0.7) + (net_strength * 0.3)

    # Detect if OI and price are diverging
    preliminary_oi_direction = "bullish" if zone_average > 0 else "bearish" if zone_average < 0 else "neutral"
    price_direction = "rising" if price_change_pct > 0.05 else "falling" if price_change_pct < -0.05 else "flat"

    is_diverging = (
        (preliminary_oi_direction == "bullish" and price_direction == "falling") or
        (preliminary_oi_direction == "bearish" and price_direction == "rising")
    )

    # Dynamic momentum weighting
    if momentum_score != 0.0:
        if is_diverging:
            # Trust price momentum more when diverging
            momentum_weight = 0.45
            zone_weight = 0.55
        else:
            # Normal case
            momentum_weight = 0.20
            zone_weight = 0.80
    else:
        momentum_weight = 0.0
        zone_weight = 1.0

    # Detect market regime for context
    market_regime = detect_market_regime(price_history or [], momentum_score)

    # Combined score
    combined_score = (zone_weight * zone_average) + (momentum_weight * momentum_score)

    # ========================================
    # OI ACCELERATION ADJUSTMENT
    # ========================================
    total_call_oi_change = total_below_bearish + total_above_bearish
    total_put_oi_change = total_below_bullish + total_above_bullish

    oi_acceleration = calculate_oi_acceleration(
        prev_oi_changes or [],
        total_call_oi_change,
        total_put_oi_change
    )

    # Adjust combined score based on OI phase and momentum
    if oi_acceleration["phase"] == "unwinding" and momentum_score > 15:
        combined_score += 15
        oi_acceleration["adjustment"] = "+15 (short covering detected)"
    elif oi_acceleration["phase"] == "unwinding" and momentum_score < -15:
        combined_score -= 15
        oi_acceleration["adjustment"] = "-15 (profit booking detected)"
    elif oi_acceleration["phase"] == "accumulation":
        combined_score += 10
        oi_acceleration["adjustment"] = "+10 (bullish accumulation)"
    elif oi_acceleration["phase"] == "distribution":
        combined_score -= 10
        oi_acceleration["adjustment"] = "-10 (bearish distribution)"
    else:
        oi_acceleration["adjustment"] = "0 (stable phase)"

    # ========================================
    # PREMIUM MOMENTUM ADJUSTMENT
    # ========================================
    premium_momentum = calculate_premium_momentum(strikes_data, prev_strikes_data, spot_price)
    pm_score = premium_momentum["premium_momentum_score"]

    if combined_score < -10 and pm_score > 20:
        adjustment = pm_score * 0.3
        combined_score += adjustment
        premium_momentum["score_adjustment"] = f"+{adjustment:.1f} (bullish premium vs bearish OI)"
    elif combined_score > 10 and pm_score < -20:
        adjustment = pm_score * 0.3
        combined_score += adjustment
        premium_momentum["score_adjustment"] = f"{adjustment:.1f} (bearish premium vs bullish OI)"
    else:
        premium_momentum["score_adjustment"] = "0 (aligned)"

    # ========================================
    # VERDICT DETERMINATION
    # ========================================
    if combined_score > 40:
        verdict = "Bulls Strongly Winning"
        strength = "strong"
    elif combined_score > 15:
        verdict = "Bulls Winning"
        strength = "moderate"
    elif combined_score > 0:
        verdict = "Slightly Bullish"
        strength = "weak"
    elif combined_score < -40:
        verdict = "Bears Strongly Winning"
        strength = "strong"
    elif combined_score < -15:
        verdict = "Bears Winning"
        strength = "moderate"
    elif combined_score < 0:
        verdict = "Slightly Bearish"
        strength = "weak"
    else:
        verdict = "Neutral"
        strength = "none"

    # ========================================
    # ADDITIONAL METRICS
    # ========================================

    # Total OI for PCR calculation
    total_put_oi = total_below_put_oi + total_above_put_oi
    total_call_oi = total_below_call_oi + total_above_call_oi
    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0

    # Volume PCR
    total_volume = total_below_volume + total_above_volume
    volume_pcr = (total_below_bullish + total_above_bullish) / max(total_below_bearish + total_above_bearish, 1)

    # Average conviction
    all_below_convictions = [d['put_conviction'] for d in below_spot_data] + [d['call_conviction'] for d in below_spot_data]
    all_above_convictions = [d['put_conviction'] for d in above_spot_data] + [d['call_conviction'] for d in above_spot_data]
    all_convictions = all_below_convictions + all_above_convictions
    avg_conviction = sum(all_convictions) / len(all_convictions) if all_convictions else 1.0

    # Confirmation status
    oi_direction = "bullish" if combined_score > 0 else "bearish" if combined_score < 0 else "neutral"

    if oi_direction == "neutral" or price_direction == "flat":
        confirmation_status = "NEUTRAL"
        confirmation_message = "Waiting for clearer signals"
    elif (oi_direction == "bullish" and price_direction == "rising") or \
         (oi_direction == "bearish" and price_direction == "falling"):
        confirmation_status = "CONFIRMED"
        confirmation_message = f"OI {oi_direction} confirmed by {price_direction} price"
    elif strength == "strong" and \
         ((oi_direction == "bullish" and price_direction == "falling") or \
          (oi_direction == "bearish" and price_direction == "rising")):
        confirmation_status = "REVERSAL_ALERT"
        confirmation_message = f"Strong {oi_direction} vs {price_direction} price - potential reversal!"
    else:
        confirmation_status = "CONFLICT"
        confirmation_message = f"OI {oi_direction} but price {price_direction} - wait for alignment"

    # Calculate new metrics: IV Skew, Max Pain, OI Clusters
    iv_skew = calculate_iv_skew(strikes_data, spot_price, num_strikes)
    max_pain = calculate_max_pain(strikes_data)
    oi_clusters = find_oi_clusters(strikes_data, spot_price)

    # Calculate trade setup
    trade_setup = calculate_trade_setup(strikes_data, spot_price, verdict, max_pain, price_history)

    # Calculate signal confidence
    signal_confidence = calculate_signal_confidence(
        combined_score, iv_skew, volume_pcr, pcr,
        max_pain, spot_price, confirmation_status,
        vix=vix, futures_oi_change=futures_oi_change
    )

    # Detect potential traps
    trap_warning = detect_trap(strikes_data, spot_price, price_direction, oi_direction, strength)

    return {
        "spot_price": spot_price,
        "atm_strike": atm_strike,

        # ========================================
        # NEW: OTM/ITM Zone Data (4 zones)
        # ========================================
        "otm_puts": {
            "strikes": otm_puts_data,
            "total_oi": otm_puts_total_oi,
            "total_oi_change": round(otm_puts_total_oi_change, 0),
            "total_force": round(otm_puts_total_force, 0)
        },
        "itm_calls": {
            "strikes": itm_calls_data,
            "total_oi": itm_calls_total_oi,
            "total_oi_change": round(itm_calls_total_oi_change, 0),
            "total_force": round(itm_calls_total_force, 0)
        },
        "otm_calls": {
            "strikes": otm_calls_data,
            "total_oi": otm_calls_total_oi,
            "total_oi_change": round(otm_calls_total_oi_change, 0),
            "total_force": round(otm_calls_total_force, 0)
        },
        "itm_puts": {
            "strikes": itm_puts_data,
            "total_oi": itm_puts_total_oi,
            "total_oi_change": round(itm_puts_total_oi_change, 0),
            "total_force": round(itm_puts_total_force, 0)
        },

        # ========================================
        # NEW: Strength Calculations
        # ========================================
        "strength_analysis": {
            "put_strength": {
                "numerator": round(put_numerator, 0),
                "denominator": round(put_denominator, 0),
                "ratio": round(put_ratio, 2),
                "score": round(put_strength_score, 1)
            },
            "call_strength": {
                "numerator": round(call_numerator, 0),
                "denominator": round(call_denominator, 0),
                "ratio": round(call_ratio, 2),
                "score": round(call_strength_score, 1)
            },
            "direction": strength_direction,
            "net_strength": round(net_strength, 1)
        },

        # ========================================
        # Below/Above Spot Zone Data (backward compatibility)
        # ========================================
        "below_spot": {
            "strikes": below_spot_data,
            "total_bullish_force": round(total_below_bullish, 0),
            "total_bearish_force": round(total_below_bearish, 0),
            "net_force": round(below_net_change, 0),
            "score": round(below_spot_score, 1)
        },
        "above_spot": {
            "strikes": above_spot_data,
            "total_bullish_force": round(total_above_bullish, 0),
            "total_bearish_force": round(total_above_bearish, 0),
            "net_force": round(above_net_change, 0),
            "score": round(above_spot_score, 1)
        },

        # OI totals
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "call_oi_change": round(total_call_oi_change, 0),
        "put_oi_change": round(total_put_oi_change, 0),
        "net_oi_change": round(total_put_oi_change - total_call_oi_change, 0),
        "combined_score": round(combined_score, 1),
        "pcr": round(pcr, 2),
        "verdict": verdict,
        "strength": strength,

        # Volume metrics
        "total_call_volume": 0,
        "total_put_volume": 0,
        "volume_pcr": round(volume_pcr, 2),
        "avg_call_conviction": round(avg_conviction, 2),
        "avg_put_conviction": round(avg_conviction, 2),

        # Momentum data
        "momentum_score": round(momentum_score, 1),
        "price_change_pct": round(price_change_pct, 2),

        # Zone scores for breakdown display
        "below_spot_score": round(below_spot_score, 1),
        "above_spot_score": round(above_spot_score, 1),
        "weights": {
            "below_spot": zone_weight / 2,
            "above_spot": zone_weight / 2,
            "momentum": momentum_weight,
            "oi_change": oi_change_weight,
            "total_oi": total_oi_weight
        },
        "confirmation_status": confirmation_status,
        "confirmation_message": confirmation_message,

        # Analysis metrics
        "iv_skew": round(iv_skew, 2),
        "max_pain": max_pain,
        "oi_clusters": oi_clusters,
        "signal_confidence": round(signal_confidence, 1),
        "trade_setup": trade_setup,
        "trap_warning": trap_warning,

        # Market context
        "vix": round(vix, 2) if vix else 0.0,
        "futures_oi_change": round(futures_oi_change, 0) if futures_oi_change else 0,
        "is_diverging": is_diverging,

        # OI acceleration
        "oi_acceleration": oi_acceleration,
        # Premium momentum
        "premium_momentum": premium_momentum,
        # Market regime
        "market_regime": market_regime
    }


def format_analysis_summary(analysis: dict) -> str:
    """Format analysis results for display."""
    if "error" in analysis:
        return f"Error: {analysis['error']}"

    lines = [
        f"=== OI Tug-of-War Analysis ===",
        f"Spot Price: {analysis['spot_price']:,.2f}",
        f"ATM Strike: {analysis['atm_strike']}",
        f"",
        f"--- OTM Calls (Bearish Pressure) ---",
    ]

    for call in analysis['otm_calls']:
        lines.append(f"  {call['strike']}: OI={call['oi']:,} (Change: {call['oi_change']:+,})")

    lines.append(f"  Total: {analysis['total_call_oi']:,} (Change: {analysis['call_oi_change']:+,})")
    lines.append("")
    lines.append("--- OTM Puts (Bullish Pressure) ---")

    for put in analysis['otm_puts']:
        lines.append(f"  {put['strike']}: OI={put['oi']:,} (Change: {put['oi_change']:+,})")

    lines.append(f"  Total: {analysis['total_put_oi']:,} (Change: {analysis['put_oi_change']:+,})")
    lines.append("")
    lines.append(f"Net OI Change: {analysis['net_oi_change']:+,}")
    lines.append(f"PCR: {analysis['pcr']}")
    lines.append("")
    lines.append(f">>> VERDICT: {analysis['verdict']} <<<")

    return "\n".join(lines)


if __name__ == "__main__":
    # Test with sample data including LTP
    sample_strikes = {
        23900: {"ce_oi": 150000, "ce_oi_change": 8000, "pe_oi": 200000, "pe_oi_change": 25000,
                "ce_ltp": 210.50, "pe_ltp": 85.25, "ce_iv": 12.5, "pe_iv": 11.8},
        23950: {"ce_oi": 180000, "ce_oi_change": 12000, "pe_oi": 170000, "pe_oi_change": 18000,
                "ce_ltp": 175.00, "pe_ltp": 98.75, "ce_iv": 12.0, "pe_iv": 12.2},
        24000: {"ce_oi": 250000, "ce_oi_change": 15000, "pe_oi": 220000, "pe_oi_change": 20000,
                "ce_ltp": 142.25, "pe_ltp": 118.50, "ce_iv": 11.5, "pe_iv": 11.9},
        24050: {"ce_oi": 280000, "ce_oi_change": 30000, "pe_oi": 180000, "pe_oi_change": 10000,
                "ce_ltp": 112.00, "pe_ltp": 145.25, "ce_iv": 11.2, "pe_iv": 12.5},
        24100: {"ce_oi": 320000, "ce_oi_change": 35000, "pe_oi": 150000, "pe_oi_change": 8000,
                "ce_ltp": 85.50, "pe_ltp": 178.00, "ce_iv": 11.0, "pe_iv": 13.0},
        24150: {"ce_oi": 290000, "ce_oi_change": 25000, "pe_oi": 120000, "pe_oi_change": 5000,
                "ce_ltp": 62.75, "pe_ltp": 215.50, "ce_iv": 10.8, "pe_iv": 13.5},
        24200: {"ce_oi": 250000, "ce_oi_change": 20000, "pe_oi": 100000, "pe_oi_change": 3000,
                "ce_ltp": 45.00, "pe_ltp": 258.00, "ce_iv": 10.5, "pe_iv": 14.0},
    }

    spot = 24025.50

    # Test strike-level analysis
    print("=" * 50)
    print("TEST 1: Strike-Level Tug-of-War Analysis")
    print("=" * 50)
    analysis = analyze_tug_of_war(sample_strikes, spot)
    print(format_analysis_summary(analysis))

    print(f"\n--- Zone Analysis ---")
    print(f"Below Spot Score: {analysis['below_spot']['score']}")
    print(f"  Bullish Force: {analysis['below_spot']['total_bullish_force']:,}")
    print(f"  Bearish Force: {analysis['below_spot']['total_bearish_force']:,}")
    print(f"  Net Force: {analysis['below_spot']['net_force']:,}")
    print(f"\nAbove Spot Score: {analysis['above_spot']['score']}")
    print(f"  Bullish Force: {analysis['above_spot']['total_bullish_force']:,}")
    print(f"  Bearish Force: {analysis['above_spot']['total_bearish_force']:,}")
    print(f"  Net Force: {analysis['above_spot']['net_force']:,}")

    print(f"\nCombined Score: {analysis['combined_score']}")
    print(f"Weights: Below={analysis['weights']['below_spot']:.0%}, Above={analysis['weights']['above_spot']:.0%}, Momentum={analysis['weights']['momentum']:.0%}")

    # Test with price history (adds momentum)
    print("\n" + "=" * 50)
    print("TEST 2: With Price Momentum")
    print("=" * 50)

    price_history = [
        {"spot_price": 24000.00},
        {"spot_price": 23980.25},
        {"spot_price": 24010.75},
        {"spot_price": 24025.50},
    ]

    analysis_with_history = analyze_tug_of_war(
        sample_strikes, spot,
        price_history=price_history
    )

    print(f"Verdict: {analysis_with_history['verdict']}")
    print(f"Combined Score: {analysis_with_history['combined_score']}")
    print(f"Momentum Score: {analysis_with_history['momentum_score']}")
    print(f"Price Change: {analysis_with_history['price_change_pct']:.2f}%")
    print(f"Confirmation: {analysis_with_history['confirmation_status']} - {analysis_with_history['confirmation_message']}")

    # Test trade setup
    print("\n" + "=" * 50)
    print("TEST 3: Trade Setup")
    print("=" * 50)

    trade = analysis_with_history['trade_setup']
    if trade:
        print(f"\nSpot: {spot}")
        print(f"Verdict: {analysis_with_history['verdict']} (Score: {analysis_with_history['combined_score']})")
        print(f"\n{trade['direction']} Setup:")
        print(f"  Strike: {trade['strike']} {trade['option_type']} ({trade['moneyness']})")
        print(f"  Entry: {trade['entry_premium']} (current premium)")
        print(f"  SL: {trade['sl_premium']} (-{trade['risk_pct']}%, {trade['risk_points']:.2f} pts risk)")
        print(f"  T1: {trade['target1_premium']} (1:{trade['risk_reward_t1']:.0f} R:R)")
        print(f"  T2: {trade['target2_premium']} (1:{trade['risk_reward_t2']:.0f} R:R)")
    else:
        print("No trade setup generated (missing LTP data)")

    # Test bearish scenario
    print("\n" + "=" * 50)
    print("TEST 4: Bearish Scenario")
    print("=" * 50)

    # Modify data to make it bearish (more call OI change)
    bearish_strikes = {
        23900: {"ce_oi": 150000, "ce_oi_change": 25000, "pe_oi": 200000, "pe_oi_change": 8000,
                "ce_ltp": 210.50, "pe_ltp": 85.25},
        23950: {"ce_oi": 180000, "ce_oi_change": 30000, "pe_oi": 170000, "pe_oi_change": 10000,
                "ce_ltp": 175.00, "pe_ltp": 98.75},
        24000: {"ce_oi": 250000, "ce_oi_change": 35000, "pe_oi": 220000, "pe_oi_change": 12000,
                "ce_ltp": 142.25, "pe_ltp": 118.50},
        24050: {"ce_oi": 280000, "ce_oi_change": 40000, "pe_oi": 180000, "pe_oi_change": 8000,
                "ce_ltp": 112.00, "pe_ltp": 145.25},
        24100: {"ce_oi": 320000, "ce_oi_change": 45000, "pe_oi": 150000, "pe_oi_change": 5000,
                "ce_ltp": 85.50, "pe_ltp": 178.00},
        24150: {"ce_oi": 290000, "ce_oi_change": 35000, "pe_oi": 120000, "pe_oi_change": 3000,
                "ce_ltp": 62.75, "pe_ltp": 215.50},
        24200: {"ce_oi": 250000, "ce_oi_change": 30000, "pe_oi": 100000, "pe_oi_change": 2000,
                "ce_ltp": 45.00, "pe_ltp": 258.00},
    }

    analysis_bearish = analyze_tug_of_war(
        bearish_strikes, spot,
        price_history=price_history
    )

    print(f"Verdict: {analysis_bearish['verdict']} (Score: {analysis_bearish['combined_score']})")
    print(f"Below Spot Score: {analysis_bearish['below_spot']['score']}")
    print(f"Above Spot Score: {analysis_bearish['above_spot']['score']}")

    trade_bearish = analysis_bearish['trade_setup']
    if trade_bearish:
        print(f"\n{trade_bearish['direction']} Setup:")
        print(f"  Strike: {trade_bearish['strike']} {trade_bearish['option_type']} ({trade_bearish['moneyness']})")
        print(f"  Entry: {trade_bearish['entry_premium']} (current premium)")
        print(f"  SL: {trade_bearish['sl_premium']} (-{trade_bearish['risk_pct']}%, {trade_bearish['risk_points']:.2f} pts risk)")
    else:
        print("No trade setup generated")
