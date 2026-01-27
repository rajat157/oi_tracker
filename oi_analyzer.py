"""
OI Analyzer - Tug-of-War Analysis Logic
Analyzes option chain OI to determine market sentiment
"""

from typing import Tuple


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


def get_itm_strikes(atm_strike: int, all_strikes: list, num_strikes: int = 3) -> Tuple[list, list]:
    """
    Get ITM strikes on both sides of ATM.

    For ITM options:
    - ITM Calls: Strikes BELOW spot (calls with intrinsic value)
    - ITM Puts: Strikes ABOVE spot (puts with intrinsic value)

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
                        include_itm: bool = False) -> dict:
    """
    Perform tug-of-war analysis on option chain data.

    Analysis Logic:
    - High Call OI addition = Bears writing calls = Bearish pressure
    - High Put OI addition = Bulls writing puts = Bullish pressure

    Args:
        strikes_data: Dict of strike -> {ce_oi, ce_oi_change, pe_oi, pe_oi_change}
        spot_price: Current underlying spot price
        num_otm_strikes: Number of OTM strikes to analyze on each side
        include_atm: Include ATM strike in analysis
        include_itm: Include ITM strikes in analysis

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

    for strike in otm_call_strikes:
        data = strikes_data.get(strike, {})
        ce_oi = data.get("ce_oi", 0)
        ce_oi_change = data.get("ce_oi_change", 0)
        total_call_oi += ce_oi
        total_call_oi_change += ce_oi_change
        otm_calls_data.append({
            "strike": strike,
            "oi": ce_oi,
            "oi_change": ce_oi_change
        })

    # Calculate totals for OTM Puts (bullish indicator)
    otm_puts_data = []
    total_put_oi = 0
    total_put_oi_change = 0

    for strike in otm_put_strikes:
        data = strikes_data.get(strike, {})
        pe_oi = data.get("pe_oi", 0)
        pe_oi_change = data.get("pe_oi_change", 0)
        total_put_oi += pe_oi
        total_put_oi_change += pe_oi_change
        otm_puts_data.append({
            "strike": strike,
            "oi": pe_oi,
            "oi_change": pe_oi_change
        })

    # ATM Analysis (if enabled)
    atm_data = None
    atm_call_oi = 0
    atm_put_oi = 0
    atm_call_oi_change = 0
    atm_put_oi_change = 0

    if include_atm:
        atm_strike_data = strikes_data.get(atm_strike, {})
        atm_call_oi = atm_strike_data.get("ce_oi", 0)
        atm_put_oi = atm_strike_data.get("pe_oi", 0)
        atm_call_oi_change = atm_strike_data.get("ce_oi_change", 0)
        atm_put_oi_change = atm_strike_data.get("pe_oi_change", 0)
        atm_data = {
            "strike": atm_strike,
            "call_oi": atm_call_oi,
            "put_oi": atm_put_oi,
            "call_oi_change": atm_call_oi_change,
            "put_oi_change": atm_put_oi_change
        }

    # ITM Analysis (if enabled)
    itm_calls_data = []
    itm_puts_data = []
    total_itm_call_oi = 0
    total_itm_put_oi = 0
    total_itm_call_oi_change = 0
    total_itm_put_oi_change = 0

    if include_itm:
        itm_call_strikes, itm_put_strikes = get_itm_strikes(
            atm_strike, all_strikes, num_otm_strikes
        )

        for strike in itm_call_strikes:
            data = strikes_data.get(strike, {})
            ce_oi = data.get("ce_oi", 0)
            ce_oi_change = data.get("ce_oi_change", 0)
            total_itm_call_oi += ce_oi
            total_itm_call_oi_change += ce_oi_change
            itm_calls_data.append({
                "strike": strike,
                "oi": ce_oi,
                "oi_change": ce_oi_change
            })

        for strike in itm_put_strikes:
            data = strikes_data.get(strike, {})
            pe_oi = data.get("pe_oi", 0)
            pe_oi_change = data.get("pe_oi_change", 0)
            total_itm_put_oi += pe_oi
            total_itm_put_oi_change += pe_oi_change
            itm_puts_data.append({
                "strike": strike,
                "oi": pe_oi,
                "oi_change": pe_oi_change
            })

    # Determine verdict based on OI changes AND Total OI
    # Positive call OI change = more bearish pressure (resistance above)
    # Positive put OI change = more bullish pressure (support below)

    net_oi_change = total_put_oi_change - total_call_oi_change
    net_total_oi = total_put_oi - total_call_oi

    # Determine weights based on enabled options
    if include_atm and include_itm:
        otm_weight, atm_weight, itm_weight = 0.60, 0.25, 0.15
    elif include_atm:
        otm_weight, atm_weight, itm_weight = 0.70, 0.30, 0.0
    elif include_itm:
        otm_weight, atm_weight, itm_weight = 0.85, 0.0, 0.15
    else:
        otm_weight, atm_weight, itm_weight = 1.0, 0.0, 0.0

    # Calculate OTM score (based on OI change - most relevant for sentiment)
    max_otm_change = max(abs(total_call_oi_change), abs(total_put_oi_change), 1)
    otm_score = (net_oi_change / max_otm_change) * 100  # -100 to +100

    # Calculate ATM score
    atm_score = 0.0
    if include_atm:
        atm_net = atm_put_oi_change - atm_call_oi_change
        max_atm = max(abs(atm_call_oi_change), abs(atm_put_oi_change), 1)
        atm_score = (atm_net / max_atm) * 100

    # Calculate ITM score
    itm_score = 0.0
    if include_itm:
        itm_net = total_itm_put_oi_change - total_itm_call_oi_change
        max_itm = max(abs(total_itm_call_oi_change), abs(total_itm_put_oi_change), 1)
        itm_score = (itm_net / max_itm) * 100

    # Combined weighted score
    combined_score = (otm_weight * otm_score) + (atm_weight * atm_score) + (itm_weight * itm_score)

    # Also calculate the legacy scores for backward compatibility
    max_total = max(total_call_oi, total_put_oi, 1)
    total_oi_score = (net_total_oi / max_total) * 100

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
        "atm_score": round(atm_score, 1) if include_atm else None,
        "itm_score": round(itm_score, 1) if include_itm else None,
        "weights": {
            "otm": otm_weight,
            "atm": atm_weight,
            "itm": itm_weight
        }
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
    # Test with sample data
    sample_strikes = {
        23900: {"ce_oi": 150000, "ce_oi_change": 8000, "pe_oi": 200000, "pe_oi_change": 25000},
        23950: {"ce_oi": 180000, "ce_oi_change": 12000, "pe_oi": 170000, "pe_oi_change": 18000},
        24000: {"ce_oi": 250000, "ce_oi_change": 15000, "pe_oi": 220000, "pe_oi_change": 20000},
        24050: {"ce_oi": 280000, "ce_oi_change": 30000, "pe_oi": 180000, "pe_oi_change": 10000},
        24100: {"ce_oi": 320000, "ce_oi_change": 35000, "pe_oi": 150000, "pe_oi_change": 8000},
        24150: {"ce_oi": 290000, "ce_oi_change": 25000, "pe_oi": 120000, "pe_oi_change": 5000},
        24200: {"ce_oi": 250000, "ce_oi_change": 20000, "pe_oi": 100000, "pe_oi_change": 3000},
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
