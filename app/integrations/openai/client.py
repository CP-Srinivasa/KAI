"""OpenAI-kompatible Clients mit ausdruecklicher Retry-Verantwortung.

Ein logischer KAI-Aufruf hat genau EINEN Eigentuemer fuer Wiederholungen.
Bis 04046c68 lagen zwei uebereinander: das SDK wiederholte von sich aus
(``max_retries=2`` als stiller Standard), und darueber sass Tenacity mit drei
Versuchen. Mit dem lokal installierten SDK wurden aus einem Aufruf neun
HTTP-Versuche, und die Telemetrie sah davon hoechstens drei
(LiteLLM-Audit 27.09., Befund E).

Deshalb nennt jeder Aufrufer ``max_retries`` ausdruecklich, und jede
tatsaechlich abgeschickte Anfrage wird am Transport gezaehlt
(:func:`app.ai.audit.note_http_request`). Die Zeile im Telemetriestrom traegt
damit die physischen Wiederholungen, nicht die geglaubten.
"""

from __future__ import annotations

from typing import Final

from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from app.ai.audit import note_http_request

#: Tenacity (oder eine andere aeussere Schicht) besitzt die Wiederholung.
OUTER_RETRY_OWNER: Final = 0
#: Direktaufrufe ohne aeussere Wiederholung: das SDK besitzt sie, sichtbar
#: gezaehlt. Derselbe Wert wie der bisherige SDK-Standard -- das Verhalten des
#: OFF-Pfads bleibt, nur ist es jetzt eine Entscheidung und gemessen.
SDK_RETRY_OWNER: Final = 2


def counted_http_client() -> DefaultAsyncHttpxClient:
    """Der HTTP-Client des SDK, mit einem Zaehler an jeder abgehenden Anfrage."""
    return DefaultAsyncHttpxClient(event_hooks={"request": [note_http_request]})


def counted_async_openai(
    *,
    api_key: str,
    timeout: float,
    max_retries: int,
    base_url: str | None = None,
) -> AsyncOpenAI:
    """Ein ``AsyncOpenAI``, dessen HTTP-Versuche im laufenden Aufruf zaehlen."""
    return AsyncOpenAI(
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
        base_url=base_url,
        http_client=counted_http_client(),
    )


__all__ = [
    "OUTER_RETRY_OWNER",
    "SDK_RETRY_OWNER",
    "counted_async_openai",
    "counted_http_client",
]
