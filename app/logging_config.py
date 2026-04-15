from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.config import get_settings


class LoggingConfigurator:
    """Configures structured logging for the application.

    Outputs JSON in production and human-readable console logs in
    development. Every log line automatically includes a timestamp,
    log level, and any context variables bound to the current request.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        # Resolve the log level string (e.g. "INFO") to a logging constant
        self.level = getattr(logging, self.settings.log_level.upper(), logging.INFO)

    def configure(self) -> None:
        """Apply logging configuration. Should be called once at startup."""
        self._configure_stdlib()
        self._configure_structlog()

    def _configure_stdlib(self) -> None:
        # Route stdlib logging through stdout so it stays with structlog output
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=self.level,
        )

    def _configure_structlog(self) -> None:
        processors = self._build_processors()

        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(self.level),
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

    def _build_processors(self) -> list[Any]:
        """Build the processor chain. Renderer is chosen based on environment."""
        processors: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]

        if self.settings.app_env == "development":
            # Pretty-printed coloured output for local development
            processors.append(structlog.dev.ConsoleRenderer())
        else:
            # Machine-readable JSON for production log aggregators
            processors.append(structlog.processors.JSONRenderer())

        return processors


def configure_logging() -> None:
    """Entry point called by main.py at startup."""
    LoggingConfigurator().configure()
