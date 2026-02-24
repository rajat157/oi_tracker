"""Tests for Kite Data Fetcher — drop-in replacement for NSE fetcher."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import date, timedelta


# --- Helper: Build a mock InstrumentMap ---

def _make_instrument_map(strikes=None, expiry=None):
    """Create a mock InstrumentMap."""
    if strikes is None:
        strikes = [22800, 22850, 22900, 22950, 23000, 23050, 23100, 23150, 23200]
    if expiry is None:
        expiry = (date.today() + timedelta(days=3)).isoformat()

    imap = MagicMock()
    imap.refresh.return_value = True
    imap.get_current_expiry.return_value = expiry
    imap.get_nifty_strikes.return_value = strikes
    imap.get_spot_symbol.return_value = "NSE:NIFTY 50"
    imap.get_vix_symbol.return_value = "NSE:INDIA VIX"

    # Build quote symbols: NFO:SYMBOL -> (strike, type, token)
    symbols = {}
    token = 1000
    for s in strikes:
        for otype in ("CE", "PE"):
            sym = f"NIFTY{expiry.replace('-', '')}{s}{otype}"
            symbols[f"NFO:{sym}"] = (s, otype, token)
            token += 1
    imap.build_quote_symbols.return_value = symbols

    # Futures
    imap.get_nifty_future.return_value = {
        'tradingsymbol': 'NIFTY26FEBFUT',
        'instrument_token': 99999,
    }

    return imap


def _make_kite_quote(ltp, oi, volume, day_open_oi=None):
    """Create a mock Kite quote response dict for one instrument."""
    if day_open_oi is None:
        day_open_oi = oi
    return {
        'last_price': ltp,
        'oi': oi,
        'volume': volume,
        'ohlc': {'open': ltp, 'high': ltp + 5, 'low': ltp - 5, 'close': ltp},
        'oi_day_high': oi + 100,
        'oi_day_low': max(oi - 100, 0),
    }


def _build_quote_response(symbols_map, base_ltp=150, base_oi=50000, base_volume=1000):
    """Build a full Kite quote API response for all symbols."""
    response = {}
    for sym_key, (strike, otype, token) in symbols_map.items():
        # Vary prices: CE premium decreases as strike goes up, PE increases
        if otype == "CE":
            ltp = max(5, base_ltp - (strike - 23000) * 0.5)
        else:
            ltp = max(5, base_ltp + (strike - 23000) * 0.5)
        response[sym_key] = _make_kite_quote(ltp, base_oi, base_volume)
    return response


@pytest.fixture
def mock_kite():
    """Create a mock KiteConnect instance."""
    kite = MagicMock()
    return kite


@pytest.fixture
def fetcher(mock_kite):
    """Create a KiteDataFetcher with mocked dependencies."""
    from kite_data import KiteDataFetcher
    imap = _make_instrument_map()
    with patch('kite_data.InstrumentMap', return_value=imap), \
         patch('kite_data.load_token', return_value='test_token'):
        f = KiteDataFetcher.__new__(KiteDataFetcher)
        f._api_key = 'test_key'
        f._kite = mock_kite
        f._instrument_map = imap
        f._day_open_oi = {}
        f._oi_baseline_date = None
    return f


class TestOutputFormat:
    """CRITICAL: Verify output matches NSE fetcher format exactly."""

    def test_output_format_matches_nse(self, fetcher, mock_kite):
        """Output dict must have identical keys/types as NSEFetcher.parse_option_data()."""
        imap = fetcher._instrument_map
        symbols = imap.build_quote_symbols.return_value

        # Mock spot price
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}

        # Mock option quotes
        mock_kite.quote.return_value = _build_quote_response(symbols)

        result = fetcher.fetch_option_chain()

        assert result is not None
        # Top-level keys
        assert "spot_price" in result
        assert "expiry_dates" in result
        assert "current_expiry" in result
        assert "strikes" in result

        assert isinstance(result["spot_price"], float)
        assert isinstance(result["expiry_dates"], list)
        assert isinstance(result["strikes"], dict)

        # Check one strike's data has all required keys
        strike_data = list(result["strikes"].values())[0]
        required_keys = [
            "ce_oi", "ce_oi_change", "ce_volume", "ce_iv", "ce_ltp",
            "pe_oi", "pe_oi_change", "pe_volume", "pe_iv", "pe_ltp",
        ]
        for key in required_keys:
            assert key in strike_data, f"Missing key: {key}"

    def test_spot_price_is_float(self, fetcher, mock_kite):
        """Spot price should be a float."""
        imap = fetcher._instrument_map
        symbols = imap.build_quote_symbols.return_value
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}
        mock_kite.quote.return_value = _build_quote_response(symbols)

        result = fetcher.fetch_option_chain()
        assert isinstance(result["spot_price"], float)

    def test_strikes_keyed_by_int(self, fetcher, mock_kite):
        """Strikes dict should be keyed by integer strike price."""
        imap = fetcher._instrument_map
        symbols = imap.build_quote_symbols.return_value
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}
        mock_kite.quote.return_value = _build_quote_response(symbols)

        result = fetcher.fetch_option_chain()
        for key in result["strikes"]:
            assert isinstance(key, int)


class TestOIChange:
    """Test OI change computation."""

    def test_oi_change_first_fetch_is_zero(self, fetcher, mock_kite):
        """First fetch of the day: OI change should be zero (baseline set)."""
        imap = fetcher._instrument_map
        symbols = imap.build_quote_symbols.return_value
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}
        mock_kite.quote.return_value = _build_quote_response(symbols, base_oi=50000)

        result = fetcher.fetch_option_chain()
        # First fetch: no baseline yet when fetcher was created, so it sets baseline
        # On first fetch, OI change = current - day_open (from Kite's oi field vs stored baseline)
        # Since baseline is just being set, change should be 0
        for strike_data in result["strikes"].values():
            assert strike_data["ce_oi_change"] == 0
            assert strike_data["pe_oi_change"] == 0

    def test_oi_change_subsequent_fetch(self, fetcher, mock_kite):
        """Subsequent fetch: OI change = current - baseline."""
        imap = fetcher._instrument_map
        symbols = imap.build_quote_symbols.return_value
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}

        # First fetch: sets baseline at 50000
        mock_kite.quote.return_value = _build_quote_response(symbols, base_oi=50000)
        fetcher.fetch_option_chain()

        # Second fetch: OI is now 55000
        mock_kite.quote.return_value = _build_quote_response(symbols, base_oi=55000)
        result = fetcher.fetch_option_chain()

        for strike_data in result["strikes"].values():
            assert strike_data["ce_oi_change"] == 5000
            assert strike_data["pe_oi_change"] == 5000


class TestIVComputation:
    """Test IV calculation from LTP."""

    def test_iv_computation_populated(self, fetcher, mock_kite):
        """Non-zero IV when LTP > 0."""
        imap = fetcher._instrument_map
        symbols = imap.build_quote_symbols.return_value
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}
        mock_kite.quote.return_value = _build_quote_response(symbols, base_ltp=150)

        result = fetcher.fetch_option_chain()
        # ATM strikes should have non-zero IV
        atm_data = result["strikes"].get(23000)
        if atm_data:
            assert atm_data["ce_iv"] > 0 or atm_data["pe_iv"] > 0

    def test_zero_ltp_gives_zero_iv(self, fetcher, mock_kite):
        """Zero premium should give zero IV."""
        imap = fetcher._instrument_map
        symbols = imap.build_quote_symbols.return_value
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}

        # Set all premiums to 0
        quotes = _build_quote_response(symbols, base_ltp=0)
        for q in quotes.values():
            q['last_price'] = 0.0
        mock_kite.quote.return_value = quotes

        result = fetcher.fetch_option_chain()
        for strike_data in result["strikes"].values():
            assert strike_data["ce_iv"] == 0.0
            assert strike_data["pe_iv"] == 0.0


class TestBatchQuotes:
    """Test batch splitting for Kite API."""

    def test_batch_quotes_split(self, fetcher, mock_kite):
        """60+ instruments should be split into multiple API calls."""
        # Create a large instrument map
        big_strikes = list(range(22000, 24000, 50))  # 40 strikes
        imap = _make_instrument_map(strikes=big_strikes)
        fetcher._instrument_map = imap

        symbols = imap.build_quote_symbols.return_value
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}

        # quote() should be called multiple times for batches
        mock_kite.quote.return_value = _build_quote_response(symbols)
        result = fetcher.fetch_option_chain()

        # Should have called quote() more than once (80 instruments / 50 batch = 2)
        assert mock_kite.quote.call_count >= 2


class TestAuthFailure:
    """Test graceful error handling."""

    def test_auth_failure_returns_none(self, fetcher, mock_kite):
        """Auth/API failure should return None."""
        mock_kite.ltp.side_effect = Exception("TokenException: Token expired")
        result = fetcher.fetch_option_chain()
        assert result is None


class TestVIX:
    """Test VIX fetching."""

    def test_fetch_india_vix(self, fetcher, mock_kite):
        """Should return VIX value."""
        mock_kite.ltp.return_value = {"NSE:INDIA VIX": {"last_price": 14.5}}
        vix = fetcher.fetch_india_vix()
        assert vix == 14.5

    def test_vix_failure_returns_none(self, fetcher, mock_kite):
        """VIX fetch failure should return None."""
        mock_kite.ltp.side_effect = Exception("Error")
        vix = fetcher.fetch_india_vix()
        assert vix is None


class TestFuturesData:
    """Test futures data fetching."""

    def test_fetch_futures_data(self, fetcher, mock_kite):
        """Should return futures price, OI, and basis."""
        mock_kite.quote.return_value = {
            "NFO:NIFTY26FEBFUT": {
                "last_price": 23050.0,
                "oi": 10000000,
                "volume": 500000,
                "ohlc": {"open": 23000, "high": 23100, "low": 22950, "close": 23050},
            }
        }
        # Need spot for basis calculation
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}
        result = fetcher.fetch_futures_data()
        assert result is not None
        assert result["future_price"] == 23050.0
        assert result["future_oi"] == 10000000
        assert result["basis"] == pytest.approx(50.0)

    def test_futures_failure_returns_none(self, fetcher, mock_kite):
        """Futures fetch failure should return None."""
        mock_kite.quote.side_effect = Exception("Error")
        result = fetcher.fetch_futures_data()
        assert result is None


class TestIntegrationWithAnalyzer:
    """Test that output works with existing analysis pipeline."""

    def test_works_with_analyze_tug_of_war(self, fetcher, mock_kite):
        """Output should pass through OI analyzer without error."""
        from oi_analyzer import analyze_tug_of_war

        imap = fetcher._instrument_map
        symbols = imap.build_quote_symbols.return_value
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23000.0}}
        mock_kite.quote.return_value = _build_quote_response(symbols, base_oi=50000)

        result = fetcher.fetch_option_chain()
        assert result is not None

        # This should NOT raise
        analysis = analyze_tug_of_war(
            result["strikes"], result["spot_price"]
        )
        assert "verdict" in analysis
        assert "atm_strike" in analysis
