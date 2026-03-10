"""Tests for analysis/ package imports."""

import pytest


class TestAnalysisImports:
    def test_main_import(self):
        from analysis import analyze_tug_of_war
        assert callable(analyze_tug_of_war)

    def test_regime_detector(self):
        from analysis.regime_detector import detect_market_regime, calculate_market_trend
        assert callable(detect_market_regime)
        assert callable(calculate_market_trend)

    def test_confirmation(self):
        from analysis.confirmation import calculate_signal_confidence
        assert callable(calculate_signal_confidence)

    def test_momentum(self):
        from analysis.momentum import (
            calculate_price_momentum,
            calculate_premium_momentum,
            calculate_oi_acceleration,
            calculate_pcr_trend,
            calculate_max_pain_drift,
        )
        assert callable(calculate_price_momentum)
        assert callable(calculate_pcr_trend)
