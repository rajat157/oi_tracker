"""Tests for PremiumMonitorService."""

import pytest

from app.services.premium_monitor_service import ActiveTrade, PremiumMonitorService


@pytest.fixture
def monitor():
    return PremiumMonitorService(shadow_mode=True)


def _make_trade(trade_id=1, entry=100.0, sl=80.0, target=122.0, is_selling=False):
    return ActiveTrade(
        trade_id=trade_id,
        strategy="iron_pulse",
        strike=23500,
        option_type="CE",
        instrument_token=1234,
        entry_premium=entry,
        sl_premium=sl,
        target_premium=target,
        is_selling=is_selling,
    )


class TestPremiumMonitorService:
    def test_register_and_unregister(self, monitor):
        trade = _make_trade()
        monitor.register_trade(trade)
        assert len(monitor._all_trades) == 1
        assert 1234 in monitor._token_to_trades

        monitor.unregister_trade(1)
        assert len(monitor._all_trades) == 0
        assert 1234 not in monitor._token_to_trades

    def test_unregister_nonexistent(self, monitor):
        monitor.unregister_trade(999)  # Should not raise

    def test_check_exit_buying_sl_hit(self):
        trade = _make_trade(entry=100, sl=80, target=122)
        result = PremiumMonitorService._check_exit(trade, 75.0)
        assert result is not None
        assert result["action"] == "LOST"
        assert result["pnl_pct"] < 0

    def test_check_exit_buying_target_hit(self):
        trade = _make_trade(entry=100, sl=80, target=122)
        result = PremiumMonitorService._check_exit(trade, 130.0)
        assert result is not None
        assert result["action"] == "WON"
        assert result["pnl_pct"] > 0

    def test_check_exit_buying_no_exit(self):
        trade = _make_trade(entry=100, sl=80, target=122)
        result = PremiumMonitorService._check_exit(trade, 100.0)
        assert result is None

    def test_check_exit_selling_sl_hit(self):
        trade = _make_trade(entry=100, sl=125, target=75, is_selling=True)
        result = PremiumMonitorService._check_exit(trade, 130.0)
        assert result is not None
        assert result["action"] == "LOST"

    def test_check_exit_selling_target_hit(self):
        trade = _make_trade(entry=100, sl=125, target=75, is_selling=True)
        result = PremiumMonitorService._check_exit(trade, 70.0)
        assert result is not None
        assert result["action"] == "WON"

    def test_check_exit_selling_no_exit(self):
        trade = _make_trade(entry=100, sl=125, target=75, is_selling=True)
        result = PremiumMonitorService._check_exit(trade, 100.0)
        assert result is None

    def test_get_status(self, monitor):
        monitor.register_trade(_make_trade())
        status = monitor.get_status()
        assert status["active_trades"] == 1
        assert status["shadow_mode"] is True
        assert len(status["trades"]) == 1

    def test_exit_callback_called(self):
        monitor = PremiumMonitorService(shadow_mode=False)
        results = []
        monitor.set_exit_callback(lambda r: results.append(r))

        trade = _make_trade(entry=100, sl=80, target=122)
        monitor.register_trade(trade)
        monitor._check_trades(1234, 75.0)  # SL hit

        assert len(results) == 1
        assert results[0]["action"] == "LOST"

    def test_shadow_mode_no_callback(self):
        monitor = PremiumMonitorService(shadow_mode=True)
        results = []
        monitor.set_exit_callback(lambda r: results.append(r))

        trade = _make_trade(entry=100, sl=80, target=122)
        monitor.register_trade(trade)
        monitor._check_trades(1234, 75.0)

        assert len(results) == 0  # Shadow mode — no callback

    def test_multiple_trades_same_token(self, monitor):
        t1 = _make_trade(trade_id=1, entry=100, sl=80, target=122)
        t2 = ActiveTrade(
            trade_id=2, strategy="dessert", strike=23500, option_type="CE",
            instrument_token=1234, entry_premium=90, sl_premium=67.5, target_premium=135,
        )
        monitor.register_trade(t1)
        monitor.register_trade(t2)
        assert len(monitor._token_to_trades[1234]) == 2

        monitor.unregister_trade(1)
        assert len(monitor._token_to_trades[1234]) == 1
