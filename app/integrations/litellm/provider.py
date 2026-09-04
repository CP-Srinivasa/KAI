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

import httpx

from app.ai.audit import classify_error
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
        return self.base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))


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
    body: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except (ValueError, UnicodeDecodeError):
        body = {}

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

    error_class = None
    if response.status_code >= 400:
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
        detail={"status_code": response.status_code},
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


__all__ = ["TRANSPORT", "LiteLLMConfig", "call_litellm", "trace_from_response"]
