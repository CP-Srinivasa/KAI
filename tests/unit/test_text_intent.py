"""Unit tests for TextIntentProcessor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messaging.text_intent import IntentResult, TextIntentProcessor


def _make_processor(api_key: str = "test-key") -> TextIntentProcessor:
    return TextIntentProcessor(api_key=api_key, model="gpt-4o", timeout=10)


def _fake_openai_response(intent: str, response: str, **extra: object) -> MagicMock:
    """Build a mock SDK chat-completions response (Audit F-1: SDK statt httpx)."""
    content = json.dumps({"intent": intent, "response": response, **extra})
    return _sdk_response(content)


def _sdk_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    return resp


def _patch_sdk(result: MagicMock | None = None, error: Exception | None = None):
    """Patch AsyncOpenAI so chat.completions.create returns *result* or raises."""
    client = MagicMock()
    if error is not None:
        client.chat.completions.create = AsyncMock(side_effect=error)
    else:
        client.chat.completions.create = AsyncMock(return_value=result)
    mock_cls = MagicMock(return_value=client)
    return patch("app.messaging.text_intent.AsyncOpenAI", mock_cls)


class TestTextIntentProcessor:
    """Tests for intent classification."""

    def test_is_configured_with_key(self) -> None:
        p = _make_processor("sk-test")
        assert p.is_configured is True

    def test_is_not_configured_without_key(self) -> None:
        p = _make_processor("")
        assert p.is_configured is False

    @pytest.mark.asyncio
    async def test_returns_not_configured_without_api_key(self) -> None:
        p = _make_processor("")
        result = await p.process("Hallo KAI")
        assert result.intent == "chat"
        assert "nicht konfiguriert" in result.response

    @pytest.mark.asyncio
    async def test_classifies_signal_intent(self) -> None:
        p = _make_processor()
        fake_resp = _fake_openai_response(
            intent="signal",
            response="Signal notiert.",
            signal={"asset": "BTC", "direction": "bullish", "reasoning": "Breakout"},
        )

        with _patch_sdk(fake_resp):
            result = await p.process("Signal: BTC bullish, Breakout über 90k")

        assert result.intent == "signal"
        assert result.signal is not None
        assert result.signal["asset"] == "BTC"
        assert result.signal["direction"] == "bullish"

    @pytest.mark.asyncio
    async def test_classifies_command_intent(self) -> None:
        p = _make_processor()
        fake_resp = _fake_openai_response(
            intent="command",
            response="Zeige Status.",
            mapped_command="status",
        )

        with _patch_sdk(fake_resp):
            result = await p.process("Wie ist der Status?")

        assert result.intent == "command"
        assert result.mapped_command == "status"

    @pytest.mark.asyncio
    async def test_classifies_query_intent(self) -> None:
        p = _make_processor()
        fake_resp = _fake_openai_response(
            intent="query",
            response="Bitcoin steht aktuell bei...",
        )

        with _patch_sdk(fake_resp):
            result = await p.process("Wie steht Bitcoin?")

        assert result.intent == "query"
        assert result.response == "Bitcoin steht aktuell bei..."

    @pytest.mark.asyncio
    async def test_handles_http_error_gracefully(self) -> None:
        p = _make_processor()

        with _patch_sdk(error=ConnectionError("network down")):
            result = await p.process("Test")

        assert result.intent == "chat"
        assert "nicht verarbeiten" in result.response

    @pytest.mark.asyncio
    async def test_handles_malformed_json_response(self) -> None:
        p = _make_processor()

        with _patch_sdk(_sdk_response("not valid json{")):
            result = await p.process("Test")

        assert result.intent == "chat"
        assert "nicht verarbeiten" in result.response


class TestIntentResult:
    """Tests for the frozen dataclass."""

    def test_defaults(self) -> None:
        r = IntentResult(intent="chat", response="Hi")
        assert r.signal is None
        assert r.mapped_command is None

    def test_with_signal(self) -> None:
        r = IntentResult(
            intent="signal",
            response="OK",
            signal={"asset": "ETH", "direction": "bearish", "reasoning": "test"},
        )
        assert r.signal["asset"] == "ETH"

    def test_is_frozen(self) -> None:
        r = IntentResult(intent="chat", response="test")
        with pytest.raises(AttributeError):
            r.intent = "signal"  # type: ignore[misc]
