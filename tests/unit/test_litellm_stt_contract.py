"""Spracherkennung ueber LiteLLM (LiteLLM-Audit 27.09., Befund C).

Bis 04046c68 pruefte der gemeinsame Transport JEDE 200 auf das Chatformat
``choices``. Eine vollstaendige Transkription ``{"text": "Hallo KAI"}`` wurde
dadurch zu ``empty``/``no_choices`` und fiel auf den Direktanbieter zurueck --
genau dann ins Leere, wenn der ausgefallen war. Die Reserve, fuer die der
Transport da ist, fehlte im einzigen Moment, in dem sie gebraucht wurde.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ai.config import InferenceSettings
from app.integrations.litellm.provider import trace_from_response
from app.messaging.voice_transcriber import VoiceTranscriber

STT = "/v1/audio/transcriptions"


def _antwort(body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        headers={"x-litellm-model-name": "openai/whisper-1"},
        request=httpx.Request("POST", f"http://127.0.0.1:4000{STT}"),
    )


def test_eine_transkription_ist_kein_leerer_chat() -> None:
    trace = trace_from_response(
        _antwort({"text": "Hallo KAI"}), requested_model="kai-stt", latency_ms=1.0, endpoint=STT
    )
    assert trace.ok
    assert trace.error_class is None
    assert "empty_reason" not in trace.detail


@pytest.mark.parametrize(
    ("body", "grund"),
    [({}, "no_text"), ({"text": ""}, "empty_text"), ({"text": "   "}, "empty_text")],
)
def test_eine_leere_transkription_bleibt_leer(body: dict[str, Any], grund: str) -> None:
    trace = trace_from_response(
        _antwort(body), requested_model="kai-stt", latency_ms=1.0, endpoint=STT
    )
    assert trace.error_class == "empty"
    assert trace.detail["empty_reason"] == grund


def test_der_chatpfad_prueft_weiter_auf_choices() -> None:
    """Die Ausnahme gilt dem Endpunkt, nicht jedem Koerper mit ``text``."""
    trace = trace_from_response(_antwort({"text": "Hallo"}), requested_model="x", latency_ms=1.0)
    assert trace.error_class == "empty"
    assert trace.detail["empty_reason"] == "no_choices"


async def test_voice_ueberlebt_den_ausfall_des_direktanbieters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vom Voice-Aufrufer bis zum Ergebnis -- mit absichtlich ausgefallenem OpenAI.

    Ein Netz gibt es im Test nicht: der HTTP-Transport selbst wird ersetzt und
    entscheidet nach Zieladresse. Telegram liefert die Datei, OpenAI antwortet
    503, der lokale Proxy liefert die Transkription.
    """
    gesehen: list[str] = []

    async def netz(self: Any, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        gesehen.append(url.split("?")[0])
        if "api.telegram.org" in url and "getFile" in url:
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/a.oga"}})
        if "api.telegram.org/file/" in url:
            return httpx.Response(200, content=b"OggS-audio")
        if "api.openai.com" in url:
            return httpx.Response(503, json={"error": {"message": "down"}}, request=request)
        if url.startswith("http://127.0.0.1:4000") and url.endswith(STT):
            return httpx.Response(
                200,
                json={"text": "Hallo KAI"},
                headers={"x-litellm-model-name": "openai/whisper-1"},
                request=request,
            )
        raise AssertionError(f"unerwartete Adresse {url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", netz)
    einstellungen = InferenceSettings(
        enabled=True,
        mode_ceiling="primary",
        route_modes={"stt": "primary"},
        max_attempts=1,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        jitter_max_seconds=0.0,
    )
    monkeypatch.setattr("app.ai.runtime.environment_settings", lambda: einstellungen)

    text = await VoiceTranscriber(bot_token="t", openai_api_key="k").transcribe("file-1")

    assert text == "Hallo KAI"
    assert not any("api.openai.com" in url for url in gesehen), "kein Rueckfall noetig"
