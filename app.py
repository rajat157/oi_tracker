"""
Flask Web Server for OI Tracker Dashboard
Main entry point for the application
"""

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

from scheduler import OIScheduler
from database import (
    get_latest_analysis, get_analysis_history, get_latest_snapshot,
    get_active_trade_setup, get_trade_setup_stats, get_trade_history,
    get_logs
)
from logger import get_logger

log = get_logger("app")


app = Flask(__name__)
app.config["SECRET_KEY"] = "oi_tracker_secret_key"


def _get_setup_with_pnl(setup: dict, strikes_data: dict) -> dict:
    """Calculate live P/L for a trade setup."""
    if not setup or not strikes_data:
        return setup

    strike = setup["strike"]
    option_type = setup.get("option_type", "CE" if setup["direction"] == "BUY_CALL" else "PE")
    strike_data = strikes_data.get(strike, {})
    current_premium = strike_data.get(
        "ce_ltp" if option_type == "CE" else "pe_ltp", 0
    )

    # Calculate live P/L
    if setup["status"] == "ACTIVE" and setup.get("activation_premium"):
        activation_premium = setup["activation_premium"]
        live_pnl_pct = ((current_premium - activation_premium) / activation_premium) * 100 if activation_premium else 0
        live_pnl_points = current_premium - activation_premium
    elif setup["status"] == "PENDING":
        entry_premium = setup["entry_premium"]
        live_pnl_pct = ((current_premium - entry_premium) / entry_premium) * 100 if entry_premium else 0
        live_pnl_points = current_premium - entry_premium
    else:
        live_pnl_pct = 0
        live_pnl_points = 0

    return {
        **setup,
        "current_premium": round(current_premium, 2),
        "live_pnl_pct": round(live_pnl_pct, 2),
        "live_pnl_points": round(live_pnl_points, 2),
        # Map database field names to frontend expected names
        "support_ref": setup.get("support_at_creation"),
        "resistance_ref": setup.get("resistance_at_creation"),
        "max_pain": setup.get("max_pain_at_creation"),
    }

# Initialize SocketIO with threading (more reliable on Windows)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Initialize scheduler with socketio for real-time updates
oi_scheduler = OIScheduler(socketio=socketio)


@app.route("/")
def dashboard():
    """Render the main dashboard page."""
    return render_template("dashboard.html")


@app.route("/api/latest")
def api_latest():
    """Get the latest OI analysis from database (single source of truth)."""
    # ALWAYS fetch from database - single source of truth
    analysis = get_latest_analysis()

    if analysis:
        # Add self_learning if missing (for old data stored without it)
        # IMPORTANT: Old analyses load stale data from learned_weights table
        # Always prefer live self-learner status to show current state
        if "self_learning" not in analysis:
            from self_learner import get_self_learner
            learner = get_self_learner()
            status = learner.get_status()
            analysis["self_learning"] = {
                "should_trade": status["signal_tracker"]["should_trade"],
                "is_paused": status["signal_tracker"]["ema_tracker"]["is_paused"],
                "ema_accuracy": round(status["signal_tracker"]["ema_tracker"]["ema_accuracy"] * 100, 1),
                "consecutive_errors": status["signal_tracker"]["ema_tracker"]["consecutive_errors"],
                "is_stale": True  # Mark as potentially stale from learned_weights table
            }
        else:
            # Analysis has embedded self_learning - this is fresh data
            analysis["self_learning"]["is_stale"] = False

        # Add live trade tracker data (needs current snapshot for live P/L)
        snapshot = get_latest_snapshot()
        if snapshot and snapshot.get("strikes"):
            active_setup = get_active_trade_setup()
            if active_setup:
                analysis["active_trade"] = _get_setup_with_pnl(active_setup, snapshot["strikes"])
            else:
                analysis["active_trade"] = None
            analysis["trade_stats"] = get_trade_setup_stats(lookback_days=30)
        else:
            analysis["active_trade"] = None
            analysis["trade_stats"] = get_trade_setup_stats(lookback_days=30)

        # Add chart history for frontend sync (last 30 data points)
        analysis["chart_history"] = get_analysis_history(limit=30)

        return jsonify(analysis)

    return jsonify({"error": "No data available yet"}), 404


@app.route("/api/history")
def api_history():
    """Get historical analysis data for charts."""
    history = get_analysis_history(limit=100)
    return jsonify(history)


@app.route("/api/refresh")
def api_refresh():
    """Manually trigger a data refresh."""
    try:
        oi_scheduler.trigger_now(force=True)
        return jsonify({"status": "success", "message": "Refresh triggered"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/market-status")
def api_market_status():
    """Get current market status."""
    return jsonify(oi_scheduler.get_market_status())


@app.route("/api/learning-report")
def api_learning_report():
    """
    Get the self-learning system's analysis report.

    Returns insights on:
    - Which confidence ranges perform best/worst
    - Which verdicts to trust/skip
    - Overall system health
    """
    from self_learner import get_self_learner
    learner = get_self_learner()

    try:
        report = learner.generate_learning_report()
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/learning-status")
def api_learning_status():
    """
    Get detailed self-learning system status.

    Includes:
    - Confidence optimizer thresholds
    - Verdict analyzer performance
    - EMA accuracy tracking
    """
    from self_learner import get_self_learner
    learner = get_self_learner()

    try:
        status = learner.get_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trades")
def trades_page():
    """Render the trade history page."""
    return render_template("trades.html")


@app.route("/api/trades")
def api_trades():
    """
    Get historical trades with pagination and filters.

    Query params:
        limit: Number of trades to return (default 50)
        offset: Number of trades to skip (default 0)
        days: Number of days to look back (default 30)
        status: Filter by status (WON, LOST, EXPIRED, CANCELLED)
        direction: Filter by direction (BUY_CALL, BUY_PUT)
    """
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    days = request.args.get("days", 30, type=int)
    status = request.args.get("status")
    direction = request.args.get("direction")

    trades = get_trade_history(
        limit=limit,
        offset=offset,
        days=days,
        status_filter=status,
        direction_filter=direction
    )

    return jsonify({
        "trades": trades,
        "has_more": len(trades) == limit,
        "offset": offset,
        "limit": limit
    })


@app.route("/api/logs")
def api_logs():
    """
    Get system logs with filtering.

    Query params:
        level: Filter by log level (DEBUG, INFO, WARNING, ERROR)
        component: Filter by component name
        hours: Hours to look back (default 24)
        limit: Max records to return (default 100)
    """
    level = request.args.get("level")
    component = request.args.get("component")
    hours = request.args.get("hours", 24, type=int)
    limit = request.args.get("limit", 100, type=int)

    logs_list, total = get_logs(
        level=level,
        component=component,
        hours=hours,
        limit=limit
    )

    filters_applied = {}
    if level:
        filters_applied["level"] = level
    if component:
        filters_applied["component"] = component
    filters_applied["hours"] = hours

    return jsonify({
        "logs": logs_list,
        "total": total,
        "filters_applied": filters_applied
    })


@socketio.on("connect")
def handle_connect():
    """Handle client connection."""
    log.info("Client connected")
    # Send latest data from database (single source of truth)
    analysis = get_latest_analysis()

    if analysis:
        # Add self_learning if missing (for old data stored without it)
        # IMPORTANT: Old analyses load stale data from learned_weights table
        # Always prefer live self-learner status to show current state
        if "self_learning" not in analysis:
            from self_learner import get_self_learner
            learner = get_self_learner()
            status = learner.get_status()
            analysis["self_learning"] = {
                "should_trade": status["signal_tracker"]["should_trade"],
                "is_paused": status["signal_tracker"]["ema_tracker"]["is_paused"],
                "ema_accuracy": round(status["signal_tracker"]["ema_tracker"]["ema_accuracy"] * 100, 1),
                "consecutive_errors": status["signal_tracker"]["ema_tracker"]["consecutive_errors"],
                "is_stale": True  # Mark as potentially stale from learned_weights table
            }
        else:
            # Analysis has embedded self_learning - this is fresh data
            analysis["self_learning"]["is_stale"] = False

        # Add live trade tracker data
        snapshot = get_latest_snapshot()
        if snapshot and snapshot.get("strikes"):
            active_setup = get_active_trade_setup()
            if active_setup:
                analysis["active_trade"] = _get_setup_with_pnl(active_setup, snapshot["strikes"])
            else:
                analysis["active_trade"] = None
            analysis["trade_stats"] = get_trade_setup_stats(lookback_days=30)
        else:
            analysis["active_trade"] = None
            analysis["trade_stats"] = get_trade_setup_stats(lookback_days=30)

        # Add chart history for frontend sync
        analysis["chart_history"] = get_analysis_history(limit=30)

        socketio.emit("oi_update", analysis)


@socketio.on("disconnect")
def handle_disconnect():
    """Handle client disconnection."""
    log.info("Client disconnected")


@socketio.on("request_refresh")
def handle_refresh_request():
    """Handle manual refresh request from client."""
    log.info("Client requested refresh")
    oi_scheduler.trigger_now()


@socketio.on("request_latest")
def handle_request_latest():
    """Handle request for latest analysis from client (uses database as single source of truth)."""
    from flask_socketio import emit

    # ALWAYS fetch from database - single source of truth
    analysis = get_latest_analysis()

    if analysis:
        # Add self_learning if missing (for old data stored without it)
        # IMPORTANT: Old analyses load stale data from learned_weights table
        # Always prefer live self-learner status to show current state
        if "self_learning" not in analysis:
            from self_learner import get_self_learner
            learner = get_self_learner()
            status = learner.get_status()
            analysis["self_learning"] = {
                "should_trade": status["signal_tracker"]["should_trade"],
                "is_paused": status["signal_tracker"]["ema_tracker"]["is_paused"],
                "ema_accuracy": round(status["signal_tracker"]["ema_tracker"]["ema_accuracy"] * 100, 1),
                "consecutive_errors": status["signal_tracker"]["ema_tracker"]["consecutive_errors"],
                "is_stale": True  # Mark as potentially stale from learned_weights table
            }
        else:
            # Analysis has embedded self_learning - this is fresh data
            analysis["self_learning"]["is_stale"] = False

        # Add live trade tracker data (needs current snapshot for live P/L)
        snapshot = get_latest_snapshot()
        if snapshot and snapshot.get("strikes"):
            active_setup = get_active_trade_setup()
            if active_setup:
                analysis["active_trade"] = _get_setup_with_pnl(active_setup, snapshot["strikes"])
            else:
                analysis["active_trade"] = None
            analysis["trade_stats"] = get_trade_setup_stats(lookback_days=30)
        else:
            analysis["active_trade"] = None
            analysis["trade_stats"] = get_trade_setup_stats(lookback_days=30)

        # Add chart history for frontend sync (last 30 data points)
        analysis["chart_history"] = get_analysis_history(limit=30)

        emit("oi_update", analysis)


@socketio.on("set_force_fetch")
def handle_set_force_fetch(data):
    """Handle force fetch toggle from client."""
    enabled = data.get("enabled", False)
    oi_scheduler.set_force_enabled(enabled)
    log.info("Force auto-fetch toggled", enabled=enabled)


def start_app(debug: bool = False, port: int = 5000):
    """Start the application."""
    import threading

    log.info("=" * 50)
    log.info("NIFTY OI Tracker Dashboard")
    log.info("=" * 50)
    log.info(f"Server starting on http://localhost:{port}")

    # Start the scheduler in a separate thread after a short delay
    def start_scheduler():
        import time
        time.sleep(2)  # Wait for server to be ready
        log.info("Starting data fetcher (first fetch may take 30-60 seconds)")
        oi_scheduler.start(interval_minutes=3)

    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    # Run the Flask app with SocketIO
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    start_app(debug=True, port=5000)
