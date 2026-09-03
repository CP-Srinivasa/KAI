"""NEO-P-002: every LLM call site leaves a v2 telemetry row.

Before this, five call sites (chat, whisper x2, intent, consensus) and every
failed EnsembleProvider attempt were invisible. These tests assert the trace,
not the transport — each call site keeps its original client and patch point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.enums import MarketScope, SentimentLabel
from tests.unit.factories import make_llm_output


@pytest.fixture
def telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the default telemetry sink; resolved at call time."""
    sink = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    return sink


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


class _SdkError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


# ── EnsembleProvider: one row per attempt (NEO-F-004) ────────────────────────


def _provider(name: str, *, model: str, fail: BaseException | None = None) -> Any:
    p = AsyncMock()
    p.provider_name = name
    p.model = model
    if fail is not None:
        p.analyze = AsyncMock(side_effect=fail)
    else:
        out = make_llm_output(
            sentiment_label=SentimentLabel.BULLISH,
            market_scope=MarketScope.CRYPTO,
        )
        out.prompt_tokens = 210
        out.completion_tokens = 40
        p.analyze = AsyncMock(return_value=out)
    return p


async def test_ensemble_records_every_attempt(telemetry: Path) -> None:
    from app.analysis.ensemble.provider import EnsembleProvider

    ensemble = EnsembleProvider(
        [
            _provider("openai", model="gpt-4o", fail=_SdkError(429)),
            _provider("gemini", model="gemini-2.5-flash"),
        ]
    )
    result = await ensemble.analyze("Bitcoin ETF approved", "Bitcoin " * 60)

    assert result.provider_used == "gemini"
    rows = sorted(_rows(telemetry), key=lambda r: r["chain_position"])
    assert len(rows) == 2

    assert rows[0]["provider"] == "openai" and rows[0]["model"] == "gpt-4o"
    assert rows[0]["ok"] is False
    assert rows[0]["error_class"] == "rate_limit" and rows[0]["http_status"] == 429
    assert rows[0]["outcome"] == "fallthrough"

    assert rows[1]["provider"] == "gemini" and rows[1]["ok"] is True
    assert rows[1]["outcome"] == "success"
    assert rows[1]["prompt_tokens"] == 210 and rows[1]["completion_tokens"] == 40
    assert all(r["purpose"] == "analysis" for r in rows)


async def test_ensemble_last_failure_is_exhausted_not_fallthrough(telemetry: Path) -> None:
    from app.analysis.ensemble.provider import EnsembleProvider

    ensemble = EnsembleProvider(
        [
            _provider("openai", model="gpt-4o", fail=_SdkError(500)),
            _provider("gemini", model="gemini-2.5-flash", fail=_SdkError(503)),
        ]
    )
    with pytest.raises(RuntimeError):
        await ensemble.analyze("t", "Bitcoin " * 60)

    rows = sorted(_rows(telemetry), key=lambda r: r["chain_position"])
    assert [r["outcome"] for r in rows] == ["fallthrough", "exhausted"]


async def test_ensemble_shares_the_ambient_correlation_id(telemetry: Path) -> None:
    from app.ai.audit import correlation_scope
    from app.analysis.ensemble.provider import EnsembleProvider

    ensemble = EnsembleProvider(
        [
            _provider("openai", model="gpt-4o", fail=_SdkError(429)),
            _provider("gemini", model="gemini-2.5-flash"),
        ]
    )
    with correlation_scope("req_scope_0001"):
        await ensemble.analyze("t", "Bitcoin " * 60)

    assert {r["correlation_id"] for r in _rows(telemetry)} == {"req_scope_0001"}


async def test_correlation_scope_does_not_leak_after_exit(telemetry: Path) -> None:
    from app.ai.audit import correlation_scope, current_correlation_id

    assert current_correlation_id() is None
    with correlation_scope("req_a"):
        assert current_correlation_id() == "req_a"
    assert current_correlation_id() is None


# ── TextIntentProcessor ──────────────────────────────────────────────────────


async def test_text_intent_records_call_with_injected_correlation_id(telemetry: Path) -> None:
    from app.messaging.text_intent import TextIntentProcessor

    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps({"intent": "chat"})))]
    resp.usage = MagicMock(prompt_tokens=17, completion_tokens=5)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)

    processor = TextIntentProcessor(api_key="test-key", model="gpt-4o", timeout=5)
    with patch("app.messaging.text_intent.AsyncOpenAI", MagicMock(return_value=client)):
        await processor.process("was geht", correlation_id="req_intent_1")

    rows = _rows(telemetry)
    assert len(rows) == 1
    assert rows[0]["purpose"] == "intent"
    assert rows[0]["provider"] == "openai" and rows[0]["model"] == "gpt-4o"
    assert rows[0]["correlation_id"] == "req_intent_1"
    assert rows[0]["ok"] is True
    assert rows[0]["prompt_tokens"] == 17 and rows[0]["completion_tokens"] == 5


async def test_text_intent_records_failure_and_still_falls_back(telemetry: Path) -> None:
    from app.messaging.text_intent import TextIntentProcessor

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_SdkError(401))

    processor = TextIntentProcessor(api_key="test-key", model="gpt-4o", timeout=5)
    with patch("app.messaging.text_intent.AsyncOpenAI", MagicMock(return_value=client)):
        result = await processor.process("was geht")

    # Caller behaviour unchanged: the scope re-raises into the existing handler.
    assert result.intent == "chat"
    rows = _rows(telemetry)
    assert len(rows) == 1
    assert rows[0]["ok"] is False and rows[0]["error_class"] == "auth"


# ── VoiceTranscriber (httpx transport deliberately unchanged) ────────────────


async def test_voice_transcriber_records_stt_call(telemetry: Path) -> None:
    from app.messaging.voice_transcriber import VoiceTranscriber

    whisper_resp = MagicMock()
    whisper_resp.raise_for_status = MagicMock()
    whisper_resp.json.return_value = {"text": "Bitcoin ist bullish"}

    client = MagicMock()
    client.post = AsyncMock(return_value=whisper_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    t = VoiceTranscriber(bot_token="fake", openai_api_key="test-key", timeout=5)
    with patch("app.messaging.voice_transcriber.httpx.AsyncClient", MagicMock(return_value=client)):
        text = await t._whisper_transcribe(b"audio", "voice/file_1.oga")

    assert text == "Bitcoin ist bullish"
    rows = _rows(telemetry)
    assert len(rows) == 1
    assert rows[0]["purpose"] == "stt"
    assert rows[0]["provider"] == "openai" and rows[0]["model"] == "whisper-1"
    assert rows[0]["ok"] is True


# ── SignalConsensusValidator ─────────────────────────────────────────────────


async def test_consensus_records_one_row_per_validator(telemetry: Path) -> None:
    from app.trading.signal_consensus import (
        GEMINI_OPENAI_BASE_URL,
        SignalConsensusValidator,
        ValidatorConfig,
    )

    payload = json.dumps({"agree": True, "confidence": 0.8, "reasoning": "ok"})
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=payload))]
    resp.usage = MagicMock(prompt_tokens=90, completion_tokens=12)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)

    validator = SignalConsensusValidator.multi(
        ValidatorConfig(api_key="k1", model="gpt-4o-mini"),
        ValidatorConfig(api_key="k2", model="gemini-2.5-flash", base_url=GEMINI_OPENAI_BASE_URL),
    )
    with patch("app.trading.signal_consensus.AsyncOpenAI", MagicMock(return_value=client)):
        await validator._validate_single(validator._configs[0], "msg", chain_position=0)
        await validator._validate_single(validator._configs[1], "msg", chain_position=1)

    rows = sorted(_rows(telemetry), key=lambda r: r["chain_position"])
    assert len(rows) == 2
    assert all(r["purpose"] == "consensus" and r["role"] == "validator" for r in rows)
    assert rows[0]["provider"] == "openai" and rows[0]["model"] == "gpt-4o-mini"
    assert rows[1]["provider"] == "gemini" and rows[1]["model"] == "gemini-2.5-flash"


async def test_consensus_failure_is_recorded_and_stays_fail_closed(telemetry: Path) -> None:
    from app.trading.signal_consensus import SignalConsensusValidator, ValidatorConfig

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_SdkError(429))

    validator = SignalConsensusValidator.multi(ValidatorConfig(api_key="k", model="gpt-4o-mini"))
    with patch("app.trading.signal_consensus.AsyncOpenAI", MagicMock(return_value=client)):
        result = await validator._validate_single(validator._configs[0], "msg")

    assert result.agreed is False  # fail-closed behaviour unchanged
    rows = _rows(telemetry)
    assert len(rows) == 1
    assert rows[0]["ok"] is False and rows[0]["error_class"] == "rate_limit"


# ── kai_chat_engine ──────────────────────────────────────────────────────────


async def test_chat_engine_records_smalltalk_call(
    telemetry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.messaging import kai_chat_engine

    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content="Klar."))]
    resp.usage = MagicMock(prompt_tokens=31, completion_tokens=4)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)

    settings = MagicMock()
    settings.providers.openai_api_key = "test-key"
    settings.providers.openai_model = "gpt-4o"
    monkeypatch.setattr(kai_chat_engine, "get_settings", lambda: settings)
    monkeypatch.setattr("openai.AsyncOpenAI", MagicMock(return_value=client))

    reply = await kai_chat_engine._respond_smalltalk("hi", "de")

    assert reply.source == "gpt4o"
    rows = _rows(telemetry)
    assert len(rows) == 1
    assert rows[0]["purpose"] == "chat" and rows[0]["provider"] == "openai"
    assert rows[0]["model"] == "gpt-4o" and rows[0]["ok"] is True
    assert rows[0]["prompt_tokens"] == 31
