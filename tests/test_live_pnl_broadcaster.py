"""Tests for monitoring/live_pnl_broadcaster.py."""

from unittest.mock import MagicMock

import pytest

from monitoring.exit_monitor import ExitMonitor, ActiveTrade
from monitoring.live_pnl_broadcaster import LivePnlBroadcaster


def _trade(trade_id=1, tracker="rally_rider", strike=22500, option_type="CE",
           token=12345, entry=100.0, sl=90.0, target=120.0, is_selling=False):
    return ActiveTrade(
        trade_id=trade_id, tracker_type=tracker, strike=strike,
        option_type=option_type, instrument_token=token,
        entry_premium=entry, sl_premium=sl, target_premium=target,
        is_selling=is_selling,
    )


@pytest.fixture
def exit_mon():
    em = ExitMonitor(tick_hub=None)
    return em


@pytest.fixture
def broadcaster(exit_mon):
    return LivePnlBroadcaster(exit_monitor=exit_mon)


class TestLtpCache:
    def test_caches_last_price_from_tick(self, broadcaster):
        broadcaster.on_tick(100, {"instrument_token": 100, "last_price": 50.0})
        assert broadcaster.get_ltp(100) == 50.0

    def test_tick_without_price_skipped(self, broadcaster):
        broadcaster.on_tick(100, {"instrument_token": 100})  # no last_price
        assert broadcaster.get_ltp(100) == 0.0

    def test_later_tick_overwrites(self, broadcaster):
        broadcaster.on_tick(100, {"last_price": 50.0})
        broadcaster.on_tick(100, {"last_price": 55.0})
        assert broadcaster.get_ltp(100) == 55.0

    def test_passive_consumer_requires_no_tokens(self, broadcaster):
        assert broadcaster.get_required_tokens() == set()


class TestPnlPayload:
    def test_empty_when_no_trades(self, broadcaster):
        assert broadcaster.get_pnl_payload() == {}

    def test_empty_when_no_ltp(self, broadcaster, exit_mon):
        exit_mon._all_trades[1] = _trade()
        assert broadcaster.get_pnl_payload() == {}

    def test_buying_trade_pnl_positive(self, broadcaster, exit_mon):
        trade = _trade(entry=100.0, token=12345)
        exit_mon._all_trades[1] = trade
        broadcaster.on_tick(12345, {"last_price": 110.0})
        payload = broadcaster.get_pnl_payload()
        assert "rally_rider" in payload
        rr = payload["rally_rider"]
        assert rr["current_premium"] == 110.0
        assert rr["pnl_pct"] == 10.0    # (110-100)/100 * 100
        assert rr["pnl_points"] == 10.0
        assert rr["strike"] == 22500
        assert rr["option_type"] == "CE"
        assert rr["entry_premium"] == 100.0

    def test_buying_trade_pnl_negative(self, broadcaster, exit_mon):
        exit_mon._all_trades[1] = _trade(entry=100.0, token=12345)
        broadcaster.on_tick(12345, {"last_price": 95.0})
        rr = broadcaster.get_pnl_payload()["rally_rider"]
        assert rr["pnl_pct"] == -5.0
        assert rr["pnl_points"] == -5.0

    def test_selling_trade_pnl_inverted(self, broadcaster, exit_mon):
        # For a seller, premium falling is profit
        exit_mon._all_trades[1] = _trade(
            entry=100.0, token=12345, is_selling=True,
        )
        broadcaster.on_tick(12345, {"last_price": 90.0})
        rr = broadcaster.get_pnl_payload()["rally_rider"]
        assert rr["pnl_pct"] == 10.0  # (100-90)/100 * 100
        assert rr["pnl_points"] == 10.0

    def test_multiple_trackers(self, broadcaster, exit_mon):
        exit_mon._all_trades[1] = _trade(
            tracker="rally_rider", token=100, entry=100.0,
        )
        exit_mon._all_trades[2] = _trade(
            trade_id=2, tracker="other_tracker", token=200, entry=50.0,
        )
        broadcaster.on_tick(100, {"last_price": 110.0})
        broadcaster.on_tick(200, {"last_price": 55.0})
        payload = broadcaster.get_pnl_payload()
        assert set(payload.keys()) == {"rally_rider", "other_tracker"}
        assert payload["rally_rider"]["pnl_pct"] == 10.0
        assert payload["other_tracker"]["pnl_pct"] == 10.0

    def test_trade_without_cached_ltp_skipped(self, broadcaster, exit_mon):
        exit_mon._all_trades[1] = _trade(token=100)
        exit_mon._all_trades[2] = _trade(trade_id=2, tracker="two", token=200)
        broadcaster.on_tick(100, {"last_price": 110.0})
        # Only token 100 has a cached LTP
        payload = broadcaster.get_pnl_payload()
        assert list(payload.keys()) == ["rally_rider"]
