"""Tests for GeminiAnalysisProvider — all SDK calls mocked via asyncio.to_thread."""

import json

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.enums import MarketScope, SentimentLabel
from app.integrations.gemini.provider import GeminiAnalysisProvider

# ── helpers ───────────────────────────────────────────────────────────────────

_VALID_OUTPUT = {
    "sentiment_label": "bearish",
    "sentiment_score": -0.8,
    "relevance_score": 0.9,
    "impact_score": 0.5,
    "confidence_score": 0.9,
    "novelty_score": 0.5,
    "spam_probability": 0.0,
    "market_scope": "crypto",
    "affected_assets": ["ETH"],
    "short_reasoning": "Ethereum is dumping",
}


def _fake_to_thread(text: str):
    async def _impl(func, *args, **kwargs):
        class FakeResponse:
            pass

        FakeResponse.text = text
        return FakeResponse()

    return _impl


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def provider():
    return GeminiAnalysisProvider(api_key="fake-key")


# ── metadata ──────────────────────────────────────────────────────────────────


def test_provider_name(provider):
    assert provider.provider_name == "gemini"


def test_provider_model_default(provider):
    assert provider.model == "gemini-2.5-flash"


def test_provider_model_custom():
    p = GeminiAnalysisProvider(api_key="k", model="gemini-1.5-pro")
    assert p.model == "gemini-1.5-pro"


def test_provider_stores_timeout():
    p = GeminiAnalysisProvider(api_key="k", timeout=60)
    assert p._timeout == 60


# ── analyze() ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_success(provider, monkeypatch):
    monkeypatch.setattr("asyncio.to_thread", _fake_to_thread(json.dumps(_VALID_OUTPUT)))

    result = await provider.analyze("ETH dumps", "Ethereum hits new lows")

    assert isinstance(result, LLMAnalysisOutput)
    assert result.sentiment_label == SentimentLabel.BEARISH
    assert result.sentiment_score == -0.8
    assert "ETH" in result.affected_assets
    assert result.market_scope == MarketScope.CRYPTO
    assert result.short_reasoning == "Ethereum is dumping"


@pytest.mark.asyncio
async def test_analyze_empty_response_raises(provider, monkeypatch):
    monkeypatch.setattr("asyncio.to_thread", _fake_to_thread(""))

    with pytest.raises(ValueError, match="empty structured output"):
        await provider.analyze("Title", "Text")


@pytest.mark.asyncio
async def test_analyze_text_truncated(provider, monkeypatch):
    """Text must be truncated to _MAX_TEXT_CHARS before sending."""
    from app.integrations.gemini import provider as mod

    captured: list = []

    async def fake_to_thread(func, *args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})

        class FakeResponse:
            text = json.dumps(_VALID_OUTPUT)

        return FakeResponse()

    monkeypatch.setattr("asyncio.to_thread", fake_to_thread)

    long_text = "z" * (mod._MAX_TEXT_CHARS + 1000)
    await provider.analyze("Title", long_text)

    assert len(captured) == 1
    # The content string passed to generate_content should not exceed limit + prompt overhead
    content_arg = (
        captured[0]["args"][1] if captured[0]["args"] else captured[0]["kwargs"].get("contents", "")
    )
    assert len(content_arg) <= mod._MAX_TEXT_CHARS + 500


@pytest.mark.asyncio
async def test_analyze_uses_json_mime_type(provider, monkeypatch):
    """GenerateContentConfig must request application/json response."""
    from google.genai import types

    captured_configs: list = []

    async def fake_to_thread(func, *args, **kwargs):
        captured_configs.append(kwargs.get("config"))

        class FakeResponse:
            text = json.dumps(_VALID_OUTPUT)

        return FakeResponse()

    monkeypatch.setattr("asyncio.to_thread", fake_to_thread)
    await provider.analyze("Title", "Text")

    assert len(captured_configs) == 1
    cfg = captured_configs[0]
    assert cfg is not None
    assert isinstance(cfg, types.GenerateContentConfig)
    assert cfg.response_mime_type == "application/json"


# ── from_settings() ───────────────────────────────────────────────────────────


def test_from_settings():
    from unittest.mock import MagicMock

    settings = MagicMock()
    settings.gemini_api_key = "gk-test"
    settings.gemini_model = "gemini-1.5-pro"
    settings.gemini_timeout = 45

    p = GeminiAnalysisProvider.from_settings(settings)

    assert p.provider_name == "gemini"
    assert p.model == "gemini-1.5-pro"
    assert p._timeout == 45
