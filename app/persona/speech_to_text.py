"""KAI Speech-to-Text Interface — disabled-by-default stub.

Architecture contract:
- voice_enabled must be explicitly True to activate
- No external API calls until enabled
- All outputs are read-only
- execution_enabled is always False
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class STTRequest:
    """Immutable speech-to-text request."""

    audio_path: str
    language: str = "de"
    model: str = "default"


@dataclass(frozen=True)
class STTResult:
    """Immutable speech-to-text result."""

    success: bool
    transcript: str | None = None
    confidence: float | None = None
    error: str | None = None
    execution_enabled: bool = False


class SpeechToTextInterface:
    """Abstract STT interface — stub only, not connected to any provider."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def transcribe(self, request: STTRequest) -> STTResult:
        """Transcribe speech to text. Returns disabled result if not enabled."""
        if not self._enabled:
            return STTResult(success=False, error="STT is disabled")
        return STTResult(success=False, error="No STT provider configured")
