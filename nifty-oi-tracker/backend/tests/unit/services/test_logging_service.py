"""Tests for LoggingService."""

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

from app.services.logging_service import (
    OILogger,
    get_logger,
    configure_logging,
    set_min_log_level,
    LOG_LEVELS,
    SESSION_ID,
    _loggers,
)


class TestOILogger:
    def setup_method(self):
        _loggers.clear()

    def test_console_output_format(self, capsys):
        logger = OILogger("test_comp")
        logger.info("hello world")
        captured = capsys.readouterr()
        assert "test_comp" in captured.out
        assert "hello world" in captured.out
        assert "INFO" in captured.out

    def test_console_output_with_details(self, capsys):
        logger = OILogger("scheduler")
        logger.info("Fetching data", spot=23500, expiry="2026-02-26")
        captured = capsys.readouterr()
        assert "spot=23500" in captured.out
        assert "expiry=2026-02-26" in captured.out

    def test_all_log_levels(self, capsys):
        logger = OILogger("engine")
        set_min_log_level("DEBUG")
        logger.debug("debug msg")
        logger.info("info msg")
        logger.warning("warn msg")
        logger.error("error msg")
        captured = capsys.readouterr()
        assert "DEBUG" in captured.out
        assert "INFO" in captured.out
        assert "WARNING" in captured.out
        assert "ERROR" in captured.out

    def test_level_filtering(self, capsys):
        set_min_log_level("WARNING")
        logger = OILogger("test")
        logger.debug("should not appear")
        logger.info("should not appear")
        logger.warning("should appear")
        captured = capsys.readouterr()
        assert "should not appear" not in captured.out
        assert "should appear" in captured.out
        # Reset
        set_min_log_level("DEBUG")

    def test_session_id_set(self):
        logger = OILogger("test")
        assert logger.session_id == SESSION_ID
        assert len(SESSION_ID) == 8

    @pytest.mark.asyncio
    async def test_db_persistence(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        logger = OILogger("test", session_factory=mock_factory)
        await logger._save_to_db(
            datetime.now(timezone.utc), "INFO", "test msg", {"key": "val"}
        )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_db_error_does_not_raise(self):
        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(side_effect=Exception("db fail"))
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        logger = OILogger("test", session_factory=mock_factory)
        # Should not raise
        await logger._save_to_db(
            datetime.now(timezone.utc), "ERROR", "boom", {}
        )


class TestGetLogger:
    def setup_method(self):
        _loggers.clear()

    def test_returns_same_instance(self):
        a = get_logger("scheduler")
        b = get_logger("scheduler")
        assert a is b

    def test_different_components(self):
        a = get_logger("scheduler")
        b = get_logger("alerts")
        assert a is not b
        assert a.component == "scheduler"
        assert b.component == "alerts"

    def test_configure_logging_sets_level(self):
        configure_logging(min_level="WARNING")
        from app.services.logging_service import _min_level
        assert _min_level == "WARNING"
        # Reset
        configure_logging(min_level="DEBUG")
