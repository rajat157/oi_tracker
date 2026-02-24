"""
Black-Scholes IV Calculator — Pure math, zero dependencies.

Computes implied volatility from option prices using Newton-Raphson
with bisection fallback. Uses math.erf for normal CDF (no scipy needed).
Risk-free rate: 7% (India).
"""

import math
from datetime import datetime

_SQRT_2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)
_MIN_TIME = 1 / 365


def norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def black_scholes_price(
    spot: float,
    strike: float,
    t: float,
    r: float,
    sigma: float,
    option_type: str,
) -> float:
    """
    Compute Black-Scholes option price.

    Args:
        spot: Current underlying price.
        strike: Option strike price.
        t: Time to expiry in years.
        r: Risk-free rate (e.g. 0.07 for 7%).
        sigma: Volatility (e.g. 0.15 for 15%).
        option_type: "CE" for call, "PE" for put.

    Returns:
        Theoretical option price (floored at 0).
    """
    if t <= 0:
        t = _MIN_TIME
    if sigma <= 0:
        sigma = 0.001

    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option_type == "CE":
        price = spot * norm_cdf(d1) - strike * math.exp(-r * t) * norm_cdf(d2)
    else:  # PE
        price = strike * math.exp(-r * t) * norm_cdf(-d2) - spot * norm_cdf(-d1)

    return max(price, 0.0)


def vega(spot: float, strike: float, t: float, r: float, sigma: float) -> float:
    """Compute vega (dPrice/dSigma) for Newton-Raphson."""
    if t <= 0:
        t = _MIN_TIME
    if sigma <= 0:
        sigma = 0.001

    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    return spot * norm_pdf(d1) * sqrt_t


def implied_volatility(
    option_price: float,
    spot: float,
    strike: float,
    t: float,
    r: float = 0.07,
    option_type: str = "CE",
) -> float:
    """
    Compute implied volatility using Newton-Raphson with bisection fallback.

    Returns:
        Implied volatility as decimal (0.15 = 15%). Returns 0.0 for
        zero/negative premium or non-convergence.
    """
    if option_price <= 0:
        return 0.0
    if t <= 0:
        t = _MIN_TIME

    # Newton-Raphson
    sigma = 0.20
    for _ in range(50):
        price = black_scholes_price(spot, strike, t, r, sigma, option_type)
        v = vega(spot, strike, t, r, sigma)

        if v < 1e-10:
            break  # Fall through to bisection

        new_sigma = sigma - (price - option_price) / v

        if new_sigma <= 0:
            break  # Fall through to bisection

        if abs(new_sigma - sigma) < 1e-6:
            return new_sigma

        sigma = new_sigma
    else:
        return max(sigma, 0.0)

    # Bisection fallback
    lo, hi = 0.001, 5.0
    for _ in range(100):
        mid = (lo + hi) / 2
        price = black_scholes_price(spot, strike, t, r, mid, option_type)

        if abs(price - option_price) < 1e-6:
            return mid

        if price > option_price:
            hi = mid
        else:
            lo = mid

    return 0.0


def time_to_expiry_years(expiry_date_str: str, now: datetime | None = None) -> float:
    """
    Compute time to expiry in years from a date string.

    Args:
        expiry_date_str: Expiry date in "DD-Mon-YYYY", "YYYY-MM-DD", or "DD-Month-YYYY" format.
        now: Reference datetime (defaults to datetime.now() if not given).

    Returns:
        Time to expiry in years. Minimum 1/365.
    """
    if now is None:
        now = datetime.now()

    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%B-%Y"):
        try:
            expiry = datetime.strptime(expiry_date_str, fmt)
            break
        except ValueError:
            continue
    else:
        return _MIN_TIME

    days = (expiry - now).days
    if days <= 0:
        return _MIN_TIME

    return days / 365
