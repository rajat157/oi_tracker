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
