"""Tests for InstrumentService."""

from datetime import date
from unittest.mock import patch

import pytest

from app.services.instrument_service import InstrumentService

SAMPLE_CSV = """instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,lot_size,instrument_type,segment,exchange
1234,100,NIFTY2632623500CE,NIFTY,0,2026-03-26,23500.0,75,CE,NFO-OPT,NFO
1235,101,NIFTY2632623500PE,NIFTY,0,2026-03-26,23500.0,75,PE,NFO-OPT,NFO
1236,102,NIFTY2632623550CE,NIFTY,0,2026-03-26,23550.0,75,CE,NFO-OPT,NFO
1237,103,NIFTY2632623550PE,NIFTY,0,2026-03-26,23550.0,75,PE,NFO-OPT,NFO
1238,104,NIFTY2632623600CE,NIFTY,0,2026-03-26,23600.0,75,CE,NFO-OPT,NFO
1239,105,NIFTY2632623600PE,NIFTY,0,2026-03-26,23600.0,75,PE,NFO-OPT,NFO
1240,106,NIFTY26MARFUT,NIFTY,0,2026-03-26,0.0,75,FUT,NFO-FUT,NFO
9999,200,BANKNIFTY2632650000CE,BANKNIFTY,0,2026-03-26,50000.0,25,CE,NFO-OPT,NFO
"""


@pytest.fixture
def service():
    return InstrumentService(api_key="test_key")


class TestInstrumentService:
    @pytest.mark.asyncio
    async def test_load_instruments(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV):
            result = await service.load_instruments()
        assert result is True
        assert len(service._instruments) == 7  # All NIFTY rows (not BANKNIFTY)
        assert len(service._options) == 6
        assert len(service._futures) == 1
        assert len(service._expiries) == 1

    @pytest.mark.asyncio
    async def test_load_instruments_caches_per_day(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV) as mock:
            await service.load_instruments()
            await service.load_instruments()  # Should use cache
        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_load_instruments_error(self, service):
        with patch.object(service, "_fetch_csv", side_effect=Exception("network")):
            result = await service.load_instruments()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_current_expiry(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV):
            await service.load_instruments()
        expiry = service.get_current_expiry()
        assert expiry == "2026-03-26"

    @pytest.mark.asyncio
    async def test_get_option_instrument(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV):
            await service.load_instruments()
        inst = service.get_option_instrument(23500, "CE", "2026-03-26")
        assert inst is not None
        assert inst["tradingsymbol"] == "NIFTY2632623500CE"
        assert inst["instrument_token"] == 1234

    @pytest.mark.asyncio
    async def test_get_option_instrument_not_found(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV):
            await service.load_instruments()
        inst = service.get_option_instrument(99999, "CE", "2026-03-26")
        assert inst is None

    @pytest.mark.asyncio
    async def test_get_instrument_token(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV):
            await service.load_instruments()
        token = service.get_instrument_token(23500, "PE", "2026-03-26")
        assert token == 1235

    @pytest.mark.asyncio
    async def test_get_nifty_strikes(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV):
            await service.load_instruments()
        strikes = service.get_nifty_strikes(23550.0, num_each_side=2)
        assert 23500 in strikes
        assert 23550 in strikes
        assert 23600 in strikes

    @pytest.mark.asyncio
    async def test_build_quote_symbols(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV):
            await service.load_instruments()
        symbols = service.build_quote_symbols([23500], "2026-03-26")
        assert "NFO:NIFTY2632623500CE" in symbols
        assert "NFO:NIFTY2632623500PE" in symbols
        # Each entry: (strike, type, token)
        assert symbols["NFO:NIFTY2632623500CE"] == (23500, "CE", 1234)

    @pytest.mark.asyncio
    async def test_get_nifty_future(self, service):
        with patch.object(service, "_fetch_csv", return_value=SAMPLE_CSV):
            await service.load_instruments()
        fut = service.get_nifty_future()
        assert fut is not None
        assert fut["tradingsymbol"] == "NIFTY26MARFUT"

    def test_static_symbols(self):
        assert InstrumentService.spot_symbol() == "NSE:NIFTY 50"
        assert InstrumentService.vix_symbol() == "NSE:INDIA VIX"
