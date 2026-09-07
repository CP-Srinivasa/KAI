"""Productive caller wiring through the one app/ai control plane."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.config import InferenceSettings
from app.ai.models import AttemptTrace
from app.analysis.ai_control_plane import ControlPlaneAnalysisProvider
from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.core.enums import MarketScope, SentimentLabel
from app.integrations.litellm.provider import LiteLLMResponse


def _settings(route: str, mode: str = "primary") -> InferenceSettings:
    return InferenceSettings(
        enabled=True,
        mode_ceiling=mode,
        route_modes={route: mode},
        max_attempts=1,
    )


def _trace(alias: str = "kai-standard") -> AttemptTrace:
    return AttemptTrace(
        transport="litellm",
        requested_model=alias,
        latency_ms=1.0,
        actual_provider="openai",
        actual_model="gpt-4o-mini",
        cost_usd=0.001,
    )


def _chat_body(content: str) -> dict[str, object]:
    return {
        "model": "gpt-4o-mini",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }


class _DirectAnalysis(BaseAnalysisProvider):
    calls = 0

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return "gpt-4o"

    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, object] | None = None,
    ) -> LLMAnalysisOutput:
        self.calls += 1
        raise AssertionError("graduated primary must not call direct after LiteLLM success")


def _analysis_output() -> LLMAnalysisOutput:
    return LLMAnalysisOutput(
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.6,
        relevance_score=0.9,
        impact_score=0.7,
        confidence_score=0.8,
        novelty_score=0.5,
        spam_probability=0.0,
        market_scope=MarketScope.CRYPTO,
    )


async def test_analysis_primary_uses_litellm_typed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _analysis_output()
    monkeypatch.setattr(
        "app.ai.runtime.call_litellm_async",
        AsyncMock(
            return_value=LiteLLMResponse(
                trace=_trace(),
                body=_chat_body(expected.model_dump_json()),
            )
        ),
    )
    direct = _DirectAnalysis()
    provider = ControlPlaneAnalysisProvider(direct, _settings("standard"))
    result = await provider.analyze("Bitcoin", "Bitcoin " * 20)
    assert result.sentiment_label == SentimentLabel.BULLISH
    assert result.provider_used == "openai"
    assert direct.calls == 0


async def test_text_intent_primary_uses_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.messaging import text_intent

    body = _chat_body(
        json.dumps({"intent": "command", "response": "OK", "mapped_command": "status"})
    )
    monkeypatch.setattr(
        "app.ai.runtime.call_litellm_async",
        AsyncMock(return_value=LiteLLMResponse(trace=_trace("kai-critical"), body=body)),
    )
    monkeypatch.setattr(
        "app.ai.runtime.inference_settings",
        lambda source=None: _settings("critical"),
    )
    monkeypatch.setattr(text_intent, "AsyncOpenAI", MagicMock(side_effect=AssertionError))
    result = await text_intent.TextIntentProcessor(api_key="direct-key").process("Status")
    assert result.intent == "command"
    assert result.mapped_command == "status"


async def test_kai_chat_primary_uses_control_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.messaging import kai_chat_engine

    monkeypatch.setattr(
        "app.ai.runtime.call_litellm_async",
        AsyncMock(return_value=LiteLLMResponse(trace=_trace(), body=_chat_body("Klar."))),
    )
    configured = SimpleNamespace(
        providers=SimpleNamespace(openai_api_key="direct-key", openai_model="gpt-4o"),
        ai_gateway=_settings("standard"),
    )
    monkeypatch.setattr(kai_chat_engine, "get_settings", lambda: configured)
    monkeypatch.setattr("openai.AsyncOpenAI", MagicMock(side_effect=AssertionError))
    result = await kai_chat_engine._respond_smalltalk("Hallo", "de")
    assert result.reply == "Klar."
    assert result.source == "litellm"


async def test_voice_stt_primary_uses_same_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.messaging import voice_transcriber

    transport = AsyncMock(
        return_value=LiteLLMResponse(
            trace=_trace("kai-stt"),
            body={"text": "Bitcoin ist bullish"},
        )
    )
    monkeypatch.setattr("app.ai.runtime.call_litellm_async", transport)
    monkeypatch.setattr(
        "app.ai.runtime.inference_settings",
        lambda source=None: _settings("stt"),
    )
    monkeypatch.setattr(
        voice_transcriber.httpx,
        "AsyncClient",
        MagicMock(side_effect=AssertionError("direct Whisper must not run")),
    )
    transcriber = voice_transcriber.VoiceTranscriber("bot", "direct-key")
    result = await transcriber._whisper_transcribe(b"audio", "voice.oga")
    assert result == "Bitcoin ist bullish"
    assert transport.await_args.kwargs["endpoint"] == "/v1/audio/transcriptions"


async def test_consensus_litellm_can_never_replace_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.trading import signal_consensus

    lite_payload = json.dumps({"agree": True, "confidence": 1.0, "reasoning": "lite"})
    monkeypatch.setattr(
        "app.ai.runtime.call_litellm_async",
        AsyncMock(
            return_value=LiteLLMResponse(
                trace=_trace("kai-reasoning"),
                body=_chat_body(lite_payload),
            )
        ),
    )
    monkeypatch.setattr(
        "app.ai.runtime.inference_settings",
        lambda source=None: _settings("reasoning"),
    )
    direct_response = MagicMock()
    direct_response.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps(
                    {"agree": False, "confidence": 0.4, "reasoning": "direct-authoritative"}
                )
            )
        )
    ]
    direct_response.usage = None
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=direct_response)
    monkeypatch.setattr(signal_consensus, "AsyncOpenAI", MagicMock(return_value=client))
    validator = signal_consensus.SignalConsensusValidator(api_key="direct-key")
    result = await validator._validate_single(validator._configs[0], "signal")
    assert result.agreed is False
    assert result.reasoning == "direct-authoritative"
