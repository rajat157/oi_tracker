"""Structured logging with console output + DB persistence."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_log import SystemLog

# ANSI color codes
COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "RESET": "\033[0m",
    "DIM": "\033[2m",
}
COLORS_ENABLED = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}

# Unique session ID per application run
SESSION_ID = str(uuid.uuid4())[:8]

# Module-level minimum log level
_min_level = "DEBUG"


def set_min_log_level(level: str) -> None:
    global _min_level
    if level.upper() in LOG_LEVELS:
        _min_level = level.upper()


def _colorize(text: str, color: str) -> str:
    if COLORS_ENABLED and color in COLORS:
        return f"{COLORS[color]}{text}{COLORS['RESET']}"
    return text


class OILogger:
    """Logger for a single component. Console + optional async DB persistence."""

    def __init__(self, component: str, session_factory=None) -> None:
        self.component = component
        self._session_factory = session_factory
        self.session_id = SESSION_ID

    def debug(self, message: str, **details) -> None:
        self._log("DEBUG", message, details)

    def info(self, message: str, **details) -> None:
        self._log("INFO", message, details)

    def warning(self, message: str, **details) -> None:
        self._log("WARNING", message, details)

    def error(self, message: str, **details) -> None:
        self._log("ERROR", message, details)

    def _log(self, level: str, message: str, details: dict) -> None:
        if LOG_LEVELS.get(level, 0) < LOG_LEVELS.get(_min_level, 0):
            return
        timestamp = datetime.now(timezone.utc)
        self._print_to_console(timestamp, level, message, details)
        if self._session_factory:
            # Fire-and-forget DB write — schedule in background
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._save_to_db(timestamp, level, message, details))
            except RuntimeError:
                pass  # No running loop — skip DB persistence

    def _print_to_console(
        self, timestamp: datetime, level: str, message: str, details: dict
    ) -> None:
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        level_padded = level.ljust(7)
        comp_padded = self.component.ljust(14)
        ts_colored = _colorize(ts_str, "DIM")
        level_colored = _colorize(level_padded, level)
        line = f"{ts_colored} | {level_colored} | {comp_padded} | {message}"
        if details:
            details_str = " | " + ", ".join(f"{k}={v}" for k, v in details.items())
            line += _colorize(details_str, "DIM")
        print(line, flush=True)

    async def _save_to_db(
        self, timestamp: datetime, level: str, message: str, details: dict
    ) -> None:
        try:
            async with self._session_factory() as session:
                log_entry = SystemLog(
                    timestamp=timestamp,
                    level=level,
                    component=self.component,
                    message=message,
                    details=details if details else None,
                    session_id=self.session_id,
                )
                session.add(log_entry)
                await session.commit()
        except Exception:
            pass  # Never let logging crash the app


# ── Factory ────────────────────────────────────────────

_loggers: dict[str, OILogger] = {}
_session_factory = None


def configure_logging(session_factory=None, min_level: str = "DEBUG") -> None:
    """Call once at startup to set the DB session factory and min level."""
    global _session_factory
    _session_factory = session_factory
    set_min_log_level(min_level)


def get_logger(component: str) -> OILogger:
    """Get or create a logger for the given component."""
    if component not in _loggers:
        _loggers[component] = OILogger(component, session_factory=_session_factory)
    return _loggers[component]
