"""Statistics API routes."""

from flask import Blueprint, jsonify, current_app

bp = Blueprint("stats", __name__)


def _get_strategy(name: str):
    """Get a strategy instance from the scheduler."""
    scheduler = current_app.config.get("oi_scheduler")
    if scheduler:
        return scheduler.strategies.get(name)
    return None


@bp.route("/api/rr-stats")
def api_rr_stats():
    s = _get_strategy("rally_rider")
    return jsonify(s.get_stats() if s else {})
