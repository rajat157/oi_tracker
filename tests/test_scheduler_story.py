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


def test_build_story_and_tiles_with_real_analysis_dict_shape():
    """Drive _build_story_and_tiles with a dict shaped like real tug_of_war output."""
    sched = _make_scheduler_for_test()
    real_shape = {
        "spot_price": 23190.0,
        "combined_score": 58.0,
        "smoothed_score": 55.0,
        "primary_sr": {
            "support": {"strike": 23100, "oi": 12345},
            "resistance": {"strike": 23300, "oi": 9876},
        },
        "momentum_score": 0.3,
        "market_regime": {"regime": "TRENDING_UP"},
        # open_price and previous_close NOT included — extractor should fall back
    }
    story_text, tiles = sched._build_story_and_tiles(real_shape, data_age_seconds=30)
    # Battle lines tile (slot 3) MUST show real support/resistance, not zeros
    bl = tiles[2]
    assert "23100" in bl["primary"], f"Expected 23100 in battle-lines tile, got: {bl['primary']}"
    assert "23300" in bl["primary"], f"Expected 23300 in battle-lines tile, got: {bl['primary']}"


def test_extract_story_fields_normalises_nested_keys():
    """_extract_story_fields maps combined_score, primary_sr, market_regime correctly."""
    sched = _make_scheduler_for_test()
    analysis = {
        "spot_price": 23190.0,
        "combined_score": 58.0,
        "smoothed_score": 55.0,
        "primary_sr": {
            "support": {"strike": 23100},
            "resistance": {"strike": 23300},
        },
        "momentum_score": 0.3,
        "market_regime": {"regime": "trending_up"},  # lowercase as tug_of_war returns it
    }
    fields = sched._extract_story_fields(analysis)
    assert fields["verdict_score"] == 58.0
    assert fields["verdict_ema"] == 55.0
    assert fields["support"] == 23100
    assert fields["resistance"] == 23300
    assert fields["momentum_9m"] == 0.3
    assert fields["regime"] == "TRENDING_UP"  # normalised to uppercase
    assert fields["spot"] == 23190.0
