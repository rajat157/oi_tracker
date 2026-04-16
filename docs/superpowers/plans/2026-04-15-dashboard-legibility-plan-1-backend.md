# Dashboard Legibility — Plan 1 of 4: Backend Story Engine + APIs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the server-side narrative engine, tile state classifier, and four new API endpoints that power the novice and expert dashboard views. No frontend changes in this plan.

**Architecture:** A pure-Python `analysis/narrative.py` module composes 2-3 sentence market stories from templates selected deterministically by regime + market state + IH/RR state. A companion `analysis/tile_state.py` classifies raw state into tile-ready payloads. Both are invoked once per 3-min analysis cycle inside `OIScheduler.fetch_and_analyze()`. A new Flask blueprint exposes `/api/story`, `/api/tiles`, `/api/ih/group`, `/api/multi-index`. Story text is persisted to `analysis_history.story_text`.

**Tech Stack:** Python 3.11+, Flask blueprints (existing pattern in `api/market.py`), SQLite (existing `analysis_history` table via `ALTER TABLE`), pytest with existing `conftest.py` fixtures (`_isolate_test_db`, `_clean_global_event_bus`).

**Reference spec:** `docs/superpowers/specs/2026-04-15-dashboard-legibility-design.md` — Sections 4 (Story Engine), 5 (Tile System, backend state portion), 7 (Data Flow + APIs).

---

## File Structure

**Create:**
- `analysis/narrative.py` — Story engine (dataclasses, template catalogues, `build_story`)
- `analysis/tile_state.py` — Tile state classifier (`build_tile_state`)
- `api/story.py` — Flask blueprint for new endpoints
- `tests/test_narrative.py` — Narrative engine unit tests
- `tests/test_tile_state.py` — Tile classifier unit tests
- `tests/test_api_story.py` — API endpoint integration tests

**Modify:**
- `db/legacy.py` — Add `story_text` to `init_db` (ALTER TABLE) and `save_analysis` signature + INSERT
- `strategies/intraday_hunter.py` — Add `story_state() -> IHStoryState` method
- `strategies/rr_strategy.py` — Add `story_state() -> RRStoryState` method
- `monitoring/scheduler.py` — Call `build_story` + `build_tile_state` in `fetch_and_analyze`; emit `story_update`, `tiles_update`, `ih_group_update` SocketIO events
- `app.py` — Register `api/story.py` blueprint
- `api/market.py` — Include `story_text` in `_enrich_analysis` output

**Out of scope for this plan:** All `templates/*.html`, all `static/**/*.{css,js}`, novice/expert view rendering, visual tokens.

---

## Baseline: verify test suite passes before starting

- [ ] **Step 0.1: Run full test suite to establish baseline**

Run: `uv run python -m pytest tests/ -q`
Expected: all tests pass. Record the total count (`N passed`) so regressions in later tasks are easy to spot.

---

## Task 1: Narrative data types

**Files:**
- Create: `analysis/narrative.py`
- Test: `tests/test_narrative.py`

- [ ] **Step 1.1: Write failing tests for Story, Warning, Severity**

Create `tests/test_narrative.py`:

```python
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
```

- [ ] **Step 1.2: Run tests — expect ImportError**

Run: `uv run python -m pytest tests/test_narrative.py -v`
Expected: `ModuleNotFoundError: No module named 'analysis.narrative'`

- [ ] **Step 1.3: Implement the data types**

Create `analysis/narrative.py`:

```python
"""Narrative engine — composes 2-3 sentence market stories from templates.

See docs/superpowers/specs/2026-04-15-dashboard-legibility-design.md (Section 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class Warning:
    """A user-facing failure condition with a recommended action."""

    code: str                               # e.g. "KITE_TOKEN_EXPIRED"
    message: str                            # plain-English description
    severity: Severity = Severity.WARN
    action_label: Optional[str] = None      # button text, e.g. "Re-authenticate"
    action_url: Optional[str] = None        # endpoint the button hits


@dataclass
class Story:
    """Generated narrative for the dashboard headline.

    Exactly one of (sentences, warning) is populated. When warning is set,
    the UI renders the warning card instead of the prose.
    """

    sentences: list[str] = field(default_factory=list)
    warning: Optional[Warning] = None

    def has_content(self) -> bool:
        return self.warning is None and len(self.sentences) > 0
```

- [ ] **Step 1.4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_narrative.py -v`
Expected: 4 passed.

- [ ] **Step 1.5: Commit**

```bash
git add analysis/narrative.py tests/test_narrative.py
git commit -m "feat(narrative): add Story, Warning, Severity data types"
```

---

## Task 2: State-container dataclasses + Mood classifier

**Files:**
- Modify: `analysis/narrative.py` (append)
- Modify: `tests/test_narrative.py` (append)

- [ ] **Step 2.1: Write failing tests**

Append to `tests/test_narrative.py`:

```python
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
```

- [ ] **Step 2.2: Run tests — expect ImportError on the new symbols**

Run: `uv run python -m pytest tests/test_narrative.py -v`
Expected: `ImportError: cannot import name 'IHStoryState'`.

- [ ] **Step 2.3: Implement**

Append to `analysis/narrative.py`:

```python
class IHGroupState(str, Enum):
    WAITING = "waiting"
    FORMING = "forming"
    LIVE = "live"
    RECENTLY_CLOSED = "recently_closed"
    LOCKED_OUT = "locked_out"


@dataclass
class IHStoryState:
    """IntradayHunter state snapshot for narrative + tile builders."""

    state: IHGroupState
    group_id: Optional[str] = None              # short group id when live/closed
    detector_armed: Optional[str] = None        # "E1" | "E2" | "E3" | None
    alignment: dict = field(default_factory=dict)  # {"NIFTY": True, "BANKNIFTY": True, "SENSEX": False}
    positions: list[dict] = field(default_factory=list)  # see test fixture for shape
    agent_verdict: Optional[str] = None         # "HOLD" | "TIGHTEN_SL" | "EXIT_NOW"
    day_bias: Optional[float] = None
    groups_today: int = 0
    max_groups_today: int = 1
    ago_minutes: Optional[int] = None           # only when RECENTLY_CLOSED


@dataclass
class RRStoryState:
    """Rally Rider state snapshot."""

    state: str                                  # "waiting" | "live"
    symbol: Optional[str] = None                # e.g. "NIFTY 23200 CE"
    entry: Optional[float] = None
    current_premium: Optional[float] = None
    pnl_pct: Optional[float] = None


@dataclass
class Mood:
    label: str          # "Bullish" | "Mildly Bullish" | "Neutral" | "Mildly Bearish" | "Bearish"
    emoji: str          # 🚀 😊 😐 😬 😱
    accent: str         # "up" | "muted" | "dn"


def classify_mood(verdict_score: float) -> Mood:
    """Map verdict score to Mood per spec Section 5.1."""
    if verdict_score >= 60:
        return Mood(label="Bullish", emoji="🚀", accent="up")
    if verdict_score >= 20:
        return Mood(label="Mildly Bullish", emoji="😊", accent="up")
    if verdict_score > -20:
        return Mood(label="Neutral", emoji="😐", accent="muted")
    if verdict_score > -60:
        return Mood(label="Mildly Bearish", emoji="😬", accent="dn")
    return Mood(label="Bearish", emoji="😱", accent="dn")
```

- [ ] **Step 2.4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_narrative.py -v`
Expected: all tests in this file pass.

- [ ] **Step 2.5: Commit**

```bash
git add analysis/narrative.py tests/test_narrative.py
git commit -m "feat(narrative): add IH/RR state types and mood classifier"
```

---

## Task 3: Template catalogue + deterministic variant selector

**Files:**
- Modify: `analysis/narrative.py`
- Modify: `tests/test_narrative.py`

- [ ] **Step 3.1: Write failing tests**

Append to `tests/test_narrative.py`:

```python
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
```

- [ ] **Step 3.2: Run tests — expect failures**

Run: `uv run python -m pytest tests/test_narrative.py -v`
Expected: ImportError on the new symbols.

- [ ] **Step 3.3: Implement**

Append to `analysis/narrative.py`:

```python
# ---------------------------------------------------------------------------
# Template catalogue — Section 4.4 of the spec
# ---------------------------------------------------------------------------

STATE_TEMPLATES: dict[tuple[str, str], list[str]] = {
    ("TRENDING_UP", "strong"): [
        "Market is rallying — up {pct}% from open.",
        "NIFTY is pushing higher, now +{pct}% on the day.",
        "Bulls in control; spot at {spot}, +{pct}% from open.",
    ],
    ("TRENDING_UP", "mild"): [
        "Market is drifting up from {open_price}.",
        "NIFTY edging higher — slow but steady move up.",
        "Spot at {spot}, quietly building gains.",
    ],
    ("TRENDING_DOWN", "strong"): [
        "Market is under pressure — down {pct}% today.",
        "NIFTY selling off; spot at {spot}, {pct}% from open.",
        "Sellers dominate; {pct}% drop on the session.",
    ],
    ("TRENDING_DOWN", "mild"): [
        "Market is drifting lower from {open_price}.",
        "NIFTY losing ground slowly; {pct}% down.",
        "Spot at {spot}, giving back gains.",
    ],
    ("HIGH_VOL_UP", "any"): [
        "Volatile bounce in progress — price up {pct}% but conviction is thin.",
        "Choppy rally to {spot}; expect whipsaws.",
        "NIFTY up {pct}% with wide swings — unstable move.",
    ],
    ("HIGH_VOL_DOWN", "any"): [
        "Volatile selloff — {pct}% down with wide swings.",
        "Panic-flavoured drop to {spot}; whipsaws likely.",
        "NIFTY {pct}% down, moves are wide and fast.",
    ],
    ("NORMAL", "small"): [
        "Market is consolidating around {spot}.",
        "NIFTY holding steady near {spot} — no clear direction.",
        "Spot drifting around {spot}; flat tape.",
    ],
    ("NORMAL", "mild"): [
        "Market is nudging {direction_word} at {spot}.",
        "NIFTY at {spot}, mild {direction_word} bias.",
        "Spot moving {direction_word}, {pct}% on the day.",
    ],
    ("LOW_VOL", "any"): [
        "Quiet session — NIFTY hovering near {spot}.",
        "Low-volatility day; spot pinned around {spot}.",
        "Flat market, spot at {spot} with tight range.",
    ],
}


PRESSURE_TEMPLATES: dict[tuple[str, str], list[str]] = {
    ("near_support", "force_bullish"): [
        "Put sellers confident below {support} and absorbing pressure.",
        "Buyers defending {support} — selling has stalled.",
        "Support at {support} holding; writers adding puts.",
    ],
    ("near_support", "force_bearish"): [
        "Support at {support} is weakening; bears pressing.",
        "Puts unwinding near {support} — defence looks fragile.",
        "Spot probing {support}; a break would accelerate selling.",
    ],
    ("near_resistance", "force_bullish"): [
        "Spot pressing {resistance}; a break would invite fresh buying.",
        "Calls unwinding near {resistance} — ceiling softening.",
        "Resistance at {resistance} being tested — bulls advancing.",
    ],
    ("near_resistance", "force_bearish"): [
        "Call sellers defending {resistance} firmly.",
        "Sellers stacked at {resistance}; ceiling intact.",
        "Resistance at {resistance} holding; writers adding calls.",
    ],
    ("centred", "force_bullish"): [
        "Put sellers confident below {support}; bulls advancing toward {resistance}.",
        "Buyers in control; {support} support, {resistance} resistance.",
        "Battle lines: {support} (put wall) and {resistance} (call wall); bulls winning.",
    ],
    ("centred", "force_bearish"): [
        "Call sellers defending {resistance}; bears pushing toward {support}.",
        "Sellers in control; {support} support, {resistance} resistance.",
        "Battle lines: {support} (put wall) and {resistance} (call wall); bears winning.",
    ],
    ("centred", "force_neutral"): [
        "Put sellers hold {support}; call sellers defend {resistance}.",
        "Range between {support} and {resistance} unchallenged.",
        "Spot centred between {support} and {resistance}; no decisive side.",
    ],
}


OUTLOOK_TEMPLATES: dict[str, list[str]] = {
    "bullish_strong": [
        "Expect continued upward bias.",
        "Path of least resistance remains up.",
        "Momentum favours further gains.",
    ],
    "bearish_strong": [
        "Expect continued downward pressure.",
        "Path of least resistance remains down.",
        "Momentum favours further declines.",
    ],
    "bullish_mild": [
        "Slight upward bias expected.",
        "Mild bullish lean into the close.",
        "Expect shallow pullbacks to be bought.",
    ],
    "bearish_mild": [
        "Slight downward bias expected.",
        "Mild bearish lean into the close.",
        "Expect shallow rallies to be sold.",
    ],
}


IH_STATE_TEMPLATES: dict[str, list[str]] = {
    "forming": [
        "Trap forming on {aligned}; {lagging} lagging. Watching {detector} for confirm.",
        "IH {detector} armed — {aligned} aligned, {lagging} lagging.",
        "Signal building: {detector} on {aligned}; awaiting R29 confirm.",
    ],
    "live": [
        "IH holding {n} position{s_plural}; net {pnl_signed} unrealised. Agent says {verdict_plain}.",
        "IH group #{group_id} open across {n} leg{s_plural}; P&L {pnl_signed}.",
        "Live IH: {n} position{s_plural}, {pnl_signed}. Agent: {verdict_plain}.",
    ],
    "recently_closed": [
        "IH closed group #{group_id} {ago}m ago: {net_result}.",
        "Last IH group ({group_id}) exited {ago}m ago — {net_result}.",
        "Group #{group_id} done {ago}m back: {net_result}.",
    ],
    "locked_out": [
        "IH paused for today — 2 losing days in a row.",
        "IH trading halted: 2-day loss circuit breaker active.",
        "No IH trades today — consecutive-loss lockout in effect.",
    ],
}


# ---------------------------------------------------------------------------
# Bucketing helpers — Section 4.5 deterministic variant selection
# ---------------------------------------------------------------------------

def magnitude_bucket(pct: float) -> str:
    """Map % change to a magnitude label."""
    if pct < -0.5:
        return "strong_dn"
    if pct < -0.1:
        return "mild_dn"
    if pct <= 0.1:
        return "small"
    if pct <= 0.5:
        return "mild"
    return "strong"


def spot_location_bucket(spot: float, support: float, resistance: float) -> str:
    """Classify where spot sits relative to support/resistance."""
    if spot is None or support is None or resistance is None:
        return "centred"
    dist_support = abs(spot - support)
    dist_resistance = abs(resistance - spot)
    if dist_support < 0.3 * dist_resistance:
        return "near_support"
    if dist_resistance < 0.3 * dist_support:
        return "near_resistance"
    return "centred"


def pick_variant(variants: list[str], regime: str, state: str, minute_of_day: int) -> str:
    """Deterministic variant picker using a 15-min bucket hash.

    Same (regime, state, minute_bucket) always returns the same variant.
    """
    if not variants:
        return ""
    bucket = minute_of_day // 15
    index = hash((regime, state, bucket)) % len(variants)
    return variants[index]
```

- [ ] **Step 3.4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_narrative.py -v`
Expected: all tests pass.

- [ ] **Step 3.5: Commit**

```bash
git add analysis/narrative.py tests/test_narrative.py
git commit -m "feat(narrative): add template catalogue and deterministic variant picker"
```

---

## Task 4: `build_story` assembly + failure-mode handling

**Files:**
- Modify: `analysis/narrative.py`
- Modify: `tests/test_narrative.py`

- [ ] **Step 4.1: Write failing tests**

Append to `tests/test_narrative.py`:

```python
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
```

- [ ] **Step 4.2: Run tests — expect failures**

Run: `uv run python -m pytest tests/test_narrative.py -v`
Expected: ImportError on `build_story` / `StoryInputs`.

- [ ] **Step 4.3: Implement `StoryInputs` and `build_story`**

Append to `analysis/narrative.py`:

```python
# ---------------------------------------------------------------------------
# Story assembly
# ---------------------------------------------------------------------------

MAX_DATA_AGE_SECONDS = 360  # 6 minutes — above this, render STALE_DATA warning


@dataclass
class StoryInputs:
    """All inputs build_story consumes. See spec Section 4.3."""

    spot: Optional[float]
    open_price: Optional[float]              # today's 09:15 open
    previous_close: Optional[float]
    support: Optional[int]
    resistance: Optional[int]
    verdict_score: Optional[float]
    regime: Optional[str]                    # RR regime label; None if not yet classified
    momentum_9m: Optional[float]             # pct
    minute_of_day: int                       # 0..1439; IST minute since midnight
    ih_state: IHStoryState
    rr_state: RRStoryState
    data_age_seconds: int                    # seconds since last successful analysis cycle


def _force_direction(verdict_score: float) -> str:
    if verdict_score is None:
        return "force_neutral"
    if verdict_score >= 20:
        return "force_bullish"
    if verdict_score <= -20:
        return "force_bearish"
    return "force_neutral"


def _outlook_key(verdict_score: float) -> Optional[str]:
    if verdict_score is None:
        return None
    if verdict_score >= 60:
        return "bullish_strong"
    if verdict_score >= 30:
        return "bullish_mild"
    if verdict_score <= -60:
        return "bearish_strong"
    if verdict_score <= -30:
        return "bearish_mild"
    return None


def _plain_verdict(verdict: str) -> str:
    return {
        "HOLD": "hold",
        "TIGHTEN_SL": "tighten stop",
        "EXIT_NOW": "exit now",
    }.get(verdict, "hold")


def _fmt_signed_pnl(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}₹{pnl:,.0f}"


def _compute_pct(current: float, anchor: float) -> float:
    if not anchor:
        return 0.0
    return round((current - anchor) / anchor * 100, 2)


def _pick_state_sentence(inputs: StoryInputs) -> str:
    pct = _compute_pct(inputs.spot or 0, inputs.open_price or inputs.previous_close or 0)
    mag = magnitude_bucket(pct)
    direction_word = "up" if pct > 0 else ("down" if pct < 0 else "flat")

    # Look up template by (regime, magnitude) then fall back to (regime, "any")
    variants = STATE_TEMPLATES.get((inputs.regime, mag)) \
        or STATE_TEMPLATES.get((inputs.regime, "any")) \
        or STATE_TEMPLATES.get(("NORMAL", "small"))

    template = pick_variant(variants, inputs.regime or "NORMAL", mag, inputs.minute_of_day)
    return template.format(
        pct=abs(pct),
        spot=int(inputs.spot or 0),
        open_price=int(inputs.open_price or 0),
        direction_word=direction_word,
    )


def _pick_pressure_sentence(inputs: StoryInputs) -> str:
    loc = spot_location_bucket(inputs.spot, inputs.support, inputs.resistance)
    force = _force_direction(inputs.verdict_score or 0)
    key = (loc, force)
    variants = PRESSURE_TEMPLATES.get(key) \
        or PRESSURE_TEMPLATES.get(("centred", "force_neutral"))
    template = pick_variant(variants, inputs.regime or "NORMAL", f"{loc}_{force}", inputs.minute_of_day)
    return template.format(
        support=inputs.support or 0,
        resistance=inputs.resistance or 0,
    )


def _pick_outlook_sentence(inputs: StoryInputs) -> Optional[str]:
    if inputs.regime == "LOW_VOL":
        return None
    key = _outlook_key(inputs.verdict_score or 0)
    if key is None:
        return None
    variants = OUTLOOK_TEMPLATES.get(key, [])
    return pick_variant(variants, inputs.regime or "NORMAL", key, inputs.minute_of_day)


def _pick_ih_sentence(inputs: StoryInputs) -> Optional[str]:
    st = inputs.ih_state
    if st.state in (IHGroupState.WAITING,):
        return None
    key = st.state.value
    variants = IH_STATE_TEMPLATES.get(key, [])
    if not variants:
        return None
    template = pick_variant(variants, inputs.regime or "NORMAL", key, inputs.minute_of_day)

    aligned = ", ".join(k for k, v in st.alignment.items() if v) or "NIFTY"
    lagging = ", ".join(k for k, v in st.alignment.items() if not v) or "none"
    n = len(st.positions)
    total_pnl = sum(
        (p.get("current_premium", 0) - p.get("entry_premium", 0))
        * p.get("quantity", 1)
        for p in st.positions
    )
    return template.format(
        aligned=aligned,
        lagging=lagging,
        detector=st.detector_armed or "signal",
        n=n,
        s_plural="s" if n != 1 else "",
        pnl_signed=_fmt_signed_pnl(total_pnl),
        group_id=st.group_id or "",
        verdict_plain=_plain_verdict(st.agent_verdict or "HOLD"),
        ago=st.ago_minutes or 0,
        net_result=_fmt_signed_pnl(total_pnl) if total_pnl else "flat",
    )


def build_story(inputs: StoryInputs) -> Story:
    """Compose the market narrative per spec Section 4.

    Returns a Story with either populated sentences or a Warning — never both.
    """
    # Failure modes first — Section 4.6
    if inputs.data_age_seconds > MAX_DATA_AGE_SECONDS:
        mins = inputs.data_age_seconds // 60
        return Story(warning=Warning(
            code="STALE_DATA",
            message=f"Last update {mins}m ago.",
            severity=Severity.WARN,
        ))
    if inputs.regime is None:
        return Story(warning=Warning(
            code="REGIME_UNKNOWN",
            message="Still gathering data…",
            severity=Severity.INFO,
        ))
    if inputs.spot is None or inputs.support is None or inputs.resistance is None:
        return Story(warning=Warning(
            code="ANALYSIS_INCOMPLETE",
            message="Analysis inputs incomplete.",
            severity=Severity.WARN,
        ))

    sentences: list[str] = []

    # IH-specific sentence takes priority over generic pressure when active
    ih_sentence = _pick_ih_sentence(inputs)
    if ih_sentence and inputs.ih_state.state in (IHGroupState.LIVE, IHGroupState.LOCKED_OUT):
        sentences.append(_pick_state_sentence(inputs))
        sentences.append(ih_sentence)
    else:
        sentences.append(_pick_state_sentence(inputs))
        sentences.append(_pick_pressure_sentence(inputs))
        if ih_sentence:  # forming / recently_closed — append as third sentence
            sentences.append(ih_sentence)
            return Story(sentences=sentences)

    outlook = _pick_outlook_sentence(inputs)
    if outlook:
        sentences.append(outlook)

    return Story(sentences=sentences)
```

- [ ] **Step 4.4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_narrative.py -v`
Expected: all tests pass.

- [ ] **Step 4.5: Commit**

```bash
git add analysis/narrative.py tests/test_narrative.py
git commit -m "feat(narrative): add build_story assembly with failure-mode warnings"
```

---

## Task 5: Tile state builder

**Files:**
- Create: `analysis/tile_state.py`
- Create: `tests/test_tile_state.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/test_tile_state.py`:

```python
"""Unit tests for analysis/tile_state.py."""

import pytest
from analysis.narrative import (
    IHStoryState, IHGroupState, RRStoryState,
)
from analysis.tile_state import build_tile_state, TileState


def _base_args(**overrides):
    defaults = dict(
        verdict_score=58.0,
        verdict_ema=55.0,
        spot=23190.0,
        support=23100,
        resistance=23300,
        momentum_9m=0.3,
        ih_state=IHStoryState(state=IHGroupState.WAITING, day_bias=0.62, max_groups_today=1),
        rr_state=RRStoryState(state="waiting"),
    )
    defaults.update(overrides)
    return defaults


def test_waiting_state_returns_four_tiles():
    tiles = build_tile_state(**_base_args())
    assert len(tiles) == 4
    assert tiles[0].slot == 1
    assert tiles[3].slot == 4


def test_mood_tile_reflects_verdict():
    tiles = build_tile_state(**_base_args(verdict_score=75))
    mood = tiles[0]
    assert mood.slot == 1
    assert "Bullish" in mood.primary
    assert mood.accent == "up"


def test_trade_tile_waiting():
    tiles = build_tile_state(**_base_args())
    trade = tiles[1]
    assert trade.slot == 2
    assert "Waiting" in trade.primary
    assert trade.accent == "info"


def test_trade_tile_forming():
    ih = IHStoryState(
        state=IHGroupState.FORMING, detector_armed="E2",
        alignment={"NIFTY": True, "BANKNIFTY": True, "SENSEX": False},
        day_bias=0.71,
    )
    tiles = build_tile_state(**_base_args(ih_state=ih))
    trade = tiles[1]
    assert "E2" in trade.primary
    assert trade.accent == "warn"
    assert trade.rows and any("NIFTY" in r["left"] for r in trade.rows)


def test_trade_tile_live():
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 142.0, "current_premium": 154.0,
         "quantity": 65, "is_paper": False},
    ]
    ih = IHStoryState(
        state=IHGroupState.LIVE, group_id="a3f2b1",
        positions=positions, agent_verdict="HOLD",
    )
    tiles = build_tile_state(**_base_args(ih_state=ih))
    trade = tiles[1]
    assert "₹" in trade.primary
    assert trade.accent in ("up", "dn")
    assert trade.rows and len(trade.rows) == 1


def test_battle_lines_tile_format():
    tiles = build_tile_state(**_base_args())
    bl = tiles[2]
    assert bl.slot == 3
    assert "23100" in bl.primary
    assert "23190" in bl.primary
    assert "23300" in bl.primary


def test_slot_four_day_bias_when_waiting():
    tiles = build_tile_state(**_base_args())
    slot4 = tiles[3]
    assert slot4.slot == 4
    assert "0.62" in slot4.primary


def test_slot_four_time_left_when_live():
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 142.0, "current_premium": 154.0,
         "quantity": 65, "is_paper": False, "time_left_minutes": 28},
    ]
    ih = IHStoryState(
        state=IHGroupState.LIVE, positions=positions, agent_verdict="HOLD",
    )
    tiles = build_tile_state(**_base_args(ih_state=ih))
    slot4 = tiles[3]
    assert "28m" in slot4.primary or "28 m" in slot4.primary
```

- [ ] **Step 5.2: Run tests — expect ImportError**

Run: `uv run python -m pytest tests/test_tile_state.py -v`
Expected: `ModuleNotFoundError: No module named 'analysis.tile_state'`.

- [ ] **Step 5.3: Implement**

Create `analysis/tile_state.py`:

```python
"""Tile state classifier — converts raw market + strategy state into
tile-ready payloads. Frontend renders these directly without further
adaptation logic.

See docs/superpowers/specs/2026-04-15-dashboard-legibility-design.md (Section 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from analysis.narrative import (
    IHGroupState, IHStoryState, RRStoryState, classify_mood,
)


@dataclass
class TileState:
    slot: int                                        # 1..4
    primary: str                                     # big headline text
    caption: str                                     # secondary line
    hint: str = ""                                    # italic footer
    accent: str = "muted"                             # "up" | "dn" | "warn" | "info" | "muted"
    rows: list[dict] = field(default_factory=list)   # optional mini-rows


def _build_mood_tile(verdict_score: float, verdict_ema: float) -> TileState:
    mood = classify_mood(verdict_score)
    ema_arrow = "↗" if verdict_ema > verdict_score else ("↘" if verdict_ema < verdict_score else "→")
    return TileState(
        slot=1,
        primary=f"{mood.emoji} {mood.label}",
        caption=f"Score {int(verdict_score)} / 100 · EMA {ema_arrow}",
        hint=_mood_hint(mood.label),
        accent=mood.accent,
    )


def _mood_hint(label: str) -> str:
    return {
        "Bullish": "Strong upside lean",
        "Mildly Bullish": "Leaning up, not racing",
        "Neutral": "No clear direction",
        "Mildly Bearish": "Leaning down, not panicked",
        "Bearish": "Strong downside lean",
    }.get(label, "")


def _build_trade_tile(ih: IHStoryState) -> TileState:
    if ih.state == IHGroupState.WAITING:
        return TileState(
            slot=2,
            primary="⏸ Waiting",
            caption=f"IH opens 09:35 · no group yet",
            hint=f"Today: {ih.groups_today}/{ih.max_groups_today} groups",
            accent="info",
        )

    if ih.state == IHGroupState.FORMING:
        rows = [
            {"left": idx, "right": "✓ aligned" if aligned else "◦ lagging"}
            for idx, aligned in ih.alignment.items()
        ] or [{"left": "NIFTY", "right": "◦ waiting"}]
        return TileState(
            slot=2,
            primary=f"{ih.detector_armed or 'E?'} armed · R29 pending",
            caption="Trap forming across indices",
            hint="Needs R29 confirm",
            accent="warn",
            rows=rows,
        )

    if ih.state == IHGroupState.LIVE:
        total_pnl = sum(
            (p.get("current_premium", 0) - p.get("entry_premium", 0)) * p.get("quantity", 1)
            for p in ih.positions
        )
        entry_sum = sum(p.get("entry_premium", 0) * p.get("quantity", 1) for p in ih.positions)
        pnl_pct = (total_pnl / entry_sum * 100) if entry_sum else 0.0
        rows = [
            {
                "left": f"{p['index']} {p['strike']}{p['option_type']}",
                "right": f"{'+' if p['current_premium'] >= p['entry_premium'] else ''}"
                         f"₹{(p['current_premium'] - p['entry_premium']) * p.get('quantity', 1):,.0f}"
                         f" [{'LIVE' if not p.get('is_paper') else 'PAPER'}]",
            }
            for p in ih.positions
        ]
        sign = "+" if total_pnl >= 0 else ""
        return TileState(
            slot=2,
            primary=f"{sign}₹{total_pnl:,.0f} · {sign}{pnl_pct:.1f}%",
            caption=f"Agent: {ih.agent_verdict or 'HOLD'}",
            hint="TSL arming at +10%",
            accent="up" if total_pnl >= 0 else "dn",
            rows=rows,
        )

    if ih.state == IHGroupState.RECENTLY_CLOSED:
        return TileState(
            slot=2,
            primary=f"Closed {ih.ago_minutes or 0}m ago",
            caption=f"Group #{ih.group_id or '?'}",
            hint="",
            accent="muted",
        )

    # LOCKED_OUT
    return TileState(
        slot=2,
        primary="Paused today",
        caption="2 losing days in a row",
        hint="IH circuit breaker active",
        accent="dn",
    )


def _build_battle_lines_tile(spot: float, support: int, resistance: int) -> TileState:
    # Which wall is spot closer to?
    if spot is None or support is None or resistance is None:
        return TileState(slot=3, primary="— ← ? → —", caption="Levels unavailable", accent="muted")
    ds = spot - support
    dr = resistance - spot
    if ds < dr * 0.5:
        hint = f"{int(ds)} pts above support"
    elif dr < ds * 0.5:
        hint = f"{int(dr)} pts below resistance"
    else:
        hint = "Spot centred between defences"
    return TileState(
        slot=3,
        primary=f"{support} ← {int(spot)} → {resistance}",
        caption="Support / Spot / Resistance",
        hint=hint,
        accent="muted",
    )


def _build_slot_four_tile(ih: IHStoryState, momentum_9m: float) -> TileState:
    if ih.state == IHGroupState.LIVE and ih.positions:
        times = " · ".join(
            f"{p['index']} {p.get('time_left_minutes', 0)}m" for p in ih.positions
        )
        return TileState(
            slot=4,
            primary=times,
            caption="Per-position time left",
            hint="EOD exit 15:15",
            accent="info",
        )

    bias = ih.day_bias
    if bias is None:
        return TileState(
            slot=4, primary="—", caption="Day bias unavailable",
            hint="Waiting for HDFC/KOTAK data", accent="muted",
        )
    arrow = "↗" if bias > 0 else ("↘" if bias < 0 else "→")
    above = bias >= 0.60
    return TileState(
        slot=4,
        primary=f"{'+' if bias >= 0 else ''}{bias:.2f} {arrow}",
        caption="Above threshold · favours calls" if above else "Below threshold",
        hint="Hover for HDFC/KOTAK breakdown",
        accent="warn" if above else "muted",
    )


def build_tile_state(
    verdict_score: float,
    verdict_ema: float,
    spot: float,
    support: int,
    resistance: int,
    momentum_9m: float,
    ih_state: IHStoryState,
    rr_state: RRStoryState,
) -> list[TileState]:
    """Produce the four tile payloads in slot order (1..4)."""
    return [
        _build_mood_tile(verdict_score, verdict_ema),
        _build_trade_tile(ih_state),
        _build_battle_lines_tile(spot, support, resistance),
        _build_slot_four_tile(ih_state, momentum_9m),
    ]
```

- [ ] **Step 5.4: Run tests — expect PASS**

Run: `uv run python -m pytest tests/test_tile_state.py -v`
Expected: all tests pass.

- [ ] **Step 5.5: Commit**

```bash
git add analysis/tile_state.py tests/test_tile_state.py
git commit -m "feat(tiles): add backend tile state classifier for novice view"
```

---

## Task 6: DB migration — `story_text` column

**Files:**
- Modify: `db/legacy.py` (two functions: `init_db` and `save_analysis`)
- Create: `tests/test_db_story_text.py`

- [ ] **Step 6.1: Write failing test**

Create `tests/test_db_story_text.py`:

```python
"""Test story_text column lifecycle in analysis_history."""

from datetime import datetime

from db.legacy import save_analysis, get_connection, get_latest_analysis


def test_save_analysis_accepts_and_persists_story_text():
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0,
        atm_strike=23200,
        total_call_oi=1000,
        total_put_oi=1100,
        call_oi_change=100,
        put_oi_change=200,
        verdict="BULLISH",
        expiry_date="2026-04-21",
        story_text="Market is drifting up. Put sellers defend 23100.",
    )
    latest = get_latest_analysis()
    assert latest is not None
    assert latest.get("story_text") == "Market is drifting up. Put sellers defend 23100."


def test_save_analysis_story_text_optional():
    # Backwards compatible — omitting story_text still works
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23200.0,
        atm_strike=23200,
        total_call_oi=1000,
        total_put_oi=1000,
        call_oi_change=0,
        put_oi_change=0,
        verdict="NEUTRAL",
        expiry_date="2026-04-21",
    )
    latest = get_latest_analysis()
    # story_text should be None when not provided
    assert "story_text" in latest
    assert latest["story_text"] is None or latest["story_text"] == ""


def test_story_text_column_exists_after_init():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(analysis_history)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "story_text" in cols
```

- [ ] **Step 6.2: Run tests — expect failure**

Run: `uv run python -m pytest tests/test_db_story_text.py -v`
Expected: `TypeError: save_analysis() got an unexpected keyword argument 'story_text'` or `story_text` column missing.

- [ ] **Step 6.3: Modify `db/legacy.py` — add ALTER TABLE in init_db**

In `db/legacy.py`, locate the `init_db()` function. Find the block of `ALTER TABLE analysis_history ADD COLUMN …` statements (around lines 277–348). Add one more — place it at the end of that block:

```python
try:
    cursor.execute("ALTER TABLE analysis_history ADD COLUMN story_text TEXT")
except sqlite3.OperationalError:
    pass  # column already exists
```

- [ ] **Step 6.4: Modify `db/legacy.py` — extend `save_analysis`**

Replace the `save_analysis` function (starting around line 491) so its signature accepts `story_text` and the INSERT writes it. The full new function:

```python
def save_analysis(timestamp, spot_price, atm_strike, total_call_oi, total_put_oi,
                  call_oi_change, put_oi_change, verdict, expiry_date,
                  atm_call_oi_change: int = 0, atm_put_oi_change: int = 0,
                  itm_call_oi_change: int = 0, itm_put_oi_change: int = 0,
                  vix: float = 0.0, iv_skew: float = 0.0, max_pain: int = 0,
                  signal_confidence: float = 0.0,
                  futures_oi: int = 0, futures_oi_change: int = 0, futures_basis: float = 0.0,
                  analysis_json: str = None,
                  prev_verdict: str = None,
                  story_text: str = None):
    """Save analysis result to history including full JSON blob and story text."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO analysis_history
            (timestamp, spot_price, atm_strike, total_call_oi, total_put_oi,
             call_oi_change, put_oi_change, verdict, prev_verdict, expiry_date,
             atm_call_oi_change, atm_put_oi_change, itm_call_oi_change, itm_put_oi_change,
             vix, iv_skew, max_pain, signal_confidence,
             futures_oi, futures_oi_change, futures_basis, analysis_json, story_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp.isoformat(),
            spot_price,
            atm_strike,
            total_call_oi,
            total_put_oi,
            call_oi_change,
            put_oi_change,
            verdict,
            prev_verdict,
            expiry_date,
            atm_call_oi_change,
            atm_put_oi_change,
            itm_call_oi_change,
            itm_put_oi_change,
            vix,
            iv_skew,
            max_pain,
            signal_confidence,
            futures_oi,
            futures_oi_change,
            futures_basis,
            analysis_json,
            story_text,
        ))
        conn.commit()
```

- [ ] **Step 6.5: Run tests**

Run: `uv run python -m pytest tests/test_db_story_text.py -v`
Expected: all 3 pass.

- [ ] **Step 6.6: Run full suite to confirm no regression**

Run: `uv run python -m pytest tests/ -q`
Expected: all tests pass; total count = baseline + 3 new tests from this task + prior tasks.

- [ ] **Step 6.7: Commit**

```bash
git add db/legacy.py tests/test_db_story_text.py
git commit -m "feat(db): add story_text column to analysis_history"
```

---

## Task 7: `IntradayHunterStrategy.story_state()`

**Files:**
- Modify: `strategies/intraday_hunter.py`
- Create: `tests/test_strategies/test_ih_story_state.py`

- [ ] **Step 7.1: Read current strategy to locate insertion point**

Run: `uv run python -c "from strategies.intraday_hunter import IntradayHunterStrategy; import inspect; print([m for m in dir(IntradayHunterStrategy) if not m.startswith('__')])"`

Record the method list. The new `story_state()` sits next to `get_active()` / `get_stats()` (around line 934 in current file).

- [ ] **Step 7.2: Write failing test**

Create `tests/test_strategies/test_ih_story_state.py`:

```python
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
```

- [ ] **Step 7.3: Run test — expect AttributeError**

Run: `uv run python -m pytest tests/test_strategies/test_ih_story_state.py -v`
Expected: `AttributeError: 'IntradayHunterStrategy' object has no attribute 'story_state'`.

- [ ] **Step 7.4: Implement `story_state()` in `strategies/intraday_hunter.py`**

Add the following method to `IntradayHunterStrategy` (place it adjacent to `get_active()` near line 934). Also add the supporting instance attributes to `__init__` if not present — see below.

Add to the end of `__init__`:

```python
# State tracking for story_state() — populated by engine during each cycle
self._day_bias: float | None = None
self._armed_detector: str | None = None
self._alignment: dict[str, bool] = {}
self._last_closed_group: dict | None = None    # {"group_id": str, "closed_at": datetime}
self._locked_out: bool = False
```

Add the method:

```python
def story_state(self):
    """Return an IHStoryState snapshot for the narrative engine.

    State precedence:
      1. LOCKED_OUT — 2-day circuit breaker is active
      2. LIVE       — any position is currently open
      3. FORMING    — an E-detector is armed but no position yet
      4. RECENTLY_CLOSED — a group closed within the last 10 minutes
      5. WAITING    — default
    """
    from analysis.narrative import IHGroupState, IHStoryState
    from datetime import datetime

    if self._locked_out:
        return IHStoryState(
            state=IHGroupState.LOCKED_OUT,
            day_bias=self._day_bias,
            groups_today=self._count_signal_groups_today(),
            max_groups_today=self._cfg.MAX_GROUPS_PER_DAY,
        )

    if self._has_open_positions():
        positions = self._fetch_active_positions()
        formatted = [
            {
                "index": p.get("index_label"),
                "strike": p.get("strike"),
                "option_type": p.get("option_type"),
                "entry_premium": p.get("entry_premium", 0),
                "current_premium": p.get("current_premium", p.get("entry_premium", 0)),
                "quantity": p.get("qty", 1),
                "is_paper": bool(p.get("is_paper", 1)),
                "time_left_minutes": p.get("time_left_minutes", 0),
            }
            for p in positions
        ]
        group_id = positions[0].get("signal_group_id", "") if positions else ""
        return IHStoryState(
            state=IHGroupState.LIVE,
            group_id=group_id[:5] if group_id else None,
            positions=formatted,
            agent_verdict=getattr(self, "_last_agent_verdict", "HOLD"),
            day_bias=self._day_bias,
            groups_today=self._count_signal_groups_today(),
            max_groups_today=self._cfg.MAX_GROUPS_PER_DAY,
        )

    if self._armed_detector is not None:
        return IHStoryState(
            state=IHGroupState.FORMING,
            detector_armed=self._armed_detector,
            alignment=dict(self._alignment),
            day_bias=self._day_bias,
            groups_today=self._count_signal_groups_today(),
            max_groups_today=self._cfg.MAX_GROUPS_PER_DAY,
        )

    if self._last_closed_group:
        delta = datetime.now() - self._last_closed_group["closed_at"]
        ago = int(delta.total_seconds() // 60)
        if ago <= 10:
            return IHStoryState(
                state=IHGroupState.RECENTLY_CLOSED,
                group_id=self._last_closed_group["group_id"],
                ago_minutes=ago,
                day_bias=self._day_bias,
                groups_today=self._count_signal_groups_today(),
                max_groups_today=self._cfg.MAX_GROUPS_PER_DAY,
            )

    return IHStoryState(
        state=IHGroupState.WAITING,
        day_bias=self._day_bias,
        groups_today=self._count_signal_groups_today(),
        max_groups_today=self._cfg.MAX_GROUPS_PER_DAY,
    )
```

- [ ] **Step 7.5: Run tests**

Run: `uv run python -m pytest tests/test_strategies/test_ih_story_state.py -v`
Expected: all 5 pass.

- [ ] **Step 7.6: Run full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 7.7: Commit**

```bash
git add strategies/intraday_hunter.py tests/test_strategies/test_ih_story_state.py
git commit -m "feat(ih): expose story_state() for narrative engine"
```

---

## Task 8: `RRStrategy.story_state()`

**Files:**
- Modify: `strategies/rr_strategy.py`
- Create: `tests/test_strategies/test_rr_story_state.py`

- [ ] **Step 8.1: Write failing test**

Create `tests/test_strategies/test_rr_story_state.py`:

```python
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
```

- [ ] **Step 8.2: Run — expect failure**

Run: `uv run python -m pytest tests/test_strategies/test_rr_story_state.py -v`
Expected: `AttributeError: 'RRStrategy' object has no attribute 'story_state'`.

- [ ] **Step 8.3: Implement in `strategies/rr_strategy.py`**

Add method adjacent to `get_active()` (around line 551):

```python
def story_state(self):
    """Return an RRStoryState snapshot for the narrative engine."""
    from analysis.narrative import RRStoryState

    active = self.get_active()
    if active is None:
        return RRStoryState(state="waiting")

    entry = active.get("entry_premium", 0.0)
    current = active.get("current_premium", entry)
    pnl_pct = ((current - entry) / entry * 100) if entry else 0.0
    symbol = f"NIFTY {active.get('strike', '?')} {active.get('option_type', '?')}"
    return RRStoryState(
        state="live",
        symbol=symbol,
        entry=entry,
        current_premium=current,
        pnl_pct=round(pnl_pct, 2),
    )
```

- [ ] **Step 8.4: Run tests**

Run: `uv run python -m pytest tests/test_strategies/test_rr_story_state.py -v`
Expected: both pass.

- [ ] **Step 8.5: Commit**

```bash
git add strategies/rr_strategy.py tests/test_strategies/test_rr_story_state.py
git commit -m "feat(rr): expose story_state() for narrative engine"
```

---

## Task 9: `/api/story` endpoint (new blueprint)

**Files:**
- Create: `api/story.py`
- Create: `tests/test_api_story.py`
- Modify: `app.py` (register blueprint)

- [ ] **Step 9.1: Write failing integration test**

Create `tests/test_api_story.py`:

```python
"""Integration tests for the story/tiles/ih-group/multi-index API surface."""

import pytest
from datetime import datetime

from db.legacy import save_analysis


@pytest.fixture
def client():
    # Import at fixture time so the test DB patch from conftest is active
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_story_returns_404_when_no_analysis(client):
    response = client.get("/api/story")
    assert response.status_code in (200, 404)
    # If 200, payload must still indicate "no data"
    if response.status_code == 200:
        assert response.json.get("sentences") == [] or response.json.get("warning") is not None


def test_api_story_returns_latest_text(client):
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
        story_text="Market is drifting up. Put sellers defend 23100.",
    )
    response = client.get("/api/story")
    assert response.status_code == 200
    data = response.json
    assert "sentences" in data
    assert any("drifting up" in s for s in data["sentences"])
    assert data.get("warning") is None
```

- [ ] **Step 9.2: Run — expect 404 or route-not-found error**

Run: `uv run python -m pytest tests/test_api_story.py::test_api_story_returns_latest_text -v`
Expected: failure (route not registered).

- [ ] **Step 9.3: Create `api/story.py`**

```python
"""Story / tiles / IH group / multi-index API endpoints."""

from flask import Blueprint, jsonify

from db.legacy import get_latest_analysis

bp = Blueprint("story", __name__)


def _split_story_text(text: str | None) -> list[str]:
    """Split persisted story text back into sentences.

    The narrative engine joins sentences with single spaces and ends each
    with a period. A naive split on '. ' is sufficient for display.
    """
    if not text:
        return []
    # Add back the period that split() consumes, except on the last segment
    parts = text.split(". ")
    return [p if p.endswith(".") else p + "." for p in parts if p.strip()]


@bp.route("/api/story")
def api_story():
    """Return the latest generated story for the dashboard headline."""
    analysis = get_latest_analysis()
    if analysis is None:
        return jsonify({"sentences": [], "warning": None}), 200
    story_text = analysis.get("story_text")
    return jsonify({
        "sentences": _split_story_text(story_text),
        "warning": None,  # Warnings are produced live by the scheduler; persisted stories never carry warnings
        "timestamp": analysis.get("timestamp"),
    }), 200
```

- [ ] **Step 9.4: Register blueprint in `app.py`**

In `app.py`, add the import alongside the other blueprint imports:

```python
from api.story import bp as story_bp
```

And register it alongside the others:

```python
app.register_blueprint(story_bp)
```

- [ ] **Step 9.5: Run tests**

Run: `uv run python -m pytest tests/test_api_story.py -v`
Expected: both tests pass.

- [ ] **Step 9.6: Commit**

```bash
git add api/story.py app.py tests/test_api_story.py
git commit -m "feat(api): add /api/story endpoint"
```

---

## Task 10: `/api/tiles` endpoint

**Files:**
- Modify: `api/story.py`
- Modify: `tests/test_api_story.py`

- [ ] **Step 10.1: Write failing test**

Append to `tests/test_api_story.py`:

```python
def test_api_tiles_returns_four_slots(client):
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
    )
    response = client.get("/api/tiles")
    assert response.status_code == 200
    data = response.json
    assert "tiles" in data
    assert len(data["tiles"]) == 4
    for i, tile in enumerate(data["tiles"], start=1):
        assert tile["slot"] == i
        assert "primary" in tile
        assert "accent" in tile
```

- [ ] **Step 10.2: Run — expect 404**

Run: `uv run python -m pytest tests/test_api_story.py::test_api_tiles_returns_four_slots -v`
Expected: 404.

- [ ] **Step 10.3: Implement the endpoint**

Append to `api/story.py`:

```python
from dataclasses import asdict

from analysis.narrative import IHStoryState, IHGroupState, RRStoryState
from analysis.tile_state import build_tile_state


def _get_scheduler():
    from flask import current_app
    return current_app.config.get("oi_scheduler")


def _strategy(label: str):
    sched = _get_scheduler()
    return (sched.strategies or {}).get(label) if sched else None


def _ih_state() -> IHStoryState:
    ih = _strategy("intraday_hunter")
    if ih is not None and hasattr(ih, "story_state"):
        return ih.story_state()
    return IHStoryState(state=IHGroupState.WAITING)


def _rr_state() -> RRStoryState:
    rr = _strategy("rally_rider")
    if rr is not None and hasattr(rr, "story_state"):
        return rr.story_state()
    return RRStoryState(state="waiting")


@bp.route("/api/tiles")
def api_tiles():
    """Return the four tile state payloads for the novice view."""
    analysis = get_latest_analysis() or {}
    tiles = build_tile_state(
        verdict_score=float(analysis.get("verdict_score") or analysis.get("score") or 0),
        verdict_ema=float(analysis.get("verdict_ema") or analysis.get("ema_score") or 0),
        spot=float(analysis.get("spot_price") or 0),
        support=int(analysis.get("support") or 0),
        resistance=int(analysis.get("resistance") or 0),
        momentum_9m=float(analysis.get("momentum_9m") or 0),
        ih_state=_ih_state(),
        rr_state=_rr_state(),
    )
    return jsonify({"tiles": [asdict(t) for t in tiles]}), 200
```

- [ ] **Step 10.4: Run**

Run: `uv run python -m pytest tests/test_api_story.py -v`
Expected: all tests pass.

- [ ] **Step 10.5: Commit**

```bash
git add api/story.py tests/test_api_story.py
git commit -m "feat(api): add /api/tiles endpoint"
```

---

## Task 11: `/api/ih/group` endpoint

**Files:**
- Modify: `api/story.py`
- Modify: `tests/test_api_story.py`

- [ ] **Step 11.1: Write failing test**

Append to `tests/test_api_story.py`:

```python
def test_api_ih_group_returns_none_when_waiting(client):
    response = client.get("/api/ih/group")
    assert response.status_code == 200
    data = response.json
    assert data.get("state") == "waiting"
    assert data.get("positions") == []
```

- [ ] **Step 11.2: Run — expect 404**

Run: `uv run python -m pytest tests/test_api_story.py::test_api_ih_group_returns_none_when_waiting -v`
Expected: 404.

- [ ] **Step 11.3: Implement**

Append to `api/story.py`:

```python
@bp.route("/api/ih/group")
def api_ih_group():
    """Return current IntradayHunter signal group state + positions."""
    state = _ih_state()
    return jsonify({
        "state": state.state.value,
        "group_id": state.group_id,
        "detector_armed": state.detector_armed,
        "alignment": state.alignment,
        "positions": state.positions,
        "agent_verdict": state.agent_verdict,
        "day_bias": state.day_bias,
        "groups_today": state.groups_today,
        "max_groups_today": state.max_groups_today,
        "ago_minutes": state.ago_minutes,
    }), 200
```

- [ ] **Step 11.4: Run**

Run: `uv run python -m pytest tests/test_api_story.py -v`
Expected: all pass.

- [ ] **Step 11.5: Commit**

```bash
git add api/story.py tests/test_api_story.py
git commit -m "feat(api): add /api/ih/group endpoint"
```

---

## Task 12: `/api/multi-index` endpoint

**Files:**
- Modify: `api/story.py`
- Modify: `tests/test_api_story.py`

- [ ] **Step 12.1: Write failing test**

Append to `tests/test_api_story.py`:

```python
def test_api_multi_index_returns_known_indices(client):
    response = client.get("/api/multi-index")
    assert response.status_code == 200
    data = response.json
    # Must include keys for each tracked instrument; values may be null
    # when no candles available (e.g. outside market hours / test environment)
    for key in ["NIFTY", "BANKNIFTY", "SENSEX", "HDFC", "KOTAK"]:
        assert key in data
```

- [ ] **Step 12.2: Run — expect 404**

Run: `uv run python -m pytest tests/test_api_story.py::test_api_multi_index_returns_known_indices -v`
Expected: 404.

- [ ] **Step 12.3: Implement**

Append to `api/story.py`:

```python
_MULTI_INDEX_LABELS = ["NIFTY", "BANKNIFTY", "SENSEX", "HDFC", "KOTAK"]


def _pct_since_open(candle_builder, label: str) -> float | None:
    """Compute % change from today's first candle's open to the latest close."""
    try:
        candles = candle_builder.get_candles(label, interval="1min", count=500)
    except Exception:
        return None
    if not candles:
        return None
    # Filter to today only
    from datetime import date as date_cls
    today = date_cls.today().isoformat()
    todays = [c for c in candles if str(c.get("date", ""))[:10] == today]
    if not todays:
        return None
    open_price = todays[0].get("open")
    last_close = todays[-1].get("close")
    if not open_price or not last_close:
        return None
    return round((last_close - open_price) / open_price * 100, 2)


@bp.route("/api/multi-index")
def api_multi_index():
    """Return % change since today's open for each tracked instrument."""
    sched = _get_scheduler()
    cb = getattr(sched, "candle_builder", None) if sched else None
    result = {}
    for label in _MULTI_INDEX_LABELS:
        result[label] = _pct_since_open(cb, label) if cb else None
    return jsonify(result), 200
```

- [ ] **Step 12.4: Run**

Run: `uv run python -m pytest tests/test_api_story.py -v`
Expected: all pass.

- [ ] **Step 12.5: Commit**

```bash
git add api/story.py tests/test_api_story.py
git commit -m "feat(api): add /api/multi-index endpoint"
```

---

## Task 13: Wire story + tiles into the 3-min analysis cycle

**Files:**
- Modify: `monitoring/scheduler.py`
- Create: `tests/test_scheduler_story.py`

- [ ] **Step 13.1: Write failing test**

Create `tests/test_scheduler_story.py`:

```python
"""Verify fetch_and_analyze produces a story and emits new SocketIO events."""

from unittest.mock import MagicMock
from datetime import datetime


def _make_scheduler_for_test():
    """Build a barely-instantiated OIScheduler suitable for unit-testing helpers."""
    from monitoring.scheduler import OIScheduler
    sched = OIScheduler.__new__(OIScheduler)
    sched.socketio = MagicMock()
    sched.strategies = {}
    sched.candle_builder = MagicMock()
    return sched


def test_build_story_and_tiles_produces_both_payloads():
    """Direct test of the helper method that composes story + tiles."""
    sched = _make_scheduler_for_test()
    analysis = {
        "spot_price": 23190.0, "verdict_score": 58.0, "verdict_ema": 55.0,
        "support": 23100, "resistance": 23300, "momentum_9m": 0.3,
        "previous_close": 23145.0, "open_price": 23145.0, "regime": "NORMAL",
    }
    story_text, tiles = sched._build_story_and_tiles(analysis, data_age_seconds=30)
    assert story_text  # non-empty
    assert isinstance(tiles, list)
    assert len(tiles) == 4


def test_build_story_and_tiles_returns_none_text_when_stale():
    """When data is stale, story_text falls back to None and tiles still render."""
    sched = _make_scheduler_for_test()
    analysis = {
        "spot_price": 23190.0, "verdict_score": 58.0, "verdict_ema": 55.0,
        "support": 23100, "resistance": 23300, "momentum_9m": 0.3,
        "previous_close": 23145.0, "open_price": 23145.0, "regime": "NORMAL",
    }
    story_text, tiles = sched._build_story_and_tiles(analysis, data_age_seconds=600)
    assert story_text is None  # warning state has no joined sentences
    assert len(tiles) == 4     # tiles still render regardless
```

- [ ] **Step 13.2: Run — expect AttributeError**

Run: `uv run python -m pytest tests/test_scheduler_story.py -v`
Expected: `AttributeError: 'OIScheduler' object has no attribute '_build_story_and_tiles'`.

- [ ] **Step 13.3: Add `_build_story_and_tiles` helper and wire it into `fetch_and_analyze`**

In `monitoring/scheduler.py`, add this helper method on `OIScheduler` (place it near the other private helpers, e.g. after `_attach_ih_inputs`):

```python
def _build_story_and_tiles(self, analysis: dict, data_age_seconds: int = 0):
    """Compose the narrative story and tile states from the latest analysis.

    Returns (story_text_or_None, list_of_tile_dicts).
    """
    from analysis.narrative import (
        StoryInputs, build_story, IHStoryState, IHGroupState, RRStoryState,
    )
    from analysis.tile_state import build_tile_state
    from datetime import datetime
    from dataclasses import asdict

    ih_strategy = self.strategies.get("intraday_hunter") if self.strategies else None
    rr_strategy = self.strategies.get("rally_rider") if self.strategies else None

    ih_state = ih_strategy.story_state() if (ih_strategy and hasattr(ih_strategy, "story_state")) \
        else IHStoryState(state=IHGroupState.WAITING)
    rr_state = rr_strategy.story_state() if (rr_strategy and hasattr(rr_strategy, "story_state")) \
        else RRStoryState(state="waiting")

    now = datetime.now()
    minute_of_day = now.hour * 60 + now.minute

    inputs = StoryInputs(
        spot=analysis.get("spot_price"),
        open_price=analysis.get("open_price"),
        previous_close=analysis.get("previous_close"),
        support=analysis.get("support"),
        resistance=analysis.get("resistance"),
        verdict_score=analysis.get("verdict_score") or analysis.get("score"),
        regime=analysis.get("regime"),
        momentum_9m=analysis.get("momentum_9m"),
        minute_of_day=minute_of_day,
        ih_state=ih_state,
        rr_state=rr_state,
        data_age_seconds=data_age_seconds,
    )
    story = build_story(inputs)
    story_text = " ".join(story.sentences) if story.has_content() else None

    tiles = build_tile_state(
        verdict_score=float(analysis.get("verdict_score") or 0),
        verdict_ema=float(analysis.get("verdict_ema") or 0),
        spot=float(analysis.get("spot_price") or 0),
        support=int(analysis.get("support") or 0),
        resistance=int(analysis.get("resistance") or 0),
        momentum_9m=float(analysis.get("momentum_9m") or 0),
        ih_state=ih_state,
        rr_state=rr_state,
    )
    return story_text, [asdict(t) for t in tiles]
```

Then inside `fetch_and_analyze`, after `_attach_ih_inputs` completes and before `save_analysis(...)` is called, add:

```python
# Generate narrative + tile payloads for the dashboard
story_text, tile_payloads = self._build_story_and_tiles(analysis, data_age_seconds=0)
```

Pass `story_text=story_text` into the existing `save_analysis(...)` call. Add tile payloads and story text to the existing `emit_payload`:

```python
emit_payload["story_text"] = story_text
emit_payload["tiles"] = tile_payloads
```

Add two new emits after the existing `socketio.emit("oi_update", emit_payload)` line:

```python
self.socketio.emit("story_update", {"story_text": story_text})
self.socketio.emit("tiles_update", {"tiles": tile_payloads})
```

- [ ] **Step 13.4: Run tests**

Run: `uv run python -m pytest tests/test_scheduler_story.py -v`
Expected: both tests pass.

- [ ] **Step 13.5: Run full suite to catch regressions**

Run: `uv run python -m pytest tests/ -q`
Expected: baseline count + all Task-1-through-13 new tests pass. Investigate any regression in pre-existing tests before committing.

- [ ] **Step 13.6: Commit**

```bash
git add monitoring/scheduler.py tests/test_scheduler_story.py
git commit -m "feat(scheduler): generate story+tiles each cycle, emit socketio updates"
```

---

## Task 14: Include `story_text` in `/api/latest`

**Files:**
- Modify: `api/market.py` (extend `_enrich_analysis`)
- Modify: `tests/test_api_story.py`

- [ ] **Step 14.1: Write failing test**

Append to `tests/test_api_story.py`:

```python
def test_api_latest_includes_story_text(client):
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
        story_text="Market is drifting up. Put sellers defend 23100.",
    )
    response = client.get("/api/latest")
    assert response.status_code == 200
    data = response.json
    assert "story_text" in data
    assert data["story_text"].startswith("Market is drifting")
```

- [ ] **Step 14.2: Run — likely passes already if get_latest_analysis returns the column**

Run: `uv run python -m pytest tests/test_api_story.py::test_api_latest_includes_story_text -v`
Expected: may already pass if `get_latest_analysis` returns `story_text` automatically (SQLite `SELECT *` patterns). If it fails, move to Step 14.3. If it already passes, skip to 14.4.

- [ ] **Step 14.3: Ensure `_enrich_analysis` preserves `story_text` (if the test above failed)**

In `api/market.py:19-42`, the `_enrich_analysis` function currently mutates but preserves existing keys. Only modify if keys are being dropped. Inspect the path: run this one-liner to print raw keys:

```bash
uv run python -c "from db.legacy import get_latest_analysis; print(sorted((get_latest_analysis() or {}).keys()))"
```

If `story_text` is missing from raw `get_latest_analysis()` output, fix `get_latest_analysis` (in `db/legacy.py`) to include it in its SELECT / row-to-dict conversion. Exact fix depends on how `get_latest_analysis` currently builds its dict — inspect that function and ensure the new column is added to its column list.

- [ ] **Step 14.4: Run tests**

Run: `uv run python -m pytest tests/test_api_story.py -v`
Expected: all pass.

- [ ] **Step 14.5: Commit**

```bash
git add api/market.py db/legacy.py tests/test_api_story.py
git commit -m "feat(api): include story_text in /api/latest payload"
```

---

## Task 15: `ih_group_update` SocketIO event on state transitions

**Files:**
- Modify: `strategies/intraday_hunter.py`
- Modify: `tests/test_strategies/test_ih_story_state.py`

- [ ] **Step 15.1: Write failing test**

Append to `tests/test_strategies/test_ih_story_state.py`:

```python
def test_ih_emits_group_update_on_open(monkeypatch):
    """When a new signal group is created, the strategy should push an ih_group_update."""
    from unittest.mock import MagicMock
    from strategies.intraday_hunter import IntradayHunterStrategy

    sio = MagicMock()
    s = _make_strategy()
    s.socketio = sio  # optional — strategy may fetch from scheduler
    # Simulate a transition (exact trigger depends on existing lifecycle)
    s._emit_group_update()  # see Step 15.3
    sio.emit.assert_called_once()
    args, _kwargs = sio.emit.call_args
    assert args[0] == "ih_group_update"
```

- [ ] **Step 15.2: Run — expect AttributeError**

Run: `uv run python -m pytest tests/test_strategies/test_ih_story_state.py::test_ih_emits_group_update_on_open -v`
Expected: `_emit_group_update` missing.

- [ ] **Step 15.3: Implement `_emit_group_update` on the strategy**

Add to `IntradayHunterStrategy` in `strategies/intraday_hunter.py`:

```python
def _emit_group_update(self):
    """Push the current IH group state to dashboard clients."""
    sio = getattr(self, "socketio", None)
    if sio is None:
        return
    state = self.story_state()
    sio.emit("ih_group_update", {
        "state": state.state.value,
        "group_id": state.group_id,
        "positions": state.positions,
        "agent_verdict": state.agent_verdict,
        "day_bias": state.day_bias,
    })
```

Then add calls to `self._emit_group_update()` at these lifecycle points in the same file:
1. Immediately after a new signal group is created (inside `create_trade` or equivalent — locate the line that assigns `signal_group_id` and add the emit call after the trade is persisted).
2. After a position exits (inside the SL/TGT/TIME_EXIT close block).
3. When the strategy arms or disarms an E-detector (inside the engine-gating logic).

Use `grep`-style search:

```bash
uv run python -c "import inspect, strategies.intraday_hunter as m; print(inspect.getsourcefile(m))"
```

Then search for `signal_group_id` within that file. Add `self._emit_group_update()` immediately after each write to trade state.

Also ensure the strategy's `__init__` accepts / stores a reference to `socketio`. The scheduler already passes it when instantiating strategies — verify by checking `monitoring/scheduler.py` where `IntradayHunterStrategy(...)` is constructed; if it doesn't already pass `socketio`, pass it now. Add to `__init__`:

```python
def __init__(self, ..., socketio=None):
    ...
    self.socketio = socketio
```

- [ ] **Step 15.4: Run tests**

Run: `uv run python -m pytest tests/test_strategies/test_ih_story_state.py -v`
Expected: all tests pass, including the new one.

- [ ] **Step 15.5: Run full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: no regressions.

- [ ] **Step 15.6: Commit**

```bash
git add strategies/intraday_hunter.py monitoring/scheduler.py tests/test_strategies/test_ih_story_state.py
git commit -m "feat(ih): emit ih_group_update SocketIO event on lifecycle transitions"
```

---

## Task 16: Manual end-to-end verification

This is a **gated verification task** (not test-driven) — run these steps against a live app before declaring Plan 1 done.

- [ ] **Step 16.1: Start the app**

Run: `uv run python app.py`
Wait for: `INFO app Scheduler started` and `INFO app Starting Flask-SocketIO server on http://localhost:5000`.

- [ ] **Step 16.2: Verify endpoints respond with expected shapes**

In a separate terminal:

```bash
curl -s http://localhost:5000/api/story | jq
curl -s http://localhost:5000/api/tiles | jq
curl -s http://localhost:5000/api/ih/group | jq
curl -s http://localhost:5000/api/multi-index | jq
curl -s http://localhost:5000/api/latest | jq '.story_text'
```

Expected:
- `/api/story` → `{"sentences": [...], "warning": null, "timestamp": "..."}`
- `/api/tiles` → `{"tiles": [{"slot": 1, ...}, {"slot": 2, ...}, {"slot": 3, ...}, {"slot": 4, ...}]}`
- `/api/ih/group` → `{"state": "waiting", "positions": [], ...}`
- `/api/multi-index` → `{"NIFTY": <float or null>, "BANKNIFTY": ..., ...}`
- `/api/latest .story_text` → non-null string once a cycle has run (trigger via `curl http://localhost:5000/api/refresh`).

- [ ] **Step 16.3: Verify SocketIO events fire**

Open `http://localhost:5000` in a browser. In devtools console, run:

```javascript
const sock = io();
sock.on("story_update", d => console.log("story_update", d));
sock.on("tiles_update", d => console.log("tiles_update", d));
sock.on("ih_group_update", d => console.log("ih_group_update", d));
```

Then trigger a refresh: `curl http://localhost:5000/api/refresh`.
Expected: `story_update` and `tiles_update` log in the browser console.

- [ ] **Step 16.4: Commit (optional — if any polish fixes emerged)**

Only if you fixed anything during 16.1–16.3.

---

## Self-Review Checklist

Before handing Plan 1 off as complete, re-read it against the spec and confirm:

**Spec coverage (Section-by-Section):**
- [ ] Spec Section 4.1 (Module + contract) → Task 1 + Task 4 ✓
- [ ] Spec Section 4.2 (Sentence structure) → Task 4 ✓
- [ ] Spec Section 4.3 (Inputs consumed) → Task 4 (`StoryInputs`) ✓
- [ ] Spec Section 4.4 (Template catalogue) → Task 3 ✓
- [ ] Spec Section 4.5 (Variant selection) → Task 3 ✓
- [ ] Spec Section 4.6 (Failure modes + actions) → Task 4 (partial: STALE_DATA, REGIME_UNKNOWN, ANALYSIS_INCOMPLETE). **NOTE:** the rest of the failure modes (Kite token, tick feed, agent timeout, day bias pending, unhandled) surface in Plan 3 (frontend) where they can read live health state. Out of scope for Plan 1 — flagged here for Plan 3.
- [ ] Spec Section 4.7 (Persistence) → Task 6 ✓
- [ ] Spec Section 4.8 (Testing) → Tests in each task ✓
- [ ] Spec Section 5.1 (Four fixed slots + mood values) → Task 2 (mood classifier), Task 5 (tile builder) ✓
- [ ] Spec Section 5.2 (Slot 2 per IH state) → Task 5 ✓
- [ ] Spec Section 5.3 (Slot 4 per state) → Task 5 ✓
- [ ] Spec Section 5.4 (Tile contract) → Task 5 (`TileState` dataclass) ✓
- [ ] Spec Section 7.1 (Backend additions) → Tasks 1–8 ✓
- [ ] Spec Section 7.2 (New API endpoints) → Tasks 9–12 ✓
- [ ] Spec Section 7.3 (SocketIO events) → Task 13 (story_update, tiles_update) + Task 15 (ih_group_update) ✓
- [ ] Spec Section 7.4 (Frontend structure) → Out of scope for Plan 1 (Plans 2/3/4).

**Placeholder scan:** No TBD / TODO / "similar to Task N" / empty code blocks.

**Type consistency:** `IHStoryState`, `RRStoryState`, `Story`, `Warning`, `TileState` — same names and field names used in all tasks that reference them.

**Test coverage target (spec Section 8):** ≥95% on `analysis/narrative.py` and `analysis/tile_state.py`. Measure:

```bash
uv run python -m pytest tests/test_narrative.py tests/test_tile_state.py --cov=analysis.narrative --cov=analysis.tile_state --cov-report=term-missing
```

---

## Done for Plan 1

- 16 tasks. ~60 TDD steps. Estimated 4–8 hours of focused work.
- Output: backend story engine + tile classifier + four APIs + SocketIO events, all test-covered.
- Next: Plan 2 (visual design system) can run in parallel since it touches CSS only and has no dependency on this plan.
