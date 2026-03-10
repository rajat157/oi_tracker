"""Tests for the alerts/ package (TelegramChannel + AlertBroker)."""

import pytest
from unittest.mock import MagicMock, patch

from alerts.telegram import TelegramChannel
from alerts.broker import AlertBroker
from core.events import EventBus, EventType


class TestTelegramChannel:
    def test_not_configured_returns_false(self):
        ch = TelegramChannel(bot_token="", default_chat_id="123")
        assert ch.is_configured is False
        assert ch.send("hello") is False

    def test_configured(self):
        ch = TelegramChannel(bot_token="abc", default_chat_id="123")
        assert ch.is_configured is True

    @patch("alerts.telegram.requests.post")
    def test_send_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        ch = TelegramChannel(bot_token="tok", default_chat_id="111")
        assert ch.send("msg") is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "tok" in call_kwargs[0][0]  # URL contains token
        assert call_kwargs[1]["json"]["chat_id"] == "111"

    @patch("alerts.telegram.requests.post")
    def test_send_failure(self, mock_post):
        mock_post.return_value = MagicMock(status_code=403)
        ch = TelegramChannel(bot_token="tok", default_chat_id="111")
        assert ch.send("msg") is False

    @patch("alerts.telegram.requests.post")
    def test_send_exception(self, mock_post):
        mock_post.side_effect = Exception("network")
        ch = TelegramChannel(bot_token="tok", default_chat_id="111")
        assert ch.send("msg") is False

    @patch("alerts.telegram.requests.post")
    def test_send_multi(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        ch = TelegramChannel(bot_token="tok", default_chat_id="111")
        assert ch.send_multi("msg", chat_ids=["a", "b"]) is True
        assert mock_post.call_count == 2

    @patch("alerts.telegram.requests.post")
    def test_send_multi_with_extra_bot(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        ch = TelegramChannel(bot_token="tok", default_chat_id="111")
        assert ch.send_multi("msg", chat_ids=["a"],
                             extra_bot_token="tok2",
                             extra_chat_ids=["c", "d"]) is True
        assert mock_post.call_count == 3  # 1 main + 2 extra


class TestAlertBroker:
    def test_subscribes_to_events(self):
        bus = EventBus()
        channel = MagicMock(spec=TelegramChannel)
        AlertBroker(bus=bus, channel=channel)
        # 4 event types subscribed
        assert bus.subscriber_count == 4

    def test_sends_on_trade_created(self):
        bus = EventBus()
        channel = MagicMock(spec=TelegramChannel)
        AlertBroker(bus=bus, channel=channel)

        bus.publish(EventType.TRADE_CREATED, {
            "tracker_type": "momentum",
            "alert_message": "<b>Test Alert</b>",
        })

        channel.send.assert_called_once_with("<b>Test Alert</b>")

    def test_sends_on_trade_exited(self):
        bus = EventBus()
        channel = MagicMock(spec=TelegramChannel)
        AlertBroker(bus=bus, channel=channel)

        bus.publish(EventType.TRADE_EXITED, {
            "tracker_type": "dessert",
            "alert_message": "exit msg",
        })

        channel.send.assert_called_once_with("exit msg")

    def test_no_message_no_send(self):
        bus = EventBus()
        channel = MagicMock(spec=TelegramChannel)
        AlertBroker(bus=bus, channel=channel)

        bus.publish(EventType.TRADE_CREATED, {
            "tracker_type": "momentum",
            # no alert_message
        })

        channel.send.assert_not_called()
        channel.send_multi.assert_not_called()

    def test_non_dict_data_ignored(self):
        bus = EventBus()
        channel = MagicMock(spec=TelegramChannel)
        AlertBroker(bus=bus, channel=channel)

        bus.publish(EventType.TRADE_CREATED, "not a dict")

        channel.send.assert_not_called()

    def test_selling_uses_multi(self):
        bus = EventBus()
        channel = MagicMock(spec=TelegramChannel)
        broker = AlertBroker(bus=bus, channel=channel)
        broker._selling_chat_ids = ["a", "b"]
        broker._selling_extra_bot = "tok2"
        broker._selling_extra_ids = ["c"]

        bus.publish(EventType.TRADE_CREATED, {
            "tracker_type": "selling",
            "alert_message": "sell alert",
        })

        channel.send_multi.assert_called_once_with(
            "sell alert",
            chat_ids=["a", "b"],
            extra_bot_token="tok2",
            extra_chat_ids=["c"],
        )
        channel.send.assert_not_called()

    def test_t1_hit_event(self):
        bus = EventBus()
        channel = MagicMock(spec=TelegramChannel)
        AlertBroker(bus=bus, channel=channel)

        bus.publish(EventType.T1_HIT, {
            "tracker_type": "iron_pulse",
            "alert_message": "T1 hit!",
        })

        channel.send.assert_called_once_with("T1 hit!")
