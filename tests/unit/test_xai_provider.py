"""Tests for GrokAnalysisProvider (xAI). All LLM calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.enums import MarketScope, SentimentLabel
from app.integrations.xai.provider import GrokAnalysisProvider


def _make_llm_output(**overrides) -> LLMAnalysisOutput:
    defaults: dict = {
        "sentiment_label": SentimentLabel.BULLISH,
        "sentiment_score": 0.6,
        "relevance_score": 0.8,
        "impact_score": 0.5,
        "confidence_score": 0.75,
        "novelty_score": 0.4,
        "spam_probability": 0.05,
        "market_scope": MarketScope.CRYPTO,
        "affected_assets": ["BTC"],
        "affected_sectors": ["Layer1"],
        "short_reasoning": "Fallback provider smoke.",
        "recommended_priority": 6,
        "actionable": True,
        "tags": ["bitcoin"],
    }
    defaults.update(overrides)
    return LLMAnalysisOutput(**defaults)


def _mock_chat_response(payload_json: str) -> MagicMock:
    msg = MagicMock()
    msg.content = payload_json
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def test_provider_name_and_defaults():
    provider = GrokAnalysisProvider(api_key="test-key")
    assert provider.provider_name == "grok"
    assert provider.model == "grok-4"


def test_from_settings():
    settings = MagicMock()
    settings.xai_api_key = "xai-test"
    settings.xai_model = "grok-4"
    settings.xai_timeout = 20
    provider = GrokAnalysisProvider.from_settings(settings)
    assert provider.provider_name == "grok"
    assert provider.model == "grok-4"


@pytest.mark.asyncio
async def test_analyze_roundtrip_parses_json_object():
    provider = GrokAnalysisProvider(api_key="test-key")
    expected = _make_llm_output()
    mock_response = _mock_chat_response(expected.model_dump_json())

    with patch.object(
        provider._client.chat.completions,
        "create",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await provider.analyze(
            title="BTC breakout",
            text="Bitcoin broke resistance at 70k.",
        )

    assert isinstance(result, LLMAnalysisOutput)
    assert result.sentiment_label == SentimentLabel.BULLISH
    assert "BTC" in result.affected_assets


@pytest.mark.asyncio
async def test_analyze_raises_on_empty_content():
    provider = GrokAnalysisProvider(api_key="test-key")
    mock_response = _mock_chat_response("")

    with patch.object(
        provider._client.chat.completions,
        "create",
        new=AsyncMock(return_value=mock_response),
    ):
        with pytest.raises(ValueError, match="empty content"):
            await provider.analyze(title="Test", text="test")
