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
