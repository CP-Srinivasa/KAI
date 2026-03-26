"""Unit tests for Telegram signal exchange relay worker."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.messaging import exchange_relay


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.asyncio
async def test_relay_exchange_outbox_once_moves_successful_rows_to_sent(
    tmp_path: Path, monkeypatch
) -> None:
    outbox = tmp_path / "outbox.jsonl"
    sent = tmp_path / "sent.jsonl"
    dead = tmp_path / "dead.jsonl"
    _write_jsonl(
        outbox,
        [
            {
                "timestamp_utc": "2026-03-26T10:00:00+00:00",
                "event": "telegram_signal_exchange_forward_queued",
                "signal_id": "sig_abc",
                "asset": "BTC",
                "symbol": "BTC/USDT",
                "direction": "bullish",
                "reasoning": "breakout",
                "source": "text",
                "status": "queued",
                "attempt_count": 0,
            }
        ],
    )

    async def _fake_post_signal(**kwargs: object) -> tuple[bool, int | None, str | None]:
        return True, 202, None

    monkeypatch.setattr(exchange_relay, "_post_signal", _fake_post_signal)

    stats = await exchange_relay.relay_exchange_outbox_once(
        outbox_path=outbox,
        sent_log_path=sent,
        dead_letter_log_path=dead,
        endpoint="https://example.invalid/relay",
        max_attempts=3,
        batch_size=10,
    )

    assert stats.processed == 1
    assert stats.sent == 1
    assert stats.requeued == 0
    assert stats.dead_lettered == 0
    assert stats.skipped == 0
    assert _read_jsonl(outbox) == []

    sent_rows = _read_jsonl(sent)
    assert len(sent_rows) == 1
    assert sent_rows[0]["event"] == "telegram_signal_exchange_forward_sent"
    assert sent_rows[0]["status"] == "sent"
    assert sent_rows[0]["attempt_count"] == 1
    assert sent_rows[0]["relay_http_status"] == 202
    assert dead.exists() is False


@pytest.mark.asyncio
async def test_relay_exchange_outbox_once_requeues_before_max_attempts(
    tmp_path: Path, monkeypatch
) -> None:
    outbox = tmp_path / "outbox.jsonl"
    sent = tmp_path / "sent.jsonl"
    dead = tmp_path / "dead.jsonl"
    _write_jsonl(
        outbox,
        [
            {
                "timestamp_utc": "2026-03-26T10:00:00+00:00",
                "event": "telegram_signal_exchange_forward_queued",
                "signal_id": "sig_retry",
                "asset": "ETH",
                "symbol": "ETH/USDT",
                "direction": "bearish",
                "reasoning": "risk-off",
                "source": "voice",
                "status": "queued",
                "attempt_count": 1,
            }
        ],
    )

    async def _fake_post_signal(**kwargs: object) -> tuple[bool, int | None, str | None]:
        return False, 503, "http_503"

    monkeypatch.setattr(exchange_relay, "_post_signal", _fake_post_signal)

    stats = await exchange_relay.relay_exchange_outbox_once(
        outbox_path=outbox,
        sent_log_path=sent,
        dead_letter_log_path=dead,
        endpoint="https://example.invalid/relay",
        max_attempts=3,
        batch_size=10,
    )

    assert stats.processed == 1
    assert stats.sent == 0
    assert stats.requeued == 1
    assert stats.dead_lettered == 0

    outbox_rows = _read_jsonl(outbox)
    assert len(outbox_rows) == 1
    assert outbox_rows[0]["status"] == "queued"
    assert outbox_rows[0]["attempt_count"] == 2
    assert outbox_rows[0]["last_error"] == "http_503"
    assert outbox_rows[0]["last_http_status"] == 503
    assert _read_jsonl(sent) == []
    assert dead.exists() is False


@pytest.mark.asyncio
async def test_relay_exchange_outbox_once_dead_letters_after_max_attempts(
    tmp_path: Path, monkeypatch
) -> None:
    outbox = tmp_path / "outbox.jsonl"
    sent = tmp_path / "sent.jsonl"
    dead = tmp_path / "dead.jsonl"
    _write_jsonl(
        outbox,
        [
            {
                "timestamp_utc": "2026-03-26T10:00:00+00:00",
                "event": "telegram_signal_exchange_forward_queued",
                "signal_id": "sig_dead",
                "asset": "SOL",
                "symbol": "SOL/USDT",
                "direction": "bullish",
                "reasoning": "momentum",
                "source": "text",
                "status": "queued",
                "attempt_count": 2,
            }
        ],
    )

    async def _fake_post_signal(**kwargs: object) -> tuple[bool, int | None, str | None]:
        return False, None, "network_error:TimeoutError"

    monkeypatch.setattr(exchange_relay, "_post_signal", _fake_post_signal)

    stats = await exchange_relay.relay_exchange_outbox_once(
        outbox_path=outbox,
        sent_log_path=sent,
        dead_letter_log_path=dead,
        endpoint="https://example.invalid/relay",
        max_attempts=3,
        batch_size=10,
    )

    assert stats.processed == 1
    assert stats.sent == 0
    assert stats.requeued == 0
    assert stats.dead_lettered == 1
    assert _read_jsonl(outbox) == []
    assert _read_jsonl(sent) == []

    dead_rows = _read_jsonl(dead)
    assert len(dead_rows) == 1
    assert dead_rows[0]["event"] == "telegram_signal_exchange_forward_dead_letter"
    assert dead_rows[0]["status"] == "dead_letter"
    assert dead_rows[0]["attempt_count"] == 3
    assert "network_error" in str(dead_rows[0]["last_error"])


def test_build_signal_pipeline_status_counts_queue_and_lookback(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    recent = now.isoformat()
    old = (now - timedelta(days=3)).isoformat()

    handoff = tmp_path / "handoff.jsonl"
    outbox = tmp_path / "outbox.jsonl"
    sent = tmp_path / "sent.jsonl"
    dead = tmp_path / "dead.jsonl"

    _write_jsonl(
        handoff,
        [
            {"timestamp_utc": recent, "event": "telegram_signal_handoff"},
            {"timestamp_utc": old, "event": "telegram_signal_handoff"},
        ],
    )
    _write_jsonl(
        outbox,
        [
            {"event": "telegram_signal_exchange_forward_queued", "status": "queued"},
            {"event": "telegram_signal_exchange_forward_queued", "status": "dead_letter"},
        ],
    )
    _write_jsonl(
        sent,
        [
            {"relayed_at_utc": recent, "event": "telegram_signal_exchange_forward_sent"},
            {"relayed_at_utc": old, "event": "telegram_signal_exchange_forward_sent"},
        ],
    )
    _write_jsonl(
        dead,
        [
            {
                "dead_lettered_at_utc": recent,
                "event": "telegram_signal_exchange_forward_dead_letter",
            }
        ],
    )

    payload = exchange_relay.build_signal_pipeline_status(
        handoff_log_path=handoff,
        outbox_log_path=outbox,
        sent_log_path=sent,
        dead_letter_log_path=dead,
        lookback_hours=24,
    )

    assert payload["report_type"] == "telegram_signal_pipeline_status"
    assert payload["handoff_total"] == 2
    assert payload["handoff_lookback"] == 1
    assert payload["outbox_queued_total"] == 1
    assert payload["exchange_sent_total"] == 2
    assert payload["exchange_sent_lookback"] == 1
    assert payload["exchange_dead_letter_total"] == 1
    assert payload["exchange_dead_letter_lookback"] == 1
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
