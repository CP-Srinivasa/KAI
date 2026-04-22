"""Tests for telegram_channel_worker (B-3, pure-handler tier).

These tests deliberately avoid Telethon — they exercise only the
pure ``process_message`` function that sits between raw channel text
and envelope-emit. The MTProto layer is a thin Telethon wrapper with
no business logic, so integration-testing it would be mostly mocking
Telethon primitives without real coverage gain.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ingestion.telegram_channel_worker import process_message

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
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
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
