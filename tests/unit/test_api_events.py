"""SSE endpoint contract tests (NEO-P-005).

Full end-to-end streaming is covered by manual browser smoke — neither
`TestClient.stream` nor `httpx.ASGITransport` cleanly reads from an SSE
generator that never ends (both buffer until EOF). We cover the pieces
that are unit-testable:

- Router registers at the expected path with the expected name.
- The inner `stream()` generator subscribes on entry, emits initial
  `: connected` comment, publishes an event, and unsubscribes on cancel.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.api.event_hub import (
    get_default_event_hub,
    reset_default_event_hub,
)
from app.api.routers import events as events_mod


@pytest.fixture(autouse=True)
def _reset_default_hub() -> None:
    reset_default_event_hub()


def test_router_registers_sse_path() -> None:
    app = FastAPI()
    app.include_router(events_mod.router)
    paths = {route.path for route in app.router.routes}
    assert "/dashboard/api/events" in paths


@pytest.mark.asyncio
async def test_stream_subscribes_publishes_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_KEEPALIVE_INTERVAL_S", 0.05)

    request = AsyncMock()
    request.is_disconnected = AsyncMock(return_value=False)

    # Kick off the endpoint handler. It returns a StreamingResponse whose
    # body_iterator is the generator we actually want to drain.
    resp = await events_mod.dashboard_events(request)

    hub = get_default_event_hub()
    # After the handler returned the response, the `stream()` coroutine has
    # not run yet. subscribe() happens inside the handler body before the
    # generator is created, so we should see one subscriber now.
    assert hub.subscriber_count == 1

    hub.publish("alert_fired", {"document_id": "doc-1", "priority": 2})

    collected: list[str] = []
    async def _drain() -> None:
        async for chunk in resp.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            collected.append(text)
            if any("alert_fired" in c for c in collected):
                return

    await asyncio.wait_for(_drain(), timeout=2.0)
    joined = "".join(collected)
    assert ": connected" in joined
    assert "event: alert_fired" in joined
    assert "doc-1" in joined

    # Simulate a client disconnect and let the generator run one more step
    # so its `finally` runs and unsubscribes.
    request.is_disconnected = AsyncMock(return_value=True)
    try:
        async for _ in resp.body_iterator:  # pragma: no cover — one-shot drain
            pass
    except StopAsyncIteration:
        pass

    # subscriber_count drops back to 0 after the generator's finally block.
    for _ in range(50):
        if hub.subscriber_count == 0:
            break
        await asyncio.sleep(0.01)
    assert hub.subscriber_count == 0
