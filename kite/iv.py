"""
Black-Scholes IV Calculator

Pure math module — no API dependencies.
Computes implied volatility from option prices using Newton-Raphson
with bisection fallback. Uses math.erf for normal CDF (no scipy needed).

Risk-free rate: 7% (India)
"""

import math
from datetime import datetime


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_price(spot: float, strike: float, t: float,
                        r: float, sigma: float, option_type: str) -> float:
    """
    Compute Black-Scholes option price.

    Args:
        spot: Current underlying price
        strike: Option strike price
        t: Time to expiry in years
        r: Risk-free rate (e.g. 0.07 for 7%)
        sigma: Volatility (e.g. 0.15 for 15%)
        option_type: "CE" for call, "PE" for put

    Returns:
        Theoretical option price
    """
    if t <= 0:
        t = 1 / 365  # Minimum time
    if sigma <= 0:
        sigma = 0.001  # Near-zero vol

    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option_type == "CE":
        price = spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    else:  # PE
        price = strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)

    return max(price, 0.0)


def _vega(spot: float, strike: float, t: float, r: float, sigma: float) -> float:
    """Compute vega (dPrice/dSigma) for Newton-Raphson."""
    if t <= 0:
        t = 1 / 365
    if sigma <= 0:
        sigma = 0.001

    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    return spot * _norm_pdf(d1) * sqrt_t


def implied_volatility(option_price: float, spot: float, strike: float,
                       t: float, r: float = 0.07, option_type: str = "CE") -> float:
    """
    Compute implied volatility using Newton-Raphson with bisection fallback.

    Args:
        option_price: Market price of the option
        spot: Current underlying price
        strike: Option strike price
        t: Time to expiry in years
        r: Risk-free rate (default 7% for India)
        option_type: "CE" for call, "PE" for put

    Returns:
        Implied volatility as a decimal (e.g. 0.15 for 15%).
        Returns 0.0 for zero/negative premium or non-convergence.
    """
    if option_price <= 0:
        return 0.0

    if t <= 0:
        t = 1 / 365

    # Initial guess
    sigma = 0.20
    max_iterations = 50
    precision = 1e-6

    # Newton-Raphson
    for _ in range(max_iterations):
        price = black_scholes_price(spot, strike, t, r, sigma, option_type)
        v = _vega(spot, strike, t, r, sigma)

        if v < 1e-10:
            break  # Vega too small, fall through to bisection

        new_sigma = sigma - (price - option_price) / v

        if new_sigma <= 0:
            break  # Went negative, fall through to bisection

        if abs(new_sigma - sigma) < precision:
            return new_sigma

        sigma = new_sigma
    else:
        # Newton-Raphson converged within loop
        return max(sigma, 0.0)

    # Bisection fallback
    lo, hi = 0.001, 5.0
    for _ in range(100):
        mid = (lo + hi) / 2
        price = black_scholes_price(spot, strike, t, r, mid, option_type)

        if abs(price - option_price) < precision:
            return mid

        if price > option_price:
            hi = mid
        else:
            lo = mid

    # Non-convergence
    return 0.0


def time_to_expiry_years(expiry_date_str: str) -> float:
    """
    Compute time to expiry in years from a date string.

    Args:
        expiry_date_str: Expiry date in "DD-Mon-YYYY" (e.g. "27-Feb-2026")
                        or "YYYY-MM-DD" format

    Returns:
        Time to expiry in years. Minimum 1/365 (1 day).
    """
    now = datetime.now()

    # Try multiple date formats
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%B-%Y"):
        try:
            expiry = datetime.strptime(expiry_date_str, fmt)
            break
        except ValueError:
            continue
    else:
        return 1 / 365  # Fallback to minimum

    days = (expiry - now).days
    if days <= 0:
        return 1 / 365  # Minimum 1 day

    return days / 365
