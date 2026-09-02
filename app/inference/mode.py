"""One default-off migration policy shared by all operational callers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


async def run_inference_mode[ResultT](
    *,
    mode: str,
    gateway_call: Callable[[], Coroutine[Any, Any, ResultT]],
    legacy_call: Callable[[], Coroutine[Any, Any, ResultT]] | None,
) -> ResultT:
    """Run off/shadow/primary without duplicating authority or fallback policy."""
    if mode == "off":
        if legacy_call is None:
            raise RuntimeError("off mode requires a legacy inference path")
        return await legacy_call()
    if mode == "shadow":
        if legacy_call is None:
            raise RuntimeError("shadow mode requires an authoritative legacy path")
        candidate: asyncio.Task[ResultT] = asyncio.create_task(gateway_call())
        try:
            authoritative = await legacy_call()
        except Exception:
            await asyncio.gather(candidate, return_exceptions=True)
            raise
        await asyncio.gather(candidate, return_exceptions=True)
        return authoritative
    if mode == "primary":
        try:
            return await gateway_call()
        except Exception:
            if legacy_call is None:
                raise
            return await legacy_call()
    raise ValueError(f"unsupported inference mode: {mode}")
