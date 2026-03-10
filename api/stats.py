"""Statistics API routes for all strategies."""

from flask import Blueprint, jsonify, current_app

bp = Blueprint("stats", __name__)


def _get_strategy(name: str):
    """Get a strategy instance from the scheduler."""
    scheduler = current_app.config.get("oi_scheduler")
    if scheduler:
        return scheduler.strategies.get(name)
    return None


@bp.route("/api/sell-stats")
def api_sell_stats():
    s = _get_strategy("selling")
    return jsonify(s.get_stats() if s else {})


@bp.route("/api/dessert-stats")
def api_dessert_stats():
    s = _get_strategy("dessert")
    return jsonify(s.get_stats() if s else {})


@bp.route("/api/momentum-stats")
def api_momentum_stats():
    s = _get_strategy("momentum")
    return jsonify(s.get_stats() if s else {})


@bp.route("/api/pa-stats")
def api_pa_stats():
    s = _get_strategy("pulse_rider")
    return jsonify(s.get_stats() if s else {})


@bp.route("/api/scalp-stats")
def api_scalp_stats():
    s = _get_strategy("scalper")
    return jsonify(s.get_stats() if s else {})
