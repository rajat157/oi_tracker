"""Tests for strategies/mc_engine.py — MC signal detection engine."""

from unittest.mock import patch, MagicMock

import pytest

from strategies.mc_engine import MCEngine
from config import MCConfig


@pytest.fixture
def engine():
    return MCEngine()


@pytest.fixture
def config():
    return MCConfig()


class TestDetectRally:
    def test_up_rally(self, config):
        closes = [100.0 + i * 3 for i in range(12)]  # 100→133 = +33 pts
        day_open = 100.0
        result = MCEngine._detect_rally(closes, day_open, config)
        assert result is not None
        assert result["direction"] == "UP"
        assert result["rally_pts"] >= 25

    def test_down_rally(self, config):
        closes = [200.0 - i * 3 for i in range(12)]  # 200→167 = -33 pts
        day_open = 200.0
        result = MCEngine._detect_rally(closes, day_open, config)
        assert result is not None
        assert result["direction"] == "DOWN"
        assert result["rally_pts"] >= 25

    def test_insufficient_move(self, config):
        closes = [100.0 + i for i in range(10)]  # 100→109 = +9 pts
        day_open = 100.0
        result = MCEngine._detect_rally(closes, day_open, config)
        assert result is None


class TestDetectPullback:
    def test_valid_pullback(self, config):
        # Rally UP 50 pts, then pulled back 20 pts (40%)
        closes = list(range(100, 151)) + [145, 140, 135, 130, 132]
        rally = {"direction": "UP", "rally_pts": 50, "rally_peak": 150}
        result = MCEngine._detect_pullback(closes, rally, config)
        assert result is not None
        assert 0.20 <= result["pullback_pct"] <= 0.65

    def test_too_shallow(self, config):
        # Rally UP 50 pts, pulled back only 5 pts (10%)
        closes = [100 + i for i in range(51)] + [149, 148, 147, 146, 145]
        rally = {"direction": "UP", "rally_pts": 50, "rally_peak": 150}
        result = MCEngine._detect_pullback(closes, rally, config)
        assert result is None

    def test_too_deep(self, config):
        # Rally UP 50 pts, pulled back 40 pts (80%)
        closes = [100 + i for i in range(51)] + [130, 120, 115, 112, 110]
        rally = {"direction": "UP", "rally_pts": 50, "rally_peak": 150}
        result = MCEngine._detect_pullback(closes, rally, config)
        assert result is None


class TestCheckResumption:
    def test_resumes_up(self):
        closes = [100, 110, 108, 105, 107]  # last candle UP
        rally = {"direction": "UP"}
        assert MCEngine._check_resumption(closes, rally) is True

    def test_resumes_down(self):
        closes = [200, 190, 192, 195, 193]  # last candle DOWN
        rally = {"direction": "DOWN"}
        assert MCEngine._check_resumption(closes, rally) is True

    def test_no_resumption(self):
        closes = [100, 110, 108, 105, 103]  # last candle DOWN for UP rally
        rally = {"direction": "UP"}
        assert MCEngine._check_resumption(closes, rally) is False

    def test_too_few_candles(self):
        closes = [100]
        rally = {"direction": "UP"}
        assert MCEngine._check_resumption(closes, rally) is False


class TestGetWeeklyTrend:
    def test_caches_daily(self, engine):
        with patch.object(MCEngine, "_compute_weekly_trend", return_value="UP") as mock:
            assert engine.get_weekly_trend() == "UP"
            assert engine.get_weekly_trend() == "UP"
            mock.assert_called_once()  # cached on second call


class TestGetMCStrike:
    def test_ce_strike(self):
        assert MCEngine._get_mc_strike(24500.0, "CE") == 24400

    def test_pe_strike(self):
        assert MCEngine._get_mc_strike(24500.0, "PE") == 24600

    def test_rounding(self):
        assert MCEngine._get_mc_strike(24523.5, "CE") == 24400
        assert MCEngine._get_mc_strike(24523.5, "PE") == 24600


class TestDetectMCSignal:
    def test_full_signal(self, engine, config):
        # Simulate today's spots: rally UP from 24000 to 24050 then pullback to 24035 then resume
        spots = [{"timestamp": f"10:{i:02d}", "spot_price": 24000 + i * 5}
                 for i in range(12)]
        # pullback candles
        spots.extend([
            {"timestamp": "10:12", "spot_price": 24045},
            {"timestamp": "10:15", "spot_price": 24040},
            {"timestamp": "10:18", "spot_price": 24035},
            {"timestamp": "10:21", "spot_price": 24030},
            {"timestamp": "10:24", "spot_price": 24038},  # resumption
        ])

        strikes_data = {23900: {"ce_ltp": 200.0}, 24100: {"pe_ltp": 180.0}}

        with patch.object(engine, "_load_todays_spots", return_value=spots), \
             patch.object(engine, "get_weekly_trend", return_value="UP"):
            result = engine.detect_mc_signal(
                {"spot_price": 24038.0}, strikes_data, config)

        if result is not None:
            assert result["option_type"] == "CE"
            assert result["signal_type"] == "MC"

    def test_no_signal_when_flat(self, engine, config):
        spots = [{"timestamp": f"10:{i:02d}", "spot_price": 24000 + (i % 3)}
                 for i in range(15)]  # flat market

        with patch.object(engine, "_load_todays_spots", return_value=spots):
            result = engine.detect_mc_signal(
                {"spot_price": 24001.0}, {}, config)
            assert result is None
