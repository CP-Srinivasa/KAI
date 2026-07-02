"""Unit tests for VoiceTranscriber."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messaging.voice_transcriber import VoiceTranscriber


def _make_transcriber(
    bot_token: str = "fake-token",
    api_key: str = "sk-test",
) -> VoiceTranscriber:
    return VoiceTranscriber(
        bot_token=bot_token,
        openai_api_key=api_key,
        timeout=5,
    )


class TestVoiceTranscriberConfig:
    def test_is_configured(self) -> None:
        t = _make_transcriber()
        assert t.is_configured is True

    def test_not_configured_without_token(self) -> None:
        t = _make_transcriber(bot_token="")
        assert t.is_configured is False

    def test_not_configured_without_api_key(self) -> None:
        t = _make_transcriber(api_key="")
        assert t.is_configured is False


class TestVoiceTranscriberTranscribe:
    @pytest.mark.asyncio
    async def test_full_pipeline_success(self) -> None:
        t = _make_transcriber()

        # Mock getFile → download → whisper
        get_file_resp = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file_123.oga"},
        }

        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"fake-ogg-audio-data"

        whisper_resp = MagicMock()
        whisper_resp.raise_for_status = MagicMock()
        whisper_resp.json.return_value = {"text": "Bitcoin ist bullish"}

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return get_file_resp
            return download_resp

        async def mock_post(url, headers=None, files=None, data=None):
            return whisper_resp

        with patch("app.messaging.voice_transcriber.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await t.transcribe("file_id_123")

        assert result == "Bitcoin ist bullish"

    @pytest.mark.asyncio
    async def test_returns_none_on_get_file_failure(self) -> None:
        t = _make_transcriber()

        get_file_resp = MagicMock()
        get_file_resp.json.return_value = {"ok": False, "description": "file not found"}

        async def mock_get(url, params=None):
            return get_file_resp

        with patch("app.messaging.voice_transcriber.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await t.transcribe("bad_file_id")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_download_failure(self) -> None:
        t = _make_transcriber()

        get_file_resp = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file.oga"},
        }

        download_resp = MagicMock()
        download_resp.status_code = 404

        async def mock_get(url, params=None):
            if "getFile" in url:
                return get_file_resp
            return download_resp

        with patch("app.messaging.voice_transcriber.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await t.transcribe("file_id_123")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_whisper_error(self) -> None:
        t = _make_transcriber()

        get_file_resp = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file.oga"},
        }
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"audio"

        async def mock_get(url, params=None):
            if "getFile" in url:
                return get_file_resp
            return download_resp

        async def mock_post(url, headers=None, files=None, data=None):
            raise ConnectionError("whisper down")

        with patch("app.messaging.voice_transcriber.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await t.transcribe("file_id_123")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        t = _make_transcriber()

        async def mock_get(url, params=None):
            raise ConnectionError("offline")

        with patch("app.messaging.voice_transcriber.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await t.transcribe("file_id_123")

        assert result is None
