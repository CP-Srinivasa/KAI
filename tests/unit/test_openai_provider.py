"""Tests for OpenAIAnalysisProvider — all LLM calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.enums import MarketScope, SentimentLabel
from app.integrations.openai.prompts import format_user_prompt
from app.integrations.openai.provider import OpenAIAnalysisProvider

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_llm_output(**overrides) -> LLMAnalysisOutput:
    defaults: dict = {
        "sentiment_label": SentimentLabel.BULLISH,
        "sentiment_score": 0.7,
        "relevance_score": 0.9,
        "impact_score": 0.6,
        "confidence_score": 0.85,
        "novelty_score": 0.5,
        "spam_probability": 0.02,
        "market_scope": MarketScope.CRYPTO,
        "affected_assets": ["BTC"],
        "affected_sectors": ["Layer1"],
        "short_reasoning": "BTC ETF approval signals institutional acceptance.",
        "recommended_priority": 7,
        "actionable": True,
        "tags": ["bitcoin", "etf"],
    }
    defaults.update(overrides)
    return LLMAnalysisOutput(**defaults)


def _mock_parse_response(parsed: LLMAnalysisOutput) -> MagicMock:
    msg = MagicMock()
    msg.parsed = parsed
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


# ── Provider metadata ──────────────────────────────────────────────────────────


def test_provider_name():
    provider = OpenAIAnalysisProvider(api_key="test-key")
    assert provider.provider_name == "openai"


def test_provider_model_default():
    provider = OpenAIAnalysisProvider(api_key="test-key")
    assert provider.model == "gpt-4o"


def test_provider_model_custom():
    provider = OpenAIAnalysisProvider(api_key="test-key", model="gpt-4o-mini")
    assert provider.model == "gpt-4o-mini"


# ── analyze() — mocked OpenAI call ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_returns_llm_output():
    provider = OpenAIAnalysisProvider(api_key="test-key")
    expected = _make_llm_output()
    mock_response = _mock_parse_response(expected)

    with patch.object(
        provider._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await provider.analyze(
            title="Bitcoin ETF Approved by SEC",
            text="The SEC has approved Bitcoin spot ETF applications...",
        )

    assert isinstance(result, LLMAnalysisOutput)
    assert result.sentiment_label == SentimentLabel.BULLISH
    assert result.sentiment_score == 0.7
    assert "BTC" in result.affected_assets
    assert result.actionable is True


@pytest.mark.asyncio
async def test_analyze_passes_context_to_prompt():
    provider = OpenAIAnalysisProvider(api_key="test-key")
    expected = _make_llm_output()
    mock_response = _mock_parse_response(expected)
    captured_calls: list = []

    async def _capture(**kwargs):
        captured_calls.append(kwargs)
        return mock_response

    with patch.object(
        provider._client.beta.chat.completions,
        "parse",
        new=AsyncMock(side_effect=_capture),
    ):
        await provider.analyze(
            title="Test",
            text="test text",
            context={"tickers": ["ETH", "SOL"]},
        )

    assert len(captured_calls) == 1
    user_msg = captured_calls[0]["messages"][1]["content"]
    assert "ETH" in user_msg or "SOL" in user_msg


@pytest.mark.asyncio
async def test_analyze_raises_on_null_parsed():
    provider = OpenAIAnalysisProvider(api_key="test-key")
    mock_response = _mock_parse_response(None)  # type: ignore

    with patch.object(
        provider._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=mock_response),
    ):
        with pytest.raises(ValueError, match="null parsed output"):
            await provider.analyze(title="Test", text="test")


# ── from_settings() ───────────────────────────────────────────────────────────


def test_from_settings():
    settings = MagicMock()
    settings.openai_api_key = "my-key"
    settings.openai_model = "gpt-4o-mini"
    settings.openai_timeout = 15
    provider = OpenAIAnalysisProvider.from_settings(settings)
    assert provider.model == "gpt-4o-mini"
    assert provider.provider_name == "openai"


# ── Prompt formatting ─────────────────────────────────────────────────────────


def test_format_user_prompt_basic():
    prompt = format_user_prompt("BTC Hits ATH", "Bitcoin reached a new all-time high today.")
    assert "BTC Hits ATH" in prompt
    assert "Bitcoin reached" in prompt


def test_format_user_prompt_with_context():
    prompt = format_user_prompt(
        "ETH update",
        "Ethereum completes upgrade.",
        context={"tickers": ["ETH"], "source_type": "rss_feed"},
    )
    assert "ETH" in prompt
    assert "rss_feed" in prompt


def test_format_user_prompt_empty_text():
    prompt = format_user_prompt("Only Title", "")
    assert "title only" in prompt.lower()
