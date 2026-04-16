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
