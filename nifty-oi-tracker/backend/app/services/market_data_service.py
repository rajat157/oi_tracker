"""Market data fetcher — option chain, VIX, futures via Kite API."""

from __future__ import annotations

import asyncio
from datetime import date

from app.engine.iv_calculator import implied_volatility, time_to_expiry_years
from app.services.instrument_service import InstrumentService
from app.services.kite_auth_service import KiteAuthService
from app.services.logging_service import get_logger

log = get_logger("market_data")

BATCH_SIZE = 50  # Kite quote API limit


class MarketDataService:
    def __init__(
        self,
        kite_auth: KiteAuthService,
        instruments: InstrumentService,
    ) -> None:
        self._kite_auth = kite_auth
        self._instruments = instruments
        self._kite = None  # Lazy init
        self._day_open_oi: dict[tuple[int, str], int] = {}
        self._oi_baseline_date: date | None = None

    async def _get_kite(self):
        """Lazy-init KiteConnect with current token."""
        from kiteconnect import KiteConnect

        token = await self._kite_auth.get_access_token()
        if not token:
            raise RuntimeError("Kite not authenticated — no access token")
        if self._kite is None:
            from app.core.config import settings
            self._kite = KiteConnect(api_key=settings.kite_api_key)
        self._kite.set_access_token(token)
        self._instruments.set_access_token(token)
        return self._kite

    async def fetch_option_chain(self) -> dict | None:
        """Fetch full NIFTY option chain with IV computation."""
        try:
            kite = await self._get_kite()
            loop = asyncio.get_running_loop()

            # Refresh instruments (daily cache)
            if not await self._instruments.load_instruments():
                log.error("Failed to refresh instruments")
                return None

            # Spot price
            spot_sym = InstrumentService.spot_symbol()
            spot_resp = await loop.run_in_executor(None, kite.ltp, spot_sym)
            spot_price = spot_resp[spot_sym]["last_price"]

            # Expiry + strikes
            current_expiry = self._instruments.get_current_expiry()
            if not current_expiry:
                log.error("No current expiry found")
                return None

            strikes_list = self._instruments.get_nifty_strikes(spot_price, num_each_side=15)
            if not strikes_list:
                log.error("No strikes found around spot")
                return None

            quote_symbols = self._instruments.build_quote_symbols(strikes_list, current_expiry)
            if not quote_symbols:
                log.error("No quote symbols built")
                return None

            # Fetch quotes in batches
            all_quotes = await self._fetch_quotes_batched(list(quote_symbols.keys()))

            # OI baseline reset on new day
            today = date.today()
            if self._oi_baseline_date != today:
                self._day_open_oi = {}
                self._oi_baseline_date = today

            # Build strikes data
            t = time_to_expiry_years(current_expiry)
            strikes_data: dict[int, dict] = {}

            for sym_key, (strike, otype, _token) in quote_symbols.items():
                quote = all_quotes.get(sym_key)
                if not quote:
                    continue

                ltp = quote.get("last_price", 0.0)
                oi = int(quote.get("oi", 0))
                volume = int(quote.get("volume", 0))

                oi_key = (strike, otype)
                if oi_key not in self._day_open_oi:
                    self._day_open_oi[oi_key] = oi
                oi_change = oi - self._day_open_oi[oi_key]

                iv = 0.0
                if ltp > 0 and spot_price > 0:
                    iv = implied_volatility(
                        option_price=ltp, spot=spot_price, strike=strike,
                        t=t, option_type=otype,
                    )
                    iv = round(iv * 100, 2)

                if strike not in strikes_data:
                    strikes_data[strike] = {
                        "ce_oi": 0, "ce_oi_change": 0, "ce_volume": 0,
                        "ce_iv": 0.0, "ce_ltp": 0.0,
                        "pe_oi": 0, "pe_oi_change": 0, "pe_volume": 0,
                        "pe_iv": 0.0, "pe_ltp": 0.0,
                    }

                prefix = "ce" if otype == "CE" else "pe"
                strikes_data[strike][f"{prefix}_oi"] = oi
                strikes_data[strike][f"{prefix}_oi_change"] = oi_change
                strikes_data[strike][f"{prefix}_volume"] = volume
                strikes_data[strike][f"{prefix}_iv"] = iv
                strikes_data[strike][f"{prefix}_ltp"] = ltp

            log.info(
                "Option chain fetched",
                spot=f"{spot_price:.2f}",
                strikes=len(strikes_data),
                expiry=current_expiry,
            )

            return {
                "spot_price": spot_price,
                "expiry_dates": [current_expiry],
                "current_expiry": current_expiry,
                "strikes": strikes_data,
            }

        except Exception as e:
            log.error("Error fetching option chain", error=str(e))
            return None

    async def fetch_india_vix(self) -> float | None:
        try:
            kite = await self._get_kite()
            loop = asyncio.get_running_loop()
            sym = InstrumentService.vix_symbol()
            resp = await loop.run_in_executor(None, kite.ltp, sym)
            return resp[sym]["last_price"]
        except Exception as e:
            log.error("Error fetching VIX", error=str(e))
            return None

    async def fetch_futures_data(self) -> dict | None:
        try:
            kite = await self._get_kite()
            loop = asyncio.get_running_loop()

            fut_inst = self._instruments.get_nifty_future()
            if not fut_inst:
                log.error("No NIFTY futures instrument found")
                return None

            fut_sym = f"NFO:{fut_inst['tradingsymbol']}"
            resp = await loop.run_in_executor(None, kite.quote, fut_sym)
            fut_quote = resp[fut_sym]

            future_price = fut_quote["last_price"]
            future_oi = int(fut_quote.get("oi", 0))

            spot_sym = InstrumentService.spot_symbol()
            spot_resp = await loop.run_in_executor(None, kite.ltp, spot_sym)
            spot_price = spot_resp[spot_sym]["last_price"]

            basis = future_price - spot_price
            basis_pct = (basis / spot_price * 100) if spot_price > 0 else 0

            return {
                "future_price": future_price,
                "future_oi": future_oi,
                "basis": basis,
                "basis_pct": basis_pct,
                "expiry": fut_inst.get("expiry", ""),
            }
        except Exception as e:
            log.error("Error fetching futures data", error=str(e))
            return None

    async def get_spot_price(self) -> float | None:
        try:
            kite = await self._get_kite()
            loop = asyncio.get_running_loop()
            sym = InstrumentService.spot_symbol()
            resp = await loop.run_in_executor(None, kite.ltp, sym)
            return resp[sym]["last_price"]
        except Exception as e:
            log.error("Error fetching spot price", error=str(e))
            return None

    async def _fetch_quotes_batched(self, symbols: list[str]) -> dict:
        kite = await self._get_kite()
        loop = asyncio.get_running_loop()
        all_quotes = {}
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i : i + BATCH_SIZE]
            try:
                resp = await loop.run_in_executor(None, lambda b=batch: kite.quote(*b))
                all_quotes.update(resp)
            except Exception as e:
                log.error("Error fetching quote batch", batch_start=i, error=str(e))
        return all_quotes
