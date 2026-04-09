"""
Kite Instruments Map

Downloads and caches Kite instrument CSVs (NFO and BFO) and provides
lookup for option/future instrument tokens.

The default `InstrumentMap()` is NIFTY/NFO (the legacy behaviour every
existing caller depends on). Use `MultiInstrumentMap` when you need to
look up BANKNIFTY (NFO) or SENSEX (BFO) options as well.
"""

import csv
import io
import requests
from datetime import date
from typing import Optional, Dict, List, Tuple
from core.logger import get_logger

log = get_logger("kite_instruments")

# Backward-compat constant — still NFO. Per-instance segment URL is
# computed from the `segment` constructor arg below.
INSTRUMENTS_URL = "https://api.kite.trade/instruments/NFO"


def _segment_url(segment: str) -> str:
    """Build the Kite instruments CSV URL for a given segment (NFO/BFO)."""
    return f"https://api.kite.trade/instruments/{segment}"


class InstrumentMap:
    """Manages instrument token lookups from Kite's instrument CSV.

    Defaults to NIFTY options on the NFO segment for backwards
    compatibility with the original code (which only knew NIFTY).
    Pass `symbol` and `segment` to use this for BANKNIFTY (NFO) or
    SENSEX (BFO).
    """

    _shared: Optional['InstrumentMap'] = None

    def __init__(self, api_key: str, access_token: str = "",
                 symbol: str = "NIFTY", segment: str = "NFO"):
        self._api_key = api_key
        self._access_token = access_token
        self._symbol = symbol
        self._segment = segment
        self._instruments_url = _segment_url(segment)
        # NIFTY instruments use 'NFO-OPT' / 'NFO-FUT'; SENSEX on BFO uses
        # 'BFO-OPT' / 'BFO-FUT'. Build the segment prefix once.
        self._opt_segment = f"{segment}-OPT"
        self._fut_segment = f"{segment}-FUT"

        self._instruments: List[dict] = []
        self._cache_date: Optional[date] = None
        # Lookup indices built on refresh
        self._options: Dict[Tuple[int, str, str], dict] = {}  # (strike, type, expiry) -> instrument
        self._futures: List[dict] = []
        self._expiries: List[str] = []
        # Register as shared instance for cross-module lookups (NIFTY only —
        # the legacy code depends on the *first* InstrumentMap being NIFTY).
        if symbol == "NIFTY" and segment == "NFO":
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
            resp = requests.get(self._instruments_url, headers=headers)
            resp.raise_for_status()

            reader = csv.DictReader(io.StringIO(resp.text))
            all_instruments = list(reader)

            # Filter for the configured underlying symbol
            self._instruments = [
                inst for inst in all_instruments
                if inst.get('name') == self._symbol
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

                if segment == self._opt_segment:
                    key = (inst['strike'], itype, inst['expiry'])
                    self._options[key] = inst
                    expiry_set.add(inst['expiry'])
                elif segment == self._fut_segment:
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
            Dict mapping "{SEGMENT}:TRADINGSYMBOL" -> (strike, option_type, instrument_token)
            (segment prefix is NFO for NIFTY/BANKNIFTY, BFO for SENSEX)
        """
        symbols = {}
        for strike in strikes:
            for otype in ("CE", "PE"):
                inst = self.get_option_instrument(strike, otype, expiry)
                if inst:
                    key = f"{self._segment}:{inst['tradingsymbol']}"
                    symbols[key] = (strike, otype, inst['instrument_token'])
        return symbols

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def segment(self) -> str:
        return self._segment

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


class MultiInstrumentMap:
    """Holds per-index InstrumentMaps for IntradayHunter (NF + BN + SX).

    Each index has its own underlying symbol and exchange segment, so they
    cannot share a single InstrumentMap. This wrapper hides that complexity:
    callers pass an `index_label` ("NIFTY" / "BANKNIFTY" / "SENSEX") and
    the right child map is consulted.

    NIFTY        — NFO segment, name=NIFTY
    BANKNIFTY    — NFO segment, name=BANKNIFTY
    SENSEX       — BFO segment, name=SENSEX
    """

    INDEX_CONFIG: Dict[str, Tuple[str, str]] = {
        "NIFTY":     ("NIFTY",     "NFO"),
        "BANKNIFTY": ("BANKNIFTY", "NFO"),
        "SENSEX":    ("SENSEX",    "BFO"),
    }

    def __init__(self, api_key: str, access_token: str = ""):
        self._api_key = api_key
        self._access_token = access_token
        self._maps: Dict[str, InstrumentMap] = {
            label: InstrumentMap(api_key, access_token, symbol=symbol, segment=segment)
            for label, (symbol, segment) in self.INDEX_CONFIG.items()
        }

    def set_access_token(self, token: str) -> None:
        self._access_token = token
        for m in self._maps.values():
            m.set_access_token(token)

    def refresh(self) -> bool:
        """Refresh all child maps. Returns True only if every child succeeded."""
        results = {label: m.refresh() for label, m in self._maps.items()}
        ok = all(results.values())
        if not ok:
            failed = [label for label, r in results.items() if not r]
            log.warning("MultiInstrumentMap partial refresh", failed=failed)
        return ok

    def get(self, index_label: str) -> Optional[InstrumentMap]:
        """Get the per-index InstrumentMap, or None if label is unknown."""
        return self._maps.get(index_label.upper())

    def get_option_instrument(
        self, index_label: str, strike: int, option_type: str, expiry: str
    ) -> Optional[dict]:
        m = self.get(index_label)
        return m.get_option_instrument(strike, option_type, expiry) if m else None

    def get_current_expiry(self, index_label: str) -> Optional[str]:
        m = self.get(index_label)
        return m.get_current_expiry() if m else None

    def get_strikes_around(
        self, index_label: str, spot: float, num_each_side: int = 3
    ) -> List[int]:
        """Return available strikes for an index centered around `spot`."""
        m = self.get(index_label)
        if not m:
            return []
        # InstrumentMap.get_nifty_strikes name is legacy — it works for any
        # underlying because the lookup goes through self._options.
        return m.get_nifty_strikes(spot, num_each_side=num_each_side)

    def labels(self) -> List[str]:
        return list(self._maps.keys())
