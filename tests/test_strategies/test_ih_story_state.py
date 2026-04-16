"""Test IntradayHunterStrategy.story_state() classification."""

import pytest
from unittest.mock import MagicMock

from analysis.narrative import IHGroupState


def _make_strategy():
    """Build a minimally-viable IH strategy instance for unit testing."""
    from strategies.intraday_hunter import IntradayHunterStrategy
    s = IntradayHunterStrategy.__new__(IntradayHunterStrategy)
    s._cfg = MagicMock(MAX_GROUPS_PER_DAY=1, AGENT_ENABLED=False)
    s._has_open_positions = MagicMock(return_value=False)
    s._fetch_active_positions = MagicMock(return_value=[])
    s._count_signal_groups_today = MagicMock(return_value=0)
    s._day_bias = 0.62
    s._armed_detector = None
    s._alignment = {}
    s._last_closed_group = None
    s._locked_out = False
    return s


def test_story_state_waiting_default():
    s = _make_strategy()
    state = s.story_state()
    assert state.state == IHGroupState.WAITING
    assert state.day_bias == 0.62


def test_story_state_forming_when_detector_armed():
    s = _make_strategy()
    s._armed_detector = "E2"
    s._alignment = {"NIFTY": True, "BANKNIFTY": True, "SENSEX": False}
    state = s.story_state()
    assert state.state == IHGroupState.FORMING
    assert state.detector_armed == "E2"
    assert state.alignment["NIFTY"] is True
    assert state.alignment["SENSEX"] is False


def test_story_state_live_when_positions_open():
    s = _make_strategy()
    s._has_open_positions = MagicMock(return_value=True)
    s._fetch_active_positions = MagicMock(return_value=[
        {
            "id": 1, "signal_group_id": "a3f2b1", "index_label": "NIFTY",
            "strike": 23200, "option_type": "CE", "qty": 65,
            "entry_premium": 142.0, "is_paper": 0,
        },
    ])
    state = s.story_state()
    assert state.state == IHGroupState.LIVE
    assert state.group_id == "a3f2b"  # short id (first 5 chars)
    assert len(state.positions) == 1


def test_story_state_locked_out():
    s = _make_strategy()
    s._locked_out = True
    state = s.story_state()
    assert state.state == IHGroupState.LOCKED_OUT


def test_story_state_recently_closed_with_ago_minutes():
    from datetime import datetime, timedelta
    s = _make_strategy()
    s._last_closed_group = {
        "group_id": "xyz789", "closed_at": datetime.now() - timedelta(minutes=8),
    }
    state = s.story_state()
    assert state.state == IHGroupState.RECENTLY_CLOSED
    assert state.group_id == "xyz789"
    assert state.ago_minutes == 8


def test_ih_emits_group_update_on_open(monkeypatch):
    """When _emit_group_update is called, it pushes ih_group_update to socketio."""
    from unittest.mock import MagicMock

    sio = MagicMock()
    s = _make_strategy()
    s.socketio = sio
    s._emit_group_update()
    sio.emit.assert_called_once()
    args, _kwargs = sio.emit.call_args
    assert args[0] == "ih_group_update"
    payload = args[1]
    assert payload["state"] == "waiting"  # default state from _make_strategy
    assert "positions" in payload


def test_ih_emit_group_update_no_socketio_is_safe():
    """When socketio is None (paper / standalone runs), emit must not crash."""
    s = _make_strategy()
    s.socketio = None
    # Must not raise
    s._emit_group_update()


def test_ih_emit_group_update_includes_full_state():
    """Payload must include state, group_id, positions, agent_verdict, day_bias."""
    from unittest.mock import MagicMock
    sio = MagicMock()
    s = _make_strategy()
    s.socketio = sio
    s._has_open_positions = MagicMock(return_value=True)
    s._fetch_active_positions = MagicMock(return_value=[
        {"id": 1, "signal_group_id": "abc12345", "index_label": "NIFTY",
         "strike": 23200, "option_type": "CE", "qty": 65,
         "entry_premium": 142.0, "is_paper": 0},
    ])
    s._last_agent_verdict = "HOLD"
    s._emit_group_update()
    payload = sio.emit.call_args.args[1]
    assert payload["state"] == "live"
    assert payload["group_id"] == "abc12"  # short id
    assert len(payload["positions"]) == 1
    assert payload["agent_verdict"] == "HOLD"
