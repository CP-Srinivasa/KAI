"""OpenAI-kompatibler Transport zum LiteLLM-Gateway — und nichts weiter.

Was hier passiert: einen Request absetzen, die Antwort lesen, daraus einen
``AttemptTrace`` machen. Was hier NICHT passiert: entscheiden, ob gerufen werden
darf (Budget), ob der Upstream gesperrt ist (Circuit), ob das Ergebnis bindet
(Modus). Das liegt in ``app.ai`` und bleibt dort — ein Provider, der
mitentscheidet, wäre die zweite Control-Plane aus dem ersten Anlauf.

**Die Identität kommt aus der Antwort, nicht aus der Anfrage.** LiteLLM meldet
im Body, welches Modell tatsächlich geantwortet hat, und im Header, welcher
Anbieter es war. Nur wenn beides da ist, gilt die Identität als bewiesen; sonst
bleiben die Felder leer und ``AttemptTrace.identity_proven`` ist ``False``. Den
angefragten Alias einzutragen wäre eine Behauptung über etwas Ungemessenes — im
ersten Anlauf stand genau das in der Telemetrie und sah wie ein Beweis aus.

**Kosten sind unbekannt, bis sie belegt sind.** Fehlt der Kostenwert in Antwort
oder Header, bleibt ``cost_usd`` ``None``. Niemals 0.

Keine neue Dependency: ``httpx`` ist bereits im Lockfile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.ai.audit import ErrorClass, classify_error
from app.ai.models import AttemptTrace

TRANSPORT = "litellm"

#: Header, unter denen LiteLLM den tatsächlichen Upstream meldet. Mehrere, weil
#: die Namen sich zwischen Versionen unterscheiden — geraten wird nichts, es
#: wird nur der erste GEFUNDENE genommen und sonst nichts eingetragen.
_PROVIDER_HEADERS = ("x-litellm-model-provider", "x-litellm-provider")
_MODEL_HEADERS = ("x-litellm-model", "x-litellm-model-id")
_COST_HEADERS = ("x-litellm-response-cost", "x-litellm-cost")
_REQUEST_ID_HEADERS = ("x-litellm-call-id", "x-request-id")


@dataclass(frozen=True)
class LiteLLMConfig:
    """Wohin gesprochen wird. Voreinstellung: ausschliesslich localhost.

    ADR 0017 verlangt eine kontrollierte Grenze. Die Voreinstellung ist deshalb
    keine erreichbare Aussenadresse, sondern der eigene Rechner — wer das ändert,
    tut es sichtbar in der Konfiguration und nicht aus Versehen.
    """

    base_url: str = "http://127.0.0.1:4000"
    timeout_s: float = 30.0
    api_key: str = ""

    @property
    def is_local(self) -> bool:
        try:
            parsed = urlsplit(self.base_url)
        except ValueError:
            return False
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.username is None
            and parsed.password is None
        )


@dataclass(frozen=True)
class LiteLLMResponse:
    """Transport evidence plus the unopinionated JSON response body."""

    trace: AttemptTrace
    body: dict[str, Any]


def _first_header(headers: Any, names: tuple[str, ...]) -> str:
    for name in names:
        try:
            value = headers.get(name)
        except AttributeError:
            return ""
        if value:
            return str(value)
    return ""


def _float_or_none(raw: str) -> float | None:
    """Ein Kostenwert — oder ``None``. Ein unlesbarer Wert ist unbekannt, nicht 0."""
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _usage_int(usage: Any, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _response_body(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _antwort_ist_leer(body: dict[str, Any]) -> tuple[bool, str]:
    """Traegt die 200 ueberhaupt Text? Und wenn nicht, warum nicht?

    Am 2026-09-08 auf kai-pi5 gemessen: Gemini 2.5 Flash verbraucht das
    Ausgabebudget zuerst fuer internes Denken. Bei `max_tokens=20` gingen alle
    17 Ausgabe-Token dorthin -- `reasoning_tokens=17`, `text_tokens=0`,
    `finish_reason=length`, `content: null` -- und die Antwort kam mit
    HTTP 200 zurueck. Ohne diese Pruefung waere das ein Versuch ohne
    `error_class`, also ein Erfolg, der nichts enthaelt.

    Der Grund gehoert dazu, nicht nur das Urteil: "abgeschnitten" verlangt ein
    groesseres Budget, "gestoppt und trotzdem leer" ist ein Modellverhalten,
    und "keine Auswahl" ist eine kaputte Antwort. Drei verschiedene naechste
    Schritte, die im Log unterscheidbar bleiben muessen.
    """
    auswahl = body.get("choices")
    if not isinstance(auswahl, list) or not auswahl:
        return True, "no_choices"
    erste = auswahl[0]
    if not isinstance(erste, dict):
        return True, "no_choices"

    nachricht = erste.get("message")
    inhalt = nachricht.get("content") if isinstance(nachricht, dict) else None
    if isinstance(inhalt, str) and inhalt.strip():
        return False, ""

    # Werkzeugaufrufe sind eine gueltige Antwort ohne Text.
    if isinstance(nachricht, dict) and nachricht.get("tool_calls"):
        return False, ""

    grund = str(erste.get("finish_reason") or "unknown")
    return True, f"empty_content_finish_{grund}"


def _detail(status_code: int, body: dict[str, Any], leer_grund: str) -> dict[str, Any]:
    """Was der Aufrufer spaeter braucht, um den Versuch zu verstehen.

    `reasoning_tokens` steht hier, weil `completion_tokens` sie zwar
    mitzaehlt und damit die Summe stimmt, die Zusammensetzung aber verliert:
    fuer eine Ein-Wort-Antwort fielen auf kai-pi5 21-37 Reasoning-Token an,
    abgerechnet wie Ausgabe. Wer nur die sichtbare Ausgabe sieht, unterschaetzt
    die Kosten dieser Route um ein Vielfaches.
    """
    ergebnis: dict[str, Any] = {"status_code": status_code}
    if leer_grund:
        ergebnis["empty_reason"] = leer_grund

    auswahl = body.get("choices")
    if isinstance(auswahl, list) and auswahl and isinstance(auswahl[0], dict):
        ende = auswahl[0].get("finish_reason")
        if isinstance(ende, str) and ende:
            ergebnis["finish_reason"] = ende

    usage = body.get("usage")
    if isinstance(usage, dict):
        details = usage.get("completion_tokens_details")
        denken = _usage_int(details, "reasoning_tokens")
        if denken is not None:
            ergebnis["reasoning_tokens"] = denken
    return ergebnis


def trace_from_response(
    response: httpx.Response,
    *,
    requested_model: str,
    latency_ms: float,
) -> AttemptTrace:
    """Eine Antwort in einen Versuch übersetzen — rein, ohne Netz.

    Getrennt von :func:`call_litellm`, damit die Übersetzung ohne laufendes
    Gateway prüfbar ist. Genau diese Trennung fehlte im ersten Anlauf, wo die
    Telemetrie nur zusammen mit einem echten Upstream testbar war und deshalb
    faktisch gar nicht.
    """
    headers = response.headers
    body = _response_body(response)

    usage = body.get("usage")
    # Das Modell aus dem BODY hat Vorrang: es ist die Antwort des Upstreams,
    # der Header nur die Weitergabe des Gateways.
    actual_model = str(body.get("model") or "") or _first_header(headers, _MODEL_HEADERS)
    actual_provider = _first_header(headers, _PROVIDER_HEADERS)
    cost = _float_or_none(_first_header(headers, _COST_HEADERS))
    if cost is None:
        hidden = body.get("_hidden_params")
        if isinstance(hidden, dict):
            cost = _float_or_none(str(hidden.get("response_cost") or ""))

    error_class: ErrorClass | None = None
    leer_grund = ""
    if response.status_code < 400:
        ist_leer, leer_grund = _antwort_ist_leer(body)
        if ist_leer:
            error_class = "empty"
    if response.status_code >= 400:
        error_marker = str(body.get("error", "")).lower()
        if "quota" in error_marker:
            error_class = "quota"
        else:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                error_class = classify_error(exc)

    return AttemptTrace(
        transport=TRANSPORT,
        requested_model=requested_model,
        latency_ms=latency_ms,
        actual_provider=actual_provider,
        actual_model=actual_model,
        input_tokens=_usage_int(usage, "prompt_tokens"),
        output_tokens=_usage_int(usage, "completion_tokens"),
        cost_usd=cost,
        error_class=error_class,
        request_id=_first_header(headers, _REQUEST_ID_HEADERS),
        detail=_detail(response.status_code, body, leer_grund),
    )


def call_litellm(
    *,
    config: LiteLLMConfig,
    model: str,
    payload: dict[str, Any],
    client: httpx.Client,
    monotonic: Any,
    correlation_id: str = "",
) -> AttemptTrace:
    """Einen Versuch ausführen. Wirft nicht — ein Fehlschlag ist ein Versuch.

    ``TransportCall`` in ``app.ai.gateway`` sagt zu, dass ein Fehlschlag als
    ``AttemptTrace`` mit ``error_class`` zurückkommt. Wer stattdessen wirft,
    umgeht die Telemetrie; im ersten Anlauf war das der Grund, warum 0 von
    12.940 Zeilen einen Fehler trugen.

    ``client`` und ``monotonic`` werden übergeben, damit dieser Pfad ohne Netz
    und ohne Uhr prüfbar bleibt.
    """
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"
    if correlation_id:
        headers["x-kai-correlation-id"] = correlation_id

    started = monotonic()
    try:
        response = client.post(
            f"{config.base_url.rstrip('/')}/v1/chat/completions",
            json={**payload, "model": model},
            headers=headers,
            timeout=config.timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - jeder Transportfehler ist ein Versuch
        return AttemptTrace(
            transport=TRANSPORT,
            requested_model=model,
            latency_ms=(monotonic() - started) * 1000.0,
            error_class=classify_error(exc),
            detail={"exception": type(exc).__name__},
        )
    return trace_from_response(
        response, requested_model=model, latency_ms=(monotonic() - started) * 1000.0
    )


async def call_litellm_async(
    *,
    config: LiteLLMConfig,
    model: str,
    client: httpx.AsyncClient,
    monotonic: Any,
    correlation_id: str = "",
    endpoint: str = "/v1/chat/completions",
    payload: dict[str, Any] | None = None,
    files: Any = None,
    data: dict[str, Any] | None = None,
) -> LiteLLMResponse:
    """Execute one non-blocking LiteLLM attempt; policy remains in ``app.ai``.

    Chat callers provide ``payload``. STT uses the same transport contract with
    multipart ``files``/``data`` and the OpenAI-compatible transcription path.
    The function never retries and never raises a network error.
    """
    headers: dict[str, str] = {}
    if payload is not None:
        headers["content-type"] = "application/json"
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"
    if correlation_id:
        headers["x-kai-correlation-id"] = correlation_id

    started = monotonic()
    try:
        if payload is not None:
            response = await client.post(
                f"{config.base_url.rstrip('/')}{endpoint}",
                json={**payload, "model": model},
                headers=headers,
                timeout=config.timeout_s,
            )
        else:
            response = await client.post(
                f"{config.base_url.rstrip('/')}{endpoint}",
                files=files,
                data={**(data or {}), "model": model},
                headers=headers,
                timeout=config.timeout_s,
            )
    except Exception as exc:  # noqa: BLE001 - transport failure is evidence
        return LiteLLMResponse(
            trace=AttemptTrace(
                transport=TRANSPORT,
                requested_model=model,
                latency_ms=(monotonic() - started) * 1000.0,
                error_class=classify_error(exc),
                detail={"exception": type(exc).__name__},
            ),
            body={},
        )

    return LiteLLMResponse(
        trace=trace_from_response(
            response,
            requested_model=model,
            latency_ms=(monotonic() - started) * 1000.0,
        ),
        body=_response_body(response),
    )


__all__ = [
    "TRANSPORT",
    "LiteLLMConfig",
    "LiteLLMResponse",
    "call_litellm",
    "call_litellm_async",
    "trace_from_response",
]
