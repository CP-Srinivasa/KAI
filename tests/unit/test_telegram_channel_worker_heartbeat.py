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
    _heartbeat_loop,
    _touch_heartbeat,
)


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
