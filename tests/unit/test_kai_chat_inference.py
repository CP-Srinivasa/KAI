from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.messaging.kai_chat_engine import ChatReply, _respond_smalltalk


def _settings(mode: str, *, key: str = "direct-key") -> SimpleNamespace:
    return SimpleNamespace(
        providers=SimpleNamespace(openai_api_key=key, openai_model="gpt-direct"),
        inference=SimpleNamespace(effective_mode=mode),
    )


@pytest.mark.asyncio
async def test_kai_chat_primary_uses_gateway() -> None:
    router = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(content="gateway", actual_provider="gemini"))
    )
    with (
        patch("app.messaging.kai_chat_engine.get_settings", return_value=_settings("primary")),
        patch("app.inference.router.get_inference_router", return_value=router),
        patch("app.messaging.kai_chat_engine._direct_smalltalk", new=AsyncMock()) as direct,
    ):
        result = await _respond_smalltalk("Hallo", "de")
    assert result.reply == "gateway" and result.source == "gemini"
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_kai_chat_shadow_never_replaces_direct() -> None:
    router = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(content="candidate", actual_provider="gemini"))
    )
    authoritative = ChatReply(reply="direct", intent="smalltalk", source="openai")
    with (
        patch("app.messaging.kai_chat_engine.get_settings", return_value=_settings("shadow")),
        patch("app.inference.router.get_inference_router", return_value=router),
        patch(
            "app.messaging.kai_chat_engine._direct_smalltalk",
            new=AsyncMock(return_value=authoritative),
        ),
    ):
        result = await _respond_smalltalk("Hallo", "de")
    assert result == authoritative
