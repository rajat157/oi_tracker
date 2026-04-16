"""Test RRStrategy.story_state()."""

from unittest.mock import MagicMock


def _make_strategy():
    from strategies.rr_strategy import RRStrategy
    s = RRStrategy.__new__(RRStrategy)
    s.get_active = MagicMock(return_value=None)
    return s


def test_story_state_waiting_when_no_active_trade():
    s = _make_strategy()
    state = s.story_state()
    assert state.state == "waiting"
    assert state.symbol is None


def test_story_state_live_when_active_trade():
    s = _make_strategy()
    s.get_active = MagicMock(return_value={
        "strike": 23200, "option_type": "CE",
        "entry_premium": 120.0, "current_premium": 140.0,
    })
    state = s.story_state()
    assert state.state == "live"
    assert "23200" in state.symbol
    assert state.entry == 120.0
    assert state.current_premium == 140.0
    assert abs(state.pnl_pct - 16.67) < 0.01
