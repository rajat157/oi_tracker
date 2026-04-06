"""Tests for Claude active trade monitoring."""

from datetime import datetime, time
from unittest.mock import patch, MagicMock

import pytest

from strategies.trade_monitor import (
    build_monitor_prompt, validate_monitor_response, MONITOR_SYSTEM_PROMPT,
)


class TestBuildMonitorPrompt:
    def test_contains_trade_context(self):
        trade_ctx = {
            "trade_id": 1, "entry_premium": 200.0, "current_premium": 210.0,
            "pnl_pct": 5.0, "sl_premium": 180.0, "target_premium": 220.0,
            "trail_stage": 0, "option_type": "CE", "strike": 24400,
            "direction": "BUY_CE", "time_in_trade_min": 15,
            "max_premium_reached": 212.0,
        }
        analysis_ctx = {"spot_price": 24500.0, "vix": 12.5, "verdict": "Bullish"}

        prompt = build_monitor_prompt("## Chart data here", trade_ctx, analysis_ctx)

        assert "24400 CE" in prompt
        assert "Rs 200.00" in prompt
        assert "Rs 210.00" in prompt
        assert "+5.00%" in prompt
        assert "Rs 180.00" in prompt
        assert "15 min" in prompt
        assert "24500.00" in prompt
        assert "12.5" in prompt
        assert "Bullish" in prompt
        assert "Chart data here" in prompt

    def test_contains_system_prompt(self):
        prompt = build_monitor_prompt("chart", {
            "entry_premium": 100, "current_premium": 110, "pnl_pct": 10,
            "sl_premium": 90, "target_premium": 120, "trail_stage": 0,
            "option_type": "CE", "strike": 24400, "direction": "BUY_CE",
            "time_in_trade_min": 10, "max_premium_reached": 115,
        }, {"spot_price": 24500, "vix": 12, "verdict": "N/A"})
        assert "HOLD" in prompt
        assert "TIGHTEN_SL" in prompt
        assert "EXIT_NOW" in prompt


class TestValidateMonitorResponse:
    def _ctx(self, **kw):
        d = {"sl_premium": 180.0, "current_premium": 210.0}
        d.update(kw)
        return d

    def test_hold_valid(self):
        assert validate_monitor_response({"action": "HOLD"}, self._ctx()) is True

    def test_exit_now_valid(self):
        assert validate_monitor_response(
            {"action": "EXIT_NOW", "reasoning": "Broke support"}, self._ctx()) is True

    def test_tighten_sl_valid(self):
        assert validate_monitor_response(
            {"action": "TIGHTEN_SL", "new_sl_premium": 195.0}, self._ctx()) is True

    def test_tighten_sl_below_current_sl_rejected(self):
        # New SL must be above current SL
        assert validate_monitor_response(
            {"action": "TIGHTEN_SL", "new_sl_premium": 175.0}, self._ctx()) is False

    def test_tighten_sl_above_current_premium_rejected(self):
        # New SL can't be above current market price
        assert validate_monitor_response(
            {"action": "TIGHTEN_SL", "new_sl_premium": 215.0}, self._ctx()) is False

    def test_tighten_sl_missing_premium_rejected(self):
        assert validate_monitor_response(
            {"action": "TIGHTEN_SL"}, self._ctx()) is False

    def test_tighten_sl_non_numeric_rejected(self):
        assert validate_monitor_response(
            {"action": "TIGHTEN_SL", "new_sl_premium": "high"}, self._ctx()) is False

    def test_invalid_action_rejected(self):
        assert validate_monitor_response(
            {"action": "BUY_MORE"}, self._ctx()) is False

    def test_empty_action_rejected(self):
        assert validate_monitor_response({}, self._ctx()) is False


class TestRRAgentMonitorFull:
    @patch("strategies.rr_agent.RRAgent.call_claude")
    def test_tighten_sl_returned(self, mock_claude):
        from strategies.rr_agent import RRAgent
        agent = RRAgent()
        mock_claude.return_value = {
            "action": "TIGHTEN_SL", "new_sl_premium": 195.0,
            "reasoning": "Lower highs forming",
        }
        result = agent.monitor_active_trade(
            "chart", {"pnl_pct": 5, "sl_premium": 180, "current_premium": 210}, {})
        assert result is not None
        assert result["action"] == "TIGHTEN_SL"
        assert result["new_sl_premium"] == 195.0

    @patch("strategies.rr_agent.RRAgent.call_claude")
    def test_exit_now_returned(self, mock_claude):
        from strategies.rr_agent import RRAgent
        agent = RRAgent()
        mock_claude.return_value = {
            "action": "EXIT_NOW", "reasoning": "Broke VWAP with volume",
        }
        result = agent.monitor_active_trade(
            "chart", {"pnl_pct": -3, "sl_premium": 180, "current_premium": 195}, {})
        assert result is not None
        assert result["action"] == "EXIT_NOW"

    @patch("strategies.rr_agent.RRAgent.call_claude")
    def test_timeout_returns_none(self, mock_claude):
        from strategies.rr_agent import RRAgent
        agent = RRAgent()
        mock_claude.return_value = None
        result = agent.monitor_active_trade(
            "chart", {"pnl_pct": 5, "sl_premium": 180, "current_premium": 210}, {})
        assert result is None

    @patch("strategies.rr_agent.RRAgent.call_claude")
    def test_invalid_response_returns_none(self, mock_claude):
        from strategies.rr_agent import RRAgent
        agent = RRAgent()
        mock_claude.return_value = {"action": "TIGHTEN_SL", "new_sl_premium": 170.0}
        # new_sl_premium < current sl_premium → invalid
        result = agent.monitor_active_trade(
            "chart", {"pnl_pct": 5, "sl_premium": 180, "current_premium": 210}, {})
        assert result is None


class TestRRAgentMonitor:
    @patch("strategies.rr_agent.RRAgent.call_claude")
    def test_hold_returns_none(self, mock_claude):
        from strategies.rr_agent import RRAgent
        agent = RRAgent()
        mock_claude.return_value = {"action": "HOLD", "reasoning": "OK"}
        result = agent.monitor_active_trade(
            "chart", {"pnl_pct": 5, "sl_premium": 180, "current_premium": 210}, {})
        assert result is None


class TestExitMonitorSLUpdate:
    def test_update_trade_sl(self):
        from monitoring.exit_monitor import ExitMonitor, ActiveTrade
        monitor = ExitMonitor()
        trade = ActiveTrade(
            trade_id=1, tracker_type="scalper", strike=24400,
            option_type="CE", instrument_token=12345,
            entry_premium=200.0, sl_premium=180.0, target_premium=220.0,
        )
        monitor._all_trades[1] = trade

        monitor.update_trade_sl(1, 195.0)

        assert monitor._all_trades[1].sl_premium == 195.0

    def test_update_nonexistent_trade(self):
        from monitoring.exit_monitor import ExitMonitor
        monitor = ExitMonitor()
        # Should not raise
        monitor.update_trade_sl(99, 195.0)
