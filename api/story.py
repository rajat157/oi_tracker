"""Story / tiles / IH group / multi-index API endpoints."""

from flask import Blueprint, jsonify

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
