"""LiteLLM-Transport (ADR 0017, Sprint 4).

Ohne Netz und ohne Uhr: `httpx.MockTransport` liefert die Antworten, eine
Zählfunktion die Zeit. Genau diese Trennung fehlte im ersten Anlauf, wo die
Telemetrie nur zusammen mit einem echten Upstream testbar war — und deshalb
faktisch gar nicht.
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations.litellm.health import GatewayHealth, probe_gateway
from app.integrations.litellm.provider import (
    TRANSPORT,
    LiteLLMConfig,
    call_litellm,
    trace_from_response,
)


def _clock(*werte: float):
    folge = list(werte)

    def tick() -> float:
        return folge.pop(0) if folge else 0.0

    return tick


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _antwort(
    *,
    status: int = 200,
    body: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body if body is not None else {},
        headers=headers or {},
        request=httpx.Request("POST", "http://127.0.0.1:4000/v1/chat/completions"),
    )


# --------------------------------------------------------------------------
# Identität kommt aus der Antwort, nicht aus der Anfrage.
# --------------------------------------------------------------------------


def test_die_identitaet_gilt_erst_wenn_beides_gemeldet_ist() -> None:
    trace = trace_from_response(
        _antwort(
            body={"model": "gpt-4o-mini", "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
            headers={"x-litellm-model-provider": "openai", "x-litellm-response-cost": "0.00042"},
        ),
        requested_model="fast",
        latency_ms=42.0,
    )
    assert trace.transport == TRANSPORT
    assert trace.identity_proven
    assert trace.actual_provider == "openai"
    assert trace.actual_model == "gpt-4o-mini"
    assert trace.model_substituted, "angefragt war der Alias 'fast'"
    assert trace.input_tokens == 12
    assert trace.output_tokens == 7
    assert trace.cost_usd == pytest.approx(0.00042)
    assert trace.ok


def test_ohne_anbieter_im_header_bleibt_die_identitaet_unbewiesen() -> None:
    """Der Alias wird NICHT eingetragen — das wäre eine Behauptung."""
    trace = trace_from_response(
        _antwort(body={"model": "gpt-4o-mini"}), requested_model="fast", latency_ms=1.0
    )
    assert trace.actual_provider == ""
    assert not trace.identity_proven
    assert not trace.model_substituted


def test_das_modell_aus_dem_body_schlaegt_den_header() -> None:
    """Der Body ist die Antwort des Upstreams, der Header nur die Weitergabe."""
    trace = trace_from_response(
        _antwort(
            body={"model": "echt-4o"},
            headers={"x-litellm-model": "durchgereicht", "x-litellm-model-provider": "openai"},
        ),
        requested_model="fast",
        latency_ms=1.0,
    )
    assert trace.actual_model == "echt-4o"


def test_fehlt_das_modell_im_body_zaehlt_der_header() -> None:
    trace = trace_from_response(
        _antwort(headers={"x-litellm-model-id": "aus-dem-header", "x-litellm-provider": "gemini"}),
        requested_model="fast",
        latency_ms=1.0,
    )
    assert trace.actual_model == "aus-dem-header"
    assert trace.actual_provider == "gemini"
    assert trace.identity_proven


# --------------------------------------------------------------------------
# Kosten sind unbekannt, bis sie belegt sind.
# --------------------------------------------------------------------------


def test_ohne_kostenangabe_bleibt_es_unbekannt() -> None:
    trace = trace_from_response(_antwort(body={"model": "m"}), requested_model="m", latency_ms=1.0)
    assert trace.cost_usd is None
    assert not trace.cost_known


def test_ein_unlesbarer_kostenwert_ist_unbekannt_und_nicht_null() -> None:
    trace = trace_from_response(
        _antwort(body={"model": "m"}, headers={"x-litellm-response-cost": "keine-zahl"}),
        requested_model="m",
        latency_ms=1.0,
    )
    assert trace.cost_usd is None


def test_die_kosten_werden_auch_aus_hidden_params_gelesen() -> None:
    trace = trace_from_response(
        _antwort(body={"model": "m", "_hidden_params": {"response_cost": 0.005}}),
        requested_model="m",
        latency_ms=1.0,
    )
    assert trace.cost_usd == pytest.approx(0.005)


# --------------------------------------------------------------------------
# Fehler sind Versuche, keine Ausnahmen.
# --------------------------------------------------------------------------


def test_ein_fehlerstatus_wird_klassifiziert_statt_geworfen() -> None:
    trace = trace_from_response(_antwort(status=429), requested_model="m", latency_ms=5.0)
    assert trace.error_class == "rate_limit"
    assert not trace.ok
    assert trace.detail["status_code"] == 429


def test_ein_unlesbarer_koerper_kippt_die_uebersetzung_nicht() -> None:
    response = httpx.Response(
        status_code=200,
        content=b"kein json",
        request=httpx.Request("POST", "http://127.0.0.1:4000/v1/chat/completions"),
    )
    trace = trace_from_response(response, requested_model="m", latency_ms=1.0)
    assert trace.ok
    assert trace.actual_model == ""
    assert trace.cost_usd is None


def test_ein_transportfehler_wird_zum_versuch_und_wirft_nicht() -> None:
    """`TransportCall` sagt zu, nicht zu werfen — sonst umgeht der Fehler die Telemetrie."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kein Gateway", request=request)

    with _client(handler) as client:
        trace = call_litellm(
            config=LiteLLMConfig(),
            model="fast",
            payload={"messages": []},
            client=client,
            monotonic=_clock(0.0, 0.25),
        )
    assert not trace.ok
    assert trace.error_class == "transport"
    assert trace.latency_ms == pytest.approx(250.0)
    assert trace.detail["exception"] == "ConnectError"


def test_ein_geglueckter_aufruf_misst_die_latenz() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-kai-correlation-id"] == "abc"
        assert b'"model":"fast"' in request.content.replace(b" ", b"")
        return _antwort(body={"model": "gpt-4o-mini"}, headers={"x-litellm-provider": "openai"})

    with _client(handler) as client:
        trace = call_litellm(
            config=LiteLLMConfig(),
            model="fast",
            payload={"messages": []},
            client=client,
            monotonic=_clock(1.0, 1.1),
            correlation_id="abc",
        )
    assert trace.ok
    assert trace.latency_ms == pytest.approx(100.0)
    assert trace.identity_proven


# --------------------------------------------------------------------------
# Health — „unknown" ist nicht „up", und die Grenze zählt.
# --------------------------------------------------------------------------


def test_ungemessen_ist_unbekannt_und_nicht_benutzbar() -> None:
    leer = GatewayHealth()
    assert leer.state == "unknown"
    assert not leer.usable


def test_ein_erreichbares_lokales_gateway_ist_benutzbar() -> None:
    with _client(lambda r: httpx.Response(200, json={"status": "healthy"}, request=r)) as client:
        health = probe_gateway(config=LiteLLMConfig(), client=client, monotonic=_clock(0.0, 0.01))
    assert health.state == "up"
    assert health.boundary_ok
    assert health.usable
    assert health.latency_ms == pytest.approx(10.0)


def test_ausserhalb_der_grenze_ist_erreichbar_aber_nicht_benutzbar() -> None:
    """Sonst wäre die Localhost-Grenze eine Empfehlung und keine Zusicherung."""
    with _client(lambda r: httpx.Response(200, json={}, request=r)) as client:
        health = probe_gateway(
            config=LiteLLMConfig(base_url="http://10.0.0.5:4000"),
            client=client,
            monotonic=_clock(0.0, 0.01),
        )
    assert health.state == "up"
    assert not health.boundary_ok
    assert not health.usable


def test_ein_unerreichbares_gateway_ist_down_nicht_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("weg", request=request)

    with _client(handler) as client:
        health = probe_gateway(config=LiteLLMConfig(), client=client, monotonic=_clock(0.0, 0.5))
    assert health.state == "down"
    assert health.error_class == "timeout"
    assert not health.usable


def test_ein_fehlerstatus_ist_down_mit_code() -> None:
    with _client(lambda r: httpx.Response(503, json={}, request=r)) as client:
        health = probe_gateway(config=LiteLLMConfig(), client=client, monotonic=_clock(0.0, 0.01))
    assert health.state == "down"
    assert health.status_code == 503
    assert health.error_class == "server"


def test_die_voreinstellung_zeigt_auf_localhost() -> None:
    """ADR 0017 verlangt eine kontrollierte Grenze — als Vorgabe, nicht als Hinweis."""
    assert LiteLLMConfig().is_local
    assert not LiteLLMConfig(base_url="https://api.example.com").is_local
