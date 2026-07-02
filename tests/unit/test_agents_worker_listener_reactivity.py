"""Tests for the F3-Folge watchdog surface: _listener_reactivity_check.

The F3 patch (D-217, 2026-05-05) added a ``reactivity_status`` field to
``_summarize_telegram_channel_ingest`` that distinguishes "process alive
but channel silent" (V19 4-day-silence pattern) from "process alive AND
updates flowing". Without surfacing it, the Watchdog agent never warns
on stale_silent — the agent's existing checks only look at
file-mtime-based liveness probes.

These tests pin the contract between canonical_read's reactivity layer
and the watchdog's check list:
- stale_silent → warn finding (with hours-since-last-update detail)
- ok / cold_boot → info (no warning, but visible in run summary)
- no_data / disabled / missing_session → silent (other probes own those)
- probe crash → warn finding, never propagate to caller
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from app.agents.worker import _listener_reactivity_check


def test_stale_silent_surfaces_as_warn_with_hours_detail() -> None:
    summary = {
        "status": "ok",
        "reactivity_status": "stale_silent",
        "last_message_age_seconds": 26 * 3600,
        "messages_since_boot": 0,
    }
    with patch(
        "app.agents.tools.canonical_read._summarize_telegram_channel_ingest",
        return_value=summary,
    ):
        result = _listener_reactivity_check(datetime.now(UTC))
    assert result is not None
    sev, title, detail = result
    assert sev == "warn"
    assert title == "listener_reactivity_stale_silent"
    assert "26.0h" in detail


def test_stale_silent_without_age_falls_back_to_generic_detail() -> None:
    # Edge: heartbeat parsed but last_message_iso was never set
    # (worker booted, never observed a message). Caller still gets a
    # warn — just without a numeric age.
    summary = {
        "status": "ok",
        "reactivity_status": "stale_silent",
        "last_message_age_seconds": None,
    }
    with patch(
        "app.agents.tools.canonical_read._summarize_telegram_channel_ingest",
        return_value=summary,
    ):
        result = _listener_reactivity_check(datetime.now(UTC))
    assert result is not None
    sev, title, _detail = result
    assert sev == "warn"
    assert title == "listener_reactivity_stale_silent"


def test_ok_status_surfaces_as_info_with_message_count() -> None:
    summary = {
        "status": "ok",
        "reactivity_status": "ok",
        "messages_since_boot": 42,
    }
    with patch(
        "app.agents.tools.canonical_read._summarize_telegram_channel_ingest",
        return_value=summary,
    ):
        result = _listener_reactivity_check(datetime.now(UTC))
    assert result is not None
    sev, title, detail = result
    assert sev == "info"
    assert title == "listener_reactivity_ok"
    assert "messages_since_boot=42" in detail


def test_cold_boot_status_surfaces_as_info() -> None:
    summary = {
        "status": "ok",
        "reactivity_status": "cold_boot",
        "messages_since_boot": 0,
    }
    with patch(
        "app.agents.tools.canonical_read._summarize_telegram_channel_ingest",
        return_value=summary,
    ):
        result = _listener_reactivity_check(datetime.now(UTC))
    assert result is not None
    sev, title, _detail = result
    assert sev == "info"
    assert title == "listener_reactivity_cold_boot"


def test_no_data_returns_none_so_no_finding_is_emitted() -> None:
    # Pre-F3 heartbeat file (empty bytes) → reactivity_status=no_data.
    # Existing mtime-based probes already surface liveness, so the
    # reactivity layer stays silent here to avoid double-warning.
    summary = {
        "status": "ok",
        "reactivity_status": "no_data",
    }
    with patch(
        "app.agents.tools.canonical_read._summarize_telegram_channel_ingest",
        return_value=summary,
    ):
        result = _listener_reactivity_check(datetime.now(UTC))
    assert result is None


def test_disabled_listener_returns_none() -> None:
    # Operator explicitly disabled the listener — no reactivity_status
    # key in the summary at all. We must not warn.
    summary = {
        "status": "disabled",
        "reason": "INGESTION_TELEGRAM_CHANNEL_ENABLED=false",
    }
    with patch(
        "app.agents.tools.canonical_read._summarize_telegram_channel_ingest",
        return_value=summary,
    ):
        result = _listener_reactivity_check(datetime.now(UTC))
    assert result is None


def test_missing_session_returns_none() -> None:
    # No session file (listener never authed). canonical_read returns
    # status=missing_session without reactivity_status. Other probes
    # tell the operator to run `telegram-channel setup`.
    summary = {
        "status": "missing_session",
        "reason": "session file not found",
    }
    with patch(
        "app.agents.tools.canonical_read._summarize_telegram_channel_ingest",
        return_value=summary,
    ):
        result = _listener_reactivity_check(datetime.now(UTC))
    assert result is None


def test_probe_exception_becomes_warn_finding_not_crash() -> None:
    # Watchdog must never crash. Any failure inside the probe (settings
    # load, import, unexpected schema) becomes a single warn finding.
    with patch(
        "app.agents.tools.canonical_read._summarize_telegram_channel_ingest",
        side_effect=RuntimeError("settings broken"),
    ):
        result = _listener_reactivity_check(datetime.now(UTC))
    assert result is not None
    sev, title, detail = result
    assert sev == "warn"
    assert title == "listener_reactivity_probe_failed"
    assert "settings broken" in detail
