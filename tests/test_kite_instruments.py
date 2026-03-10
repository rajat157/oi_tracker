"""Tests for Kite Instruments map."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import date, timedelta
from io import StringIO

# Sample instrument CSV data (matches Kite's actual CSV format)
SAMPLE_CSV = """instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange
11536386,45064,NIFTY2620623000CE,NIFTY,0,2026-02-06,23000,0.05,75,CE,NFO-OPT,NFO
11536642,45065,NIFTY2620623000PE,NIFTY,0,2026-02-06,23000,0.05,75,PE,NFO-OPT,NFO
11537154,45066,NIFTY2620623050CE,NIFTY,0,2026-02-06,23050,0.05,75,CE,NFO-OPT,NFO
11537410,45067,NIFTY2620623050PE,NIFTY,0,2026-02-06,23050,0.05,75,PE,NFO-OPT,NFO
11537666,45068,NIFTY2620623100CE,NIFTY,0,2026-02-06,23100,0.05,75,CE,NFO-OPT,NFO
11537922,45069,NIFTY2620623100PE,NIFTY,0,2026-02-06,23100,0.05,75,PE,NFO-OPT,NFO
11538178,45070,NIFTY2620622950CE,NIFTY,0,2026-02-06,22950,0.05,75,CE,NFO-OPT,NFO
11538434,45071,NIFTY2620622950PE,NIFTY,0,2026-02-06,22950,0.05,75,PE,NFO-OPT,NFO
11538690,45072,NIFTY2620622900CE,NIFTY,0,2026-02-06,22900,0.05,75,CE,NFO-OPT,NFO
11538946,45073,NIFTY2620622900PE,NIFTY,0,2026-02-06,22900,0.05,75,PE,NFO-OPT,NFO
11539202,45074,NIFTY2621323000CE,NIFTY,0,2026-02-13,23000,0.05,75,CE,NFO-OPT,NFO
11539458,45075,NIFTY2621323000PE,NIFTY,0,2026-02-13,23000,0.05,75,PE,NFO-OPT,NFO
11600000,45312,NIFTY26FEBFUT,NIFTY,0,2026-02-26,0,0.05,75,FUT,NFO-FUT,NFO
11600256,45313,NIFTY26MARFUT,NIFTY,0,2026-03-26,0,0.05,75,FUT,NFO-FUT,NFO
99999998,39062,BANKNIFTY2620650000CE,BANKNIFTY,0,2026-02-06,50000,0.05,30,CE,NFO-OPT,NFO
"""


@pytest.fixture
def mock_csv_response():
    """Mock requests.get to return sample CSV."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = SAMPLE_CSV
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


@pytest.fixture
def instrument_map(mock_csv_response):
    """Create an InstrumentMap with mocked CSV download."""
    from kite.instruments import InstrumentMap
    with patch('kite.instruments.requests.get', return_value=mock_csv_response):
        imap = InstrumentMap(api_key="test_key", access_token="test_token")
        imap.refresh()
    return imap


class TestRefresh:
    """Test CSV parsing and refresh."""

    def test_refresh_parses_csv(self, instrument_map):
        """Instruments CSV should be correctly parsed."""
        assert instrument_map._instruments is not None
        assert len(instrument_map._instruments) > 0

    def test_only_nifty_instruments(self, instrument_map):
        """Should filter for NIFTY only (no BANKNIFTY)."""
        for inst in instrument_map._instruments:
            assert inst['name'] == 'NIFTY'

    def test_refresh_returns_true(self, mock_csv_response):
        """Refresh should return True on success."""
        from kite.instruments import InstrumentMap
        with patch('kite.instruments.requests.get', return_value=mock_csv_response):
            imap = InstrumentMap(api_key="test_key", access_token="test_token")
            assert imap.refresh() is True

    def test_refresh_failure_returns_false(self):
        """Refresh should return False on HTTP error."""
        from kite.instruments import InstrumentMap
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 500")
        with patch('kite.instruments.requests.get', return_value=mock_resp):
            imap = InstrumentMap(api_key="test_key", access_token="test_token")
            assert imap.refresh() is False


class TestGetCurrentExpiry:
    """Test expiry date selection."""

    def test_get_current_expiry(self, instrument_map):
        """Should return the nearest expiry >= today."""
        expiry = instrument_map.get_current_expiry()
        assert expiry is not None
        # Should be a string in YYYY-MM-DD format
        assert len(expiry) == 10

    def test_returns_nearest_expiry(self):
        """Should return the nearest future weekly expiry."""
        from kite.instruments import InstrumentMap
        from datetime import timedelta
        # Build CSV with two future expiries
        exp1 = (date.today() + timedelta(days=3)).isoformat()
        exp2 = (date.today() + timedelta(days=10)).isoformat()
        csv_data = (
            "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n"
            f"100,1,NIFTY_CE,NIFTY,0,{exp1},23000,0.05,75,CE,NFO-OPT,NFO\n"
            f"200,2,NIFTY_CE2,NIFTY,0,{exp2},23000,0.05,75,CE,NFO-OPT,NFO\n"
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = csv_data
        mock_resp.raise_for_status = MagicMock()
        with patch('kite.instruments.requests.get', return_value=mock_resp):
            imap = InstrumentMap(api_key="test_key", access_token="test_token")
            imap.refresh()
            assert imap.get_current_expiry() == exp1


class TestGetOptionInstrument:
    """Test strike/type/expiry lookup."""

    def test_get_option_instrument(self, instrument_map):
        """Should find the correct instrument for strike/type/expiry."""
        expiry = "2026-02-06"
        inst = instrument_map.get_option_instrument(23000, "CE", expiry)
        assert inst is not None
        assert inst['strike'] == 23000
        assert inst['instrument_type'] == 'CE'
        assert inst['instrument_token'] == 11536386

    def test_get_put_instrument(self, instrument_map):
        """Should find PUT instrument."""
        inst = instrument_map.get_option_instrument(23000, "PE", "2026-02-06")
        assert inst is not None
        assert inst['instrument_type'] == 'PE'

    def test_missing_strike_returns_none(self, instrument_map):
        """Should return None for non-existent strike."""
        inst = instrument_map.get_option_instrument(99999, "CE", "2026-02-06")
        assert inst is None


class TestBuildQuoteSymbols:
    """Test quote symbol construction."""

    def test_build_quote_symbols(self, instrument_map):
        """Should build correct NFO:SYMBOL format."""
        strikes = [22900, 22950, 23000]
        expiry = "2026-02-06"
        symbols = instrument_map.build_quote_symbols(strikes, expiry)
        assert len(symbols) > 0
        # Each key should be NFO:SYMBOL format
        for key in symbols:
            assert key.startswith("NFO:")
        # Should have CE and PE for each strike
        assert len(symbols) == 6  # 3 strikes × 2

    def test_symbol_values_are_tuples(self, instrument_map):
        """Values should be (strike, type, instrument_token) tuples."""
        strikes = [23000]
        symbols = instrument_map.build_quote_symbols(strikes, "2026-02-06")
        for key, val in symbols.items():
            assert isinstance(val, tuple)
            assert len(val) == 3  # (strike, type, token)


class TestCacheSameDay:
    """Test caching behavior."""

    def test_cache_same_day(self, mock_csv_response):
        """Should not re-download on same day."""
        from kite.instruments import InstrumentMap
        with patch('kite.instruments.requests.get', return_value=mock_csv_response) as mock_get:
            imap = InstrumentMap(api_key="test_key", access_token="test_token")
            imap.refresh()
            imap.refresh()  # Second call same day
            assert mock_get.call_count == 1  # Only one download


class TestGetNiftyFuture:
    """Test futures instrument lookup."""

    def test_get_nifty_future(self, instrument_map):
        """Should return current month NIFTY futures instrument."""
        fut = instrument_map.get_nifty_future()
        assert fut is not None
        assert fut['instrument_type'] == 'FUT'
        assert fut['name'] == 'NIFTY'

    def test_future_has_token(self, instrument_map):
        """Future instrument should have an instrument_token."""
        fut = instrument_map.get_nifty_future()
        assert 'instrument_token' in fut
        assert fut['instrument_token'] > 0


class TestGetNiftyStrikes:
    """Test strike generation around spot."""

    def test_get_nifty_strikes(self, instrument_map):
        """Should return strikes available in instruments around spot."""
        strikes = instrument_map.get_nifty_strikes(23000, num_each_side=3)
        assert len(strikes) > 0
        # All should be valid NIFTY strikes (multiples of 50)
        for s in strikes:
            assert s % 50 == 0

    def test_strikes_centered_around_spot(self, instrument_map):
        """Strikes should be centered around spot price."""
        strikes = instrument_map.get_nifty_strikes(23000, num_each_side=2)
        assert 23000 in strikes


class TestSymbolHelpers:
    """Test symbol string helpers."""

    def test_get_spot_symbol(self, instrument_map):
        assert instrument_map.get_spot_symbol() == "NSE:NIFTY 50"

    def test_get_vix_symbol(self, instrument_map):
        assert instrument_map.get_vix_symbol() == "NSE:INDIA VIX"
