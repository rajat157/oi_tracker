"""
Kite Instruments Map

Downloads and caches NFO instruments CSV daily from Kite API.
Provides lookup for NIFTY option/future instrument tokens.
"""

import csv
import io
import requests
from datetime import date
from typing import Optional, Dict, List, Tuple
from logger import get_logger

log = get_logger("kite_instruments")

INSTRUMENTS_URL = "https://api.kite.trade/instruments/NFO"


class InstrumentMap:
    """Manages NIFTY instrument token lookups from Kite's instrument CSV."""

    _shared: Optional['InstrumentMap'] = None

    def __init__(self, api_key: str, access_token: str = ""):
        self._api_key = api_key
        self._access_token = access_token
        self._instruments: List[dict] = []
        self._cache_date: Optional[date] = None
        # Lookup indices built on refresh
        self._options: Dict[Tuple[int, str, str], dict] = {}  # (strike, type, expiry) -> instrument
        self._futures: List[dict] = []
        self._expiries: List[str] = []
        # Register as shared instance for cross-module lookups
        InstrumentMap._shared = self

    @classmethod
    def _get_shared_instance(cls) -> Optional['InstrumentMap']:
        """Get the shared instance (set by the first InstrumentMap created)."""
        if cls._shared and cls._shared._options:
            return cls._shared
        return None

    def set_access_token(self, access_token: str):
        """Update the access token (called when token is refreshed)."""
        self._access_token = access_token

    def refresh(self) -> bool:
        """
        Download instruments CSV and build lookup tables.
        Cached per day — skips re-download on same day.

        Returns:
            True if instruments loaded successfully, False on error.
        """
        today = date.today()
        if self._cache_date == today and self._instruments:
            return True

        try:
            headers = {}
            if self._api_key and self._access_token:
                headers['Authorization'] = f'token {self._api_key}:{self._access_token}'
                headers['X-Kite-Version'] = '3'
            resp = requests.get(INSTRUMENTS_URL, headers=headers)
            resp.raise_for_status()

            reader = csv.DictReader(io.StringIO(resp.text))
            all_instruments = list(reader)

            # Filter for NIFTY only
            self._instruments = [
                inst for inst in all_instruments
                if inst.get('name') == 'NIFTY'
            ]

            # Parse numeric fields
            for inst in self._instruments:
                inst['strike'] = float(inst.get('strike', 0))
                if inst['strike'] == int(inst['strike']):
                    inst['strike'] = int(inst['strike'])
                inst['instrument_token'] = int(inst.get('instrument_token', 0))
                inst['lot_size'] = int(inst.get('lot_size', 75))

            # Build option lookup: (strike, type, expiry) -> instrument
            self._options = {}
            self._futures = []
            expiry_set = set()

            for inst in self._instruments:
                segment = inst.get('segment', '')
                itype = inst.get('instrument_type', '')

                if segment == 'NFO-OPT':
                    key = (inst['strike'], itype, inst['expiry'])
                    self._options[key] = inst
                    expiry_set.add(inst['expiry'])
                elif segment == 'NFO-FUT':
                    self._futures.append(inst)

            # Sort expiries chronologically
            self._expiries = sorted(expiry_set)

            self._cache_date = today
            log.info("Instruments refreshed",
                     options=len(self._options),
                     futures=len(self._futures),
                     expiries=len(self._expiries))
            return True

        except Exception as e:
            log.error("Failed to refresh instruments", error=str(e))
            return False

    def get_current_expiry(self) -> Optional[str]:
        """
        Get the nearest weekly expiry >= today.

        Returns:
            Expiry date string (YYYY-MM-DD) or None.
        """
        today = date.today()
        for expiry_str in self._expiries:
            try:
                exp_date = date.fromisoformat(expiry_str)
                if exp_date >= today:
                    return expiry_str
            except ValueError:
                continue
        return self._expiries[-1] if self._expiries else None

    def get_option_instrument(self, strike: int, option_type: str,
                              expiry: str) -> Optional[dict]:
        """
        Look up an option instrument by strike, type, and expiry.

        Args:
            strike: Strike price (e.g. 23000)
            option_type: "CE" or "PE"
            expiry: Expiry date string (YYYY-MM-DD)

        Returns:
            Instrument dict or None if not found.
        """
        return self._options.get((strike, option_type, expiry))

    def get_nifty_strikes(self, spot: float, num_each_side: int = 15) -> List[int]:
        """
        Get available NIFTY strikes around the spot price.

        Args:
            spot: Current spot price
            num_each_side: Number of strikes on each side of ATM

        Returns:
            Sorted list of strike prices available in instruments.
        """
        current_expiry = self.get_current_expiry()
        if not current_expiry:
            return []

        # Get unique strikes for the current expiry
        available = set()
        for (strike, otype, exp) in self._options:
            if exp == current_expiry:
                available.add(strike)

        if not available:
            return []

        # Sort and filter around spot
        all_strikes = sorted(available)
        atm_idx = min(range(len(all_strikes)),
                      key=lambda i: abs(all_strikes[i] - spot))

        start = max(0, atm_idx - num_each_side)
        end = min(len(all_strikes), atm_idx + num_each_side + 1)

        return all_strikes[start:end]

    def build_quote_symbols(self, strikes: List[int],
                            expiry: str) -> Dict[str, Tuple[int, str, int]]:
        """
        Build Kite quote symbol strings for a set of strikes.

        Args:
            strikes: List of strike prices
            expiry: Expiry date string

        Returns:
            Dict mapping "NFO:TRADINGSYMBOL" -> (strike, option_type, instrument_token)
        """
        symbols = {}
        for strike in strikes:
            for otype in ("CE", "PE"):
                inst = self.get_option_instrument(strike, otype, expiry)
                if inst:
                    key = f"NFO:{inst['tradingsymbol']}"
                    symbols[key] = (strike, otype, inst['instrument_token'])
        return symbols

    def get_nifty_future(self) -> Optional[dict]:
        """
        Get the current (nearest expiry) NIFTY futures instrument.

        Returns:
            Futures instrument dict or None.
        """
        if not self._futures:
            return None

        today = date.today()
        # Find nearest future expiry >= today
        valid = []
        for fut in self._futures:
            try:
                exp_date = date.fromisoformat(fut['expiry'])
                if exp_date >= today:
                    valid.append((exp_date, fut))
            except ValueError:
                continue

        if valid:
            valid.sort(key=lambda x: x[0])
            return valid[0][1]

        return self._futures[0]  # Fallback to first

    def get_spot_symbol(self) -> str:
        """Get the Kite symbol for NIFTY 50 spot."""
        return "NSE:NIFTY 50"

    def get_vix_symbol(self) -> str:
        """Get the Kite symbol for India VIX."""
        return "NSE:INDIA VIX"
