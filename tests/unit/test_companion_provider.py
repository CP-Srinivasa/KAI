import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis.factory import create_provider
from app.analysis.providers.companion import InternalCompanionProvider
from app.core.settings import AppSettings, ProviderSettings


def test_companion_settings_validation():
    # Should pass (localhost)
    settings = ProviderSettings(companion_model_endpoint="http://localhost:11434")
    assert settings.companion_model_endpoint == "http://localhost:11434"

    # Should pass (internal Docker IP)
    settings = ProviderSettings(companion_model_endpoint="http://host.docker.internal:8000")
    assert settings.companion_model_endpoint == "http://host.docker.internal:8000"

    # Should fail (external endpoint)
    with pytest.raises(ValueError, match="MUST be localhost or internal"):
        ProviderSettings(companion_model_endpoint="https://api.openai.com/v1")


def test_factory_internal_branch():
    app_settings = AppSettings()
    app_settings.providers.companion_model_endpoint = None

    # When endpoint is None, factory returns None for companion
    provider = create_provider("companion", app_settings)
    assert provider is None

    # When endpoint is configured, returns companion provider
    app_settings.providers.companion_model_endpoint = "http://localhost:11434"
    provider = create_provider("companion", app_settings)
    assert isinstance(provider, InternalCompanionProvider)
    assert provider.endpoint == "http://localhost:11434"
    assert provider.model == "kai-analyst-v1"


@pytest.mark.asyncio
async def test_companion_analyze_success():
    provider = InternalCompanionProvider("http://localhost:11434", "kai-analyst-v1")

    mock_response = {
        "id": "chatcmpl-123",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "co_thought": "Test reasoning.",
                    "sentiment_label": "bullish",
                    "sentiment_score": 0.8,
                    "relevance_score": 0.9,
                    "impact_score": 0.7,
                    "priority_score": 8,
                    "market_scope": "macro",
                    "affected_assets": ["BTC"],
                    "tags": ["crypto"]
                })
            }
        }]
    }

    mock_post = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response
    mock_post.return_value = mock_resp

    with patch("httpx.AsyncClient.post", mock_post):
        result = await provider.analyze("Test Title", "Test Text")

        assert result.sentiment_label.value == "bullish"
        assert result.sentiment_score == 0.8
        assert result.impact_score == 0.7
        assert result.actionable is True
        assert result.recommended_priority == 8
        assert result.short_reasoning == "Test reasoning."
        assert result.affected_assets == ["BTC"]
        assert result.market_scope.value == "macro"


@pytest.mark.asyncio
async def test_companion_analyze_prefers_summary_field() -> None:
    provider = InternalCompanionProvider("http://localhost:11434", "kai-analyst-v1")

    mock_response = {
        "id": "chatcmpl-123",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "summary": "Structured summary.",
                    "sentiment_label": "neutral",
                    "sentiment_score": 0.1,
                    "relevance_score": 0.6,
                    "impact_score": 0.4,
                    "priority_score": 6,
                    "market_scope": "crypto",
                    "affected_assets": ["ETH"],
                    "tags": ["etf"],
                })
            }
        }]
    }

    mock_post = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response
    mock_post.return_value = mock_resp

    with patch("httpx.AsyncClient.post", mock_post):
        result = await provider.analyze("Test Title", "Test Text")

    assert result.short_reasoning == "Structured summary."
    assert result.affected_assets == ["ETH"]
    assert result.market_scope.value == "crypto"


@pytest.mark.asyncio
async def test_companion_analyze_impact_capped():
    provider = InternalCompanionProvider("http://localhost:11434", "kai-analyst-v1")

    mock_response = {
        "id": "chatcmpl-123",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "impact_score": 1.0,  # Should be capped at 0.8 locally
                })
            }
        }]
    }

    mock_post = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response
    mock_post.return_value = mock_resp

    with patch("httpx.AsyncClient.post", mock_post):
        result = await provider.analyze("Test", "Text")
        assert result.impact_score == 0.8  # Capped limit (Invariant I-17)


@pytest.mark.asyncio
async def test_companion_analyze_http_error_raises_runtime_error() -> None:
    provider = InternalCompanionProvider("http://localhost:11434", "kai-analyst-v1")

    mock_post = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.text = "service unavailable"
    mock_post.return_value = mock_resp

    with patch("httpx.AsyncClient.post", mock_post):
        with pytest.raises(RuntimeError, match="Companion model request failed"):
            await provider.analyze("Test", "Text")
