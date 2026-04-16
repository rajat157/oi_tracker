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
