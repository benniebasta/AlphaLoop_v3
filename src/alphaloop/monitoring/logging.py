"""Structured logging setup using structlog."""

from __future__ import annotations

import logging
import sys

import structlog


def setup_logging(log_level: str = "INFO", json_output: bool = False) -> None:
    """
    Configure structured logging for the application.

    Args:
        log_level: Python log level name (DEBUG, INFO, WARNING, ERROR).
        json_output: If True, output JSON lines. Otherwise, human-readable.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging to structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )
