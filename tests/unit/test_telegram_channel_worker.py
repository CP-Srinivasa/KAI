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
    _HEARTBEAT_STATE,
    _checkpoint_chat_id_marked,
    get_last_seen_id,
    load_checkpoint,
    make_verbose_observer_handler,
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

    def test_save_checkpoint_never_regresses_same_chat(self, tmp_path: Path) -> None:
        path = tmp_path / "checkpoint.json"
        first_seen = datetime(2026, 5, 31, 10, 0, tzinfo=UTC)
        retry_seen = datetime(2026, 5, 31, 10, 1, tzinfo=UTC)
        save_checkpoint(path, chat_id=-100, message_id=25, now=first_seen)
        save_checkpoint(path, chat_id=-100, message_id=10, now=retry_seen)

        state = load_checkpoint(path)

        assert state["-100"]["last_message_id"] == 25
        assert state["-100"]["last_seen_at"] == retry_seen.isoformat()

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
            json.dumps({"1275462917": {"last_message_id": 23820, "last_seen_at": "old"}}),
            encoding="utf-8",
        )
        save_checkpoint(path, chat_id=-1001275462917, message_id=23830)
        state = load_checkpoint(path)
        assert "1275462917" not in state
        assert state["-1001275462917"]["last_message_id"] == 23830

    def test_save_migrates_legacy_key_without_regressing_checkpoint(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "checkpoint.json"
        path.write_text(
            json.dumps({"1275462917": {"last_message_id": 23830, "last_seen_at": "old"}}),
            encoding="utf-8",
        )
        save_checkpoint(path, chat_id=-1001275462917, message_id=23820)

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
        reverse: bool = False,
    ) -> Any:
        self.calls.append({"entity": entity, "min_id": min_id, "limit": limit, "reverse": reverse})
        ordered = [msg for msg in self._messages if msg.id > min_id]
        ordered = sorted(ordered, key=lambda m: m.id, reverse=not reverse)
        if limit is not None:
            ordered = ordered[:limit]

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
        assert client.calls == [
            {"entity": client.calls[0]["entity"], "min_id": 9, "limit": 200, "reverse": True}
        ]

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

    def test_replay_large_gap_starts_at_checkpoint_not_newest_page(self) -> None:
        messages = [
            _FakeMessage(id=msg_id, raw_text=str(msg_id), message=str(msg_id))
            for msg_id in range(10, 260)
        ]
        client = _FakeClient(messages)
        seen: list[int] = []

        result = asyncio.run(
            replay_missed_messages(
                client,
                entity=object(),
                chat_id=-100,
                last_seen_id=9,
                process_fn=lambda mid, _t: seen.append(mid),
                max_replay=200,
            )
        )

        assert result["scanned"] == 200
        assert result["processed"] == 200
        assert seen[0] == 10
        assert seen[-1] == 209
        assert 210 not in seen

    def test_replay_supports_async_process_fn(self) -> None:
        # V25 (2026-05-04): Replay handler is async because it must call the
        # async send_approval_request to keep parity with the live handler.
        client = _FakeClient(
            [
                _FakeMessage(id=10, raw_text="a", message="a"),
                _FakeMessage(id=11, raw_text="b", message="b"),
            ]
        )
        seen: list[int] = []

        async def async_handler(msg_id: int, _text: str) -> None:
            await asyncio.sleep(0)
            seen.append(msg_id)

        result = asyncio.run(
            replay_missed_messages(
                client,
                entity=object(),
                chat_id=-100,
                last_seen_id=9,
                process_fn=async_handler,
            )
        )
        assert seen == [10, 11]
        assert result == {"scanned": 2, "processed": 2, "skipped_no_checkpoint": 0}

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


# ── F4 (2026-05-05) — Verbose-Observer (opt-in diagnostic) ─────────────────


class TestVerboseObserverHandler:
    """Verifies the F4 diagnostic constraints. The handler MUST:

    - log on DEBUG (not INFO) — silent at default logger config
    - log only chat_id + msg_id, NEVER message text (PII boundary)
    - NOT bump the F3 reactivity counter (would corrupt stale_silent classify)
    """

    def _make_event(
        self, *, chat_id: int | None, msg_id: int | None, raw_text: str = ""
    ) -> SimpleNamespace:
        msg = SimpleNamespace(id=msg_id, message=raw_text) if msg_id is not None else None
        return SimpleNamespace(chat_id=chat_id, message=msg, raw_text=raw_text)

    def test_logs_chat_id_and_msg_id_at_debug_level(self, caplog: Any) -> None:
        import logging

        custom_logger = logging.getLogger("test_f4_observer_debug")
        custom_logger.setLevel(logging.DEBUG)
        handler = make_verbose_observer_handler(custom_logger)

        with caplog.at_level(logging.DEBUG, logger="test_f4_observer_debug"):
            asyncio.run(
                handler(
                    self._make_event(chat_id=-1001275462917, msg_id=23999, raw_text="x"),
                )
            )

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(debug_records) == 1, "must log exactly once at DEBUG"
        msg = debug_records[0].getMessage()
        assert "-1001275462917" in msg
        assert "23999" in msg

    def test_silent_at_info_level(self, caplog: Any) -> None:
        # Default production logger config is INFO. Even if F4 is enabled
        # by env, it must produce zero log output without an explicit
        # DEBUG bump — that's the cheap insurance against accidental
        # production-noise from a forgotten flag.
        import logging

        custom_logger = logging.getLogger("test_f4_observer_info")
        custom_logger.setLevel(logging.INFO)
        handler = make_verbose_observer_handler(custom_logger)

        with caplog.at_level(logging.INFO, logger="test_f4_observer_info"):
            asyncio.run(handler(self._make_event(chat_id=-1001234567890, msg_id=42, raw_text="y")))

        assert caplog.records == [], "INFO-level config must suppress F4 output"

    def test_does_not_log_message_text(self, caplog: Any) -> None:
        # PII guard: irrelevant channels the user follows must not bleed
        # into KAI logs. raw_text/message content goes through the handler
        # but never appears in the log line.
        import logging

        custom_logger = logging.getLogger("test_f4_observer_no_text")
        custom_logger.setLevel(logging.DEBUG)
        handler = make_verbose_observer_handler(custom_logger)

        secret_text = "PRIVATE-MESSAGE-TEXT-DO-NOT-LOG-12345"
        with caplog.at_level(logging.DEBUG, logger="test_f4_observer_no_text"):
            asyncio.run(handler(self._make_event(chat_id=-100, msg_id=1, raw_text=secret_text)))

        for record in caplog.records:
            assert secret_text not in record.getMessage(), (
                "verbose-observer must never log message text"
            )

    def test_does_not_increment_f3_reactivity_counter(self) -> None:
        # F3 counter-purity: the diagnostic observer must NOT count toward
        # messages_since_boot. It receives updates from arbitrary channels;
        # mixing those into the target-channel reactivity metric would
        # corrupt the stale_silent classification (a busy unrelated chat
        # would mask a stale premium-channel).
        import logging

        from app.ingestion.telegram_channel_worker import _init_heartbeat

        _HEARTBEAT_STATE.clear()
        # Init counter at 0, then run the observer 5 times.
        try:
            _init_heartbeat(Path("./_unused_test_heartbeat_path"))
            assert _HEARTBEAT_STATE["messages_since_boot"] == 0

            handler = make_verbose_observer_handler(logging.getLogger("test_f4_purity"))
            for i in range(5):
                asyncio.run(handler(self._make_event(chat_id=-100, msg_id=i, raw_text="")))

            # Counter must still be 0 — observer does not call
            # _record_message_observed.
            assert _HEARTBEAT_STATE["messages_since_boot"] == 0
        finally:
            _HEARTBEAT_STATE.clear()
            # Cleanup: remove the heartbeat file _init_heartbeat created.
            try:
                Path("./_unused_test_heartbeat_path").unlink()
            except FileNotFoundError:
                pass
