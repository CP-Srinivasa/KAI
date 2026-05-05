"""Tests for `kai cli ingestion telegram-channel bootstrap-checkpoint` (F5).

The CLI seeds the listener checkpoint manually for recovery scenarios
(post Pi-cutover, post session-rebuild). All risk-mitigation paths are
exercised here — silent overwrite is never allowed without --force.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


def _settings_with_checkpoint(
    checkpoint_path: Path,
    *,
    target_chat_id: int = 0,
):
    cfg = MagicMock()
    cfg.checkpoint_path = str(checkpoint_path)
    cfg.target_chat_id = target_chat_id
    cfg.enabled = True
    cfg.api_id = 12345
    cfg.api_hash = "x"
    holder = MagicMock()
    holder.telegram_channel_ingest = cfg
    return holder


def test_bootstrap_writes_canonical_marked_form_on_empty_file(tmp_path: Path) -> None:
    # Operator passes the unmarked entity.id; CLI normalises to marked
    # form (-100<peer>) before write, so the resulting file matches the
    # canonical convention the worker reads.
    checkpoint = tmp_path / "checkpoint.json"
    holder = _settings_with_checkpoint(checkpoint)

    with patch("app.cli.commands.ingestion.get_settings", return_value=holder):
        result = runner.invoke(
            app,
            [
                "ingestion",
                "telegram-channel",
                "bootstrap-checkpoint",
                "--message-id",
                "23820",
                "--chat-id",
                "1275462917",  # unmarked
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert "-1001275462917" in payload  # canonical marked key
    assert payload["-1001275462917"]["last_message_id"] == 23820


def test_bootstrap_refuses_message_id_zero_or_negative(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    holder = _settings_with_checkpoint(checkpoint, target_chat_id=-1001275462917)

    with patch("app.cli.commands.ingestion.get_settings", return_value=holder):
        result = runner.invoke(
            app,
            [
                "ingestion",
                "telegram-channel",
                "bootstrap-checkpoint",
                "--message-id",
                "0",
            ],
        )

    assert result.exit_code == 2
    assert "must be > 0" in result.output
    assert not checkpoint.exists()


def test_bootstrap_refuses_when_no_chat_id_resolvable(tmp_path: Path) -> None:
    # No --chat-id passed AND no INGESTION_TELEGRAM_CHANNEL_TARGET_CHAT_ID
    # set in settings (target_chat_id == 0). Must fail loudly rather
    # than silently writing a chat_id=0 entry.
    checkpoint = tmp_path / "checkpoint.json"
    holder = _settings_with_checkpoint(checkpoint, target_chat_id=0)

    with patch("app.cli.commands.ingestion.get_settings", return_value=holder):
        result = runner.invoke(
            app,
            [
                "ingestion",
                "telegram-channel",
                "bootstrap-checkpoint",
                "--message-id",
                "100",
            ],
        )

    assert result.exit_code == 2
    assert "No chat_id resolved" in result.output
    assert not checkpoint.exists()


def test_bootstrap_prompts_before_overwriting_existing_entry(tmp_path: Path) -> None:
    # Pre-existing checkpoint must trigger the interactive confirm.
    # CliRunner with input="n\n" simulates an operator typing "no".
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps(
            {
                "-1001275462917": {
                    "last_message_id": 23820,
                    "last_seen_at": "2026-04-30T11:36:13+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    holder = _settings_with_checkpoint(checkpoint, target_chat_id=-1001275462917)

    with patch("app.cli.commands.ingestion.get_settings", return_value=holder):
        result = runner.invoke(
            app,
            [
                "ingestion",
                "telegram-channel",
                "bootstrap-checkpoint",
                "--message-id",
                "24000",
            ],
            input="n\n",
        )

    assert result.exit_code == 1
    assert "Aborted by operator" in result.output
    # File untouched.
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert payload["-1001275462917"]["last_message_id"] == 23820


def test_bootstrap_force_skips_confirmation_for_existing_entry(tmp_path: Path) -> None:
    # --force bypasses the interactive prompt — for scripts / cron / CI.
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps(
            {
                "-1001275462917": {
                    "last_message_id": 23820,
                    "last_seen_at": "2026-04-30T11:36:13+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    holder = _settings_with_checkpoint(checkpoint, target_chat_id=-1001275462917)

    with patch("app.cli.commands.ingestion.get_settings", return_value=holder):
        result = runner.invoke(
            app,
            [
                "ingestion",
                "telegram-channel",
                "bootstrap-checkpoint",
                "--message-id",
                "24500",
                "--force",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert payload["-1001275462917"]["last_message_id"] == 24500


def test_bootstrap_refuses_lower_message_id_without_force(tmp_path: Path) -> None:
    # Lower-than-existing message_id would trigger duplicate-replay of
    # already-processed messages → operator-visible duplicate approval-
    # sends. Must require explicit --force to proceed.
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps(
            {
                "-1001275462917": {
                    "last_message_id": 24500,
                    "last_seen_at": "2026-05-04T12:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    holder = _settings_with_checkpoint(checkpoint, target_chat_id=-1001275462917)

    with patch("app.cli.commands.ingestion.get_settings", return_value=holder):
        result = runner.invoke(
            app,
            [
                "ingestion",
                "telegram-channel",
                "bootstrap-checkpoint",
                "--message-id",
                "23000",  # lower than existing 24500
            ],
        )

    assert result.exit_code == 3
    assert "LOWER than existing" in result.output
    # Rich console wraps long lines; split tokens are sufficient evidence.
    assert "duplicate" in result.output
    assert "operator-approval-sends" in result.output
    # File untouched.
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert payload["-1001275462917"]["last_message_id"] == 24500


def test_bootstrap_dry_run_shows_preview_without_writing(tmp_path: Path) -> None:
    # --dry-run must produce the would-be JSON in stdout but never
    # touch disk.
    checkpoint = tmp_path / "checkpoint.json"
    holder = _settings_with_checkpoint(checkpoint, target_chat_id=-1001275462917)

    with patch("app.cli.commands.ingestion.get_settings", return_value=holder):
        result = runner.invoke(
            app,
            [
                "ingestion",
                "telegram-channel",
                "bootstrap-checkpoint",
                "--message-id",
                "23820",
                "--dry-run",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "would write" in result.output
    assert "23820" in result.output
    # File NOT created.
    assert not checkpoint.exists()
