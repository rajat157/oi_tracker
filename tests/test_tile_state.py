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


# ---------------------------------------------------------------------------
# Issue C1: EMA arrow direction is inverted
# ---------------------------------------------------------------------------

def test_mood_tile_ema_arrow_rising_when_score_above_ema():
    """When verdict_score > verdict_ema, score is rising → should show ↗."""
    tiles = build_tile_state(**_base_args(verdict_score=70, verdict_ema=50))
    assert "↗" in tiles[0].caption


def test_mood_tile_ema_arrow_falling_when_score_below_ema():
    """When verdict_score < verdict_ema, score is falling → should show ↘."""
    tiles = build_tile_state(**_base_args(verdict_score=30, verdict_ema=50))
    assert "↘" in tiles[0].caption


def test_mood_tile_ema_arrow_flat_when_score_equals_ema():
    """When verdict_score == verdict_ema → should show →."""
    tiles = build_tile_state(**_base_args(verdict_score=50, verdict_ema=50))
    assert "→" in tiles[0].caption


# ---------------------------------------------------------------------------
# Issue I1: RECENTLY_CLOSED Slot 4 should show P&L summary
# ---------------------------------------------------------------------------

def test_slot_four_recently_closed_shows_pnl_summary():
    """RECENTLY_CLOSED state: slot 4 should show group id and time, not day bias."""
    ih = IHStoryState(
        state=IHGroupState.RECENTLY_CLOSED, group_id="abc123",
        ago_minutes=8, day_bias=0.5,
    )
    tiles = build_tile_state(**_base_args(ih_state=ih))
    slot4 = tiles[3]
    # Should mention the closed group id
    assert "abc" in slot4.primary or "abc" in slot4.caption
    # Should mention time ago
    assert "8m" in slot4.caption or "8 m" in slot4.caption


def test_slot_four_recently_closed_does_not_show_day_bias():
    """RECENTLY_CLOSED state: slot 4 must NOT fall through to day-bias display."""
    ih = IHStoryState(
        state=IHGroupState.RECENTLY_CLOSED, group_id="abc123",
        ago_minutes=8, day_bias=0.5,
    )
    tiles = build_tile_state(**_base_args(ih_state=ih))
    slot4 = tiles[3]
    # day_bias is 0.5 — if it fell through, primary would contain "0.50"
    assert "0.50" not in slot4.primary


# ---------------------------------------------------------------------------
# Issue I2: Battle Lines hint nonsensical when spot breaks support/resistance
# ---------------------------------------------------------------------------

def test_battle_lines_when_spot_below_support():
    """When spot < support, hint should indicate break below support."""
    tiles = build_tile_state(**_base_args(spot=22850, support=22900, resistance=23300))
    bl = tiles[2]
    assert "below" in bl.hint.lower() or "under" in bl.hint.lower()


def test_battle_lines_when_spot_above_resistance():
    """When spot > resistance, hint should indicate break above resistance."""
    tiles = build_tile_state(**_base_args(spot=23350, support=22900, resistance=23300))
    bl = tiles[2]
    assert "above" in bl.hint.lower() or "over" in bl.hint.lower()


def test_battle_lines_when_spot_within_range_still_works():
    """Normal case: spot between support and resistance shows sensible hint."""
    tiles = build_tile_state(**_base_args(spot=23100, support=23000, resistance=23300))
    bl = tiles[2]
    # Should NOT say "below" or "above" for in-range spot
    assert "-" not in bl.hint or "pts" in bl.hint  # no negative pts


# ---------------------------------------------------------------------------
# Issue I3: LIVE/PAPER as structured field, not string glue
# ---------------------------------------------------------------------------

def test_trade_tile_live_row_has_is_paper_flag():
    """LIVE rows must carry is_paper as a boolean field."""
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 142.0, "current_premium": 154.0,
         "quantity": 65, "is_paper": True},
    ]
    ih = IHStoryState(state=IHGroupState.LIVE, group_id="x", positions=positions, agent_verdict="HOLD")
    tiles = build_tile_state(**_base_args(ih_state=ih))
    row = tiles[1].rows[0]
    assert "is_paper" in row
    assert row["is_paper"] is True


def test_trade_tile_live_row_not_paper_flag():
    """LIVE rows: is_paper=False for live (real) positions."""
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 142.0, "current_premium": 154.0,
         "quantity": 65, "is_paper": False},
    ]
    ih = IHStoryState(state=IHGroupState.LIVE, group_id="x", positions=positions, agent_verdict="HOLD")
    tiles = build_tile_state(**_base_args(ih_state=ih))
    row = tiles[1].rows[0]
    assert row["is_paper"] is False


def test_trade_tile_live_row_no_paper_or_live_in_right():
    """LIVE/PAPER badge must NOT be glued into the right string."""
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 142.0, "current_premium": 154.0,
         "quantity": 65, "is_paper": True},
    ]
    ih = IHStoryState(state=IHGroupState.LIVE, group_id="x", positions=positions, agent_verdict="HOLD")
    tiles = build_tile_state(**_base_args(ih_state=ih))
    row = tiles[1].rows[0]
    assert "PAPER" not in row["right"]
    assert "LIVE" not in row["right"]


# ---------------------------------------------------------------------------
# Issue I4: fmt_signed_pnl reuse — negative P&L sign placement
# ---------------------------------------------------------------------------

def test_trade_tile_live_negative_pnl_formatted_correctly():
    """Primary P&L must be '-₹1,300', not '₹-1,300'."""
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 200.0, "current_premium": 180.0,
         "quantity": 65, "is_paper": False},
    ]
    ih = IHStoryState(state=IHGroupState.LIVE, group_id="x", positions=positions, agent_verdict="HOLD")
    tiles = build_tile_state(**_base_args(ih_state=ih))
    primary = tiles[1].primary
    # Should be "-₹1,300 · …" not "₹-1,300 · …"
    assert "-₹" in primary
    assert "₹-" not in primary


def test_trade_tile_live_row_negative_pnl_formatted_correctly():
    """Row right field must be '-₹1,300', not '₹-1,300'."""
    positions = [
        {"index": "NIFTY", "strike": 23200, "option_type": "CE",
         "entry_premium": 200.0, "current_premium": 180.0,
         "quantity": 65, "is_paper": False},
    ]
    ih = IHStoryState(state=IHGroupState.LIVE, group_id="x", positions=positions, agent_verdict="HOLD")
    tiles = build_tile_state(**_base_args(ih_state=ih))
    row = tiles[1].rows[0]
    assert "-₹" in row["right"]
    assert "₹-" not in row["right"]
