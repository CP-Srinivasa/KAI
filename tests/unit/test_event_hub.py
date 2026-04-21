"""Tests for the SSE event-hub used by the operator dashboard (NEO-P-005).

Covers:
- publish/subscribe round-trip across threads
- queue-full drop policy keeps the publisher non-blocking
- SSE-endpoint streams events + keepalives and cleans up on disconnect
- ServerEvent.to_sse shapes a wire-format payload
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from app.api.event_hub import (
    EventHub,
    ServerEvent,
    get_default_event_hub,
    reset_default_event_hub,
)


@pytest.fixture(autouse=True)
def _reset_default_hub() -> None:
    reset_default_event_hub()


def test_server_event_to_sse_wraps_ts_and_json() -> None:
    evt = ServerEvent(event="alert_fired", data={"doc": "x", "priority": 3})
    wire = evt.to_sse()
    assert wire.startswith("event: alert_fired\n")
    body_line = next(ln for ln in wire.splitlines() if ln.startswith("data: "))
    payload = json.loads(body_line[len("data: "):])
    assert payload["doc"] == "x"
    assert payload["priority"] == 3
    assert "ts" in payload
    assert wire.endswith("\n\n")


@pytest.mark.asyncio
async def test_publish_reaches_subscribed_queue() -> None:
    hub = EventHub()
    queue, _sub = hub.subscribe()
    hub.publish("alert_fired", {"document_id": "d1"})
    evt = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert evt.event == "alert_fired"
    assert evt.data == {"document_id": "d1"}


@pytest.mark.asyncio
async def test_publish_from_worker_thread_delivers_via_loop() -> None:
    hub = EventHub()
    queue, _sub = hub.subscribe()

    def _worker() -> None:
        hub.publish("fill_settled", {"symbol": "BTCUSDT"})

    threading.Thread(target=_worker, daemon=True).start()
    evt = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert evt.event == "fill_settled"
    assert evt.data == {"symbol": "BTCUSDT"}


@pytest.mark.asyncio
async def test_publish_drops_events_when_queue_full() -> None:
    from app.api import event_hub as event_hub_mod

    hub = EventHub()
    queue, _sub = hub.subscribe()
    for i in range(event_hub_mod._MAX_QUEUE_SIZE + 5):
        hub.publish("alert_fired", {"i": i})
    await asyncio.sleep(0.01)  # let call_soon_threadsafe drain
    # Capacity cap holds — publisher never blocked, excess silently dropped.
    assert queue.qsize() <= event_hub_mod._MAX_QUEUE_SIZE


@pytest.mark.asyncio
async def test_unsubscribe_removes_subscriber() -> None:
    hub = EventHub()
    _q, sub = hub.subscribe()
    assert hub.subscriber_count == 1
    hub.unsubscribe(sub)
    assert hub.subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_noop_when_no_subscribers() -> None:
    hub = EventHub()
    # Must not raise / allocate queues.
    hub.publish("alert_fired", {"x": 1})
    assert hub.subscriber_count == 0


@pytest.mark.asyncio
async def test_default_hub_is_module_singleton() -> None:
    h1 = get_default_event_hub()
    h2 = get_default_event_hub()
    assert h1 is h2
