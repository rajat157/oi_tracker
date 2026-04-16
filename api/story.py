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

    ts_str = analysis.get("timestamp")

    # Staleness check — if the data is older than MAX_DATA_AGE_SECONDS, warn
    # the client so the dashboard can surface a visible stale-data indicator.
    from datetime import datetime as _dt
    from analysis.narrative import MAX_DATA_AGE_SECONDS
    if ts_str:
        try:
            ts = _dt.fromisoformat(str(ts_str))
            age_seconds = (_dt.now() - ts).total_seconds()
            if age_seconds > MAX_DATA_AGE_SECONDS:
                mins = int(age_seconds // 60)
                return jsonify({
                    "sentences": [],
                    "warning": {
                        "code": "STALE_DATA",
                        "message": f"Last update {mins}m ago.",
                        "severity": "warn",
                    },
                    "timestamp": ts_str,
                }), 200
        except (ValueError, TypeError):
            pass

    story_text = analysis.get("story_text")
    return jsonify({
        "sentences": _split_story_text(story_text),
        "warning": None,
        "timestamp": ts_str,
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


def _extract_analysis_fields(analysis: dict) -> dict:
    """Normalise field names from the tug_of_war analysis blob.

    analyze_tug_of_war() stores values at nested paths; this helper
    flattens them for tile/story consumption.
    """
    sr = analysis.get("primary_sr") or {}
    support_dict = sr.get("support") or {}
    resistance_dict = sr.get("resistance") or {}
    support = support_dict.get("strike") if isinstance(support_dict, dict) else None
    resistance = resistance_dict.get("strike") if isinstance(resistance_dict, dict) else None

    verdict_score = (
        analysis.get("combined_score")
        or analysis.get("verdict_score")
        or analysis.get("score")
        or 0
    )
    verdict_ema = (
        analysis.get("smoothed_score")
        or analysis.get("verdict_ema")
        or analysis.get("ema_score")
        or 0
    )
    momentum_9m = float(
        analysis.get("momentum_score")
        or analysis.get("momentum_9m")
        or analysis.get("price_change_pct")
        or 0
    )
    return {
        "spot": float(analysis.get("spot_price") or 0),
        "support": int(support or 0),
        "resistance": int(resistance or 0),
        "verdict_score": float(verdict_score or 0),
        "verdict_ema": float(verdict_ema or 0),
        "momentum_9m": momentum_9m,
    }


@bp.route("/api/tiles")
def api_tiles():
    """Return the four tile state payloads for the novice view."""
    analysis = get_latest_analysis() or {}
    f = _extract_analysis_fields(analysis)
    tiles = build_tile_state(
        verdict_score=f["verdict_score"],
        verdict_ema=f["verdict_ema"],
        spot=f["spot"],
        support=f["support"],
        resistance=f["resistance"],
        momentum_9m=f["momentum_9m"],
        ih_state=_ih_state(),
        rr_state=_rr_state(),
    )
    return jsonify({"tiles": [asdict(t) for t in tiles]}), 200


_MULTI_INDEX_LABELS = ["NIFTY", "BANKNIFTY", "SENSEX", "HDFCBANK", "KOTAKBANK"]


def _pct_since_open(candle_builder, label: str) -> float | None:
    """Compute % change from today's first candle's open to the latest close."""
    try:
        candles = candle_builder.get_candles(label, interval="1min", count=500)
    except Exception:
        return None
    if not candles:
        return None
    # Filter to today only
    from datetime import date as date_cls
    today = date_cls.today().isoformat()
    todays = [c for c in candles if str(c.get("date", ""))[:10] == today]
    if not todays:
        return None
    open_price = todays[0].get("open")
    last_close = todays[-1].get("close")
    if not open_price or not last_close:
        return None
    return round((last_close - open_price) / open_price * 100, 2)


@bp.route("/api/multi-index")
def api_multi_index():
    """Return % change since today's open for each tracked instrument."""
    sched = _get_scheduler()
    cb = getattr(sched, "candle_builder", None) if sched else None
    result = {}
    for label in _MULTI_INDEX_LABELS:
        result[label] = _pct_since_open(cb, label) if cb else None
    return jsonify(result), 200
