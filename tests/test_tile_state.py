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
