"""Aufrufpfade ohne Herstellerbindung und mit einem Retry-Eigentuemer.

LiteLLM-Audit 27.09.:

* Befund D -- Chat, Intent und Spracherkennung prueften einen OpenAI-Schluessel,
  BEVOR sie die zentrale Route fragten. "OpenAI eingerichtet, aber
  ausgefallen" lief ueber die Reserve; "OpenAI bewusst nicht eingerichtet"
  wurde vorher abgewiesen.
* Befund E -- im Direktanbieter lagen SDK-Wiederholungen und Tenacity
  uebereinander: mit dem lokalen SDK wurden aus einem logischen Aufruf neun
  HTTP-Versuche, die Telemetrie sah hoechstens drei.

Kein Netz: der HTTP-Transport selbst wird ersetzt und antwortet nach
Zieladresse. Damit laufen die ECHTEN SDK-Clients -- in CI die gelockten
Versionen aus ``requirements.lock``.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tenacity import wait_none

from app.ai.audit import llm_call_scope
from app.ai.config import InferenceSettings
from app.ai.runtime import litellm_can_carry

LITELLM = "http://127.0.0.1:4000"


def _einstellungen(modus: str, *routen: str, base_url: str = LITELLM) -> InferenceSettings:
    return InferenceSettings(
        enabled=True,
        mode_ceiling=modus,
        route_modes=dict.fromkeys(routen, modus),
        litellm_base_url=base_url,
        max_attempts=1,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        jitter_max_seconds=0.0,
    )


def _antwort(request: Any, status: int, **kwargs: Any) -> Any:
    """Eine Antwort aus DERSELBEN Bibliothek wie die Anfrage.

    Die gelockten SDKs (openai 3.x, anthropic 1.x) sprechen ``httpx2``, der
    LiteLLM-Client und Telegram ``httpx``. Ein Test, der nur eine der beiden
    abfaengt, liefe mit der anderen ins echte Netz.
    """
    modul = importlib.import_module(type(request).__module__.split(".")[0])
    return modul.Response(status, request=request, **kwargs)


def _netz(monkeypatch: pytest.MonkeyPatch, antwort: Callable[[Any], Any]) -> list[str]:
    gesehen: list[str] = []

    async def handle(self: Any, request: Any) -> Any:
        gesehen.append(str(request.url))
        return antwort(request)

    for name in ("httpx", "httpx2"):
        try:
            modul = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(modul.AsyncHTTPTransport, "handle_async_request", handle)
    return gesehen


def _chat_antwort(request: Any, text: str = "lite") -> Any:
    return _antwort(
        request,
        200,
        json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]},
        headers={"x-litellm-model-name": "gemini/gemini-3.6-flash"},
    )


def _kaputt(request: Any) -> Any:
    # retry-after-ms haelt die SDK-eigenen Pausen im Test kurz.
    return _antwort(
        request, 503, json={"error": {"message": "down"}}, headers={"retry-after-ms": "1"}
    )


class _AppSettings:
    """Genug von ``AppSettings`` fuer den Chat-Eingang -- ohne OpenAI-Schluessel."""

    class providers:  # noqa: N801 - spiegelt das Attribut von AppSettings
        openai_api_key = ""
        openai_model = "gpt-4o"


# --------------------------------------------------------------------------
# D -- Verfuegbarkeit haengt an der freigegebenen Route.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("einstellungen", "purpose", "erwartet"),
    [
        (_einstellungen("off", "standard"), "chat", False),
        (_einstellungen("shadow", "standard"), "chat", False),
        (_einstellungen("primary", "standard"), "chat", True),
        (_einstellungen("primary", "critical"), "intent", True),
        (_einstellungen("primary", "stt"), "stt", True),
        (_einstellungen("primary", "reasoning"), "consensus", False),
        (_einstellungen("primary", "standard", base_url="http://10.0.0.5:4000"), "chat", False),
    ],
    ids=["off", "schatten", "primary", "intent", "stt", "consensus-gedeckelt", "nicht-lokal"],
)
def test_litellm_traegt_nur_was_die_route_autoritativ_erlaubt(
    einstellungen: InferenceSettings, purpose: Any, erwartet: bool
) -> None:
    assert litellm_can_carry(purpose, einstellungen) is erwartet


async def test_chat_ohne_openai_laeuft_ueber_die_freigegebene_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.messaging import kai_chat_engine

    gesehen = _netz(monkeypatch, _chat_antwort)
    monkeypatch.setattr(kai_chat_engine, "get_settings", lambda: _AppSettings())
    monkeypatch.setattr(
        "app.ai.runtime.environment_settings", lambda: _einstellungen("primary", "standard")
    )

    antwort = await kai_chat_engine._respond_smalltalk("hi", "de")

    assert antwort.source == "litellm"
    assert antwort.reply == "lite"
    assert all(url.startswith(LITELLM) for url in gesehen)


async def test_chat_ohne_openai_und_ohne_route_bleibt_ehrlich_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.messaging import kai_chat_engine

    gesehen = _netz(monkeypatch, _chat_antwort)
    monkeypatch.setattr(kai_chat_engine, "get_settings", lambda: _AppSettings())
    monkeypatch.setattr(
        "app.ai.runtime.environment_settings", lambda: _einstellungen("off", "standard")
    )

    antwort = await kai_chat_engine._respond_smalltalk("hi", "de")

    assert antwort.source == "fallback"
    assert gesehen == []


async def test_intent_ohne_openai_laeuft_ueber_die_freigegebene_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.messaging.text_intent import TextIntentProcessor

    inhalt = json.dumps({"intent": "status", "response": "alles gruen"})
    _netz(monkeypatch, lambda request: _chat_antwort(request, inhalt))
    monkeypatch.setattr(
        "app.ai.runtime.environment_settings", lambda: _einstellungen("primary", "critical")
    )

    prozessor = TextIntentProcessor(api_key="", model="gpt-4o-mini")
    assert prozessor.is_configured
    ergebnis = await prozessor.process("wie steht es?")

    assert ergebnis.intent == "status"
    assert ergebnis.response == "alles gruen"


async def test_voice_ohne_openai_laeuft_ueber_die_freigegebene_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.messaging.voice_transcriber import VoiceTranscriber

    def antwort(request: Any) -> Any:
        url = str(request.url)
        if "getFile" in url:
            return _antwort(request, 200, json={"ok": True, "result": {"file_path": "voice/a.oga"}})
        if "api.telegram.org/file/" in url:
            return _antwort(request, 200, content=b"OggS-audio")
        if url.startswith(LITELLM):
            return _antwort(request, 200, json={"text": "Hallo KAI"})
        raise AssertionError(f"unerwartete Adresse {url}")

    gesehen = _netz(monkeypatch, antwort)
    monkeypatch.setattr(
        "app.ai.runtime.environment_settings", lambda: _einstellungen("primary", "stt")
    )

    transkription = VoiceTranscriber(bot_token="t", openai_api_key="")
    assert transkription.is_configured
    assert await transkription.transcribe("file-1") == "Hallo KAI"
    assert not any("api.openai.com" in url for url in gesehen)


async def test_ohne_openai_und_mit_ausgefallener_route_meldet_der_rueckfall_nicht_eingerichtet(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.messaging.text_intent import TextIntentProcessor

    gesehen = _netz(monkeypatch, _kaputt)
    monkeypatch.setattr(
        "app.ai.runtime.environment_settings", lambda: _einstellungen("primary", "critical")
    )

    ergebnis = await TextIntentProcessor(api_key="", model="gpt-4o-mini").process("hallo")

    assert all(url.startswith(LITELLM) for url in gesehen), "kein Versuch gegen OpenAI"
    assert ergebnis.intent == "chat"
    assert "nicht eingerichtet" in caplog.text


# --------------------------------------------------------------------------
# E -- ein Eigentuemer der Wiederholung, gezaehlte HTTP-Versuche.
# --------------------------------------------------------------------------


def _zeilen(pfad: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in pfad.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("anbieter", ["openai", "anthropic", "grok"])
async def test_ein_analyseaufruf_macht_hoechstens_drei_http_versuche(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, anbieter: str
) -> None:
    """Tenacity besitzt die Wiederholung: 3 Versuche, nicht 3 x 3."""
    if anbieter == "openai":
        from app.integrations.openai.provider import OpenAIAnalysisProvider as Anbieter
    elif anbieter == "anthropic":
        from app.integrations.anthropic.provider import (
            AnthropicAnalysisProvider as Anbieter,  # type: ignore[assignment]
        )
    else:
        from app.integrations.xai.provider import (
            GrokAnalysisProvider as Anbieter,  # type: ignore[assignment]
        )

    gesehen = _netz(monkeypatch, _kaputt)
    monkeypatch.setattr(Anbieter.analyze.retry, "wait", wait_none())  # type: ignore[attr-defined]
    senke = tmp_path / "llm.jsonl"

    provider = Anbieter(api_key="k", model="m")
    with pytest.raises(Exception):  # noqa: B017,PT011 - die Klasse ist SDK-spezifisch
        async with llm_call_scope(purpose="analysis", provider=anbieter, model="m", path=senke):
            await provider.analyze(title="t", text="x")

    assert len(gesehen) == 3
    assert _zeilen(senke)[-1]["retry_count"] == 2, "die Zeile traegt die physischen Versuche"


async def test_der_direkte_chatpfad_zaehlt_die_sdk_wiederholungen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ohne aeussere Schicht besitzt das SDK die Wiederholung -- sichtbar gezaehlt."""
    from openai import AsyncOpenAI

    from app.integrations.openai.client import SDK_RETRY_OWNER, counted_http_client

    gesehen = _netz(monkeypatch, _kaputt)
    senke = tmp_path / "llm.jsonl"
    client = AsyncOpenAI(
        api_key="k", timeout=5.0, max_retries=SDK_RETRY_OWNER, http_client=counted_http_client()
    )
    with pytest.raises(Exception):  # noqa: B017,PT011
        async with llm_call_scope(purpose="chat", provider="openai", model="m", path=senke):
            await client.chat.completions.create(model="m", messages=[])

    assert len(gesehen) == SDK_RETRY_OWNER + 1
    assert _zeilen(senke)[-1]["retry_count"] == SDK_RETRY_OWNER
