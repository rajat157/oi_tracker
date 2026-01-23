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


def analyze_tug_of_war(strikes_data: dict, spot_price: float,
                        num_otm_strikes: int = 3) -> dict:
    """
    Perform tug-of-war analysis on option chain data.

    Analysis Logic:
    - High Call OI addition = Bears writing calls = Bearish pressure
    - High Put OI addition = Bulls writing puts = Bullish pressure

    Args:
        strikes_data: Dict of strike -> {ce_oi, ce_oi_change, pe_oi, pe_oi_change}
        spot_price: Current underlying spot price
        num_otm_strikes: Number of OTM strikes to analyze on each side

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

    # Determine verdict based on OI changes AND Total OI
    # Positive call OI change = more bearish pressure (resistance above)
    # Positive put OI change = more bullish pressure (support below)

    net_oi_change = total_put_oi_change - total_call_oi_change
    net_total_oi = total_put_oi - total_call_oi

    # Calculate weighted score:
    # - OI Change (70% weight): Today's fresh bets - most relevant
    # - Total OI (30% weight): Overall positioning - adds context

    # Normalize the values to make them comparable
    # Use the larger absolute value as the base for normalization
    max_change = max(abs(total_call_oi_change), abs(total_put_oi_change), 1)
    max_total = max(total_call_oi, total_put_oi, 1)

    # Score from -100 (extreme bearish) to +100 (extreme bullish)
    change_score = (net_oi_change / max_change) * 100  # -100 to +100
    total_score = (net_total_oi / max_total) * 100      # -100 to +100

    # Combined weighted score
    combined_score = (0.7 * change_score) + (0.3 * total_score)

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
        "change_score": round(change_score, 1),
        "total_oi_score": round(total_score, 1),
        "combined_score": round(combined_score, 1),
        "pcr": round(pcr, 2),
        "verdict": verdict,
        "strength": strength
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

    analysis = analyze_tug_of_war(sample_strikes, spot)
    print(format_analysis_summary(analysis))
