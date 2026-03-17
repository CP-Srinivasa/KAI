"""Tests for app/analysis/providers/openai_provider.py (structured-outputs version)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.providers.openai_provider import (
    PROMPT_VERSION,
    OpenAIAnalysisProvider,
    _build_user_content,
)
from app.core.enums import MarketScope, SentimentLabel
from app.core.errors import ProviderError


def _make_llm_output() -> LLMAnalysisOutput:
    return LLMAnalysisOutput(
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.9,
        impact_score=0.7,
        confidence_score=0.85,
        novelty_score=0.6,
        spam_probability=0.01,
        market_scope=MarketScope.CRYPTO,
        affected_assets=["BTC"],
        short_reasoning="Halving approaches.",
        recommended_priority=7,
        actionable=True,
    )


def _mock_parse_response(parsed: LLMAnalysisOutput | None) -> MagicMock:
    msg = MagicMock()
    msg.parsed = parsed
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


# ── Metadata ──────────────────────────────────────────────────────────────────


def test_provider_name():
    provider = OpenAIAnalysisProvider(api_key="test")
    assert provider.provider_name == "openai"


def test_provider_model_default():
    provider = OpenAIAnalysisProvider(api_key="test")
    assert provider.model == "gpt-4o"


def test_provider_model_custom():
    provider = OpenAIAnalysisProvider(api_key="test", model="gpt-4o-mini")
    assert provider.model == "gpt-4o-mini"


def test_prompt_version():
    assert PROMPT_VERSION == "v2"


# ── analyze() ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_returns_llm_output():
    provider = OpenAIAnalysisProvider(api_key="test")
    expected = _make_llm_output()
    mock_response = _mock_parse_response(expected)

    with patch.object(
        provider._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await provider.analyze(title="BTC Halving", text="Bitcoin block reward halved.")

    assert isinstance(result, LLMAnalysisOutput)
    assert result.sentiment_label == SentimentLabel.BULLISH
    assert result.actionable is True


@pytest.mark.asyncio
async def test_analyze_raises_provider_error_on_null_parsed():
    provider = OpenAIAnalysisProvider(api_key="test")
    mock_response = _mock_parse_response(None)

    with patch.object(
        provider._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=mock_response),
    ):
        with pytest.raises(ProviderError, match="null parsed output"):
            await provider.analyze(title="Test", text="test")


@pytest.mark.asyncio
async def test_analyze_wraps_api_exception():
    provider = OpenAIAnalysisProvider(api_key="test")

    with patch.object(
        provider._client.beta.chat.completions,
        "parse",
        new=AsyncMock(side_effect=RuntimeError("connection refused")),
    ):
        with pytest.raises(ProviderError, match="connection refused"):
            await provider.analyze(title="Test", text="test")


# ── _build_user_content ───────────────────────────────────────────────────────


def test_build_user_content_basic():
    content = _build_user_content("My Title", "Some text here.", None)
    assert "My Title" in content
    assert "Some text here." in content


def test_build_user_content_empty_text():
    content = _build_user_content("Title Only", "", None)
    assert "Title Only" in content
    assert "Title only" in content or "no body" in content.lower() or "[Title only" in content


def test_build_user_content_truncates_long_text():
    long_text = "x" * 5000
    content = _build_user_content("T", long_text, None)
    assert len(content) < 5100  # truncated to 4000


def test_build_user_content_includes_context():
    content = _build_user_content("T", "text", {"tickers": ["ETH", "SOL"]})
    assert "ETH" in content
    assert "SOL" in content
