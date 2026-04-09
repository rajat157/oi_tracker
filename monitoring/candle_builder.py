"""CandleBuilder — aggregates Kite WebSocket ticks into 1-min and 3-min OHLC candles.

This is the core "data hub" for all strategies and alerts. It owns no Kite
connection directly — it is a `TickConsumer` that receives ticks from TickHub.
Per `(instrument_token, interval)` pair, it maintains:

1. A current in-progress candle (O/H/L/C/V, bucket start timestamp).
2. A ring buffer of the most recent N closed candles.
3. DB persistence to `live_candles` on each candle close.

Public read API:
    builder.get_candles(label_or_token, interval, count=N) -> List[candle dict]

Bootstrap + gap-fill:
    builder.bootstrap()         # one-time startup seed from kite.historical_data
    builder.backfill_gap(...)   # fired by on_connect after a reconnect

Instrument registration:
    builder.register_instrument(label, token, instr_type, intervals=('1min','3min'))
    builder.unregister_instrument(token)

Dynamic option strike rotation (called by scheduler every 3 min):
    builder.set_option_strikes(ce_strikes=[...], pe_strikes=[...], expiry=..., spot=...)

Thread safety: all state mutations (buffer append, current candle update,
instrument registration) are guarded by `threading.RLock`. Strategy reads
lock briefly and return a shallow copy so strategies are not holding the
lock during their own processing.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta, date as date_cls, time as time_cls
from typing import Dict, List, Optional, Set, Tuple, Union

from core.logger import get_logger
from db.legacy import (
    save_live_candle,
    get_live_candles,
    get_last_live_candle_ts,
)
from monitoring.tick_hub import TickConsumer

log = get_logger("candle_builder")

# Each (token, interval) buffer holds this many closed candles in memory.
BUFFER_SIZE = 240

# Intraday grace period before an unused option strike is actually dropped.
STRIKE_GRACE_MINUTES = 5

# Bootstrap lookback (how much recent history to seed at startup).
BOOTSTRAP_LOOKBACK_MIN = 240  # 4 hours

# Market open (IST) — used to align 3-min bucket boundaries to 09:15.
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15


def _align_1min_bucket(ts: datetime) -> datetime:
    """Floor `ts` to the start of its 1-minute bucket."""
    return ts.replace(second=0, microsecond=0)


def _align_3min_bucket(ts: datetime) -> datetime:
    """Floor `ts` to the start of its 3-minute bucket aligned to 9:15."""
    market_open = ts.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE,
        second=0, microsecond=0,
    )
    if ts < market_open:
        # Pre-market — use the previous day's 9:15 as anchor
        return market_open
    delta = ts - market_open
    buckets = int(delta.total_seconds() // 180)
    return market_open + timedelta(seconds=buckets * 180)


def _bucket_start_for(ts: datetime, interval: str) -> datetime:
    if interval == "1min":
        return _align_1min_bucket(ts)
    if interval == "3min":
        return _align_3min_bucket(ts)
    raise ValueError(f"Unsupported interval: {interval}")


def _strip_tz(ts):
    """Return a tz-naive datetime so bootstrap and live aggregator paths
    produce identical timestamp strings (avoids duplicate live_candles rows).

    Kite's historical_data returns tz-aware datetimes (IST, +05:30), while
    the WebSocket tick path can produce either. We normalize to tz-naive
    by simply dropping the tzinfo — IST is the only timezone we ever see.
    """
    if isinstance(ts, datetime):
        if ts.tzinfo is not None:
            return ts.replace(tzinfo=None)
        return ts
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except ValueError:
            return ts
    return ts


class CandleBuilder(TickConsumer):
    """Aggregates ticks into 1-min and 3-min OHLC candles per instrument."""

    def __init__(self, kite_fetcher=None, tick_hub=None):
        """Args:
            kite_fetcher: KiteDataFetcher (used only for bootstrap + gap fill).
            tick_hub: TickHub — used to request/release subscriptions when
                instruments are registered/unregistered. May be set later
                via `set_tick_hub()` to avoid circular construction order.
        """
        self._kite_fetcher = kite_fetcher
        self._tick_hub = tick_hub
        self._lock = threading.RLock()

        # token -> {label, instrument_type, intervals, expiry (for options)}
        self._instruments: Dict[int, dict] = {}
        # label -> token (fast reverse lookup)
        self._label_to_token: Dict[str, int] = {}

        # (token, interval) -> deque[candle dict]
        self._buffers: Dict[Tuple[int, str], deque] = {}
        # (token, interval) -> in-progress candle dict (or None)
        self._current: Dict[Tuple[int, str], Optional[dict]] = {}

        # Token grace-period tracking for option rotation.
        # token -> datetime when it was marked for removal (None = active)
        self._pending_removal: Dict[int, datetime] = {}

    def set_tick_hub(self, tick_hub) -> None:
        self._tick_hub = tick_hub

    # ------------------------------------------------------------------
    # Instrument registration
    # ------------------------------------------------------------------

    def register_instrument(
        self,
        label: str,
        token: int,
        instr_type: str,
        intervals: Tuple[str, ...] = ("1min", "3min"),
        expiry: Optional[str] = None,
        index_label: str = "NIFTY",
    ) -> None:
        """Start building candles for this instrument.

        Idempotent: re-registering a token keeps its existing buffer.
        Requests a TickHub subscription and seeds buffers from DB +
        historical_data (gap-fill).

        index_label: which underlying this option/index belongs to. Used
            to scope option-strike rotation per-index (so the NIFTY
            rotation doesn't kick out BANKNIFTY/SENSEX strikes) and to
            decide whether the historical bootstrap path can find a
            matching instrument map.
        """
        with self._lock:
            if token in self._instruments:
                # Already registered; just clear any pending-removal flag.
                self._pending_removal.pop(token, None)
                self._instruments[token]["label"] = label
                self._instruments[token]["expiry"] = expiry
                self._instruments[token]["index_label"] = index_label
                self._label_to_token[label] = token
                return

            self._instruments[token] = {
                "label": label,
                "instrument_type": instr_type,
                "intervals": tuple(intervals),
                "expiry": expiry,
                "index_label": index_label,
            }
            self._label_to_token[label] = token
            for interval in intervals:
                key = (token, interval)
                if key not in self._buffers:
                    self._buffers[key] = deque(maxlen=BUFFER_SIZE)
                    self._current[key] = None

        # Subscribe via TickHub if available.
        if self._tick_hub is not None:
            try:
                self._tick_hub.request_subscription([token])
            except Exception as e:
                log.error("TickHub.request_subscription failed",
                          token=token, error=str(e))

        # Seed history (DB first, then historical_data for gap fill).
        self._seed_history(token, intervals, label, instr_type, expiry, index_label)

        log.info("Instrument registered",
                 label=label, token=token, type=instr_type,
                 index=index_label, intervals=list(intervals))

    def unregister_instrument(self, token: int) -> None:
        """Immediately drop an instrument (no grace period). Releases
        the TickHub subscription."""
        with self._lock:
            meta = self._instruments.pop(token, None)
            if meta:
                self._label_to_token.pop(meta["label"], None)
            # Keep buffers around in memory for a moment so anyone mid-read
            # does not KeyError. Discard them explicitly:
            for key in list(self._buffers.keys()):
                if key[0] == token:
                    self._buffers.pop(key, None)
                    self._current.pop(key, None)
            self._pending_removal.pop(token, None)

        if self._tick_hub is not None and meta is not None:
            try:
                self._tick_hub.release_subscription([token])
            except Exception as e:
                log.error("TickHub.release_subscription failed",
                          token=token, error=str(e))

    def set_option_strikes(
        self,
        ce_strikes: List[int],
        pe_strikes: List[int],
        expiry: str,
        spot: float,
        instrument_map=None,
    ) -> None:
        """Rotate option strike subscriptions based on current spot.

        Called by scheduler every 3 min. Computes which strikes are new
        vs stale. New strikes are registered immediately (with bootstrap).
        Stale strikes are marked for removal; if still absent after
        STRIKE_GRACE_MINUTES, they are actually unregistered.
        """
        if instrument_map is None:
            log.warning("set_option_strikes: no instrument_map provided")
            return

        desired_tokens: Set[int] = set()
        desired_meta: Dict[int, dict] = {}

        for strike in ce_strikes:
            inst = instrument_map.get_option_instrument(strike, "CE", expiry)
            if inst:
                tok = inst["instrument_token"]
                desired_tokens.add(tok)
                desired_meta[tok] = {
                    "label": f"NIFTY_{strike}_CE",
                    "strike": strike,
                    "option_type": "CE",
                }
        for strike in pe_strikes:
            inst = instrument_map.get_option_instrument(strike, "PE", expiry)
            if inst:
                tok = inst["instrument_token"]
                desired_tokens.add(tok)
                desired_meta[tok] = {
                    "label": f"NIFTY_{strike}_PE",
                    "strike": strike,
                    "option_type": "PE",
                }

        now = datetime.now()
        with self._lock:
            current_option_tokens = {
                tok for tok, meta in self._instruments.items()
                if meta.get("instrument_type") == "option"
            }

        new_tokens = desired_tokens - current_option_tokens
        stale_tokens = current_option_tokens - desired_tokens

        # Register newly-desired strikes
        for tok in new_tokens:
            meta = desired_meta[tok]
            self.register_instrument(
                label=meta["label"], token=tok, instr_type="option",
                intervals=("1min", "3min"), expiry=expiry,
            )

        # Refresh grace-period tracking
        with self._lock:
            # Clear pending removal on any token that's back in the desired set
            for tok in desired_tokens:
                self._pending_removal.pop(tok, None)
            # Mark stale tokens (if not already pending) and record the time
            for tok in stale_tokens:
                if tok not in self._pending_removal:
                    self._pending_removal[tok] = now
            # Actually drop any that have exceeded the grace period
            to_drop = [
                tok for tok, since in self._pending_removal.items()
                if (now - since).total_seconds() >= STRIKE_GRACE_MINUTES * 60
            ]

        for tok in to_drop:
            self.unregister_instrument(tok)

        log.info("Option strikes rotated",
                 new=len(new_tokens),
                 stale_pending=len(stale_tokens) - len(to_drop),
                 dropped=len(to_drop),
                 total_option_tokens=len(desired_tokens))

    def register_option_strike(
        self,
        index_label: str,
        strike: int,
        option_type: str,
        expiry: str,
        instrument_map=None,
    ) -> Optional[str]:
        """Lazy-subscribe to a single option strike for any index.

        Used by IntradayHunter when a position opens — we don't want to
        permanently subscribe to BN/SX option ATM strikes (the spot moves
        all day, ATM rotates), so we subscribe on demand.

        Returns the registered label (e.g. "BANKNIFTY_56000_CE") on success,
        or None if the instrument cannot be resolved.
        """
        if instrument_map is None:
            log.warning("register_option_strike: no instrument_map",
                        index=index_label, strike=strike, type=option_type)
            return None
        inst = instrument_map.get_option_instrument(strike, option_type, expiry)
        if not inst:
            log.warning("register_option_strike: instrument not found",
                        index=index_label, strike=strike, type=option_type, expiry=expiry)
            return None

        token = inst["instrument_token"]
        label = f"{index_label}_{strike}_{option_type}"
        self.register_instrument(
            label=label,
            token=token,
            instr_type="option",
            intervals=("1min", "3min"),
            expiry=expiry,
            index_label=index_label,
        )
        return label

    # ------------------------------------------------------------------
    # Bootstrap + gap-fill
    # ------------------------------------------------------------------

    def _seed_history(
        self,
        token: int,
        intervals: Tuple[str, ...],
        label: str,
        instr_type: str,
        expiry: Optional[str],
        index_label: str = "NIFTY",
    ) -> None:
        """Load recent candles from DB + backfill the gap via historical_data.

        Runs inline (blocking). Caller should expect ~1 API call per
        (interval) pair, so ~2 per instrument.
        """
        for interval in intervals:
            # 1. Pull existing candles from DB into the in-memory buffer
            db_candles = get_live_candles(token, interval, limit=BUFFER_SIZE)
            with self._lock:
                buf = self._buffers.get((token, interval))
                if buf is not None:
                    for c in db_candles:
                        buf.append(c)
            # 2. Optionally fetch recent history from Kite to cover any gap
            self._fetch_and_append_history(
                token, interval, label, instr_type, expiry,
                lookback_minutes=BOOTSTRAP_LOOKBACK_MIN,
                index_label=index_label)

    def _fetch_and_append_history(
        self,
        token: int,
        interval: str,
        label: str,
        instr_type: str,
        expiry: Optional[str],
        lookback_minutes: int,
        index_label: str = "NIFTY",
    ) -> None:
        """Fetch OHLC from kite.historical_data and append missing candles
        to the buffer + DB."""
        if self._kite_fetcher is None:
            return

        try:
            kite_interval = "minute" if interval == "1min" else "3minute"
            if instr_type in ("index", "stock"):
                # Generic by-token fetch — works for NIFTY, BANKNIFTY, SENSEX,
                # HDFCBANK, KOTAKBANK, etc.
                candles = self._kite_fetcher.fetch_token_candles(
                    token=token,
                    interval=kite_interval,
                    lookback_minutes=lookback_minutes,
                )
            elif instr_type == "option":
                if index_label != "NIFTY":
                    # BN/SX option historical needs MultiInstrumentMap routing
                    # which fetch_option_candles doesn't yet do. Use the
                    # generic by-token path so we still backfill — the
                    # token is the source of truth either way.
                    candles = self._kite_fetcher.fetch_token_candles(
                        token=token,
                        interval=kite_interval,
                        lookback_minutes=lookback_minutes,
                    )
                else:
                    # Parse strike/type out of the label "NIFTY_{strike}_{CE|PE}"
                    try:
                        parts = label.split("_")
                        strike = int(parts[-2])
                        option_type = parts[-1]
                    except (ValueError, IndexError):
                        log.error("Cannot parse option label", label=label)
                        return
                    candles = self._kite_fetcher.fetch_option_candles(
                        strike=strike,
                        option_type=option_type,
                        lookback_minutes=lookback_minutes,
                        interval=kite_interval,
                    )
            else:
                log.warning("Unknown instrument_type for history fetch",
                            label=label, type=instr_type)
                return

            if not candles:
                return

            with self._lock:
                buf = self._buffers.get((token, interval))
                if buf is None:
                    return
                existing_ts = {_strip_tz(c["date"]) for c in buf}
                for row in candles:
                    dt = _strip_tz(row["date"])
                    if dt in existing_ts:
                        continue
                    candle = {
                        "date": dt,
                        "open": row["open"],
                        "high": row["high"],
                        "low": row["low"],
                        "close": row["close"],
                        "volume": row.get("volume", 0),
                        "oi": row.get("oi", 0),
                    }
                    buf.append(candle)
                    # Persist (INSERT OR REPLACE is idempotent)
                    ts_str = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
                    try:
                        save_live_candle(
                            timestamp=ts_str,
                            instrument_token=token,
                            interval=interval,
                            label=label,
                            instrument_type=instr_type,
                            open=row["open"],
                            high=row["high"],
                            low=row["low"],
                            close=row["close"],
                            volume=int(row.get("volume", 0) or 0),
                            oi=int(row.get("oi", 0) or 0),
                        )
                    except Exception as e:
                        log.error("save_live_candle failed",
                                  token=token, interval=interval, ts=ts_str,
                                  error=str(e))

            log.info("History seeded",
                     token=token, interval=interval, label=label,
                     fetched=len(candles))
        except Exception as e:
            log.error("_fetch_and_append_history failed",
                      token=token, interval=interval, error=str(e))

    def bootstrap(self) -> None:
        """One-time startup seed across all currently-registered instruments."""
        with self._lock:
            items = [(tok, meta) for tok, meta in self._instruments.items()]
        for tok, meta in items:
            self._fetch_and_append_history(
                token=tok,
                interval="1min",
                label=meta["label"],
                instr_type=meta["instrument_type"],
                expiry=meta.get("expiry"),
                lookback_minutes=BOOTSTRAP_LOOKBACK_MIN,
            )
            self._fetch_and_append_history(
                token=tok,
                interval="3min",
                label=meta["label"],
                instr_type=meta["instrument_type"],
                expiry=meta.get("expiry"),
                lookback_minutes=BOOTSTRAP_LOOKBACK_MIN,
            )
        log.info("CandleBuilder bootstrap complete", instruments=len(items))

    def backfill_gap(self, token: int, interval: str) -> None:
        """Called after a WebSocket reconnect — detect the last stored
        candle and fetch anything newer via historical_data."""
        with self._lock:
            meta = self._instruments.get(token)
        if not meta:
            return
        self._fetch_and_append_history(
            token=token,
            interval=interval,
            label=meta["label"],
            instr_type=meta["instrument_type"],
            expiry=meta.get("expiry"),
            lookback_minutes=BOOTSTRAP_LOOKBACK_MIN,
        )

    # ------------------------------------------------------------------
    # TickConsumer interface
    # ------------------------------------------------------------------

    def on_tick(self, token: int, tick: dict) -> None:
        """Aggregate one tick into the current candle(s) for this token.

        Runs on the WebSocket thread. Keep it fast.
        """
        with self._lock:
            meta = self._instruments.get(token)
            if not meta:
                return

            last_price = tick.get("last_price")
            if last_price is None:
                return

            ts = self._extract_ts(tick)
            if ts is None:
                return

            for interval in meta["intervals"]:
                self._ingest(token, interval, ts, last_price, tick, meta)

    def on_connect(self) -> None:
        """After reconnect: backfill the gap for every (token, interval)."""
        with self._lock:
            keys = list(self._buffers.keys())
        for token, interval in keys:
            try:
                self.backfill_gap(token, interval)
            except Exception as e:
                log.error("backfill_gap failed on reconnect",
                          token=token, interval=interval, error=str(e))

    def get_required_tokens(self) -> Set[int]:
        with self._lock:
            return set(self._instruments.keys())

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def get_candles(
        self,
        label_or_token: Union[str, int],
        interval: str,
        count: Optional[int] = None,
    ) -> List[dict]:
        """Return a list of closed candles (oldest first).

        The caller receives a shallow copy so the internal deque is safe
        to mutate on the WS thread concurrently.
        """
        if isinstance(label_or_token, str):
            token = self._label_to_token.get(label_or_token)
            if token is None:
                return []
        else:
            token = label_or_token

        with self._lock:
            buf = self._buffers.get((token, interval))
            if buf is None:
                return []
            candles = list(buf)

        if count is not None and count > 0:
            candles = candles[-count:]
        return candles

    def get_current_candle(
        self, label_or_token: Union[str, int], interval: str,
    ) -> Optional[dict]:
        """Return the in-progress (not yet closed) candle, or None."""
        if isinstance(label_or_token, str):
            token = self._label_to_token.get(label_or_token)
            if token is None:
                return None
        else:
            token = label_or_token
        with self._lock:
            cur = self._current.get((token, interval))
            return dict(cur) if cur else None

    # ------------------------------------------------------------------
    # Internal aggregation
    # ------------------------------------------------------------------

    def _extract_ts(self, tick: dict) -> Optional[datetime]:
        """Extract a tz-naive datetime from a Kite tick.

        Kite MODE_FULL ticks contain `exchange_timestamp` (datetime) or
        `last_trade_time` (datetime). We strip any tz info so live and
        bootstrap candles share the same timestamp format.
        """
        ts = tick.get("exchange_timestamp") or tick.get("last_trade_time")
        if isinstance(ts, datetime):
            return _strip_tz(ts)
        if isinstance(ts, str):
            try:
                return _strip_tz(datetime.fromisoformat(ts))
            except ValueError:
                pass
        return datetime.now()

    def _ingest(
        self,
        token: int,
        interval: str,
        ts: datetime,
        last_price: float,
        tick: dict,
        meta: dict,
    ) -> None:
        """Apply one tick to the (token, interval) aggregation."""
        bucket = _bucket_start_for(ts, interval)
        key = (token, interval)
        current = self._current.get(key)

        if current is None:
            self._current[key] = self._make_new_candle(
                bucket, last_price, tick)
            return

        if bucket > current["bucket"]:
            # Close the previous candle and persist it
            self._flush(token, interval, current, meta)
            self._current[key] = self._make_new_candle(
                bucket, last_price, tick)
            return

        # Same bucket — update H/L/C and best-effort volume/OI
        if last_price > current["high"]:
            current["high"] = last_price
        if last_price < current["low"]:
            current["low"] = last_price
        current["close"] = last_price

        vol_total = tick.get("volume_traded") or tick.get("volume") or 0
        if vol_total:
            # tick.volume_traded is cumulative day volume; bar volume is delta
            if current["_start_vol"] == 0 and vol_total:
                current["_start_vol"] = vol_total
            current["volume"] = max(0, int(vol_total - current["_start_vol"]))

        oi = tick.get("oi")
        if oi is not None:
            current["oi"] = int(oi)

    def _make_new_candle(
        self, bucket: datetime, price: float, tick: dict,
    ) -> dict:
        vol_total = tick.get("volume_traded") or tick.get("volume") or 0
        return {
            "bucket": bucket,
            "date": bucket,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 0,
            "oi": int(tick.get("oi") or 0),
            "_start_vol": int(vol_total or 0),
        }

    def _flush(
        self, token: int, interval: str, candle: dict, meta: dict,
    ) -> None:
        """Append a closed candle to the ring buffer and persist to DB."""
        buf = self._buffers.get((token, interval))
        if buf is None:
            return
        closed = {
            "date": candle["bucket"],
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "volume": candle["volume"],
            "oi": candle["oi"],
        }
        buf.append(closed)

        ts_str = candle["bucket"].isoformat()
        try:
            save_live_candle(
                timestamp=ts_str,
                instrument_token=token,
                interval=interval,
                label=meta["label"],
                instrument_type=meta["instrument_type"],
                open=closed["open"],
                high=closed["high"],
                low=closed["low"],
                close=closed["close"],
                volume=int(closed["volume"] or 0),
                oi=int(closed["oi"] or 0),
            )
        except Exception as e:
            log.error("save_live_candle failed on flush",
                      token=token, interval=interval, ts=ts_str,
                      error=str(e))
