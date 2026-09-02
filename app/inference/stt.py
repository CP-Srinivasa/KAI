"""Speech-to-text abstraction kept separate from chat inference routes."""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Protocol

import httpx
from openai import AsyncOpenAI

from app.core.settings import InferenceSettings
from app.inference.mode import run_inference_mode
from app.observability.llm_telemetry import record_llm_call


class SpeechToTextProvider(Protocol):
    async def transcribe(
        self,
        audio_data: bytes,
        filename: str,
        *,
        language: str,
        mime_type: str,
    ) -> str | None: ...


class OpenAISpeechToTextProvider:
    """Legacy direct OpenAI transcription; this is the exact off-mode path."""

    def __init__(self, *, api_key: str, model: str = "whisper-1", timeout: float = 90.0) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str,
        *,
        language: str,
        mime_type: str,
    ) -> str | None:
        if not self._api_key:
            return None
        client = AsyncOpenAI(api_key=self._api_key, timeout=self._timeout)
        response = await client.audio.transcriptions.create(
            model=self._model,
            language=language,
            file=(filename, audio_data, mime_type),
        )
        return (response.text or "").strip() or None


class GatewaySpeechToTextProvider:
    """LiteLLM/OpenAI-compatible multipart STT route with KAI telemetry."""

    def __init__(self, settings: InferenceSettings, *, role: str = "primary") -> None:
        self._settings = settings
        self._role = role

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str,
        *,
        language: str,
        mime_type: str,
    ) -> str | None:
        alias = self._settings.route_aliases.get("stt", "").strip()
        if not alias:
            raise ValueError("no STT route alias configured")
        request_id = __import__("uuid").uuid4().hex
        headers = {"X-Request-ID": request_id}
        if self._settings.gateway_api_key:
            headers["Authorization"] = f"Bearer {self._settings.gateway_api_key}"
        endpoint = f"{self._settings.gateway_url.rstrip('/')}/audio/transcriptions"
        started = monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._settings.timeout_seconds) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    files={"file": (filename, audio_data, mime_type)},
                    data={"model": alias, "language": language},
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("STT gateway response is not an object")
            text = str(payload.get("text", "")).strip()
            if not text:
                raise ValueError("STT gateway returned empty transcript")
        except Exception as exc:
            record_llm_call(
                provider="litellm",
                model=alias,
                ok=False,
                latency_ms=(monotonic() - started) * 1000.0,
                role=self._role,
                logical_route="stt",
                requested_model_alias=alias,
                error_type=type(exc).__name__,
                request_id=request_id,
                schema_validation="failed",
                path=Path(self._settings.telemetry_path),
            )
            raise
        record_llm_call(
            provider=str(payload.get("provider") or "unknown"),
            model=str(payload.get("model") or alias),
            ok=True,
            latency_ms=(monotonic() - started) * 1000.0,
            role=self._role,
            logical_route="stt",
            requested_model_alias=alias,
            actual_provider=(str(payload["provider"]) if payload.get("provider") else None),
            actual_model=(str(payload["model"]) if payload.get("model") else None),
            request_id=request_id,
            schema_validation="passed",
            path=Path(self._settings.telemetry_path),
        )
        return text


class ModeSpeechToTextProvider:
    """Default-off STT migration with non-authoritative shadow and direct fallback."""

    def __init__(
        self,
        *,
        mode: str,
        legacy: SpeechToTextProvider,
        gateway: SpeechToTextProvider,
    ) -> None:
        self._mode = mode
        self._legacy = legacy
        self._gateway = gateway

    async def transcribe(
        self,
        audio_data: bytes,
        filename: str,
        *,
        language: str,
        mime_type: str,
    ) -> str | None:
        async def gateway_call() -> str | None:
            return await self._gateway.transcribe(
                audio_data, filename, language=language, mime_type=mime_type
            )

        async def legacy_call() -> str | None:
            return await self._legacy.transcribe(
                audio_data, filename, language=language, mime_type=mime_type
            )

        return await run_inference_mode(
            mode=self._mode,
            gateway_call=gateway_call,
            legacy_call=legacy_call,
        )


def build_speech_to_text_provider(
    *,
    inference: InferenceSettings,
    openai_api_key: str,
    openai_timeout: float,
    model: str = "whisper-1",
) -> SpeechToTextProvider:
    legacy = OpenAISpeechToTextProvider(
        api_key=openai_api_key,
        model=model,
        timeout=openai_timeout,
    )
    mode = inference.effective_mode
    if mode == "off":
        return legacy
    return ModeSpeechToTextProvider(
        mode=mode,
        legacy=legacy,
        gateway=GatewaySpeechToTextProvider(
            inference,
            role="shadow" if mode == "shadow" else "primary",
        ),
    )
