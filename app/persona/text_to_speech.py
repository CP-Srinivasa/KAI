"""KAI Text-to-Speech Interface — disabled-by-default stub.

Architecture contract:
- voice_enabled must be explicitly True to activate
- No external API calls until enabled
- All outputs are read-only summaries
- execution_enabled is always False
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TTSRequest:
    """Immutable text-to-speech request."""

    text: str
    language: str = "de"
    voice_id: str = "default"
    speed: float = 1.0


@dataclass(frozen=True)
class TTSResult:
    """Immutable text-to-speech result."""

    success: bool
    audio_path: str | None = None
    error: str | None = None
    execution_enabled: bool = False


class TextToSpeechInterface:
    """Abstract TTS interface — stub only, not connected to any provider."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def synthesize(self, request: TTSRequest) -> TTSResult:
        """Synthesize speech from text. Returns disabled result if not enabled."""
        if not self._enabled:
            return TTSResult(success=False, error="TTS is disabled")
        # Future: delegate to provider (e.g., OpenAI TTS, Google Cloud TTS)
        return TTSResult(success=False, error="No TTS provider configured")
