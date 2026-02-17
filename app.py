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
        # Static self_learning status (self-learner removed - using fixed strategy params)
        analysis["self_learning"] = {
            "should_trade": True,
            "is_paused": False,
            "ema_accuracy": 0,
            "consecutive_errors": 0,
            "is_stale": False
        }

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

        # Add sell trade data
        try:
            from selling_tracker import get_active_sell_setup
            active_sell = get_active_sell_setup()
            if active_sell and snapshot and snapshot.get("strikes"):
                strike_d = snapshot["strikes"].get(active_sell["strike"], {})
                opt = active_sell["option_type"]
                cur_prem = strike_d.get("ce_ltp" if opt == "CE" else "pe_ltp", 0)
                if cur_prem and cur_prem > 0:
                    sell_pnl = ((active_sell["entry_premium"] - cur_prem) / active_sell["entry_premium"]) * 100
                    active_sell["current_premium"] = cur_prem
                    active_sell["current_pnl"] = sell_pnl
            analysis["active_sell_trade"] = active_sell
        except Exception:
            analysis["active_sell_trade"] = None

        # Add dessert trade data
        try:
            from dessert_tracker import get_active_dessert, DessertTracker
            active_dessert = get_active_dessert()
            if active_dessert and snapshot and snapshot.get("strikes"):
                strike_d = snapshot["strikes"].get(active_dessert["strike"], {})
                cur_prem = strike_d.get("pe_ltp", 0)
                if cur_prem and cur_prem > 0:
                    d_pnl = ((cur_prem - active_dessert["entry_premium"]) / active_dessert["entry_premium"]) * 100
                    active_dessert["current_premium"] = cur_prem
                    active_dessert["current_pnl"] = d_pnl
            analysis["active_dessert_trade"] = active_dessert
            analysis["dessert_stats"] = DessertTracker().get_dessert_stats()
        except Exception:
            analysis["active_dessert_trade"] = None
            analysis["dessert_stats"] = {}

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
    """Self-learning system removed - returns static response."""
    return jsonify({"status": "disabled", "message": "Self-learner removed - using fixed strategy params"})


@app.route("/api/learning-status")
def api_learning_status():
    """Self-learning system removed - returns static response."""
    return jsonify({"status": "disabled", "message": "Self-learner removed - using fixed strategy params"})


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


@app.route("/api/sell-trades")
def api_sell_trades():
    """Get selling trade history."""
    from selling_tracker import get_connection
    days = request.args.get("days", 30, type=int)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM sell_trade_setups
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at DESC
        """, (f"-{days} days",))
        trades = [dict(r) for r in cursor.fetchall()]
    return jsonify({"trades": trades})


@app.route("/api/sell-stats")
def api_sell_stats():
    """Get selling trade statistics."""
    from selling_tracker import SellingTracker
    tracker = SellingTracker()
    return jsonify(tracker.get_sell_stats())


@app.route("/api/dessert-trades")
def api_dessert_trades():
    """Get dessert trade history."""
    from database import get_connection
    days = request.args.get("days", 30, type=int)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM dessert_trades
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at DESC
        """, (f"-{days} days",))
        trades = [dict(r) for r in cursor.fetchall()]
    return jsonify({"trades": trades})


@app.route("/api/dessert-stats")
def api_dessert_stats():
    """Get dessert trade statistics."""
    from dessert_tracker import DessertTracker
    tracker = DessertTracker()
    return jsonify(tracker.get_dessert_stats())


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
        # Static self_learning status (self-learner removed)
        analysis["self_learning"] = {
            "should_trade": True,
            "is_paused": False,
            "ema_accuracy": 0,
            "consecutive_errors": 0,
            "is_stale": False
        }

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
        # Static self_learning status (self-learner removed)
        analysis["self_learning"] = {
            "should_trade": True,
            "is_paused": False,
            "ema_accuracy": 0,
            "consecutive_errors": 0,
            "is_stale": False
        }

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


# ===== KITE AUTH ROUTES =====

@app.route('/kite/login')
def kite_login():
    """Redirect to Kite login page."""
    import os
    api_key = os.environ.get('KITE_API_KEY', '')
    if not api_key:
        return jsonify({"error": "KITE_API_KEY not configured"}), 400
    login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    from flask import redirect
    return redirect(login_url)


@app.route('/kite/callback')
def kite_callback():
    """Capture request_token from Kite redirect and exchange for access_token."""
    import os, hashlib, requests as req
    
    request_token = request.args.get('request_token', '')
    if not request_token:
        return """<html><body style="font-family:sans-serif;text-align:center;padding:50px;">
        <h1 style="color:red;">Login Failed</h1>
        <p>No request token received from Kite.</p>
        <a href="/kite/login">Try Again</a>
        </body></html>"""
    
    api_key = os.environ.get('KITE_API_KEY', '')
    api_secret = os.environ.get('KITE_API_SECRET', '')
    
    if not api_secret:
        # Show manual token entry
        return f"""<html><body style="font-family:sans-serif;text-align:center;padding:50px;">
        <h1 style="color:orange;">API Secret Missing</h1>
        <p>Add KITE_API_SECRET to .env and restart.</p>
        <p>Request token: <code>{request_token}</code></p>
        </body></html>"""
    
    # Exchange request_token for access_token
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()
    
    try:
        resp = req.post("https://api.kite.trade/session/token", data={
            'api_key': api_key,
            'request_token': request_token,
            'checksum': checksum
        }, timeout=10)
        result = resp.json()
        
        if result.get('status') == 'success':
            access_token = result['data']['access_token']
            
            # Save token
            from kite_auth import save_token
            save_token(access_token)
            
            return f"""<html><body style="font-family:sans-serif;text-align:center;padding:50px;">
            <h1 style="color:green;">Login Successful!</h1>
            <p>Access token saved. Iron Pulse can now place orders automatically.</p>
            <p>Token: <code>{access_token[:10]}...</code></p>
            <p><a href="/">Back to Dashboard</a></p>
            <script>setTimeout(function(){{ window.close(); }}, 3000);</script>
            </body></html>"""
        else:
            return f"""<html><body style="font-family:sans-serif;text-align:center;padding:50px;">
            <h1 style="color:red;">Token Exchange Failed</h1>
            <p>{result.get('message', 'Unknown error')}</p>
            <a href="/kite/login">Try Again</a>
            </body></html>"""
    except Exception as e:
        return f"""<html><body style="font-family:sans-serif;text-align:center;padding:50px;">
        <h1 style="color:red;">Error</h1>
        <p>{str(e)}</p>
        <a href="/kite/login">Try Again</a>
        </body></html>"""


@app.route('/kite/status')
def kite_status():
    """Check if Kite is authenticated for today."""
    from kite_auth import load_token
    token = load_token()
    return jsonify({
        "authenticated": bool(token),
        "token_preview": f"{token[:10]}..." if token else None
    })


@app.route('/kite/save-token', methods=['POST'])
def kite_save_token():
    """Manually save an access token (fallback)."""
    data = request.get_json()
    token = data.get('token', '').strip()
    if not token:
        return jsonify({"error": "No token provided"}), 400
    
    from kite_auth import save_token
    save_token(token)
    return jsonify({"status": "success", "message": "Token saved"})


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
