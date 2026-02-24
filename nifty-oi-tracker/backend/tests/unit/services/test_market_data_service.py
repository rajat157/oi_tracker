"""Tests for MarketDataService."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.instrument_service import InstrumentService
from app.services.market_data_service import MarketDataService


@pytest.fixture
def mock_kite_auth():
    auth = AsyncMock()
    auth.get_access_token = AsyncMock(return_value="test_token")
    return auth


@pytest.fixture
def mock_instruments():
    inst = MagicMock(spec=InstrumentService)
    inst.load_instruments = AsyncMock(return_value=True)
    inst.get_current_expiry.return_value = "2026-03-26"
    inst.get_nifty_strikes.return_value = [23450, 23500, 23550, 23600, 23650]
    inst.build_quote_symbols.return_value = {
        "NFO:NIFTY23500CE": (23500, "CE", 1001),
        "NFO:NIFTY23500PE": (23500, "PE", 1002),
        "NFO:NIFTY23550CE": (23550, "CE", 1003),
        "NFO:NIFTY23550PE": (23550, "PE", 1004),
    }
    inst.get_nifty_future.return_value = {
        "tradingsymbol": "NIFTY26MARFUT",
        "expiry": "2026-03-26",
    }
    return inst


@pytest.fixture
def service(mock_kite_auth, mock_instruments):
    return MarketDataService(kite_auth=mock_kite_auth, instruments=mock_instruments)


class TestMarketDataService:
    @pytest.mark.asyncio
    async def test_fetch_option_chain(self, service, mock_instruments):
        mock_kite = MagicMock()
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23530.0}}
        mock_kite.quote.return_value = {
            "NFO:NIFTY23500CE": {"last_price": 120.0, "oi": 500000, "volume": 10000},
            "NFO:NIFTY23500PE": {"last_price": 80.0, "oi": 400000, "volume": 8000},
            "NFO:NIFTY23550CE": {"last_price": 90.0, "oi": 300000, "volume": 7000},
            "NFO:NIFTY23550PE": {"last_price": 110.0, "oi": 350000, "volume": 6000},
        }

        with patch("app.services.market_data_service.MarketDataService._get_kite",
                    return_value=mock_kite):
            result = await service.fetch_option_chain()

        assert result is not None
        assert result["spot_price"] == 23530.0
        assert result["current_expiry"] == "2026-03-26"
        assert 23500 in result["strikes"]
        assert result["strikes"][23500]["ce_oi"] == 500000
        assert result["strikes"][23500]["pe_oi"] == 400000

    @pytest.mark.asyncio
    async def test_fetch_option_chain_no_auth(self, service):
        service._kite_auth.get_access_token = AsyncMock(return_value=None)
        service._kite = None

        result = await service.fetch_option_chain()
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_india_vix(self, service):
        mock_kite = MagicMock()
        mock_kite.ltp.return_value = {"NSE:INDIA VIX": {"last_price": 14.5}}

        with patch("app.services.market_data_service.MarketDataService._get_kite",
                    return_value=mock_kite):
            result = await service.fetch_india_vix()

        assert result == 14.5

    @pytest.mark.asyncio
    async def test_fetch_futures_data(self, service):
        mock_kite = MagicMock()
        mock_kite.quote.return_value = {
            "NFO:NIFTY26MARFUT": {"last_price": 23580.0, "oi": 1200000}
        }
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23530.0}}

        with patch("app.services.market_data_service.MarketDataService._get_kite",
                    return_value=mock_kite):
            result = await service.fetch_futures_data()

        assert result is not None
        assert result["future_price"] == 23580.0
        assert result["future_oi"] == 1200000
        assert result["basis"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_get_spot_price(self, service):
        mock_kite = MagicMock()
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23500.0}}

        with patch("app.services.market_data_service.MarketDataService._get_kite",
                    return_value=mock_kite):
            result = await service.get_spot_price()

        assert result == 23500.0

    @pytest.mark.asyncio
    async def test_oi_baseline_reset(self, service):
        """OI baseline resets when day changes."""
        service._oi_baseline_date = date(2026, 2, 23)
        service._day_open_oi = {(23500, "CE"): 100000}

        mock_kite = MagicMock()
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23530.0}}
        mock_kite.quote.return_value = {
            "NFO:NIFTY23500CE": {"last_price": 120.0, "oi": 500000, "volume": 10000},
            "NFO:NIFTY23500PE": {"last_price": 80.0, "oi": 400000, "volume": 8000},
            "NFO:NIFTY23550CE": {"last_price": 90.0, "oi": 300000, "volume": 7000},
            "NFO:NIFTY23550PE": {"last_price": 110.0, "oi": 350000, "volume": 6000},
        }

        with patch("app.services.market_data_service.MarketDataService._get_kite",
                    return_value=mock_kite):
            result = await service.fetch_option_chain()

        # OI change should be 0 for new day (baseline = current)
        assert result is not None
        assert result["strikes"][23500]["ce_oi_change"] == 0

    @pytest.mark.asyncio
    async def test_fetch_option_chain_instruments_fail(self, service, mock_instruments):
        mock_instruments.load_instruments = AsyncMock(return_value=False)
        mock_kite = MagicMock()
        mock_kite.ltp.return_value = {"NSE:NIFTY 50": {"last_price": 23530.0}}

        with patch("app.services.market_data_service.MarketDataService._get_kite",
                    return_value=mock_kite):
            result = await service.fetch_option_chain()

        assert result is None
