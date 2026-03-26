"""Unit tests for TextIntentProcessor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messaging.text_intent import IntentResult, TextIntentProcessor


def _make_processor(api_key: str = "test-key") -> TextIntentProcessor:
    return TextIntentProcessor(api_key=api_key, model="gpt-4o", timeout=10)


def _fake_openai_response(intent: str, response: str, **extra: object) -> MagicMock:
    """Build a mock httpx response mimicking OpenAI chat completions."""
    content = json.dumps({"intent": intent, "response": response, **extra})
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    return resp


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

        async def fake_post(url, headers=None, json=None):
            return fake_resp

        with patch("app.messaging.text_intent.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

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

        async def fake_post(url, headers=None, json=None):
            return fake_resp

        with patch("app.messaging.text_intent.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

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

        async def fake_post(url, headers=None, json=None):
            return fake_resp

        with patch("app.messaging.text_intent.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await p.process("Wie steht Bitcoin?")

        assert result.intent == "query"
        assert result.response == "Bitcoin steht aktuell bei..."

    @pytest.mark.asyncio
    async def test_handles_http_error_gracefully(self) -> None:
        p = _make_processor()

        async def failing_post(url, headers=None, json=None):
            raise ConnectionError("network down")

        with patch("app.messaging.text_intent.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = failing_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await p.process("Test")

        assert result.intent == "chat"
        assert "nicht verarbeiten" in result.response

    @pytest.mark.asyncio
    async def test_handles_malformed_json_response(self) -> None:
        p = _make_processor()

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"content": "not valid json{"}}],
        }

        async def fake_post(url, headers=None, json=None):
            return resp

        with patch("app.messaging.text_intent.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

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
