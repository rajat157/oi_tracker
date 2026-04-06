"""Tests for strategies/premium_engine.py — hybrid chart builder + NIFTY formatter."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from strategies.premium_engine import PremiumEngine


@pytest.fixture
def engine():
    return PremiumEngine()


def _ohlc(closes, start_hour=10):
    """Build Kite-shape OHLC candles with a datetime in the 'date' field."""
    return [
        {
            "date": datetime(2026, 4, 6, start_hour, i, 0),
            "open": c - 0.5,
            "high": c + 1,
            "low": c - 1,
            "close": c,
            "volume": 100 + i,
        }
        for i, c in enumerate(closes)
    ]


class TestBuildPremiumChartFromOhlc:
    """Tests for the hybrid chart builder.

    After the CandleBuilder refactor, candles are passed in directly as
    `ce_candles` + `pe_candles` (from CandleBuilder via the analysis dict).
    The hybrid IV/OI merge from oi_snapshots is unchanged.
    """

    def test_basic_merge(self, engine):
        """Real OHLC + IV/OI map → merged candles with both."""
        ce_candles = _ohlc([300, 305, 310, 315, 320])
        pe_candles = _ohlc([200, 198, 195, 192, 190])
        # oi_snapshots rows for IV/OI by HH:MM (matches start_hour=10, i=0..4)
        iv_oi_rows_ce = [
            ("2026-04-06T10:00:05", 15.1, 100000),
            ("2026-04-06T10:01:05", 15.2, 101000),
            ("2026-04-06T10:02:05", 15.3, 102000),
            ("2026-04-06T10:03:05", 15.4, 103000),
            ("2026-04-06T10:04:05", 15.5, 104000),
        ]
        iv_oi_rows_pe = [
            ("2026-04-06T10:00:05", 16.1, 200000),
            ("2026-04-06T10:01:05", 16.2, 201000),
            ("2026-04-06T10:02:05", 16.3, 202000),
            ("2026-04-06T10:03:05", 16.4, 203000),
            ("2026-04-06T10:04:05", 16.5, 204000),
        ]

        with patch("strategies.premium_engine.get_connection") as mock_conn:
            ctx = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            # Two sequential queries: CE then PE
            ctx.execute.return_value.fetchall.side_effect = [iv_oi_rows_ce, iv_oi_rows_pe]

            chart = engine.build_premium_chart_from_ohlc(
                spot_price=24000.0,
                ce_strike=23900,
                pe_strike=24100,
                ce_candles=ce_candles,
                pe_candles=pe_candles,
            )

        assert chart is not None
        assert chart["ce_strike"] == 23900
        assert chart["pe_strike"] == 24100
        assert chart["spot_price"] == 24000.0
        assert len(chart["ce_candles"]) == 5
        assert len(chart["pe_candles"]) == 5
        # LTP should be the CE close (real OHLC), not an oi_snapshots value
        assert chart["ce_candles"][-1]["ltp"] == 320
        assert chart["pe_candles"][-1]["ltp"] == 190
        # IV/OI should be merged from the mock DB rows
        assert chart["ce_candles"][-1]["iv"] == 15.5
        assert chart["ce_candles"][-1]["oi"] == 104000
        assert chart["pe_candles"][-1]["iv"] == 16.5

    def test_empty_candles_returns_none(self, engine):
        """Caller passes empty candle lists → chart is None."""
        chart = engine.build_premium_chart_from_ohlc(
            spot_price=24000.0,
            ce_strike=23900,
            pe_strike=24100,
            ce_candles=[],
            pe_candles=[],
        )
        assert chart is None

    def test_iv_oi_fallback_on_missing_timestamp(self, engine):
        """When OHLC timestamp has no matching IV/OI row, carry forward last value."""
        ce_candles = _ohlc([300, 305, 310, 315, 320])  # 10:00 - 10:04
        pe_candles = _ohlc([200, 198, 195, 192, 190])
        # Only provide IV/OI for 10:00, 10:01, 10:02; 10:03 and 10:04 are missing
        iv_oi_rows_ce = [
            ("2026-04-06T10:00:05", 15.1, 100000),
            ("2026-04-06T10:01:05", 15.2, 101000),
            ("2026-04-06T10:02:05", 15.3, 102000),
        ]
        iv_oi_rows_pe = []  # No PE data at all

        with patch("strategies.premium_engine.get_connection") as mock_conn:
            ctx = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            ctx.execute.return_value.fetchall.side_effect = [iv_oi_rows_ce, iv_oi_rows_pe]

            chart = engine.build_premium_chart_from_ohlc(
                spot_price=24000.0,
                ce_strike=23900,
                pe_strike=24100,
                ce_candles=ce_candles,
                pe_candles=pe_candles,
            )

        assert chart is not None
        # Last CE candle (10:04) has no matching row — should carry forward 15.3
        assert chart["ce_candles"][-1]["iv"] == 15.3
        assert chart["ce_candles"][-1]["oi"] == 102000
        # PE has no data at all — iv/oi stays 0
        assert chart["pe_candles"][-1]["iv"] == 0.0

    def test_preserves_ltp_as_close(self, engine):
        """Regression: ltp field must be the candle close, not an IV/OI lookup."""
        ce_candles = [{
            "date": datetime(2026, 4, 6, 10, 0, 0),
            "open": 99, "high": 105, "low": 95, "close": 100, "volume": 50,
        }] * 5  # 5 identical candles
        pe_candles = [{
            "date": datetime(2026, 4, 6, 10, 0, 0),
            "open": 48, "high": 52, "low": 46, "close": 50, "volume": 30,
        }] * 5

        with patch("strategies.premium_engine.get_connection") as mock_conn:
            ctx = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            ctx.execute.return_value.fetchall.return_value = []

            chart = engine.build_premium_chart_from_ohlc(
                spot_price=24000.0,
                ce_strike=23900,
                pe_strike=24100,
                ce_candles=ce_candles,
                pe_candles=pe_candles,
            )

        # All ltps should be the close (100), not open (99) or anything else
        for c in chart["ce_candles"]:
            assert c["ltp"] == 100


class TestFormatNiftyOhlcForPrompt:
    def test_basic_format(self, engine):
        candles = _ohlc([24000, 24005, 24010, 24015, 24020])
        out = PremiumEngine.format_nifty_ohlc_for_prompt(
            candles, label="NIFTY 1-min")
        assert "NIFTY 1-min" in out
        assert "| Time |" in out
        assert "24000.0" in out
        assert "24020.0" in out
        # Summary line with last-5 closes
        assert "Last 5 closes:" in out

    def test_empty_returns_no_data_message(self, engine):
        out = PremiumEngine.format_nifty_ohlc_for_prompt([], label="NIFTY 3-min")
        assert "no data" in out.lower()
        assert "NIFTY 3-min" in out

    def test_max_rows_truncation(self, engine):
        candles = _ohlc(list(range(24000, 24050)))  # 50 candles
        out = PremiumEngine.format_nifty_ohlc_for_prompt(
            candles, label="NIFTY 3-min", max_rows=10)
        # Only last 10 candles should appear
        assert "(last 10 candles)" in out
        # First candle should NOT appear (24000)
        assert "| 24000.0 |" not in out
        # Last candle should appear (24049)
        assert "24049.0" in out

    def test_summary_direction_arrows(self, engine):
        """Rising closes produce 'up' arrows in summary."""
        candles = _ohlc([100, 101, 102, 103, 104, 105])
        out = PremiumEngine.format_nifty_ohlc_for_prompt(
            candles, label="NIFTY 1-min")
        assert "up up up up" in out


class TestLoadIvOiMap:
    def test_hhmm_key_format(self):
        """Keys in the map are HH:MM extracted from timestamp strings."""
        rows = [
            ("2026-04-06T10:12:05", 15.5, 100000),
            ("2026-04-06T10:15:05", 15.6, 101000),
        ]
        with patch("strategies.premium_engine.get_connection") as mock_conn:
            ctx = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            ctx.execute.return_value.fetchall.return_value = rows
            result = PremiumEngine._load_iv_oi_map("2026-04-06", 23900, "CE")
        assert "10:12" in result
        assert "10:15" in result
        assert result["10:12"] == (15.5, 100000)
