"""Tests for monitoring/orderflow_collector.py."""

import json
from unittest.mock import MagicMock

import pytest

from monitoring.orderflow_collector import OrderflowCollector


@pytest.fixture
def collector():
    return OrderflowCollector(tick_hub=None)


def _depth(bid_price=100.0, bid_qty=500, ask_price=100.5, ask_qty=400):
    return {
        "buy": [{"price": bid_price, "quantity": bid_qty, "orders": 3}],
        "sell": [{"price": ask_price, "quantity": ask_qty, "orders": 2}],
    }


class TestDepthCaching:
    def test_on_tick_caches_depth(self, collector):
        tick = {"instrument_token": 100, "last_price": 50.0, "depth": _depth()}
        collector.on_tick(100, tick)
        assert 100 in collector._latest_depth
        assert collector._latest_depth[100]["buy"][0]["price"] == 100.0

    def test_on_tick_without_depth_skipped(self, collector):
        collector.on_tick(100, {"instrument_token": 100, "last_price": 50.0})
        assert 100 not in collector._latest_depth

    def test_on_tick_replaces_existing(self, collector):
        collector.on_tick(100, {"depth": _depth(bid_qty=500)})
        collector.on_tick(100, {"depth": _depth(bid_qty=700)})
        assert collector._latest_depth[100]["buy"][0]["quantity"] == 700


class TestCoreStrikeManagement:
    def test_update_core_strikes_subscribes_six(self):
        hub = MagicMock()
        inst_map = MagicMock()
        inst_map.get_current_expiry.return_value = "2026-04-10"
        # Distinct token per (strike, option_type)
        inst_map.get_option_instrument.side_effect = lambda s, ot, e: {
            "instrument_token": s * 10 + (1 if ot == "CE" else 2),
        }
        c = OrderflowCollector(tick_hub=hub)
        c._instrument_map = inst_map
        c.update_core_strikes(spot_price=22500.0)

        assert len(c._core_tokens) == 6
        hub.request_subscription.assert_called()

    def test_update_core_strikes_diff_only_new_tokens(self):
        hub = MagicMock()
        inst_map = MagicMock()
        inst_map.get_current_expiry.return_value = "2026-04-10"
        inst_map.get_option_instrument.side_effect = lambda s, ot, e: {
            "instrument_token": s * 10 + (1 if ot == "CE" else 2),
        }
        c = OrderflowCollector(tick_hub=hub)
        c._instrument_map = inst_map

        c.update_core_strikes(spot_price=22500.0)
        first_call_tokens = set(c._core_tokens.keys())
        hub.reset_mock()

        # Same spot → same strikes → no new subs
        c.update_core_strikes(spot_price=22500.0)
        hub.request_subscription.assert_not_called()
        hub.release_subscription.assert_not_called()

        # Shift spot by 50 → ATM moves, some strikes change
        c.update_core_strikes(spot_price=22550.0)
        assert set(c._core_tokens.keys()) != first_call_tokens

    def test_update_core_skips_active_trade_tokens_from_unsub(self):
        hub = MagicMock()
        inst_map = MagicMock()
        inst_map.get_current_expiry.return_value = "2026-04-10"
        inst_map.get_option_instrument.side_effect = lambda s, ot, e: {
            "instrument_token": s * 10 + (1 if ot == "CE" else 2),
        }
        c = OrderflowCollector(tick_hub=hub)
        c._instrument_map = inst_map
        c.update_core_strikes(spot_price=22500.0)
        old_tokens = set(c._core_tokens.keys())

        hub.reset_mock()
        # Spot shifts far — all old tokens should be released EXCEPT any that
        # overlap with active trade tokens.
        active = {list(old_tokens)[0]}
        c.update_core_strikes(spot_price=23000.0, active_trade_tokens=active)

        # The token we marked as active should NOT have been released
        released = [
            call.args[0] for call in hub.release_subscription.call_args_list
        ]
        flat_released = set()
        for batch in released:
            flat_released.update(batch)
        assert active.isdisjoint(flat_released)


class TestSnapshotCollection:
    def test_empty_when_no_depth(self, collector):
        assert collector.collect_snapshots() == []

    def test_core_strike_snapshot(self, collector):
        collector._core_tokens = {100: {"strike": 22500, "option_type": "CE"}}
        collector._core_spot = 22503.5
        collector._latest_depth[100] = _depth(bid_qty=500, ask_qty=400)
        snaps = collector.collect_snapshots()
        assert len(snaps) == 1
        s = snaps[0]
        assert s["instrument_token"] == 100
        assert s["strike"] == 22500
        assert s["option_type"] == "CE"
        assert s["spot_price"] == 22503.5
        assert s["total_bid_qty"] == 500
        assert s["total_ask_qty"] == 400
        # OBI = 500/400 = 1.25
        assert s["bid_ask_imbalance"] == 1.25
        assert s["best_bid_price"] == 100.0
        # depth_json must be valid JSON
        reloaded = json.loads(s["depth_json"])
        assert "buy" in reloaded

    def test_active_trade_snapshot_wins_over_core(self, collector):
        """If a token is BOTH a core strike AND an active trade, we only emit one record."""
        collector._core_tokens = {100: {"strike": 22500, "option_type": "CE"}}
        collector._latest_depth[100] = _depth()

        trade_mock = MagicMock(strike=22500, option_type="CE")
        snaps = collector.collect_snapshots(
            active_trades_by_token={100: trade_mock}
        )
        assert len(snaps) == 1
        # The returned record came from the active-trades branch
        assert snaps[0]["instrument_token"] == 100
