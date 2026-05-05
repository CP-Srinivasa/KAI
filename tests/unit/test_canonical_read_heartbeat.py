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
from datetime import UTC, datetime, timedelta
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


# ── F3 (2026-05-05) — Reactivity-Status-Klassifikation ──────────────────────


def _write_heartbeat_json(
    path: Path,
    *,
    boot_offset_s: float,
    last_msg_offset_s: float | None,
    messages_since_boot: int,
) -> None:
    # Produce a F3-format heartbeat file with controllable ages, then
    # match its mtime to the most recent of (boot, last_message) so the
    # liveness `status` stays "ok" and the test isolates reactivity.
    import json as _json

    now = datetime.now(UTC)
    boot_iso = (now - timedelta(seconds=boot_offset_s)).isoformat()
    if last_msg_offset_s is not None:
        last_msg_iso: str | None = (now - timedelta(seconds=last_msg_offset_s)).isoformat()
    else:
        last_msg_iso = None
    last_hb_offset_s = min(boot_offset_s, last_msg_offset_s or boot_offset_s)
    last_hb_iso = (now - timedelta(seconds=last_hb_offset_s)).isoformat()
    payload = {
        "boot_iso": boot_iso,
        "last_heartbeat_iso": last_hb_iso,
        "last_message_iso": last_msg_iso,
        "messages_since_boot": messages_since_boot,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(payload), encoding="utf-8")
    # Also pin mtime so the liveness mtime-check stays fresh.
    fresh = time.time() - last_hb_offset_s
    os.utime(path, (fresh, fresh))


def test_reactivity_ok_when_messages_observed_recently(tmp_path: Path) -> None:
    # Worker has been up for 6 hours, processed 17 messages, last one
    # was 12 minutes ago — clear "ok" reactivity.
    session = tmp_path / "telegram_channel.session"
    _touch(session, age_seconds=120)
    heartbeat = tmp_path / "heartbeat"
    _write_heartbeat_json(
        heartbeat,
        boot_offset_s=6 * 3600,
        last_msg_offset_s=12 * 60,
        messages_since_boot=17,
    )

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings_with_heartbeat(),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            heartbeat_path_override=str(heartbeat),
        )
    assert result["reactivity_status"] == "ok"
    assert result["messages_since_boot"] == 17
    assert result["last_message_age_seconds"] is not None
    assert result["last_message_age_seconds"] < 24 * 3600
    assert result["boot_age_seconds"] is not None


def test_reactivity_stale_silent_when_last_message_older_than_24h(
    tmp_path: Path,
) -> None:
    # Worker has been up for 4 days, last message was 30 hours ago —
    # this is the V19 4-day-silence pattern. Liveness `status` stays "ok"
    # (heartbeat-loop ticks), reactivity flags it as stale_silent.
    session = tmp_path / "telegram_channel.session"
    _touch(session, age_seconds=120)
    heartbeat = tmp_path / "heartbeat"
    _write_heartbeat_json(
        heartbeat,
        boot_offset_s=4 * 24 * 3600,
        last_msg_offset_s=30 * 3600,
        messages_since_boot=12,
    )

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings_with_heartbeat(),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            heartbeat_path_override=str(heartbeat),
        )
    assert result["reactivity_status"] == "stale_silent"
    assert result["status"] == "ok", "liveness must stay OK; reactivity is the new layer"
    assert result["last_message_age_seconds"] is not None
    assert result["last_message_age_seconds"] >= 24 * 3600


def test_reactivity_cold_boot_when_fresh_with_zero_messages(tmp_path: Path) -> None:
    # Worker just booted (10 minutes ago), no messages yet — should be
    # cold_boot rather than stale_silent because the boot is too recent
    # to draw silence conclusions.
    session = tmp_path / "telegram_channel.session"
    _touch(session, age_seconds=120)
    heartbeat = tmp_path / "heartbeat"
    _write_heartbeat_json(
        heartbeat,
        boot_offset_s=10 * 60,
        last_msg_offset_s=None,
        messages_since_boot=0,
    )

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings_with_heartbeat(),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            heartbeat_path_override=str(heartbeat),
        )
    assert result["reactivity_status"] == "cold_boot"
    assert result["messages_since_boot"] == 0
    assert result["last_message_age_seconds"] is None
    assert result["boot_age_seconds"] is not None
    assert result["boot_age_seconds"] < 3600


def test_reactivity_no_data_for_pre_f3_empty_heartbeat_file(tmp_path: Path) -> None:
    # Backwards-compat anchor: a heartbeat file written by pre-F3 code
    # is empty bytes (no JSON). canonical_read must NOT crash on JSON
    # parse failure — it falls back to "no_data" reactivity while the
    # liveness `status` stays correct from mtime.
    session = tmp_path / "telegram_channel.session"
    _touch(session, age_seconds=3600)
    heartbeat = tmp_path / "heartbeat"
    _touch(heartbeat, age_seconds=30)  # empty bytes, fresh mtime

    with patch(
        "app.agents.tools.canonical_read.get_settings",
        return_value=_settings_with_heartbeat(),
    ):
        result = _summarize_telegram_channel_ingest(
            now=datetime.now(UTC),
            session_path_override=str(session),
            pid_file_override=str(tmp_path / "no.pid"),
            heartbeat_path_override=str(heartbeat),
        )
    assert result["status"] == "ok"  # mtime path unchanged
    assert result["reactivity_status"] == "no_data"
    assert result["messages_since_boot"] is None
    assert result["last_message_age_seconds"] is None
    assert result["boot_age_seconds"] is None
