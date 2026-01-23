"""
Flask Web Server for OI Tracker Dashboard
Main entry point for the application
"""

from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO

from scheduler import OIScheduler
from database import get_latest_analysis, get_analysis_history, get_latest_snapshot
from oi_analyzer import analyze_tug_of_war


app = Flask(__name__)
app.config["SECRET_KEY"] = "oi_tracker_secret_key"

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
    """Get the latest OI analysis."""
    # Always get fresh analysis from snapshot to ensure scores are calculated
    snapshot = get_latest_snapshot()

    if snapshot and snapshot.get("strikes"):
        # Re-run analysis to get full details with scores
        analysis = analyze_tug_of_war(
            snapshot["strikes"],
            snapshot["spot_price"]
        )
        analysis["timestamp"] = snapshot["timestamp"]
        analysis["expiry_date"] = snapshot["expiry_date"]
        return jsonify(analysis)

    # Fall back to scheduler's cached analysis
    analysis = oi_scheduler.get_last_analysis()
    if analysis:
        return jsonify(analysis)

    # Last resort: database summary (without scores)
    db_analysis = get_latest_analysis()
    if db_analysis:
        return jsonify(db_analysis)

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


@socketio.on("connect")
def handle_connect():
    """Handle client connection."""
    print("Client connected")
    # Send latest data to newly connected client
    analysis = oi_scheduler.get_last_analysis()
    if analysis:
        socketio.emit("oi_update", analysis)


@socketio.on("disconnect")
def handle_disconnect():
    """Handle client disconnection."""
    print("Client disconnected")


@socketio.on("request_refresh")
def handle_refresh_request():
    """Handle manual refresh request from client."""
    print("Client requested refresh")
    oi_scheduler.trigger_now()


def start_app(debug: bool = False, port: int = 5000):
    """Start the application."""
    import threading

    print("=" * 50)
    print("  NIFTY OI Tracker Dashboard")
    print("=" * 50)
    print(f"\n  Server starting on http://localhost:{port}")
    print("  Press Ctrl+C to stop\n")

    # Start the scheduler in a separate thread after a short delay
    def start_scheduler():
        import time
        time.sleep(2)  # Wait for server to be ready
        print("\n  Starting data fetcher (first fetch may take 30-60 seconds)...")
        oi_scheduler.start(interval_minutes=3)

    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    # Run the Flask app with SocketIO
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    start_app(debug=True, port=5000)
