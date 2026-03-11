"""Statistics API routes."""

from flask import Blueprint, jsonify, current_app

bp = Blueprint("stats", __name__)


def _get_strategy(name: str):
    """Get a strategy instance from the scheduler."""
    scheduler = current_app.config.get("oi_scheduler")
    if scheduler:
        return scheduler.strategies.get(name)
    return None


@bp.route("/api/scalp-stats")
def api_scalp_stats():
    s = _get_strategy("scalper")
    return jsonify(s.get_stats() if s else {})
