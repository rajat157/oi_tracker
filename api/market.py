"""Market data API routes: /api/latest, /api/history, /api/refresh, /api/market-status."""

from flask import Blueprint, jsonify, request

from db.legacy import (
    get_latest_analysis, get_analysis_history, get_latest_snapshot,
)

bp = Blueprint("market", __name__)


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
        "support_ref": setup.get("support_at_creation"),
        "resistance_ref": setup.get("resistance_at_creation"),
        "max_pain": setup.get("max_pain_at_creation"),
    }


def _add_active_trade_pnl(trade: dict, strikes_data: dict, is_selling: bool = False) -> dict:
    """Add current_premium and current_pnl to an active trade dict."""
    if not trade or not strikes_data:
        return trade
    strike_data = strikes_data.get(trade["strike"], {})
    key = "pe_ltp" if trade["option_type"] == "PE" else "ce_ltp"
    cur = strike_data.get(key, 0)
    if cur and cur > 0:
        if is_selling:
            pnl = ((trade["entry_premium"] - cur) / trade["entry_premium"]) * 100
        else:
            pnl = ((cur - trade["entry_premium"]) / trade["entry_premium"]) * 100
        trade["current_premium"] = cur
        trade["current_pnl"] = pnl
    return trade


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

    # Iron Pulse
    try:
        ip = strategies.get("iron_pulse")
        if ip and strikes:
            analysis["active_trade"] = ip.get_active_setup_with_pnl(strikes)
            analysis["trade_stats"] = ip.get_stats()
        else:
            analysis["active_trade"] = None
            analysis["trade_stats"] = {}
    except Exception:
        analysis["active_trade"] = None
        analysis["trade_stats"] = {}

    # Selling
    try:
        sell = strategies.get("selling")
        analysis["active_sell_trade"] = _add_active_trade_pnl(
            sell.get_active(), strikes, is_selling=True) if sell else None
    except Exception:
        analysis["active_sell_trade"] = None

    # Dessert
    try:
        dessert = strategies.get("dessert")
        if dessert:
            analysis["active_dessert_trade"] = _add_active_trade_pnl(
                dessert.get_active(), strikes)
            analysis["dessert_stats"] = dessert.get_stats()
        else:
            analysis["active_dessert_trade"] = None
            analysis["dessert_stats"] = {}
    except Exception:
        analysis["active_dessert_trade"] = None
        analysis["dessert_stats"] = {}

    # Momentum
    try:
        mom = strategies.get("momentum")
        if mom:
            analysis["active_momentum_trade"] = _add_active_trade_pnl(
                mom.get_active(), strikes)
            analysis["momentum_stats"] = mom.get_stats()
        else:
            analysis["active_momentum_trade"] = None
            analysis["momentum_stats"] = {}
    except Exception:
        analysis["active_momentum_trade"] = None
        analysis["momentum_stats"] = {}

    # PA (PulseRider)
    try:
        pr = strategies.get("pulse_rider")
        if pr:
            analysis["active_pa_trade"] = _add_active_trade_pnl(
                pr.get_active(), strikes)
            analysis["pa_stats"] = pr.get_stats()
        else:
            analysis["active_pa_trade"] = None
            analysis["pa_stats"] = {}
    except Exception:
        analysis["active_pa_trade"] = None
        analysis["pa_stats"] = {}

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
