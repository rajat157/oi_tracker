"""Dashboard page routes."""

from flask import Blueprint, render_template

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def dashboard():
    """Render the main dashboard page."""
    return render_template("dashboard.html")


@bp.route("/trades")
def trades_page():
    """Render the trade history page."""
    return render_template("trades.html")
