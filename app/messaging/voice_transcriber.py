"""Voice message transcription for Telegram operator messages.

Downloads voice messages from Telegram and transcribes them via
OpenAI Whisper API.  The transcribed text can then be fed into
the TextIntentProcessor for intent classification.
"""

from __future__ import annotations

import logging

import httpx

from app.inference.stt import OpenAISpeechToTextProvider, SpeechToTextProvider

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"


class VoiceTranscriber:
    """Download Telegram voice messages and transcribe via Whisper."""

    def __init__(
        self,
        bot_token: str,
        openai_api_key: str,
        whisper_model: str = "whisper-1",
        timeout: int = 30,
        speech_provider: SpeechToTextProvider | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._openai_api_key = openai_api_key
        self._whisper_model = whisper_model
        self._timeout = timeout
        self._speech_provider = speech_provider or OpenAISpeechToTextProvider(
            api_key=openai_api_key,
            model=whisper_model,
            timeout=timeout,
        )
        self._speech_provider_injected = speech_provider is not None

    @property
    def is_configured(self) -> bool:
        return bool(self._bot_token) and (
            bool(self._openai_api_key) or self._speech_provider_injected
        )

    async def transcribe(self, file_id: str) -> str | None:
        """Download voice from Telegram and transcribe via Whisper.

        Returns the transcribed text, or ``None`` on failure.
        """
        file_path = await self._get_file_path(file_id)
        if not file_path:
            return None

        audio_data = await self._download_file(file_path)
        if not audio_data:
            return None

        return await self._whisper_transcribe(audio_data, file_path)

    async def _get_file_path(self, file_id: str) -> str | None:
        """Resolve a Telegram file_id to a downloadable file_path."""
        url = f"{_TELEGRAM_API_BASE}/bot{self._bot_token}/getFile"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params={"file_id": file_id})
                data = resp.json()
                if data.get("ok"):
                    return str(data["result"]["file_path"])
                logger.error("[VOICE] getFile failed: %s", data)
        except Exception as exc:  # noqa: BLE001
            logger.error("[VOICE] getFile error: %s", exc)
        return None

    async def _download_file(self, file_path: str) -> bytes | None:
        """Download a file from Telegram's file storage."""
        url = f"{_TELEGRAM_API_BASE}/file/bot{self._bot_token}/{file_path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.content
                logger.error("[VOICE] Download failed: HTTP %s", resp.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.error("[VOICE] Download error: %s", exc)
        return None

    async def _whisper_transcribe(self, audio_data: bytes, filename: str) -> str | None:
        """Send audio bytes to OpenAI Whisper API for transcription."""
        # Determine extension from Telegram file_path (e.g. "voice/file_123.oga")
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "ogg"

        try:
            text = await self._speech_provider.transcribe(
                audio_data,
                f"voice.{ext}",
                language="de",
                mime_type="audio/ogg",
            )
            if text:
                logger.info("[VOICE] Transcribed %d chars", len(text))
                return text
            logger.warning("[VOICE] Speech provider returned empty transcript")
        except Exception as exc:  # noqa: BLE001
            logger.error("[VOICE] Speech provider error: %s", exc)
        return None
