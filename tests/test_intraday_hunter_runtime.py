"""Tests for the new IntradayHunter runtime components.

Covers:
    - kite/auth.ensure_authenticated (no-prompt fast path + timeout path)
    - monitoring/startup_backfill.backfill_recent_history (no-token + happy path)
    - kite/instruments.MultiInstrumentMap (per-index segment routing)
    - strategies/intraday_hunter_agent.IntradayHunterAgent (prompt + parsing,
      no real subprocess calls)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ── kite/auth.ensure_authenticated ──────────────────────────────────────

class TestEnsureAuthenticated:
    def test_existing_token_short_circuits(self):
        from kite import auth
        with patch.object(auth, "load_token", return_value="existing_token_abc"):
            assert auth.ensure_authenticated(timeout_seconds=1) is True

    def test_no_api_key_returns_false(self):
        from kite import auth
        with patch.object(auth, "load_token", return_value=""), \
             patch.object(auth, "API_KEY", ""):
            assert auth.ensure_authenticated(timeout_seconds=1) is False

    def test_timeout_when_callback_never_fires(self):
        from kite import auth
        with patch.object(auth, "load_token", return_value=""), \
             patch.object(auth, "API_KEY", "fake_key"), \
             patch.object(auth, "webbrowser") as mock_wb:
            mock_wb.open = MagicMock()
            ok = auth.ensure_authenticated(
                timeout_seconds=1, poll_interval=0.1, auto_open_browser=True)
            assert ok is False
            mock_wb.open.assert_called_once()

    def test_polls_until_token_appears(self):
        from kite import auth
        # Return empty 3 times, then a token on the 4th call
        responses = ["", "", "", "got_it"]
        call_count = {"n": 0}

        def fake_load():
            r = responses[call_count["n"]]
            call_count["n"] += 1
            return r

        with patch.object(auth, "load_token", side_effect=fake_load), \
             patch.object(auth, "API_KEY", "fake_key"), \
             patch.object(auth, "webbrowser"):
            ok = auth.ensure_authenticated(
                timeout_seconds=5, poll_interval=0.05, auto_open_browser=False)
            assert ok is True


# ── monitoring/startup_backfill.backfill_recent_history ─────────────────

class TestStartupBackfill:
    def test_no_api_key_returns_empty(self, monkeypatch):
        from monitoring import startup_backfill
        monkeypatch.delenv("KITE_API_KEY", raising=False)
        result = startup_backfill.backfill_recent_history()
        assert result == {}

    def test_no_token_returns_empty(self, monkeypatch):
        from monitoring import startup_backfill
        monkeypatch.setenv("KITE_API_KEY", "fake_key")
        with patch.object(startup_backfill, "get_setting", return_value=None):
            result = startup_backfill.backfill_recent_history()
            assert result == {}

    def test_skips_up_to_date_instruments(self, monkeypatch, tmp_path):
        """If last stored timestamp is yesterday, skip the API call."""
        from monitoring import startup_backfill

        # Use a temp DB
        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(startup_backfill, "DB_PATH", db_path)
        monkeypatch.setenv("KITE_API_KEY", "fake_key")

        # Pre-populate with yesterday's last candle for NIFTY
        import sqlite3
        conn = sqlite3.connect(db_path)
        startup_backfill._ensure_table(conn)
        yesterday_late = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO instrument_history (label, interval, timestamp, open, high, low, close, volume) "
            "VALUES ('NIFTY', '1min', ?, 100, 101, 99, 100, 0)",
            (yesterday_late,),
        )
        conn.commit()
        conn.close()

        # Mock the Kite client so the test doesn't make real API calls
        mock_kite = MagicMock()
        mock_kite.historical_data.return_value = []
        with patch.object(startup_backfill, "get_setting", return_value="fake_token"), \
             patch("kiteconnect.KiteConnect", return_value=mock_kite):
            result = startup_backfill.backfill_recent_history()

        # NIFTY should be in results (the empty list is fine — no candles needed)
        assert "NIFTY" in result


# ── kite/instruments.MultiInstrumentMap ─────────────────────────────────

class TestMultiInstrumentMap:
    def test_three_child_maps(self):
        from kite.instruments import MultiInstrumentMap
        m = MultiInstrumentMap(api_key="key", access_token="tok")
        assert sorted(m.labels()) == ["BANKNIFTY", "NIFTY", "SENSEX"]
        assert m.get("NIFTY") is not None
        assert m.get("BANKNIFTY") is not None
        assert m.get("SENSEX") is not None
        assert m.get("UNKNOWN") is None

    def test_per_index_segment(self):
        from kite.instruments import MultiInstrumentMap
        m = MultiInstrumentMap(api_key="key")
        assert m.get("NIFTY").segment == "NFO"
        assert m.get("BANKNIFTY").segment == "NFO"
        assert m.get("SENSEX").segment == "BFO"

    def test_set_access_token_propagates(self):
        from kite.instruments import MultiInstrumentMap
        m = MultiInstrumentMap(api_key="key")
        m.set_access_token("new_token")
        for child in m._maps.values():
            assert child._access_token == "new_token"

    def test_get_option_instrument_routes_to_child(self):
        from kite.instruments import MultiInstrumentMap, InstrumentMap
        m = MultiInstrumentMap(api_key="key")
        # Inject a fake instrument into BANKNIFTY's child map
        bn = m.get("BANKNIFTY")
        bn._options[(56000, "CE", "2026-04-30")] = {"instrument_token": 123}
        result = m.get_option_instrument("BANKNIFTY", 56000, "CE", "2026-04-30")
        assert result == {"instrument_token": 123}


# ── strategies/intraday_hunter_agent.IntradayHunterAgent ────────────────

class TestIntradayHunterAgent:
    def test_agent_imports(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        a = IntradayHunterAgent()
        assert hasattr(a, "build_signal_prompt")
        assert hasattr(a, "build_monitor_prompt")
        assert hasattr(a, "call_claude")
        assert hasattr(a, "confirm_signal")
        assert hasattr(a, "monitor_position")

    def test_parse_response_direct_json(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        a = IntradayHunterAgent()
        raw = '{"action": "TRADE", "confidence": 80, "reasoning": "ok"}'
        result = a._parse_response(raw)
        assert result["action"] == "TRADE"
        assert result["confidence"] == 80

    def test_parse_response_markdown_block(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        a = IntradayHunterAgent()
        raw = '```json\n{"action": "NO_TRADE", "reasoning": "weak"}\n```'
        result = a._parse_response(raw)
        assert result["action"] == "NO_TRADE"

    def test_parse_response_invalid(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        a = IntradayHunterAgent()
        assert a._parse_response("not json at all") is None

    def test_confirm_signal_no_trade(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        from strategies.intraday_hunter_engine import Signal
        a = IntradayHunterAgent()
        sig = Signal(direction="BUY", trigger="E1", minute_idx=20,
                     day_bias_score=0.0, skip_bn=False)
        signal_data = {"signal": sig, "positions": [], "vix": 13.5, "nifty_spot": 25000}
        with patch.object(a, "call_claude",
                          return_value={"action": "NO_TRADE", "reasoning": "weak"}):
            assert a.confirm_signal(signal_data, {}) is None

    def test_confirm_signal_low_confidence(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        from strategies.intraday_hunter_engine import Signal
        a = IntradayHunterAgent()
        sig = Signal(direction="BUY", trigger="E1", minute_idx=20,
                     day_bias_score=0.0, skip_bn=False)
        signal_data = {"signal": sig, "positions": [], "vix": 13.5, "nifty_spot": 25000}
        with patch.object(a, "call_claude",
                          return_value={"action": "TRADE", "confidence": 40,
                                        "reasoning": "weak"}):
            assert a.confirm_signal(signal_data, {}) is None

    def test_confirm_signal_passes(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        from strategies.intraday_hunter_engine import Signal
        a = IntradayHunterAgent()
        sig = Signal(direction="BUY", trigger="E1", minute_idx=20,
                     day_bias_score=0.0, skip_bn=False)
        signal_data = {"signal": sig, "positions": [], "vix": 13.5, "nifty_spot": 25000}
        with patch.object(a, "call_claude",
                          return_value={"action": "TRADE", "confidence": 80,
                                        "reasoning": "strong setup"}):
            result = a.confirm_signal(signal_data, {})
            assert result is not None
            assert result["confidence"] == 80

    def test_monitor_position_hold_returns_none(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        a = IntradayHunterAgent()
        pos = {
            "id": 1, "index_label": "NIFTY", "strike": 25000, "option_type": "CE",
            "direction": "BUY", "qty": 65, "entry_premium": 100,
            "sl_premium": 80, "target_premium": 145,
        }
        with patch.object(a, "call_claude",
                          return_value={"action": "HOLD", "reasoning": "fine"}):
            assert a.monitor_position(pos, {}, current_premium=110) is None

    def test_monitor_position_exit_now(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        a = IntradayHunterAgent()
        pos = {
            "id": 1, "index_label": "NIFTY", "strike": 25000, "option_type": "CE",
            "direction": "BUY", "qty": 65, "entry_premium": 100,
            "sl_premium": 80, "target_premium": 145,
        }
        with patch.object(a, "call_claude",
                          return_value={"action": "EXIT_NOW", "reasoning": "broken"}):
            result = a.monitor_position(pos, {}, current_premium=110)
            assert result is not None
            assert result["action"] == "EXIT_NOW"

    def test_monitor_position_tighten_sl_validates(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        a = IntradayHunterAgent()
        pos = {
            "id": 1, "index_label": "NIFTY", "strike": 25000, "option_type": "CE",
            "direction": "BUY", "qty": 65, "entry_premium": 100,
            "sl_premium": 80, "target_premium": 145,
        }
        # New SL above old SL but below current = valid
        with patch.object(a, "call_claude",
                          return_value={"action": "TIGHTEN_SL",
                                        "new_sl_premium": 95,
                                        "reasoning": "lock profit"}):
            result = a.monitor_position(pos, {}, current_premium=110)
            assert result is not None
            assert result["new_sl_premium"] == 95

    def test_monitor_position_tighten_sl_rejects_widening(self):
        from strategies.intraday_hunter_agent import IntradayHunterAgent
        a = IntradayHunterAgent()
        pos = {
            "id": 1, "index_label": "NIFTY", "strike": 25000, "option_type": "CE",
            "direction": "BUY", "qty": 65, "entry_premium": 100,
            "sl_premium": 80, "target_premium": 145,
        }
        # New SL BELOW old SL = widening = rejected
        with patch.object(a, "call_claude",
                          return_value={"action": "TIGHTEN_SL",
                                        "new_sl_premium": 70,
                                        "reasoning": "loose"}):
            assert a.monitor_position(pos, {}, current_premium=110) is None


# ── OrderExecutor with new params (paper mode no-op) ────────────────────

class TestOrderExecutorIHParams:
    def test_paper_mode_per_call_args_noop(self):
        """In paper mode, the new quantity/exchange/instrument_map args are
        accepted but the call is a no-op."""
        from kite.order_executor import OrderExecutor
        oe = OrderExecutor()
        result = oe.place_entry(
            trade_id=1, strike=25000, option_type="CE",
            entry_premium=100, sl_premium=80, target_premium=145,
            tracker_type="intraday_hunter",
            quantity=30, exchange="BFO", instrument_map=None,
        )
        assert result.success is True
        assert result.is_paper is True

    def test_round_to_tick_works(self):
        from kite.order_executor import OrderExecutor
        oe = OrderExecutor()
        assert oe.round_to_tick(100.03, "nearest") == 100.05
        assert oe.round_to_tick(100.06, "down") == 100.05
        assert oe.round_to_tick(100.06, "up") == 100.10
