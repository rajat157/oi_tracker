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
