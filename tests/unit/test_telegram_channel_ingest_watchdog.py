"""Tests for _summarize_telegram_channel_ingest — liveness watchdog that
surfaces via /status and the daily operator summary.

Motivation: on 2026-04-21 the MTProto listener silently died and 6
premium signals never entered the pipeline (2026-04-23/-24). The
watchdog must flag this within a bounded staleness window instead of
days. These tests cover the status transitions the /status consumer
depends on: ok / stale / missing_session / disabled, plus the rule
that either the PID file or the session file being fresh counts as
alive.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agents.tools.canonical_read import _summarize_telegram_channel_ingest


def _settings(*, enabled: bool = True, session_rel: str = "artifacts/telegram_channel.session"):
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.session_path = session_rel
    holder = MagicMock()
    holder.telegram_channel_ingest = cfg
    return holder


def _touch(path: Path, age_seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"session-stub")
    past = time.time() - age_seconds
    import os
    os.utime(path, (past, past))


def test_disabled_short_circuits_and_never_touches_filesystem(tmp_path: Path) -> None:
    # When ingest is turned off we must not report stale/missing — the
    # operator intentionally opted out. Status: disabled, no filesystem IO.
    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=False),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            session_path_override=str(tmp_path / "nonexistent.session"),
            pid_file_override=str(tmp_path / "nonexistent.pid"),
        )
    assert result["status"] == "disabled"
    assert "INGESTION_TELEGRAM_CHANNEL_ENABLED" in str(result["reason"])


def test_missing_session_file_reported_explicitly(tmp_path: Path) -> None:
    # A missing session file means the listener was never authed — distinct
    # from "stale" (was alive, died). /status must tell the operator to
    # run `telegram-channel setup`, not just restart the listener.
    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=True),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            session_path_override=str(tmp_path / "never_authed.session"),
            pid_file_override=str(tmp_path / "no.pid"),
        )
    assert result["status"] == "missing_session"
    assert "setup" in str(result["reason"])


def test_fresh_session_within_threshold_reports_ok(tmp_path: Path) -> None:
    # Session file was touched 5 minutes ago, threshold 30 min → ok.
    session = tmp_path / "artifacts" / "fresh.session"
    _touch(session, age_seconds=300)  # 5 min

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=True),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
        )
    assert result["status"] == "ok"
    assert result["age_seconds"] >= 300
    assert result["age_seconds"] < 1800
    assert result["last_seen_source"] == "session"
    assert result["pid_file_exists"] is False


def test_stale_session_beyond_threshold_reports_stale(tmp_path: Path) -> None:
    # This is the 2026-04-21..24 scenario: session 3 days old.
    session = tmp_path / "artifacts" / "stale.session"
    _touch(session, age_seconds=3 * 24 * 3600)

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=True),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
        )
    assert result["status"] == "stale"
    assert result["age_seconds"] > 1800


def test_replay_fields_default_to_none_when_marker_absent(tmp_path: Path) -> None:
    # No marker file exists yet → all replay_* fields are None.
    # Operators must see "never attempted" distinctly from "0 messages
    # recovered" (which is a legitimate empty-replay outcome).
    session = tmp_path / "artifacts" / "fresh.session"
    _touch(session, age_seconds=60)

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=True),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            replay_marker_override=str(tmp_path / "no_marker.json"),
        )
    assert result["replay_attempted_at"] is None
    assert result["replay_processed_count"] is None
    assert result["replay_scanned_count"] is None


def test_replay_fields_surfaced_from_marker(tmp_path: Path) -> None:
    # Marker file from worker boot → fields exposed verbatim. This is the
    # signal V4 is supposed to surface: gap-replay actually ran, processed
    # this many messages.
    import json as _json
    session = tmp_path / "artifacts" / "fresh.session"
    _touch(session, age_seconds=60)
    marker = tmp_path / "replay.json"
    marker.write_text(
        _json.dumps({
            "attempted_at": "2026-04-26T12:00:00+00:00",
            "scanned": 17,
            "processed": 15,
            "skipped_no_checkpoint": 0,
        }),
        encoding="utf-8",
    )

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=True),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            replay_marker_override=str(marker),
        )
    assert result["replay_attempted_at"] == "2026-04-26T12:00:00+00:00"
    assert result["replay_processed_count"] == 15
    assert result["replay_scanned_count"] == 17


def test_replay_marker_corrupt_json_does_not_crash(tmp_path: Path) -> None:
    # A corrupted marker file must not break the watchdog — the listener
    # could still be alive. Replay fields fall back to None.
    session = tmp_path / "artifacts" / "fresh.session"
    _touch(session, age_seconds=60)
    marker = tmp_path / "broken.json"
    marker.write_text("{not valid json", encoding="utf-8")

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=True),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            replay_marker_override=str(marker),
        )
    assert result["status"] == "ok"
    assert result["replay_attempted_at"] is None
    assert result["replay_processed_count"] is None


def test_fresh_pid_file_overrides_stale_session(tmp_path: Path) -> None:
    # Just-restarted listener: PID file brand new, session file still
    # stale because Telethon hasn't processed an update yet. Liveness
    # must count PID file as alive — otherwise the operator sees "stale"
    # seconds after a successful start.
    session = tmp_path / "artifacts" / "stale.session"
    _touch(session, age_seconds=3 * 24 * 3600)
    pid_file = tmp_path / ".telegram_listener.pid"
    pid_file.write_text("12345")  # fresh mtime

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=True),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(pid_file),
        )
    assert result["status"] == "ok"
    assert result["last_seen_source"] == "pid_file"
    assert result["pid_file_exists"] is True


def test_stale_session_and_stale_pid_still_stale(tmp_path: Path) -> None:
    # Both signals old → stale. Guards against a bug where max() would
    # pick one without comparing to threshold.
    session = tmp_path / "artifacts" / "s.session"
    pid_file = tmp_path / ".telegram_listener.pid"
    _touch(session, age_seconds=7200)   # 2h
    pid_file.write_text("9999")
    import os
    past = time.time() - 7200
    os.utime(pid_file, (past, past))

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings(enabled=True),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(pid_file),
        )
    assert result["status"] == "stale"
