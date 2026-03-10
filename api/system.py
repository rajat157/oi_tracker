"""System API routes: logs, predictions, V-shape."""

from flask import Blueprint, jsonify, request

from db.legacy import get_logs

bp = Blueprint("system", __name__)


@bp.route("/api/logs")
def api_logs():
    """Get system logs with filtering."""
    level = request.args.get("level")
    component = request.args.get("component")
    hours = request.args.get("hours", 24, type=int)
    limit = request.args.get("limit", 100, type=int)

    logs_list, total = get_logs(level=level, component=component,
                                hours=hours, limit=limit)
    filters_applied = {"hours": hours}
    if level:
        filters_applied["level"] = level
    if component:
        filters_applied["component"] = component

    return jsonify({"logs": logs_list, "total": total,
                    "filters_applied": filters_applied})


@bp.route("/api/v-shape-signals")
def api_v_shape_signals():
    from analysis.v_shape import get_v_shape_signals
    days = request.args.get("days", 30, type=int)
    return jsonify({"signals": get_v_shape_signals(days=days)})


@bp.route("/api/v-shape-stats")
def api_v_shape_stats():
    from analysis.v_shape import get_v_shape_stats
    return jsonify(get_v_shape_stats())


@bp.route("/api/prediction-tree")
def api_prediction_tree():
    from analysis.prediction import get_prediction_state
    return jsonify(get_prediction_state() or {})


@bp.route("/api/prediction-stats")
def api_prediction_stats():
    from analysis.prediction import PredictionEngine
    return jsonify(PredictionEngine().get_prediction_stats())
