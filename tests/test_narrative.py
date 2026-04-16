"""Unit tests for analysis/narrative.py."""

import pytest
from analysis.narrative import Story, Warning, Severity


def test_story_with_sentences_is_valid():
    s = Story(sentences=["Market is drifting up.", "Put sellers below 23100."], warning=None)
    assert s.sentences == ["Market is drifting up.", "Put sellers below 23100."]
    assert s.warning is None
    assert s.has_content()


def test_story_with_warning_is_valid():
    w = Warning(
        code="KITE_TOKEN_EXPIRED",
        message="Kite login expired.",
        action_label="Re-authenticate",
        action_url="/auth/kite",
        severity=Severity.ERROR,
    )
    s = Story(sentences=[], warning=w)
    assert not s.has_content()
    assert s.warning.code == "KITE_TOKEN_EXPIRED"


def test_warning_without_action_is_valid():
    w = Warning(code="STALE_DATA", message="Last update 8m ago.", severity=Severity.WARN)
    assert w.action_label is None
    assert w.action_url is None


def test_severity_values():
    assert Severity.INFO.value == "info"
    assert Severity.WARN.value == "warn"
    assert Severity.ERROR.value == "error"


from analysis.narrative import (
    IHStoryState, RRStoryState, IHGroupState, Mood, classify_mood,
)


def test_ih_group_state_enum():
    assert IHGroupState.WAITING.value == "waiting"
    assert IHGroupState.FORMING.value == "forming"
    assert IHGroupState.LIVE.value == "live"
    assert IHGroupState.RECENTLY_CLOSED.value == "recently_closed"
    assert IHGroupState.LOCKED_OUT.value == "locked_out"


def test_ih_story_state_waiting():
    s = IHStoryState(state=IHGroupState.WAITING)
    assert s.state == IHGroupState.WAITING
    assert s.positions == []
    assert s.day_bias is None


def test_ih_story_state_live_with_positions():
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 142.0, "current_premium": 154.0, "is_paper": False},
        {"index": "BANKNIFTY", "strike": 50400, "option_type": "CE",
         "entry_premium": 210.0, "current_premium": 225.0, "is_paper": True},
    ]
    s = IHStoryState(
        state=IHGroupState.LIVE, group_id="a3f2", positions=positions,
        agent_verdict="HOLD", day_bias=0.62,
    )
    assert s.state == IHGroupState.LIVE
    assert len(s.positions) == 2
    assert s.agent_verdict == "HOLD"


def test_rr_story_state():
    s = RRStoryState(state="live", symbol="NIFTY 23200 CE", entry=120.0, pnl_pct=8.1)
    assert s.state == "live"
    assert s.pnl_pct == 8.1


def test_classify_mood_bullish():
    m = classify_mood(verdict_score=75)
    assert m.label == "Bullish"
    assert m.emoji == "🚀"
    assert m.accent == "up"


def test_classify_mood_mildly_bullish():
    m = classify_mood(verdict_score=35)
    assert m.label == "Mildly Bullish"
    assert m.emoji == "😊"
    assert m.accent == "up"


def test_classify_mood_neutral():
    m = classify_mood(verdict_score=5)
    assert m.label == "Neutral"
    assert m.emoji == "😐"
    assert m.accent == "muted"


def test_classify_mood_mildly_bearish():
    m = classify_mood(verdict_score=-35)
    assert m.label == "Mildly Bearish"
    assert m.emoji == "😬"
    assert m.accent == "dn"


def test_classify_mood_bearish():
    m = classify_mood(verdict_score=-75)
    assert m.label == "Bearish"
    assert m.emoji == "😱"
    assert m.accent == "dn"


def test_classify_mood_boundaries():
    # Per spec Section 5.1: >=60 Bullish, 20..60 Mildly Bullish, -20..20 Neutral, -60..-20 Mildly Bearish, <=-60 Bearish
    assert classify_mood(60).label == "Bullish"
    assert classify_mood(20).label == "Mildly Bullish"
    assert classify_mood(-20).label == "Mildly Bearish"
    assert classify_mood(-60).label == "Bearish"
    assert classify_mood(0).label == "Neutral"
