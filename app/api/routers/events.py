"""Server-Sent-Events endpoint for the operator dashboard.

Streams alert/fill/gate events to subscribed browser clients so the UI
surface reacts in seconds instead of waiting for the 30–90 s poll
kadenz. Poll-based endpoints (`/dashboard/api/quality`,
`/dashboard/api/provenance`) remain authoritative for initial snapshots
and fallback when the SSE connection drops.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.event_hub import get_default_event_hub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_KEEPALIVE_INTERVAL_S = 15.0


@router.get("/dashboard/api/events")
async def dashboard_events(request: Request) -> StreamingResponse:
    hub = get_default_event_hub()
    queue, sub = hub.subscribe()

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_INTERVAL_S)
                    yield evt.to_sse()
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hub.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
