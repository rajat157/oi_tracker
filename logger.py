"""
Centralized logging system for OI Tracker.

Provides structured logging with:
- Consistent timestamp format
- Log levels (DEBUG, INFO, WARNING, ERROR)
- Console output with color coding
- Database persistence for analysis
- Component tagging
"""

import json
import sys
import uuid
from datetime import datetime
from typing import Optional

# ANSI color codes for console output
COLORS = {
    "DEBUG": "\033[36m",    # Cyan
    "INFO": "\033[32m",     # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",    # Red
    "RESET": "\033[0m",     # Reset
    "DIM": "\033[2m",       # Dim
}

# Check if running in a terminal that supports colors
COLORS_ENABLED = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

# Generate a unique session ID for this application run
SESSION_ID = str(uuid.uuid4())[:8]

# Minimum log level to display (can be changed at runtime)
MIN_LOG_LEVEL = "DEBUG"
LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}


def _colorize(text: str, color: str) -> str:
    """Apply ANSI color to text if colors are enabled."""
    if COLORS_ENABLED and color in COLORS:
        return f"{COLORS[color]}{text}{COLORS['RESET']}"
    return text


def _format_timestamp(dt: datetime) -> str:
    """Format timestamp consistently."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class OILogger:
    """
    Logger class for OI Tracker components.

    Usage:
        from logger import get_logger
        log = get_logger("scheduler")

        log.info("Fetching OI data")
        log.warning("Rate limit approaching", requests=45, limit=50)
        log.error("Failed to parse response", error=str(e))
    """

    def __init__(self, component: str, db_enabled: bool = True):
        """
        Initialize logger for a component.

        Args:
            component: Name of the component (e.g., scheduler, trade_tracker)
            db_enabled: Whether to save logs to database (default True)
        """
        self.component = component
        self.db_enabled = db_enabled
        self.session_id = SESSION_ID

    def debug(self, message: str, **details):
        """Log a debug message."""
        self._log("DEBUG", message, details)

    def info(self, message: str, **details):
        """Log an info message."""
        self._log("INFO", message, details)

    def warning(self, message: str, **details):
        """Log a warning message."""
        self._log("WARNING", message, details)

    def error(self, message: str, **details):
        """Log an error message."""
        self._log("ERROR", message, details)

    def _log(self, level: str, message: str, details: dict):
        """
        Internal method to handle logging.

        1. Format and print to console
        2. Store in database (if enabled)
        """
        # Check log level
        if LOG_LEVELS.get(level, 0) < LOG_LEVELS.get(MIN_LOG_LEVEL, 0):
            return

        timestamp = datetime.now()

        # Format console output
        self._print_to_console(timestamp, level, message, details)

        # Save to database
        if self.db_enabled:
            self._save_to_db(timestamp, level, message, details)

    def _print_to_console(self, timestamp: datetime, level: str, message: str, details: dict):
        """Print formatted log to console."""
        # Format: 2026-02-04 10:15:32 | INFO    | scheduler    | Message
        ts_str = _format_timestamp(timestamp)
        level_padded = level.ljust(7)
        component_padded = self.component.ljust(12)

        # Apply colors
        ts_colored = _colorize(ts_str, "DIM")
        level_colored = _colorize(level_padded, level)

        # Build output line
        line = f"{ts_colored} | {level_colored} | {component_padded} | {message}"

        # Add details if present
        if details:
            details_str = " | " + ", ".join(f"{k}={v}" for k, v in details.items())
            line += _colorize(details_str, "DIM")

        print(line)

    def _save_to_db(self, timestamp: datetime, level: str, message: str, details: dict):
        """Save log entry to database."""
        try:
            # Import here to avoid circular imports
            from database import save_log

            details_json = json.dumps(details) if details else None
            save_log(
                timestamp=timestamp,
                level=level,
                component=self.component,
                message=message,
                details=details_json,
                session_id=self.session_id
            )
        except Exception:
            # Don't let logging errors crash the application
            pass


# Cache of logger instances by component
_loggers: dict = {}


def get_logger(component: str, db_enabled: bool = True) -> OILogger:
    """
    Factory function to get or create a logger for a component.

    Args:
        component: Name of the component
        db_enabled: Whether to enable database logging

    Returns:
        OILogger instance for the component
    """
    key = f"{component}:{db_enabled}"
    if key not in _loggers:
        _loggers[key] = OILogger(component, db_enabled)
    return _loggers[key]


def set_min_log_level(level: str):
    """
    Set minimum log level to display.

    Args:
        level: Minimum level (DEBUG, INFO, WARNING, ERROR)
    """
    global MIN_LOG_LEVEL
    if level.upper() in LOG_LEVELS:
        MIN_LOG_LEVEL = level.upper()


if __name__ == "__main__":
    # Test the logger
    log = get_logger("test")

    log.debug("This is a debug message", key="value")
    log.info("This is an info message")
    log.warning("This is a warning", confidence=75, threshold=80)
    log.error("This is an error", error="Something went wrong")

    print("\n--- Testing with DB disabled ---")
    log2 = get_logger("test_no_db", db_enabled=False)
    log2.info("This won't be saved to DB")
