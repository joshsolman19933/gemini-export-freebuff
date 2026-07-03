"""
Shared logging configuration for the Gemini Chat Exporter project.

Configures dual handlers (console + file) with structured formatting.
Log level is controlled via the LOG_LEVEL environment variable (default: INFO).

Usage:
    from gemini_export.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Export started")
    logger.warning("Rate limit approaching")
    logger.error("Export failed", exc_info=True)
"""

import logging
import os
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_FILE = Path("gemini-export.log")
_LOG_INITIALIZED = False


def _ensure_logging_configured() -> None:
    """Configure root logger once with console + file handlers."""
    global _LOG_INITIALIZED
    if _LOG_INITIALIZED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Root captures everything; handlers filter

    # Console handler: human-readable, shows the configured level and above
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_LOG_FORMAT, _LOG_DATE_FORMAT))
    root.addHandler(console)

    # File handler: always DEBUG, captures everything for debugging
    try:
        file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _LOG_DATE_FORMAT))
        root.addHandler(file_handler)
    except OSError:
        pass  # File logging is best-effort; don't block startup

    # Suppress noisy third-party loggers
    for noisy in ("urllib3", "httpcore", "httpx", "openai", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _LOG_INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given module name.

    Ensures the logging framework is initialized on first call.
    """
    _ensure_logging_configured()
    return logging.getLogger(name)
