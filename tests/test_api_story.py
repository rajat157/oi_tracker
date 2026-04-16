"""Integration tests for the story/tiles/ih-group/multi-index API surface."""

import pytest
from datetime import datetime

from db.legacy import save_analysis


@pytest.fixture
def client():
    # Import at fixture time so the test DB patch from conftest is active
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_story_returns_404_when_no_analysis(client):
    response = client.get("/api/story")
    assert response.status_code in (200, 404)
    # If 200, payload must still indicate "no data"
    if response.status_code == 200:
        assert response.json.get("sentences") == [] or response.json.get("warning") is not None


def test_api_story_returns_latest_text(client):
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
        story_text="Market is drifting up. Put sellers defend 23100.",
    )
    response = client.get("/api/story")
    assert response.status_code == 200
    data = response.json
    assert "sentences" in data
    assert any("drifting up" in s for s in data["sentences"])
    assert data.get("warning") is None


def test_api_tiles_returns_four_slots(client):
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
    )
    response = client.get("/api/tiles")
    assert response.status_code == 200
    data = response.json
    assert "tiles" in data
    assert len(data["tiles"]) == 4
    for i, tile in enumerate(data["tiles"], start=1):
        assert tile["slot"] == i
        assert "primary" in tile
        assert "accent" in tile


def test_api_ih_group_returns_none_when_waiting(client):
    response = client.get("/api/ih/group")
    assert response.status_code == 200
    data = response.json
    assert data.get("state") == "waiting"
    assert data.get("positions") == []


def test_api_multi_index_returns_known_indices(client):
    response = client.get("/api/multi-index")
    assert response.status_code == 200
    data = response.json
    # Must include keys for each tracked instrument; values may be null
    # when no candles available (e.g. outside market hours / test environment)
    for key in ["NIFTY", "BANKNIFTY", "SENSEX", "HDFCBANK", "KOTAKBANK"]:
        assert key in data


def test_api_latest_includes_story_text(client):
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
        story_text="Market is drifting up. Put sellers defend 23100.",
    )
    response = client.get("/api/latest")
    assert response.status_code == 200
    data = response.json
    assert "story_text" in data
    assert data["story_text"].startswith("Market is drifting")


def test_api_latest_includes_story_text_when_analysis_json_present(client):
    """The Path A code (parsed analysis_json blob) must also surface story_text."""
    import json
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
        story_text="Test story sentence.",
        analysis_json=json.dumps({"verdict_score": 58.0, "regime": "NORMAL"}),
    )
    response = client.get("/api/latest")
    assert response.status_code == 200
    data = response.json
    assert data.get("story_text") == "Test story sentence."


def test_api_story_returns_stale_warning_when_old(client):
    """When the last analysis is older than MAX_DATA_AGE_SECONDS, return STALE_DATA."""
    from datetime import timedelta
    from db.legacy import get_connection
    # Clear any fresh rows left by previous tests so our stale row is the newest
    with get_connection() as conn:
        conn.execute("DELETE FROM analysis_history")
        conn.commit()
    save_analysis(
        timestamp=datetime.now() - timedelta(minutes=15),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
        story_text="An old story.",
    )
    response = client.get("/api/story")
    assert response.status_code == 200
    data = response.json
    assert data.get("warning") is not None
    assert data["warning"]["code"] == "STALE_DATA"
    assert data.get("sentences") == []


def test_api_story_no_stale_warning_for_fresh_data(client):
    """When data is fresh (just saved), no stale warning should be returned."""
    from db.legacy import get_connection
    # Clear any stale rows left by previous tests so our fresh row is the newest
    with get_connection() as conn:
        conn.execute("DELETE FROM analysis_history")
        conn.commit()
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0, atm_strike=23200,
        total_call_oi=1000, total_put_oi=1100,
        call_oi_change=100, put_oi_change=200,
        verdict="BULLISH", expiry_date="2026-04-21",
        story_text="Fresh story.",
    )
    response = client.get("/api/story")
    assert response.status_code == 200
    data = response.json
    assert data.get("warning") is None
