"""Tests for API blueprints using Flask test client."""

import pytest
from flask import Flask

from api.dashboard import bp as dashboard_bp
from api.market import bp as market_bp
from api.trades import bp as trades_bp
from api.stats import bp as stats_bp
from api.system import bp as system_bp
from api.kite_auth import bp as kite_bp


@pytest.fixture
def app():
    """Create a minimal Flask app with all blueprints registered."""
    app = Flask(__name__, template_folder="../../templates")
    app.config["TESTING"] = True
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(market_bp)
    app.register_blueprint(trades_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(kite_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestBlueprintRegistration:
    def test_all_blueprints_register(self, app):
        """Verify all blueprints register without conflicts."""
        bp_names = [bp.name for bp in app.blueprints.values()]
        assert "dashboard" in bp_names
        assert "market" in bp_names
        assert "trades" in bp_names
        assert "stats" in bp_names
        assert "system" in bp_names
        assert "kite_auth" in bp_names

    def test_route_count(self, app):
        """Verify expected number of routes registered."""
        rules = [r.rule for r in app.url_map.iter_rules() if r.rule != "/static/<path:filename>"]
        # All the routes we defined
        expected_routes = [
            "/", "/trades",
            "/api/latest", "/api/history", "/api/market-status", "/api/refresh",
            "/api/learning-report", "/api/learning-status",
            "/api/rr-trades",
            "/api/rr-stats",
            "/api/logs", "/api/v-shape-signals", "/api/v-shape-stats",
            "/kite/login", "/kite/callback", "/kite/status", "/kite/save-token",
        ]
        for route in expected_routes:
            assert route in rules, f"Missing route: {route}"


class TestDashboardBlueprint:
    def test_dashboard_route_exists(self, app):
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/" in rules

    def test_trades_route_exists(self, app):
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/trades" in rules


class TestMarketBlueprint:
    def test_learning_report_disabled(self, client):
        resp = client.get("/api/learning-report")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "disabled"

    def test_learning_status_disabled(self, client):
        resp = client.get("/api/learning-status")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "disabled"

    def test_market_status_no_scheduler(self, client):
        resp = client.get("/api/market-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["is_open"] is False


class TestKiteAuthBlueprint:
    def test_kite_login_no_key(self, client):
        resp = client.get("/kite/login")
        assert resp.status_code == 400

    def test_kite_callback_no_token(self, client):
        resp = client.get("/kite/callback")
        assert resp.status_code == 200
        assert b"Login Failed" in resp.data

    def test_kite_save_token_no_body(self, client):
        resp = client.post("/kite/save-token", json={})
        assert resp.status_code == 400
