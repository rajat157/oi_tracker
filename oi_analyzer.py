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

    # Calculate SL/targets based on premium (20% risk)
    sl_pct = 0.20
    risk = premium * sl_pct
    sl_premium = premium - risk
    t1_premium = premium + risk        # 1:1 R:R
    t2_premium = premium + (risk * 2)  # 1:2 R:R

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
                                confirmation_status: str) -> float:
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
                        num_otm_strikes: int = 3,
                        include_atm: bool = False,
                        include_itm: bool = False,
                        momentum_score: Optional[float] = None,
                        price_history: Optional[List[dict]] = None) -> dict:
    """
    Perform tug-of-war analysis on option chain data.

    Analysis Logic:
    - High Call OI addition = Bears writing calls = Bearish pressure
    - High Put OI addition = Bulls writing puts = Bullish pressure
    - Price momentum confirms/contradicts OI signals

    Args:
        strikes_data: Dict of strike -> {ce_oi, ce_oi_change, pe_oi, pe_oi_change}
        spot_price: Current underlying spot price
        num_otm_strikes: Number of OTM strikes to analyze on each side
        include_atm: Include ATM strike in analysis
        include_itm: Include ITM strikes in analysis
        momentum_score: Pre-calculated momentum score (-100 to +100)
        price_history: List of recent price data for momentum calculation

    Returns:
        dict with analysis results including verdict
    """
    all_strikes = list(strikes_data.keys())

    if not all_strikes:
        return {
            "error": "No strike data available",
            "verdict": "No Data"
        }

    # Find ATM strike
    atm_strike = find_atm_strike(spot_price, all_strikes)

    # Get OTM strikes
    otm_call_strikes, otm_put_strikes = get_otm_strikes(
        atm_strike, all_strikes, num_otm_strikes
    )

    # Calculate totals for OTM Calls (bearish indicator)
    otm_calls_data = []
    total_call_oi = 0
    total_call_oi_change = 0
    total_call_volume = 0

    for strike in otm_call_strikes:
        data = strikes_data.get(strike, {})
        ce_oi = data.get("ce_oi", 0)
        ce_oi_change = data.get("ce_oi_change", 0)
        ce_volume = data.get("ce_volume", 0)

        # Calculate conviction multiplier
        conviction = calculate_conviction_multiplier(ce_volume, ce_oi_change)
        weighted_change = ce_oi_change * conviction

        total_call_oi += ce_oi
        total_call_oi_change += weighted_change
        total_call_volume += ce_volume

        otm_calls_data.append({
            "strike": strike,
            "oi": ce_oi,
            "oi_change": ce_oi_change,
            "volume": ce_volume,
            "conviction": conviction
        })

    # Calculate totals for OTM Puts (bullish indicator)
    otm_puts_data = []
    total_put_oi = 0
    total_put_oi_change = 0
    total_put_volume = 0

    for strike in otm_put_strikes:
        data = strikes_data.get(strike, {})
        pe_oi = data.get("pe_oi", 0)
        pe_oi_change = data.get("pe_oi_change", 0)
        pe_volume = data.get("pe_volume", 0)

        # Calculate conviction multiplier
        conviction = calculate_conviction_multiplier(pe_volume, pe_oi_change)
        weighted_change = pe_oi_change * conviction

        total_put_oi += pe_oi
        total_put_oi_change += weighted_change
        total_put_volume += pe_volume

        otm_puts_data.append({
            "strike": strike,
            "oi": pe_oi,
            "oi_change": pe_oi_change,
            "volume": pe_volume,
            "conviction": conviction
        })

    # ATM Analysis (if enabled)
    atm_data = None
    atm_call_oi = 0
    atm_put_oi = 0
    atm_call_oi_change = 0
    atm_put_oi_change = 0
    atm_call_volume = 0
    atm_put_volume = 0
    atm_call_conviction = 1.0
    atm_put_conviction = 1.0

    if include_atm:
        atm_strike_data = strikes_data.get(atm_strike, {})
        atm_call_oi = atm_strike_data.get("ce_oi", 0)
        atm_put_oi = atm_strike_data.get("pe_oi", 0)
        atm_call_oi_change = atm_strike_data.get("ce_oi_change", 0)
        atm_put_oi_change = atm_strike_data.get("pe_oi_change", 0)
        atm_call_volume = atm_strike_data.get("ce_volume", 0)
        atm_put_volume = atm_strike_data.get("pe_volume", 0)

        # Calculate conviction for ATM strikes
        atm_call_conviction = calculate_conviction_multiplier(atm_call_volume, atm_call_oi_change)
        atm_put_conviction = calculate_conviction_multiplier(atm_put_volume, atm_put_oi_change)

        atm_data = {
            "strike": atm_strike,
            "call_oi": atm_call_oi,
            "put_oi": atm_put_oi,
            "call_oi_change": atm_call_oi_change,
            "put_oi_change": atm_put_oi_change,
            "call_volume": atm_call_volume,
            "put_volume": atm_put_volume,
            "call_conviction": atm_call_conviction,
            "put_conviction": atm_put_conviction
        }

    # ITM Analysis (if enabled)
    itm_calls_data = []
    itm_puts_data = []
    total_itm_call_oi = 0
    total_itm_put_oi = 0
    total_itm_call_oi_change = 0
    total_itm_put_oi_change = 0
    total_itm_call_volume = 0
    total_itm_put_volume = 0

    if include_itm:
        itm_call_strikes, itm_put_strikes = get_itm_strikes(
            atm_strike, all_strikes, num_otm_strikes
        )

        for strike in itm_call_strikes:
            data = strikes_data.get(strike, {})
            ce_oi = data.get("ce_oi", 0)
            ce_oi_change = data.get("ce_oi_change", 0)
            ce_volume = data.get("ce_volume", 0)

            # Calculate conviction multiplier
            conviction = calculate_conviction_multiplier(ce_volume, ce_oi_change)
            weighted_change = ce_oi_change * conviction

            total_itm_call_oi += ce_oi
            total_itm_call_oi_change += weighted_change
            total_itm_call_volume += ce_volume

            itm_calls_data.append({
                "strike": strike,
                "oi": ce_oi,
                "oi_change": ce_oi_change,
                "volume": ce_volume,
                "conviction": conviction
            })

        for strike in itm_put_strikes:
            data = strikes_data.get(strike, {})
            pe_oi = data.get("pe_oi", 0)
            pe_oi_change = data.get("pe_oi_change", 0)
            pe_volume = data.get("pe_volume", 0)

            # Calculate conviction multiplier
            conviction = calculate_conviction_multiplier(pe_volume, pe_oi_change)
            weighted_change = pe_oi_change * conviction

            total_itm_put_oi += pe_oi
            total_itm_put_oi_change += weighted_change
            total_itm_put_volume += pe_volume

            itm_puts_data.append({
                "strike": strike,
                "oi": pe_oi,
                "oi_change": pe_oi_change,
                "volume": pe_volume,
                "conviction": conviction
            })

    # Determine verdict based on OI changes AND Total OI
    # Positive call OI change = more bearish pressure (resistance above)
    # Positive put OI change = more bullish pressure (support below)

    net_oi_change = total_put_oi_change - total_call_oi_change
    net_total_oi = total_put_oi - total_call_oi

    # Calculate momentum if price_history provided
    if momentum_score is None and price_history:
        momentum_score = calculate_price_momentum(price_history)
    elif momentum_score is None:
        momentum_score = 0.0

    # Determine weights based on enabled options
    # With momentum enabled, reduce other weights proportionally
    if momentum_score != 0.0:
        # Momentum gets 20% weight
        momentum_weight = 0.20
        if include_atm and include_itm:
            otm_weight, atm_weight, itm_weight = 0.50, 0.20, 0.10
        elif include_atm:
            otm_weight, atm_weight, itm_weight = 0.60, 0.20, 0.0
        elif include_itm:
            otm_weight, atm_weight, itm_weight = 0.70, 0.0, 0.10
        else:
            otm_weight, atm_weight, itm_weight = 0.80, 0.0, 0.0
    else:
        # No momentum, use original weights
        momentum_weight = 0.0
        if include_atm and include_itm:
            otm_weight, atm_weight, itm_weight = 0.60, 0.25, 0.15
        elif include_atm:
            otm_weight, atm_weight, itm_weight = 0.70, 0.30, 0.0
        elif include_itm:
            otm_weight, atm_weight, itm_weight = 0.85, 0.0, 0.15
        else:
            otm_weight, atm_weight, itm_weight = 1.0, 0.0, 0.0

    # Calculate OTM score (70% OI change + 30% Total OI)
    max_otm_change = max(abs(total_call_oi_change), abs(total_put_oi_change), 1)
    max_otm_total = max(total_call_oi, total_put_oi, 1)
    otm_change_score = (net_oi_change / max_otm_change) * 100
    otm_total_score = (net_total_oi / max_otm_total) * 100
    otm_score = (0.7 * otm_change_score) + (0.3 * otm_total_score)

    # Calculate ATM score (70% OI change + 30% Total OI)
    atm_score = 0.0
    atm_change_score = 0.0
    atm_total_score = 0.0
    if include_atm:
        # Apply conviction weighting to ATM changes
        weighted_atm_call_change = atm_call_oi_change * atm_call_conviction
        weighted_atm_put_change = atm_put_oi_change * atm_put_conviction
        atm_net_change = weighted_atm_put_change - weighted_atm_call_change
        atm_net_total = atm_put_oi - atm_call_oi
        max_atm_change = max(abs(weighted_atm_call_change), abs(weighted_atm_put_change), 1)
        max_atm_total = max(atm_call_oi, atm_put_oi, 1)
        atm_change_score = (atm_net_change / max_atm_change) * 100
        atm_total_score = (atm_net_total / max_atm_total) * 100
        atm_score = (0.7 * atm_change_score) + (0.3 * atm_total_score)

    # Calculate ITM score (70% OI change + 30% Total OI)
    # ITM Zone Scoring Logic:
    # - ITM Call writers (below spot) want price to fall → Bearish pressure
    # - ITM Put writers (above spot) want price to rise → Bullish pressure
    # - Formula: Put OI - Call OI (same directionality as OTM)
    # - Positive score = Bulls pulling/holding price UP
    # - Negative score = Bears pulling/holding price DOWN
    itm_score = 0.0
    itm_change_score = 0.0
    itm_total_score = 0.0
    if include_itm:
        itm_net_change = total_itm_put_oi_change - total_itm_call_oi_change
        itm_net_total = total_itm_put_oi - total_itm_call_oi
        max_itm_change = max(abs(total_itm_call_oi_change), abs(total_itm_put_oi_change), 1)
        max_itm_total = max(total_itm_call_oi, total_itm_put_oi, 1)
        itm_change_score = (itm_net_change / max_itm_change) * 100
        itm_total_score = (itm_net_total / max_itm_total) * 100
        itm_score = (0.7 * itm_change_score) + (0.3 * itm_total_score)

    # Combined weighted score across zones (including momentum)
    combined_score = (otm_weight * otm_score) + (atm_weight * atm_score) + (itm_weight * itm_score) + (momentum_weight * momentum_score)

    # Store component scores for display
    total_oi_score = otm_total_score  # For backward compatibility display

    # Determine verdict based on combined score
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

    # PCR (Put-Call Ratio) based on OI
    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0

    # Calculate price change percentage for display
    price_change_pct = 0.0
    if price_history and len(price_history) >= 2:
        current = price_history[-1]['spot_price']
        past = price_history[0]['spot_price']
        if past > 0:
            price_change_pct = ((current - past) / past) * 100

    # Calculate confirmation status
    oi_direction = "bullish" if combined_score > 0 else "bearish" if combined_score < 0 else "neutral"
    price_direction = "rising" if price_change_pct > 0.05 else "falling" if price_change_pct < -0.05 else "flat"

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

    # Calculate average conviction scores
    avg_call_conviction = 0.0
    avg_put_conviction = 0.0
    if otm_calls_data:
        avg_call_conviction = sum(d['conviction'] for d in otm_calls_data) / len(otm_calls_data)
    if otm_puts_data:
        avg_put_conviction = sum(d['conviction'] for d in otm_puts_data) / len(otm_puts_data)

    # Calculate volume PCR
    volume_pcr = total_put_volume / max(total_call_volume, 1)

    # Calculate new metrics: IV Skew, Max Pain, OI Clusters
    iv_skew = calculate_iv_skew(strikes_data, spot_price, num_otm_strikes)
    max_pain = calculate_max_pain(strikes_data)
    oi_clusters = find_oi_clusters(strikes_data, spot_price)

    # Calculate trade setup (pass price_history for swing-based SL/targets)
    trade_setup = calculate_trade_setup(strikes_data, spot_price, verdict, max_pain, price_history)

    # Calculate signal confidence
    signal_confidence = calculate_signal_confidence(
        combined_score, iv_skew, volume_pcr, pcr,
        max_pain, spot_price, confirmation_status
    )

    # Detect potential traps
    trap_warning = detect_trap(strikes_data, spot_price, price_direction, oi_direction, strength)

    return {
        "spot_price": spot_price,
        "atm_strike": atm_strike,
        "otm_calls": otm_calls_data,
        "otm_puts": otm_puts_data,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "call_oi_change": total_call_oi_change,
        "put_oi_change": total_put_oi_change,
        "net_oi_change": net_oi_change,
        "net_total_oi": net_total_oi,
        "change_score": round(otm_score, 1),
        "total_oi_score": round(total_oi_score, 1),
        "combined_score": round(combined_score, 1),
        "pcr": round(pcr, 2),
        "verdict": verdict,
        "strength": strength,
        # Volume metrics
        "total_call_volume": total_call_volume,
        "total_put_volume": total_put_volume,
        "volume_pcr": round(volume_pcr, 2),
        "avg_call_conviction": round(avg_call_conviction, 2),
        "avg_put_conviction": round(avg_put_conviction, 2),
        # Momentum data
        "momentum_score": round(momentum_score, 1),
        "price_change_pct": round(price_change_pct, 2),
        # ATM/ITM toggle data
        "include_atm": include_atm,
        "include_itm": include_itm,
        "atm_data": atm_data,
        "itm_calls": itm_calls_data,
        "itm_puts": itm_puts_data,
        "total_itm_call_oi": total_itm_call_oi,
        "total_itm_put_oi": total_itm_put_oi,
        "itm_call_oi_change": total_itm_call_oi_change,
        "itm_put_oi_change": total_itm_put_oi_change,
        "otm_score": round(otm_score, 1),
        "otm_change_score": round(otm_change_score, 1),
        "otm_total_score": round(otm_total_score, 1),
        "atm_score": round(atm_score, 1) if include_atm else None,
        "atm_change_score": round(atm_change_score, 1) if include_atm else None,
        "atm_total_score": round(atm_total_score, 1) if include_atm else None,
        "itm_score": round(itm_score, 1) if include_itm else None,
        "itm_change_score": round(itm_change_score, 1) if include_itm else None,
        "itm_total_score": round(itm_total_score, 1) if include_itm else None,
        "weights": {
            "otm": otm_weight,
            "atm": atm_weight,
            "itm": itm_weight,
            "momentum": momentum_weight
        },
        "confirmation_status": confirmation_status,
        "confirmation_message": confirmation_message,
        # New metrics for improved accuracy
        "iv_skew": round(iv_skew, 2),
        "max_pain": max_pain,
        "oi_clusters": oi_clusters,
        "signal_confidence": round(signal_confidence, 1),
        "trade_setup": trade_setup,
        "trap_warning": trap_warning
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

    # Test OTM only (default)
    print("=" * 50)
    print("TEST 1: OTM Only (Default)")
    print("=" * 50)
    analysis = analyze_tug_of_war(sample_strikes, spot)
    print(format_analysis_summary(analysis))
    print(f"\nWeights: OTM={analysis['weights']['otm']}, ATM={analysis['weights']['atm']}, ITM={analysis['weights']['itm']}")
    print(f"OTM Score: {analysis['otm_score']}")

    # Test with ATM
    print("\n" + "=" * 50)
    print("TEST 2: OTM + ATM")
    print("=" * 50)
    analysis_atm = analyze_tug_of_war(sample_strikes, spot, include_atm=True)
    print(f"Verdict: {analysis_atm['verdict']}")
    print(f"Combined Score: {analysis_atm['combined_score']}")
    print(f"Weights: OTM={analysis_atm['weights']['otm']}, ATM={analysis_atm['weights']['atm']}, ITM={analysis_atm['weights']['itm']}")
    print(f"OTM Score: {analysis_atm['otm_score']}, ATM Score: {analysis_atm['atm_score']}")
    print(f"ATM Data: {analysis_atm['atm_data']}")

    # Test with ITM
    print("\n" + "=" * 50)
    print("TEST 3: OTM + ITM")
    print("=" * 50)
    analysis_itm = analyze_tug_of_war(sample_strikes, spot, include_itm=True)
    print(f"Verdict: {analysis_itm['verdict']}")
    print(f"Combined Score: {analysis_itm['combined_score']}")
    print(f"Weights: OTM={analysis_itm['weights']['otm']}, ATM={analysis_itm['weights']['atm']}, ITM={analysis_itm['weights']['itm']}")
    print(f"OTM Score: {analysis_itm['otm_score']}, ITM Score: {analysis_itm['itm_score']}")
    print(f"ITM Calls: {analysis_itm['itm_calls']}")
    print(f"ITM Puts: {analysis_itm['itm_puts']}")

    # Test with both ATM and ITM
    print("\n" + "=" * 50)
    print("TEST 4: OTM + ATM + ITM")
    print("=" * 50)
    analysis_all = analyze_tug_of_war(sample_strikes, spot, include_atm=True, include_itm=True)
    print(f"Verdict: {analysis_all['verdict']}")
    print(f"Combined Score: {analysis_all['combined_score']}")
    print(f"Weights: OTM={analysis_all['weights']['otm']}, ATM={analysis_all['weights']['atm']}, ITM={analysis_all['weights']['itm']}")
    print(f"Scores: OTM={analysis_all['otm_score']}, ATM={analysis_all['atm_score']}, ITM={analysis_all['itm_score']}")

    # Test premium-based trade setup (OPTION BUYER)
    print("\n" + "=" * 50)
    print("TEST 5: Premium-Based Trade Setup (Option Buyer)")
    print("=" * 50)

    price_history = [
        {"spot_price": 24000.00},
        {"spot_price": 23980.25},
        {"spot_price": 24010.75},
        {"spot_price": 24025.50},
    ]

    analysis_with_history = analyze_tug_of_war(
        sample_strikes, spot,
        include_atm=True, include_itm=True,
        price_history=price_history
    )

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
        print(f"\nSpot Reference:")
        print(f"  Current: {trade['spot_price']}")
        print(f"  Support: {trade['support_ref']}")
        print(f"  Resistance: {trade['resistance_ref']}")
        print(f"  Max Pain: {trade['max_pain']}")
    else:
        print("No trade setup generated (missing LTP data)")

    # Test bearish trade setup
    print("\n" + "=" * 50)
    print("TEST 6: Bearish Trade Setup (Put Buyer)")
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
        include_atm=True, include_itm=True,
        price_history=price_history
    )

    trade_bearish = analysis_bearish['trade_setup']
    if trade_bearish:
        print(f"\nSpot: {spot}")
        print(f"Verdict: {analysis_bearish['verdict']} (Score: {analysis_bearish['combined_score']})")
        print(f"\n{trade_bearish['direction']} Setup:")
        print(f"  Strike: {trade_bearish['strike']} {trade_bearish['option_type']} ({trade_bearish['moneyness']})")
        print(f"  Entry: {trade_bearish['entry_premium']} (current premium)")
        print(f"  SL: {trade_bearish['sl_premium']} (-{trade_bearish['risk_pct']}%, {trade_bearish['risk_points']:.2f} pts risk)")
        print(f"  T1: {trade_bearish['target1_premium']} (1:{trade_bearish['risk_reward_t1']:.0f} R:R)")
        print(f"  T2: {trade_bearish['target2_premium']} (1:{trade_bearish['risk_reward_t2']:.0f} R:R)")
    else:
        print("No trade setup generated")
