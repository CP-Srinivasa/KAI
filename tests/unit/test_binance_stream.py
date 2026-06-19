"""Unit tests for the Binance liquidation stream consumer (#316).

Offline: no real socket. Verifies parse/append, the drain loop + heartbeat, and
the bounded reconnect loop via injected fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.ingestion.liquidations import binance_stream as bs
from app.market_data.liquidation_ledger import load_events

_RAW = json.dumps(
    {
        "e": "forceOrder",
        "E": 1568014460893,
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",
            "q": "0.5",
            "p": "60000",
            "ap": "60000",
            "z": "0.5",
            "T": 1568014460893,
        },
    }
)


def test_process_raw_appends_valid(tmp_path: Path) -> None:
    led = tmp_path / "liq.jsonl"
    assert bs.process_raw(_RAW, led) is True
    events = load_events(led)
    assert len(events) == 1
    assert events[0].symbol == "BTCUSDT"
    assert events[0].liquidated_side == "LONG"


def test_process_raw_ignores_garbage(tmp_path: Path) -> None:
    led = tmp_path / "liq.jsonl"
    assert bs.process_raw("{not json", led) is False
    assert bs.process_raw(json.dumps({"e": "depthUpdate"}), led) is False  # no 'o'
    assert bs.process_raw(json.dumps([1, 2, 3]), led) is False  # not a dict
    assert not led.exists()


class _FakeWS:
    """Async-iterable stand-in for a connected websocket."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)

    def __aiter__(self) -> _FakeWS:
        return self

    async def __anext__(self) -> str:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class _FakeConnect:
    """Callable returning an async-context-manager that yields a fixed ws."""

    def __init__(self, ws: _FakeWS) -> None:
        self._ws = ws
        self.calls = 0

    def __call__(self, url: str) -> _FakeConnect:
        self.calls += 1
        return self

    async def __aenter__(self) -> _FakeWS:
        return self._ws

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_consume_drains_and_heartbeats(tmp_path: Path) -> None:
    led = tmp_path / "liq.jsonl"
    hb = tmp_path / "hb.txt"
    ws = _FakeWS([_RAW, "garbage", _RAW])
    await bs._consume(ws, led, hb)
    assert len(load_events(led)) == 2  # garbage skipped
    # heartbeat written + parseable as ISO timestamp
    assert datetime.fromisoformat(hb.read_text(encoding="utf-8").strip()).tzinfo is not None


class _SilentWS:
    """Connected ws that never yields a message (calm market)."""

    def __aiter__(self) -> _SilentWS:
        return self

    async def __anext__(self) -> str:
        await asyncio.sleep(3600)  # block until cancelled
        raise StopAsyncIteration  # pragma: no cover


@pytest.mark.asyncio
async def test_consume_heartbeat_ticks_without_messages(tmp_path: Path) -> None:
    """Regression: the heartbeat must refresh even with zero messages, else a
    calm market would look like a dead feed (false 'down')."""
    led = tmp_path / "liq.jsonl"
    hb = tmp_path / "hb.txt"
    task = asyncio.create_task(bs._consume(_SilentWS(), led, hb, heartbeat_tick_s=0.05))
    try:
        await asyncio.sleep(0.12)
        first = hb.read_text(encoding="utf-8")
        await asyncio.sleep(0.15)
        second = hb.read_text(encoding="utf-8")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert first and second
    assert first != second  # ticker advanced the timestamp without any message
    assert load_events(led) == []  # no messages → nothing written


@pytest.mark.asyncio
async def test_run_consumes_then_stops(tmp_path: Path) -> None:
    led = tmp_path / "liq.jsonl"
    hb = tmp_path / "hb.txt"
    fake = _FakeConnect(_FakeWS([_RAW]))
    await bs.run(
        url="ws://fake",
        ledger_path=led,
        heartbeat_path=hb,
        connect=fake,
        stop_after_disconnects=1,
    )
    assert fake.calls == 1
    assert len(load_events(led)) == 1


@pytest.mark.asyncio
async def test_run_survives_connect_error(tmp_path: Path) -> None:
    led = tmp_path / "liq.jsonl"
    hb = tmp_path / "hb.txt"

    def _raising_connect(url: str) -> object:
        raise ConnectionError("boom")

    # stop_after_disconnects=1 → one failed attempt, then return (no real sleep)
    await bs.run(
        url="ws://fake",
        ledger_path=led,
        heartbeat_path=hb,
        connect=_raising_connect,
        stop_after_disconnects=1,
    )
    assert load_events(led) == []
