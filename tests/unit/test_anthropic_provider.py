"""Tests for AnthropicAnalysisProvider — all API calls mocked."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.enums import MarketScope, SentimentLabel
from app.integrations.anthropic.provider import AnthropicAnalysisProvider

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_tool_block(overrides: dict | None = None) -> object:
    class FakeBlock:
        type = "tool_use"
        name = "record_analysis"
        input = {
            "sentiment_label": "bullish",
            "sentiment_score": 0.8,
            "relevance_score": 0.9,
            "impact_score": 0.5,
            "confidence_score": 0.9,
            "novelty_score": 0.5,
            "spam_probability": 0.0,
            "market_scope": "crypto",
            "affected_assets": ["BTC"],
            "short_reasoning": "Bitcoin is pumping",
        }

    if overrides:
        FakeBlock.input = {**FakeBlock.input, **overrides}
    return FakeBlock()


def _make_text_block(text: str = "I cannot analyze this.") -> object:
    class FakeBlock:
        type = "text"

    FakeBlock.text = text
    return FakeBlock()


def _mock_response(*blocks) -> object:
    class FakeResponse:
        content = list(blocks)

    return FakeResponse()


def _mock_client(response: object) -> MagicMock:
    mock = MagicMock()
    mock.messages = AsyncMock()
    mock.messages.create.return_value = response
    return mock


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def provider():
    return AnthropicAnalysisProvider(api_key="fake-key")


# ── metadata ──────────────────────────────────────────────────────────────────


def test_provider_name(provider):
    assert provider.provider_name == "anthropic"


def test_provider_model_default(provider):
    assert provider.model == "claude-3-7-sonnet-20250219"


def test_provider_model_custom():
    p = AnthropicAnalysisProvider(api_key="k", model="claude-3-5-haiku-20241022")
    assert p.model == "claude-3-5-haiku-20241022"


# ── analyze() ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_success(provider):
    provider._client = _mock_client(_mock_response(_make_tool_block()))

    result = await provider.analyze("BTC pumps", "Bitcoin goes to the moon")

    assert isinstance(result, LLMAnalysisOutput)
    assert result.sentiment_label == SentimentLabel.BULLISH
    assert result.sentiment_score == 0.8
    assert "BTC" in result.affected_assets
    assert result.short_reasoning == "Bitcoin is pumping"
    assert result.market_scope == MarketScope.CRYPTO


@pytest.mark.asyncio
async def test_analyze_refusal_raises(provider):
    provider._client = _mock_client(_mock_response(_make_text_block()))

    with pytest.raises(ValueError, match="did not call record_analysis tool"):
        await provider.analyze("Title", "Text")


@pytest.mark.asyncio
async def test_analyze_passes_context(provider):
    """Tool input should include context-derived data via the prompt."""
    captured: list[dict] = []

    async def fake_create(**kwargs):
        captured.append(kwargs)
        return _mock_response(_make_tool_block())

    provider._client = MagicMock()
    provider._client.messages = MagicMock()
    provider._client.messages.create = fake_create

    await provider.analyze("Test", "test text", context={"tickers": ["SOL"]})

    assert len(captured) == 1
    user_msg = captured[0]["messages"][0]["content"]
    assert "SOL" in user_msg


@pytest.mark.asyncio
async def test_analyze_tool_choice_forced(provider):
    """tool_choice must be set to force the model to call record_analysis."""
    captured: list[dict] = []

    async def fake_create(**kwargs):
        captured.append(kwargs)
        return _mock_response(_make_tool_block())

    provider._client = MagicMock()
    provider._client.messages = MagicMock()
    provider._client.messages.create = fake_create

    await provider.analyze("Test", "text")

    call = captured[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_analysis"}


@pytest.mark.asyncio
async def test_analyze_text_truncated_to_max_chars(provider):
    """Text longer than _MAX_TEXT_CHARS must be truncated before sending."""
    from app.integrations.anthropic import provider as mod

    long_text = "x" * (mod._MAX_TEXT_CHARS + 500)
    captured: list[dict] = []

    async def fake_create(**kwargs):
        captured.append(kwargs)
        return _mock_response(_make_tool_block())

    provider._client = MagicMock()
    provider._client.messages = MagicMock()
    provider._client.messages.create = fake_create

    await provider.analyze("Title", long_text)

    user_content = captured[0]["messages"][0]["content"]
    assert len(user_content) <= mod._MAX_TEXT_CHARS + 500  # prompt overhead allowed


# ── from_settings() ───────────────────────────────────────────────────────────


def test_from_settings():
    settings = MagicMock()
    settings.anthropic_api_key = "my-key"
    settings.anthropic_model = "claude-3-5-haiku-20241022"
    settings.anthropic_timeout = 20

    p = AnthropicAnalysisProvider.from_settings(settings)

    assert p.provider_name == "anthropic"
    assert p.model == "claude-3-5-haiku-20241022"
