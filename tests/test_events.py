"""Tests for core/events.py — EventBus pub/sub."""

import pytest

from core.events import EventBus, EventType


class TestEventBus:
    def setup_method(self):
        self.bus = EventBus()

    def test_subscribe_and_publish(self):
        received = []
        self.bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))
        self.bus.publish(EventType.TRADE_CREATED, {"id": 1})
        assert received == [{"id": 1}]

    def test_multiple_subscribers(self):
        results = []
        self.bus.subscribe("TEST", lambda et, d: results.append("A"))
        self.bus.subscribe("TEST", lambda et, d: results.append("B"))
        self.bus.publish("TEST")
        assert results == ["A", "B"]

    def test_no_subscribers(self):
        # Should not raise
        self.bus.publish(EventType.TRADE_EXITED, {"id": 42})

    def test_exception_in_subscriber_doesnt_kill_others(self):
        results = []

        def bad_handler(et, d):
            raise ValueError("boom")

        self.bus.subscribe("TEST", bad_handler)
        self.bus.subscribe("TEST", lambda et, d: results.append("ok"))
        self.bus.publish("TEST", {})
        assert results == ["ok"]

    def test_clear(self):
        self.bus.subscribe("X", lambda et, d: None)
        assert self.bus.subscriber_count == 1
        self.bus.clear()
        assert self.bus.subscriber_count == 0

    def test_string_event_type(self):
        received = []
        self.bus.subscribe("CUSTOM_EVENT", lambda et, d: received.append(d))
        self.bus.publish("CUSTOM_EVENT", "data")
        assert received == ["data"]

    def test_subscriber_count(self):
        self.bus.subscribe(EventType.TRADE_CREATED, lambda et, d: None)
        self.bus.subscribe(EventType.TRADE_EXITED, lambda et, d: None)
        self.bus.subscribe(EventType.TRADE_CREATED, lambda et, d: None)
        assert self.bus.subscriber_count == 3


class TestEventType:
    def test_known_types(self):
        assert EventType.TRADE_CREATED == "TRADE_CREATED"
        assert EventType.TRADE_EXITED == "TRADE_EXITED"
        assert EventType.TRADE_UPDATED == "TRADE_UPDATED"
        assert EventType.ANALYSIS_COMPLETE == "ANALYSIS_COMPLETE"
        assert EventType.T1_HIT == "T1_HIT"
