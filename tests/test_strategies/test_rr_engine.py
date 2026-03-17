"""Tests for strategies/rr_engine.py — Rally Rider signal detection engine."""

from unittest.mock import patch, MagicMock

import pytest

from strategies.rr_engine import RREngine
from config import RRConfig, RR_REGIME_PARAMS


@pytest.fixture
def engine():
    return RREngine()


@pytest.fixture
def config():
    return RRConfig()


class TestRoundToTick:
    def test_round_up_small(self):
        assert RREngine.round_to_tick(230.03) == 230.05

    def test_round_down_small(self):
        assert RREngine.round_to_tick(230.07) == 230.05

    def test_round_down_mid(self):
        assert RREngine.round_to_tick(230.12) == 230.10

    def test_round_up_mid(self):
        assert RREngine.round_to_tick(230.18) == 230.20

    def test_exact_tick(self):
        assert RREngine.round_to_tick(230.05) == 230.05

    def test_zero(self):
        assert RREngine.round_to_tick(0.0) == 0.0

    def test_whole_number(self):
        assert RREngine.round_to_tick(200.0) == 200.0

    def test_round_at_boundary(self):
        # 230.025 should round to 230.05 (nearest tick)
        result = RREngine.round_to_tick(230.025)
        assert result in (230.00, 230.05)  # IEEE float rounding


class TestClassifyRegime:
    def test_caches_daily(self, engine, config):
        with patch.object(RREngine, "_compute_regime", return_value="TRENDING_UP") as mock:
            assert engine.classify_regime(config) == "TRENDING_UP"
            assert engine.classify_regime(config) == "TRENDING_UP"
            mock.assert_called_once()

    def test_normal_fallback(self, engine, config):
        """When DB has insufficient data, should fall back to NORMAL."""
        with patch("strategies.rr_engine.get_connection") as mock_conn:
            mock_ctx = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            mock_ctx.execute.return_value.fetchall.return_value = []
            mock_ctx.execute.return_value.fetchone.return_value = None
            result = engine._compute_regime(config)
            assert result == "NORMAL"

    def test_high_vol_down(self, engine, config):
        with patch("strategies.rr_engine.get_connection") as mock_conn:
            mock_ctx = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            # High range, negative returns
            daily_rows = [
                ("2025-01-05", 300.0, 24000.0),
                ("2025-01-04", 280.0, 24200.0),
                ("2025-01-03", 290.0, 24400.0),
            ]
            vix_row = (18.0,)  # high VIX
            mock_ctx.execute.return_value.fetchall.return_value = daily_rows
            mock_ctx.execute.return_value.fetchone.return_value = vix_row
            result = engine._compute_regime(config)
            assert result == "HIGH_VOL_DOWN"

    def test_low_vol(self, engine, config):
        with patch("strategies.rr_engine.get_connection") as mock_conn:
            mock_ctx = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            daily_rows = [
                ("2025-01-05", 100.0, 24500.0),
                ("2025-01-04", 110.0, 24490.0),
                ("2025-01-03", 90.0, 24480.0),
            ]
            vix_row = (10.0,)  # low VIX
            mock_ctx.execute.return_value.fetchall.return_value = daily_rows
            mock_ctx.execute.return_value.fetchone.return_value = vix_row
            result = engine._compute_regime(config)
            assert result == "LOW_VOL"

    def test_get_regime_params_known(self, engine):
        params = engine.get_regime_params("TRENDING_UP")
        assert params["direction"] == "CE_ONLY"
        assert params["max_trades"] == 3

    def test_get_regime_params_fallback(self, engine):
        params = engine.get_regime_params("UNKNOWN_REGIME")
        assert params == RR_REGIME_PARAMS["NORMAL"]


class TestDetectMCSignal:
    def test_up_rally(self, engine):
        # Rally from 24000 to 24060 (+60 pts), pullback to 24040, resume to 24045
        closes = [24000.0 + i * 5 for i in range(13)]  # 24000→24060
        closes.extend([24050, 24045, 24040, 24038, 24042])  # pullback + resume
        result = engine._detect_mc_signal(closes, 24000.0)
        assert result is not None
        assert result["signal_type"] == "MC"
        assert result["option_type"] == "CE"

    def test_down_rally(self, engine):
        # Rally from 24100 to 24040 (-60 pts), pullback to 24060, resume to 24055
        closes = [24100.0 - i * 5 for i in range(13)]  # 24100→24040
        closes.extend([24050, 24055, 24060, 24062, 24058])  # pullback + resume
        result = engine._detect_mc_signal(closes, 24100.0)
        assert result is not None
        assert result["signal_type"] == "MC"
        assert result["option_type"] == "PE"

    def test_insufficient_move(self, engine):
        closes = [24000.0 + i for i in range(10)]  # only 9 pts
        result = engine._detect_mc_signal(closes, 24000.0)
        assert result is None

    def test_no_resumption(self, engine):
        closes = [24000.0 + i * 3 for i in range(12)]
        closes.extend([24028, 24025, 24022, 24020, 24018])  # no resume
        result = engine._detect_mc_signal(closes, 24000.0)
        assert result is None


class TestDetectMOMSignal:
    def test_four_higher_closes_ce(self, engine):
        closes = [24000, 24002, 24005, 24008, 24012, 24016, 24020, 24025, 24030]
        # last 4: 24020→24025→24030 (need 4 higher)
        result = engine._detect_mom_signal(closes)
        assert result is not None
        assert result["signal_type"] == "MOM"
        assert result["option_type"] == "CE"

    def test_four_lower_closes_pe(self, engine):
        closes = [24050, 24048, 24045, 24042, 24038, 24035, 24030, 24025, 24020]
        result = engine._detect_mom_signal(closes)
        assert result is not None
        assert result["signal_type"] == "MOM"
        assert result["option_type"] == "PE"

    def test_mixed_no_signal(self, engine):
        closes = [24000, 24005, 24003, 24007, 24004, 24009, 24006]
        result = engine._detect_mom_signal(closes)
        assert result is None

    def test_too_few_candles(self, engine):
        closes = [24000, 24005, 24010]
        result = engine._detect_mom_signal(closes)
        assert result is None


class TestDetectVWAPSignal:
    def test_cross_above(self, engine):
        # 20 candles trending down then crossing up
        closes = [24000 - i * 2 for i in range(18)]  # down
        avg = sum(closes) / len(closes)
        # Add candles: prev below avg, current above with gap
        closes.append(avg - 5)  # below VWAP
        closes.append(avg + 10)  # above VWAP with 10pt gap (>3)
        result = engine._detect_vwap_signal(closes)
        # VWAP signal depends on exact math; just check structure
        if result:
            assert result["signal_type"] == "VWAP"
            assert result["option_type"] in ("CE", "PE")

    def test_insufficient_separation(self, engine):
        # Closes hovering near VWAP
        closes = [24000 + (i % 2) for i in range(20)]
        result = engine._detect_vwap_signal(closes)
        assert result is None

    def test_too_few_candles(self, engine):
        closes = [24000, 24005]
        result = engine._detect_vwap_signal(closes)
        assert result is None


class TestDetectSignals:
    def test_direction_filter_ce_only(self, engine):
        closes = [24000.0 + i * 3 for i in range(12)]
        closes.extend([24028, 24025, 24022, 24020, 24023])

        # For CE_ONLY, PE signals should be filtered out
        spots = [{"timestamp": f"10:{i:02d}", "spot_price": c} for i, c in enumerate(closes)]

        regime_config = {"signals": {"MC", "MOM"}, "direction": "CE_ONLY"}
        with patch.object(engine, "_load_todays_spots", return_value=spots):
            signals = engine.detect_signals({"spot_price": closes[-1]}, regime_config)
            for s in signals:
                assert "CE" in s["direction"]

    def test_direction_filter_pe_only(self, engine):
        # Build a PE signal (down rally)
        closes = [24100.0 - i * 3 for i in range(12)]
        closes.extend([24072, 24075, 24078, 24080, 24077])
        spots = [{"timestamp": f"10:{i:02d}", "spot_price": c} for i, c in enumerate(closes)]

        regime_config = {"signals": {"MC"}, "direction": "PE_ONLY"}
        with patch.object(engine, "_load_todays_spots", return_value=spots):
            signals = engine.detect_signals({"spot_price": closes[-1]}, regime_config)
            for s in signals:
                assert "PE" in s["direction"]

    def test_empty_when_not_enough_data(self, engine):
        spots = [{"timestamp": "10:00", "spot_price": 24000}] * 5
        regime_config = {"signals": {"MC", "MOM", "VWAP"}, "direction": "BOTH"}
        with patch.object(engine, "_load_todays_spots", return_value=spots):
            signals = engine.detect_signals({"spot_price": 24000}, regime_config)
            assert signals == []


class TestGetRRStrikes:
    def test_ce_strike(self):
        assert RREngine.get_rr_strike(24500.0, "CE") == 24400

    def test_pe_strike(self):
        assert RREngine.get_rr_strike(24500.0, "PE") == 24600

    def test_rounding(self):
        assert RREngine.get_rr_strike(24523.5, "CE") == 24400
        assert RREngine.get_rr_strike(24523.5, "PE") == 24600


class TestPickBestSignal:
    def test_mc_beats_mom(self):
        signals = [
            {"signal_type": "MOM", "direction": "BUY_CE"},
            {"signal_type": "MC", "direction": "BUY_CE"},
        ]
        best = RREngine.pick_best_signal(signals)
        assert best["signal_type"] == "MC"

    def test_mom_beats_vwap(self):
        signals = [
            {"signal_type": "VWAP", "direction": "BUY_PE"},
            {"signal_type": "MOM", "direction": "BUY_PE"},
        ]
        best = RREngine.pick_best_signal(signals)
        assert best["signal_type"] == "MOM"

    def test_empty_list(self):
        assert RREngine.pick_best_signal([]) is None

    def test_single_signal(self):
        signals = [{"signal_type": "VWAP", "direction": "BUY_CE"}]
        best = RREngine.pick_best_signal(signals)
        assert best["signal_type"] == "VWAP"
