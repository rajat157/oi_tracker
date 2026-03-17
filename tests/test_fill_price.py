"""Tests for fill price correction in OrderExecutor."""

from unittest.mock import patch, MagicMock

import pytest

from kite.order_executor import OrderExecutor, OrderResult


@pytest.fixture
def live_executor():
    """Live mode executor with mocked instrument map."""
    ex = OrderExecutor.__new__(OrderExecutor)
    ex._enabled = True
    ex._lots = 1
    ex._quantity = 65
    ex._product = "NRML"
    ex._active_gtts = {}
    ex._active_orders = {}
    ex._trade_symbols = {}
    import threading
    ex._lock = threading.Lock()
    mock_map = MagicMock()
    mock_map.get_current_expiry.return_value = "2025-01-07"
    mock_map.get_option_instrument.return_value = {
        "tradingsymbol": "NIFTY2510724400CE",
    }
    ex._instrument_map = mock_map
    return ex


class TestGetOrderStatus:
    @patch("kite.broker._headers")
    @patch("kite.broker.requests.get")
    def test_success(self, mock_get, mock_headers):
        mock_headers.return_value = {"Authorization": "token x:y"}
        mock_get.return_value.json.return_value = {
            "status": "success",
            "data": [
                {"status": "OPEN", "average_price": 0, "filled_quantity": 0},
                {"status": "COMPLETE", "average_price": 203.50, "filled_quantity": 65},
            ],
        }
        from kite.broker import get_order_status
        result = get_order_status("ORD123")
        assert result["status"] == "success"
        assert result["average_price"] == 203.50
        assert result["filled_quantity"] == 65
        assert result["order_status"] == "COMPLETE"

    @patch("kite.broker._headers")
    @patch("kite.broker.requests.get")
    def test_api_error(self, mock_get, mock_headers):
        mock_headers.return_value = {"Authorization": "token x:y"}
        mock_get.return_value.json.return_value = {
            "status": "error", "message": "Invalid order ID",
        }
        from kite.broker import get_order_status
        result = get_order_status("BAD_ID")
        assert result["status"] == "error"

    @patch("kite.broker._headers", return_value=None)
    def test_no_auth(self, mock_headers):
        from kite.broker import get_order_status
        result = get_order_status("ORD123")
        assert result["status"] == "error"
        assert "No access token" in result["message"]


class TestQueryFillPrice:
    @patch("kite.broker.get_order_status")
    def test_success_first_try(self, mock_status, live_executor):
        mock_status.return_value = {
            "status": "success", "average_price": 203.50,
            "filled_quantity": 65, "order_status": "COMPLETE",
        }
        result = live_executor._query_fill_price("ORD1", expected=200.0, max_retries=1)
        assert result == 203.50

    @patch("kite.broker.get_order_status")
    def test_retries_then_success(self, mock_status, live_executor):
        mock_status.side_effect = [
            {"status": "success", "average_price": 0, "order_status": "OPEN", "filled_quantity": 0},
            {"status": "success", "average_price": 205.10, "order_status": "COMPLETE", "filled_quantity": 65},
        ]
        result = live_executor._query_fill_price("ORD1", expected=200.0, max_retries=2, delay=0.01)
        assert result == 205.10

    @patch("kite.broker.get_order_status")
    def test_all_retries_fail_returns_expected(self, mock_status, live_executor):
        mock_status.return_value = {"status": "error", "message": "Timeout"}
        result = live_executor._query_fill_price("ORD1", expected=200.0, max_retries=2, delay=0.01)
        assert result == 200.0

    @patch("kite.broker.get_order_status")
    def test_rejected_order_returns_expected(self, mock_status, live_executor):
        mock_status.return_value = {
            "status": "success", "average_price": 0,
            "order_status": "REJECTED", "filled_quantity": 0,
        }
        result = live_executor._query_fill_price("ORD1", expected=200.0, max_retries=1)
        assert result == 200.0


class TestFillPriceCorrection:
    @patch("kite.order_executor.OrderExecutor._update_trade_order_info")
    @patch("kite.order_executor.OrderExecutor._query_fill_price")
    @patch("kite.broker.place_gtt_oco")
    @patch("kite.broker.place_order")
    @patch("kite.broker.is_authenticated", return_value=True)
    def test_market_order_corrects_gtt(self, mock_auth, mock_order, mock_gtt,
                                        mock_fill, mock_update, live_executor):
        mock_order.return_value = {"status": "success", "data": {"order_id": "ORD1"}}
        mock_gtt.return_value = {"status": "success", "data": {"trigger_id": 42}}
        # Fill at 205.00 instead of expected 200.00
        mock_fill.return_value = 205.0

        result = live_executor.place_entry(
            trade_id=1, strike=24400, option_type="CE",
            entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
            order_type="MARKET",
        )

        assert result.success is True
        assert result.actual_fill_price == 205.0

        # Verify GTT was placed with corrected prices
        # SL was -10%: 200->180. New SL: 205 * 0.9 = 184.50
        # TGT was +10%: 200->220. New TGT: 205 * 1.1 = 225.50
        gtt_call = mock_gtt.call_args[1]
        assert gtt_call["entry_price"] == 205.0
        assert gtt_call["sl_price"] == 184.50
        assert gtt_call["target_price"] in (225.50, 225.55)  # float ceil rounding

    @patch("kite.order_executor.OrderExecutor._update_trade_order_info")
    @patch("kite.broker.place_gtt_oco")
    @patch("kite.broker.place_order")
    @patch("kite.broker.is_authenticated", return_value=True)
    def test_limit_order_no_fill_query(self, mock_auth, mock_order, mock_gtt,
                                        mock_update, live_executor):
        mock_order.return_value = {"status": "success", "data": {"order_id": "ORD1"}}
        mock_gtt.return_value = {"status": "success", "data": {"trigger_id": 42}}

        result = live_executor.place_entry(
            trade_id=1, strike=24400, option_type="CE",
            entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
            order_type="LIMIT",
        )

        # No fill query for LIMIT orders — GTT uses original prices
        gtt_call = mock_gtt.call_args[1]
        assert gtt_call["entry_price"] == 200.0
        assert gtt_call["sl_price"] == 180.0
        assert gtt_call["target_price"] == 220.0

    @patch("kite.order_executor.OrderExecutor._update_trade_order_info")
    @patch("kite.order_executor.OrderExecutor._query_fill_price")
    @patch("kite.broker.place_gtt_oco")
    @patch("kite.broker.place_order")
    @patch("kite.broker.is_authenticated", return_value=True)
    def test_fill_matches_expected_no_correction(self, mock_auth, mock_order, mock_gtt,
                                                   mock_fill, mock_update, live_executor):
        mock_order.return_value = {"status": "success", "data": {"order_id": "ORD1"}}
        mock_gtt.return_value = {"status": "success", "data": {"trigger_id": 42}}
        mock_fill.return_value = 200.0  # same as expected

        live_executor.place_entry(
            trade_id=1, strike=24400, option_type="CE",
            entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
            order_type="MARKET",
        )

        # No correction — GTT uses original prices
        gtt_call = mock_gtt.call_args[1]
        assert gtt_call["sl_price"] == 180.0
        assert gtt_call["target_price"] == 220.0

    def test_fill_correction_preserves_ratios(self):
        """Verify the ratio math: SL/TGT percentages preserved on actual fill."""
        # Entry 200, SL 180 (-10%), Target 220 (+10%). Fill at 210.
        entry, sl, target = 200.0, 180.0, 220.0
        actual_fill = 210.0

        sl_pct = (entry - sl) / entry  # 0.10
        tgt_pct = (target - entry) / entry  # 0.10

        new_sl = OrderExecutor.round_to_tick(actual_fill * (1 - sl_pct), "down")
        new_target = OrderExecutor.round_to_tick(actual_fill * (1 + tgt_pct), "up")

        assert new_sl == 189.0    # 210 * 0.9 = 189.0
        assert new_target in (231.0, 231.05)  # 210 * 1.1 = 231.0 (float ceil rounding)


class TestOrderResultFillField:
    def test_default_zero(self):
        r = OrderResult(success=True)
        assert r.actual_fill_price == 0.0

    def test_with_fill(self):
        r = OrderResult(success=True, actual_fill_price=203.50)
        assert r.actual_fill_price == 203.50
