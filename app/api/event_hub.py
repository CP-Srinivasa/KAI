"""In-process Server-Sent-Events Bus for the dashboard surface.

Provides a fire-and-forget publish path from synchronous pipeline code
(alert dispatch, paper fill settlement) into one-or-more subscribed SSE
clients. Designed for a single-worker uvicorn deployment — see D-159.
Broadcasting is best-effort: events to a slow subscriber are dropped once
that client's queue hits the cap, so a stuck browser never stalls the
publisher.

Threading model: publishers may run in any context (APScheduler worker
thread, sync CLI path, async request handler). We capture the subscriber's
running event-loop at subscribe-time and use `call_soon_threadsafe` to hand
each event off to the right loop. The subscriber coroutine reads from its
own `asyncio.Queue`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_MAX_QUEUE_SIZE = 64


@dataclass(frozen=True)
class ServerEvent:
    event: str
    data: dict[str, Any]
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_sse(self) -> str:
        payload = {"ts": self.ts, **self.data}
        return f"event: {self.event}\ndata: {json.dumps(payload)}\n\n"


@dataclass(eq=False)
class _Subscriber:
    # eq=False preserves identity-hash so a Subscriber can live in a set —
    # value-equality here would treat two fresh Queues as equal and collapse
    # rows silently.
    queue: asyncio.Queue[ServerEvent]
    loop: asyncio.AbstractEventLoop


class EventHub:
    def __init__(self) -> None:
        self._subs: set[_Subscriber] = set()

    def subscribe(self) -> tuple[asyncio.Queue[ServerEvent], _Subscriber]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[ServerEvent] = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        sub = _Subscriber(queue=queue, loop=loop)
        self._subs.add(sub)
        return queue, sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        self._subs.discard(sub)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        if not self._subs:
            return
        evt = ServerEvent(event=event, data=data)
        for sub in list(self._subs):
            try:
                sub.loop.call_soon_threadsafe(self._deliver, sub.queue, evt)
            except RuntimeError:
                self._subs.discard(sub)

    @staticmethod
    def _deliver(queue: asyncio.Queue[ServerEvent], evt: ServerEvent) -> None:
        try:
            queue.put_nowait(evt)
        except asyncio.QueueFull:
            logger.debug("event_hub.drop.slow_subscriber event=%s", evt.event)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


_default_hub: EventHub | None = None


def get_default_event_hub() -> EventHub:
    global _default_hub
    if _default_hub is None:
        _default_hub = EventHub()
    return _default_hub


def reset_default_event_hub() -> None:
    """Test-only helper — replaces the module singleton with a fresh hub."""
    global _default_hub
    _default_hub = EventHub()
