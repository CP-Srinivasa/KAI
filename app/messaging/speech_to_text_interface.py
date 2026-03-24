"""Disabled-by-default speech-to-text interface stub for future KAI channels."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechToTextRequest:
    audio_ref: str
    language_hint: str | None = None


@dataclass(frozen=True)
class SpeechToTextResult:
    enabled: bool = False
    status: str = "disabled"
    text: str = ""
    reason: str = "Speech-to-text interface is disabled by default."


class SpeechToTextInterface:
    """Safe no-op STT surface until an approved backend is connected."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def transcribe(self, request: SpeechToTextRequest) -> SpeechToTextResult:
        if not self._enabled:
            return SpeechToTextResult()
        return SpeechToTextResult(
            enabled=True,
            status="noop",
            reason=(
                "Speech-to-text backend is not connected. "
                f"Request accepted for audio_ref={request.audio_ref!r}."
            ),
        )
