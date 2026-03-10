"""Tests for core/analysis.py — AnalysisResult dataclass."""

import pytest

from core.analysis import AnalysisResult


class TestAnalysisResult:
    def test_defaults(self):
        r = AnalysisResult()
        assert r.verdict == "No Data"
        assert r.spot_price == 0.0
        assert r.combined_score == 0.0

    def test_from_dict_basic(self):
        d = {
            "spot_price": 24500.0,
            "atm_strike": 24500,
            "verdict": "Bulls Winning",
            "combined_score": 42.5,
            "signal_confidence": 72.0,
            "pcr": 0.85,
        }
        r = AnalysisResult.from_dict(d)
        assert r.spot_price == 24500.0
        assert r.verdict == "Bulls Winning"
        assert r.signal_confidence == 72.0

    def test_from_dict_extra_keys(self):
        d = {
            "verdict": "Slightly Bearish",
            "some_future_field": [1, 2, 3],
        }
        r = AnalysisResult.from_dict(d)
        assert r.verdict == "Slightly Bearish"
        assert r._extra["some_future_field"] == [1, 2, 3]

    def test_to_dict_round_trip(self):
        d = {
            "spot_price": 24600.0,
            "verdict": "Dead Zone",
            "iv_skew": -0.5,
            "custom_key": "preserved",
        }
        r = AnalysisResult.from_dict(d)
        out = r.to_dict()
        assert out["spot_price"] == 24600.0
        assert out["custom_key"] == "preserved"
        assert out["iv_skew"] == -0.5

    def test_dict_style_access(self):
        r = AnalysisResult(verdict="Neutral", signal_confidence=55.0)
        assert r["verdict"] == "Neutral"
        assert r["signal_confidence"] == 55.0

    def test_dict_style_access_extra(self):
        r = AnalysisResult.from_dict({"verdict": "OK", "foo": "bar"})
        assert r["foo"] == "bar"

    def test_get_method(self):
        r = AnalysisResult(verdict="Test")
        assert r.get("verdict") == "Test"
        assert r.get("nonexistent", 42) == 42

    def test_from_none(self):
        r = AnalysisResult.from_dict(None)
        assert r.verdict == "No Data"

    def test_nested_dicts_default_empty(self):
        r = AnalysisResult()
        assert r.otm_puts == {}
        assert r.trade_setup == {}
        assert r.oi_flow_summary == {}
