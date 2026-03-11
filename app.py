"""
Flask Web Server for OI Tracker Dashboard
Main entry point for the application
"""

import logging

from flask import Flask, request
from flask_socketio import SocketIO

from api.dashboard import bp as dashboard_bp
from api.market import bp as market_bp, _enrich_analysis
from api.trades import bp as trades_bp
from api.stats import bp as stats_bp
from api.system import bp as system_bp
from api.kite_auth import bp as kite_bp
from db.legacy import get_latest_analysis
from monitoring.scheduler import OIScheduler
from core.logger import get_logger

log = get_logger("app")


app = Flask(__name__)
app.config["SECRET_KEY"] = "oi_tracker_secret_key"

# Suppress default Werkzeug request logging — we route through OILogger instead
logging.getLogger("werkzeug").setLevel(logging.ERROR)


@app.after_request
def log_request(response):
    log.debug("HTTP request", method=request.method, path=request.path, status=response.status_code)
    return response

# Register API blueprints
app.register_blueprint(dashboard_bp)
app.register_blueprint(market_bp)
app.register_blueprint(trades_bp)
app.register_blueprint(stats_bp)
app.register_blueprint(system_bp)
app.register_blueprint(kite_bp)

# Initialize SocketIO with threading (more reliable on Windows)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Initialize scheduler with socketio for real-time updates
oi_scheduler = OIScheduler(socketio=socketio)

# Store scheduler in app config for blueprint access
app.config["oi_scheduler"] = oi_scheduler


# ===== SocketIO Handlers (must stay in app.py) =====

@socketio.on("connect")
def handle_connect():
    """Handle client connection — send latest enriched analysis."""
    log.info("Client connected")
    analysis = get_latest_analysis()
    if analysis:
        socketio.emit("oi_update", _enrich_analysis(analysis))


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
    """Handle request for latest analysis from client."""
    from flask_socketio import emit
    analysis = get_latest_analysis()
    if analysis:
        emit("oi_update", _enrich_analysis(analysis))


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
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, use_reloader=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    start_app(debug=False, port=5000)
