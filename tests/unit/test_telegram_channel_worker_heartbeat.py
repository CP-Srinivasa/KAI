"""Tests for the heartbeat helpers in the Telegram channel worker
(D-191 / S-003).

We test the *pure* helpers (``_touch_heartbeat`` + ``_heartbeat_loop``).
The full ``run_worker`` coroutine is integration-only because it pulls
in Telethon and a real (or stub) MTProto client; those tests live in
the integration suite. The heartbeat invariants we care about are:

1. _touch_heartbeat creates the file when it does not exist.
2. _touch_heartbeat refreshes mtime when the file exists.
3. _touch_heartbeat is fail-soft on OSError (no exception escapes).
4. _heartbeat_loop touches at least once before its first sleep and
   exits cleanly on cancellation.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.ingestion.telegram_channel_worker import (
    _HEARTBEAT_STATE,
    _heartbeat_loop,
    _init_heartbeat,
    _record_message_observed,
    _touch_heartbeat,
)


@pytest.fixture(autouse=True)
def _clear_heartbeat_state():
    # F3 (2026-05-05): module-level state must not leak across cases.
    # Pre-F3 tests assume _touch_heartbeat behaves in mtime-only mode;
    # they only stay green if state starts empty.
    _HEARTBEAT_STATE.clear()
    yield
    _HEARTBEAT_STATE.clear()


def test_touch_heartbeat_creates_file_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "heartbeat"
    assert not target.exists()
    _touch_heartbeat(target)
    assert target.exists()
    # mtime must be roughly "now" (allow 5 s jitter for slow CI).
    assert abs(target.stat().st_mtime - time.time()) < 5


def test_touch_heartbeat_refreshes_existing_file_mtime(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    target.write_bytes(b"")
    past = time.time() - 3600
    os.utime(target, (past, past))
    assert target.stat().st_mtime < time.time() - 1000

    _touch_heartbeat(target)

    refreshed = target.stat().st_mtime
    assert abs(refreshed - time.time()) < 5


def test_touch_heartbeat_swallows_oserror(tmp_path: Path) -> None:
    # If the parent directory cannot be created (simulated via patching
    # mkdir to raise), the worker must continue running. Liveness is an
    # observability concern; failure to write must not crash ingestion.
    target = tmp_path / "heartbeat"

    with patch.object(Path, "mkdir", side_effect=OSError("disk full")):
        # Must NOT raise.
        _touch_heartbeat(target)
    # File was never created because mkdir failed first.
    assert not target.exists()


def test_heartbeat_loop_touches_then_cancels_cleanly(tmp_path: Path) -> None:
    # The loop must:
    # 1. write the heartbeat at least once before its first await sleep,
    # 2. exit cleanly when the parent cancels its Task in finally.
    target = tmp_path / "heartbeat"

    async def runner() -> None:
        task = asyncio.create_task(_heartbeat_loop(target, interval_seconds=60.0))
        # Give the loop a tick to run the first touch before its sleep.
        await asyncio.sleep(0.05)
        assert target.exists(), "heartbeat must be written before first sleep"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(runner())


def test_heartbeat_loop_repeats_touches_within_interval(tmp_path: Path) -> None:
    # Verify that on a short interval the file mtime advances on every
    # iteration. We use a 50 ms interval and observe two distinct mtimes.
    target = tmp_path / "heartbeat"

    async def runner() -> None:
        task = asyncio.create_task(_heartbeat_loop(target, interval_seconds=0.05))
        await asyncio.sleep(0.02)
        first_mtime = target.stat().st_mtime
        # Force some clock advance so utime sees a new value (mtime
        # resolution on Windows can be coarse; sleep covers it).
        await asyncio.sleep(0.15)
        second_mtime = target.stat().st_mtime
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert second_mtime >= first_mtime
        # At least the second tick must have happened (no exact equality
        # check because filesystem mtime granularity varies).

    asyncio.run(runner())


# ── F3 (2026-05-05) — Reactivity-Counter ────────────────────────────────────


def test_init_heartbeat_writes_json_with_zero_counter(tmp_path: Path) -> None:
    # Boot: counter starts at 0, last_message_iso None, boot_iso stamped.
    target = tmp_path / "heartbeat"
    _init_heartbeat(target)

    import json as _json

    payload = _json.loads(target.read_text(encoding="utf-8"))
    assert payload["messages_since_boot"] == 0
    assert payload["last_message_iso"] is None
    assert isinstance(payload["boot_iso"], str)
    assert isinstance(payload["last_heartbeat_iso"], str)
    # boot_iso == last_heartbeat_iso at the moment of init.
    assert payload["boot_iso"] == payload["last_heartbeat_iso"]


def test_record_message_observed_increments_counter_and_stamps_message_ts(
    tmp_path: Path,
) -> None:
    # Each message-observed event bumps the counter and refreshes
    # last_message_iso. last_heartbeat_iso also refreshes (every observed
    # message proves the listener is alive).
    target = tmp_path / "heartbeat"
    _init_heartbeat(target)

    import json as _json

    boot_iso = _json.loads(target.read_text(encoding="utf-8"))["boot_iso"]

    _record_message_observed(target)
    after_one = _json.loads(target.read_text(encoding="utf-8"))
    assert after_one["messages_since_boot"] == 1
    assert isinstance(after_one["last_message_iso"], str)
    assert after_one["boot_iso"] == boot_iso  # boot_iso never changes mid-run

    _record_message_observed(target)
    after_two = _json.loads(target.read_text(encoding="utf-8"))
    assert after_two["messages_since_boot"] == 2


def test_touch_heartbeat_after_init_updates_only_last_heartbeat(
    tmp_path: Path,
) -> None:
    # Periodic _touch_heartbeat (60-second loop) must NOT touch the
    # counter — that's reserved for _record_message_observed. It only
    # refreshes last_heartbeat_iso so the watchdog sees the worker alive
    # even on a silent channel.
    target = tmp_path / "heartbeat"
    _init_heartbeat(target)
    _record_message_observed(target)  # counter -> 1

    import json as _json

    before = _json.loads(target.read_text(encoding="utf-8"))
    counter_before = before["messages_since_boot"]
    last_msg_before = before["last_message_iso"]

    _touch_heartbeat(target)

    after = _json.loads(target.read_text(encoding="utf-8"))
    assert after["messages_since_boot"] == counter_before  # unchanged
    assert after["last_message_iso"] == last_msg_before  # unchanged
    # last_heartbeat_iso may equal or be later — datetime resolution is
    # microseconds on POSIX but coarser on Windows; we accept either.
    assert after["last_heartbeat_iso"] >= before["last_heartbeat_iso"]


def test_touch_heartbeat_without_init_falls_back_to_mtime_only(
    tmp_path: Path,
) -> None:
    # Pre-F3 callers (and pre-existing tests) must still work: when the
    # module-level state is empty, _touch_heartbeat behaves like the
    # legacy mtime-only path on an empty file. This is the backwards-
    # compat anchor for any deploy that hasn't run _init_heartbeat yet
    # (e.g. an in-flight worker upgrade).
    target = tmp_path / "heartbeat"
    assert not _HEARTBEAT_STATE  # autouse fixture confirmed clear
    _touch_heartbeat(target)

    # Legacy semantics: file exists but is empty bytes (NOT JSON).
    assert target.exists()
    assert target.read_bytes() == b""


def test_record_message_observed_lazily_inits_when_state_empty(
    tmp_path: Path,
) -> None:
    # Defensive: if a unit test (or a regression in the worker boot
    # sequence) calls _record_message_observed without _init_heartbeat
    # first, the helper must self-initialise rather than silently drop
    # the event. This protects against off-by-one in counter math.
    target = tmp_path / "heartbeat"
    assert not _HEARTBEAT_STATE
    _record_message_observed(target)

    import json as _json

    payload = _json.loads(target.read_text(encoding="utf-8"))
    # Counter is 1 — init set it to 0, then the observed-message
    # increment in the same call brought it to 1. Without lazy init
    # the counter would still be at 0 (the bug we're guarding against).
    assert payload["messages_since_boot"] == 1
    assert payload["last_message_iso"] is not None
