"""Trade history API routes for all strategies."""

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from db.legacy import get_trade_history
from db.trade_repo import TradeRepository

bp = Blueprint("trades", __name__)

_repo = TradeRepository()


def _get_trades_for_table(table: str):
    """Generic handler for trade history endpoints."""
    days = request.args.get("days", 30, type=int)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    trades = _repo._fetch_all(
        f"SELECT * FROM {table} WHERE created_at >= ? ORDER BY created_at DESC",
        (cutoff,),
    )
    return jsonify({"trades": trades})


@bp.route("/api/trades")
def api_trades():
    """Get historical Iron Pulse trades with pagination and filters."""
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    days = request.args.get("days", 30, type=int)
    status = request.args.get("status")
    direction = request.args.get("direction")
    trades = get_trade_history(limit=limit, offset=offset, days=days,
                               status_filter=status, direction_filter=direction)
    return jsonify({"trades": trades, "has_more": len(trades) == limit,
                    "offset": offset, "limit": limit})


@bp.route("/api/sell-trades")
def api_sell_trades():
    return _get_trades_for_table("sell_trade_setups")


@bp.route("/api/dessert-trades")
def api_dessert_trades():
    return _get_trades_for_table("dessert_trades")


@bp.route("/api/momentum-trades")
def api_momentum_trades():
    return _get_trades_for_table("momentum_trades")


@bp.route("/api/pa-trades")
def api_pa_trades():
    return _get_trades_for_table("pa_trades")


@bp.route("/api/scalp-trades")
def api_scalp_trades():
    return _get_trades_for_table("scalp_trades")
