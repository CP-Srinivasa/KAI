from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.core.settings import InferenceSettings
from app.inference.stt import (
    ModeSpeechToTextProvider,
    OpenAISpeechToTextProvider,
    build_speech_to_text_provider,
)


@dataclass
class _Speech:
    result: str | None = None
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str,
        *,
        language: str,
        mime_type: str,
    ) -> str | None:
        self.calls.append(filename)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_stt_shadow_keeps_legacy_transcript() -> None:
    legacy = _Speech(result="authoritative")
    gateway = _Speech(result="candidate")
    provider = ModeSpeechToTextProvider(mode="shadow", legacy=legacy, gateway=gateway)
    result = await provider.transcribe(b"audio", "voice.oga", language="de", mime_type="audio/ogg")
    assert result == "authoritative"
    assert legacy.calls == ["voice.oga"] and gateway.calls == ["voice.oga"]


@pytest.mark.asyncio
async def test_stt_primary_uses_gateway() -> None:
    legacy = _Speech(result="legacy")
    gateway = _Speech(result="gateway")
    provider = ModeSpeechToTextProvider(mode="primary", legacy=legacy, gateway=gateway)
    result = await provider.transcribe(b"audio", "voice.oga", language="de", mime_type="audio/ogg")
    assert result == "gateway"
    assert not legacy.calls


@pytest.mark.asyncio
async def test_stt_primary_gateway_failure_falls_back_direct() -> None:
    legacy = _Speech(result="legacy")
    gateway = _Speech(error=TimeoutError("gateway timeout"))
    provider = ModeSpeechToTextProvider(mode="primary", legacy=legacy, gateway=gateway)
    result = await provider.transcribe(b"audio", "voice.oga", language="de", mime_type="audio/ogg")
    assert result == "legacy"


def test_stt_off_factory_is_exact_direct_provider() -> None:
    settings = InferenceSettings(enabled=False, mode="primary", _env_file=None)
    provider = build_speech_to_text_provider(
        inference=settings,
        openai_api_key="not-a-real-key",
        openai_timeout=30,
    )
    assert isinstance(provider, OpenAISpeechToTextProvider)
