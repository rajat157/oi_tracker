"""Tests for monitoring/candle_builder.py — tick → OHLC aggregation + rotation."""

from collections import deque
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from monitoring.candle_builder import (
    CandleBuilder,
    _align_1min_bucket,
    _align_3min_bucket,
    _bucket_start_for,
    _strip_tz,
    BUFFER_SIZE,
    STRIKE_GRACE_MINUTES,
)


@pytest.fixture
def builder():
    """A CandleBuilder with no fetcher and no hub — pure in-memory aggregation."""
    cb = CandleBuilder(kite_fetcher=None, tick_hub=None)
    # Manually register NIFTY without triggering history fetch.
    cb._instruments[256265] = {
        "label": "NIFTY",
        "instrument_type": "index",
        "intervals": ("1min", "3min"),
        "expiry": None,
    }
    cb._label_to_token["NIFTY"] = 256265
    cb._buffers[(256265, "1min")] = deque(maxlen=BUFFER_SIZE)
    cb._buffers[(256265, "3min")] = deque(maxlen=BUFFER_SIZE)
    cb._current[(256265, "1min")] = None
    cb._current[(256265, "3min")] = None
    return cb


def _tick(price, ts, token=256265, volume_traded=None):
    d = {"instrument_token": token, "last_price": price, "exchange_timestamp": ts}
    if volume_traded is not None:
        d["volume_traded"] = volume_traded
    return d


class TestStripTz:
    """Regression tests for the tz-strip helper.

    Bootstrap (kite.historical_data) returns tz-aware datetimes, while live
    tick aggregation produces tz-naive ones. Both must serialize to the same
    ISO string so the live_candles primary key catches duplicates instead of
    storing two rows for the same minute.
    """

    def test_strips_tzinfo(self):
        from datetime import timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        aware = datetime(2026, 4, 7, 9, 21, 0, tzinfo=ist)
        result = _strip_tz(aware)
        assert result.tzinfo is None
        assert result == datetime(2026, 4, 7, 9, 21, 0)

    def test_naive_unchanged(self):
        naive = datetime(2026, 4, 7, 9, 21, 0)
        assert _strip_tz(naive) == naive
        assert _strip_tz(naive).tzinfo is None

    def test_isoformat_string_with_tz(self):
        result = _strip_tz("2026-04-07T09:21:00+05:30")
        assert isinstance(result, datetime)
        assert result.tzinfo is None
        assert result == datetime(2026, 4, 7, 9, 21, 0)

    def test_isoformat_string_without_tz(self):
        result = _strip_tz("2026-04-07T09:21:00")
        assert isinstance(result, datetime)
        assert result == datetime(2026, 4, 7, 9, 21, 0)

    def test_aware_and_naive_serialize_identically_after_strip(self):
        """The fix: both paths produce the same DB primary-key string."""
        from datetime import timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        from_bootstrap = datetime(2026, 4, 7, 9, 21, 0, tzinfo=ist)
        from_live = datetime(2026, 4, 7, 9, 21, 0)
        assert _strip_tz(from_bootstrap).isoformat() == _strip_tz(from_live).isoformat()


class TestBucketAlignment:
    def test_1min_floor(self):
        ts = datetime(2026, 4, 6, 10, 17, 42)
        assert _align_1min_bucket(ts) == datetime(2026, 4, 6, 10, 17, 0)

    def test_3min_boundary_values(self):
        assert _align_3min_bucket(datetime(2026, 4, 6, 9, 15, 0)) == datetime(2026, 4, 6, 9, 15, 0)
        assert _align_3min_bucket(datetime(2026, 4, 6, 9, 17, 59)) == datetime(2026, 4, 6, 9, 15, 0)
        assert _align_3min_bucket(datetime(2026, 4, 6, 9, 18, 0)) == datetime(2026, 4, 6, 9, 18, 0)
        assert _align_3min_bucket(datetime(2026, 4, 6, 10, 20, 30)) == datetime(2026, 4, 6, 10, 18, 0)
        assert _align_3min_bucket(datetime(2026, 4, 6, 10, 21, 0)) == datetime(2026, 4, 6, 10, 21, 0)

    def test_bucket_start_for_router(self):
        ts = datetime(2026, 4, 6, 10, 15, 30)
        assert _bucket_start_for(ts, "1min") == datetime(2026, 4, 6, 10, 15, 0)
        assert _bucket_start_for(ts, "3min") == datetime(2026, 4, 6, 10, 15, 0)

    def test_unknown_interval_raises(self):
        with pytest.raises(ValueError):
            _bucket_start_for(datetime.now(), "5min")


class TestSingleBucketAggregation:
    def test_ohlc_within_one_bucket(self, builder):
        builder.on_tick(256265, _tick(22500.0, datetime(2026, 4, 6, 10, 15, 5)))
        builder.on_tick(256265, _tick(22510.0, datetime(2026, 4, 6, 10, 15, 30)))
        builder.on_tick(256265, _tick(22495.0, datetime(2026, 4, 6, 10, 15, 45)))
        builder.on_tick(256265, _tick(22505.0, datetime(2026, 4, 6, 10, 15, 58)))
        # Bucket not yet closed
        assert builder.get_candles("NIFTY", "1min") == []
        cur = builder.get_current_candle("NIFTY", "1min")
        assert cur["open"] == 22500.0
        assert cur["high"] == 22510.0
        assert cur["low"] == 22495.0
        assert cur["close"] == 22505.0

    def test_candle_closes_on_bucket_transition(self, builder):
        builder.on_tick(256265, _tick(100.0, datetime(2026, 4, 6, 10, 15, 5)))
        builder.on_tick(256265, _tick(110.0, datetime(2026, 4, 6, 10, 15, 30)))
        # New minute — previous candle flushes
        builder.on_tick(256265, _tick(115.0, datetime(2026, 4, 6, 10, 16, 2)))
        closed = builder.get_candles("NIFTY", "1min")
        assert len(closed) == 1
        c = closed[0]
        assert c["open"] == 100.0
        assert c["high"] == 110.0
        assert c["low"] == 100.0
        assert c["close"] == 110.0
        assert c["date"] == datetime(2026, 4, 6, 10, 15, 0)
        # New candle is in progress
        cur = builder.get_current_candle("NIFTY", "1min")
        assert cur["open"] == 115.0

    def test_volume_delta_from_cumulative(self, builder):
        builder.on_tick(256265, _tick(100.0, datetime(2026, 4, 6, 10, 15, 5), volume_traded=1000))
        builder.on_tick(256265, _tick(101.0, datetime(2026, 4, 6, 10, 15, 30), volume_traded=1050))
        builder.on_tick(256265, _tick(102.0, datetime(2026, 4, 6, 10, 16, 2), volume_traded=1100))
        closed = builder.get_candles("NIFTY", "1min")
        assert len(closed) == 1
        # Volume in the bar = 1050 - 1000 = 50 (the 1100 tick starts the next bar)
        assert closed[0]["volume"] == 50


class TestMultipleBuckets:
    def test_three_minute_aggregation(self, builder):
        # Five ticks spanning 10:15 → 10:16 → 10:17 → 10:18 (crosses 3-min boundary at 10:18)
        builder.on_tick(256265, _tick(100, datetime(2026, 4, 6, 10, 15, 5)))
        builder.on_tick(256265, _tick(105, datetime(2026, 4, 6, 10, 16, 10)))
        builder.on_tick(256265, _tick(97, datetime(2026, 4, 6, 10, 17, 30)))
        builder.on_tick(256265, _tick(103, datetime(2026, 4, 6, 10, 18, 5)))  # New 3-min bucket
        closed_3m = builder.get_candles("NIFTY", "3min")
        assert len(closed_3m) == 1
        c = closed_3m[0]
        assert c["date"] == datetime(2026, 4, 6, 10, 15, 0)
        assert c["open"] == 100
        assert c["high"] == 105
        assert c["low"] == 97
        assert c["close"] == 97  # last close BEFORE the bucket transition
        # 1-min path should have 3 closed candles (10:15, 10:16, 10:17)
        closed_1m = builder.get_candles("NIFTY", "1min")
        assert len(closed_1m) == 3

    def test_count_parameter_returns_last_n(self, builder):
        for i in range(10):
            ts = datetime(2026, 4, 6, 10, 15 + i, 5)
            ts_next = datetime(2026, 4, 6, 10, 16 + i, 5)
            builder.on_tick(256265, _tick(100.0 + i, ts))
            builder.on_tick(256265, _tick(101.0 + i, ts_next))  # force flush
        all_candles = builder.get_candles("NIFTY", "1min")
        last3 = builder.get_candles("NIFTY", "1min", count=3)
        assert len(last3) == 3
        assert last3 == all_candles[-3:]


class TestUnknownInstrument:
    def test_unknown_token_noop(self, builder):
        builder.on_tick(999999, _tick(100.0, datetime(2026, 4, 6, 10, 15, 0), token=999999))
        assert builder.get_current_candle("NIFTY", "1min") is None

    def test_unknown_label_returns_empty(self, builder):
        assert builder.get_candles("NONEXISTENT", "1min") == []


class TestDBPersistence:
    def test_flush_calls_save_live_candle(self, builder):
        with patch("monitoring.candle_builder.save_live_candle") as mock_save:
            builder.on_tick(256265, _tick(100.0, datetime(2026, 4, 6, 10, 15, 5)))
            builder.on_tick(256265, _tick(110.0, datetime(2026, 4, 6, 10, 16, 2)))  # flush

            # At least one save call for the 1-min close
            assert mock_save.call_count >= 1
            kwargs = mock_save.call_args_list[0].kwargs
            assert kwargs["instrument_token"] == 256265
            assert kwargs["label"] == "NIFTY"
            assert kwargs["interval"] in ("1min", "3min")
            assert kwargs["instrument_type"] == "index"


class TestStrikeRotation:
    def test_new_strikes_registered(self):
        fake_inst_map = MagicMock()
        # Return different tokens per strike
        fake_inst_map.get_option_instrument.side_effect = lambda strike, ot, exp: {
            "instrument_token": 10000 + strike + (0 if ot == "CE" else 1),
        }
        fake_hub = MagicMock()
        cb = CandleBuilder(kite_fetcher=None, tick_hub=fake_hub)

        cb.set_option_strikes(
            ce_strikes=[22500, 22550, 22600],
            pe_strikes=[22650, 22700, 22750],
            expiry="2026-04-10",
            spot=22600,
            instrument_map=fake_inst_map,
        )
        # 6 new option instruments should have been registered
        option_instruments = [
            tok for tok, m in cb._instruments.items()
            if m.get("instrument_type") == "option"
        ]
        assert len(option_instruments) == 6
        # TickHub should have been asked to subscribe each one
        assert fake_hub.request_subscription.call_count >= 6

    def test_grace_period_before_actual_drop(self):
        fake_inst_map = MagicMock()
        fake_inst_map.get_option_instrument.side_effect = lambda strike, ot, exp: {
            "instrument_token": 10000 + strike + (0 if ot == "CE" else 1),
        }
        fake_hub = MagicMock()
        cb = CandleBuilder(kite_fetcher=None, tick_hub=fake_hub)

        # Round 1: register 3 CE + 3 PE
        cb.set_option_strikes(
            ce_strikes=[22500, 22550, 22600],
            pe_strikes=[22650, 22700, 22750],
            expiry="2026-04-10",
            spot=22600,
            instrument_map=fake_inst_map,
        )
        before = {tok for tok, m in cb._instruments.items() if m["instrument_type"] == "option"}
        assert len(before) == 6

        # Round 2: totally different strikes (all old ones become stale)
        cb.set_option_strikes(
            ce_strikes=[22800, 22850, 22900],
            pe_strikes=[22950, 23000, 23050],
            expiry="2026-04-10",
            spot=22900,
            instrument_map=fake_inst_map,
        )
        # Old strikes should still be in _instruments (grace period) + new ones added
        option_count = sum(
            1 for m in cb._instruments.values() if m["instrument_type"] == "option"
        )
        assert option_count == 12  # 6 old (grace) + 6 new

        # Force grace-period to expire: fake the pending_removal timestamps
        from datetime import datetime, timedelta
        long_ago = datetime.now() - timedelta(minutes=STRIKE_GRACE_MINUTES + 1)
        for tok in list(cb._pending_removal.keys()):
            cb._pending_removal[tok] = long_ago

        # Round 3: same strikes as round 2 — this should now drop the pending ones
        cb.set_option_strikes(
            ce_strikes=[22800, 22850, 22900],
            pe_strikes=[22950, 23000, 23050],
            expiry="2026-04-10",
            spot=22900,
            instrument_map=fake_inst_map,
        )
        option_count = sum(
            1 for m in cb._instruments.values() if m["instrument_type"] == "option"
        )
        assert option_count == 6  # only the new ones remain


class TestLabelLookup:
    def test_label_resolves_to_token(self, builder):
        builder.on_tick(256265, _tick(100.0, datetime(2026, 4, 6, 10, 15, 5)))
        builder.on_tick(256265, _tick(110.0, datetime(2026, 4, 6, 10, 16, 2)))  # flush

        by_label = builder.get_candles("NIFTY", "1min")
        by_token = builder.get_candles(256265, "1min")
        assert by_label == by_token
