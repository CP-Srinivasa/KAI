"""Unit tests for the Telegram-channel listener poll-backstop (2026-05-31).

Incident: the MTProto push-update stream died silently — run_until_disconnected
kept blocking, the heartbeat loop kept ticking, but no messages were observed
(messages_since_boot stuck at 1 for ~46h) and a NIGHT/USDT premium signal was
lost. The poll-backstop pulls via the checkpoint+replay path so a dead push
stream can no longer cause silent signal loss.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.ingestion import telegram_channel_worker as w


def _write_checkpoint(path: Path, chat_id: int, last_id: int) -> None:
    path.write_text(json.dumps({str(chat_id): {"last_message_id": last_id}}), encoding="utf-8")


@pytest.mark.asyncio
async def test_poll_backstop_pulls_with_current_checkpoint(tmp_path, monkeypatch):
    """Each iteration reloads the on-disk checkpoint and replays from it."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 4242)
    seen_last_seen: list[int] = []

    async def fake_replay(client, entity, *, chat_id, last_seen_id, process_fn):
        seen_last_seen.append(last_seen_id)
        # Break the loop after the first successful poll.
        raise asyncio.CancelledError

    monkeypatch.setattr(w, "replay_missed_messages", fake_replay)

    with pytest.raises(asyncio.CancelledError):
        await w._poll_backstop_loop(
            client=object(),
            entity=object(),
            checkpoint_path=ckpt,
            process_fn=lambda mid, txt: None,
            chat_id_marked=-100123,
            interval_s=0,
        )
    assert seen_last_seen == [4242]


@pytest.mark.asyncio
async def test_poll_backstop_failsoft_then_hard_recovery(tmp_path, monkeypatch):
    """Transient failures are swallowed; after N consecutive ones the client
    is disconnected so systemd restarts the worker."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 7)
    calls = {"replay": 0}

    async def always_fail(client, entity, *, chat_id, last_seen_id, process_fn):
        calls["replay"] += 1
        raise RuntimeError("simulated connection death")

    monkeypatch.setattr(w, "replay_missed_messages", always_fail)
    monkeypatch.setattr(w, "_POLL_MAX_CONSECUTIVE_FAILURES", 3)

    disconnected = {"n": 0}

    class FakeClient:
        async def disconnect(self):
            disconnected["n"] += 1

    # Returns (does not raise) once it disconnects for the systemd restart path.
    await w._poll_backstop_loop(
        client=FakeClient(),
        entity=object(),
        checkpoint_path=ckpt,
        process_fn=lambda mid, txt: None,
        chat_id_marked=-100123,
        interval_s=0,
    )
    assert calls["replay"] == 3
    assert disconnected["n"] == 1


@pytest.mark.asyncio
async def test_poll_backstop_recovers_after_transient_failure(tmp_path, monkeypatch):
    """A single failure must NOT trigger hard-recovery; the counter resets on
    the next success."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 1)
    seq = iter([RuntimeError("blip"), "ok"])

    async def flaky(client, entity, *, chat_id, last_seen_id, process_fn):
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        raise asyncio.CancelledError  # break loop on the successful pass

    monkeypatch.setattr(w, "replay_missed_messages", flaky)
    monkeypatch.setattr(w, "_POLL_MAX_CONSECUTIVE_FAILURES", 3)

    disconnected = {"n": 0}

    class FakeClient:
        async def disconnect(self):
            disconnected["n"] += 1

    with pytest.raises(asyncio.CancelledError):
        await w._poll_backstop_loop(
            client=FakeClient(),
            entity=object(),
            checkpoint_path=ckpt,
            process_fn=lambda mid, txt: None,
            chat_id_marked=-100123,
            interval_s=0,
        )
    assert disconnected["n"] == 0
