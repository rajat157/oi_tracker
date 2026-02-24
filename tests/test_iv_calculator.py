"""Tests for Black-Scholes IV calculator."""

import pytest
from iv_calculator import black_scholes_price, implied_volatility, time_to_expiry_years


class TestBlackScholesPrice:
    """Test BS pricing formula."""

    def test_atm_call_has_positive_price(self):
        """ATM call should have positive price."""
        price = black_scholes_price(
            spot=23000, strike=23000, t=5 / 365,
            r=0.07, sigma=0.13, option_type="CE"
        )
        assert price > 0

    def test_atm_put_has_positive_price(self):
        """ATM put should have positive price."""
        price = black_scholes_price(
            spot=23000, strike=23000, t=5 / 365,
            r=0.07, sigma=0.13, option_type="PE"
        )
        assert price > 0

    def test_deep_itm_call_near_intrinsic(self):
        """Deep ITM call should be close to intrinsic value."""
        price = black_scholes_price(
            spot=23000, strike=22000, t=5 / 365,
            r=0.07, sigma=0.13, option_type="CE"
        )
        intrinsic = 23000 - 22000
        assert price >= intrinsic * 0.99  # Should be at least intrinsic

    def test_deep_otm_call_near_zero(self):
        """Deep OTM call should be near zero."""
        price = black_scholes_price(
            spot=23000, strike=25000, t=5 / 365,
            r=0.07, sigma=0.13, option_type="CE"
        )
        assert price < 1.0

    def test_put_call_parity(self):
        """BS call and put prices should satisfy put-call parity:
        C - P = S - K * e^(-rT)
        """
        import math
        spot, strike, t, r, sigma = 23000, 23000, 10 / 365, 0.07, 0.15
        call = black_scholes_price(spot, strike, t, r, sigma, "CE")
        put = black_scholes_price(spot, strike, t, r, sigma, "PE")
        parity_rhs = spot - strike * math.exp(-r * t)
        assert abs((call - put) - parity_rhs) < 0.01

    def test_zero_vol_call_equals_intrinsic(self):
        """With zero vol, ITM call = discounted intrinsic."""
        import math
        spot, strike, t, r = 23000, 22500, 5 / 365, 0.07
        price = black_scholes_price(spot, strike, t, r, 0.001, "CE")
        expected = max(spot - strike * math.exp(-r * t), 0)
        assert abs(price - expected) < 5.0  # Small sigma, close to intrinsic


class TestImpliedVolatility:
    """Test IV solver."""

    def test_roundtrip_atm_call(self):
        """Compute BS price at known IV, recover IV, verify match."""
        known_iv = 0.14
        price = black_scholes_price(
            spot=23000, strike=23000, t=5 / 365,
            r=0.07, sigma=known_iv, option_type="CE"
        )
        recovered_iv = implied_volatility(
            option_price=price, spot=23000, strike=23000,
            t=5 / 365, r=0.07, option_type="CE"
        )
        assert abs(recovered_iv - known_iv) < 0.001

    def test_roundtrip_otm_put(self):
        """OTM put IV roundtrip."""
        known_iv = 0.18
        price = black_scholes_price(
            spot=23000, strike=22500, t=7 / 365,
            r=0.07, sigma=known_iv, option_type="PE"
        )
        recovered_iv = implied_volatility(
            option_price=price, spot=23000, strike=22500,
            t=7 / 365, r=0.07, option_type="PE"
        )
        assert abs(recovered_iv - known_iv) < 0.001

    def test_zero_premium_returns_zero(self):
        """Zero premium should return 0.0 IV."""
        iv = implied_volatility(
            option_price=0.0, spot=23000, strike=23000,
            t=5 / 365, r=0.07, option_type="CE"
        )
        assert iv == 0.0

    def test_negative_premium_returns_zero(self):
        """Negative premium should return 0.0 IV."""
        iv = implied_volatility(
            option_price=-5.0, spot=23000, strike=23000,
            t=5 / 365, r=0.07, option_type="CE"
        )
        assert iv == 0.0

    def test_deep_otm_low_premium(self):
        """Convergence on tiny premiums (deep OTM)."""
        # Deep OTM put with tiny premium
        iv = implied_volatility(
            option_price=0.50, spot=23000, strike=21000,
            t=3 / 365, r=0.07, option_type="PE"
        )
        # Should converge to something reasonable (not 0, not huge)
        assert 0.0 <= iv <= 3.0  # Wide range, just ensure convergence

    def test_expiry_day_handling(self):
        """Near-zero time to expiry should not crash."""
        # Expiry day: t very small
        iv = implied_volatility(
            option_price=50.0, spot=23000, strike=22950,
            t=1 / 365, r=0.07, option_type="CE"
        )
        assert iv >= 0.0  # Should not crash or return negative

    def test_known_nifty_values(self):
        """NIFTY at 23000, strike 23000 CE, premium 150, DTE 5 → IV ~12-16%."""
        iv = implied_volatility(
            option_price=150, spot=23000, strike=23000,
            t=5 / 365, r=0.07, option_type="CE"
        )
        assert 0.10 <= iv <= 0.20  # ~12-16% expected

    def test_high_premium_high_iv(self):
        """High premium should give high IV."""
        iv_low = implied_volatility(
            option_price=100, spot=23000, strike=23000,
            t=5 / 365, r=0.07, option_type="CE"
        )
        iv_high = implied_volatility(
            option_price=300, spot=23000, strike=23000,
            t=5 / 365, r=0.07, option_type="CE"
        )
        assert iv_high > iv_low


class TestTimeToExpiry:
    """Test time-to-expiry calculation."""

    def test_future_date(self):
        """Future expiry should give positive time."""
        from datetime import datetime, timedelta
        future = (datetime.now() + timedelta(days=5)).strftime("%d-%b-%Y")
        t = time_to_expiry_years(future)
        assert 4 / 365 <= t <= 6 / 365

    def test_today_returns_min(self):
        """Expiry today should return minimum (1/365)."""
        from datetime import datetime
        today = datetime.now().strftime("%d-%b-%Y")
        t = time_to_expiry_years(today)
        assert t == pytest.approx(1 / 365, abs=0.001)

    def test_past_date_returns_min(self):
        """Past expiry should return minimum (1/365)."""
        t = time_to_expiry_years("01-Jan-2020")
        assert t == pytest.approx(1 / 365, abs=0.001)

    def test_iso_format_date(self):
        """Should handle ISO format dates (YYYY-MM-DD) too."""
        from datetime import datetime, timedelta
        future = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        t = time_to_expiry_years(future)
        assert t > 0
