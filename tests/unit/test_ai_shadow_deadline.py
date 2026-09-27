"""Schatten ohne Wartezeit, Frist ueber alles (LiteLLM-Audit 27.09., Befund E).

Bis 04046c68 wartete eine fertige Direktantwort im SCHATTEN auf den gesamten
LiteLLM-Lauf, Wiederholungen eingeschlossen -- ein unverbindlicher Vergleich
hielt die Benutzerantwort fest. Und ueber einem Aufruf ausserhalb von OFF lag
keine Gesamtfrist: LiteLLM-Versuche, Pausen und Rueckfall summierten sich frei.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai import gateway
from app.ai.config import InferenceSettings
from app.ai.gateway import detached_shadow_count, drain_detached_shadows
from app.ai.runtime import InferenceDeadlineExceededError, LiteLLMRequest, circuit_state, invoke


def _einstellungen(modus: str, **extra: Any) -> InferenceSettings:
    return InferenceSettings(
        enabled=True,
        mode_ceiling=modus,
        route_modes={"standard": modus},
        max_attempts=1,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        jitter_max_seconds=0.0,
        **extra,
    )


def _antwort(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": "lite"}, "finish_reason": "stop"}]},
        headers={"x-litellm-model-name": "gemini/gemini-3.6-flash"},
        request=request,
    )


def _request() -> LiteLLMRequest[str]:
    return LiteLLMRequest(
        parser=lambda body: str(body["choices"][0]["message"]["content"]),
        payload={"messages": []},
    )


class _Fabrik:
    """Baut Clients mit einem Handler und merkt sich, ob sie geschlossen wurden."""

    def __init__(self, handler: Callable[[httpx.Request], Awaitable[httpx.Response]]) -> None:
        self.handler = handler
        self.clients: list[httpx.AsyncClient] = []

    def __call__(self, **_: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        self.clients.append(client)
        return client


async def _direkt() -> str:
    return "direct"


@pytest.fixture(autouse=True)
async def _keine_losen_schatten() -> Any:
    yield
    await drain_detached_shadows()


async def test_der_schatten_haelt_die_fertige_antwort_nicht_fest(tmp_path: Path) -> None:
    freigabe = asyncio.Event()

    async def langsam(request: httpx.Request) -> httpx.Response:
        await freigabe.wait()
        return _antwort(request)

    fabrik = _Fabrik(langsam)
    senke = tmp_path / "llm.jsonl"
    ergebnis = await asyncio.wait_for(
        invoke(
            purpose="analysis",
            direct_call=_direkt,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=_request(),
            settings=_einstellungen("shadow", shadow_grace_seconds=0.05),
            client_factory=fabrik,
            telemetry_path=senke,
        ),
        timeout=2.0,
    )

    assert ergebnis.value == "direct"
    assert ergebnis.outcome is not None
    assert ergebnis.outcome.gateway.detail["shadow_detached"] == "detached"
    assert detached_shadow_count() == 1
    assert not fabrik.clients[0].is_closed, "der Schatten benutzt seinen Client noch"

    freigabe.set()
    await drain_detached_shadows()
    await asyncio.sleep(0)  # der Aufraeum-Task schliesst den Client

    zeilen = [json.loads(z) for z in senke.read_text(encoding="utf-8").splitlines() if z]
    schatten = [z for z in zeilen if z.get("transport") == "litellm"]
    assert len(schatten) == 1, "die Evidenz des Schattens bleibt erhalten"
    assert schatten[0]["mode"] == "shadow"
    assert fabrik.clients[0].is_closed, "nach dem Schatten wird aufgeraeumt"


async def test_zu_viele_abgekoppelte_schatten_werden_abgebrochen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(gateway, "MAX_DETACHED_SHADOWS", 0)

    async def haengt(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unerreichbar")

    ergebnis = await asyncio.wait_for(
        invoke(
            purpose="analysis",
            direct_call=_direkt,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=_request(),
            settings=_einstellungen("shadow", shadow_grace_seconds=0.0),
            client_factory=_Fabrik(haengt),
            telemetry_path=tmp_path / "llm.jsonl",
        ),
        timeout=2.0,
    )
    assert ergebnis.value == "direct"
    assert ergebnis.outcome is not None
    assert ergebnis.outcome.gateway.detail["shadow_detached"] == "cancelled_cap"
    assert detached_shadow_count() == 0
    assert circuit_state() == [], "die abgebrochene Probe hinterlaesst keinen Zustand"


async def test_die_gesamtfrist_umfasst_litellm_und_rueckfall(tmp_path: Path) -> None:
    async def haengt(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unerreichbar")

    with pytest.raises(InferenceDeadlineExceededError):
        await asyncio.wait_for(
            invoke(
                purpose="analysis",
                direct_call=_direkt,
                direct_provider="openai",
                direct_model="gpt-4o",
                litellm=_request(),
                settings=_einstellungen("primary", route_deadline_seconds={"standard": 0.1}),
                client_factory=_Fabrik(haengt),
                telemetry_path=tmp_path / "llm.jsonl",
            ),
            timeout=2.0,
        )


async def test_die_gesamtfrist_gilt_auch_fuer_den_direktpfad_im_schatten(tmp_path: Path) -> None:
    async def direkt_haengt() -> str:
        await asyncio.Event().wait()
        raise AssertionError("unerreichbar")

    async def schnell(request: httpx.Request) -> httpx.Response:
        return _antwort(request)

    with pytest.raises(InferenceDeadlineExceededError):
        await asyncio.wait_for(
            invoke(
                purpose="analysis",
                direct_call=direkt_haengt,
                direct_provider="openai",
                direct_model="gpt-4o",
                litellm=_request(),
                settings=_einstellungen("shadow", route_deadline_seconds={"standard": 0.1}),
                client_factory=_Fabrik(schnell),
                telemetry_path=tmp_path / "llm.jsonl",
            ),
            timeout=2.0,
        )


async def test_off_bleibt_ohne_frist(tmp_path: Path) -> None:
    """OFF ist der harte Rueckweg: kein Client, keine Task, keine Frist."""

    async def langsam_direkt() -> str:
        await asyncio.sleep(0.2)
        return "direct"

    ergebnis = await invoke(
        purpose="analysis",
        direct_call=langsam_direkt,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_request(),
        settings=InferenceSettings(enabled=False, route_deadline_seconds={"standard": 0.01}),
        telemetry_path=tmp_path / "llm.jsonl",
    )
    assert ergebnis.value == "direct"


def test_die_frist_ohne_eintrag_deckt_versuche_pausen_und_rueckfall() -> None:
    from app.ai.retry import RetryPolicy
    from app.ai.runtime import _gesamtfrist

    einstellungen = InferenceSettings(enabled=True, timeout_seconds=30.0, max_attempts=3)
    retry = RetryPolicy(max_attempts=3, base_backoff_s=0.25, max_backoff_s=2.0, max_jitter_s=0.1)
    frist = _gesamtfrist(einstellungen, "standard", retry)
    assert frist >= 30.0 * 3 + 30.0, "drei LiteLLM-Versuche UND ein Rueckfall passen hinein"
    assert _gesamtfrist(
        InferenceSettings(route_deadline_seconds={"standard": 12.0}), "standard", retry
    ) == pytest.approx(12.0)
