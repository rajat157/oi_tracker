"""Kite instrument map — daily CSV cache + strike/future lookups."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import date

from app.services.logging_service import get_logger

log = get_logger("instruments")

INSTRUMENTS_URL = "https://api.kite.trade/instruments/NFO"


class InstrumentService:
    """Downloads NFO instruments CSV from Kite and provides lookup indices."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._access_token: str = ""
        self._instruments: list[dict] = []
        self._cache_date: date | None = None
        self._options: dict[tuple[int, str, str], dict] = {}  # (strike, type, expiry)
        self._futures: list[dict] = []
        self._expiries: list[str] = []

    def set_access_token(self, token: str) -> None:
        self._access_token = token

    async def load_instruments(self) -> bool:
        """Download and parse instruments CSV. Cached per day."""
        today = date.today()
        if self._cache_date == today and self._instruments:
            return True

        try:
            raw = await asyncio.get_running_loop().run_in_executor(
                None, self._fetch_csv
            )
            reader = csv.DictReader(io.StringIO(raw))
            all_inst = list(reader)

            self._instruments = [i for i in all_inst if i.get("name") == "NIFTY"]

            for inst in self._instruments:
                inst["strike"] = float(inst.get("strike", 0))
                if inst["strike"] == int(inst["strike"]):
                    inst["strike"] = int(inst["strike"])
                inst["instrument_token"] = int(inst.get("instrument_token", 0))
                inst["lot_size"] = int(inst.get("lot_size", 75))

            self._options = {}
            self._futures = []
            expiry_set: set[str] = set()

            for inst in self._instruments:
                segment = inst.get("segment", "")
                itype = inst.get("instrument_type", "")
                if segment == "NFO-OPT":
                    key = (inst["strike"], itype, inst["expiry"])
                    self._options[key] = inst
                    expiry_set.add(inst["expiry"])
                elif segment == "NFO-FUT":
                    self._futures.append(inst)

            self._expiries = sorted(expiry_set)
            self._cache_date = today
            log.info(
                "Instruments refreshed",
                options=len(self._options),
                futures=len(self._futures),
                expiries=len(self._expiries),
            )
            return True

        except Exception as e:
            log.error("Failed to refresh instruments", error=str(e))
            return False

    def _fetch_csv(self) -> str:
        import requests

        headers = {}
        if self._api_key and self._access_token:
            headers["Authorization"] = f"token {self._api_key}:{self._access_token}"
            headers["X-Kite-Version"] = "3"
        resp = requests.get(INSTRUMENTS_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text

    # ── Lookups ────────────────────────────────────────

    def get_current_expiry(self) -> str | None:
        today = date.today()
        for exp_str in self._expiries:
            try:
                if date.fromisoformat(exp_str) >= today:
                    return exp_str
            except ValueError:
                continue
        return self._expiries[-1] if self._expiries else None

    def get_option_instrument(
        self, strike: int, option_type: str, expiry: str
    ) -> dict | None:
        return self._options.get((strike, option_type, expiry))

    def get_instrument_token(self, strike: int, option_type: str, expiry: str) -> int | None:
        inst = self.get_option_instrument(strike, option_type, expiry)
        return inst["instrument_token"] if inst else None

    def get_nifty_strikes(self, spot: float, num_each_side: int = 15) -> list[int]:
        expiry = self.get_current_expiry()
        if not expiry:
            return []
        available = sorted({s for (s, _, e) in self._options if e == expiry})
        if not available:
            return []
        atm_idx = min(range(len(available)), key=lambda i: abs(available[i] - spot))
        start = max(0, atm_idx - num_each_side)
        end = min(len(available), atm_idx + num_each_side + 1)
        return available[start:end]

    def build_quote_symbols(
        self, strikes: list[int], expiry: str
    ) -> dict[str, tuple[int, str, int]]:
        symbols = {}
        for strike in strikes:
            for otype in ("CE", "PE"):
                inst = self.get_option_instrument(strike, otype, expiry)
                if inst:
                    key = f"NFO:{inst['tradingsymbol']}"
                    symbols[key] = (strike, otype, inst["instrument_token"])
        return symbols

    def get_nifty_future(self) -> dict | None:
        if not self._futures:
            return None
        today = date.today()
        valid = []
        for fut in self._futures:
            try:
                exp = date.fromisoformat(fut["expiry"])
                if exp >= today:
                    valid.append((exp, fut))
            except ValueError:
                continue
        if valid:
            valid.sort(key=lambda x: x[0])
            return valid[0][1]
        return self._futures[0]

    @staticmethod
    def spot_symbol() -> str:
        return "NSE:NIFTY 50"

    @staticmethod
    def vix_symbol() -> str:
        return "NSE:INDIA VIX"
