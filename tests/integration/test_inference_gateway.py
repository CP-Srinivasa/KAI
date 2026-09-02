from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.core.enums import MarketScope, SentimentLabel
from app.core.settings import InferenceSettings
from app.inference.analysis_provider import (
    GatewayAnalysisProvider,
    InferenceModeAnalysisProvider,
)
from app.inference.models import InferenceRoute
from app.inference.router import InferenceRouter


def _analysis(label: SentimentLabel, priority: int) -> LLMAnalysisOutput:
    return LLMAnalysisOutput(
        sentiment_label=label,
        sentiment_score=0.5 if label is SentimentLabel.BULLISH else -0.5,
        relevance_score=0.8,
        impact_score=0.7,
        confidence_score=0.8,
        novelty_score=0.4,
        spam_probability=0.1,
        market_scope=MarketScope.CRYPTO,
        recommended_priority=priority,
    )


class _Legacy(BaseAnalysisProvider):
    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return "gpt-current"

    async def analyze(self, title: str, text: str, context=None) -> LLMAnalysisOutput:
        result = _analysis(SentimentLabel.BULLISH, 8)
        result.provider_used = "openai"
        result.model_used = self.model
        return result


def _settings(tmp_path: Path, mode: str) -> InferenceSettings:
    return InferenceSettings(
        enabled=True,
        mode=mode,
        route_aliases={
            "bulk": "bulk",
            "standard": "standard",
            "reasoning": "reasoning",
            "critical": "critical",
            "stt": "stt",
        },
        route_fallbacks={},
        retries_per_model=0,
        telemetry_path=str(tmp_path / "telemetry.jsonl"),
        shadow_comparison_path=str(tmp_path / "shadow.jsonl"),
        _env_file=None,
    )


@pytest.mark.asyncio
async def test_shadow_gateway_never_replaces_current_analysis(tmp_path: Path) -> None:
    candidate = _analysis(SentimentLabel.BEARISH, 3)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "candidate-model",
                "provider": "gemini",
                "choices": [{"message": {"content": candidate.model_dump_json()}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 5},
            },
        )

    settings = _settings(tmp_path, "shadow")
    router = InferenceRouter(settings, transport=httpx.MockTransport(handler))
    provider = InferenceModeAnalysisProvider(
        mode="shadow",
        gateway=GatewayAnalysisProvider(router, route=InferenceRoute.STANDARD, role="shadow"),
        legacy=_Legacy(),
        settings=settings,
    )
    result = await provider.analyze("title", "long enough document text", {})
    assert result.sentiment_label is SentimentLabel.BULLISH
    row = json.loads((tmp_path / "shadow.jsonl").read_text("utf-8"))
    assert row["divergence"]["direction_disagreement"] is True
    assert row["authoritative"] == "current"
    assert row["influences_execution"] is False


@pytest.mark.asyncio
async def test_primary_gateway_failure_uses_legacy_fallback(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    settings = _settings(tmp_path, "primary")
    router = InferenceRouter(settings, transport=httpx.MockTransport(handler))
    provider = InferenceModeAnalysisProvider(
        mode="primary",
        gateway=GatewayAnalysisProvider(router),
        legacy=_Legacy(),
        settings=settings,
    )
    result = await provider.analyze("title", "text", {})
    assert result.provider_used == "openai"
    assert result.model_used == "gpt-current"
    assert result.logical_route == "standard"
