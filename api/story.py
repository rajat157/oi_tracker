"""Story / tiles / IH group / multi-index API endpoints."""

from dataclasses import asdict

from flask import Blueprint, jsonify

from analysis.narrative import IHStoryState, IHGroupState, RRStoryState
from analysis.tile_state import build_tile_state
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
