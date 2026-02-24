"""
Kite Data Fetcher — Drop-in replacement for NSEFetcher.

Uses Kite Connect REST API instead of Selenium/NSE scraping.
Produces identical output format to NSEFetcher.parse_option_data().

OI Change strategy:
  First fetch each day records baseline OI per (strike, type).
  Subsequent fetches: change = current OI - baseline OI.
  This matches NSE's changeinOpenInterest field.
"""

import os
from datetime import date
from typing import Optional, Dict, Tuple, List

from kiteconnect import KiteConnect

from kite_instruments import InstrumentMap
from iv_calculator import implied_volatility, time_to_expiry_years
from kite_auth import load_token
from logger import get_logger

log = get_logger("kite_data")

BATCH_SIZE = 50  # Kite quote API batch limit


class KiteDataFetcher:
    """Fetches option chain data via Kite Connect API."""

    def __init__(self):
        api_key = os.environ.get('KITE_API_KEY', '')
        access_token = load_token()

        self._kite = KiteConnect(api_key=api_key)
        if access_token:
            self._kite.set_access_token(access_token)

        self._instrument_map = InstrumentMap(
            api_key=api_key, access_token=access_token
        )

        # OI baseline tracking (per day)
        self._day_open_oi: Dict[Tuple[int, str], int] = {}  # (strike, type) -> baseline OI
        self._oi_baseline_date: Optional[date] = None

    def close(self):
        """No-op. No browser to close."""
        pass

    def fetch_option_chain(self) -> Optional[dict]:
        """
        Fetch NIFTY option chain data via Kite API.

        Returns:
            Dict with identical format to NSEFetcher.parse_option_data():
            {
                "spot_price": float,
                "expiry_dates": [str],
                "current_expiry": str,
                "strikes": {
                    int: {
                        "ce_oi": int, "ce_oi_change": int, "ce_volume": int,
                        "ce_iv": float, "ce_ltp": float,
                        "pe_oi": int, "pe_oi_change": int, "pe_volume": int,
                        "pe_iv": float, "pe_ltp": float,
                    }
                }
            }
            Or None on failure.
        """
        try:
            # 1. Refresh instruments (cached per day)
            if not self._instrument_map.refresh():
                log.error("Failed to refresh instruments")
                return None

            # 2. Get spot price
            spot_sym = self._instrument_map.get_spot_symbol()
            spot_resp = self._kite.ltp(spot_sym)
            spot_price = spot_resp[spot_sym]["last_price"]

            # 3. Determine strikes and expiry
            current_expiry = self._instrument_map.get_current_expiry()
            if not current_expiry:
                log.error("No current expiry found")
                return None

            strikes_list = self._instrument_map.get_nifty_strikes(spot_price, num_each_side=15)
            if not strikes_list:
                log.error("No strikes found around spot")
                return None

            # 4. Build quote symbols
            quote_symbols = self._instrument_map.build_quote_symbols(strikes_list, current_expiry)
            if not quote_symbols:
                log.error("No quote symbols built")
                return None

            # 5. Fetch quotes in batches
            all_quotes = self._fetch_quotes_batched(list(quote_symbols.keys()))

            # 6. Reset OI baseline on new day
            today = date.today()
            if self._oi_baseline_date != today:
                self._day_open_oi = {}
                self._oi_baseline_date = today

            # 7. Build strikes_data dict
            t = time_to_expiry_years(current_expiry)
            strikes_data: Dict[int, dict] = {}

            for sym_key, (strike, otype, token) in quote_symbols.items():
                quote = all_quotes.get(sym_key)
                if not quote:
                    continue

                ltp = quote.get('last_price', 0.0)
                oi = int(quote.get('oi', 0))
                volume = int(quote.get('volume', 0))

                # OI change: current - baseline
                oi_key = (strike, otype)
                if oi_key not in self._day_open_oi:
                    self._day_open_oi[oi_key] = oi  # Set baseline
                oi_change = oi - self._day_open_oi[oi_key]

                # Compute IV via Black-Scholes
                iv = 0.0
                if ltp > 0 and spot_price > 0:
                    iv = implied_volatility(
                        option_price=ltp, spot=spot_price,
                        strike=strike, t=t, option_type=otype
                    )
                    # Convert to percentage for consistency with NSE format
                    iv = round(iv * 100, 2)

                # Initialize strike entry if needed
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

            log.info("Option chain fetched via Kite",
                     spot=f"{spot_price:.2f}",
                     strikes=len(strikes_data),
                     expiry=current_expiry)

            return {
                "spot_price": spot_price,
                "expiry_dates": [current_expiry],
                "current_expiry": current_expiry,
                "strikes": strikes_data,
            }

        except Exception as e:
            log.error("Error fetching option chain via Kite", error=str(e))
            return None

    def fetch_india_vix(self) -> Optional[float]:
        """Fetch India VIX via Kite LTP API."""
        try:
            vix_sym = self._instrument_map.get_vix_symbol()
            resp = self._kite.ltp(vix_sym)
            return resp[vix_sym]["last_price"]
        except Exception as e:
            log.error("Error fetching VIX", error=str(e))
            return None

    def fetch_futures_data(self) -> Optional[dict]:
        """
        Fetch NIFTY futures data via Kite API.

        Returns:
            Dict with: future_price, future_oi, basis, basis_pct, expiry
            Or None on failure.
        """
        try:
            fut_inst = self._instrument_map.get_nifty_future()
            if not fut_inst:
                log.error("No NIFTY futures instrument found")
                return None

            fut_sym = f"NFO:{fut_inst['tradingsymbol']}"
            resp = self._kite.quote(fut_sym)
            fut_quote = resp[fut_sym]

            future_price = fut_quote['last_price']
            future_oi = int(fut_quote.get('oi', 0))

            # Get spot for basis calculation
            spot_sym = self._instrument_map.get_spot_symbol()
            spot_resp = self._kite.ltp(spot_sym)
            spot_price = spot_resp[spot_sym]["last_price"]

            basis = future_price - spot_price
            basis_pct = (basis / spot_price * 100) if spot_price > 0 else 0

            return {
                "future_price": future_price,
                "future_oi": future_oi,
                "basis": basis,
                "basis_pct": basis_pct,
                "expiry": fut_inst.get('expiry', ''),
            }

        except Exception as e:
            log.error("Error fetching futures data", error=str(e))
            return None

    def _fetch_quotes_batched(self, symbols: List[str]) -> dict:
        """
        Fetch quotes in batches of BATCH_SIZE.

        Args:
            symbols: List of "NFO:SYMBOL" strings

        Returns:
            Merged dict of all quote responses.
        """
        all_quotes = {}
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i:i + BATCH_SIZE]
            try:
                resp = self._kite.quote(*batch)
                all_quotes.update(resp)
            except Exception as e:
                log.error("Error fetching quote batch",
                          batch_start=i, batch_size=len(batch), error=str(e))
        return all_quotes
