"""Tests for monitoring/exit_monitor.py — SL/target/soft-SL detection.

Ported from the retired tests/test_premium_monitor.py. The _check_exit logic
is preserved 1:1 from premium_monitor, so these tests exercise the exact
same behaviour with the new class.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from monitoring.exit_monitor import ExitMonitor, ActiveTrade


@dataclass
class MockActiveTrade:
    """Matches ActiveTrade field-for-field; kept as a shim for readability."""
    trade_id: int
    tracker_type: str
    strike: int
    option_type: str
    instrument_token: int
    entry_premium: float
    sl_premium: float
    target_premium: float
    is_selling: bool = False
    soft_sl: float = 0.0
    soft_sl_breached: bool = False
    soft_sl_breach_premium: float = 0.0


def _trade(**kw):
    defaults = dict(
        trade_id=1, tracker_type="rally_rider",
        strike=23000, option_type="CE",
        instrument_token=1000,
        entry_premium=100.0, sl_premium=80.0, target_premium=122.0,
        is_selling=False,
    )
    defaults.update(kw)
    return ActiveTrade(**defaults)


class TestSLTargetDetection:
    def test_buying_sl_hit(self):
        m = ExitMonitor(shadow_mode=True)
        result = m._check_exit(_trade(), current_premium=75.0)
        assert result is not None
        assert result["action"] == "LOST"

    def test_buying_target_hit(self):
        m = ExitMonitor(shadow_mode=True)
        result = m._check_exit(_trade(), current_premium=130.0)
        assert result is not None
        assert result["action"] == "WON"

    def test_selling_sl_hit(self):
        m = ExitMonitor(shadow_mode=True)
        trade = _trade(
            trade_id=2, tracker_type="selling", strike=23000,
            option_type="PE", instrument_token=2000, entry_premium=100.0,
            sl_premium=125.0, target_premium=50.0, is_selling=True,
        )
        result = m._check_exit(trade, current_premium=130.0)
        assert result is not None
        assert result["action"] == "LOST"

    def test_selling_target_hit(self):
        m = ExitMonitor(shadow_mode=True)
        trade = _trade(
            trade_id=2, tracker_type="selling", strike=23000,
            option_type="PE", instrument_token=2000, entry_premium=100.0,
            sl_premium=125.0, target_premium=50.0, is_selling=True,
        )
        result = m._check_exit(trade, current_premium=45.0)
        assert result is not None
        assert result["action"] == "WON"

    def test_no_action_in_range(self):
        m = ExitMonitor(shadow_mode=True)
        result = m._check_exit(_trade(), current_premium=100.0)
        assert result is None

    def test_selling_no_action_in_range(self):
        m = ExitMonitor(shadow_mode=True)
        trade = _trade(
            trade_id=2, tracker_type="selling", option_type="PE",
            instrument_token=2000, entry_premium=100.0,
            sl_premium=125.0, target_premium=50.0, is_selling=True,
        )
        result = m._check_exit(trade, current_premium=90.0)
        assert result is None

    def test_soft_sl_flags_only_does_not_exit(self):
        """Soft SL should mark the trade as breached but NOT return exit."""
        m = ExitMonitor(shadow_mode=True)
        trade = _trade(soft_sl=95.0)
        result = m._check_exit(trade, current_premium=93.0)
        assert result is None
        assert trade.soft_sl_breached is True
        assert trade.soft_sl_breach_premium == 93.0


class TestShadowMode:
    def test_shadow_mode_logs_only(self):
        m = ExitMonitor(shadow_mode=True)
        callback = MagicMock()
        m.set_exit_callback(callback)
        m.register_trade(_trade())
        m.on_tick(1000, {"instrument_token": 1000, "last_price": 75.0})
        callback.assert_not_called()

    def test_live_mode_calls_callback(self):
        m = ExitMonitor(shadow_mode=False)
        callback = MagicMock()
        m.set_exit_callback(callback)
        m.register_trade(_trade())
        m.on_tick(1000, {"instrument_token": 1000, "last_price": 130.0})
        callback.assert_called_once()
        payload = callback.call_args[0][0]
        assert payload["trade_id"] == 1
        assert payload["action"] == "WON"


class TestRegistration:
    def test_register_adds_to_state(self):
        m = ExitMonitor(shadow_mode=True)
        m.register_trade(_trade())
        assert 1000 in m._token_to_trades
        assert len(m._token_to_trades[1000]) == 1
        assert m.is_monitoring(1)

    def test_unregister_removes(self):
        m = ExitMonitor(shadow_mode=True)
        m.register_trade(_trade())
        m.unregister_trade(trade_id=1)
        assert m.is_monitoring(1) is False
        assert m._token_to_trades.get(1000, []) == []

    def test_multiple_trades_same_token(self):
        m = ExitMonitor(shadow_mode=True)
        m.register_trade(_trade(trade_id=1, tracker_type="rally_rider"))
        m.register_trade(_trade(trade_id=2, tracker_type="other"))
        assert len(m._token_to_trades[1000]) == 2

    def test_unregister_one_keeps_other(self):
        m = ExitMonitor(shadow_mode=True)
        m.register_trade(_trade(trade_id=1, tracker_type="rally_rider"))
        m.register_trade(_trade(trade_id=2, tracker_type="other"))
        m.unregister_trade(trade_id=1)
        assert len(m._token_to_trades[1000]) == 1
        assert m.is_monitoring(2) is True

    def test_tick_hub_subscribes_on_register(self):
        hub = MagicMock()
        m = ExitMonitor(tick_hub=hub, shadow_mode=True)
        m.register_trade(_trade())
        hub.request_subscription.assert_called_once_with([1000])

    def test_tick_hub_releases_on_last_unregister(self):
        hub = MagicMock()
        m = ExitMonitor(tick_hub=hub, shadow_mode=True)
        m.register_trade(_trade())
        hub.reset_mock()
        m.unregister_trade(1)
        hub.release_subscription.assert_called_once_with([1000])


class TestSoftSlUpdates:
    def test_update_soft_sl_resets_breached(self):
        m = ExitMonitor(shadow_mode=True)
        trade = _trade(soft_sl=90.0)
        trade.soft_sl_breached = True
        trade.soft_sl_breach_premium = 85.0
        m.register_trade(trade)
        m.update_soft_sl(1, new_soft_sl=92.0)
        assert trade.soft_sl == 92.0
        assert trade.soft_sl_breached is False
        assert trade.soft_sl_breach_premium == 0.0

    def test_get_soft_sl_status(self):
        m = ExitMonitor(shadow_mode=True)
        trade = _trade(soft_sl=90.0)
        m.register_trade(trade)
        status = m.get_soft_sl_status(1)
        assert status == {
            "soft_sl": 90.0,
            "soft_sl_breached": False,
            "soft_sl_breach_premium": 0.0,
        }

    def test_get_soft_sl_status_unknown_trade(self):
        m = ExitMonitor(shadow_mode=True)
        assert m.get_soft_sl_status(999) == {}


class TestScanExistingTrades:
    def test_scan_picks_up_active_trade(self):
        m = ExitMonitor(shadow_mode=True)

        # Mock instrument map
        m._instrument_map = MagicMock()
        m._instrument_map.get_current_expiry.return_value = "2026-02-27"
        mock_inst = {"instrument_token": 5000, "tradingsymbol": "NIFTY2622723000CE"}
        m._instrument_map.get_option_instrument.return_value = mock_inst

        # Mock strategy
        mock_strat = MagicMock()
        mock_strat.tracker_type = "rally_rider"
        mock_strat.is_selling = False
        mock_strat.get_active.return_value = {
            "id": 1, "status": "ACTIVE",
            "strike": 23000, "option_type": "CE",
            "entry_premium": 100.0, "sl_premium": 80.0,
            "target1_premium": 122.0, "target2_premium": None,
        }

        m.scan_existing_trades({"rally_rider": mock_strat})
        assert 5000 in m._token_to_trades


class TestGetStatus:
    def test_empty(self):
        m = ExitMonitor(shadow_mode=True)
        status = m.get_status()
        assert status["shadow_mode"] is True
        assert status["active_trades"] == 0

    def test_with_trade(self):
        m = ExitMonitor(shadow_mode=True)
        m.register_trade(_trade())
        status = m.get_status()
        assert status["active_trades"] == 1
        assert len(status["trades"]) == 1


class TestTickConsumerInterface:
    def test_get_required_tokens_reflects_trades(self):
        m = ExitMonitor(shadow_mode=True)
        m.register_trade(_trade(trade_id=1, instrument_token=100))
        m.register_trade(_trade(trade_id=2, tracker_type="other", instrument_token=200))
        assert m.get_required_tokens() == {100, 200}

    def test_on_tick_skips_unknown_token(self):
        m = ExitMonitor(shadow_mode=False)
        callback = MagicMock()
        m.set_exit_callback(callback)
        # No trades registered — tick should be a no-op
        m.on_tick(999, {"last_price": 100.0})
        callback.assert_not_called()

    def test_on_tick_skips_missing_last_price(self):
        m = ExitMonitor(shadow_mode=False)
        callback = MagicMock()
        m.set_exit_callback(callback)
        m.register_trade(_trade())
        m.on_tick(1000, {"instrument_token": 1000})  # no last_price
        callback.assert_not_called()
