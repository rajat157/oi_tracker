"""Trade history API routes."""

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from db.trade_repo import TradeRepository

bp = Blueprint("trades", __name__)

_repo = TradeRepository()


@bp.route("/api/scalp-trades")
def api_scalp_trades():
    """Get historical scalper trades."""
    days = request.args.get("days", 30, type=int)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    trades = _repo._fetch_all(
        "SELECT * FROM scalp_trades WHERE created_at >= ? ORDER BY created_at DESC",
        (cutoff,),
    )
    return jsonify({"trades": trades})


@bp.route("/api/rr-trades")
def api_rr_trades():
    """Get historical Rally Rider trades."""
    days = request.args.get("days", 30, type=int)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    trades = _repo._fetch_all(
        "SELECT * FROM rr_trades WHERE created_at >= ? ORDER BY created_at DESC",
        (cutoff,),
    )
    return jsonify({"trades": trades})
