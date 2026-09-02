from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.inference.mode import run_inference_mode


@pytest.mark.asyncio
async def test_off_calls_only_legacy() -> None:
    gateway = AsyncMock(return_value="gateway")
    legacy = AsyncMock(return_value="legacy")
    result = await run_inference_mode(mode="off", gateway_call=gateway, legacy_call=legacy)
    assert result == "legacy"
    gateway.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_returns_legacy_even_when_gateway_differs() -> None:
    result = await run_inference_mode(
        mode="shadow",
        gateway_call=AsyncMock(return_value="candidate"),
        legacy_call=AsyncMock(return_value="authoritative"),
    )
    assert result == "authoritative"


@pytest.mark.asyncio
async def test_primary_falls_back_to_legacy() -> None:
    result = await run_inference_mode(
        mode="primary",
        gateway_call=AsyncMock(side_effect=TimeoutError("down")),
        legacy_call=AsyncMock(return_value="legacy"),
    )
    assert result == "legacy"


@pytest.mark.asyncio
async def test_primary_without_legacy_propagates_gateway_failure() -> None:
    with pytest.raises(TimeoutError):
        await run_inference_mode(
            mode="primary",
            gateway_call=AsyncMock(side_effect=TimeoutError("down")),
            legacy_call=None,
        )
