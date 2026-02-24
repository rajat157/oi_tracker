"""Tests for Black-Scholes IV calculator — pure math, no DB needed."""

import math
from datetime import datetime

import pytest

from app.engine.iv_calculator import (
    black_scholes_price,
    implied_volatility,
    norm_cdf,
    norm_pdf,
    time_to_expiry_years,
    vega,
)


# ── norm_cdf ─────────────────────────────────────────────────

class TestNormCdf:
    def test_zero(self):
        assert norm_cdf(0.0) == pytest.approx(0.5)

    def test_large_positive(self):
        assert norm_cdf(5.0) == pytest.approx(1.0, abs=1e-6)

    def test_large_negative(self):
        assert norm_cdf(-5.0) == pytest.approx(0.0, abs=1e-6)

    def test_symmetry(self):
        assert norm_cdf(1.0) + norm_cdf(-1.0) == pytest.approx(1.0)

    def test_one_sigma(self):
        # P(X < 1) ≈ 0.8413
        assert norm_cdf(1.0) == pytest.approx(0.8413, abs=1e-3)


# ── norm_pdf ─────────────────────────────────────────────────

class TestNormPdf:
    def test_peak_at_zero(self):
        assert norm_pdf(0.0) == pytest.approx(1 / math.sqrt(2 * math.pi))

    def test_symmetry(self):
        assert norm_pdf(1.5) == pytest.approx(norm_pdf(-1.5))

    def test_positive(self):
        assert norm_pdf(2.0) > 0


# ── black_scholes_price ─────────────────────────────────────

class TestBlackScholesPrice:
    """Known BS values for validation."""

    def test_atm_call(self):
        # ATM call: S=100, K=100, t=1y, r=5%, σ=20%
        price = black_scholes_price(100, 100, 1.0, 0.05, 0.20, "CE")
        assert price == pytest.approx(10.45, abs=0.2)

    def test_atm_put(self):
        price = black_scholes_price(100, 100, 1.0, 0.05, 0.20, "PE")
        assert price == pytest.approx(5.57, abs=0.2)

    def test_deep_itm_call(self):
        price = black_scholes_price(100, 80, 1.0, 0.05, 0.20, "CE")
        assert price > 20  # Deep ITM, mostly intrinsic

    def test_deep_otm_call(self):
        price = black_scholes_price(100, 120, 0.01, 0.05, 0.20, "CE")
        assert price < 1  # Deep OTM, near-zero

    def test_put_call_parity(self):
        S, K, t, r, sigma = 100, 100, 0.5, 0.07, 0.25
        call = black_scholes_price(S, K, t, r, sigma, "CE")
        put = black_scholes_price(S, K, t, r, sigma, "PE")
        # C - P = S - K*exp(-rT)
        assert call - put == pytest.approx(S - K * math.exp(-r * t), abs=0.01)

    def test_floor_at_zero(self):
        price = black_scholes_price(100, 200, 0.001, 0.05, 0.10, "CE")
        assert price >= 0

    def test_zero_time_handled(self):
        price = black_scholes_price(100, 100, 0, 0.05, 0.20, "CE")
        assert price >= 0

    def test_zero_vol_handled(self):
        price = black_scholes_price(100, 100, 1.0, 0.05, 0, "CE")
        assert price >= 0

    def test_nifty_realistic(self):
        # NIFTY: spot=22500, strike=22500, t=3 days, r=7%, σ=15%
        price = black_scholes_price(22500, 22500, 3 / 365, 0.07, 0.15, "CE")
        assert 50 < price < 300  # Reasonable ATM premium


# ── vega ─────────────────────────────────────────────────────

class TestVega:
    def test_positive(self):
        v = vega(100, 100, 1.0, 0.05, 0.20)
        assert v > 0

    def test_atm_highest(self):
        v_atm = vega(100, 100, 1.0, 0.05, 0.20)
        v_otm = vega(100, 120, 1.0, 0.05, 0.20)
        assert v_atm > v_otm

    def test_zero_time(self):
        v = vega(100, 100, 0, 0.05, 0.20)
        assert v >= 0


# ── implied_volatility ──────────────────────────────────────

class TestImpliedVolatility:
    def test_round_trip_call(self):
        """Compute price with known σ, then recover σ via IV."""
        sigma = 0.25
        price = black_scholes_price(100, 100, 0.5, 0.07, sigma, "CE")
        iv = implied_volatility(price, 100, 100, 0.5, 0.07, "CE")
        assert iv == pytest.approx(sigma, abs=0.001)

    def test_round_trip_put(self):
        sigma = 0.30
        price = black_scholes_price(100, 100, 0.25, 0.07, sigma, "PE")
        iv = implied_volatility(price, 100, 100, 0.25, 0.07, "PE")
        assert iv == pytest.approx(sigma, abs=0.001)

    def test_zero_price_returns_zero(self):
        assert implied_volatility(0, 100, 100, 0.5) == 0.0

    def test_negative_price_returns_zero(self):
        assert implied_volatility(-5, 100, 100, 0.5) == 0.0

    def test_very_high_price(self):
        """Edge: price near spot should give high IV."""
        iv = implied_volatility(50, 100, 100, 0.01, 0.07, "CE")
        assert iv > 1.0  # Very high IV needed

    def test_nifty_realistic(self):
        """Realistic NIFTY option: price=150, spot=22500, strike=22500, t=3d."""
        iv = implied_volatility(150, 22500, 22500, 3 / 365, 0.07, "CE")
        assert 0.05 < iv < 2.0  # Reasonable IV range

    def test_zero_time(self):
        iv = implied_volatility(5, 100, 100, 0, 0.07, "CE")
        assert iv >= 0


# ── time_to_expiry_years ────────────────────────────────────

class TestTimeToExpiryYears:
    def test_dd_mon_yyyy(self):
        t = time_to_expiry_years("27-Feb-2026", now=datetime(2026, 2, 24))
        assert t == pytest.approx(3 / 365, abs=0.001)

    def test_yyyy_mm_dd(self):
        t = time_to_expiry_years("2026-03-01", now=datetime(2026, 2, 24))
        assert t == pytest.approx(5 / 365, abs=0.001)

    def test_past_date_returns_min(self):
        t = time_to_expiry_years("2020-01-01", now=datetime(2026, 2, 24))
        assert t == pytest.approx(1 / 365)

    def test_invalid_format_returns_min(self):
        t = time_to_expiry_years("not-a-date")
        assert t == pytest.approx(1 / 365)

    def test_same_day_returns_min(self):
        t = time_to_expiry_years("2026-02-24", now=datetime(2026, 2, 24))
        assert t == pytest.approx(1 / 365)

    def test_full_month_format(self):
        t = time_to_expiry_years("27-February-2026", now=datetime(2026, 2, 24))
        assert t == pytest.approx(3 / 365, abs=0.001)
