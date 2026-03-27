"""Statistics API routes."""

from flask import Blueprint, jsonify, current_app

bp = Blueprint("stats", __name__)


def _get_strategy(name: str):
    """Get a strategy instance from the scheduler."""
    scheduler = current_app.config.get("oi_scheduler")
    if scheduler:
        return scheduler.strategies.get(name)
    return None


@bp.route("/api/rr-stats")
def api_rr_stats():
    s = _get_strategy("rally_rider")
    return jsonify(s.get_stats() if s else {})


@bp.route("/api/rr-regime")
def api_rr_regime():
    """Get current RR regime classification (cached daily)."""
    s = _get_strategy("rally_rider")
    if not s:
        return jsonify({})
    try:
        from config import RRConfig
        regime = s.engine.classify_regime(RRConfig())
        cfg = s.engine.get_regime_params(regime)
        return jsonify({
            "name": regime,
            "direction": cfg.get("direction", "BOTH"),
            "time_start": f"{cfg['time_start'].hour}:{cfg['time_start'].minute:02d}",
            "time_end": f"{cfg['time_end'].hour}:{cfg['time_end'].minute:02d}",
            "max_trades": cfg.get("max_trades", 3),
            "signals": list(cfg.get("signals", set())),
        })
    except Exception:
        return jsonify({})
