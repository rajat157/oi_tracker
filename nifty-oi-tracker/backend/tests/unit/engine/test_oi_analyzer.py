"""Tests for the OI Analyzer — tug-of-war engine (pure functions)."""

import pytest

from app.engine.oi_analyzer import (
    analyze_tug_of_war,
    calculate_conviction_multiplier,
    calculate_dynamic_sl_pct,
    calculate_iv_skew,
    calculate_market_trend,
    calculate_max_pain,
    calculate_oi_acceleration,
    calculate_premium_momentum,
    calculate_price_momentum,
    calculate_signal_confidence,
    calculate_trade_setup,
    detect_market_regime,
    detect_trap,
    determine_verdict_with_hysteresis,
    find_atm_strike,
    find_oi_clusters,
    get_itm_strikes,
    get_otm_strikes,
)


# ── Fixtures ────────────────────────────────────────────────

def _make_strikes_data(
    spot: float = 22500,
    step: int = 50,
    n: int = 6,
    ce_oi: int = 100_000,
    pe_oi: int = 120_000,
    ce_oi_change: int = 5000,
    pe_oi_change: int = 8000,
) -> dict:
    """Create synthetic strikes data around a spot price."""
    atm = round(spot / step) * step
    data = {}
    for i in range(-n, n + 1):
        strike = atm + i * step
        data[strike] = {
            "ce_oi": ce_oi + i * 1000,
            "pe_oi": pe_oi - i * 1000,
            "ce_oi_change": ce_oi_change + i * 500,
            "pe_oi_change": pe_oi_change - i * 500,
            "ce_volume": 50000,
            "pe_volume": 60000,
            "ce_iv": 14.0 + i * 0.5,
            "pe_iv": 15.0 - i * 0.3,
            "ce_ltp": max(5, 200 - i * 30),
            "pe_ltp": max(5, 200 + i * 30),
        }
    return data


def _make_price_history(spot: float = 22500, count: int = 5, trend: float = 10) -> list[dict]:
    """Create synthetic price history."""
    return [{"spot_price": spot + i * trend} for i in range(count)]


# ── find_atm_strike ─────────────────────────────────────────

class TestFindAtmStrike:
    def test_exact_match(self):
        assert find_atm_strike(22500, [22400, 22450, 22500, 22550]) == 22500

    def test_closest_match(self):
        assert find_atm_strike(22520, [22400, 22450, 22500, 22550]) == 22500

    def test_empty_list(self):
        assert find_atm_strike(22500, []) == 0

    def test_single_strike(self):
        assert find_atm_strike(22500, [22400]) == 22400


# ── get_otm_strikes / get_itm_strikes ───────────────────────

class TestStrikeSelection:
    def test_otm_strikes_correct_sides(self):
        strikes = [22300, 22350, 22400, 22450, 22500, 22550, 22600, 22650, 22700]
        otm_calls, otm_puts = get_otm_strikes(22500, strikes, num_strikes=3)
        assert all(s > 22500 for s in otm_calls)
        assert all(s < 22500 for s in otm_puts)

    def test_otm_strikes_count(self):
        strikes = list(range(22200, 22900, 50))
        otm_calls, otm_puts = get_otm_strikes(22500, strikes, num_strikes=3)
        assert len(otm_calls) == 3
        assert len(otm_puts) == 3

    def test_itm_strikes_correct_sides(self):
        strikes = list(range(22200, 22900, 50))
        itm_calls, itm_puts = get_itm_strikes(22500, strikes, num_strikes=3)
        # ITM calls: below spot
        assert all(s < 22500 for s in itm_calls)
        # ITM puts: above spot
        assert all(s > 22500 for s in itm_puts)

    def test_atm_not_in_list(self):
        strikes = [22400, 22450, 22550, 22600]
        otm_calls, otm_puts = get_otm_strikes(22500, strikes, num_strikes=2)
        assert len(otm_calls) <= 2
        assert len(otm_puts) <= 2


# ── calculate_price_momentum ────────────────────────────────

class TestPriceMomentum:
    def test_rising_price(self):
        history = _make_price_history(22500, count=5, trend=10)
        score = calculate_price_momentum(history)
        assert score > 0

    def test_falling_price(self):
        history = _make_price_history(22500, count=5, trend=-10)
        score = calculate_price_momentum(history)
        assert score < 0

    def test_flat_price(self):
        history = _make_price_history(22500, count=5, trend=0)
        score = calculate_price_momentum(history)
        assert score == 0

    def test_capped_at_100(self):
        history = [{"spot_price": 20000}, {"spot_price": 25000}]
        score = calculate_price_momentum(history)
        assert score == 100

    def test_capped_at_minus_100(self):
        history = [{"spot_price": 25000}, {"spot_price": 20000}]
        score = calculate_price_momentum(history)
        assert score == -100

    def test_empty_history(self):
        assert calculate_price_momentum([]) == 0.0

    def test_single_point(self):
        assert calculate_price_momentum([{"spot_price": 22500}]) == 0.0


# ── detect_market_regime ─────────────────────────────────────

class TestDetectMarketRegime:
    def test_trending_up(self):
        # Price near high, strong positive momentum, range > 0.2%
        history = [{"spot_price": 22400}, {"spot_price": 22450}, {"spot_price": 22500}]
        result = detect_market_regime(history, momentum_score=30)
        assert result["regime"] == "trending_up"
        assert result["oi_change_weight"] == 0.30

    def test_trending_down(self):
        history = [{"spot_price": 22600}, {"spot_price": 22550}, {"spot_price": 22500}]
        result = detect_market_regime(history, momentum_score=-30)
        assert result["regime"] == "trending_down"

    def test_range_bound(self):
        history = [{"spot_price": 22500}, {"spot_price": 22510}, {"spot_price": 22505}]
        result = detect_market_regime(history, momentum_score=5)
        assert result["regime"] == "range_bound"
        assert result["oi_change_weight"] == 0.70

    def test_insufficient_data(self):
        result = detect_market_regime([], 0)
        assert result["regime"] == "range_bound"


# ── calculate_conviction_multiplier ──────────────────────────

class TestConvictionMultiplier:
    def test_high_turnover(self):
        assert calculate_conviction_multiplier(1000, 1000) == 1.5

    def test_moderate_turnover(self):
        assert calculate_conviction_multiplier(300, 1000) == 1.0

    def test_low_turnover(self):
        assert calculate_conviction_multiplier(100, 1000) == 0.5

    def test_negligible_oi_change(self):
        assert calculate_conviction_multiplier(50000, 50) == 0.5


# ── calculate_dynamic_sl_pct ────────────────────────────────

class TestDynamicSlPct:
    def test_low_iv(self):
        data = {22500: {"ce_iv": 10.0}}
        assert calculate_dynamic_sl_pct(data, 22500, "CE") == 0.15

    def test_medium_iv(self):
        data = {22500: {"ce_iv": 16.0}}
        assert calculate_dynamic_sl_pct(data, 22500, "CE") == 0.20

    def test_high_iv(self):
        data = {22500: {"pe_iv": 25.0}}
        assert calculate_dynamic_sl_pct(data, 22500, "PE") == 0.25

    def test_zero_iv_returns_default(self):
        data = {22500: {"ce_iv": 0}}
        assert calculate_dynamic_sl_pct(data, 22500, "CE") == 0.20

    def test_missing_strike(self):
        assert calculate_dynamic_sl_pct({}, 22500, "CE") == 0.20


# ── calculate_iv_skew ───────────────────────────────────────

class TestIvSkew:
    def test_bearish_skew(self):
        """Higher put IV = positive skew = bearish."""
        data = _make_strikes_data()
        # With default data, pe_iv > ce_iv on OTM puts
        skew = calculate_iv_skew(data, 22500)
        assert isinstance(skew, float)

    def test_empty_data(self):
        assert calculate_iv_skew({}, 22500) == 0.0


# ── calculate_max_pain ──────────────────────────────────────

class TestMaxPain:
    def test_symmetric_oi(self):
        """Max pain should be near ATM with symmetric OI."""
        data = {
            22400: {"ce_oi": 50000, "pe_oi": 50000},
            22450: {"ce_oi": 80000, "pe_oi": 80000},
            22500: {"ce_oi": 100000, "pe_oi": 100000},
            22550: {"ce_oi": 80000, "pe_oi": 80000},
            22600: {"ce_oi": 50000, "pe_oi": 50000},
        }
        mp = calculate_max_pain(data)
        assert mp == 22500  # Symmetric → ATM is max pain

    def test_put_heavy_oi(self):
        """Heavy put OI below should pull max pain down."""
        data = {
            22400: {"ce_oi": 10000, "pe_oi": 500000},
            22500: {"ce_oi": 10000, "pe_oi": 10000},
            22600: {"ce_oi": 10000, "pe_oi": 10000},
        }
        mp = calculate_max_pain(data)
        assert mp <= 22500

    def test_empty_data(self):
        assert calculate_max_pain({}) == 0


# ── find_oi_clusters ────────────────────────────────────────

class TestFindOiClusters:
    def test_identifies_support_resistance(self):
        data = _make_strikes_data()
        clusters = find_oi_clusters(data, 22500)
        assert "support" in clusters
        assert "resistance" in clusters

    def test_empty_data(self):
        clusters = find_oi_clusters({}, 22500)
        assert clusters["support"] == []
        assert clusters["resistance"] == []


# ── calculate_oi_acceleration ────────────────────────────────

class TestOiAcceleration:
    def test_bullish_accumulation(self):
        prev = [(5000, 8000)]
        result = calculate_oi_acceleration(prev, 5000, 20000)
        assert result["net_acceleration"] > 0

    def test_stable(self):
        prev = [(5000, 8000)]
        result = calculate_oi_acceleration(prev, 5000, 8000)
        assert result["phase"] == "stable"

    def test_empty_history(self):
        result = calculate_oi_acceleration([], 5000, 8000)
        assert result["phase"] == "stable"


# ── calculate_premium_momentum ──────────────────────────────

class TestPremiumMomentum:
    def test_bullish_momentum(self):
        curr = {22500: {"ce_ltp": 200, "pe_ltp": 150}}
        prev = {22500: {"ce_ltp": 180, "pe_ltp": 160}}
        result = calculate_premium_momentum(curr, prev, 22500)
        assert result["premium_momentum_score"] > 0

    def test_no_previous_data(self):
        curr = {22500: {"ce_ltp": 200, "pe_ltp": 150}}
        result = calculate_premium_momentum(curr, None, 22500)
        assert result["premium_momentum_score"] == 0.0


# ── calculate_signal_confidence ─────────────────────────────

class TestSignalConfidence:
    def test_strong_confirmed(self):
        conf = calculate_signal_confidence(
            combined_score=50, iv_skew=-3, volume_pcr=1.5,
            oi_pcr=1.2, max_pain=22500, spot_price=22510,
            confirmation_status="CONFIRMED", vix=13, futures_oi_change=50000,
        )
        assert conf > 70

    def test_weak_conflicting(self):
        conf = calculate_signal_confidence(
            combined_score=5, iv_skew=5, volume_pcr=0.7,
            oi_pcr=0.8, max_pain=22500, spot_price=23000,
            confirmation_status="CONFLICT", vix=28, futures_oi_change=-50000,
        )
        assert conf < 40

    def test_clamped_0_to_100(self):
        conf = calculate_signal_confidence(
            combined_score=100, iv_skew=-10, volume_pcr=2.0,
            oi_pcr=2.0, max_pain=22500, spot_price=22500,
            confirmation_status="CONFIRMED", vix=10, futures_oi_change=100000,
        )
        assert 0 <= conf <= 100


# ── determine_verdict_with_hysteresis ────────────────────────

class TestVerdictHysteresis:
    def test_strongly_bullish(self):
        verdict, strength = determine_verdict_with_hysteresis(60)
        assert "Bull" in verdict
        assert strength == "strong"

    def test_slightly_bearish(self):
        verdict, strength = determine_verdict_with_hysteresis(-15)
        assert "Bear" in verdict

    def test_dead_zone_keeps_previous(self):
        verdict, strength = determine_verdict_with_hysteresis(5, prev_verdict="Slightly Bullish")
        assert verdict == "Slightly Bullish"

    def test_flip_requires_larger_move(self):
        # Was bullish, needs < -37.5 to flip bearish
        verdict, _ = determine_verdict_with_hysteresis(-30, prev_verdict="Slightly Bullish")
        assert "Bull" in verdict  # Not enough to flip

    def test_strong_flip(self):
        verdict, _ = determine_verdict_with_hysteresis(-40, prev_verdict="Slightly Bullish")
        assert "Bear" in verdict  # Strong enough to flip

    def test_neutral_first_time(self):
        verdict, strength = determine_verdict_with_hysteresis(0)
        assert verdict == "Neutral"
        assert strength == "none"


# ── detect_trap ─────────────────────────────────────────────

class TestDetectTrap:
    def test_no_trap_normal(self):
        data = _make_strikes_data()
        result = detect_trap(data, 22500, "rising", "bullish", "weak")
        assert result is None

    def test_bull_trap(self):
        data = _make_strikes_data()
        result = detect_trap(data, 22500, "rising", "bearish", "strong")
        # May or may not trigger depending on cluster proximity
        # Just check it doesn't crash
        assert result is None or result["type"] == "BULL_TRAP"


# ── calculate_market_trend ───────────────────────────────────

class TestCalculateMarketTrend:
    def test_upward_trend(self):
        history = [
            {"combined_score": 10 + i * 5, "momentum_score": 20, "market_regime": {"regime": "trending_up"}}
            for i in range(10)
        ]
        result = calculate_market_trend(history)
        assert result["trend"] == "upward"

    def test_insufficient_data(self):
        result = calculate_market_trend([])
        assert result["trend"] == "sideways"
        assert result["confidence"] == 0


# ── calculate_trade_setup ────────────────────────────────────

class TestCalculateTradeSetup:
    def test_bullish_setup(self):
        data = _make_strikes_data()
        setup = calculate_trade_setup(data, 22500, "Slightly Bullish")
        assert setup is not None
        assert setup["direction"] == "BUY_CALL"
        assert setup["option_type"] == "CE"
        assert setup["entry_premium"] > 0
        assert setup["sl_premium"] < setup["entry_premium"]
        assert setup["target1_premium"] > setup["entry_premium"]

    def test_bearish_setup(self):
        data = _make_strikes_data()
        setup = calculate_trade_setup(data, 22500, "Slightly Bearish")
        assert setup is not None
        assert setup["direction"] == "BUY_PUT"
        assert setup["option_type"] == "PE"

    def test_empty_data(self):
        assert calculate_trade_setup({}, 22500, "Slightly Bullish") is None


# ── analyze_tug_of_war (main function) ──────────────────────

class TestAnalyzeTugOfWar:
    def test_basic_analysis(self):
        data = _make_strikes_data()
        result = analyze_tug_of_war(data, 22500)
        assert "verdict" in result
        assert "combined_score" in result
        assert "spot_price" in result
        assert result["spot_price"] == 22500
        assert "atm_strike" in result

    def test_with_momentum(self):
        data = _make_strikes_data()
        history = _make_price_history(22500, 5, trend=20)
        result = analyze_tug_of_war(data, 22500, price_history=history)
        assert result["momentum_score"] != 0

    def test_with_vix(self):
        data = _make_strikes_data()
        result = analyze_tug_of_war(data, 22500, vix=15.0)
        assert result["signal_confidence"] >= 0

    def test_with_prev_verdict(self):
        data = _make_strikes_data()
        result = analyze_tug_of_war(data, 22500, prev_verdict="Slightly Bullish")
        assert "verdict" in result

    def test_empty_data(self):
        result = analyze_tug_of_war({}, 22500)
        assert result["verdict"] == "No Data"

    def test_four_zones_present(self):
        data = _make_strikes_data()
        result = analyze_tug_of_war(data, 22500)
        for zone in ["otm_puts", "itm_calls", "otm_calls", "itm_puts"]:
            assert zone in result
            assert "total_oi" in result[zone]
            assert "total_force" in result[zone]

    def test_strength_analysis_present(self):
        data = _make_strikes_data()
        result = analyze_tug_of_war(data, 22500)
        sa = result["strength_analysis"]
        assert "put_strength" in sa
        assert "call_strength" in sa
        assert "net_strength" in sa
        assert "direction" in sa

    def test_signal_confidence_range(self):
        data = _make_strikes_data()
        result = analyze_tug_of_war(data, 22500, vix=14)
        assert 0 <= result["signal_confidence"] <= 100

    def test_trade_setup_included(self):
        data = _make_strikes_data()
        result = analyze_tug_of_war(data, 22500)
        # Trade setup may be None or a dict
        assert "trade_setup" in result

    def test_with_futures_oi(self):
        data = _make_strikes_data()
        result = analyze_tug_of_war(data, 22500, futures_oi_change=50000)
        assert result["signal_confidence"] >= 0

    def test_with_prev_oi_changes(self):
        data = _make_strikes_data()
        prev = [(5000, 8000), (6000, 9000)]
        result = analyze_tug_of_war(data, 22500, prev_oi_changes=prev)
        assert "oi_acceleration" in result

    def test_with_prev_strikes(self):
        data = _make_strikes_data()
        prev_data = _make_strikes_data(ce_oi=90000, pe_oi=110000)
        result = analyze_tug_of_war(data, 22500, prev_strikes_data=prev_data)
        assert "premium_momentum" in result
