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
