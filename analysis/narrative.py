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
