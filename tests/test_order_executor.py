"""Tests for kite/order_executor.py — unified order execution service."""

from unittest.mock import patch, MagicMock

import pytest

from kite.order_executor import OrderExecutor, OrderResult


@pytest.fixture
def executor():
    """Paper mode executor (default)."""
    with patch("kite.order_executor._live_cfg") as mock_cfg:
        mock_cfg.ENABLED = False
        mock_cfg.LOTS = 1
        mock_cfg.quantity = 65
        mock_cfg.PRODUCT = "NRML"
        ex = OrderExecutor()
        ex._enabled = False
        return ex


@pytest.fixture
def live_executor():
    """Live mode executor with mocked instrument map."""
    with patch("kite.order_executor._live_cfg") as mock_cfg:
        mock_cfg.ENABLED = True
        mock_cfg.LOTS = 1
        mock_cfg.quantity = 65
        mock_cfg.PRODUCT = "NRML"
        ex = OrderExecutor()
        ex._enabled = True
        ex._lots = 1
        ex._quantity = 65
        ex._product = "NRML"
        # Mock instrument map
        mock_map = MagicMock()
        mock_map.get_current_expiry.return_value = "2025-01-07"
        mock_map.get_option_instrument.return_value = {
            "tradingsymbol": "NIFTY2510724400CE",
            "instrument_token": 12345,
        }
        ex._instrument_map = mock_map
        return ex


class TestRoundToTick:
    def test_nearest(self):
        assert OrderExecutor.round_to_tick(230.03) == 230.05
        assert OrderExecutor.round_to_tick(230.07) == 230.05
        assert OrderExecutor.round_to_tick(230.12) == 230.10
        assert OrderExecutor.round_to_tick(230.18) == 230.20

    def test_up(self):
        assert OrderExecutor.round_to_tick(230.01, "up") == 230.05
        assert OrderExecutor.round_to_tick(230.05, "up") == 230.05
        assert OrderExecutor.round_to_tick(230.06, "up") == 230.10

    def test_down(self):
        assert OrderExecutor.round_to_tick(230.09, "down") == 230.05
        assert OrderExecutor.round_to_tick(230.05, "down") == 230.05
        assert OrderExecutor.round_to_tick(230.14, "down") == 230.10

    def test_whole_number(self):
        assert OrderExecutor.round_to_tick(200.0) == 200.0

    def test_zero(self):
        assert OrderExecutor.round_to_tick(0.0) == 0.0


class TestPaperMode:
    def test_place_entry_paper(self, executor):
        result = executor.place_entry(1, 24400, "CE", 200.0, 180.0, 220.0)
        assert result.success is True
        assert result.is_paper is True
        assert result.order_id == ""

    def test_modify_sl_paper(self, executor):
        result = executor.modify_sl(1, 190.0, 210.0, 220.0)
        assert result.success is True
        assert result.is_paper is True

    def test_cancel_exit_orders_paper(self, executor):
        result = executor.cancel_exit_orders(1)
        assert result.success is True
        assert result.is_paper is True

    def test_place_exit_paper(self, executor):
        result = executor.place_exit(1, 24400, "CE")
        assert result.success is True
        assert result.is_paper is True

    def test_is_live_false(self, executor):
        assert executor.is_live is False


class TestLiveEntrySuccess:
    @patch("kite.order_executor.OrderExecutor._update_trade_order_info")
    @patch("kite.broker.place_gtt_oco")
    @patch("kite.broker.place_order")
    @patch("kite.broker.is_authenticated", return_value=True)
    def test_entry_and_gtt(self, mock_auth, mock_order, mock_gtt, mock_update,
                           live_executor):
        mock_order.return_value = {
            "status": "success",
            "data": {"order_id": "ORD123"},
        }
        mock_gtt.return_value = {
            "status": "success",
            "data": {"trigger_id": 456},
        }

        result = live_executor.place_entry(
            trade_id=1, strike=24400, option_type="CE",
            entry_premium=200.03, sl_premium=180.07, target_premium=220.01,
        )

        assert result.success is True
        assert result.is_paper is False
        assert result.order_id == "ORD123"
        assert result.gtt_trigger_id == 456

        # Verify tick rounding: entry=nearest, sl=down, target=up
        order_call = mock_order.call_args
        assert order_call[1]["transaction_type"] == "BUY"
        assert order_call[1]["quantity"] == 65
        assert order_call[1]["order_type"] == "MARKET"

        gtt_call = mock_gtt.call_args
        assert gtt_call[1]["sl_price"] == 180.05      # 180.07 rounded down
        assert gtt_call[1]["target_price"] == 220.05   # 220.01 rounded up
        assert gtt_call[1]["entry_price"] == 200.05    # 200.03 rounded nearest

        # Verify internal state
        assert live_executor._active_orders[1] == "ORD123"
        assert live_executor._active_gtts[1] == 456

    @patch("kite.order_executor.OrderExecutor._update_trade_order_info")
    @patch("kite.broker.place_gtt_oco")
    @patch("kite.broker.place_order")
    @patch("kite.broker.is_authenticated", return_value=True)
    def test_limit_order(self, mock_auth, mock_order, mock_gtt, mock_update,
                         live_executor):
        mock_order.return_value = {
            "status": "success", "data": {"order_id": "ORD456"}}
        mock_gtt.return_value = {
            "status": "success", "data": {"trigger_id": 789}}

        live_executor.place_entry(
            trade_id=2, strike=24400, option_type="CE",
            entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
            order_type="LIMIT",
        )

        order_call = mock_order.call_args
        assert order_call[1]["order_type"] == "LIMIT"
        assert order_call[1]["price"] == 200.0


class TestLiveEntryFailure:
    @patch("kite.broker.place_order")
    @patch("kite.broker.is_authenticated", return_value=True)
    def test_entry_order_fails(self, mock_auth, mock_order, live_executor):
        mock_order.return_value = {"status": "error", "message": "Insufficient margin"}

        result = live_executor.place_entry(
            trade_id=1, strike=24400, option_type="CE",
            entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
        )

        assert result.success is False
        assert "Insufficient margin" in result.error
        assert 1 not in live_executor._active_orders

    @patch("kite.order_executor.OrderExecutor._update_trade_order_info")
    @patch("kite.broker.place_gtt_oco")
    @patch("kite.broker.place_order")
    @patch("kite.broker.is_authenticated", return_value=True)
    def test_gtt_fails_entry_succeeds(self, mock_auth, mock_order, mock_gtt,
                                       mock_update, live_executor):
        mock_order.return_value = {
            "status": "success", "data": {"order_id": "ORD123"}}
        mock_gtt.return_value = {"status": "error", "message": "GTT limit reached"}

        result = live_executor.place_entry(
            trade_id=1, strike=24400, option_type="CE",
            entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
        )

        assert result.success is True
        assert result.order_id == "ORD123"
        assert result.gtt_trigger_id == 0
        assert 1 not in live_executor._active_gtts

    @patch("kite.broker.is_authenticated", return_value=False)
    def test_not_authenticated(self, mock_auth, live_executor):
        result = live_executor.place_entry(
            trade_id=1, strike=24400, option_type="CE",
            entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
        )
        assert result.success is False
        assert "Not authenticated" in result.error

    def test_no_instrument_map(self, live_executor):
        live_executor._instrument_map = None
        with patch("kite.broker.is_authenticated", return_value=True):
            result = live_executor.place_entry(
                trade_id=1, strike=24400, option_type="CE",
                entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
            )
        assert result.success is False
        assert "Symbol not found" in result.error


class TestModifySL:
    @patch("kite.broker.modify_gtt")
    def test_modify_success(self, mock_modify, live_executor):
        live_executor._active_gtts[1] = 456
        live_executor._trade_symbols[1] = "NIFTY2510724400CE"
        mock_modify.return_value = {"status": "success"}

        result = live_executor.modify_sl(1, 190.03, 210.0, 220.0)

        assert result.success is True
        call_kw = mock_modify.call_args[1]
        assert call_kw["new_sl_price"] == 190.0  # 190.03 rounded down
        assert call_kw["target_price"] == 220.0

    def test_no_gtt_to_modify(self, live_executor):
        result = live_executor.modify_sl(99, 190.0, 210.0, 220.0)
        assert result.success is True
        assert result.is_paper is True  # no-op


class TestCancelExitOrders:
    @patch("kite.broker.delete_gtt")
    def test_cancel_success(self, mock_delete, live_executor):
        live_executor._active_gtts[1] = 456
        live_executor._active_orders[1] = "ORD123"
        live_executor._trade_symbols[1] = "SYM"
        mock_delete.return_value = {"status": "success"}

        result = live_executor.cancel_exit_orders(1)

        assert result.success is True
        mock_delete.assert_called_once_with(456)
        assert 1 not in live_executor._active_gtts
        assert 1 not in live_executor._active_orders

    def test_cancel_no_gtt(self, live_executor):
        result = live_executor.cancel_exit_orders(99)
        assert result.success is True  # idempotent


class TestPlaceExit:
    @patch("kite.broker.place_order")
    @patch("kite.broker.is_authenticated", return_value=True)
    def test_exit_success(self, mock_auth, mock_order, live_executor):
        mock_order.return_value = {
            "status": "success", "data": {"order_id": "EXIT123"}}

        result = live_executor.place_exit(1, 24400, "CE")

        assert result.success is True
        assert result.order_id == "EXIT123"
        call_kw = mock_order.call_args[1]
        assert call_kw["transaction_type"] == "SELL"
        assert call_kw["order_type"] == "MARKET"


class TestOrderResult:
    def test_defaults(self):
        r = OrderResult(success=True)
        assert r.order_id == ""
        assert r.gtt_trigger_id == 0
        assert r.error == ""
        assert r.is_paper is True

    def test_live_result(self):
        r = OrderResult(success=True, order_id="ORD1",
                        gtt_trigger_id=42, is_paper=False)
        assert r.order_id == "ORD1"
        assert r.gtt_trigger_id == 42
        assert r.is_paper is False


class TestSymbolResolution:
    def test_resolves_correctly(self, live_executor):
        sym = live_executor._resolve_symbol(24400, "CE")
        assert sym == "NIFTY2510724400CE"
        live_executor._instrument_map.get_option_instrument.assert_called_once()

    def test_no_instrument_map(self, live_executor):
        live_executor._instrument_map = None
        assert live_executor._resolve_symbol(24400, "CE") is None

    def test_no_expiry(self, live_executor):
        live_executor._instrument_map.get_current_expiry.return_value = None
        assert live_executor._resolve_symbol(24400, "CE") is None
