"""Tests for telegram_channel_worker (B-3, pure-handler tier).

These tests deliberately avoid Telethon — they exercise only the
pure ``process_message`` function that sits between raw channel text
and envelope-emit. The MTProto layer is a thin Telethon wrapper with
no business logic, so integration-testing it would be mostly mocking
Telethon primitives without real coverage gain.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.ingestion.telegram_channel_worker import (
    _checkpoint_chat_id_marked,
    get_last_seen_id,
    load_checkpoint,
    process_message,
    replay_missed_messages,
    save_checkpoint,
)

SAMPLE_GUN = """\
Long/Buy #GUN/USDT

Entry Point - 2800

Targets: 2815 - 2830 - 2840 - 2855

Leverage - 10x

Stop Loss - 2680"""

SAMPLE_NON_SIGNAL = "Good morning traders! Market is ranging."


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


class _EmitSpy:
    """Stand-in for emit_parsed_signal — captures calls, returns canned value."""

    def __init__(self, *, return_value: dict[str, Any] | None):
        self.calls: list[dict[str, Any]] = []
        self._return = return_value

    def __call__(self, parsed, *, source, chat_id, now=None, **_kw):
        self.calls.append(
            {
                "source": source,
                "chat_id": chat_id,
                "parsed_symbol": parsed.symbol,
                "parsed_direction": parsed.direction,
            }
        )
        return self._return


class TestProcessMessage:
    def test_non_signal_logs_raw_only(self, tmp_path: Path) -> None:
        raw_log = tmp_path / "raw.jsonl"
        spy = _EmitSpy(return_value={"envelope_id": "ENV-X"})
        result = process_message(
            SAMPLE_NON_SIGNAL,
            source_tag="telegram_premium_channel",
            chat_id=-100111222,
            raw_log_path=raw_log,
            emit_fn=spy,
        )
        assert result == {
            "parsed": False,
            "emitted": False,
            "envelope_id": None,
            "reason": "not_a_signal",
        }
        # emit must not be called for non-signals.
        assert spy.calls == []
        # raw log must record the non-signal for later review.
        logged = _read_jsonl(raw_log)
        assert len(logged) == 1
        assert logged[0]["outcome"] == "not_a_signal"
        assert logged[0]["chat_id"] == -100111222

    def test_parsed_signal_emits_envelope(self, tmp_path: Path) -> None:
        raw_log = tmp_path / "raw.jsonl"
        spy = _EmitSpy(return_value={"envelope_id": "ENV-ABC"})
        result = process_message(
            SAMPLE_GUN,
            source_tag="telegram_premium_channel",
            chat_id=-100111222,
            raw_log_path=raw_log,
            emit_fn=spy,
        )
        assert result["parsed"] is True
        assert result["emitted"] is True
        assert result["envelope_id"] == "ENV-ABC"
        # emit was called once with the correct source and chat_id.
        assert len(spy.calls) == 1
        assert spy.calls[0]["source"] == "telegram_premium_channel"
        assert spy.calls[0]["chat_id"] == -100111222
        assert spy.calls[0]["parsed_symbol"] == "GUNUSDT"
        # raw log entry marks it as parsed.
        logged = _read_jsonl(raw_log)
        assert logged[-1]["outcome"] == "parsed"
        assert logged[-1]["symbol"] == "GUNUSDT"
        assert logged[-1]["direction"] == "long"

    def test_duplicate_emit_returns_not_emitted(self, tmp_path: Path) -> None:
        """emit_fn returning None (e.g. idempotency-dup) → emitted=False."""
        raw_log = tmp_path / "raw.jsonl"
        spy = _EmitSpy(return_value=None)
        result = process_message(
            SAMPLE_GUN,
            source_tag="telegram_premium_channel",
            chat_id=-100111222,
            raw_log_path=raw_log,
            emit_fn=spy,
        )
        assert result["parsed"] is True
        assert result["emitted"] is False
        assert result["envelope_id"] is None
        assert result["reason"] == "duplicate_or_write_failed"

    def test_empty_text_is_not_a_signal(self, tmp_path: Path) -> None:
        raw_log = tmp_path / "raw.jsonl"
        spy = _EmitSpy(return_value=None)
        result = process_message(
            "",
            source_tag="t",
            chat_id=None,
            raw_log_path=raw_log,
            emit_fn=spy,
        )
        assert result["parsed"] is False
        assert spy.calls == []

    def test_timestamp_is_recorded_in_raw_log(self, tmp_path: Path) -> None:
        raw_log = tmp_path / "raw.jsonl"
        spy = _EmitSpy(return_value={"envelope_id": "ENV-T"})
        fixed = datetime(2026, 4, 20, 21, 0, 0, tzinfo=UTC)
        process_message(
            SAMPLE_GUN,
            source_tag="t",
            chat_id=1,
            raw_log_path=raw_log,
            emit_fn=spy,
            now=fixed,
        )
        logged = _read_jsonl(raw_log)
        assert logged[-1]["timestamp_utc"] == fixed.isoformat()


# ── Checkpoint + Gap-Replay tests ───────────────────────────────────────────


class TestCheckpoint:
    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_checkpoint(tmp_path / "absent.json") == {}

    def test_load_corrupt_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "checkpoint.json"
        path.write_text("not-json", encoding="utf-8")
        assert load_checkpoint(path) == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "checkpoint.json"
        save_checkpoint(path, chat_id=-1001234, message_id=42)
        save_checkpoint(path, chat_id=-1009999, message_id=7)
        state = load_checkpoint(path)
        assert state["-1001234"]["last_message_id"] == 42
        assert state["-1009999"]["last_message_id"] == 7
        # last_seen_at is a non-empty ISO string for both chats.
        assert isinstance(state["-1001234"]["last_seen_at"], str)
        assert state["-1001234"]["last_seen_at"]

    def test_save_overwrites_same_chat(self, tmp_path: Path) -> None:
        path = tmp_path / "checkpoint.json"
        save_checkpoint(path, chat_id=-100, message_id=10)
        save_checkpoint(path, chat_id=-100, message_id=25)
        state = load_checkpoint(path)
        assert state["-100"]["last_message_id"] == 25

    def test_get_last_seen_id_handles_missing_and_garbage(self) -> None:
        assert get_last_seen_id({}, -100) == 0
        assert get_last_seen_id({"-100": {"last_message_id": "abc"}}, -100) == 0
        assert get_last_seen_id({"-100": {"last_message_id": 17}}, -100) == 17

    # ── F6 (2026-05-04) Chat-ID-Key-Normalisation ──────────────────────────

    def test_chat_id_marked_helper_normalises_unmarked_to_marked(self) -> None:
        # Telethon entity.id (unmarked) -> marked with -100 prefix
        assert _checkpoint_chat_id_marked(1275462917) == -1001275462917
        # Already-marked passes through
        assert _checkpoint_chat_id_marked(-1001275462917) == -1001275462917
        # Edge case: 0 and small negatives untouched
        assert _checkpoint_chat_id_marked(0) == 0
        assert _checkpoint_chat_id_marked(-100) == -100

    def test_save_normalises_unmarked_chat_id(self, tmp_path: Path) -> None:
        # Caller passes unmarked entity.id; on disk we expect the marked key.
        path = tmp_path / "checkpoint.json"
        save_checkpoint(path, chat_id=1275462917, message_id=23820)
        state = load_checkpoint(path)
        assert "-1001275462917" in state
        assert "1275462917" not in state
        assert state["-1001275462917"]["last_message_id"] == 23820

    def test_get_last_seen_id_falls_back_to_legacy_unmarked_key(self) -> None:
        # Pre-F6 checkpoints may carry the unmarked key. Lookup with the
        # marked form must still find it (deprecation warning is logged but
        # not asserted here).
        legacy = {"1275462917": {"last_message_id": 99, "last_seen_at": "x"}}
        assert get_last_seen_id(legacy, -1001275462917) == 99

    def test_save_migrates_legacy_unmarked_key(self, tmp_path: Path) -> None:
        # On the first save after upgrade, the legacy unmarked entry must be
        # removed and the canonical marked entry written. This converges the
        # checkpoint without a separate migration tool.
        path = tmp_path / "checkpoint.json"
        path.write_text(
            json.dumps(
                {"1275462917": {"last_message_id": 23820, "last_seen_at": "old"}}
            ),
            encoding="utf-8",
        )
        save_checkpoint(path, chat_id=-1001275462917, message_id=23830)
        state = load_checkpoint(path)
        assert "1275462917" not in state
        assert state["-1001275462917"]["last_message_id"] == 23830


class _FakeMessage(SimpleNamespace):
    """Stand-in for a Telethon Message — supports getattr for id/raw_text."""


class _FakeClient:
    """Stand-in for a Telethon TelegramClient — yields canned messages."""

    def __init__(self, messages: list[_FakeMessage]) -> None:
        self._messages = messages
        self.calls: list[dict[str, Any]] = []

    def iter_messages(
        self,
        entity: Any,
        *,
        min_id: int = 0,
        limit: int | None = None,
    ) -> Any:
        self.calls.append({"entity": entity, "min_id": min_id, "limit": limit})
        # Telethon returns newest-first; we mimic that — replay must sort.
        ordered = sorted(self._messages, key=lambda m: m.id, reverse=True)

        async def _gen() -> Any:
            for msg in ordered:
                yield msg

        return _gen()


class TestReplayMissedMessages:
    def test_no_checkpoint_skips_replay(self, tmp_path: Path) -> None:
        client = _FakeClient([_FakeMessage(id=10, raw_text="hi", message="hi")])
        seen: list[tuple[int, str]] = []
        result = asyncio.run(
            replay_missed_messages(
                client,
                entity=object(),
                chat_id=-100,
                last_seen_id=0,
                process_fn=lambda mid, t: seen.append((mid, t)),
            )
        )
        assert result == {"scanned": 0, "processed": 0, "skipped_no_checkpoint": 1}
        # iter_messages MUST NOT be called when no checkpoint exists.
        assert client.calls == []
        assert seen == []

    def test_replay_processes_messages_in_chronological_order(
        self,
        tmp_path: Path,
    ) -> None:
        client = _FakeClient(
            [
                _FakeMessage(id=12, raw_text="c", message="c"),
                _FakeMessage(id=10, raw_text="a", message="a"),
                _FakeMessage(id=11, raw_text="b", message="b"),
            ]
        )
        seen: list[tuple[int, str]] = []
        result = asyncio.run(
            replay_missed_messages(
                client,
                entity=object(),
                chat_id=-100,
                last_seen_id=9,
                process_fn=lambda mid, t: seen.append((mid, t)),
            )
        )
        assert result["scanned"] == 3
        assert result["processed"] == 3
        # ascending msg-id order so the persisted checkpoint advances monotonically.
        assert [mid for mid, _ in seen] == [10, 11, 12]
        # Telethon was queried with the correct min_id.
        assert client.calls == [{"entity": client.calls[0]["entity"], "min_id": 9, "limit": 200}]

    def test_replay_filters_messages_at_or_below_checkpoint(self) -> None:
        # Defense-in-depth: even if Telethon returns a stale msg with id <= min_id
        # (e.g. boundary inclusivity differences across versions), we drop it.
        client = _FakeClient(
            [
                _FakeMessage(id=9, raw_text="old", message="old"),
                _FakeMessage(id=10, raw_text="boundary", message="boundary"),
                _FakeMessage(id=11, raw_text="new", message="new"),
            ]
        )
        seen: list[int] = []
        result = asyncio.run(
            replay_missed_messages(
                client,
                entity=object(),
                chat_id=-100,
                last_seen_id=10,
                process_fn=lambda mid, _t: seen.append(mid),
            )
        )
        assert seen == [11]
        assert result["scanned"] == 1
        assert result["processed"] == 1

    def test_replay_handler_error_does_not_abort(self) -> None:
        client = _FakeClient(
            [
                _FakeMessage(id=10, raw_text="ok1", message="ok1"),
                _FakeMessage(id=11, raw_text="boom", message="boom"),
                _FakeMessage(id=12, raw_text="ok2", message="ok2"),
            ]
        )
        seen: list[int] = []

        def handler(msg_id: int, _text: str) -> None:
            if msg_id == 11:
                raise RuntimeError("boom")
            seen.append(msg_id)

        result = asyncio.run(
            replay_missed_messages(
                client,
                entity=object(),
                chat_id=-100,
                last_seen_id=9,
                process_fn=handler,
            )
        )
        # 11 raised → counted as scanned, not processed; 10+12 succeed.
        assert seen == [10, 12]
        assert result["scanned"] == 3
        assert result["processed"] == 2

    def test_replay_falls_back_to_message_attr_when_raw_text_empty(self) -> None:
        # Telethon exposes ``raw_text`` for plain text but service messages
        # only carry ``message``. Replay must look at both.
        client = _FakeClient([_FakeMessage(id=10, raw_text="", message="from_message_field")])
        captured: list[str] = []
        asyncio.run(
            replay_missed_messages(
                client,
                entity=object(),
                chat_id=-100,
                last_seen_id=5,
                process_fn=lambda _mid, text: captured.append(text),
            )
        )
        assert captured == ["from_message_field"]
