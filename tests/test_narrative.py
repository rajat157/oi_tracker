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


from analysis.narrative import (
    magnitude_bucket, spot_location_bucket, pick_variant,
    STATE_TEMPLATES, PRESSURE_TEMPLATES, OUTLOOK_TEMPLATES, IH_STATE_TEMPLATES,
)


def test_magnitude_bucket_ranges():
    assert magnitude_bucket(-0.8) == "strong_dn"
    assert magnitude_bucket(-0.3) == "mild_dn"
    assert magnitude_bucket(0.0) == "small"
    assert magnitude_bucket(0.05) == "small"
    assert magnitude_bucket(0.3) == "mild"
    assert magnitude_bucket(0.8) == "strong"


def test_spot_location_near_support():
    assert spot_location_bucket(spot=23105, support=23100, resistance=23400) == "near_support"


def test_spot_location_near_resistance():
    assert spot_location_bucket(spot=23390, support=23100, resistance=23400) == "near_resistance"


def test_spot_location_centred():
    assert spot_location_bucket(spot=23250, support=23100, resistance=23400) == "centred"


def test_pick_variant_deterministic():
    variants = ["A", "B", "C"]
    # Same inputs → same output
    v1 = pick_variant(variants, regime="TRENDING_UP", state="strong", minute_of_day=600)
    v2 = pick_variant(variants, regime="TRENDING_UP", state="strong", minute_of_day=600)
    assert v1 == v2
    # Different minute bucket (15-min buckets) may pick different variant
    v3 = pick_variant(variants, regime="TRENDING_UP", state="strong", minute_of_day=615)
    # Within same bucket (600..614), same variant
    v4 = pick_variant(variants, regime="TRENDING_UP", state="strong", minute_of_day=614)
    assert v1 == v4


def test_pick_variant_empty_list_returns_fallback():
    assert pick_variant([], regime="X", state="y", minute_of_day=0) == ""


def test_state_templates_cover_all_regimes():
    required_regimes = {
        "TRENDING_UP", "TRENDING_DOWN", "HIGH_VOL_UP", "HIGH_VOL_DOWN",
        "NORMAL", "LOW_VOL",
    }
    covered = {key[0] for key in STATE_TEMPLATES.keys()}
    assert required_regimes.issubset(covered), f"missing regimes: {required_regimes - covered}"


def test_every_template_slot_has_at_least_three_variants():
    for key, variants in STATE_TEMPLATES.items():
        assert len(variants) >= 3, f"STATE_TEMPLATES[{key}] has <3 variants"
    for key, variants in PRESSURE_TEMPLATES.items():
        assert len(variants) >= 3, f"PRESSURE_TEMPLATES[{key}] has <3 variants"


def test_ih_state_templates_cover_all_group_states():
    required = {"forming", "live", "recently_closed", "locked_out"}
    assert required.issubset(set(IH_STATE_TEMPLATES.keys()))


from analysis.narrative import build_story, StoryInputs


def _base_inputs(**overrides):
    """Build a valid StoryInputs for tests; override specific fields."""
    defaults = dict(
        spot=23190.0,
        open_price=23145.0,
        previous_close=23145.0,
        support=23100,
        resistance=23300,
        verdict_score=58.0,
        regime="NORMAL",
        momentum_9m=0.3,
        minute_of_day=630,
        ih_state=IHStoryState(state=IHGroupState.WAITING, day_bias=0.62),
        rr_state=RRStoryState(state="waiting"),
        data_age_seconds=30,
    )
    defaults.update(overrides)
    return StoryInputs(**defaults)


def test_build_story_returns_two_or_three_sentences():
    story = build_story(_base_inputs())
    assert story.warning is None
    assert 2 <= len(story.sentences) <= 3


def test_build_story_has_outlook_when_verdict_strong():
    story = build_story(_base_inputs(verdict_score=70, regime="TRENDING_UP"))
    assert len(story.sentences) == 3


def test_build_story_no_outlook_when_verdict_weak():
    story = build_story(_base_inputs(verdict_score=10, regime="NORMAL"))
    assert len(story.sentences) == 2


def test_build_story_no_outlook_when_low_vol():
    story = build_story(_base_inputs(verdict_score=80, regime="LOW_VOL"))
    assert len(story.sentences) == 2


def test_build_story_stale_data_returns_warning():
    story = build_story(_base_inputs(data_age_seconds=600))  # 10 min old
    assert story.warning is not None
    assert story.warning.code == "STALE_DATA"
    assert story.sentences == []


def test_build_story_missing_regime_returns_warning():
    story = build_story(_base_inputs(regime=None))
    assert story.warning is not None
    assert story.warning.code == "REGIME_UNKNOWN"


def test_build_story_ih_live_sentence_mentions_pnl():
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 142.0, "current_premium": 154.0, "is_paper": False},
    ]
    story = build_story(_base_inputs(
        ih_state=IHStoryState(
            state=IHGroupState.LIVE, group_id="a3f2b1",
            positions=positions, agent_verdict="HOLD", day_bias=0.62,
        ),
    ))
    combined = " ".join(story.sentences)
    # Live IH story must include the IH sentence mentioning positions/PnL
    assert "IH" in combined or "position" in combined.lower()


def test_build_story_deterministic_for_same_inputs():
    inputs = _base_inputs()
    s1 = build_story(inputs)
    s2 = build_story(inputs)
    assert s1.sentences == s2.sentences


# ---------------------------------------------------------------------------
# Issue 1: _fmt_signed_pnl sign placement
# ---------------------------------------------------------------------------

from analysis.narrative import _fmt_signed_pnl


def test_fmt_signed_pnl_positive():
    assert _fmt_signed_pnl(500) == "+₹500"


def test_fmt_signed_pnl_negative():
    assert _fmt_signed_pnl(-500) == "-₹500"


def test_fmt_signed_pnl_zero():
    assert _fmt_signed_pnl(0) == "₹0"


def test_fmt_signed_pnl_thousands_separator():
    assert _fmt_signed_pnl(1500) == "+₹1,500"
    assert _fmt_signed_pnl(-1500) == "-₹1,500"


# ---------------------------------------------------------------------------
# Issue 2: pick_variant cross-process determinism
# ---------------------------------------------------------------------------

import subprocess
import sys


def test_pick_variant_deterministic_across_processes():
    """pick_variant must produce the same output across separate Python processes.

    Python's built-in hash() is randomized per-process. We use a stable hash
    (md5) so persisted stories can be regenerated identically during replay.
    """
    code = (
        "from analysis.narrative import pick_variant; "
        "print(pick_variant(['A', 'B', 'C'], regime='TRENDING_UP', state='strong', minute_of_day=600))"
    )
    r1 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    r2 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r1.stdout.strip() == r2.stdout.strip(), \
        f"Cross-process determinism broken: {r1.stdout!r} vs {r2.stdout!r}"


# ---------------------------------------------------------------------------
# Issue 3: REGIME_UNKNOWN priority over STALE_DATA
# ---------------------------------------------------------------------------


def test_build_story_regime_unknown_takes_priority_over_stale():
    """When both regime is None AND data is stale, surface REGIME_UNKNOWN.

    A fresh app start can satisfy both conditions; REGIME_UNKNOWN is the
    more actionable message ('Still gathering data...' vs 'Last update Nm ago').
    """
    story = build_story(_base_inputs(regime=None, data_age_seconds=600))
    assert story.warning is not None
    assert story.warning.code == "REGIME_UNKNOWN"
