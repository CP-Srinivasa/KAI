"""Disabled-by-default text-to-speech interface stub for future KAI channels."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextToSpeechRequest:
    text: str
    voice_profile: str = "default"
    language: str = "en"


@dataclass(frozen=True)
class TextToSpeechResult:
    enabled: bool = False
    status: str = "disabled"
    audio_ref: str | None = None
    reason: str = "Text-to-speech interface is disabled by default."


class TextToSpeechInterface:
    """Safe no-op TTS surface until an approved backend is connected."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def synthesize(self, request: TextToSpeechRequest) -> TextToSpeechResult:
        if not self._enabled:
            return TextToSpeechResult()
        return TextToSpeechResult(
            enabled=True,
            status="noop",
            reason=(
                "Text-to-speech backend is not connected. "
                f"Request accepted for voice_profile={request.voice_profile!r}."
            ),
        )
