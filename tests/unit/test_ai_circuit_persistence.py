"""Der Circuit ueberlebt den einzelnen Aufruf (LiteLLM-Audit 27.09., Befund B).

Bis 04046c68 uebergab ``app.ai.runtime.invoke`` dem Gateway kein Buch. Jeder
Aufruf begann mit einem leeren ``CircuitBook``, und der Fehlerstand stand nach
jedem Aufruf wieder auf 1: sechs Aufrufe gegen einen Upstream mit HTTP 503
erzeugten sechs Transportanfragen. Der Breaker existierte, gesperrt hat er nie.

Diese Tests laufen deshalb ueber MEHRERE unabhaengige Aufrufe von ``invoke`` --
ein Test innerhalb eines Aufrufs haette den Defekt nicht gesehen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai.circuit import (
    DEFAULT_COOLDOWN_S,
    DEFAULT_FAILURE_THRESHOLD,
    CircuitBook,
    CircuitKey,
    CircuitPolicy,
    CircuitStore,
)
from app.ai.config import InferenceSettings
from app.ai.gateway import SKIP_CIRCUIT_OPEN
from app.ai.runtime import LiteLLMRequest, circuit_state, invoke, reset_circuit_state

POLICY = CircuitPolicy(failure_threshold=3, cooldown_s=60.0)


def _settings(mode: str = "primary") -> InferenceSettings:
    return InferenceSettings(
        enabled=True,
        mode_ceiling=mode,
        route_modes={"standard": mode},
        max_attempts=1,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        jitter_max_seconds=0.0,
    )


def _factory(handler: Callable[[httpx.Request], httpx.Response]):
    def build(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return build


def _request() -> LiteLLMRequest[str]:
    def parse(body: dict[str, Any]) -> str:
        return str(body["choices"][0]["message"]["content"])

    return LiteLLMRequest(parser=parse, payload={"messages": []})


def _antwort(request: httpx.Request, *, status: int, identitaet: bool) -> httpx.Response:
    headers = {"x-litellm-model-name": "gemini/gemini-3.6-flash"} if identitaet else {}
    return httpx.Response(
        status,
        json={"choices": [{"message": {"content": "lite"}}]} if status < 400 else {"error": "x"},
        headers=headers,
        request=request,
    )


async def _direkt() -> str:
    return "direct"


class _Uhr:
    def __init__(self) -> None:
        self.jetzt = 1000.0

    def __call__(self) -> float:
        return self.jetzt


@pytest.fixture(autouse=True)
def _frischer_kreis() -> None:
    reset_circuit_state()


async def _aufruf(
    handler: Callable[[httpx.Request], httpx.Response], tmp_path: Path, uhr: _Uhr
) -> Any:
    return await invoke(
        purpose="analysis",
        direct_call=_direkt,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_request(),
        settings=_settings(),
        client_factory=_factory(handler),
        telemetry_path=tmp_path / "llm.jsonl",
        clock=uhr,
    )


@pytest.mark.parametrize("identitaet", [False, True], ids=["grob", "fein"])
async def test_sechs_aufrufe_gegen_503_oeffnen_den_kreis(tmp_path: Path, identitaet: bool) -> None:
    """Die Reproduktion aus dem Audit, einmal mit und einmal ohne Identitaet.

    Ohne Identitaet bucht der Kreis auf den Alias; mit Identitaet auf den
    einzigen bekannten Upstream. In BEIDEN Faellen darf nach der Schwelle kein
    weiterer Transportversuch mehr hinausgehen -- der Direktpfad traegt.
    """
    anfragen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal anfragen
        anfragen += 1
        return _antwort(request, status=503, identitaet=identitaet)

    uhr = _Uhr()
    ergebnisse = [await _aufruf(handler, tmp_path, uhr) for _ in range(6)]

    assert anfragen == DEFAULT_FAILURE_THRESHOLD, "nach der Schwelle geht nichts mehr hinaus"
    assert all(item.value == "direct" for item in ergebnisse), "der Rueckfall traegt weiter"
    letzte = ergebnisse[-1].outcome
    assert letzte is not None
    assert SKIP_CIRCUIT_OPEN in letzte.gateway.skipped
    assert letzte.litellm_attempts == ()
    assert circuit_state(), "der offene Kreis ist von aussen sichtbar"


async def test_der_fehlerstand_waechst_ueber_aufrufe(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _antwort(request, status=503, identitaet=False)

    uhr = _Uhr()
    for erwartet in range(1, DEFAULT_FAILURE_THRESHOLD):
        await _aufruf(handler, tmp_path, uhr)
        stand = circuit_state()
        assert [eintrag["consecutive_failures"] for eintrag in stand] == [erwartet]


async def test_nach_dem_cooldown_heilt_eine_probe_den_kreis(tmp_path: Path) -> None:
    kaputt = True
    anfragen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal anfragen
        anfragen += 1
        return _antwort(request, status=503 if kaputt else 200, identitaet=False)

    uhr = _Uhr()
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        await _aufruf(handler, tmp_path, uhr)
    assert anfragen == DEFAULT_FAILURE_THRESHOLD

    uhr.jetzt += DEFAULT_COOLDOWN_S
    kaputt = False
    geheilt = await _aufruf(handler, tmp_path, uhr)
    assert geheilt.value == "lite"
    assert geheilt.transport == "litellm"
    assert circuit_state() == [], "Erfolg schliesst den Kreis vollstaendig"


async def test_waehrend_der_probe_bleibt_der_kreis_fuer_andere_zu(tmp_path: Path) -> None:
    """Halboffen heisst EINE Probe -- auch wenn zwei Aufrufe gleichzeitig kommen."""
    freigabe = asyncio.Event()
    anfragen = 0

    async def langsam(request: httpx.Request) -> httpx.Response:
        nonlocal anfragen
        anfragen += 1
        await freigabe.wait()
        return _antwort(request, status=200, identitaet=False)

    def kaputt(request: httpx.Request) -> httpx.Response:
        nonlocal anfragen
        anfragen += 1
        return _antwort(request, status=503, identitaet=False)

    uhr = _Uhr()
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        await _aufruf(kaputt, tmp_path, uhr)
    anfragen = 0
    uhr.jetzt += DEFAULT_COOLDOWN_S

    def build(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(langsam))

    probe = asyncio.create_task(
        invoke(
            purpose="analysis",
            direct_call=_direkt,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=_request(),
            settings=_settings(),
            client_factory=build,
            telemetry_path=tmp_path / "llm.jsonl",
            clock=uhr,
        )
    )
    while anfragen == 0:
        await asyncio.sleep(0)
    zweiter = await _aufruf(kaputt, tmp_path, uhr)
    assert zweiter.value == "direct"
    assert anfragen == 1, "der zweite Aufruf darf nicht mitproben"
    freigabe.set()
    assert (await probe).transport == "litellm"


async def test_eine_abgebrochene_probe_blockiert_den_kreis_nicht_dauerhaft(
    tmp_path: Path,
) -> None:
    """Wird die Probe abgebrochen, darf sie nicht ewig als 'unterwegs' gelten."""
    haengt = asyncio.Event()

    async def haengend(request: httpx.Request) -> httpx.Response:
        haengt.set()
        await asyncio.Event().wait()
        raise AssertionError("unerreichbar")

    def kaputt(request: httpx.Request) -> httpx.Response:
        return _antwort(request, status=503, identitaet=False)

    uhr = _Uhr()
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        await _aufruf(kaputt, tmp_path, uhr)
    uhr.jetzt += DEFAULT_COOLDOWN_S

    def build(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(haengend))

    probe = asyncio.create_task(
        invoke(
            purpose="analysis",
            direct_call=_direkt,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=_request(),
            settings=_settings(),
            client_factory=build,
            telemetry_path=tmp_path / "llm.jsonl",
            clock=uhr,
        )
    )
    await haengt.wait()
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    anfragen = 0

    def heil(request: httpx.Request) -> httpx.Response:
        nonlocal anfragen
        anfragen += 1
        return _antwort(request, status=200, identitaet=False)

    naechster = await _aufruf(heil, tmp_path, uhr)
    assert anfragen == 1, "nach dem Abbruch ist wieder genau eine Probe frei"
    assert naechster.transport == "litellm"


# --------------------------------------------------------------------------
# Das Buch selbst: Zulassung je Alias und die abgesicherte Probe.
# --------------------------------------------------------------------------


def _oeffnen(book: CircuitBook, key: CircuitKey) -> CircuitBook:
    for _ in range(POLICY.failure_threshold):
        book = book.on_failure(key, now_s=0.0, policy=POLICY)
    return book


def test_der_einzige_bekannte_upstream_offen_sperrt_den_alias() -> None:
    fein = CircuitKey("standard", "kai-standard", "gemini/flash")
    book = _oeffnen(CircuitBook(), fein)
    zugelassen, _ = book.admit("standard", "kai-standard", now_s=1.0, policy=POLICY)
    assert not zugelassen


def test_eine_belegt_gesunde_alternative_haelt_den_alias_offen() -> None:
    """Die Zusicherung aus ADR 0017 bleibt: ein kaputter Upstream nimmt die
    Alternativen nicht mit -- sobald es eine gibt, die geantwortet hat."""
    kaputt = CircuitKey("standard", "kai-standard", "gemini/flash")
    heil = CircuitKey("standard", "kai-standard", "openai/gpt-4o-mini")
    book = CircuitBook().on_success(heil)
    book = _oeffnen(book, kaputt)
    zugelassen, _ = book.admit("standard", "kai-standard", now_s=1.0, policy=POLICY)
    assert zugelassen


def test_eine_verwaiste_probe_verfaellt_nach_dem_cooldown() -> None:
    key = CircuitKey("standard", "kai-standard")
    book = _oeffnen(CircuitBook(), key)
    zugelassen, book = book.admit("standard", "kai-standard", now_s=60.0, policy=POLICY)
    assert zugelassen
    zweiter, _ = book.admit("standard", "kai-standard", now_s=60.0, policy=POLICY)
    assert not zweiter, "nur EINE Probe"
    spaet, _ = book.admit("standard", "kai-standard", now_s=120.0, policy=POLICY)
    assert spaet, "eine Probe, die nie zurueckkam, sperrt nicht fuer immer"


def test_der_speicher_teilt_den_zustand_zwischen_aufrufern() -> None:
    store = CircuitStore()
    key = CircuitKey("standard", "kai-standard")
    for _ in range(POLICY.failure_threshold):
        store.update(lambda book: book.on_failure(key, now_s=0.0, policy=POLICY))
    assert not store.admit("standard", "kai-standard", now_s=1.0, policy=POLICY)
    store.reset()
    assert store.book == CircuitBook()
