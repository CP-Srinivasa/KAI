"""Tests for the heartbeat-based liveness path in
``_summarize_telegram_channel_ingest`` (D-191 / S-003).

The pre-existing watchdog tests (test_telegram_channel_ingest_watchdog.py)
covered session + PID-file. This file extends that coverage to the third
candidate added for D-191: a dedicated heartbeat file the worker touches
every ~60 s independent of channel chatter.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agents.tools.canonical_read import _summarize_telegram_channel_ingest


def _settings_with_heartbeat(
    *,
    enabled: bool = True,
    heartbeat_path: str = "artifacts/telegram_listener_heartbeat",
    heartbeat_stale_seconds: int = 1800,
):
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.session_path = "artifacts/telegram_channel.session"
    cfg.heartbeat_path = heartbeat_path
    cfg.heartbeat_stale_seconds = heartbeat_stale_seconds
    holder = MagicMock()
    holder.telegram_channel_ingest = cfg
    return holder


def _touch(path: Path, age_seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    past = time.time() - age_seconds
    os.utime(path, (past, past))


def test_fresh_heartbeat_overrides_stale_session(tmp_path: Path) -> None:
    # Silent channel scenario: session was last touched days ago because
    # no message arrived, but the worker is alive and writing the
    # heartbeat every minute. Liveness must read OK from the heartbeat.
    session = tmp_path / "telegram_channel.session"
    _touch(session, age_seconds=3 * 24 * 3600)
    heartbeat = tmp_path / "heartbeat"
    _touch(heartbeat, age_seconds=30)

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings_with_heartbeat(),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            heartbeat_path_override=str(heartbeat),
        )
    assert result["status"] == "ok"
    assert result["last_seen_source"] == "heartbeat"
    assert result["heartbeat_file_exists"] is True


def test_missing_heartbeat_falls_back_to_session(tmp_path: Path) -> None:
    # Heartbeat file simply absent (older deploy, never touched). The
    # function must not crash; it falls back to session/PID candidates.
    session = tmp_path / "telegram_channel.session"
    _touch(session, age_seconds=300)

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings_with_heartbeat(),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            heartbeat_path_override=str(tmp_path / "missing_heartbeat"),
        )
    assert result["status"] == "ok"
    assert result["last_seen_source"] == "session"
    assert result["heartbeat_file_exists"] is False


def test_stale_heartbeat_and_stale_session_reports_stale(tmp_path: Path) -> None:
    # Worker died but heartbeat file still on disk. Both sources stale =>
    # status must be "stale" so the operator gets a real signal.
    session = tmp_path / "telegram_channel.session"
    heartbeat = tmp_path / "heartbeat"
    _touch(session, age_seconds=7200)
    _touch(heartbeat, age_seconds=7200)

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings_with_heartbeat(),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            stale_threshold_seconds=1800,
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            heartbeat_path_override=str(heartbeat),
        )
    assert result["status"] == "stale"
    assert result["age_seconds"] >= 1800


def test_threshold_falls_back_to_settings_when_caller_omits(tmp_path: Path) -> None:
    # When the caller does NOT pass stale_threshold_seconds (this is the
    # /status path now), the value must come from cfg.heartbeat_stale_seconds.
    session = tmp_path / "telegram_channel.session"
    heartbeat = tmp_path / "heartbeat"
    _touch(session, age_seconds=120)
    _touch(heartbeat, age_seconds=900)

    holder = _settings_with_heartbeat(heartbeat_stale_seconds=600)

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=holder,
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            heartbeat_path_override=str(heartbeat),
        )
    # heartbeat 900 s > settings threshold 600 s, but session 120 s wins
    # via max(); status reflects youngest mtime vs threshold.
    assert result["stale_threshold_seconds"] == 600
    assert result["last_seen_source"] == "session"
    assert result["status"] == "ok"


def test_legacy_settings_without_heartbeat_attribute_still_work(
    tmp_path: Path,
) -> None:
    # Defensive: if a deployment runs an old settings object (no
    # heartbeat_path / heartbeat_stale_seconds) the watchdog must keep
    # working with session+PID semantics.
    session = tmp_path / "telegram_channel.session"
    _touch(session, age_seconds=120)

    legacy_cfg = MagicMock(spec=["enabled", "session_path"])
    legacy_cfg.enabled = True
    legacy_cfg.session_path = "artifacts/telegram_channel.session"
    holder = MagicMock()
    holder.telegram_channel_ingest = legacy_cfg

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=holder,
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
        )
    assert result["status"] == "ok"
    assert result["last_seen_source"] == "session"
    assert result["heartbeat_file_exists"] is False
