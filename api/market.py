"""Market data API routes: /api/latest, /api/history, /api/refresh, /api/market-status."""

from flask import Blueprint, jsonify, request

from db.legacy import (
    get_latest_analysis, get_analysis_history, get_latest_snapshot,
)

bp = Blueprint("market", __name__)


def _get_strategies() -> dict:
    """Get strategies dict from the scheduler stored in app config."""
    from flask import current_app
    scheduler = current_app.config.get("oi_scheduler")
    return scheduler.strategies if scheduler else {}


def _enrich_analysis(analysis: dict) -> dict:
    """Add trade data, V-shape status, chart history to an analysis dict."""
    analysis["self_learning"] = {
        "should_trade": True, "is_paused": False,
        "ema_accuracy": 0, "consecutive_errors": 0, "is_stale": False,
    }

    snapshot = get_latest_snapshot()
    strikes = snapshot.get("strikes") if snapshot else None
    strategies = _get_strategies()

    # V-shape
    try:
        from analysis.v_shape import get_v_shape_status
        analysis["v_shape_status"] = get_v_shape_status() or {"signal_level": "NONE"}
    except Exception:
        analysis["v_shape_status"] = {"signal_level": "NONE"}

    # Chart history
    from datetime import date as date_cls
    today_str = date_cls.today().strftime("%Y-%m-%d")
    analysis["chart_history"] = get_analysis_history(limit=30, date=today_str)

    return analysis


@bp.route("/api/latest")
def api_latest():
    """Get the latest OI analysis from database."""
    analysis = get_latest_analysis()
    if analysis:
        return jsonify(_enrich_analysis(analysis))
    return jsonify({"error": "No data available yet"}), 404


@bp.route("/api/history")
def api_history():
    """Get historical analysis data for charts."""
    date = request.args.get("date")
    if not date:
        from datetime import date as date_cls
        date = date_cls.today().strftime("%Y-%m-%d")
    return jsonify(get_analysis_history(limit=200, date=date))


@bp.route("/api/market-status")
def api_market_status():
    """Get current market status."""
    from flask import current_app
    scheduler = current_app.config.get("oi_scheduler")
    if scheduler:
        return jsonify(scheduler.get_market_status())
    return jsonify({"is_open": False})


@bp.route("/api/refresh")
def api_refresh():
    """Manually trigger a data refresh."""
    from flask import current_app
    scheduler = current_app.config.get("oi_scheduler")
    if scheduler:
        try:
            scheduler.trigger_now()
            return jsonify({"status": "success", "message": "Refresh triggered"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Scheduler not initialized"}), 500


@bp.route("/api/learning-report")
def api_learning_report():
    return jsonify({"status": "disabled", "message": "Self-learner removed"})


@bp.route("/api/learning-status")
def api_learning_status():
    return jsonify({"status": "disabled", "message": "Self-learner removed"})
