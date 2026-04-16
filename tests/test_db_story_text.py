"""Test story_text column lifecycle in analysis_history."""

from datetime import datetime

from db.legacy import save_analysis, get_connection, get_latest_analysis


def test_save_analysis_accepts_and_persists_story_text():
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23190.0,
        atm_strike=23200,
        total_call_oi=1000,
        total_put_oi=1100,
        call_oi_change=100,
        put_oi_change=200,
        verdict="BULLISH",
        expiry_date="2026-04-21",
        story_text="Market is drifting up. Put sellers defend 23100.",
    )
    latest = get_latest_analysis()
    assert latest is not None
    assert latest.get("story_text") == "Market is drifting up. Put sellers defend 23100."


def test_save_analysis_story_text_optional():
    # Backwards compatible — omitting story_text still works
    save_analysis(
        timestamp=datetime.now(),
        spot_price=23200.0,
        atm_strike=23200,
        total_call_oi=1000,
        total_put_oi=1000,
        call_oi_change=0,
        put_oi_change=0,
        verdict="NEUTRAL",
        expiry_date="2026-04-21",
    )
    latest = get_latest_analysis()
    # story_text should be None when not provided
    assert "story_text" in latest
    assert latest["story_text"] is None or latest["story_text"] == ""


def test_story_text_column_exists_after_init():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(analysis_history)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "story_text" in cols
