"""Tests for monitoring/tick_hub.py — TickHub + TickConsumer."""

from unittest.mock import MagicMock

import pytest

from monitoring.tick_hub import TickHub, TickConsumer


class RecordingConsumer(TickConsumer):
    """Test consumer that records every tick it sees."""

    def __init__(self, required_tokens=None):
        self.ticks = []
        self.on_connect_calls = 0
        self._required = set(required_tokens or [])

    def on_tick(self, token, tick):
        self.ticks.append((token, tick))

    def on_connect(self):
        self.on_connect_calls += 1

    def get_required_tokens(self):
        return set(self._required)


class TestRefCounting:
    def test_request_subscription_increments(self):
        hub = TickHub()
        hub.request_subscription([100, 200])
        assert hub.get_ref_count(100) == 1
        assert hub.get_ref_count(200) == 1

    def test_multiple_requests_stack(self):
        hub = TickHub()
        hub.request_subscription([100])
        hub.request_subscription([100])
        hub.request_subscription([100])
        assert hub.get_ref_count(100) == 3

    def test_release_decrements_but_keeps_until_zero(self):
        hub = TickHub()
        hub.request_subscription([100])
        hub.request_subscription([100])
        hub.release_subscription([100])
        assert hub.get_ref_count(100) == 1
        assert 100 in hub.get_subscribed_tokens()
        hub.release_subscription([100])
        assert hub.get_ref_count(100) == 0
        assert 100 not in hub.get_subscribed_tokens()

    def test_release_nonexistent_is_noop(self):
        hub = TickHub()
        hub.release_subscription([999])  # no error
        assert hub.get_ref_count(999) == 0

    def test_release_does_not_go_negative(self):
        hub = TickHub()
        hub.request_subscription([100])
        hub.release_subscription([100])
        hub.release_subscription([100])  # second release is no-op
        assert hub.get_ref_count(100) == 0


class TestConsumerDispatch:
    def test_dispatch_to_all_consumers(self):
        hub = TickHub()
        c1 = RecordingConsumer()
        c2 = RecordingConsumer()
        hub.add_consumer(c1)
        hub.add_consumer(c2)

        hub._on_ticks(None, [
            {"instrument_token": 100, "last_price": 50.0},
            {"instrument_token": 200, "last_price": 60.0},
        ])

        assert len(c1.ticks) == 2
        assert len(c2.ticks) == 2
        assert c1.ticks[0][0] == 100
        assert c1.ticks[1][0] == 200

    def test_consumer_exception_does_not_break_dispatch(self):
        hub = TickHub()

        class Broken(TickConsumer):
            def on_tick(self, token, tick):
                raise RuntimeError("boom")

        good = RecordingConsumer()
        hub.add_consumer(Broken())
        hub.add_consumer(good)

        hub._on_ticks(None, [{"instrument_token": 1, "last_price": 10.0}])
        # Good consumer still got the tick despite the broken one
        assert len(good.ticks) == 1

    def test_tick_without_token_is_skipped(self):
        hub = TickHub()
        c = RecordingConsumer()
        hub.add_consumer(c)
        hub._on_ticks(None, [{"last_price": 10.0}])  # no instrument_token
        assert c.ticks == []

    def test_add_same_consumer_twice_is_idempotent(self):
        hub = TickHub()
        c = RecordingConsumer()
        hub.add_consumer(c)
        hub.add_consumer(c)
        hub._on_ticks(None, [{"instrument_token": 1, "last_price": 10.0}])
        # Only one tick, not two (consumer not duplicated)
        assert len(c.ticks) == 1


class TestOnConnect:
    def test_on_connect_dispatches_to_consumers(self):
        hub = TickHub()
        c1 = RecordingConsumer(required_tokens=[100])
        c2 = RecordingConsumer(required_tokens=[200])
        hub.add_consumer(c1)
        hub.add_consumer(c2)

        mock_ws = MagicMock()
        hub._on_connect(mock_ws, {})

        assert c1.on_connect_calls == 1
        assert c2.on_connect_calls == 1

    def test_on_connect_broken_consumer_does_not_break_others(self):
        hub = TickHub()

        class Broken(TickConsumer):
            def on_tick(self, token, tick):
                pass

            def on_connect(self):
                raise RuntimeError("boom")

        good = RecordingConsumer(required_tokens=[500])
        hub.add_consumer(Broken())
        hub.add_consumer(good)
        hub._on_connect(MagicMock(), {})
        assert good.on_connect_calls == 1

    def test_on_connect_merges_consumer_required_tokens(self):
        hub = TickHub()
        c = RecordingConsumer(required_tokens=[111, 222])
        hub.add_consumer(c)
        hub._on_connect(MagicMock(), {})
        # Tokens from get_required_tokens are now tracked in hub's ref map
        assert 111 in hub.get_subscribed_tokens()
        assert 222 in hub.get_subscribed_tokens()


class TestHealthCounter:
    def test_tick_counter_increments(self):
        hub = TickHub()
        hub.add_consumer(RecordingConsumer())
        hub._on_ticks(None, [
            {"instrument_token": 1, "last_price": 10.0},
            {"instrument_token": 2, "last_price": 20.0},
        ])
        assert hub._tick_count == 2
