"""Tests for logging configuration — specifically the httpx/httpcore
token-leak guard.

Background: Telegram bot API URLs carry the bot token as a path segment.
At httpx's default INFO level the full URL (including the token) is emitted
to the log sink. configure_logging() must silence httpx/httpcore to WARNING.
"""

from __future__ import annotations

import logging

from app.core.logging import configure_logging


def _reset_logger(name: str) -> None:
    """Force logger back to unset so configure_logging() has to re-apply."""
    logging.getLogger(name).setLevel(logging.NOTSET)


def test_configure_logging_silences_httpx_info_leak() -> None:
    _reset_logger("httpx")
    _reset_logger("httpcore")
    configure_logging("INFO")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_configure_logging_httpx_still_silenced_at_debug() -> None:
    """Even when the app log level is DEBUG, httpx must stay at WARNING —
    debug-level URL logging would leak tokens just as badly."""
    _reset_logger("httpx")
    _reset_logger("httpcore")
    configure_logging("DEBUG")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_httpx_warning_still_reaches_sink() -> None:
    """WARNING-level records from httpx must NOT be suppressed — otherwise
    real connection errors would go silent."""
    _reset_logger("httpx")
    configure_logging("INFO")
    httpx_logger = logging.getLogger("httpx")
    assert httpx_logger.isEnabledFor(logging.WARNING) is True
    assert httpx_logger.isEnabledFor(logging.ERROR) is True
